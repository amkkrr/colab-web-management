#!/usr/bin/env python3
"""Automated acceptance gate tests for Colab Server Monitor & Control Plane v2.0."""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.colab_agent import ColabAgent, FuseDriveBackend  # noqa: E402
from control.collector import LocalControlStorage, MultiInstanceUptimeTracker, run_drive_gc  # noqa: E402
from control.protocol import (  # noqa: E402
    assert_not_in_cloud_drive,
    build_action_envelope,
    compute_liveness,
    utc_now_iso,
    validate_upstream_url,
)
from control.registry import InstanceRegistry  # noqa: E402
from server import create_handler  # noqa: E402


def _fake_hw(gpu_name: str | None = "NVIDIA L4") -> dict:
    if gpu_name:
        gpu = {
            "available": True,
            "name": gpu_name,
            "count": 1,
            "util_gpu_pct": 66.0,
            "util_mem_pct": 40.0,
            "vram_used_mib": 14000.0,
            "vram_total_mib": 23034.0,
            "vram_pct": 60.8,
            "temp_c": 60.0,
            "power_draw_w": 55.5,
            "power_limit_w": 72.0,
            "clock_sm_mhz": 2000,
            "throttle_reasons": "none",
        }
    else:
        gpu = {"available": False, "count": 0}
    ram_total = 53.0 if gpu_name else 12.7
    ram_used = 14.0 if gpu_name else 3.2
    return {
        "gpu": gpu,
        "cpu": {"cores_logical": 8, "util_pct": 20.0, "load_avg_1m": 1.2},
        "ram": {
            "used_gib": ram_used,
            "total_gib": ram_total,
            "pct": round(ram_used * 100.0 / ram_total, 1),
            "cgroup_limit_gib": ram_total,
            "oom_kill_count": 0,
        },
        "disk": {"mount": "/", "used_gib": 45.0, "free_gib": 67.6, "total_gib": 112.6, "pct": 40.0},
    }


