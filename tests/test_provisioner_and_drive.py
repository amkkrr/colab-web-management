#!/usr/bin/env python3
"""Unit and integration tests for StdlibOAuthDriveClient and ColabProvisioner."""

from __future__ import annotations

import io
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from control.collector import MultiInstanceUptimeTracker, estimate_hardware_ccu_rate  # noqa: E402
from control.drive_api import (  # noqa: E402
    COLAB_NOTEBOOK_MIME,
    FOLDER_MIME,
    StdlibOAuthDriveClient,
    load_gdrive_oauth_config,
)
from control.protocol import build_action_envelope, utc_now_iso  # noqa: E402
from control.provisioner import BOOTSTRAP_MARKER, ColabProvisioner, parse_sessions_output, parse_usage_output  # noqa: E402
from server import create_handler  # noqa: E402


class _FakeHTTPResp:
    def __init__(self, body: bytes) -> None:
        self._buf = io.BytesIO(body)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_FakeHTTPResp":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class _FakeDriveServer:
    """In-memory Drive v3 + OAuth2 token endpoint mock for urllib opener."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.token_refreshes = 0
        self.create_calls = 0
        self.patch_calls = 0
        self.next_id = 1
        self.items: dict[str, dict[str, Any]] = {}
        self._tick = 1

    def _gen_id(self) -> str:
        val = f"fid_{self.next_id}"
        self.next_id += 1
        return val

    def _next_mtime(self) -> str:
        self._tick += 1
        return f"2026-11-21T14:00:{self._tick % 60:02d}.{self._tick:03d}Z"

    def seed_file(self, folder_parts: list[str], name: str, data: bytes, *, mime_type: str = "application/json") -> str:
        with self.lock:
            parent_id = "root"
            for part in folder_parts:
                found = None
                for fid, item in self.items.items():
                    if item["parent_id"] == parent_id and item["name"] == part and item["mimeType"] == FOLDER_MIME:
                        found = fid
                        break
                if found is None:
                    found = self._gen_id()
                    self.items[found] = {
                        "id": found,
                        "name": part,
                        "parent_id": parent_id,
                        "mimeType": FOLDER_MIME,
                        "modifiedTime": self._next_mtime(),
                        "data": b"",
                    }
                parent_id = found
            fid = self._gen_id()
            self.items[fid] = {
                "id": fid,
                "name": name,
                "parent_id": parent_id,
                "mimeType": mime_type,
                "modifiedTime": self._next_mtime(),
                "data": data,
            }
            return fid

    def open(self, req: urllib.request.Request, timeout: float = 15.0) -> _FakeHTTPResp:  # noqa: ARG002
        url = req.full_url
        method = req.get_method()
        if url == "https://oauth2.googleapis.com/token" and method == "POST":
            with self.lock:
                self.token_refreshes += 1
                tok = f"fake-access-token-{self.token_refreshes}"
            return _FakeHTTPResp(json.dumps({"access_token": tok, "expires_in": 3600}).encode("utf-8"))

        auth = req.headers.get("Authorization") or ""
        if not auth.startswith("Bearer fake-access-token-"):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b"unauthorized"))

        parsed = urllib.parse.urlsplit(url)
        qs = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/drive/v3/files" and method == "GET":
            q = (qs.get("q") or [""])[0]
            parent_m = re.search(r"'([^']+)' in parents", q)
            name_m = re.search(r"name = '([^']+)'", q)
            want_parent = parent_m.group(1) if parent_m else None
            want_name = name_m.group(1).replace("\\'", "'") if name_m else None
            only_folder = f"mimeType = '{FOLDER_MIME}'" in q
            exclude_folder = f"mimeType != '{FOLDER_MIME}'" in q
            want_nb = ".ipynb" in q or COLAB_NOTEBOOK_MIME in q
            with self.lock:
                matches = []
                for item in self.items.values():
                    if want_parent and item["parent_id"] != want_parent:
                        continue
                    if want_name and item["name"] != want_name:
                        continue
                    if only_folder and item["mimeType"] != FOLDER_MIME:
                        continue
                    if exclude_folder and item["mimeType"] == FOLDER_MIME:
                        continue
                    if want_nb and not (item["name"].lower().endswith(".ipynb") or item["mimeType"] == COLAB_NOTEBOOK_MIME):
                        continue
                    matches.append(
                        {
                            "id": item["id"],
                            "name": item["name"],
                            "mimeType": item["mimeType"],
                            "modifiedTime": item["modifiedTime"],
                            "size": str(len(item["data"])),
                            "parents": [item["parent_id"]] if item["parent_id"] else [],
                        }
                    )
                matches.sort(key=lambda x: x["modifiedTime"], reverse=True)
            return _FakeHTTPResp(json.dumps({"files": matches}).encode("utf-8"))

        if parsed.path == "/drive/v3/files" and method == "POST":
            body = json.loads((req.data or b"{}").decode("utf-8"))
            with self.lock:
                fid = self._gen_id()
                self.items[fid] = {
                    "id": fid,
                    "name": body["name"],
                    "parent_id": (body.get("parents") or ["root"])[0],
                    "mimeType": body.get("mimeType") or "application/octet-stream",
                    "modifiedTime": self._next_mtime(),
                    "data": b"",
                }
            return _FakeHTTPResp(json.dumps({"id": fid}).encode("utf-8"))

        if parsed.path.startswith("/drive/v3/files/") and method == "GET":
            fid = parsed.path.rsplit("/", 1)[-1]
            with self.lock:
                item = self.items.get(fid)
            if not item:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"not found"))
            if (qs.get("alt") or [""])[0] == "media":
                return _FakeHTTPResp(item["data"])
            return _FakeHTTPResp(
                json.dumps(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "mimeType": item["mimeType"],
                        "modifiedTime": item["modifiedTime"],
                        "size": str(len(item["data"])),
                        "parents": [item["parent_id"]] if item["parent_id"] else [],
                        "trashed": False,
                    }
                ).encode("utf-8")
            )

        if parsed.path == "/upload/drive/v3/files" and method == "POST":
            ct = req.headers.get("Content-type") or req.headers.get("Content-Type") or ""
            boundary = ct.split("boundary=")[-1].strip()
            parts = (req.data or b"").split(f"--{boundary}".encode("utf-8"))
            meta_raw = parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n")
            media_raw = parts[2].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n")
            meta = json.loads(meta_raw.decode("utf-8"))
            with self.lock:
                self.create_calls += 1
                fid = self._gen_id()
                self.items[fid] = {
                    "id": fid,
                    "name": meta["name"],
                    "parent_id": (meta.get("parents") or ["root"])[0],
                    "mimeType": "application/json",
                    "modifiedTime": self._next_mtime(),
                    "data": media_raw,
                }
            return _FakeHTTPResp(json.dumps({"id": fid}).encode("utf-8"))

        if parsed.path.startswith("/upload/drive/v3/files/") and method == "PATCH":
            fid = parsed.path.rsplit("/", 1)[-1]
            with self.lock:
                item = self.items.get(fid)
                if not item:
                    raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"not found"))
                self.patch_calls += 1
                item["data"] = req.data or b""
                item["modifiedTime"] = self._next_mtime()
            return _FakeHTTPResp(json.dumps({"id": fid}).encode("utf-8"))

        raise AssertionError(f"Unexpected request: {method} {url}")


def _write_fake_colab_cli(path: Path, state_file: Path, *, boot_instance_id: str = "inst_20261121_140000_abcd1234") -> None:
    script = f"""#!/usr/bin/env python3
