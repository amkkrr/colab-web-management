"""Headless Google Colab VM provisioner via google-colab-cli + pure OAuth2 Drive bootstrap."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess  # nosec B404 - invokes validated colab CLI binary with shell=False
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from control.drive_api import StdlibOAuthDriveClient
from control.protocol import assert_not_in_cloud_drive, atomic_write_json, redact_text, utc_now_iso

ALLOWED_GPUS: frozenset[str] = frozenset({"T4", "L4", "G4", "A100", "H100"})
ALLOWED_TPUS: frozenset[str] = frozenset({"v5e1", "v6e1"})
SAFE_SESSION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,47}$")
SESSION_LINE_RE = re.compile(
    r"^\[([^\]]+)\]\s+(\S+)\s+\|\s+Hardware:\s*([^|]+)\|\s+Shape:\s*([^|]+)\|\s+Variant:\s*([^|]+)(?:\|\s+Status:\s*(.+))?$"
)
USAGE_BALANCE_RE = re.compile(r"Current balance:\s*([0-9]+(?:\.[0-9]+)?)\s+compute units", re.I)
USAGE_RATE_RE = re.compile(r"Usage rate:\s*([0-9]+(?:\.[0-9]+)?)/hr", re.I)
USAGE_ASSIGN_RE = re.compile(r"Active assignments:\s*([0-9]+)", re.I)
BOOTSTRAP_MARKER = "__COLAB_MONITOR_BOOT_JSON__:"


def find_colab_cli(explicit_bin: str | None = None) -> str | None:
    """Locate the google-colab-cli executable if installed."""
    candidates: list[str] = []
    if explicit_bin and str(explicit_bin).strip():
        candidates.append(str(explicit_bin).strip())
    env_bin = (os.environ.get("COLAB_CLI_BIN") or "").strip()
    if env_bin:
        candidates.append(env_bin)
    which_bin = shutil.which("colab")
    if which_bin:
        candidates.append(which_bin)
    candidates.append(str(Path.home() / ".local" / "bin" / "colab"))

    for cand in candidates:
        p = Path(cand).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
    return None


def parse_sessions_output(stdout: str) -> list[dict[str, Any]]:
    """Parse `colab sessions` output lines into structured session records."""
    sessions: list[dict[str, Any]] = []
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = SESSION_LINE_RE.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        endpoint = m.group(2).strip()
        hardware = m.group(3).strip()
        shape = m.group(4).strip()
        variant = m.group(5).strip()
        status = (m.group(6) or "").strip() or "ACTIVE"
        sessions.append(
            {
                "name": name,
                "endpoint": endpoint,
                "hardware": hardware,
                "shape": shape,
                "variant": variant,
                "status": status,
                "is_orphan": name == "?",
            }
        )
    return sessions


def parse_usage_output(stdout: str) -> dict[str, Any] | None:
    """Parse `colab usage` output into structured Compute Units balance and rate."""
    text = stdout or ""
    bal_m = USAGE_BALANCE_RE.search(text)
    rate_m = USAGE_RATE_RE.search(text)
    if not bal_m or not rate_m:
        return None
    balance = float(bal_m.group(1))
    rate_hourly = float(rate_m.group(1))
    assign_m = USAGE_ASSIGN_RE.search(text)
    assignments_count = int(assign_m.group(1)) if assign_m else 0
    return {
        "available": True,
        "balance": round(balance, 2),
        "rate_hourly": round(rate_hourly, 2),
        "assignments_count": assignments_count,
        "estimated_remaining_hours": round(balance / rate_hourly, 1) if rate_hourly > 0 else None,
        "fetched_at": utc_now_iso(),
    }


def build_oauth_bootstrap_code(
    *,
    oauth_payload: dict[str, str] | None,
    project: str,
    label: str,
    session_name: str,
    idle_timeout_minutes: int,
) -> str:
    """Build Python bootstrap snippet executed inside Colab VM via stdin (no secrets in argv)."""
    cfg_json = json.dumps(oauth_payload or {}, ensure_ascii=False)
    proj = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(project or "colab_headless")).strip("_") or "colab_headless"
    lbl = re.sub(r"[\x00-\x1f\x7f]+", " ", str(label or session_name)).strip()[:80] or session_name
    idle_min = max(0, min(360, int(idle_timeout_minutes)))
    notebook_tag = f"cli:{session_name}"

    return f"""import importlib.util, json, os, pathlib, threading