class ControlPlaneAcceptanceTests(unittest.TestCase):
    def test_dual_channel_tunnel_fallback_and_shared_cmd_id_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            monitor_root = tmp_dir / "_colab_monitor"
            state_dir = tmp_dir / "local_state"
            ep_file = tmp_dir / "active_endpoint.json"

            service_calls = {"interrupt": 0}
            unassign_called = {"count": 0}

            agent = ColabAgent(
                project="h3_test_studio",
                label="Fake L4 Gateway",
                drive_backend=FuseDriveBackend(monitor_root),
                run_mode="online_gateway",
                api_key="agent-secret-token-99999",
                hardware_collector=lambda: _fake_hw("NVIDIA L4"),
                unassign_fn=lambda: unassign_called.__setitem__("count", unassign_called["count"] + 1),
                shutdown_grace_sec=0.05,
            )
            agent.register_service(
                "comfyui",
                check_fn=lambda: True,
                interrupt_fn=lambda: (service_calls.__setitem__("interrupt", service_calls["interrupt"] + 1), True)[1],
                port=8188,
            )
            agent.start_http_server("127.0.0.1", 0)
            agent.publish_heartbeat()

            handler = create_handler(
                ep_file,
                allow_remote=False,
                monitor_root=monitor_root,
                state_dir=state_dir,
            )
            web = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            wt = threading.Thread(target=web.serve_forever, daemon=True)
            wt.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://127.0.0.1:{web.server_port}"

            try:
                # 1. Discover instance via /api/v2/instances & verify zero secret leakage
                with opener.open(f"{base}/api/v2/instances", timeout=5) as resp:
                    raw = resp.read().decode("utf-8")
                self.assertNotIn(agent.api_key, raw)
                payload = json.loads(raw)
                self.assertEqual(len(payload["instances"]), 1)
                inst = payload["instances"][0]
                self.assertEqual(inst["instance_id"], agent.instance_id)
                self.assertEqual(inst["liveness_state"], "ONLINE_LIVE")

                # 2. Channel A action succeeds immediately via tunnel
                req_a = urllib.request.Request(
                    f"{base}/api/v2/instances/{agent.instance_id}/actions",
                    data=json.dumps({"action": "lifecycle.set_keepalive_lease", "params": {"duration_minutes": 60}}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Colab-Action": "lifecycle.set_keepalive_lease"},
                    method="POST",
                )
                with opener.open(req_a, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    res_a = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(res_a["ok"])
                self.assertEqual(res_a["channel"], "tunnel")
                self.assertEqual(res_a["status"], "succeeded")

                # 3. Shared cmd_id idempotency across Channel A and Channel B
                env = build_action_envelope(
                    agent.instance_id,
                    "ops.service_control",
                    {"service": "comfyui", "op": "interrupt"},
                    api_key=agent.api_key,
                )
                c1, ack1 = agent.execute_envelope(env, channel="tunnel")
                c2, ack2 = agent.execute_envelope(env, channel="drive")
                self.assertEqual(c1, 200)
                self.assertEqual(c2, 200)
                self.assertEqual(ack1["cmd_id"], ack2["cmd_id"])
                self.assertEqual(service_calls["interrupt"], 1, "Same cmd_id must execute strictly once across channels")

                # 4. Kill Agent HTTP server -> simulate tunnel outage -> verify <3s fallback to Drive mailbox
                if agent._http_server:
                    agent._http_server.shutdown()
                    agent._http_server.server_close()
                    agent._http_server = None

                t0 = time.monotonic()
                req_b = urllib.request.Request(
                    f"{base}/api/v2/instances/{agent.instance_id}/actions",
                    data=json.dumps({"action": "workload.pause_queue", "params": {}}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Colab-Action": "workload.pause_queue"},
                    method="POST",
                )
                with opener.open(req_b, timeout=5) as resp:
                    self.assertEqual(resp.status, 202)
                    res_b = json.loads(resp.read().decode("utf-8"))
                elapsed = time.monotonic() - t0
                self.assertLess(elapsed, 3.0, f"Fallback took too long: {elapsed:.2f}s")
                self.assertEqual(res_b["channel"], "drive")
                self.assertEqual(res_b["status"], "queued")
                cmd_id = res_b["cmd_id"]

                # Command file exists in Drive mailbox with exact cmd_id
                cmd_file = monitor_root / "commands" / agent.instance_id / f"{cmd_id}.json"
                self.assertTrue(cmd_file.is_file())

                # Fake Agent consumes Drive mailbox and writes ACK
                acks = agent.poll_drive_commands_once()
                self.assertEqual(len(acks), 1)
                self.assertEqual(acks[0]["status"], "succeeded")
                self.assertTrue(agent.workload["paused"])
                self.assertFalse(cmd_file.exists())

                # Server tracks ACK transition queued -> succeeded
                with opener.open(f"{base}/api/v2/commands/{cmd_id}", timeout=5) as resp:
                    cmd_status = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(cmd_status["command"]["status"], "succeeded")
            finally:
                agent.stop()
                web.shutdown()
                web.server_close()

    def test_security_and_path_boundary_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            monitor_root = tmp_dir / "_colab_monitor"
            state_dir = tmp_dir / "local_state"
            proj_dir = tmp_dir / "project"
            proj_dir.mkdir()
            scratch_dir = tmp_dir / "scratch_ok"
            scratch_dir.mkdir()
            (scratch_dir / "temp.bin").write_bytes(b"x" * 2048)
            outputs_dir = proj_dir / "outputs"
            outputs_dir.mkdir()
            (outputs_dir / "keep.png").write_bytes(b"important")

            log_file = tmp_dir / "comfyui.log"
            secret_key = "h3_super_secret_key_xyz987"
            log_file.write_text(
                f"Starting ComfyUI\nAuthorization: Bearer {secret_key}\n"
                "token=sk-1234567890abcdef ya29.a0AfH6SMB_secret hf_abcdefghijklmno\n",
                encoding="utf-8",
            )

            agent = ColabAgent(
                project="sec_test",
                label="Security Test Instance",
                drive_backend=FuseDriveBackend(monitor_root),
                api_key=secret_key,
                project_drive_dir=proj_dir,
                scratch_dirs=[scratch_dir],
                hardware_collector=lambda: _fake_hw("NVIDIA L4"),
                process_collector=lambda lim: [
                    {"pid": 1, "ppid": 0, "create_time": 1.0, "name": "tini", "cmdline": "/sbin/tini", "rss_mib": 4.0},
                    {"pid": 500, "ppid": 1, "create_time": 2.0, "name": "python3", "cmdline": "python3 -m ipykernel_launcher", "rss_mib": 200.0},
                    {"pid": 999999, "ppid": 1, "create_time": 3.0, "name": "stranger", "cmdline": "stranger", "rss_mib": 10.0},
                ],
            )
            agent.register_log_path("comfyui", log_file)
            agent.start_http_server("127.0.0.1", 0)
            agent.publish_heartbeat()

            # (1) Expired envelope -> 410 / rejected_expired
            expired_env = build_action_envelope(
                agent.instance_id, "lifecycle.keepalive", {}, api_key=secret_key, now_ts=time.time() - 600, ttl_sec=60
            )
            code, ack = agent.execute_envelope(expired_env, channel="drive")
            self.assertEqual(code, 410)
            self.assertEqual(ack["status"], "rejected_expired")

            # (2) Cross-instance_id -> 409 / rejected_invalid
            cross_env = build_action_envelope("inst_other_99999999", "lifecycle.keepalive", {}, api_key=secret_key)
            code, ack = agent.execute_envelope(cross_env, channel="drive")
            self.assertEqual(code, 409)
            self.assertEqual(ack["status"], "rejected_invalid")

            # (3) Missing / tampered HMAC -> 401 / rejected_unauthorized
            bad_sig_env = build_action_envelope(agent.instance_id, "lifecycle.keepalive", {}, api_key="wrong-key")
            code, ack = agent.execute_envelope(bad_sig_env, channel="drive")
            self.assertEqual(code, 401)
            self.assertEqual(ack["status"], "rejected_unauthorized")

            # (4) Protected process kill -> 403
            for pid in (1, 500, 999999):
                kill_env = build_action_envelope(
                    agent.instance_id, "ops.kill_process", {"pid": pid, "signal": "TERM"}, api_key=secret_key
                )
                code, ack = agent.execute_envelope(kill_env, channel="tunnel")
                self.assertEqual(code, 403, f"PID {pid} must be protected")

            # (5) queue_file path traversal -> 422
            bad_q_env = build_action_envelope(
                agent.instance_id, "workload.reload_drive_queue", {"queue_file": "../secrets.json"}, api_key=secret_key
            )
            code, ack = agent.execute_envelope(bad_q_env, channel="tunnel")
            self.assertEqual(code, 422)
            self.assertEqual(ack["status"], "rejected_invalid")

            # (6) cleanup_disk dry_run vs real & never touches outputs
            dry_env = build_action_envelope(
                agent.instance_id, "ops.cleanup_disk", {"targets": ["agent_scratch"], "dry_run": True}, api_key=secret_key
            )
            code, ack = agent.execute_envelope(dry_env, channel="tunnel")
            self.assertEqual(code, 200)
            self.assertEqual(ack["data"]["reclaimable_bytes"], 2048)
            self.assertTrue((scratch_dir / "temp.bin").exists())

            real_env = build_action_envelope(
                agent.instance_id, "ops.cleanup_disk", {"targets": ["agent_scratch"], "dry_run": False}, api_key=secret_key
            )
            code, ack = agent.execute_envelope(real_env, channel="tunnel")
            self.assertEqual(code, 200)
            self.assertFalse((scratch_dir / "temp.bin").exists())
            self.assertTrue((outputs_dir / "keep.png").exists())

            # (7) Server HTTP gates: Host/Origin/X-Colab-Action/log allowlist/redaction
            handler = create_handler(tmp_dir / "ep.json", allow_remote=False, monitor_root=monitor_root, state_dir=state_dir)
            web = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            wt = threading.Thread(target=web.serve_forever, daemon=True)
            wt.start()
            try:
                # Missing X-Colab-Action -> 403
                conn = http.client.HTTPConnection("127.0.0.1", web.server_port, timeout=5)
                conn.request(
                    "POST",
                    f"/api/v2/instances/{agent.instance_id}/actions",
                    body=json.dumps({"action": "lifecycle.keepalive", "params": {}}),
                    headers={"Host": f"127.0.0.1:{web.server_port}", "Content-Type": "application/json"},
                )
                self.assertEqual(conn.getresponse().status, 403)
                conn.close()

                # Non-loopback Host -> 403
                conn = http.client.HTTPConnection("127.0.0.1", web.server_port, timeout=5)
                conn.request("GET", "/api/v2/instances", headers={"Host": f"evil.example:{web.server_port}"})
                self.assertEqual(conn.getresponse().status, 403)
                conn.close()

                # Non-whitelist log source -> 400
                conn = http.client.HTTPConnection("127.0.0.1", web.server_port, timeout=5)
                conn.request("GET", f"/api/v2/instances/{agent.instance_id}/logs?source=../etc/passwd")
                self.assertEqual(conn.getresponse().status, 400)
                conn.close()

                # Whitelisted log redaction check (zero secret leakage!)
                conn = http.client.HTTPConnection("127.0.0.1", web.server_port, timeout=5)
                conn.request("GET", f"/api/v2/instances/{agent.instance_id}/logs?source=comfyui")
                resp = conn.getresponse()
                body = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                for leak in (secret_key, "sk-1234567890abcdef", "ya29.a0AfH6SMB_secret", "hf_abcdefghijklmno"):
                    self.assertNotIn(leak, body, f"Leaked secret in logs: {leak}")
                conn.close()
            finally:
                agent.stop()
                web.shutdown()
                web.server_close()

    def test_multi_instance_dedup_concurrent_uptime_and_legacy_501(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            monitor_root = tmp_dir / "_colab_monitor"
            state_dir = tmp_dir / "local_state"

            # Instance 1: NVIDIA L4 online gateway
            a1 = ColabAgent(
                project="h3_test_studio",
                label="L4 Online",
                drive_backend=FuseDriveBackend(monitor_root),
                run_mode="online_gateway",
                hardware_collector=lambda: _fake_hw("NVIDIA L4"),
            )
            url1 = a1.start_http_server("127.0.0.1", 0)
            a1.publish_heartbeat()

            # Instance 2: CPU Only offline batch (Drive only)
            a2 = ColabAgent(
                project="kokoro",
                label="CPU Audiobook",
                drive_backend=FuseDriveBackend(monitor_root),
                run_mode="offline_batch",
                hardware_collector=lambda: _fake_hw(None),
            )
            a2.publish_heartbeat()

            # Matching Legacy active_endpoint.json for a1 (MUST deduplicate!)
            ep1_dir = tmp_dir / "H3_Test_Studio"
            ep1_dir.mkdir()
            ep1 = ep1_dir / "active_endpoint.json"
            ep1.write_text(json.dumps({"base_url": url1, "api_key": a1.api_key, "instance_id": a1.instance_id}), encoding="utf-8")

            # Independent Legacy active_endpoint.json (different url -> kept as Legacy)
            ep2_dir = tmp_dir / "Legacy_Only"
            ep2_dir.mkdir()
            ep2 = ep2_dir / "active_endpoint.json"
            ep2.write_text(json.dumps({"base_url": "http://127.0.0.1:1", "api_key": "legacy-key"}), encoding="utf-8")

            try:
                reg = InstanceRegistry(monitor_root=monitor_root, primary_ep=ep1, scan_roots=[ep2_dir])
                insts = reg.discover_all()
                self.assertEqual(len(insts), 3, f"Expected 2 v2 + 1 independent legacy (deduped ep1), got {[i['instance_id'] for i in insts]}")

                # Multi-instance concurrent uptime tracking (both L4 & CPU Only online simultaneously!)
                tracker = MultiInstanceUptimeTracker(state_dir / "uptime_ledger.json")
                t0 = time.time()
                tracker.record_instances(insts, now_ts=t0)
                summary = tracker.record_instances(insts, now_ts=t0 + 45.0, force_flush=True)
                self.assertEqual(summary["online_count"], 2)
                by_hw = {item["hardware_key"]: item for item in summary["windows"]["all"]["items"]}
                self.assertTrue(by_hw["NVIDIA L4"]["is_current"])
                self.assertTrue(by_hw["CPU Only"]["is_current"])
                self.assertGreaterEqual(by_hw["NVIDIA L4"]["seconds"], 45.0)
                self.assertGreaterEqual(by_hw["CPU Only"]["seconds"], 45.0)

                # Legacy instance rejects v2-only action with 501
                legacy_id = next(i["instance_id"] for i in insts if i["is_legacy"])
                from control.router import ActionRouter

                router = ActionRouter(reg, monitor_root=monitor_root)
                code, res = router.dispatch_action(legacy_id, "ops.cleanup_disk", {"targets": ["comfy_temp"], "dry_run": True})
                self.assertEqual(code, 501)
                self.assertEqual(res["status"], "rejected_unsupported_legacy")
            finally:
                a1.stop()
                a2.stop()

    def test_sqlite_cloud_path_refusal_liveness_track_gc_and_cli_v2(self) -> None:
        # 1. Cloud-drive path refusal for SQLite / local state
        with self.assertRaises(ValueError):
            assert_not_in_cloud_drive(Path("/Users/test/Library/CloudStorage/GoogleDrive-x/MyDrive/state"))
        with self.assertRaises(ValueError):
            LocalControlStorage(Path("/tmp/GoogleDrive-fake/state"))

        # 2. Liveness UTC state machine (§3.2)
        now = 1_760_000_000.0
        st, _, skew = compute_liveness(heartbeat_at=utc_now_iso(now - 30), tunnel_ok=False, drain_requested=False, lifecycle_state="running", now_ts=now)
        self.assertEqual((st, skew), ("ONLINE_DRIVE_ONLY", False))
        st, _, _ = compute_liveness(heartbeat_at=utc_now_iso(now - 180), tunnel_ok=False, drain_requested=False, lifecycle_state="running", now_ts=now)
        self.assertEqual(st, "DEGRADED_STALE")
        st, _, skew = compute_liveness(heartbeat_at=utc_now_iso(now + 120), tunnel_ok=False, drain_requested=False, lifecycle_state="running", now_ts=now)
        self.assertEqual((st, skew), ("DEGRADED_STALE", True))
        st, _, _ = compute_liveness(heartbeat_at=utc_now_iso(now - 400), tunnel_ok=False, drain_requested=False, lifecycle_state="running", now_ts=now)
        self.assertEqual(st, "OFFLINE")

        # 3. Local SQLite storage + ColabAgent.track() + Drive GC + CLI v2
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            storage = LocalControlStorage(tmp_dir / "state")
            storage.record_sample({"instance_id": "inst_1", "hardware": _fake_hw("NVIDIA L4"), "workload": {"running": 1}}, now)
            self.assertEqual(len(storage.query_timeseries("inst_1", "1h", now_ts=now + 5)), 1)

            monitor_root = tmp_dir / "_colab_monitor"
            unassigned = {"done": False}
            agent = ColabAgent(
                project="kokoro",
                label="Track Test",
                drive_backend=FuseDriveBackend(monitor_root),
                auto_shutdown_after_batch=True,
                unassign_fn=lambda: unassigned.__setitem__("done", True),
                shutdown_grace_sec=0.02,
            )
            processed = []
            for ch in agent.track(["ch1", "ch2"], stage="合成"):
                processed.append(ch)
            self.assertEqual(processed, ["ch1", "ch2"])
            time.sleep(0.1)
            self.assertTrue(unassigned["done"])
            agent.stop()

            # GC removes old files
            gc_res = run_drive_gc(monitor_root, now_ts=time.time() + 8 * 86400.0)
            self.assertGreaterEqual(gc_res["instances"], 1)
            self.assertGreaterEqual(gc_res["secrets"], 1)

            # CLI v2 instances & non-interactive high-risk action confirmation guard
            proc_list = subprocess.run(
                [sys.executable, str(ROOT / "h3_cli_monitor.py"), "instances", "--monitor-root", str(monitor_root), "--ep", str(tmp_dir / "ep.json"), "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(proc_list.returncode, 0)
            self.assertIn("instances", json.loads(proc_list.stdout))

            proc_refuse = subprocess.run(
                [sys.executable, str(ROOT / "h3_cli_monitor.py"), "action", "--monitor-root", str(monitor_root), "--instance", "inst_x", "lifecycle.shutdown_now"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(proc_refuse.returncode, 0)
            self.assertIn("--yes", proc_refuse.stderr)

    def test_notebook_launcher_and_idempotent_safe_injector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            monitor_root = tmp_dir / "_colab_monitor"
            state_dir = tmp_dir / "local_state"
            nb_dir = tmp_dir / "Colab Notebooks"
            nb_dir.mkdir()
            outside_nb = tmp_dir / "outside.ipynb"
            outside_nb.write_text(json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": []}), encoding="utf-8")

            fuse_nb = nb_dir / "kokoro_test.ipynb"
            orig_fuse = {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {"cell_type": "markdown", "metadata": {}, "source": ["# Kokoro Test\n"]},
                    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["from google.colab import drive\ndrive.mount('/content/drive')\n"]},
                    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["print('synthesize')\n"]},
                ],
            }
            fuse_nb.write_text(json.dumps(orig_fuse, indent=1), encoding="utf-8")

            oauth_nb = nb_dir / "h3_oauth_test.ipynb"
            orig_oauth = {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["class DriveSyncManager:\n    pass\nGDRIVE = DriveSyncManager()\n"]},
                    {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["app = FastAPI()\n"]},
                ],
            }
            oauth_nb.write_text(json.dumps(orig_oauth, indent=1), encoding="utf-8")

            handler = create_handler(
                tmp_dir / "ep.json",
                allow_remote=False,
                monitor_root=monitor_root,
                state_dir=state_dir,
                notebook_roots=[nb_dir],
            )
            web = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            wt = threading.Thread(target=web.serve_forever, daemon=True)
            wt.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://127.0.0.1:{web.server_port}"

            try:
                # 1. List discovered notebooks & verify mode detection
                with opener.open(f"{base}/api/v2/notebooks", timeout=5) as resp:
                    listed = json.loads(resp.read().decode("utf-8"))
                by_name = {n["name"]: n for n in listed["notebooks"]}
                self.assertEqual(by_name["kokoro_test.ipynb"]["detected_mode"], "fuse_mounted")
                self.assertEqual(by_name["h3_oauth_test.ipynb"]["detected_mode"], "oauth_silent")
                self.assertFalse(by_name["kokoro_test.ipynb"]["injected"])

                def _post(path: str, action: str, payload: dict) -> tuple[int, dict]:
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

                # 2. Dry-run preview must NOT modify file
                code, prev = _post("/api/v2/notebooks/inject", "notebooks.inject", {"path": str(fuse_nb), "dry_run": True})
                self.assertEqual(code, 200)
                self.assertEqual(prev["insert_index"], 2)  # right after drive.mount cell
                self.assertEqual(len(json.loads(fuse_nb.read_text(encoding="utf-8"))["cells"]), 3)

                # 3. Apply injection -> local backup created, colab_agent.py synced to _colab_monitor/, cell inserted
                code, applied = _post("/api/v2/notebooks/inject", "notebooks.inject", {"path": str(fuse_nb), "dry_run": False})
                self.assertEqual(code, 200)
                self.assertTrue(Path(applied["backup_path"]).is_file())
                self.assertTrue((monitor_root / "colab_agent.py").is_file())
                nb_after = json.loads(fuse_nb.read_text(encoding="utf-8"))
                self.assertEqual(len(nb_after["cells"]), 4)
                self.assertIn("colab_monitor_injected", nb_after["cells"][2]["metadata"])

                # 4. Idempotent second injection updates in-place (still 4 cells, no duplicates!)
                code, updated = _post("/api/v2/notebooks/inject", "notebooks.inject", {"path": str(fuse_nb), "dry_run": False})
                self.assertEqual(code, 200)
                self.assertEqual(updated["action"], "update")
                self.assertEqual(len(json.loads(fuse_nb.read_text(encoding="utf-8"))["cells"]), 4)

                # 5. Clean removal restores exact original 3 cells
                code, removed = _post("/api/v2/notebooks/remove", "notebooks.remove", {"path": str(fuse_nb), "dry_run": False})
                self.assertEqual(code, 200)
                self.assertEqual(removed["removed_count"], 1)
                self.assertEqual(json.loads(fuse_nb.read_text(encoding="utf-8"))["cells"], orig_fuse["cells"])

                # 6. OAuth notebook injection places cell right after DriveSyncManager
                code, oa = _post("/api/v2/notebooks/inject", "notebooks.inject", {"path": str(oauth_nb), "dry_run": False})
                self.assertEqual(code, 200)
                self.assertEqual(oa["mode"], "oauth_silent")
                self.assertEqual(oa["insert_index"], 1)
                self.assertIn("OAuthDriveBackend", oa["cell_source"])

                # 7. Bind URL validation: reject javascript: & non-Google host, accept colab URL, launch returns URL
                code_bad, _ = _post("/api/v2/notebooks/bind-url", "notebooks.bind_url", {"path": str(fuse_nb), "colab_url": "javascript:alert(1)"})
                self.assertEqual(code_bad, 422)
                code_ok, _ = _post("/api/v2/notebooks/bind-url", "notebooks.bind_url", {"path": str(fuse_nb), "colab_url": "https://colab.research.google.com/drive/1abcXYZ"})
                self.assertEqual(code_ok, 200)
                code_launch, launched = _post("/api/v2/notebooks/launch", "notebooks.launch", {"path": str(fuse_nb), "open_browser": False})
                self.assertEqual(code_launch, 200)
                self.assertEqual(launched["mode"], "colab_direct")
                self.assertEqual(launched["launched_url"], "https://colab.research.google.com/drive/1abcXYZ")

                # 8. Path outside allowed notebook roots -> 403 Forbidden
                code_esc, _ = _post("/api/v2/notebooks/inject", "notebooks.inject", {"path": str(outside_nb), "dry_run": True})
                self.assertEqual(code_esc, 403)
            finally:
                web.shutdown()
                web.server_close()

    def test_public_repo_security_hardening_and_privacy_guard(self) -> None:
        from scripts.privacy_guard import check_file

        # 1. Upstream URL SSRF / file:// scheme validation
        self.assertEqual(validate_upstream_url("https://demo-l4.trycloudflare.com"), "https://demo-l4.trycloudflare.com")
        self.assertEqual(validate_upstream_url("http://127.0.0.1:8000"), "http://127.0.0.1:8000")
        for bad_url in ("file:///etc/passwd", "ftp://example.com/a", "http://169.254.169.254/latest", "javascript:alert(1)"):
            with self.assertRaises(ValueError, msg=f"Must reject {bad_url}"):
                validate_upstream_url(bad_url)

        # 2. Browser security headers (CSP, X-Frame-Options, Referrer-Policy, nosniff)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            handler = create_handler(
                tmp_dir / "ep.json",
                allow_remote=False,
                monitor_root=tmp_dir / "_colab_monitor",
                state_dir=tmp_dir / "state",
            )
            web = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            wt = threading.Thread(target=web.serve_forever, daemon=True)
            wt.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", web.server_port, timeout=5)
                conn.request("GET", "/")
                resp = conn.getresponse()
                resp.read()
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")
                self.assertEqual(resp.getheader("Referrer-Policy"), "no-referrer")
                self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
                csp = resp.getheader("Content-Security-Policy") or ""
                self.assertIn("default-src 'self'", csp)
                self.assertIn("frame-ancestors 'none'", csp)
                conn.close()
            finally:
                web.shutdown()
                web.server_close()

            # 3. Privacy guard blocks forbidden files, personal paths, emails, real tunnels, and private denylist terms
            leaky = tmp_dir / "notes.md"
            leaky.write_text(
                "user=/Users/alice/Documents/foo\n"
                "mail=alice.test@gmail.com\n"
                "url=https://alpha-beta-gamma-delta.trycloudflare.com\n"
                "project=MyPrivateSecretBook\n",
                encoding="utf-8",
            )
            errs = check_file("notes.md", leaky, ["MyPrivateSecretBook"])
            joined = "\n".join(errs)
            self.assertIn("local-user-path", joined)
            self.assertIn("personal-email", joined)
            self.assertIn("real-trycloudflare-tunnel", joined)
            self.assertIn("private-denylist", joined)

            pyc_errs = check_file("__pycache__/server.cpython-312.pyc", tmp_dir / "missing.pyc", [])
            self.assertTrue(any("FORBIDDEN_EXT" in e for e in pyc_errs))
            ep_errs = check_file("active_endpoint.json", tmp_dir / "missing.json", [])
            self.assertTrue(any("FORBIDDEN_FILE" in e for e in ep_errs))


if __name__ == "__main__":
    unittest.main()
