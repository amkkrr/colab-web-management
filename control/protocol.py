"""Shared v2.0 protocol helpers: signing, redaction, liveness, atomic I/O, and state-dir guard."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = "2.0"
DEFAULT_DRIVE_MONITOR_REL = (os.environ.get("COLAB_MONITOR_DRIVE_SUBDIR") or "_colab_monitor").strip("/") or "_colab_monitor"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

ALL_WRITE_ACTIONS: frozenset[str] = frozenset(
    {
        # 4.1 lifecycle.* (7)
        "lifecycle.keepalive",
        "lifecycle.set_keepalive_lease",
        "lifecycle.set_idle_policy",
        "lifecycle.set_deadline",
        "lifecycle.drain_shutdown",
        "lifecycle.cancel_drain",
        "lifecycle.shutdown_now",
        # 4.2 network.* (3)
        "network.rebuild_tunnel",
        "network.start_gateway",
        "network.stop_gateway",
        # 4.3 ops.* (3)
        "ops.service_control",
        "ops.kill_process",
        "ops.cleanup_disk",
        # 4.4 workload.* (4)
        "workload.pause_queue",
        "workload.resume_queue",
        "workload.reload_drive_queue",
        "workload.queue_mutate",
        # 4.5 storage.* & logs.* (2)
        "storage.flush_and_verify",
        "logs.snapshot_to_drive",
    }
)

READ_CAPABILITIES: frozenset[str] = frozenset(
    {
        "read.processes",
        "read.logs",
        "read.timeseries",
    }
)

LEGACY_SUPPORTED_ACTIONS: frozenset[str] = frozenset(
    {
        "lifecycle.keepalive",
        "lifecycle.shutdown_now",
    }
)

HIGH_RISK_ACTIONS: frozenset[str] = frozenset(
    {
        "lifecycle.shutdown_now",
        "lifecycle.drain_shutdown",
        "lifecycle.set_deadline",
        "ops.kill_process",
        "ops.cleanup_disk",
        "fleet.shutdown_all",
        "fleet.drain_all",
    }
)

ALLOWED_LOG_SOURCES: frozenset[str] = frozenset({"comfyui", "cloudflared", "gateway", "agent"})
ALLOWED_FLEET_ACTIONS: frozenset[str] = frozenset({"fleet.drain_all", "fleet.shutdown_all"})
SAFE_QUEUE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.json$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{3,96}$")

CLOCK_SKEW_TOLERANCE_SEC = 60.0
DRIVE_ONLINE_MAX_AGE_SEC = 120.0
DRIVE_STALE_MAX_AGE_SEC = 300.0
SHUTTING_DOWN_WINDOW_SEC = 180.0
DEFAULT_CMD_TTL_SEC = 300
DEFAULT_ACK_TIMEOUT_SEC = 120.0

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
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

_CLOUD_DIR_MARKERS = ("cloudstorage", "googledrive", "mydrive", "我的云端硬盘")


def utc_now_iso(now_ts: float | None = None) -> str:
    ts = time.time() if now_ts is None else float(now_ts)
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso_ts(raw: str | None) -> float | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def make_instance_id(now_ts: float | None = None) -> str:
    ts = time.time() if now_ts is None else float(now_ts)
    stamp = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"inst_{stamp}_{secrets.token_hex(4)}"


def make_cmd_id(now_ts: float | None = None) -> str:
    ts = time.time() if now_ts is None else float(now_ts)
    stamp = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"cmd_{stamp}_{secrets.token_hex(4)}"


def canonical_envelope_bytes(envelope: dict[str, Any]) -> bytes:
    """Serialize an ActionEnvelope without `sig` into canonical UTF-8 JSON bytes."""
    clean = {k: v for k, v in envelope.items() if k != "sig"}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_envelope(envelope: dict[str, Any], api_key: str) -> str:
    """Compute hmac-sha256 signature for an ActionEnvelope."""
    digest = hmac.new(api_key.encode("utf-8"), canonical_envelope_bytes(envelope), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def verify_envelope_sig(envelope: dict[str, Any], api_key: str | None) -> bool:
    """Constant-time verify ActionEnvelope HMAC signature."""
    if not api_key or not isinstance(envelope, dict):
        return False
    sig = envelope.get("sig")
    if not isinstance(sig, str) or not sig.startswith("hmac-sha256:"):
        return False
    expected = sign_envelope(envelope, api_key)
    return hmac.compare_digest(sig, expected)


def build_action_envelope(
    instance_id: str,
    action: str,
    params: dict[str, Any] | None = None,
    *,
    api_key: str | None = None,
    issued_by: str = "local_web_ui",
    ttl_sec: int = DEFAULT_CMD_TTL_SEC,
    cmd_id: str | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now_ts is None else float(now_ts)
    ttl = max(30, min(3600, int(ttl_sec)))
    env: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "cmd_id": cmd_id or make_cmd_id(now),
        "instance_id": instance_id,
        "action": action,
        "params": params if isinstance(params, dict) else {},
        "issued_at": utc_now_iso(now),
        "ttl_sec": ttl,
        "expires_at": utc_now_iso(now + ttl),
        "issued_by": issued_by,
    }
    if api_key:
        env["sig"] = sign_envelope(env, api_key)
    return env


def redact_text(text: str, extra_secrets: list[str] | set[str] | tuple[str, ...] | None = None) -> str:
    """Redact known secrets verbatim plus common token patterns."""
    if not text:
        return text
    out = text
    if extra_secrets:
        for secret in sorted({s for s in extra_secrets if isinstance(s, str) and len(s) >= 6}, key=len, reverse=True):
            out = out.replace(secret, "[REDACTED]")
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def redact_payload(obj: Any, extra_secrets: list[str] | set[str] | tuple[str, ...] | None = None) -> Any:
    """Recursively redact secrets in JSON-serializable payloads."""
    if isinstance(obj, str):
        return redact_text(obj, extra_secrets)
    if isinstance(obj, list):
        return [redact_payload(x, extra_secrets) for x in obj]
    if isinstance(obj, dict):
        res: dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).lower() in ("api_key", "refresh_token", "access_token", "client_secret"):
                continue
            res[k] = redact_payload(v, extra_secrets)
        return res
    return obj


def is_cloud_synced_path(path: Path) -> bool:
    """Detect whether a local filesystem path resides inside a cloud drive sync root."""
    norm = str(path.resolve()).lower()
    return any(marker in norm for marker in _CLOUD_DIR_MARKERS)


def validate_upstream_url(url: str) -> str:
    """
    Validate an upstream tunnel/API URL before passing to urllib (mitigates file:// and SSRF schemes).
    Allows https:// to any non-empty host, or plain http:// strictly to loopback (127.0.0.1 / localhost / ::1).
    """
    clean = (url or "").strip()
    parsed = urlparse(clean)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"Invalid upstream URL (missing host): {url!r}")
    if scheme == "https":
        return clean
    if scheme == "http" and host in _LOOPBACK_HOSTS:
        return clean
    raise ValueError(f"Disallowed upstream URL scheme/host (expected https:// or loopback http://): {scheme}://{host}")


def assert_not_in_cloud_drive(path: Path) -> Path:
    """Refuse to place SQLite or local state files inside a cloud-synced directory."""
    resolved = path.resolve()
    if is_cloud_synced_path(resolved):
        raise ValueError(
            f"Refusing to place local state/SQLite inside cloud-synced directory: {resolved}. "
            "Use --state-dir or H3_MONITOR_STATE_DIR pointing to a local non-cloud directory."
        )
    return resolved


def resolve_local_state_dir(explicit_dir: Path | str | None = None) -> Path:
    """Resolve and validate the pure-local state directory (never inside Google Drive)."""
    if explicit_dir:
        candidate = Path(explicit_dir).expanduser()
    elif os.environ.get("H3_MONITOR_STATE_DIR"):
        candidate = Path(os.environ["H3_MONITOR_STATE_DIR"]).expanduser()
    elif sys.platform == "darwin":
        candidate = Path.home() / "Library" / "Application Support" / "h3-web-monitor"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        candidate = base / "h3-web-monitor"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
        candidate = base / "h3-web-monitor"

    resolved = assert_not_in_cloud_drive(candidate)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically using ._tmp_* staging (ignored by readers) with Drive-safe retries."""
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp_name = f"._tmp_{os.getpid()}_{threading.get_ident()}_{target.name}"
    tmp_path = target.parent / tmp_name
    written = False
    try:
        tmp_path.write_text(encoded, encoding="utf-8")
        for attempt in range(3):
            try:
                os.replace(tmp_path, target)
                written = True
                break
            except OSError:
                if attempt < 2:
                    time.sleep(0.02)
        if not written:
            target.write_text(encoded, encoding="utf-8")
            written = True
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def compute_liveness(
    *,
    heartbeat_at: str | None,
    tunnel_ok: bool,
    drain_requested: bool,
    lifecycle_state: str | None,
    shutdown_ack_age_sec: float | None = None,
    now_ts: float | None = None,
) -> tuple[str, float | None, bool]:
    """
    Compute §3.2 liveness state enum strictly from UTC heartbeat_at & tunnel status.
    Returns (liveness_state, age_sec, clock_skew_warning).
    """
    now = time.time() if now_ts is None else float(now_ts)
    lc_state = (lifecycle_state or "").strip().lower()
    hb_ts = parse_iso_ts(heartbeat_at)
    raw_age = (now - hb_ts) if hb_ts is not None else None

    if lc_state == "terminated":
        return "OFFLINE", (max(0.0, raw_age) if raw_age is not None else None), False

    if lc_state == "shutting_down":
        if raw_age is None or raw_age <= SHUTTING_DOWN_WINDOW_SEC:
            return "SHUTTING_DOWN", (max(0.0, raw_age) if raw_age is not None else 0.0), False
        return "OFFLINE", max(0.0, raw_age), False

    if shutdown_ack_age_sec is not None and 0.0 <= shutdown_ack_age_sec <= SHUTTING_DOWN_WINDOW_SEC:
        if not tunnel_ok:
            return "OFFLINE", (max(0.0, raw_age) if raw_age is not None else shutdown_ack_age_sec), False
        return "SHUTTING_DOWN", (max(0.0, raw_age) if raw_age is not None else shutdown_ack_age_sec), False

    if tunnel_ok:
        age = max(0.0, raw_age) if raw_age is not None else 0.0
        if drain_requested:
            return "DRAINING", age, False
        return "ONLINE_LIVE", age, False

    if raw_age is None:
        return "OFFLINE", None, False

    if raw_age < -CLOCK_SKEW_TOLERANCE_SEC:
        return "DEGRADED_STALE", raw_age, True

    age = max(0.0, raw_age)
    if age <= DRIVE_ONLINE_MAX_AGE_SEC:
        return ("DRAINING" if drain_requested else "ONLINE_DRIVE_ONLY"), age, False
    if age <= DRIVE_STALE_MAX_AGE_SEC:
        return "DEGRADED_STALE", age, False
    return "OFFLINE", age, False


def v2_to_v1_snapshot(
    inst: dict[str, Any],
    *,
    uptime_summary: dict[str, Any] | None = None,
    recent_outputs: list[dict[str, Any]] | None = None,
    total_output_files: int = 0,
) -> dict[str, Any]:
    """Map a v2 instance snapshot to the v1 /api/snapshot compatibility structure (§6.1)."""
    liveness = inst.get("liveness_state") or "OFFLINE"
    online = liveness in ("ONLINE_LIVE", "ONLINE_DRIVE_ONLY", "DRAINING")
    lc = inst.get("lifecycle") or {}
    hw = inst.get("hardware") or {}
    gpu = hw.get("gpu") or {}
    cpu = hw.get("cpu") or {}
    ram = hw.get("ram") or {}
    disk = hw.get("disk") or {}
    wl = inst.get("workload") or {}
    tunnel = inst.get("tunnel") or {}
    services = inst.get("services") or {}
    drive = hw.get("drive") or {}

    v1_health = (
        {
            "active_jobs": wl.get("running", 0),
            "idle_seconds": lc.get("idle_seconds", 0),
            "idle_timeout_minutes": lc.get("idle_timeout_minutes", 0),
            "comfy_alive": (services.get("comfyui") or {}).get("status") == "running",
            "dit_tier": inst.get("dit_tier") or (inst.get("endpoint_meta") or {}).get("dit_tier"),
            "gdrive_mode": drive.get("mode"),
        }
        if online
        else None
    )

    v1_gpu = dict(gpu)
    if "power_draw_w" in v1_gpu and "power_w" not in v1_gpu:
        v1_gpu["power_w"] = v1_gpu["power_draw_w"]

    v1_metrics = (
        {
            "gpu": v1_gpu,
            "cpu": dict(cpu) if cpu else None,
            "ram": ram,
            "disk": disk,
            "jobs": {
                "running": wl.get("running", 0),
                "queued": wl.get("queued", 0),
                "succeeded": wl.get("succeeded", 0),
                "failed": wl.get("failed", 0),
            },
        }
        if online and hw
        else None
    )

    snap: dict[str, Any] = {
        "online": online,
        "instance_id": inst.get("instance_id"),
        "liveness_state": liveness,
        "fetched_at": inst.get("fetched_at") or utc_now_iso(),
        "ep_path": inst.get("source_path") or inst.get("legacy_ep_hint") or "",
        "ep_exists": True,
        "base_url": tunnel.get("base_url") or "",
        "has_api_key": bool(inst.get("has_api_key")),
        "endpoint_updated_at": tunnel.get("updated_at") or inst.get("started_at"),
        "endpoint_meta": {
            "dit_tier": inst.get("dit_tier"),
            "default_size": None,
            "idle_timeout_minutes": lc.get("idle_timeout_minutes"),
        },
        "health": v1_health,
        "metrics": v1_metrics,
        "recent_outputs": recent_outputs if recent_outputs is not None else [],
        "total_output_files": total_output_files,
    }
    if uptime_summary is not None:
        snap["uptime_summary"] = uptime_summary
    if liveness == "SHUTTING_DOWN":
        snap["shutdown_pending"] = True
    if not online:
        snap["offline_reason"] = inst.get("offline_reason") or (
            "shutdown_confirmed" if lc.get("state") in ("terminated", "shutting_down") else "unreachable"
        )
        snap["error"] = inst.get("error") or f"实例 {inst.get('label') or inst.get('instance_id')} 当前处于 {liveness} 状态。"
    return snap