import json, pathlib, sys

STATE = pathlib.Path({str(state_file)!r})
args = sys.argv[1:]
cmd = args[0] if args else ""

def load_state():
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {{"sessions": {{}}, "uploads": [], "calls": []}}

def save_state(st):
    STATE.write_text(json.dumps(st), encoding="utf-8")

st = load_state()
st["calls"].append(args)

def opt_val(flag, default=""):
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return args[idx + 1]
    return default

if cmd == "sessions":
    if not st["sessions"]:
        print("[colab] No active sessions found on server.")
    else:
        for sname, s in st["sessions"].items():
            print(f"[{{sname}}] {{s['endpoint']}} | Hardware: {{s['hardware']}} | Shape: {{s['shape']}} | Variant: {{s['variant']}}")
    save_state(st)
    sys.exit(0)

if cmd == "new":
    sname = opt_val("-s", "auto-sess")
    gpu = opt_val("--gpu")
    tpu = opt_val("--tpu")
    hw = gpu or tpu or "CPU"
    variant = "GPU" if gpu else ("TPU" if tpu else "DEFAULT")
    shape = "High-RAM" if "--high-mem" in args else "Standard"
    st["sessions"][sname] = {{
        "endpoint": f"m-s-{{sname}}",
        "hardware": hw,
        "shape": shape,
        "variant": variant,
    }}
    save_state(st)
    print(f"[colab] Created session {{sname}} ({{hw}})")
    sys.exit(0)

if cmd == "upload":
    sname = opt_val("-s")
    st["uploads"].append({{"session": sname, "src": args[-2], "dst": args[-1]}})
    save_state(st)
    sys.exit(0)

if cmd == "exec":
    sname = opt_val("-s")
    stdin_code = sys.stdin.read()
    assert "refresh_token" in stdin_code
    assert not any("fake-refresh-secret" in a for a in args)
    out = {{
        "instance_id": {boot_instance_id!r},
        "session_name": sname,
        "drive_mode": "oauth_silent",
        "drive_healthy": True,
        "gpu_name": st["sessions"].get(sname, {{}}).get("hardware") if st["sessions"].get(sname, {{}}).get("variant") == "GPU" else None,
        "ram_total_gib": 12.7,
    }}
    print({BOOTSTRAP_MARKER!r} + json.dumps(out))
    save_state(st)
    sys.exit(0)

if cmd == "url":
    sname = opt_val("-s")
    if sname not in st["sessions"]:
        print("Session not found", file=sys.stderr)
        sys.exit(1)
    ep = st["sessions"][sname]["endpoint"]
    print(f"https://colab.research.google.com/notebooks/empty.ipynb?dbu=%2Ftun%2Fm%2F{{ep}}#datalabBackendUrl=https://colab.research.google.com/tun/m/{{ep}}")
    save_state(st)
    sys.exit(0)