_CM_OAUTH_CFG = json.loads({cfg_json!r})
_CM_OAUTH_PATH = pathlib.Path("/dev/shm/colab_gdrive_oauth.json")
if _CM_OAUTH_CFG.get("refresh_token"):
    fd = os.open(str(_CM_OAUTH_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(_CM_OAUTH_CFG, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(_CM_OAUTH_PATH, 0o600)
    os.environ["COLAB_GDRIVE_OAUTH_FILE"] = str(_CM_OAUTH_PATH)

_CM_DRIVE_LOCK = threading.Lock()
_CM_DRIVE_CACHE = {{"creds": None, "svc": None}}

def get_drive_service():
    from google.auth.transport.requests import Request as _GoogleAuthRequest
    from google.oauth2.credentials import Credentials as _GoogleCredentials
    from googleapiclient.discovery import build as _google_build

    with _CM_DRIVE_LOCK:
        cfg = json.loads(_CM_OAUTH_PATH.read_text(encoding="utf-8"))
        creds = _CM_DRIVE_CACHE["creds"]
        if creds is None:
            creds = _GoogleCredentials(
                token=None,
                refresh_token=cfg["refresh_token"],
                token_uri="https://oauth2.googleapis.com/token",
                client_id=cfg["client_id"],
                client_secret=cfg["client_secret"],
                scopes=["https://www.googleapis.com/auth/drive"],
            )
            _CM_DRIVE_CACHE["creds"] = creds
        if not creds.valid:
            creds.refresh(_GoogleAuthRequest())
            _CM_DRIVE_CACHE["svc"] = None
        if _CM_DRIVE_CACHE["svc"] is None:
            _CM_DRIVE_CACHE["svc"] = _google_build("drive", "v3", credentials=creds, cache_discovery=False)
        return _CM_DRIVE_CACHE["svc"]

if "COLAB_AGENT" in globals() and globals()["COLAB_AGENT"] is not None:
    try:
        globals()["COLAB_AGENT"].stop()
    except Exception:
        pass

_cm_agent_path = pathlib.Path("/content/colab_agent.py")
_cm_spec = importlib.util.spec_from_file_location("colab_agent", _cm_agent_path)
_cm_mod = importlib.util.module_from_spec(_cm_spec)
_cm_spec.loader.exec_module(_cm_mod)

_cm_monitor_dir = (_CM_OAUTH_CFG.get("monitor_folder_path") or "_colab_monitor").strip("/") or "_colab_monitor"
_cm_backend = _cm_mod.OAuthDriveBackend(service_factory=get_drive_service, monitor_folder_path=_cm_monitor_dir)
COLAB_AGENT = _cm_mod.ColabAgent(
    project={proj!r},
    label={lbl!r},
    notebook_name={notebook_tag!r},
    drive_backend=_cm_backend,
    run_mode="offline_batch",
    idle_timeout_minutes={idle_min},
)
COLAB_AGENT.start_background_workers()
_cm_snap = COLAB_AGENT.publish_heartbeat()
_cm_gpu = ((_cm_snap.get("hardware") or {{}}).get("gpu") or {{}})
_cm_ram = ((_cm_snap.get("hardware") or {{}}).get("ram") or {{}})
_cm_drive = ((_cm_snap.get("hardware") or {{}}).get("drive") or {{}})
_cm_out = {{
    "instance_id": COLAB_AGENT.instance_id,
    "session_name": {session_name!r},
    "drive_mode": _cm_drive.get("mode") or "oauth_silent",
    "drive_healthy": bool(_cm_drive.get("healthy", True)),
    "gpu_name": _cm_gpu.get("name") if _cm_gpu.get("available") else None,
    "ram_total_gib": _cm_ram.get("total_gib"),
}}
print({BOOTSTRAP_MARKER!r} + json.dumps(_cm_out, ensure_ascii=False), flush=True)
"""


class ColabProvisioner:
    """Manages headless Colab VM provisioning and lifecycle via google-colab-cli."""

    def __init__(
        self,
        *,
        state_dir: Path,
        agent_src_path: Path,
        drive_client: StdlibOAuthDriveClient | None = None,
        cli_bin: str | None = None,
        on_boot_complete: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.state_dir = assert_not_in_cloud_drive(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.agent_src_path = agent_src_path.resolve()
        self.drive_client = drive_client
        self.cli_path = find_colab_cli(cli_bin)
        self.on_boot_complete = on_boot_complete
        self.meta_path = self.state_dir / "provisioner_sessions.json"

        self._op_lock = threading.Lock()
        self._jobs_lock = threading.Lock()
        self._meta_lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_job_id: str | None = None
        self._sessions_cache: list[dict[str, Any]] = []
        self._sessions_cache_ts: float = 0.0
        self._sessions_error: str | None = None
        self._drive_health_cache: dict[str, Any] | None = None
        self._drive_health_ts: float = 0.0
        self._usage_cache: dict[str, Any] | None = None
        self._usage_cache_ts: float = 0.0

    def _secrets(self) -> tuple[str, ...]:
        if self.drive_client is not None:
            return self.drive_client._secrets()  # noqa: SLF001
        return ()

    def _cli_auth_configured(self) -> bool:
        token_path = Path.home() / ".config" / "colab-cli" / "token.json"
        adc_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        if token_path.is_file() or adc_path.is_file():
            return True
        gac = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
        return bool(gac and Path(gac).expanduser().is_file())

    def _load_meta(self) -> dict[str, dict[str, Any]]:
        with self._meta_lock:
            if not self.meta_path.is_file():
                return {}
            try:
                raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("sessions"), dict):
                    return dict(raw["sessions"])
            except Exception:
                pass
            return {}

    def _save_meta_entry(self, session_name: str, rec: dict[str, Any]) -> None:
        with self._meta_lock:
            sessions: dict[str, dict[str, Any]] = {}
            if self.meta_path.is_file():
                try:
                    raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and isinstance(raw.get("sessions"), dict):
                        sessions = dict(raw["sessions"])
                except Exception:
                    sessions = {}
            sessions[session_name] = rec
            atomic_write_json(self.meta_path, {"version": 1, "updated_at": utc_now_iso(), "sessions": sessions})

    def _delete_meta_entry(self, session_name: str) -> None:
        with self._meta_lock:
            if not self.meta_path.is_file():
                return
            try:
                raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
                sessions = dict(raw.get("sessions") or {}) if isinstance(raw, dict) else {}
            except Exception:
                return
            if session_name in sessions:
                sessions.pop(session_name, None)
                atomic_write_json(self.meta_path, {"version": 1, "updated_at": utc_now_iso(), "sessions": sessions})

    def _run_cli(
        self,
        args: list[str],
        *,
        timeout: float = 30.0,
        input_text: str | None = None,
    ) -> tuple[int, str, str]:
        if not self.cli_path:
            raise FileNotFoundError("google-colab-cli executable not found")
        cmd = [self.cli_path, *args]
        proc = subprocess.run(  # nosec B603 - fixed binary path + validated args, shell=False
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        stdout = redact_text(proc.stdout or "", self._secrets())
        stderr = redact_text(proc.stderr or "", self._secrets())
        return proc.returncode, stdout, stderr

    def _merge_session_meta(self, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        meta = self._load_meta()
        merged: list[dict[str, Any]] = []
        for s in sessions:
            item = dict(s)
            extra = meta.get(s["name"])
            if isinstance(extra, dict):
                item["instance_id"] = extra.get("instance_id")
                item["project"] = extra.get("project")
                item["label"] = extra.get("label")
                item["drive_mode"] = extra.get("drive_mode")
                item["created_at"] = extra.get("created_at")
            merged.append(item)
        return merged

    def list_sessions(self, *, refresh: bool = True) -> list[dict[str, Any]]:
        now = time.time()
        if not self.cli_path:
            self._sessions_cache = []
            self._sessions_error = "未检测到 colab CLI"
            return []
        if not refresh and self._sessions_cache_ts > 0 and (now - self._sessions_cache_ts) < 15.0:
            return self._merge_session_meta(self._sessions_cache)
        try:
            rc, stdout, stderr = self._run_cli(["sessions"], timeout=25.0)
            if rc != 0:
                msg = (stderr or stdout).strip() or f"colab sessions 退出码 {rc}"
                self._sessions_error = msg[:300]
                return self._merge_session_meta(self._sessions_cache)
            parsed = parse_sessions_output(stdout)
            self._sessions_cache = parsed
            self._sessions_cache_ts = time.time()
            self._sessions_error = None
            return self._merge_session_meta(parsed)
        except Exception as exc:
            self._sessions_error = redact_text(str(exc), self._secrets())
            return self._merge_session_meta(self._sessions_cache)

    def _get_drive_health(self, *, refresh: bool = False) -> dict[str, Any] | None:
        if self.drive_client is None:
            return None
        now = time.time()
        if not refresh and self._drive_health_cache is not None and (now - self._drive_health_ts) < 60.0:
            return dict(self._drive_health_cache)
        health = self.drive_client.check_health()
        self._drive_health_cache = dict(health)
        self._drive_health_ts = now
        return health

    def get_usage(self, *, refresh: bool = False) -> dict[str, Any] | None:
        if not self.cli_path:
            return None
        now = time.time()
        if not refresh and self._usage_cache is not None and (now - self._usage_cache_ts) < 60.0:
            return dict(self._usage_cache)
        try:
            rc, stdout, _ = self._run_cli(["usage"], timeout=20.0)
            if rc == 0:
                parsed = parse_usage_output(stdout)
                if parsed is not None:
                    self._usage_cache = dict(parsed)
                    self._usage_cache_ts = time.time()
                    return dict(parsed)
        except Exception:
            pass
        return dict(self._usage_cache) if self._usage_cache is not None else None

    def get_status(self, *, refresh_sessions: bool = False) -> dict[str, Any]:
        with self._jobs_lock:
            active_job = dict(self._jobs[self._active_job_id]) if self._active_job_id in self._jobs else None
            recent = sorted(self._jobs.values(), key=lambda j: j.get("started_at") or "", reverse=True)[:10]
            recent_copy = [dict(j) for j in recent]

        sessions = self.list_sessions(refresh=refresh_sessions) if (refresh_sessions or self._sessions_cache_ts == 0.0) else self._merge_session_meta(self._sessions_cache)
        return {
            "ok": True,
            "cli_available": bool(self.cli_path),
            "cli_path": self.cli_path,
            "cli_auth_configured": self._cli_auth_configured(),
            "drive_oauth_configured": self.drive_client is not None,
            "drive_monitor_path": self.drive_client.config.monitor_folder_path if self.drive_client else None,
            "drive_oauth_health": self._get_drive_health(refresh=refresh_sessions),
            "usage": self.get_usage(refresh=refresh_sessions),
            "busy": active_job is not None,
            "active_job": active_job,
            "recent_jobs": recent_copy,
            "sessions": sessions,
            "sessions_error": self._sessions_error,
        }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _update_job(self, job_id: str, **updates: Any) -> None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            log_line = updates.pop("log_line", None)
            if log_line:
                logs = list(job.get("steps_log") or [])
                logs.append(f"[{utc_now_iso()}] {log_line}")
                job["steps_log"] = logs[-30:]
            job.update(updates)

    def start_boot(
        self,
        *,
        runtime_type: str = "cpu",
        gpu: str = "",
        tpu: str = "",
        high_mem: bool = False,
        session_name: str = "",
        project: str = "",
        label: str = "",
        idle_timeout_minutes: int = 60,
        attach_agent: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        if not self.cli_path:
            return 503, {"ok": False, "error": "未检测到 google-colab-cli，请先安装并完成 OAuth2 登录。"}

        rt = str(runtime_type or "cpu").strip().lower()
        clean_gpu = str(gpu or "").strip().upper()
        clean_tpu = str(tpu or "").strip().lower()
        if rt not in ("cpu", "gpu", "tpu"):
            return 422, {"ok": False, "error": f"不支持的算力类型: {runtime_type}"}
        if rt == "gpu" and clean_gpu not in ALLOWED_GPUS:
            return 422, {"ok": False, "error": f"不支持的 GPU 型号: {gpu}（允许: {', '.join(sorted(ALLOWED_GPUS))}）"}
        if rt == "tpu" and clean_tpu not in ALLOWED_TPUS:
            return 422, {"ok": False, "error": f"不支持的 TPU 型号: {tpu}（允许: {', '.join(sorted(ALLOWED_TPUS))}）"}

        raw_name = str(session_name or "").strip()
        if raw_name:
            if not SAFE_SESSION_RE.match(raw_name):
                return 422, {
                    "ok": False,
                    "error": "会话名只允许字母/数字开头，包含字母、数字、'.'、'_'、'-'（最长 48 位）。",
                }
            resolved_session = raw_name
        else:
            tag = clean_gpu.lower() if rt == "gpu" else (clean_tpu if rt == "tpu" else "cpu")
            resolved_session = f"vm-{tag}-{secrets.token_hex(3)}"

        if attach_agent and self.drive_client is None:
            return 422, {
                "ok": False,
                "error": "未配置纯 OAuth2 Google Drive 凭据（COLAB_GDRIVE_OAUTH_ENV 或 GDRIVE_*），无法注入纯 OAuth2 监控探针。",
            }

        idle_min = max(0, min(360, int(idle_timeout_minutes)))
        clean_proj = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(project or resolved_session)).strip("_") or "colab_headless"
        clean_lbl = re.sub(r"[\x00-\x1f\x7f]+", " ", str(label or resolved_session)).strip()[:80] or resolved_session
        runtime_spec = f"gpu:{clean_gpu}" if rt == "gpu" else (f"tpu:{clean_tpu}" if rt == "tpu" else "cpu")
        if high_mem:
            runtime_spec += "+high-mem"

        job_id = f"boot_{int(time.time())}_{secrets.token_hex(4)}"
        with self._jobs_lock:
            if self._active_job_id is not None:
                return 409, {
                    "ok": False,
                    "error": f"当前已有开机任务在执行中（{self._active_job_id}），请等待完成后再发起。",
                }
            job: dict[str, Any] = {
                "ok": True,
                "job_id": job_id,
                "status": "queued",
                "step": "queued",
                "session_name": resolved_session,
                "runtime_spec": runtime_spec,
                "attach_agent": bool(attach_agent),
                "instance_id": None,
                "colab_url": None,
                "error": None,
                "started_at": utc_now_iso(),
                "finished_at": None,
                "steps_log": [f"[{utc_now_iso()}] 开机任务已排队：{resolved_session} ({runtime_spec})"],
            }
            self._jobs[job_id] = job
            self._active_job_id = job_id

        t = threading.Thread(
            target=self._run_boot_job,
            kwargs={
                "job_id": job_id,
                "runtime_type": rt,
                "gpu": clean_gpu,
                "tpu": clean_tpu,
                "high_mem": bool(high_mem),
                "session_name": resolved_session,
                "project": clean_proj,
                "label": clean_lbl,
                "idle_timeout_minutes": idle_min,
                "attach_agent": bool(attach_agent),
            },
            daemon=True,
            name=f"colab-boot-{resolved_session}",
        )
        t.start()
        return 202, dict(job)

    def _run_boot_job(
        self,
        *,
        job_id: str,
        runtime_type: str,
        gpu: str,
        tpu: str,
        high_mem: bool,
        session_name: str,
        project: str,
        label: str,
        idle_timeout_minutes: int,
        attach_agent: bool,
    ) -> None:
        vm_allocated = False
        try:
            with self._op_lock:
                # Step 1: Allocate VM via `colab new`
                self._update_job(
                    job_id,
                    status="running",
                    step="allocating_vm",
                    log_line=f"正在向 Colab 申请算力 ({session_name})…",
                )
                new_args = ["new", "-s", session_name]
                if runtime_type == "gpu":
                    new_args.extend(["--gpu", gpu])
                elif runtime_type == "tpu":
                    new_args.extend(["--tpu", tpu])
                if high_mem:
                    new_args.append("--high-mem")

                rc, stdout, stderr = self._run_cli(new_args, timeout=180.0)
                if rc != 0:
                    err = (stderr or stdout).strip() or f"colab new 失败（退出码 {rc}）"
                    raise RuntimeError(err[:500])

                vm_allocated = True
                self._save_meta_entry(
                    session_name,
                    {
                        "session_name": session_name,
                        "project": project,
                        "label": label,
                        "runtime_type": runtime_type,
                        "hardware": gpu or tpu or "CPU",
                        "high_mem": high_mem,
                        "drive_mode": "oauth_silent" if attach_agent else "none",
                        "instance_id": None,
                        "created_at": utc_now_iso(),
                    },
                )
                self._update_job(job_id, log_line="Colab VM 分配成功。")

                boot_info: dict[str, Any] = {}
                if attach_agent:
                    # Step 2: Upload single-file colab_agent.py
                    self._update_job(
                        job_id,
                        step="uploading_agent",
                        log_line="正在上传 colab_agent.py 到 /content/colab_agent.py…",
                    )
                    rc_up, out_up, err_up = self._run_cli(
                        ["upload", "-s", session_name, str(self.agent_src_path), "/content/colab_agent.py"],
                        timeout=60.0,
                    )
                    if rc_up != 0:
                        err = (err_up or out_up).strip() or f"colab upload 失败（退出码 {rc_up}）"
                        raise RuntimeError(err[:500])

                    # Step 3: Inject OAuth2 credentials via stdin -> /dev/shm & start ColabAgent
                    self._update_job(
                        job_id,
                        step="bootstrapping_oauth_agent",
                        log_line="正在通过纯 OAuth2 API 注入凭据并启动 ColabAgent 探针…",
                    )
                    oauth_payload = self.drive_client.export_vm_payload() if self.drive_client else None
                    code = build_oauth_bootstrap_code(
                        oauth_payload=oauth_payload,
                        project=project,
                        label=label,
                        session_name=session_name,
                        idle_timeout_minutes=idle_timeout_minutes,
                    )
                    rc_ex, out_ex, err_ex = self._run_cli(
                        ["exec", "-s", session_name, "--timeout", "120"],
                        timeout=140.0,
                        input_text=code,
                    )
                    if rc_ex != 0:
                        err = (err_ex or out_ex).strip() or f"colab exec 探针引导失败（退出码 {rc_ex}）"
                        raise RuntimeError(err[:600])

                    for line in out_ex.splitlines():
                        if BOOTSTRAP_MARKER in line:
                            raw_json = line.split(BOOTSTRAP_MARKER, 1)[1].strip()
                            parsed = json.loads(raw_json)
                            if isinstance(parsed, dict):
                                boot_info = parsed
                                break

                    if not boot_info.get("instance_id"):
                        raise RuntimeError(
                            f"探针未返回 instance_id，输出摘要: {(out_ex or err_ex).strip()[:300]}"
                        )

                    self._save_meta_entry(
                        session_name,
                        {
                            "session_name": session_name,
                            "project": project,
                            "label": label,
                            "runtime_type": runtime_type,
                            "hardware": boot_info.get("gpu_name") or gpu or tpu or "CPU",
                            "high_mem": high_mem,
                            "drive_mode": boot_info.get("drive_mode") or "oauth_silent",
                            "instance_id": boot_info["instance_id"],
                            "created_at": utc_now_iso(),
                        },
                    )
                    self._update_job(
                        job_id,
                        instance_id=boot_info["instance_id"],
                        log_line=f"探针已上线 (OAuth2): {boot_info['instance_id']}",
                    )

                # Step 4: Fetch browser attach URL (best effort)
                colab_url: str | None = None
                try:
                    rc_u, out_u, _ = self._run_cli(["url", "-s", session_name], timeout=25.0)
                    if rc_u == 0:
                        colab_url = self._extract_colab_url(out_u)
                except Exception:
                    colab_url = None

                # Step 5: Sync Drive mailbox immediately so UI shows the new instance right away
                if self.on_boot_complete is not None:
                    try:
                        self.on_boot_complete(boot_info)
                    except Exception:
                        pass

                self._sessions_cache_ts = 0.0
                self._usage_cache_ts = 0.0
                self.list_sessions(refresh=True)
                self._update_job(
                    job_id,
                    status="succeeded",
                    step="completed",
                    colab_url=colab_url,
                    finished_at=utc_now_iso(),
                    log_line="一键开机完成。",
                )
        except Exception as exc:
            msg = redact_text(str(exc), self._secrets())
            if vm_allocated:
                self._sessions_cache_ts = 0.0
                try:
                    self.list_sessions(refresh=True)
                except Exception:
                    pass
            self._update_job(
                job_id,
                status="failed",
                step="failed",
                error=msg,
                finished_at=utc_now_iso(),
                log_line=f"开机失败: {msg[:300]}",
            )
        finally:
            with self._jobs_lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None

    @staticmethod
    def _extract_colab_url(stdout: str) -> str | None:
        for raw in (stdout or "").splitlines():
            cand = raw.strip()
            if not cand.startswith("https://"):
                continue
            parsed = urlparse(cand)
            if parsed.scheme == "https" and (parsed.hostname or "").lower() == "colab.research.google.com":
                return cand
        return None

    def stop_session(self, session_name: str) -> tuple[int, dict[str, Any]]:
        clean = str(session_name or "").strip()
        if not SAFE_SESSION_RE.match(clean):
            return 422, {"ok": False, "error": f"无效的会话名: {session_name}"}
        if not self.cli_path:
            return 503, {"ok": False, "error": "未检测到 google-colab-cli"}
        if not self._op_lock.acquire(blocking=False):
            return 409, {"ok": False, "error": "当前有开机/停止操作正在执行中，请稍候重试。"}
        try:
            rc, stdout, stderr = self._run_cli(["stop", "-s", clean], timeout=45.0)
            if rc != 0:
                msg = (stderr or stdout).strip() or f"colab stop 退出码 {rc}"
                return 502, {"ok": False, "error": msg[:400]}
            self._delete_meta_entry(clean)
            self._sessions_cache_ts = 0.0
            self._usage_cache_ts = 0.0
            sessions = self.list_sessions(refresh=True)
            return 200, {
                "ok": True,
                "session_name": clean,
                "message": f"已释放 Colab 会话 {clean}。",
                "sessions": sessions,
            }
        except Exception as exc:
            return 502, {"ok": False, "error": redact_text(str(exc), self._secrets())}
        finally:
            self._op_lock.release()

    def get_session_url(self, session_name: str) -> tuple[int, dict[str, Any]]:
        clean = str(session_name or "").strip()
        if not SAFE_SESSION_RE.match(clean):
            return 422, {"ok": False, "error": f"无效的会话名: {session_name}"}
        if not self.cli_path:
            return 503, {"ok": False, "error": "未检测到 google-colab-cli"}
        try:
            rc, stdout, stderr = self._run_cli(["url", "-s", clean], timeout=25.0)
            if rc != 0:
                msg = (stderr or stdout).strip() or f"colab url 退出码 {rc}"
                return 502, {"ok": False, "error": msg[:400]}
            url = self._extract_colab_url(stdout)
            if not url:
                return 502, {"ok": False, "error": "未能从 colab url 输出中解析出有效链接"}
            return 200, {"ok": True, "session_name": clean, "url": url}
        except Exception as exc:
            return 502, {"ok": False, "error": redact_text(str(exc), self._secrets())}
