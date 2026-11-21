#!/usr/bin/env python3
"""
Colab Server Monitor & Control Plane — Single-File Zero-Dependency Agent (v2.0)

Features:
- Zero third-party dependencies for telemetry & Drive FUSE mailbox (Python 3.11+ stdlib only)
- Built-in DriveBackend abstraction: FuseDriveBackend + OAuthDriveBackend (reuses existing Drive v3 service)
- Dual-channel execution: Channel A (/agent/v1/* HTTP via FastAPI or built-in stdlib server)
  + Channel B (Google Drive _colab_monitor/commands & acks mailbox)
- Unified HMAC-SHA256 verification, instance_id & expires_at (+/-60s) anti-replay, and shared cmd_id idempotency
- Enumerated action allowlist (5 categories, 19 actions) — strictly no arbitrary RCE
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import parse_qs, urlparse

SCHEMA_VERSION = "2.0"
CLOCK_SKEW_TOLERANCE_SEC = 60.0
MAX_LOG_LINES = 2000
MAX_LOG_BYTES = 512 * 1024

ALLOWED_LOG_SOURCES = frozenset({"comfyui", "cloudflared", "gateway", "agent"})
SAFE_QUEUE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.json$")

PROTECTED_PROCESS_NAMES = frozenset(
    {
        "init",
        "systemd",
        "tini",
        "sshd",
        "drive",
        "fusermount",
        "fusermount3",
        "colab-fileshim",
        "dap_multiplexer",
        "node",
        "ipykernel_launcher",
    }
)

_REDACT_PATTERNS = (
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{6,}", re.I), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[REDACTED_SK]"),
    (re.compile(r"\bya29\.[A-Za-z0-9._-]{10,}\b"), "[REDACTED_OAUTH]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{10,}\b"), "[REDACTED_HF]"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"), "[REDACTED_GAPI]"),
    (
        re.compile(r'("(?:refresh_token|access_token|api_key|client_secret)"\s*:\s*")[^"]*(")', re.I),
        r'\1[REDACTED]\2',
    ),
)


def _utc_iso(ts: float | None = None) -> str:
    t = time.time() if ts is None else float(ts)
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(raw: str | None) -> float | None:
    if not raw or not isinstance(raw, str):
        return None
    txt = raw.strip()
    if not txt:
        return None
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _canonical_bytes(envelope: dict[str, Any]) -> bytes:
    clean = {k: v for k, v in envelope.items() if k != "sig"}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sign_envelope(envelope: dict[str, Any], api_key: str) -> str:
    digest = hmac.new(api_key.encode("utf-8"), _canonical_bytes(envelope), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def _verify_sig(envelope: dict[str, Any], api_key: str) -> bool:
    if not api_key or not isinstance(envelope, dict):
        return False
    sig = envelope.get("sig")
    if not isinstance(sig, str) or not sig.startswith("hmac-sha256:"):
        return False
    return hmac.compare_digest(sig, _sign_envelope(envelope, api_key))


def _redact_text(text: str, extra_secrets: Iterable[str] = ()) -> str:
    out = text
    for sec in sorted({s for s in extra_secrets if isinstance(s, str) and len(s) >= 6}, key=len, reverse=True):
        out = out.replace(sec, "[REDACTED]")
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.parent / f"._tmp_{os.getpid()}_{threading.get_ident()}_{path.name}"
    written = False
    try:
        tmp.write_text(encoded, encoding="utf-8")
        for attempt in range(3):
            try:
                os.replace(tmp, path)
                written = True
                break
            except OSError:
                if attempt < 2:
                    time.sleep(0.02)
        if not written:
            path.write_text(encoded, encoding="utf-8")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# DriveBackend abstraction (FUSE & OAuth v3 API)
# ---------------------------------------------------------------------------


class DriveBackend:
    """Minimal Drive JSON I/O protocol decoupled from project-locked SyncManagers."""

    mode: str = "fuse"

    def ensure_dir(self, rel_dir: str) -> None:
        raise NotImplementedError

    def put_json(self, rel_path: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def get_json(self, rel_path: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def list_json(self, rel_dir: str) -> list[tuple[str, dict[str, Any]]]:
        raise NotImplementedError

    def delete_file(self, rel_path: str) -> bool:
        raise NotImplementedError


class FuseDriveBackend(DriveBackend):
    """Filesystem backend for /content/drive/MyDrive/<subdir>/_colab_monitor or local tests."""

    mode = "fuse"

    def __init__(self, monitor_root: Path | str) -> None:
        self.root = Path(monitor_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, rel_path: str) -> Path:
        clean = rel_path.lstrip("/")
        target = (self.root / clean).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError(f"Path escapes monitor_root: {rel_path}")
        return target

    def ensure_dir(self, rel_dir: str) -> None:
        self._resolve(rel_dir).mkdir(parents=True, exist_ok=True)

    def put_json(self, rel_path: str, payload: dict[str, Any]) -> None:
        target = self._resolve(rel_path)
        _atomic_write_json(target, payload)

    def get_json(self, rel_path: str) -> dict[str, Any] | None:
        target = self._resolve(rel_path)
        if not target.is_file():
            return None
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def list_json(self, rel_dir: str) -> list[tuple[str, dict[str, Any]]]:
        folder = self._resolve(rel_dir)
        if not folder.is_dir():
            return []
        results: list[tuple[str, dict[str, Any]]] = []
        for fp in sorted(folder.glob("*.json"), key=lambda p: p.name):
            if fp.name.startswith("._tmp_"):
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    rel = fp.relative_to(self.root).as_posix()
                    results.append((rel, data))
            except Exception:
                continue
        return results

    def delete_file(self, rel_path: str) -> bool:
        target = self._resolve(rel_path)
        try:
            target.unlink(missing_ok=True)
            return True
        except OSError:
            return False


class OAuthDriveBackend(DriveBackend):
    """
    Google Drive v3 API backend reusing an existing service factory (e.g. GDRIVE._service).
    Resolves `_colab_monitor` at the configured Drive path, caches fileIds for atomic in-place
    updates, and deduplicates duplicate same-name files by latest modifiedTime.
    """

    mode = "oauth_silent"

    def __init__(
        self,
        service_factory: Callable[[], Any],
        monitor_folder_path: str = "_colab_monitor",
    ) -> None:
        self._service_factory = service_factory
        self._monitor_folder_path = monitor_folder_path.strip("/")
        self._lock = threading.Lock()
        self._folder_ids: dict[str, str] = {}
        self._file_ids: dict[str, str] = {}

    @staticmethod
    def _escape_q(val: str) -> str:
        return val.replace("\\", "\\\\").replace("'", "\\'")

    def _find_or_create_folder(self, svc: Any, parent_id: str, name: str) -> str:
        q = (
            f"name = '{self._escape_q(name)}' and '{parent_id}' in parents "
            "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        res = svc.files().list(q=q, fields="files(id, modifiedTime)", orderBy="modifiedTime desc", pageSize=5).execute()
        files = res.get("files") or []
        if files:
            return files[0]["id"]
        created = (
            svc.files()
            .create(
                body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
                fields="id",
            )
            .execute()
        )
        return created["id"]

    def _resolve_folder_id_locked(self, svc: Any, rel_dir: str) -> str:
        norm = rel_dir.strip("/")
        if norm in self._folder_ids:
            return self._folder_ids[norm]
        parts = [p for p in self._monitor_folder_path.split("/") if p]
        if norm:
            parts.extend(p for p in norm.split("/") if p)
        cur_id = "root"
        prefix = ""
        for idx, part in enumerate(parts):
            prefix = f"{prefix}/{part}" if prefix else part
            cache_key = "" if idx < len(self._monitor_folder_path.split("/")) else "/".join(parts[len(self._monitor_folder_path.split("/")): idx + 1])
            if cache_key and cache_key in self._folder_ids:
                cur_id = self._folder_ids[cache_key]
            else:
                cur_id = self._find_or_create_folder(svc, cur_id, part)
                if idx == len(self._monitor_folder_path.split("/")) - 1:
                    self._folder_ids[""] = cur_id
                elif cache_key:
                    self._folder_ids[cache_key] = cur_id
        self._folder_ids[norm] = cur_id
        return cur_id

    def ensure_dir(self, rel_dir: str) -> None:
        with self._lock:
            svc = self._service_factory()
            self._resolve_folder_id_locked(svc, rel_dir)

    def put_json(self, rel_path: str, payload: dict[str, Any]) -> None:
        from googleapiclient.http import MediaIoBaseUpload  # type: ignore

        clean = rel_path.strip("/")
        parent_rel, _, name = clean.rpartition("/")
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        media = MediaIoBaseUpload(io.BytesIO(raw), mimetype="application/json", resumable=False)
        with self._lock:
            svc = self._service_factory()
            folder_id = self._resolve_folder_id_locked(svc, parent_rel)
            file_id = self._file_ids.get(clean)
            if not file_id:
                q = f"name = '{self._escape_q(name)}' and '{folder_id}' in parents and trashed = false"
                res = svc.files().list(q=q, fields="files(id, modifiedTime)", orderBy="modifiedTime desc", pageSize=10).execute()
                matches = res.get("files") or []
                if matches:
                    file_id = matches[0]["id"]
                    self._file_ids[clean] = file_id
                    for stale in matches[1:]:
                        try:
                            svc.files().delete(fileId=stale["id"]).execute()
                        except Exception:
                            pass
            if file_id:
                try:
                    svc.files().update(fileId=file_id, media_body=media, fields="id").execute()
                    return
                except Exception:
                    self._file_ids.pop(clean, None)
            created = svc.files().create(body={"name": name, "parents": [folder_id]}, media_body=media, fields="id").execute()
            self._file_ids[clean] = created["id"]

    def get_json(self, rel_path: str) -> dict[str, Any] | None:
        clean = rel_path.strip("/")
        parent_rel, _, name = clean.rpartition("/")
        with self._lock:
            svc = self._service_factory()
            folder_id = self._resolve_folder_id_locked(svc, parent_rel)
            q = f"name = '{self._escape_q(name)}' and '{folder_id}' in parents and trashed = false"
            res = svc.files().list(q=q, fields="files(id, modifiedTime)", orderBy="modifiedTime desc", pageSize=5).execute()
            matches = res.get("files") or []
            if not matches:
                return None
            file_id = matches[0]["id"]
            self._file_ids[clean] = file_id
            raw = svc.files().get_media(fileId=file_id).execute()
            try:
                data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
                return data if isinstance(data, dict) else None
            except Exception:
                return None

    def list_json(self, rel_dir: str) -> list[tuple[str, dict[str, Any]]]:
        norm = rel_dir.strip("/")
        with self._lock:
            svc = self._service_factory()
            folder_id = self._resolve_folder_id_locked(svc, norm)
            q = f"'{folder_id}' in parents and trashed = false"
            res = svc.files().list(q=q, fields="files(id, name, modifiedTime)", orderBy="modifiedTime desc", pageSize=100).execute()
            seen_names: set[str] = set()
            results: list[tuple[str, dict[str, Any]]] = []
            for item in res.get("files") or []:
                fname = item.get("name") or ""
                if not fname.endswith(".json") or fname.startswith("._tmp_") or fname in seen_names:
                    continue
                seen_names.add(fname)
                try:
                    raw = svc.files().get_media(fileId=item["id"]).execute()
                    data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
                    if isinstance(data, dict):
                        rel = f"{norm}/{fname}" if norm else fname
                        self._file_ids[rel] = item["id"]
                        results.append((rel, data))
                except Exception:
                    continue
            return results

    def delete_file(self, rel_path: str) -> bool:
        clean = rel_path.strip("/")
        parent_rel, _, name = clean.rpartition("/")
        with self._lock:
            svc = self._service_factory()
            folder_id = self._resolve_folder_id_locked(svc, parent_rel)
            q = f"name = '{self._escape_q(name)}' and '{folder_id}' in parents and trashed = false"
            res = svc.files().list(q=q, fields="files(id)", pageSize=10).execute()
            deleted = False
            for item in res.get("files") or []:
                try:
                    svc.files().delete(fileId=item["id"]).execute()
                    deleted = True
                except Exception:
                    pass
            self._file_ids.pop(clean, None)
            return deleted


# ---------------------------------------------------------------------------
# Zero-dependency hardware & process collectors
# ---------------------------------------------------------------------------


def collect_gpu_metrics() -> dict[str, Any]:
    """Query GPU stats using nvidia-smi without pynvml."""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit,clocks.sm",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return {"available": False, "count": 0}
        lines = [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip()]
        parts = [p.strip() for p in lines[0].split(",")]
        if len(parts) < 9:
            return {"available": False, "count": 0}

        def _f(val: str) -> float | None:
            try:
                return float(val)
            except ValueError:
                return None

        used = _f(parts[3]) or 0.0
        total = _f(parts[4]) or 0.0
        vram_pct = round(used * 100.0 / total, 1) if total > 0 else 0.0
        return {
            "available": True,
            "name": parts[0],
            "count": len(lines),
            "util_gpu_pct": _f(parts[1]) or 0.0,
            "util_mem_pct": _f(parts[2]) or 0.0,
            "vram_used_mib": round(used, 1),
            "vram_total_mib": round(total, 1),
            "vram_pct": vram_pct,
            "temp_c": _f(parts[5]),
            "power_draw_w": _f(parts[6]),
            "power_limit_w": _f(parts[7]),
            "clock_sm_mhz": int(_f(parts[8]) or 0),
            "throttle_reasons": "none",
        }
    except Exception:
        return {"available": False, "count": 0}


_PROC_STAT_LOCK = threading.Lock()
_PREV_PROC_STAT: tuple[int, int] | None = None


def _sample_proc_stat_cpu_pct() -> float | None:
    """Compute interval CPU utilization (%) from Linux /proc/stat jiffies delta."""
    global _PREV_PROC_STAT
    stat_path = Path("/proc/stat")
    if not stat_path.is_file():
        return None
    try:
        first_line = stat_path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return None
    parts = first_line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    try:
        vals = [int(x) for x in parts[1:]]
    except ValueError:
        return None
    total = sum(vals)
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    with _PROC_STAT_LOCK:
        prev = _PREV_PROC_STAT
        _PREV_PROC_STAT = (total, idle)
    if prev is None:
        return None
    dt = total - prev[0]
    di = idle - prev[1]
    if dt <= 0:
        return None
    busy_pct = (dt - max(0, di)) * 100.0 / dt
    return round(max(0.0, min(100.0, busy_pct)), 1)


def collect_host_metrics(disk_mount: str = "/") -> dict[str, Any]:
    """Collect CPU, RAM, and Disk metrics using stdlib + /proc (Linux) with safe cross-platform fallbacks."""
    cores = os.cpu_count() or 1
    try:
        load_1m = round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        load_1m = 0.0
    sampled_cpu = _sample_proc_stat_cpu_pct()
    cpu_pct = sampled_cpu if sampled_cpu is not None else round(min(100.0, (load_1m / max(1, cores)) * 100.0), 1)

    ram_total_gib = 0.0
    ram_used_gib = 0.0
    cgroup_limit_gib: float | None = None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        kv: dict[str, int] = {}
        try:
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    parts = v.strip().split()
                    if parts and parts[0].isdigit():
                        kv[k.strip()] = int(parts[0])
            tot_kb = kv.get("MemTotal", 0)
            avail_kb = kv.get("MemAvailable", kv.get("MemFree", 0))
            ram_total_gib = round(tot_kb / (1024 * 1024), 2)
            ram_used_gib = round(max(0, tot_kb - avail_kb) / (1024 * 1024), 2)
        except OSError:
            pass
    for cg_path in (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        if cg_path.is_file():
            try:
                val = cg_path.read_text(encoding="utf-8").strip()
                if val.isdigit():
                    num = int(val)
                    if 0 < num < (1 << 50):
                        cgroup_limit_gib = round(num / (1024**3), 2)
                        break
            except OSError:
                pass

    ram_pct = round(ram_used_gib * 100.0 / ram_total_gib, 1) if ram_total_gib > 0 else 0.0

    try:
        usage = shutil.disk_usage(disk_mount)
        d_total = round(usage.total / (1024**3), 2)
        d_used = round(usage.used / (1024**3), 2)
        d_free = round(usage.free / (1024**3), 2)
        d_pct = round(usage.used * 100.0 / usage.total, 1) if usage.total > 0 else 0.0
    except OSError:
        d_total = d_used = d_free = d_pct = 0.0

    return {
        "gpu": collect_gpu_metrics(),
        "cpu": {"cores_logical": cores, "util_pct": cpu_pct, "load_avg_1m": load_1m},
        "ram": {
            "used_gib": ram_used_gib,
            "total_gib": ram_total_gib,
            "pct": ram_pct,
            "cgroup_limit_gib": cgroup_limit_gib if cgroup_limit_gib is not None else ram_total_gib,
            "oom_kill_count": 0,
        },
        "disk": {"mount": disk_mount, "used_gib": d_used, "free_gib": d_free, "total_gib": d_total, "pct": d_pct},
    }


# ---------------------------------------------------------------------------
# ColabAgent Core
# ---------------------------------------------------------------------------


class ColabAgent:
    """Single-file Colab monitor & control agent supporting dual-channel operation."""

    def __init__(
        self,
        *,
        project: str,
        label: str,
        drive_backend: DriveBackend,
        notebook_name: str = "",
        run_mode: str = "offline_batch",
        instance_id: str | None = None,
        api_key: str | None = None,
        heartbeat_interval_sec: int = 30,
        command_poll_interval_sec: int = 15,
        idle_timeout_minutes: int = 60,
        auto_shutdown_after_batch: bool = False,
        project_drive_dir: Path | str | None = None,
        legacy_ep_file: Path | str | None = None,
        scratch_dirs: list[Path | str] | None = None,
        hardware_collector: Callable[[], dict[str, Any]] | None = None,
        process_collector: Callable[[int], list[dict[str, Any]]] | None = None,
        unassign_fn: Callable[[], None] | None = None,
        tunnel_rebuilder: Callable[[], str] | None = None,
        gateway_controller: dict[str, Callable[..., Any]] | None = None,
        queue_controller: dict[str, Callable[..., Any]] | None = None,
        shutdown_grace_sec: float = 2.0,
    ) -> None:
        now = time.time()
        self.project = project
        self.label = label
        self.drive = drive_backend
        self.notebook_name = notebook_name
        self.run_mode = run_mode
        stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.instance_id = instance_id or f"inst_{stamp}_{secrets.token_hex(4)}"
        self._api_key = (api_key or secrets.token_urlsafe(32)).strip()
        self.started_at_ts = now
        self.started_at = _utc_iso(now)
        self.heartbeat_interval_sec = max(15, min(60, int(heartbeat_interval_sec)))
        self.command_poll_interval_sec = max(1, min(60, int(command_poll_interval_sec)))
        self.project_drive_dir = Path(project_drive_dir).resolve() if project_drive_dir else None
        self.legacy_ep_file = Path(legacy_ep_file).resolve() if legacy_ep_file else None
        self._scratch_dirs = [Path(p).resolve() for p in (scratch_dirs or [])]
        self._hardware_collector = hardware_collector or collect_host_metrics
        self._process_collector = process_collector
        self._unassign_fn = unassign_fn or self._default_unassign
        self._tunnel_rebuilder = tunnel_rebuilder
        self._gateway_controller = gateway_controller or {}
        self._queue_controller = queue_controller or {}
        self._shutdown_grace_sec = max(0.0, float(shutdown_grace_sec))

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._workers: list[threading.Thread] = []
        self._http_server: ThreadingHTTPServer | None = None

        self._last_active_ts = now
        self._state_ref: dict[str, Any] | None = None
        self._jobs_ref: dict[str, Any] | None = None

        self.lifecycle: dict[str, Any] = {
            "state": "running",
            "idle_seconds": 0,
            "idle_timeout_minutes": max(0, int(idle_timeout_minutes)),
            "keepalive_lease_until": None,
            "deadline_shutdown_at": None,
            "deadline_mode": "drain",
            "auto_shutdown_after_batch": bool(auto_shutdown_after_batch),
            "drain_requested": False,
            "drain_include_queued": False,
            "drain_timeout_minutes": 120,
        }
        self.tunnel: dict[str, Any] = {
            "enabled": run_mode == "online_gateway",
            "healthy": False,
            "base_url": "",
            "agent_prefix": "/agent/v1",
            "updated_at": None,
        }
        self.workload: dict[str, Any] = {
            "kind": "job_queue",
            "paused": False,
            "running": 0,
            "queued": 0,
            "succeeded": 0,
            "failed": 0,
            "progress": {"stage": "", "current_item": "", "done": 0, "total": 0, "eta_sec": None},
        }
        self._services: dict[str, dict[str, Any]] = {}
        self._log_paths: dict[str, Path] = {}
        self._agent_logs: collections.deque[str] = collections.deque(maxlen=500)
        self._timeseries: collections.deque[dict[str, Any]] = collections.deque(maxlen=240)
        self._executed_acks: dict[str, dict[str, Any]] = {}
        self._managed_pids: set[int] = set()
        self._last_drive_latency_ms: int = 0
        self._last_drive_verified_at: str | None = None

        self._log(f"Agent initialized: instance_id={self.instance_id}, project={self.project}, mode={self.run_mode}")
        self._bootstrap_drive_dirs()

    @property
    def api_key(self) -> str:
        return self._api_key

    def _log(self, msg: str) -> None:
        line = f"[{_utc_iso()}] {msg}"
        with self._lock:
            self._agent_logs.append(line)

    def _bootstrap_drive_dirs(self) -> None:
        for rel in (
            "instances",
            "secrets",
            f"commands/{self.instance_id}",
            f"acks/{self.instance_id}",
            "snapshots",
            "audit",
        ):
            try:
                self.drive.ensure_dir(rel)
            except Exception as exc:
                self._log(f"Failed ensuring dir {rel}: {exc}")
        self.publish_secret()
        self._prune_stale_commands_on_startup()

    def publish_secret(self) -> None:
        """Persist api_key to _colab_monitor/secrets/<instance_id>.json (never in instances/)."""
        self.drive.put_json(
            f"secrets/{self.instance_id}.json",
            {
                "schema_version": SCHEMA_VERSION,
                "instance_id": self.instance_id,
                "api_key": self._api_key,
                "created_at": self.started_at,
            },
        )

    def _prune_stale_commands_on_startup(self) -> None:
        try:
            items = self.drive.list_json(f"commands/{self.instance_id}")
        except Exception:
            return
        for rel_path, env in items:
            issued_ts = _parse_iso(env.get("issued_at"))
            if issued_ts is None or issued_ts < self.started_at_ts - CLOCK_SKEW_TOLERANCE_SEC:
                self.drive.delete_file(rel_path)

    def register_service(
        self,
        name: str,
        *,
        check_fn: Callable[[], bool] | None = None,
        restart_fn: Callable[[], bool] | None = None,
        start_fn: Callable[[], bool] | None = None,
        stop_fn: Callable[[], bool] | None = None,
        interrupt_fn: Callable[[], bool] | None = None,
        free_vram_fn: Callable[[], dict[str, Any] | bool] | None = None,
        pid_fn: Callable[[], int | None] | None = None,
        port: int | None = None,
        log_path: Path | str | None = None,
    ) -> None:
        with self._lock:
            self._services[name] = {
                "check_fn": check_fn,
                "restart_fn": restart_fn,
                "start_fn": start_fn,
                "stop_fn": stop_fn,
                "interrupt_fn": interrupt_fn,
                "free_vram_fn": free_vram_fn,
                "pid_fn": pid_fn,
                "port": port,
                "started_at": time.time(),
            }
            if log_path and name in ALLOWED_LOG_SOURCES:
                self._log_paths[name] = Path(log_path)

    def register_log_path(self, source: str, path: Path | str) -> None:
        if source not in ALLOWED_LOG_SOURCES:
            raise ValueError(f"Unsupported log source: {source}")
        with self._lock:
            self._log_paths[source] = Path(path)

    def register_managed_pid(self, pid: int) -> None:
        with self._lock:
            if pid > 1 and pid != os.getpid():
                self._managed_pids.add(int(pid))

    def set_tunnel(self, base_url: str, *, healthy: bool = True, enabled: bool = True) -> None:
        with self._lock:
            self.tunnel["enabled"] = bool(enabled)
            self.tunnel["healthy"] = bool(healthy)
            self.tunnel["base_url"] = base_url.rstrip("/")
            self.tunnel["updated_at"] = _utc_iso()
            if enabled and base_url:
                self.run_mode = "online_gateway"

    def _compute_capabilities(self) -> list[str]:
        caps = [
            "lifecycle.keepalive",
            "lifecycle.set_keepalive_lease",
            "lifecycle.set_idle_policy",
            "lifecycle.set_deadline",
            "lifecycle.drain_shutdown",
            "lifecycle.cancel_drain",
            "lifecycle.shutdown_now",
            "ops.kill_process",
            "ops.cleanup_disk",
            "workload.pause_queue",
            "workload.resume_queue",
            "storage.flush_and_verify",
            "logs.snapshot_to_drive",
            "read.processes",
            "read.logs",
            "read.timeseries",
        ]
        if self.tunnel.get("enabled"):
            caps.append("network.stop_gateway")
            if self._tunnel_rebuilder or self._gateway_controller.get("rebuild_tunnel"):
                caps.append("network.rebuild_tunnel")
        else:
            if self._gateway_controller.get("start_gateway"):
                caps.append("network.start_gateway")
        if self._services:
            caps.append("ops.service_control")
        if self.project_drive_dir or self._queue_controller.get("reload_drive_queue"):
            caps.append("workload.reload_drive_queue")
        if self._queue_controller.get("queue_mutate"):
            caps.append("workload.queue_mutate")
        return caps

    def _snapshot_services(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        now = time.time()
        for name, cfg in self._services.items():
            alive = True
            if callable(cfg.get("check_fn")):
                try:
                    alive = bool(cfg["check_fn"]())
                except Exception:
                    alive = False
            pid = None
            if callable(cfg.get("pid_fn")):
                try:
                    pid = cfg["pid_fn"]()
                    if isinstance(pid, int) and pid > 1 and pid != os.getpid():
                        self._managed_pids.add(pid)
                except Exception:
                    pid = None
            out[name] = {
                "status": "running" if alive else "stopped",
                "pid": pid,
                "port": cfg.get("port"),
                "uptime_sec": int(max(0.0, now - cfg.get("started_at", now))) if alive else 0,
            }
        return out

    def build_snapshot(self, *, record_timeseries: bool = False) -> dict[str, Any]:
        now = time.time()
        hw = self._hardware_collector()
        with self._lock:
            if self._state_ref and isinstance(self._state_ref.get("last_active"), (int, float)):
                self._last_active_ts = max(self._last_active_ts, float(self._state_ref["last_active"]))
            idle_sec = int(max(0.0, now - self._last_active_ts))
            self.lifecycle["idle_seconds"] = idle_sec
            lease_until_ts = _parse_iso(self.lifecycle.get("keepalive_lease_until"))
            if lease_until_ts is not None and now >= lease_until_ts:
                self.lifecycle["keepalive_lease_until"] = None

            if self._jobs_ref is not None:
                vals = list(self._jobs_ref.values())
                self.workload["running"] = sum(1 for j in vals if j.get("status") == "running")
                self.workload["queued"] = sum(1 for j in vals if j.get("status") == "queued")
                self.workload["succeeded"] = sum(1 for j in vals if j.get("status") == "succeeded")
                self.workload["failed"] = sum(1 for j in vals if j.get("status") == "failed")

            hw_copy = dict(hw)
            hw_copy["drive"] = {
                "mode": self.drive.mode,
                "healthy": True,
                "latency_ms": self._last_drive_latency_ms,
                "pending_uploads": 0,
                "last_verified_at": self._last_drive_verified_at or _utc_iso(now),
            }

            snap = {
                "schema_version": SCHEMA_VERSION,
                "instance_id": self.instance_id,
                "project": self.project,
                "label": self.label,
                "notebook_name": self.notebook_name,
                "run_mode": self.run_mode,
                "started_at": self.started_at,
                "heartbeat_at": _utc_iso(now),
                "heartbeat_interval_sec": self.heartbeat_interval_sec,
                "capabilities": self._compute_capabilities(),
                "managed_services": sorted(self._services.keys()),
                "tunnel": dict(self.tunnel),
                "legacy_ep_hint": str(self.legacy_ep_file) if self.legacy_ep_file else None,
                "lifecycle": dict(self.lifecycle),
                "hardware": hw_copy,
                "services": self._snapshot_services(),
                "workload": {
                    **{k: v for k, v in self.workload.items() if k != "progress"},
                    "progress": dict(self.workload.get("progress") or {}),
                },
            }

            if record_timeseries:
                gpu = hw_copy.get("gpu") or {}
                ram = hw_copy.get("ram") or {}
                disk = hw_copy.get("disk") or {}
                self._timeseries.append(
                    {
                        "sampled_at": _utc_iso(now),
                        "sampled_at_ts": round(now, 1),
                        "gpu_util": gpu.get("util_gpu_pct"),
                        "vram_used_mib": gpu.get("vram_used_mib"),
                        "ram_used_gib": ram.get("used_gib"),
                        "disk_free_gib": disk.get("free_gib"),
                        "temp_c": gpu.get("temp_c"),
                        "power_w": gpu.get("power_draw_w"),
                        "active_jobs": self.workload.get("running", 0),
                    }
                )
            return snap

    def publish_heartbeat(self) -> dict[str, Any]:
        t0 = time.monotonic()
        snap = self.build_snapshot(record_timeseries=True)
        self.drive.put_json(f"instances/{self.instance_id}.json", snap)
        self._last_drive_latency_ms = int((time.monotonic() - t0) * 1000)
        self._last_drive_verified_at = _utc_iso()
        return snap

    def get_timeseries(self, window: str = "1h") -> list[dict[str, Any]]:
        cutoff = time.time() - (900.0 if window == "15m" else 3600.0)
        with self._lock:
            return [s for s in self._timeseries if float(s.get("sampled_at_ts", 0)) >= cutoff]

    def _is_protected_process(self, pid: int, name: str, cmdline: str) -> str | None:
        if pid <= 1:
            return "PID 1 系统根进程"
        if pid == os.getpid():
            return "colab_agent 宿主进程"
        low_name = (name or "").lower()
        low_cmd = (cmdline or "").lower()
        for pname in PROTECTED_PROCESS_NAMES:
            if pname in low_name or pname in low_cmd:
                return f"受保护系统进程 ({pname})"
        return None

    def list_processes(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(50, int(limit)))
        if self._process_collector:
            raw = self._process_collector(limit)
        else:
            raw = self._default_collect_processes(limit)
        out: list[dict[str, Any]] = []
        with self._lock:
            managed = set(self._managed_pids)
        for p in raw[:limit]:
            pid = int(p.get("pid", 0))
            ppid = int(p.get("ppid", 0))
            name = str(p.get("name") or "")
            cmd = _redact_text(str(p.get("cmdline") or name), [self._api_key])
            prot = self._is_protected_process(pid, name, cmd)
            in_allowlist = (pid in managed) or (ppid == os.getpid() and pid != os.getpid())
            killable = (prot is None) and in_allowlist
            reason = prot or (None if killable else "非受管业务进程（仅允许终止受管子进程）")
            out.append(
                {
                    "pid": pid,
                    "ppid": ppid,
                    "create_time": float(p.get("create_time", 0.0)),
                    "name": name,
                    "cmdline": cmd[:200],
                    "rss_mib": round(float(p.get("rss_mib", 0.0)), 1),
                    "cpu_pct": round(float(p.get("cpu_pct", 0.0)), 1),
                    "killable": killable,
                    "protected_reason": reason,
                }
            )
        return out

    def _default_collect_processes(self, limit: int) -> list[dict[str, Any]]:
        proc_dir = Path("/proc")
        items: list[dict[str, Any]] = []
        if proc_dir.is_dir():
            for entry in proc_dir.iterdir():
                if not entry.name.isdigit():
                    continue
                pid = int(entry.name)
                try:
                    stat_txt = (entry / "stat").read_text(encoding="utf-8", errors="ignore")
                    lparen = stat_txt.find("(")
                    rparen = stat_txt.rfind(")")
                    if lparen == -1 or rparen == -1:
                        continue
                    name = stat_txt[lparen + 1 : rparen]
                    rest = stat_txt[rparen + 2 :].split()
                    ppid = int(rest[1]) if len(rest) > 1 else 0
                    start_ticks = float(rest[19]) if len(rest) > 19 else 0.0
                    rss_pages = int(rest[21]) if len(rest) > 21 else 0
                    rss_mib = (rss_pages * os.sysconf("SC_PAGE_SIZE")) / (1024 * 1024)
                    cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
                    items.append(
                        {
                            "pid": pid,
                            "ppid": ppid,
                            "create_time": start_ticks,
                            "name": name,
                            "cmdline": cmdline or name,
                            "rss_mib": rss_mib,
                            "cpu_pct": 0.0,
                        }
                    )
                except Exception:
                    continue
            items.sort(key=lambda x: -x["rss_mib"])
            return items[:limit]
        # Fallback for macOS / non-Linux testing
        return [
            {
                "pid": os.getpid(),
                "ppid": os.getppid(),
                "create_time": self.started_at_ts,
                "name": "python3",
                "cmdline": "colab_agent host",
                "rss_mib": 32.0,
                "cpu_pct": 0.5,
            }
        ]

    def read_logs(self, source: str, tail: int = 200) -> dict[str, Any]:
        if source not in ALLOWED_LOG_SOURCES:
            raise ValueError(f"Log source not in allowlist: {source}")
        max_lines = max(1, min(MAX_LOG_LINES, int(tail)))
        raw_lines: list[str] = []
        if source == "agent":
            with self._lock:
                raw_lines = list(self._agent_logs)[-max_lines:]
        else:
            with self._lock:
                path = self._log_paths.get(source)
            if path and path.is_file():
                try:
                    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
                except OSError:
                    raw_lines = []

        redacted_lines = [_redact_text(ln, [self._api_key]) for ln in raw_lines]
        truncated = False
        kept: list[str] = []
        used_bytes = 0
        for ln in reversed(redacted_lines):
            b = len(ln.encode("utf-8")) + 1
            if used_bytes + b > MAX_LOG_BYTES:
                truncated = True
                break
            kept.append(ln)
            used_bytes += b
        kept.reverse()
        return {"source": source, "lines": kept, "truncated": truncated, "redacted": True}

    # -----------------------------------------------------------------------
    # Unified Dual-Channel Action Execution (with HMAC, TTL, shared cmd_id)
    # -----------------------------------------------------------------------

    def _make_ack(
        self,
        cmd_id: str,
        action: str,
        status: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
        accepted_at: str | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any]:
        now_iso = _utc_iso()
        return {
            "schema_version": SCHEMA_VERSION,
            "cmd_id": cmd_id,
            "instance_id": self.instance_id,
            "action": action,
            "status": status,
            "accepted_at": accepted_at or now_iso,
            "finished_at": finished_at if finished_at is not None else (now_iso if status not in ("accepted", "running", "queued") else None),
            "message": _redact_text(message, [self._api_key]),
            "data": data or {},
        }

    def execute_envelope(self, envelope: dict[str, Any], *, channel: str = "tunnel") -> tuple[int, dict[str, Any]]:
        """
        Validate and execute an ActionEnvelope identically for Channel A and Channel B.
        Returns (http_status_code, ActionAck).
        """
        cmd_id = str(envelope.get("cmd_id") or "")
        action = str(envelope.get("action") or "")
        target_inst = str(envelope.get("instance_id") or "")
        params = envelope.get("params") if isinstance(envelope.get("params"), dict) else {}

        if not cmd_id or not action:
            ack = self._make_ack(cmd_id or "invalid", action or "unknown", "rejected_invalid", "缺少 cmd_id 或 action。")
            return HTTPStatus.UNPROCESSABLE_ENTITY, ack

        # 1. HMAC verification (both Channel A & Channel B)
        if not _verify_sig(envelope, self._api_key):
            ack = self._make_ack(cmd_id, action, "rejected_unauthorized", "HMAC 签名校验失败或缺失。")
            return HTTPStatus.UNAUTHORIZED, ack

        # 2. instance_id match
        if target_inst != self.instance_id:
            ack = self._make_ack(cmd_id, action, "rejected_invalid", f"instance_id 不匹配 ({target_inst} != {self.instance_id})。")
            return HTTPStatus.CONFLICT, ack

        # 3. Expiration & pre-startup anti-replay check (+/- 60s tolerance)
        now = time.time()
        exp_ts = _parse_iso(envelope.get("expires_at"))
        issued_ts = _parse_iso(envelope.get("issued_at"))
        if exp_ts is None or now > exp_ts + CLOCK_SKEW_TOLERANCE_SEC:
            ack = self._make_ack(cmd_id, action, "rejected_expired", "指令已超过 expires_at 有效期，拒绝执行。")
            return HTTPStatus.GONE, ack
        if issued_ts is not None and issued_ts < self.started_at_ts - CLOCK_SKEW_TOLERANCE_SEC:
            ack = self._make_ack(cmd_id, action, "rejected_expired", "指令签发时间早于当前实例启动时间，拒绝执行。")
            return HTTPStatus.GONE, ack

        # 4. Shared idempotency check (at-most-once across Channel A and Channel B)
        with self._lock:
            existing = self._executed_acks.get(cmd_id)
            if existing is not None:
                return HTTPStatus.OK, dict(existing)

        accepted_at = _utc_iso(now)
        http_code, ack = self._dispatch_action(cmd_id, action, params, accepted_at=accepted_at, channel=channel)

        with self._lock:
            self._executed_acks[cmd_id] = dict(ack)
        self._log(f"Action [{channel}] {action} ({cmd_id}) -> {ack['status']}: {ack['message']}")
        return http_code, ack

    def _dispatch_action(
        self,
        cmd_id: str,
        action: str,
        params: dict[str, Any],
        *,
        accepted_at: str,
        channel: str,
    ) -> tuple[int, dict[str, Any]]:
        now = time.time()

        # --- 4.1 lifecycle.* ---
        if action == "lifecycle.keepalive":
            with self._lock:
                self._last_active_ts = now
                if self._state_ref is not None:
                    self._state_ref["last_active"] = now
                self.lifecycle["idle_seconds"] = 0
            return HTTPStatus.OK, self._make_ack(
                cmd_id, action, "succeeded", "已重置空闲计时器。", data={"idle_seconds": 0}, accepted_at=accepted_at
            )

        if action == "lifecycle.set_keepalive_lease":
            dur = params.get("duration_minutes")
            if not isinstance(dur, int) or isinstance(dur, bool) or (dur != 0 and not (15 <= dur <= 240)):
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", "duration_minutes 必须为 0 或 15..240 的整数。", accepted_at=accepted_at
                )
            with self._lock:
                until = None if dur == 0 else _utc_iso(now + dur * 60)
                self.lifecycle["keepalive_lease_until"] = until
                self._last_active_ts = now
            msg = "已取消保活租约。" if dur == 0 else f"已开启 {dur} 分钟保活租约（到期 {until}）。"
            return HTTPStatus.OK, self._make_ack(
                cmd_id, action, "succeeded", msg, data={"keepalive_lease_until": until}, accepted_at=accepted_at
            )

        if action == "lifecycle.set_idle_policy":
            tm = params.get("idle_timeout_minutes")
            auto_sd = params.get("auto_shutdown_after_batch")
            if tm is not None and (not isinstance(tm, int) or isinstance(tm, bool) or not (0 <= tm <= 360)):
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", "idle_timeout_minutes 必须为 0..360 的整数。", accepted_at=accepted_at
                )
            if auto_sd is not None and not isinstance(auto_sd, bool):
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", "auto_shutdown_after_batch 必须为布尔值。", accepted_at=accepted_at
                )
            with self._lock:
                if tm is not None:
                    self.lifecycle["idle_timeout_minutes"] = tm
                    if self._state_ref is not None:
                        self._state_ref["idle_timeout_minutes"] = tm
                if auto_sd is not None:
                    self.lifecycle["auto_shutdown_after_batch"] = auto_sd
                cur_tm = self.lifecycle["idle_timeout_minutes"]
                cur_auto = self.lifecycle["auto_shutdown_after_batch"]
            desc = "已禁用自动空闲释放" if cur_tm == 0 else f"空闲释放阈值已更新为 {cur_tm} 分钟"
            return HTTPStatus.OK, self._make_ack(
                cmd_id,
                action,
                "succeeded",
                f"{desc}（批跑后自动关机={'开' if cur_auto else '关'}）。",
                data={"idle_timeout_minutes": cur_tm, "auto_shutdown_after_batch": cur_auto},
                accepted_at=accepted_at,
            )

        if action == "lifecycle.set_deadline":
            after = params.get("shutdown_after_minutes")
            mode = params.get("mode") or "drain"
            if mode not in ("drain", "immediate"):
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", "mode 必须为 drain 或 immediate。", accepted_at=accepted_at
                )
            if after is not None and (not isinstance(after, int) or isinstance(after, bool) or not (5 <= after <= 720)):
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", "shutdown_after_minutes 必须为 null 或 5..720 的整数。", accepted_at=accepted_at
                )
            with self._lock:
                deadline = None if after is None else _utc_iso(now + after * 60)
                self.lifecycle["deadline_shutdown_at"] = deadline
                self.lifecycle["deadline_mode"] = mode
            msg = "已取消会话死线。" if deadline is None else f"已设置会话死线 {deadline}（模式={mode}）。"
            return HTTPStatus.OK, self._make_ack(
                cmd_id, action, "succeeded", msg, data={"deadline_shutdown_at": deadline, "mode": mode}, accepted_at=accepted_at
            )

        if action == "lifecycle.drain_shutdown":
            include_queued = bool(params.get("include_queued", False))
            timeout_min = params.get("timeout_minutes", 120)
            if not isinstance(timeout_min, int) or isinstance(timeout_min, bool) or not (5 <= timeout_min <= 360):
                timeout_min = 120
            with self._lock:
                self.lifecycle["drain_requested"] = True
                self.lifecycle["drain_include_queued"] = include_queued
                self.lifecycle["drain_timeout_minutes"] = timeout_min
                running = int(self.workload.get("running", 0))
                queued = int(self.workload.get("queued", 0))
            should_shutdown_now = (running == 0) and (not include_queued or queued == 0)
            if should_shutdown_now:
                self._schedule_safe_shutdown(reason="drain_shutdown_idle")
                msg = "当前无在途任务，已进入安全刷盘关机流程。"
            else:
                msg = f"已进入排空关机模式：待当前 {running} 项运行中任务完成并同步云盘后自动释放实例。"
            return HTTPStatus.OK, self._make_ack(
                cmd_id,
                action,
                "succeeded",
                msg,
                data={"drain_requested": True, "remaining_running_jobs": running, "include_queued": include_queued},
                accepted_at=accepted_at,
            )

        if action == "lifecycle.cancel_drain":
            with self._lock:
                if self.lifecycle.get("state") in ("shutting_down", "terminated"):
                    return HTTPStatus.CONFLICT, self._make_ack(
                        cmd_id, action, "failed", "实例已进入释放流程，无法撤回排空。", accepted_at=accepted_at
                    )
                self.lifecycle["drain_requested"] = False
            return HTTPStatus.OK, self._make_ack(
                cmd_id, action, "succeeded", "已撤回排空关机状态，恢复正常接单。", data={"drain_requested": False}, accepted_at=accepted_at
            )

        if action == "lifecycle.shutdown_now":
            self._schedule_safe_shutdown(reason="shutdown_now")
            return HTTPStatus.OK, self._make_ack(
                cmd_id,
                action,
                "succeeded",
                "已受理立即关机指令，正在执行云盘刷盘保护并在数秒后释放实例。",
                data={"state": "shutting_down"},
                accepted_at=accepted_at,
            )

        # --- 4.2 network.* ---
        if action == "network.rebuild_tunnel":
            fn = self._tunnel_rebuilder or self._gateway_controller.get("rebuild_tunnel")
            if not callable(fn):
                return HTTPStatus.CONFLICT, self._make_ack(
                    cmd_id, action, "failed", "当前实例未注册隧道重建处理器。", accepted_at=accepted_at
                )
            try:
                new_url = str(fn() or "").rstrip("/")
                if new_url:
                    self.set_tunnel(new_url, healthy=True, enabled=True)
                    self.publish_heartbeat()
                return HTTPStatus.OK, self._make_ack(
                    cmd_id, action, "succeeded", f"隧道重建完成：{new_url or '已刷新'}", data={"base_url": new_url}, accepted_at=accepted_at
                )
            except Exception as exc:
                return HTTPStatus.INTERNAL_SERVER_ERROR, self._make_ack(
                    cmd_id, action, "failed", f"重建隧道失败：{exc}", accepted_at=accepted_at
                )

        if action == "network.start_gateway":
            fn = self._gateway_controller.get("start_gateway")
            if not callable(fn):
                return HTTPStatus.CONFLICT, self._make_ack(
                    cmd_id, action, "failed", "当前实例未注册动态启动网关处理器。", accepted_at=accepted_at
                )
            # §4.2 Mandatory order: persist secret BEFORE exposing tunnel
            self.publish_secret()
            try:
                res = fn(port=int(params.get("port", 8000)), with_tunnel=bool(params.get("with_tunnel", True)))
                new_url = str(res.get("base_url") if isinstance(res, dict) else (res or "")).rstrip("/")
                self.set_tunnel(new_url, healthy=bool(new_url), enabled=True)
                self.publish_heartbeat()
                return HTTPStatus.OK, self._make_ack(
                    cmd_id,
                    action,
                    "succeeded",
                    f"已动态启动网关与隧道：{new_url}",
                    data={"base_url": new_url, "secret_ready": True},
                    accepted_at=accepted_at,
                )
            except Exception as exc:
                return HTTPStatus.INTERNAL_SERVER_ERROR, self._make_ack(
                    cmd_id, action, "failed", f"启动网关失败：{exc}", accepted_at=accepted_at
                )

        if action == "network.stop_gateway":
            fn = self._gateway_controller.get("stop_gateway")
            if callable(fn):
                try:
                    fn()
                except Exception as exc:
                    return HTTPStatus.INTERNAL_SERVER_ERROR, self._make_ack(
                        cmd_id, action, "failed", f"关闭隧道失败：{exc}", accepted_at=accepted_at
                    )
            with self._lock:
                self.tunnel["enabled"] = False
                self.tunnel["healthy"] = False
                self.tunnel["base_url"] = ""
                self.run_mode = "offline_batch"
            self.publish_heartbeat()
            return HTTPStatus.OK, self._make_ack(
                cmd_id, action, "succeeded", "已关闭公网隧道，保留云盘带外信箱管控。", data={"tunnel_enabled": False}, accepted_at=accepted_at
            )

        # --- 4.3 ops.* ---
        if action == "ops.service_control":
            svc_name = str(params.get("service") or "")
            op = str(params.get("op") or "")
            if op not in ("restart", "start", "stop", "interrupt", "free_vram"):
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", f"不支持的服务操作：{op}", accepted_at=accepted_at
                )
            with self._lock:
                svc = self._services.get(svc_name)
            if not svc:
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", f"未注册受管子服务：{svc_name}", accepted_at=accepted_at
                )
            return self._exec_service_control(cmd_id, action, svc_name, svc, op, accepted_at=accepted_at)

        if action == "ops.kill_process":
            pid = params.get("pid")
            create_time = params.get("create_time")
            sig_name = str(params.get("signal") or "TERM").upper()
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or sig_name not in ("TERM", "KILL"):
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", "pid 必须为正整数且 signal 须为 TERM 或 KILL。", accepted_at=accepted_at
                )
            procs = self.list_processes(limit=50)
            target = next((p for p in procs if p["pid"] == pid), None)
            if target is None:
                prot = self._is_protected_process(pid, "", "")
                if prot:
                    return HTTPStatus.FORBIDDEN, self._make_ack(
                        cmd_id, action, "rejected_invalid", f"拒绝终止受保护进程：{prot}", accepted_at=accepted_at
                    )
                return HTTPStatus.FORBIDDEN, self._make_ack(
                    cmd_id, action, "rejected_invalid", f"拒绝终止未在受管业务进程白名单中的 PID {pid}。", accepted_at=accepted_at
                )
            if not target["killable"]:
                return HTTPStatus.FORBIDDEN, self._make_ack(
                    cmd_id, action, "rejected_invalid", f"拒绝终止受保护进程：{target.get('protected_reason')}", accepted_at=accepted_at
                )
            if create_time is not None and abs(float(create_time) - float(target["create_time"])) > 5.0:
                return HTTPStatus.CONFLICT, self._make_ack(
                    cmd_id, action, "failed", f"PID {pid} 的 create_time 不匹配（进程已复用），已中止操作。", accepted_at=accepted_at
                )
            try:
                os.kill(pid, signal.SIGKILL if sig_name == "KILL" else signal.SIGTERM)
                return HTTPStatus.OK, self._make_ack(
                    cmd_id, action, "succeeded", f"已向受管进程 PID {pid} 发送 SIG{sig_name}。", data={"pid": pid, "signal": sig_name}, accepted_at=accepted_at
                )
            except OSError as exc:
                return HTTPStatus.INTERNAL_SERVER_ERROR, self._make_ack(
                    cmd_id, action, "failed", f"发送信号失败：{exc}", accepted_at=accepted_at
                )

        if action == "ops.cleanup_disk":
            return self._exec_cleanup_disk(cmd_id, action, params, accepted_at=accepted_at)

        # --- 4.4 workload.* ---
        if action == "workload.pause_queue":
            with self._lock:
                self.workload["paused"] = True
                self._resume_event.clear()
            fn = self._queue_controller.get("pause_queue")
            if callable(fn):
                fn()
            return HTTPStatus.OK, self._make_ack(
                cmd_id, action, "succeeded", "已挂起任务队列拉取，当前在途项完成后将暂停。", data={"paused": True}, accepted_at=accepted_at
            )

        if action == "workload.resume_queue":
            with self._lock:
                self.workload["paused"] = False
                self._resume_event.set()
            fn = self._queue_controller.get("resume_queue")
            if callable(fn):
                fn()
            return HTTPStatus.OK, self._make_ack(
                cmd_id, action, "succeeded", "已恢复任务队列运行。", data={"paused": False}, accepted_at=accepted_at
            )

        if action == "workload.reload_drive_queue":
            qfile = str(params.get("queue_file") or "offline_batch_queue.json")
            if not SAFE_QUEUE_FILENAME_RE.match(qfile):
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", f"非法 queue_file 文件名（禁止路径遍历）：{qfile}", accepted_at=accepted_at
                )
            if self.project_drive_dir is not None:
                resolved = (self.project_drive_dir / qfile).resolve()
                if not resolved.is_relative_to(self.project_drive_dir):
                    return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                        cmd_id, action, "rejected_invalid", "queue_file 越出项目目录边界。", accepted_at=accepted_at
                    )
            fn = self._queue_controller.get("reload_drive_queue")
            if not callable(fn):
                return HTTPStatus.CONFLICT, self._make_ack(
                    cmd_id, action, "failed", "当前实例未注册队列热重载处理器。", accepted_at=accepted_at
                )
            try:
                added = fn(qfile)
                return HTTPStatus.OK, self._make_ack(
                    cmd_id, action, "succeeded", f"已重新加载队列文件 {qfile}。", data={"queue_file": qfile, "result": added}, accepted_at=accepted_at
                )
            except Exception as exc:
                return HTTPStatus.INTERNAL_SERVER_ERROR, self._make_ack(
                    cmd_id, action, "failed", f"热重载队列失败：{exc}", accepted_at=accepted_at
                )

        if action == "workload.queue_mutate":
            op = str(params.get("op") or "")
            job_id = params.get("job_id")
            if op not in ("cancel_job", "prioritize_job", "retry_failed", "clear_pending"):
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", f"不支持的队列变更操作：{op}", accepted_at=accepted_at
                )
            fn = self._queue_controller.get("queue_mutate")
            if not callable(fn):
                return HTTPStatus.CONFLICT, self._make_ack(
                    cmd_id, action, "failed", "当前实例未注册队列变更处理器。", accepted_at=accepted_at
                )
            try:
                res = fn(op=op, job_id=job_id)
                return HTTPStatus.OK, self._make_ack(
                    cmd_id, action, "succeeded", f"队列变更 {op} 已完成。", data={"op": op, "result": res}, accepted_at=accepted_at
                )
            except Exception as exc:
                return HTTPStatus.INTERNAL_SERVER_ERROR, self._make_ack(
                    cmd_id, action, "failed", f"队列变更失败：{exc}", accepted_at=accepted_at
                )

        # --- 4.5 storage.* & logs.* ---
        if action == "storage.flush_and_verify":
            if hasattr(os, "sync"):
                try:
                    os.sync()
                except OSError:
                    pass
            probe = {"verified_at": _utc_iso(), "instance_id": self.instance_id, "mode": self.drive.mode}
            self.drive.put_json(f"acks/{self.instance_id}/_flush_probe.json", probe)
            readback = self.drive.get_json(f"acks/{self.instance_id}/_flush_probe.json")
            ok = isinstance(readback, dict) and readback.get("verified_at") == probe["verified_at"]
            self.drive.delete_file(f"acks/{self.instance_id}/_flush_probe.json")
            self._last_drive_verified_at = probe["verified_at"]
            return (HTTPStatus.OK if ok else HTTPStatus.INTERNAL_SERVER_ERROR), self._make_ack(
                cmd_id,
                action,
                "succeeded" if ok else "failed",
                "云盘同步探针写入与回读校验通过。" if ok else "云盘探针回读校验失败。",
                data={"verified": ok, "verified_at": probe["verified_at"], "drive_mode": self.drive.mode},
                accepted_at=accepted_at,
            )

        if action == "logs.snapshot_to_drive":
            sources = params.get("sources") or ["comfyui", "cloudflared", "gateway", "agent"]
            if not isinstance(sources, list) or not sources or any(s not in ALLOWED_LOG_SOURCES for s in sources):
                return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                    cmd_id, action, "rejected_invalid", "sources 仅允许白名单键：comfyui/cloudflared/gateway/agent。", accepted_at=accepted_at
                )
            snap_logs = {src: self.read_logs(src, tail=MAX_LOG_LINES) for src in sources}
            ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            rel = f"snapshots/{self.instance_id}_{ts_tag}.json"
            self.drive.put_json(
                rel,
                {
                    "schema_version": SCHEMA_VERSION,
                    "instance_id": self.instance_id,
                    "created_at": _utc_iso(),
                    "snapshot": self.build_snapshot(),
                    "logs": snap_logs,
                },
            )
            return HTTPStatus.OK, self._make_ack(
                cmd_id, action, "succeeded", f"已打包脱敏日志快照至 _colab_monitor/{rel}。", data={"snapshot_path": rel}, accepted_at=accepted_at
            )

        return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
            cmd_id, action, "rejected_invalid", f"未知动作枚举：{action}", accepted_at=accepted_at
        )

    def _exec_service_control(
        self, cmd_id: str, action: str, svc_name: str, svc: dict[str, Any], op: str, *, accepted_at: str
    ) -> tuple[int, dict[str, Any]]:
        fn = svc.get(f"{op}_fn")
        if callable(fn):
            try:
                res = fn()
                return HTTPStatus.OK, self._make_ack(
                    cmd_id, action, "succeeded", f"受管服务 {svc_name} 执行 {op} 成功。", data={"service": svc_name, "op": op, "result": res}, accepted_at=accepted_at
                )
            except Exception as exc:
                return HTTPStatus.INTERNAL_SERVER_ERROR, self._make_ack(
                    cmd_id, action, "failed", f"受管服务 {svc_name} 执行 {op} 失败：{exc}", accepted_at=accepted_at
                )
        # Built-in HTTP helpers for ComfyUI interrupt / free_vram (§4.3)
        port = svc.get("port")
        if svc_name == "comfyui" and port and op in ("interrupt", "free_vram"):
            endpoint = f"http://127.0.0.1:{port}/interrupt" if op == "interrupt" else f"http://127.0.0.1:{port}/free"
            body = b"{}" if op == "interrupt" else json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8")
            req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(req, timeout=10) as resp:
                    resp.read()
                return HTTPStatus.OK, self._make_ack(
                    cmd_id, action, "succeeded", f"ComfyUI {op} 请求已完成。", data={"service": svc_name, "op": op}, accepted_at=accepted_at
                )
            except Exception as exc:
                return HTTPStatus.BAD_GATEWAY, self._make_ack(
                    cmd_id, action, "failed", f"调用 ComfyUI {endpoint} 失败：{exc}", accepted_at=accepted_at
                )
        return HTTPStatus.CONFLICT, self._make_ack(
            cmd_id, action, "failed", f"服务 {svc_name} 未提供 {op} 回调。", accepted_at=accepted_at
        )

    def _exec_cleanup_disk(
        self, cmd_id: str, action: str, params: dict[str, Any], *, accepted_at: str
    ) -> tuple[int, dict[str, Any]]:
        allowed_targets: dict[str, list[Path]] = {
            "comfy_temp": [Path("/content/ComfyUI/temp")],
            "pip_cache": [Path("/root/.cache/pip")],
            "hf_incomplete": [Path.home() / ".cache" / "huggingface"],
            "agent_scratch": list(self._scratch_dirs) + list(Path(tempfile.gettempdir()).glob("colab_agent_*")),
        }
        targets = params.get("targets")
        dry_run = bool(params.get("dry_run", True))
        if not isinstance(targets, list) or not targets or any(t not in allowed_targets for t in targets):
            return HTTPStatus.UNPROCESSABLE_ENTITY, self._make_ack(
                cmd_id, action, "rejected_invalid", f"targets 仅允许枚举：{sorted(allowed_targets.keys())}", accepted_at=accepted_at
            )

        forbidden_roots = [Path("/content/drive").resolve()]
        if self.project_drive_dir:
            forbidden_roots.append(self.project_drive_dir)
            forbidden_roots.append((self.project_drive_dir / "outputs").resolve())

        def _is_forbidden(p: Path) -> bool:
            rp = p.resolve()
            if rp.name == "outputs":
                return True
            return any(rp == fr or rp.is_relative_to(fr) for fr in forbidden_roots)

        reclaimable_bytes = 0
        file_count = 0
        removed_count = 0
        for key in targets:
            for root_dir in allowed_targets[key]:
                if not root_dir.exists() or _is_forbidden(root_dir):
                    continue
                iterator = root_dir.rglob("*.incomplete") if key == "hf_incomplete" else root_dir.rglob("*")
                for fp in list(iterator):
                    try:
                        if fp.is_symlink() or _is_forbidden(fp) or not fp.is_file():
                            continue
                        sz = fp.stat().st_size
                        reclaimable_bytes += sz
                        file_count += 1
                        if not dry_run:
                            fp.unlink(missing_ok=True)
                            removed_count += 1
                    except OSError:
                        continue

        mb = round(reclaimable_bytes / (1024 * 1024), 2)
        msg = (
            f"[Dry-Run] 预计可释放 {mb} MiB（{file_count} 个文件）。"
            if dry_run
            else f"已清理 {removed_count} 个文件，释放约 {mb} MiB。"
        )
        return HTTPStatus.OK, self._make_ack(
            cmd_id,
            action,
            "succeeded",
            msg,
            data={"dry_run": dry_run, "reclaimable_bytes": reclaimable_bytes, "file_count": file_count, "removed_count": removed_count},
            accepted_at=accepted_at,
        )

    def _schedule_safe_shutdown(self, *, reason: str) -> None:
        with self._lock:
            if self.lifecycle.get("state") in ("shutting_down", "terminated"):
                return
            self.lifecycle["state"] = "shutting_down"
        self._log(f"Initiating §8.5 safe shutdown sequence (reason={reason})")
        try:
            self.publish_heartbeat()
        except Exception:
            pass

        def _worker() -> None:
            if self.drive.mode == "fuse" and hasattr(os, "sync"):
                try:
                    os.sync()
                except OSError:
                    pass
            if self._shutdown_grace_sec > 0:
                time.sleep(self._shutdown_grace_sec)
            with self._lock:
                self.lifecycle["state"] = "terminated"
            try:
                self.publish_heartbeat()
            except Exception:
                pass
            self._stop_event.set()
            try:
                self._unassign_fn()
            except Exception as exc:
                self._log(f"unassign_fn error: {exc}")

        threading.Thread(target=_worker, daemon=True, name="colab-agent-shutdown").start()

    @staticmethod
    def _default_unassign() -> None:
        try:
            from google.colab import runtime  # type: ignore

            runtime.unassign()
        except Exception:
            return

    # -----------------------------------------------------------------------
    # Channel B Polling & Background Workers
    # -----------------------------------------------------------------------

    def poll_drive_commands_once(self) -> list[dict[str, Any]]:
        """Consume pending Drive mailbox commands for this instance and write ACKs."""
        acks: list[dict[str, Any]] = []
        items = self.drive.list_json(f"commands/{self.instance_id}")
        for rel_path, env in items:
            cmd_id = str(env.get("cmd_id") or Path(rel_path).stem)
            _, ack = self.execute_envelope(env, channel="drive")
            try:
                self.drive.put_json(f"acks/{self.instance_id}/{cmd_id}.json", ack)
            except Exception as exc:
                self._log(f"Failed writing ACK for {cmd_id}: {exc}")
            self.drive.delete_file(rel_path)
            acks.append(ack)
        return acks

    def _check_deadline_and_idle(self) -> None:
        now = time.time()
        with self._lock:
            if self.lifecycle.get("state") != "running":
                return
            deadline_ts = _parse_iso(self.lifecycle.get("deadline_shutdown_at"))
            deadline_mode = self.lifecycle.get("deadline_mode", "drain")
        if deadline_ts is not None and now >= deadline_ts:
            if deadline_mode == "immediate":
                self._schedule_safe_shutdown(reason="deadline_immediate")
            else:
                with self._lock:
                    self.lifecycle["drain_requested"] = True
                    running = int(self.workload.get("running", 0))
                if running == 0:
                    self._schedule_safe_shutdown(reason="deadline_drain")

    def start_background_workers(self) -> None:
        if self._workers:
            return
        self._stop_event.clear()

        def _hb_loop() -> None:
            while not self._stop_event.is_set():
                try:
                    self.publish_heartbeat()
                    self._check_deadline_and_idle()
                except Exception as exc:
                    self._log(f"Heartbeat error: {exc}")
                self._stop_event.wait(self.heartbeat_interval_sec)

        def _cmd_loop() -> None:
            while not self._stop_event.is_set():
                try:
                    self.poll_drive_commands_once()
                except Exception as exc:
                    self._log(f"Command poll error: {exc}")
                self._stop_event.wait(self.command_poll_interval_sec)

        for name, fn in (("colab-agent-hb", _hb_loop), ("colab-agent-cmd", _cmd_loop)):
            t = threading.Thread(target=fn, daemon=True, name=name)
            t.start()
            self._workers.append(t)

    def stop(self) -> None:
        self._stop_event.set()
        self._resume_event.set()
        if self._http_server:
            try:
                self._http_server.shutdown()
                self._http_server.server_close()
            except Exception:
                pass
            self._http_server = None
        for t in self._workers:
            t.join(timeout=2.0)
        self._workers.clear()

    # -----------------------------------------------------------------------
    # Way 2: Universal Loop Integration (`track` & `checkpoint`)
    # -----------------------------------------------------------------------

    def checkpoint(self) -> bool:
        """
        Safe checkpoint for batch/audio loops.
        Blocks while paused (preserving KeyboardInterrupt safety), and returns False when drain should stop the loop.
        """
        while not self._resume_event.is_set():
            if self._stop_event.is_set():
                return False
            self._resume_event.wait(timeout=0.5)
        with self._lock:
            drain = bool(self.lifecycle.get("drain_requested"))
            include_q = bool(self.lifecycle.get("drain_include_queued"))
        if drain and not include_q:
            self._schedule_safe_shutdown(reason="drain_checkpoint")
            return False
        return not self._stop_event.is_set()

    def track(self, items: Iterable[Any], *, stage: str = "批跑处理") -> Iterator[Any]:
        seq = list(items)
        total = len(seq)
        started = time.monotonic()
        with self._lock:
            self.workload["queued"] = total
            self.workload["progress"] = {"stage": stage, "current_item": "", "done": 0, "total": total, "eta_sec": None}
        for idx, item in enumerate(seq):
            if not self.checkpoint():
                break
            with self._lock:
                self.workload["running"] = 1
                self.workload["queued"] = max(0, total - idx - 1)
                self.workload["progress"]["current_item"] = str(item)[:80]
            try:
                yield item
                with self._lock:
                    self.workload["running"] = 0
                    self.workload["succeeded"] = int(self.workload.get("succeeded", 0)) + 1
                    done = idx + 1
                    elapsed = time.monotonic() - started
                    eta = int((elapsed / done) * (total - done)) if done > 0 and total > done else 0
                    self.workload["progress"]["done"] = done
                    self.workload["progress"]["eta_sec"] = eta
            except KeyboardInterrupt:
                with self._lock:
                    self.workload["running"] = 0
                self._log("Loop interrupted by KeyboardInterrupt; leaving runtime online without drain shutdown.")
                raise
        with self._lock:
            self.workload["running"] = 0
            should_stop = bool(self.lifecycle.get("drain_requested")) or bool(self.lifecycle.get("auto_shutdown_after_batch"))
        if should_stop:
            self._schedule_safe_shutdown(reason="batch_complete")

    # -----------------------------------------------------------------------
    # Channel A Mounting: FastAPI (optional) + Built-in Stdlib HTTP Server
    # -----------------------------------------------------------------------

    def bind_fastapi(
        self,
        app: Any,
        *,
        api_key_getter: Callable[[], str] | None = None,
        state_ref: dict[str, Any] | None = None,
        jobs_ref: dict[str, Any] | None = None,
    ) -> None:
        from fastapi import Header, HTTPException, Request  # type: ignore
        from fastapi.responses import JSONResponse  # type: ignore

        self._state_ref = state_ref
        self._jobs_ref = jobs_ref
        if api_key_getter:
            k = (api_key_getter() or "").strip()
            if k:
                self._api_key = k
                self.publish_secret()

        def _check_bearer(authorization: str) -> None:
            expected = f"Bearer {self._api_key}"
            if not hmac.compare_digest(authorization or "", expected):
                raise HTTPException(status_code=401, detail="Unauthorized")

        @app.get("/agent/v1/health")
        def agent_health() -> dict[str, Any]:
            snap = self.build_snapshot()
            return {
                "ok": True,
                "schema_version": SCHEMA_VERSION,
                "instance_id": self.instance_id,
                "state": snap["lifecycle"]["state"],
                "idle_seconds": snap["lifecycle"]["idle_seconds"],
                "idle_timeout_minutes": snap["lifecycle"]["idle_timeout_minutes"],
                "active_jobs": snap["workload"]["running"],
            }

        @app.get("/agent/v1/metrics")
        def agent_metrics(authorization: str = Header(default="")) -> dict[str, Any]:
            _check_bearer(authorization)
            return self.build_snapshot()

        @app.get("/agent/v1/timeseries")
        def agent_ts(window: str = "1h", authorization: str = Header(default="")) -> dict[str, Any]:
            _check_bearer(authorization)
            return {"instance_id": self.instance_id, "samples": self.get_timeseries(window)}

        @app.get("/agent/v1/processes")
        def agent_procs(limit: int = 20, authorization: str = Header(default="")) -> dict[str, Any]:
            _check_bearer(authorization)
            return {"instance_id": self.instance_id, "processes": self.list_processes(limit)}

        @app.get("/agent/v1/logs")
        def agent_logs(source: str = "agent", tail: int = 200, authorization: str = Header(default="")) -> dict[str, Any]:
            _check_bearer(authorization)
            try:
                return self.read_logs(source, tail=tail)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        @app.post("/agent/v1/actions")
        async def agent_actions(request: Request, authorization: str = Header(default="")) -> JSONResponse:
            _check_bearer(authorization)
            env = await request.json()
            code, ack = self.execute_envelope(env if isinstance(env, dict) else {}, channel="tunnel")
            return JSONResponse(content=ack, status_code=int(code))

        @app.get("/agent/v1/actions/{cmd_id}")
        def agent_action_status(cmd_id: str, authorization: str = Header(default="")) -> dict[str, Any]:
            _check_bearer(authorization)
            with self._lock:
                ack = self._executed_acks.get(cmd_id)
            if not ack:
                raise HTTPException(status_code=404, detail="Command not found")
            return ack

    def start_http_server(self, host: str = "127.0.0.1", port: int = 0) -> str:
        """Start built-in stdlib /agent/v1/* HTTP server (ideal for standalone use & Fake Agent tests)."""
        agent = self

        class AgentHTTPHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(int(status))
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _require_bearer(self) -> bool:
                auth = self.headers.get("Authorization") or ""
                if not hmac.compare_digest(auth, f"Bearer {agent.api_key}"):
                    self._send_json({"ok": False, "error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                    return False
                return True

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)
                if path == "/agent/v1/health":
                    snap = agent.build_snapshot()
                    self._send_json(
                        {
                            "ok": True,
                            "schema_version": SCHEMA_VERSION,
                            "instance_id": agent.instance_id,
                            "state": snap["lifecycle"]["state"],
                            "idle_seconds": snap["lifecycle"]["idle_seconds"],
                            "idle_timeout_minutes": snap["lifecycle"]["idle_timeout_minutes"],
                            "active_jobs": snap["workload"]["running"],
                        }
                    )
                    return
                if not self._require_bearer():
                    return
                if path == "/agent/v1/metrics":
                    self._send_json(agent.build_snapshot())
                    return
                if path == "/agent/v1/timeseries":
                    win = (query.get("window") or ["1h"])[0]
                    self._send_json({"instance_id": agent.instance_id, "samples": agent.get_timeseries(win)})
                    return
                if path == "/agent/v1/processes":
                    try:
                        limit = int((query.get("limit") or ["20"])[0])
                    except ValueError:
                        limit = 20
                    self._send_json({"instance_id": agent.instance_id, "processes": agent.list_processes(limit)})
                    return
                if path == "/agent/v1/logs":
                    src = (query.get("source") or ["agent"])[0]
                    try:
                        tail = int((query.get("tail") or ["200"])[0])
                    except ValueError:
                        tail = 200
                    try:
                        self._send_json(agent.read_logs(src, tail=tail))
                    except ValueError as exc:
                        self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                if path.startswith("/agent/v1/actions/"):
                    cid = path.split("/agent/v1/actions/", 1)[1]
                    with agent._lock:
                        ack = agent._executed_acks.get(cid)
                    if not ack:
                        self._send_json({"ok": False, "error": "Command not found"}, HTTPStatus.NOT_FOUND)
                        return
                    self._send_json(ack)
                    return
                self._send_json({"ok": False, "error": "Not Found"}, HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/agent/v1/actions":
                    self._send_json({"ok": False, "error": "Not Found"}, HTTPStatus.NOT_FOUND)
                    return
                if not self._require_bearer():
                    return
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                    raw = self.rfile.read(length) if length > 0 else b"{}"
                    env = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._send_json({"ok": False, "error": "Invalid JSON body"}, HTTPStatus.BAD_REQUEST)
                    return
                code, ack = agent.execute_envelope(env if isinstance(env, dict) else {}, channel="tunnel")
                self._send_json(ack, status=int(code))

        srv = ThreadingHTTPServer((host, port), AgentHTTPHandler)
        self._http_server = srv
        t = threading.Thread(target=srv.serve_forever, daemon=True, name="colab-agent-http")
        t.start()
        base_url = f"http://{host}:{srv.server_port}"
        self.set_tunnel(base_url, healthy=True, enabled=True)
        return base_url