if cmd == "stop":
    sname = opt_val("-s")
    st["sessions"].pop(sname, None)
    save_state(st)
    print(f"[colab] Stopped {{sname}}")
    sys.exit(0)

if cmd == "usage":
    cnt = len(st["sessions"])
    rate = 4.82 if cnt == 1 else (round(4.82 * cnt, 2) if cnt > 1 else 0.00)
    print("Current balance: 903.33 compute units")
    print(f"Usage rate: {{rate:.2f}}/hr")
    print(f"Active assignments: {{cnt}}")
    save_state(st)
    sys.exit(0)

sys.exit(1)
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class DriveApiAndProvisionerTests(unittest.TestCase):
    def test_load_gdrive_oauth_config_and_repr_redaction(self) -> None:
        keys = ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN", "COLAB_GDRIVE_OAUTH_ENV", "COLAB_DRIVE_MONITOR_PATH")
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env_file = Path(tmp) / "oauth.env"
                env_file.write_text(
                    "# test oauth env\n"
                    'export GDRIVE_CLIENT_ID="cid-12345678.apps.googleusercontent.com"\n'
                    "GDRIVE_CLIENT_SECRET='sec-secret-value-abc'\n"
                    "GDRIVE_REFRESH_TOKEN=1//0g-refresh-token-xyz\n",
                    encoding="utf-8",
                )
                cfg = load_gdrive_oauth_config(env_file=env_file, monitor_folder_path="/team/_colab_monitor/")
                self.assertIsNotNone(cfg)
                assert cfg is not None
                self.assertEqual(cfg.client_id, "cid-12345678.apps.googleusercontent.com")
                self.assertEqual(cfg.client_secret, "sec-secret-value-abc")
                self.assertEqual(cfg.refresh_token, "1//0g-refresh-token-xyz")
                self.assertEqual(cfg.monitor_folder_path, "team/_colab_monitor")
                rep = repr(cfg)
                self.assertIn("[REDACTED]", rep)
                self.assertNotIn("sec-secret-value-abc", rep)
                self.assertNotIn("1//0g-refresh-token-xyz", rep)

                bad_env = Path(tmp) / "incomplete.env"
                bad_env.write_text("GDRIVE_CLIENT_ID=only_id\n", encoding="utf-8")
                self.assertIsNone(load_gdrive_oauth_config(env_file=bad_env))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_stdlib_oauth_drive_client_crud_and_mailbox_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "oauth.env"
            env_file.write_text(
                "GDRIVE_CLIENT_ID=cid-test\n"
                "GDRIVE_CLIENT_SECRET=fake-client-secret\n"
                "GDRIVE_REFRESH_TOKEN=fake-refresh-secret\n",
                encoding="utf-8",
            )
            cfg = load_gdrive_oauth_config(env_file=env_file)
            assert cfg is not None
            fake_srv = _FakeDriveServer()
            client = StdlibOAuthDriveClient(cfg, opener=fake_srv)

            health1 = client.check_health()
            health2 = client.check_health()
            self.assertTrue(health1["ok"])
            self.assertTrue(health2["ok"])
            self.assertEqual(fake_srv.token_refreshes, 1)

            # First write -> multipart create; second write -> PATCH in place
            client.put_json("instances/inst_1.json", {"instance_id": "inst_1", "heartbeat_at": utc_now_iso()})
            self.assertEqual(fake_srv.create_calls, 1)
            self.assertEqual(fake_srv.patch_calls, 0)
            client.put_json("instances/inst_1.json", {"instance_id": "inst_1", "heartbeat_at": utc_now_iso()})
            self.assertEqual(fake_srv.create_calls, 1)
            self.assertEqual(fake_srv.patch_calls, 1)

            # Seed secret and ACK on Drive, plus stale local instance cache
            fake_srv.seed_file(
                ["_colab_monitor", "secrets"],
                "inst_1.json",
                json.dumps({"instance_id": "inst_1", "api_key": "agent-key-123"}).encode("utf-8"),
            )
            fake_srv.seed_file(
                ["_colab_monitor", "acks", "inst_1"],
                "cmd_1.json",
                json.dumps({"cmd_id": "cmd_1", "status": "succeeded", "message": "ok"}).encode("utf-8"),
            )

            local_root = Path(tmp) / "local_monitor"
            (local_root / "instances").mkdir(parents=True)
            stale_file = local_root / "instances" / "inst_stale.json"
            stale_file.write_text("{}", encoding="utf-8")

            c1 = client.sync_mailbox_to_local(local_root, pending_ack_targets=[("inst_1", "cmd_1")])
            self.assertEqual(c1, {"instances": 1, "secrets": 1, "acks": 1})
            self.assertFalse(stale_file.exists())
            self.assertTrue((local_root / "instances" / "inst_1.json").is_file())
            sec_file = local_root / "secrets" / "inst_1.json"
            self.assertTrue(sec_file.is_file())
            self.assertEqual(stat.S_IMODE(sec_file.stat().st_mode), 0o600)
            self.assertTrue((local_root / "acks" / "inst_1" / "cmd_1.json").is_file())

            # Second sync skips unchanged mtime files
            c2 = client.sync_mailbox_to_local(local_root, pending_ack_targets=[("inst_1", "cmd_1")])
            self.assertEqual(c2, {"instances": 0, "secrets": 0, "acks": 0})

            env = build_action_envelope("inst_1", "lifecycle.keepalive", api_key="agent-key-123")
            client.push_command_json("inst_1", env["cmd_id"], env)
            pushed = client.get_json(f"commands/inst_1/{env['cmd_id']}.json")
            self.assertIsNotNone(pushed)
            assert pushed is not None
            self.assertEqual(pushed["cmd_id"], env["cmd_id"])

    def test_provisioner_validation_async_boot_and_redaction(self) -> None:
        parsed = parse_sessions_output(
            "[colab] A new version is available\n"
            "[my-t4] m-s-abc123 | Hardware: T4 | Shape: Standard | Variant: GPU\n"
            "[?] m-s-orphan | Hardware: CPU | Shape: Standard | Variant: DEFAULT\n"
        )
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["name"], "my-t4")
        self.assertFalse(parsed[0]["is_orphan"])
        self.assertTrue(parsed[1]["is_orphan"])

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            env_file = tmp_dir / "oauth.env"
            env_file.write_text(
                "GDRIVE_CLIENT_ID=cid-test\n"
                "GDRIVE_CLIENT_SECRET=fake-client-secret\n"
                "GDRIVE_REFRESH_TOKEN=fake-refresh-secret\n",
                encoding="utf-8",
            )
            cfg = load_gdrive_oauth_config(env_file=env_file)
            assert cfg is not None
            fake_srv = _FakeDriveServer()
            drive_client = StdlibOAuthDriveClient(cfg, opener=fake_srv)

            cli_state = tmp_dir / "cli_state.json"
            fake_cli = tmp_dir / "colab"
            _write_fake_colab_cli(fake_cli, cli_state)

            boot_callbacks: list[dict[str, Any]] = []
            prov = ColabProvisioner(
                state_dir=tmp_dir / "state",
                agent_src_path=ROOT / "agent" / "colab_agent.py",
                drive_client=drive_client,
                cli_bin=str(fake_cli),
                on_boot_complete=lambda info: boot_callbacks.append(info),
            )

            # Input validation rejections
            c_gpu, _ = prov.start_boot(runtime_type="gpu", gpu="RTX4090")
            self.assertEqual(c_gpu, 422)
            c_tpu, _ = prov.start_boot(runtime_type="tpu", tpu="v9")
            self.assertEqual(c_tpu, 422)
            c_name, _ = prov.start_boot(runtime_type="cpu", session_name="bad name!")
            self.assertEqual(c_name, 422)

            prov_no_drive = ColabProvisioner(
                state_dir=tmp_dir / "state_nd",
                agent_src_path=ROOT / "agent" / "colab_agent.py",
                drive_client=None,
                cli_bin=str(fake_cli),
            )
            c_nd, _ = prov_no_drive.start_boot(runtime_type="cpu", attach_agent=True)
            self.assertEqual(c_nd, 422)

            # Async boot + completion
            code, job = prov.start_boot(runtime_type="gpu", gpu="T4", high_mem=True, session_name="unit-t4", idle_timeout_minutes=45)
            self.assertEqual(code, 202)
            job_id = job["job_id"]

            final_job = None
            for _ in range(50):
                cur = prov.get_job(job_id)
                if cur and cur["status"] in ("succeeded", "failed"):
                    final_job = cur
                    break
                time.sleep(0.05)

            self.assertIsNotNone(final_job)
            assert final_job is not None
            self.assertEqual(final_job["status"], "succeeded", msg=final_job)
            self.assertEqual(final_job["instance_id"], "inst_20261121_140000_abcd1234")
            self.assertTrue((final_job["colab_url"] or "").startswith("https://colab.research.google.com/"))
            self.assertEqual(len(boot_callbacks), 1)

            status_json = json.dumps(prov.get_status(refresh_sessions=True), ensure_ascii=False)
            self.assertNotIn("fake-client-secret", status_json)
            self.assertNotIn("fake-refresh-secret", status_json)
            self.assertIn("unit-t4", status_json)

            ucode, ures = prov.get_session_url("unit-t4")
            self.assertEqual(ucode, 200)
            self.assertTrue(ures["url"].startswith("https://colab.research.google.com/"))

            scode, sres = prov.stop_session("unit-t4")
            self.assertEqual(scode, 200)
            self.assertTrue(sres["ok"])
            self.assertEqual(prov.list_sessions(refresh=True), [])

    def test_http_provisioner_endpoints_and_drive_action_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            env_file = tmp_dir / "oauth.env"
            env_file.write_text(
                "GDRIVE_CLIENT_ID=cid-test\n"
                "GDRIVE_CLIENT_SECRET=fake-client-secret\n"
                "GDRIVE_REFRESH_TOKEN=fake-refresh-secret\n",
                encoding="utf-8",
            )
            cfg = load_gdrive_oauth_config(env_file=env_file)
            assert cfg is not None
            fake_srv = _FakeDriveServer()
            drive_client = StdlibOAuthDriveClient(cfg, opener=fake_srv)

            # Seed Drive with an agent instance + secret so boot completion tick discovers it immediately
            iid = "inst_20261121_140500_beef0001"
            fake_srv.seed_file(
                ["_colab_monitor", "secrets"],
                f"{iid}.json",
                json.dumps({"instance_id": iid, "api_key": "agent-secret-xyz"}).encode("utf-8"),
            )
            fake_srv.seed_file(
                ["_colab_monitor", "instances"],
                f"{iid}.json",
                json.dumps(
                    {
                        "schema_version": "2.0",
                        "instance_id": iid,
                        "project": "headless_smoke",
                        "label": "headless-cpu",
                        "run_mode": "offline_batch",
                        "heartbeat_at": utc_now_iso(),
                        "tunnel": {"enabled": False, "healthy": False, "base_url": ""},
                        "lifecycle": {"state": "running", "idle_seconds": 0, "idle_timeout_minutes": 15, "drain_requested": False},
                        "hardware": {"gpu": {"available": False}, "ram": {"used_gib": 1.2, "total_gib": 12.7, "pct": 9.4}, "drive": {"mode": "oauth_silent", "healthy": True}},
                        "capabilities": ["lifecycle.keepalive", "lifecycle.shutdown_now"],
                    }
                ).encode("utf-8"),
            )

            cli_state = tmp_dir / "cli_state.json"
            fake_cli = tmp_dir / "colab"
            _write_fake_colab_cli(fake_cli, cli_state, boot_instance_id=iid)

            handler = create_handler(
                tmp_dir / "active_endpoint.json",
                allow_remote=False,
                monitor_root=tmp_dir / "monitor_cache",
                state_dir=tmp_dir / "local_state",
                drive_client=drive_client,
                colab_cli_bin=str(fake_cli),
            )
            web = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            wt = threading.Thread(target=web.serve_forever, daemon=True)
            wt.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://127.0.0.1:{web.server_port}"

            try:
                # 1. Status check
                with opener.open(f"{base}/api/v2/provisioner/status?refresh=1", timeout=5) as resp:
                    raw_status = resp.read().decode("utf-8")
                st = json.loads(raw_status)
                self.assertTrue(st["cli_available"])
                self.assertTrue(st["drive_oauth_configured"])
                self.assertTrue(st["drive_oauth_health"]["ok"])
                self.assertIsNotNone(st.get("usage"))
                self.assertEqual(st["usage"]["balance"], 903.33)
                self.assertNotIn("fake-client-secret", raw_status)
                self.assertNotIn("fake-refresh-secret", raw_status)

                # 2. Missing X-Colab-Action header rejected with 403
                bad_req = urllib.request.Request(
                    f"{base}/api/v2/provisioner/boot",
                    data=json.dumps({"action": "provisioner.boot", "runtime_type": "cpu"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    opener.open(bad_req, timeout=5)
                self.assertEqual(ctx.exception.code, 403)

                # 3. Boot request returns 202 and discovers new instance
                boot_req = urllib.request.Request(
                    f"{base}/api/v2/provisioner/boot",
                    data=json.dumps(
                        {"action": "provisioner.boot", "runtime_type": "cpu", "session_name": "smoke-cpu", "attach_agent": True}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Colab-Action": "provisioner.boot"},
                    method="POST",
                )
                with opener.open(boot_req, timeout=5) as resp:
                    self.assertEqual(resp.status, 202)
                    boot_data = json.loads(resp.read().decode("utf-8"))
                job_id = boot_data["job_id"]

                for _ in range(50):
                    with opener.open(f"{base}/api/v2/provisioner/jobs/{job_id}", timeout=5) as resp:
                        job_state = json.loads(resp.read().decode("utf-8"))
                    if job_state["status"] in ("succeeded", "failed"):
                        break
                    time.sleep(0.05)
                self.assertEqual(job_state["status"], "succeeded")

                with opener.open(f"{base}/api/v2/instances", timeout=5) as resp:
                    insts_raw = resp.read().decode("utf-8")
                self.assertNotIn("agent-secret-xyz", insts_raw)
                insts_payload = json.loads(insts_raw)
                insts = insts_payload["instances"]
                self.assertEqual(len(insts), 1)
                self.assertEqual(insts[0]["instance_id"], iid)
                self.assertEqual(insts[0]["liveness_state"], "ONLINE_DRIVE_ONLY")
                self.assertEqual(insts[0]["hardware"]["drive"]["mode"], "oauth_silent")
                ccu_sum = insts_payload["uptime_summary"]["compute_units_summary"]
                self.assertEqual(ccu_sum["account_balance"], 903.33)
                self.assertIn("all_units", ccu_sum)
                self.assertIn("compute_units", insts_payload["uptime_summary"]["windows"]["all"]["items"][0])

                # 4. Dispatching Channel B action pushes command JSON onto Drive
                act_req = urllib.request.Request(
                    f"{base}/api/v2/instances/{iid}/actions",
                    data=json.dumps({"action": "lifecycle.keepalive", "params": {}}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Colab-Action": "lifecycle.keepalive"},
                    method="POST",
                )
                with opener.open(act_req, timeout=5) as resp:
                    self.assertEqual(resp.status, 202)
                    act_data = json.loads(resp.read().decode("utf-8"))
                cmd_id = act_data["cmd_id"]
                pushed_cmd = drive_client.get_json(f"commands/{iid}/{cmd_id}.json")
                self.assertIsNotNone(pushed_cmd)
                assert pushed_cmd is not None
                self.assertEqual(pushed_cmd["action"], "lifecycle.keepalive")

                # 5. Get URL & stop session via HTTP
                url_req = urllib.request.Request(
                    f"{base}/api/v2/provisioner/url",
                    data=json.dumps({"action": "provisioner.url", "session_name": "smoke-cpu"}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Colab-Action": "provisioner.url"},
                    method="POST",
                )
                with opener.open(url_req, timeout=5) as resp:
                    url_data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(url_data["url"].startswith("https://colab.research.google.com/"))

                stop_req = urllib.request.Request(
                    f"{base}/api/v2/provisioner/stop",
                    data=json.dumps({"action": "provisioner.stop", "session_name": "smoke-cpu"}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Colab-Action": "provisioner.stop"},
                    method="POST",
                )
                with opener.open(stop_req, timeout=5) as resp:
                    stop_data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(stop_data["ok"])
            finally:
                web.shutdown()
                web.server_close()

    def test_usage_parsing_rate_estimation_and_compute_units_ledger(self) -> None:
        parsed_idle = parse_usage_output(
            "Current balance: 903.33 compute units\n"
            "Usage rate: 0.00/hr\n"
            "Active assignments: 0\n"
        )
        self.assertIsNotNone(parsed_idle)
        assert parsed_idle is not None
        self.assertEqual(parsed_idle["balance"], 903.33)
        self.assertEqual(parsed_idle["rate_hourly"], 0.0)
        self.assertEqual(parsed_idle["assignments_count"], 0)
        self.assertIsNone(parsed_idle["estimated_remaining_hours"])

        parsed_busy = parse_usage_output(
            "Current balance: 100.00 compute units\n"
            "Usage rate: 5.00/hr\n"
            "Active assignments: 1\n"
        )
        assert parsed_busy is not None
        self.assertEqual(parsed_busy["estimated_remaining_hours"], 20.0)
        self.assertIsNone(parse_usage_output("unrelated text"))

        self.assertEqual(estimate_hardware_ccu_rate("NVIDIA L4", "gpu", overrides={}), 4.82)
        self.assertEqual(estimate_hardware_ccu_rate("NVIDIA A100-SXM4-40GB", "gpu", overrides={}), 11.77)
        self.assertEqual(estimate_hardware_ccu_rate("CPU Only", "cpu_only", ram_total_gib=12.7, overrides={}), 0.10)
        self.assertEqual(estimate_hardware_ccu_rate("CPU Only", "cpu_only", ram_total_gib=51.0, overrides={}), 1.20)
        self.assertEqual(estimate_hardware_ccu_rate("CPU Only (High-RAM)", "cpu_only", overrides={}), 1.20)
        self.assertEqual(estimate_hardware_ccu_rate("待识别硬件", "unknown", overrides={}), 0.0)

        saved_env = os.environ.pop("COLAB_CCU_RATES_JSON", None)
        try:
            os.environ["COLAB_CCU_RATES_JSON"] = json.dumps({"l4": 6.0, "cpu_only": 0.25})
            self.assertEqual(estimate_hardware_ccu_rate("NVIDIA L4", "gpu"), 6.0)
            self.assertEqual(estimate_hardware_ccu_rate("CPU Only", "cpu_only", ram_total_gib=12.7), 0.25)
        finally:
            if saved_env is None:
                os.environ.pop("COLAB_CCU_RATES_JSON", None)
            else:
                os.environ["COLAB_CCU_RATES_JSON"] = saved_env

        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "uptime_ledger.json"
            tracker = MultiInstanceUptimeTracker(ledger_path)
            t0 = time.time() - 5500.0
            inst_l4 = {
                "instance_id": "inst_l4_1",
                "liveness_state": "ONLINE_LIVE",
                "hardware": {"gpu": {"available": True, "name": "NVIDIA L4"}, "ram": {"total_gib": 53.0}},
            }
            inst_cpu = {
                "instance_id": "inst_cpu_1",
                "liveness_state": "ONLINE_DRIVE_ONLY",
                "hardware": {"gpu": {"available": False}, "ram": {"total_gib": 12.7}},
            }

            # Single L4 instance calibrated via live `colab usage` rate (5.00/hr over 1800s -> 2.50 CU)
            tracker.record_instances([inst_l4], now_ts=t0, live_usage=parsed_busy)
            summary_single: dict[str, Any] = {}
            for step in range(200, 1801, 200):
                summary_single = tracker.record_instances(
                    [inst_l4],
                    now_ts=t0 + step,
                    force_flush=(step == 1800),
                    live_usage=parsed_busy,
                )
            l4_item = summary_single["windows"]["all"]["items"][0]
            self.assertEqual(l4_item["hardware_key"], "NVIDIA L4")
            self.assertEqual(l4_item["rate_source"], "live_ccu")
            self.assertEqual(l4_item["rate_cu_per_hour"], 5.0)
            self.assertAlmostEqual(l4_item["compute_units"], 2.5, places=2)
            self.assertEqual(l4_item["compute_units_share_pct"], 100.0)
            self.assertEqual(summary_single["compute_units_summary"]["account_balance"], 100.0)
            self.assertEqual(summary_single["compute_units_summary"]["all_units"], 2.5)

            # Add concurrent CPU Only for 3600s -> 0.10 CU; L4 gets another 3600s at calibrated 5.00 -> +5.00 CU
            tracker.record_instances([inst_l4, inst_cpu], now_ts=t0 + 1800.0)
            summary_multi: dict[str, Any] = {}
            for step in range(2000, 5401, 200):
                summary_multi = tracker.record_instances(
                    [inst_l4, inst_cpu],
                    now_ts=t0 + step,
                    force_flush=(step == 5400),
                )
            for win_key in ("all", "today", "7d", "current"):
                self.assertAlmostEqual(summary_multi["windows"][win_key]["total_compute_units"], 7.6, places=2)
            by_hw = {i["hardware_key"]: i for i in summary_multi["windows"]["all"]["items"]}
            self.assertAlmostEqual(by_hw["NVIDIA L4"]["compute_units"], 7.5, places=2)
            self.assertAlmostEqual(by_hw["CPU Only"]["compute_units"], 0.1, places=2)
            self.assertIn("CPU Only (High-RAM)", by_hw)
            self.assertEqual(by_hw["CPU Only (High-RAM)"]["seconds"], 0.0)
            self.assertEqual(by_hw["CPU Only (High-RAM)"]["rate_cu_per_hour"], 1.2)
            self.assertAlmostEqual(summary_multi["windows"]["all"]["gpu_compute_units"], 7.5, places=2)
            self.assertAlmostEqual(summary_multi["windows"]["all"]["cpu_only_compute_units"], 0.1, places=2)
            self.assertAlmostEqual(
                by_hw["NVIDIA L4"]["compute_units_share_pct"] + by_hw["CPU Only"]["compute_units_share_pct"],
                100.0,
                delta=0.2,
            )

            # Reload from disk preserves calibrated rates & CU totals
            reloaded = MultiInstanceUptimeTracker(ledger_path)
            summary_reload = reloaded.record_instances([], now_ts=t0 + 5405.0)
            re_by_hw = {i["hardware_key"]: i for i in summary_reload["windows"]["all"]["items"]}
            self.assertEqual(re_by_hw["NVIDIA L4"]["rate_source"], "live_ccu")
            self.assertAlmostEqual(re_by_hw["NVIDIA L4"]["compute_units"], 7.5, places=2)

            # High-RAM CPU (51 GiB) and Standard CPU (12.7 GiB) tracked separately (1.20 vs 0.10 CU/h)
            hm_tracker = MultiInstanceUptimeTracker(Path(tmp) / "hm_ledger.json")
            inst_cpu_hm = {
                "instance_id": "inst_cpu_hm_1",
                "liveness_state": "ONLINE_DRIVE_ONLY",
                "hardware": {"gpu": {"available": False}, "ram": {"total_gib": 51.0}},
            }
            hm_start = t0 + 1800.0
            hm_tracker.record_instances([inst_cpu, inst_cpu_hm], now_ts=hm_start)
            hm_summary: dict[str, Any] = {}
            for step in range(200, 3601, 200):
                hm_summary = hm_tracker.record_instances([inst_cpu, inst_cpu_hm], now_ts=hm_start + step, force_flush=(step == 3600))
            hm_by_hw = {i["hardware_key"]: i for i in hm_summary["windows"]["all"]["items"]}
            self.assertAlmostEqual(hm_by_hw["CPU Only"]["compute_units"], 0.10, places=2)
            self.assertEqual(hm_by_hw["CPU Only"]["rate_cu_per_hour"], 0.10)
            self.assertAlmostEqual(hm_by_hw["CPU Only (High-RAM)"]["compute_units"], 1.20, places=2)
            self.assertEqual(hm_by_hw["CPU Only (High-RAM)"]["rate_cu_per_hour"], 1.20)
            self.assertEqual(hm_by_hw["CPU Only (High-RAM)"]["category"], "cpu_only")
            self.assertAlmostEqual(hm_summary["windows"]["all"]["cpu_only_compute_units"], 1.30, places=2)

    def test_drive_api_notebook_scan_launch_and_safe_inject_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            monitor_root = tmp_dir / "monitor"
            state_dir = tmp_dir / "state"
            fake_drive = _FakeDriveServer()
            orig_nb = {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["class DriveSyncManager:\n    pass\nGDRIVE = DriveSyncManager()\n"]},
                    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["print('hello')\n"]},
                ],
            }
            oauth_fid = fake_drive.seed_file(
                ["Colab Notebooks"],
                "remote_oauth.ipynb",
                json.dumps(orig_nb, indent=1).encode("utf-8"),
                mime_type=COLAB_NOTEBOOK_MIME,
            )
            fake_drive.seed_file(
                ["Colab Notebooks"],
                "remote_oauth_backup.ipynb",
                json.dumps(orig_nb, indent=1).encode("utf-8"),
                mime_type=COLAB_NOTEBOOK_MIME,
            )

            env_file = tmp_dir / "gdrive_oauth.env"
            env_file.write_text(
                "GDRIVE_CLIENT_ID=fake-client-id.apps.googleusercontent.com\n"
                "GDRIVE_CLIENT_SECRET=fake-client-secret\n"
                "GDRIVE_REFRESH_TOKEN=fake-refresh-secret\n"
                "COLAB_DRIVE_MONITOR_PATH=_colab_monitor\n",
                encoding="utf-8",
            )
            cfg = load_gdrive_oauth_config(env_file)
            assert cfg is not None
            drive_client = StdlibOAuthDriveClient(cfg, opener=fake_drive)

            handler = create_handler(
                tmp_dir / "ep.json",
                allow_remote=False,
                monitor_root=monitor_root,
                state_dir=state_dir,
                drive_client=drive_client,
            )
            web = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            wt = threading.Thread(target=web.serve_forever, daemon=True)
            wt.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://127.0.0.1:{web.server_port}"

            try:
                with opener.open(f"{base}/api/v2/notebooks", timeout=5) as resp:
                    listed = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(listed["ok"])
                self.assertTrue(listed["drive_scan_enabled"])
                self.assertIsNone(listed["drive_error"])
                names = [n["name"] for n in listed["notebooks"]]
                self.assertEqual(names, ["remote_oauth.ipynb"])
                nb_entry = listed["notebooks"][0]
                self.assertEqual(nb_entry["source"], "drive_api")
                self.assertEqual(nb_entry["path"], f"gdrive://{oauth_fid}")
                self.assertEqual(nb_entry["detected_mode"], "oauth_silent")
                self.assertEqual(nb_entry["colab_url"], f"https://colab.research.google.com/drive/{oauth_fid}")

                with opener.open(f"{base}/api/v2/notebooks?include_backups=1", timeout=5) as resp:
                    listed_all = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(sorted(n["name"] for n in listed_all["notebooks"]), ["remote_oauth.ipynb", "remote_oauth_backup.ipynb"])

                def _post(path: str, action: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                    req = urllib.request.Request(
                        f"{base}{path}",
                        data=json.dumps({"action": action, **payload}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "X-Colab-Action": action},
                        method="POST",
                    )
                    try:
                        with opener.open(req, timeout=5) as r:
                            return r.status, json.loads(r.read().decode("utf-8"))
                    except urllib.error.HTTPError as he:
                        return he.code, json.loads(he.read().decode("utf-8"))

                # Direct launch uses Drive file ID URL
                code, launch_res = _post("/api/v2/notebooks/launch", "notebooks.launch", {"path": f"gdrive://{oauth_fid}", "open_browser": False})
                self.assertEqual(code, 200)
                self.assertEqual(launch_res["mode"], "colab_direct")
                self.assertEqual(launch_res["launched_url"], f"https://colab.research.google.com/drive/{oauth_fid}")

                # Dry-run inject preview does not modify Drive file
                code, prev = _post("/api/v2/notebooks/inject", "notebooks.inject", {"path": f"gdrive://{oauth_fid}", "dry_run": True})
                self.assertEqual(code, 200)
                self.assertEqual(prev["action"], "insert")
                self.assertEqual(prev["mode"], "oauth_silent")
                self.assertEqual(len(json.loads(fake_drive.items[oauth_fid]["data"].decode("utf-8"))["cells"]), 2)

                # Apply inject -> local backup + colab_agent.py synced to Drive + remote .ipynb updated in place
                code, applied = _post("/api/v2/notebooks/inject", "notebooks.inject", {"path": f"gdrive://{oauth_fid}", "dry_run": False})
                self.assertEqual(code, 200)
                self.assertTrue(Path(applied["backup_path"]).is_file())
                self.assertTrue(any(it["name"] == "colab_agent.py" for it in fake_drive.items.values()))
                remote_after = json.loads(fake_drive.items[oauth_fid]["data"].decode("utf-8"))
                self.assertEqual(len(remote_after["cells"]), 3)
                self.assertIn("colab_monitor_injected", remote_after["cells"][1]["metadata"])

                # Idempotent second inject -> update in place
                code, updated = _post("/api/v2/notebooks/inject", "notebooks.inject", {"path": f"gdrive://{oauth_fid}", "dry_run": False})
                self.assertEqual(code, 200)
                self.assertEqual(updated["action"], "update")
                self.assertEqual(len(json.loads(fake_drive.items[oauth_fid]["data"].decode("utf-8"))["cells"]), 3)

                # Clean remove -> restores original cells
                code, removed = _post("/api/v2/notebooks/remove", "notebooks.remove", {"path": f"gdrive://{oauth_fid}", "dry_run": False})
                self.assertEqual(code, 200)
                self.assertEqual(removed["removed_count"], 1)
                self.assertEqual(json.loads(fake_drive.items[oauth_fid]["data"].decode("utf-8"))["cells"], orig_nb["cells"])

                # Path traversal / invalid Drive ID -> 403 Forbidden
                code_bad, _ = _post("/api/v2/notebooks/inject", "notebooks.inject", {"path": "gdrive://../evil", "dry_run": True})
                self.assertEqual(code_bad, 403)
            finally:
                web.shutdown()
                web.server_close()


if __name__ == "__main__":
    unittest.main()
