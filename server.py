#!/usr/bin/env python3
"""
MiniMax-H3 Web Dashboard 轻量后端服务 (server.py)
- 零第三方依赖（仅用 Python 标准库 http.server）
- 代理只读采集 /health、/v1/metrics 与本地 outputs/h3_*.json，不向浏览器暴露 api_key
- 默认仅绑定 127.0.0.1，支持静态资源与安全路径校验
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from control.collector import ControlPlaneCollector, LocalControlStorage, MultiInstanceUptimeTracker
from control.injector import inject_notebook, remove_injection
from control.launcher import NotebookLauncher, auto_discover_notebook_roots
from control.protocol import (
    ALLOWED_LOG_SOURCES,
    SAFE_ID_RE,
    redact_payload,
    resolve_local_state_dir,
    v2_to_v1_snapshot,
)
from control.registry import InstanceRegistry, _http_get_json as _agent_http_get_json, auto_discover_monitor_root
from control.router import ActionRouter
from h3_cli_monitor import DEFAULT_EP_FILE, fetch_snapshot, send_keepalive, send_shutdown

HERE = Path(__file__).resolve().parent
WEB_ROOT = (HERE / "web").resolve()
SAFE_OUTPUT_PNG = re.compile(r"^h3_[A-Za-z0-9_.-]+\.png$")
CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)

UPTIME_LEDGER_FILENAME = "uptime_ledger.json"
MAX_HEARTBEAT_GAP_SEC = 300.0
MAX_INITIAL_SEED_SEC = 7200.0
MAX_UPTIME_SEED_SEC = 86400.0
DISK_FLUSH_INTERVAL_SEC = 30.0
MAX_STORED_SEGMENTS = 2000
COMPACT_AGE_SEC = 30 * 86400.0


def _classify_hardware(snap: dict, prior_hw: tuple[str, str] | None = None) -> tuple[str, str]:
    """Classify machine hardware as a GPU model, CPU Only, or unknown fallback."""
    metrics = snap.get("metrics")
    if isinstance(metrics, dict):
        gpu = metrics.get("gpu")
        if isinstance(gpu, dict):
            if gpu.get("available") is True:
                name = " ".join(str(gpu.get("name") or "").split()) or "未知 GPU"
                return name, "gpu"
            if gpu.get("available") is False:
                return "CPU Only", "cpu_only"

    health = snap.get("health")
    if isinstance(health, dict):
        gpu_name = " ".join(str(health.get("gpu_name") or "").split())
        if gpu_name:
            return gpu_name, "gpu"
        dev = str(health.get("device") or health.get("accelerator") or "").strip().lower()
        if dev in ("cpu", "cpu_only", "cpu-only", "none"):
            return "CPU Only", "cpu_only"

    ep_meta = snap.get("endpoint_meta")
    if isinstance(ep_meta, dict):
        dit_tier = str(ep_meta.get("dit_tier") or "").strip().lower()
        if dit_tier in ("cpu", "cpu_only", "cpu-only"):
            return "CPU Only", "cpu_only"

    if prior_hw is not None and prior_hw[1] != "unknown":
        return prior_hw

    return "待识别硬件", "unknown"


def _make_session_key(snap: dict) -> str:
    """Build a stable session key from tunnel base_url and endpoint updated_at without secrets."""
    base_url = str(snap.get("base_url") or "").strip().rstrip("/")
    updated_at = str(snap.get("endpoint_updated_at") or "").strip()
    return f"{base_url}|{updated_at}"


def _extract_initial_seed_seconds(snap: dict) -> float:
    """Estimate initial session duration on first sighting without counting unmonitored gaps."""
    for container in (snap.get("metrics"), snap.get("health")):
        if isinstance(container, dict):
            val = container.get("uptime_seconds")
            if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                return min(float(val), MAX_UPTIME_SEED_SEC)

    health = snap.get("health")
    if isinstance(health, dict):
        idle = health.get("idle_seconds")
        if isinstance(idle, (int, float)) and not isinstance(idle, bool) and idle > 0:
            return min(float(idle), MAX_INITIAL_SEED_SEC)

    return 0.0


class UptimeTracker:
    """Thread-safe persistent ledger tracking cumulative uptime by GPU model and CPU Only."""

    def __init__(self, ledger_path: Path) -> None:
        self.ledger_path = ledger_path
        self._lock = threading.Lock()
        self._loaded = False
        self._loaded_mtime_ns: int | None = None
        self._seen_session_keys: set[str] = set()
        self._archived_totals: dict[str, dict] = {}
        self._segments: list[dict] = []
        self._last_flush_ts: float = 0.0
        self._dirty_immediate: bool = False
        self._dirty_periodic: bool = False

    def _ensure_loaded_locked(self) -> None:
        if self.ledger_path.is_file():
            try:
                mtime_ns = self.ledger_path.stat().st_mtime_ns
            except OSError:
                mtime_ns = None
            if not self._loaded or (mtime_ns is not None and mtime_ns != self._loaded_mtime_ns):
                try:
                    raw = json.loads(self.ledger_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        self._load_payload_locked(raw)
                        self._loaded_mtime_ns = mtime_ns
                except Exception:
                    pass
        self._loaded = True

    def _load_payload_locked(self, raw: dict) -> None:
        seen_raw = raw.get("seen_session_keys")
        seen: set[str] = set()
        if isinstance(seen_raw, list):
            for item in seen_raw:
                if isinstance(item, str) and item:
                    seen.add(item)

        archived: dict[str, dict] = {}
        arch_raw = raw.get("archived_totals")
        if isinstance(arch_raw, dict):
            for hw_key, info in arch_raw.items():
                if not isinstance(hw_key, str) or not hw_key or not isinstance(info, dict):
                    continue
                cat = info.get("category")
                if cat not in ("gpu", "cpu_only", "unknown"):
                    cat = "cpu_only" if hw_key == "CPU Only" else "gpu"
                sec = info.get("seconds")
                cnt = info.get("session_count")
                ls = info.get("last_seen_ts")
                archived[hw_key] = {
                    "category": cat,
                    "seconds": max(0.0, float(sec)) if isinstance(sec, (int, float)) else 0.0,
                    "session_count": max(0, int(cnt)) if isinstance(cnt, (int, float)) else 0,
                    "last_seen_ts": float(ls) if isinstance(ls, (int, float)) else None,
                }

        segments: list[dict] = []
        segs_raw = raw.get("segments")
        if isinstance(segs_raw, list):
            for seg in segs_raw:
                if not isinstance(seg, dict):
                    continue
                skey = seg.get("session_key")
                hw_key = seg.get("hardware_key")
                cat = seg.get("category")
                st = seg.get("start_ts")
                ls = seg.get("last_seen_ts")
                if (
                    not isinstance(skey, str)
                    or not isinstance(hw_key, str)
                    or not hw_key
                    or not isinstance(st, (int, float))
                    or not isinstance(ls, (int, float))
                ):
                    continue
                if cat not in ("gpu", "cpu_only", "unknown"):
                    cat = "cpu_only" if hw_key == "CPU Only" else "gpu"
                start_f = max(0.0, float(st))
                last_f = max(start_f, float(ls))
                segments.append(
                    {
                        "session_key": skey,
                        "hardware_key": hw_key,
                        "category": cat,
                        "start_ts": start_f,
                        "last_seen_ts": last_f,
                        "active": bool(seg.get("active")),
                    }
                )
                seen.add(skey)

        self._seen_session_keys = seen
        self._archived_totals = archived
        self._segments = segments

    def _compact_locked(self, now: float) -> None:
        if len(self._segments) <= MAX_STORED_SEGMENTS:
            return
        cutoff = now - COMPACT_AGE_SEC
        kept: list[dict] = []
        folded: list[dict] = []
        for seg in self._segments:
            if seg.get("active") or seg["last_seen_ts"] >= cutoff:
                kept.append(seg)
            else:
                folded.append(seg)
        if not folded:
            return

        kept_hw_sessions = {
            (seg["hardware_key"], seg["session_key"]) for seg in kept
        }
        folded_hw_sessions: dict[str, set[str]] = {}
        for seg in folded:
            hw_key = seg["hardware_key"]
            cat = seg["category"]
            dur = max(0.0, seg["last_seen_ts"] - seg["start_ts"])
            entry = self._archived_totals.setdefault(
                hw_key,
                {"category": cat, "seconds": 0.0, "session_count": 0, "last_seen_ts": None},
            )
            entry["seconds"] += dur
            if entry["last_seen_ts"] is None or seg["last_seen_ts"] > entry["last_seen_ts"]:
                entry["last_seen_ts"] = seg["last_seen_ts"]
            if (hw_key, seg["session_key"]) not in kept_hw_sessions:
                folded_hw_sessions.setdefault(hw_key, set()).add(seg["session_key"])

        for hw_key, skeys in folded_hw_sessions.items():
            self._archived_totals[hw_key]["session_count"] += len(skeys)

        self._segments = kept
        self._dirty_immediate = True

    def _flush_locked(self, now: float, force_flush: bool = False) -> None:
        if not self.ledger_path.parent.exists():
            return
        if not self._segments and not self._archived_totals and not self.ledger_path.exists():
            self._dirty_immediate = False
            self._dirty_periodic = False
            return
        should_write = (
            force_flush
            or self._dirty_immediate
            or (self._dirty_periodic and (now - self._last_flush_ts >= DISK_FLUSH_INTERVAL_SEC))
        )
        if not should_write:
            return

        payload = {
            "version": 1,
            "updated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(timespec="seconds"),
            "seen_session_keys": sorted(self._seen_session_keys),
            "archived_totals": self._archived_totals,
            "segments": self._segments,
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp_path = self.ledger_path.with_suffix(".json.tmp")
        written = False
        try:
            tmp_path.write_text(encoded, encoding="utf-8")
            for attempt in range(3):
                try:
                    os.replace(tmp_path, self.ledger_path)
                    written = True
                    break
                except OSError:
                    if attempt < 2:
                        time.sleep(0.02)
            if not written:
                self.ledger_path.write_text(encoded, encoding="utf-8")
                written = True
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return

        if written:
            self._last_flush_ts = now
            self._dirty_immediate = False
            self._dirty_periodic = False
            try:
                self._loaded_mtime_ns = self.ledger_path.stat().st_mtime_ns
            except OSError:
                pass

    def flush(self) -> None:
        """Persist any pending in-memory uptime segments to disk."""
        with self._lock:
            if not self._loaded:
                return
            self._flush_locked(time.time(), force_flush=True)

    def _deactivate_all_locked(self) -> None:
        for seg in self._segments:
            if seg.get("active"):
                seg["active"] = False
                self._dirty_immediate = True

    def _build_summary_locked(self, snap: dict, now: float) -> dict:
        online = bool(snap.get("online")) and not bool(snap.get("is_demo"))
        active_seg = next((s for s in reversed(self._segments) if s.get("active")), None) if online else None
        current_session_key = active_seg["session_key"] if active_seg else None
        current_hw_key = active_seg["hardware_key"] if active_seg else None
        current_hw_cat = active_seg["category"] if active_seg else None

        local_now = datetime.fromtimestamp(now).astimezone()
        today_start_dt = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_ts = today_start_dt.timestamp()
        seven_days_start_ts = now - 7 * 86400.0

        catalog: dict[str, str] = {"CPU Only": "cpu_only"}
        global_last_seen: dict[str, float] = {}
        for hw_key, info in self._archived_totals.items():
            catalog.setdefault(hw_key, info.get("category", "gpu"))
            ls = info.get("last_seen_ts")
            if isinstance(ls, (int, float)):
                global_last_seen[hw_key] = max(global_last_seen.get(hw_key, 0.0), float(ls))
        for seg in self._segments:
            hw_key = seg["hardware_key"]
            if hw_key == "CPU Only":
                catalog[hw_key] = "cpu_only"
            else:
                catalog.setdefault(hw_key, seg["category"])
            global_last_seen[hw_key] = max(global_last_seen.get(hw_key, 0.0), seg["last_seen_ts"])

        def compute_window(win_id: str) -> dict:
            durations: dict[str, float] = {hk: 0.0 for hk in catalog}
            hw_sessions: dict[str, set[str]] = {hk: set() for hk in catalog}
            archived_sess_cnt: dict[str, int] = {hk: 0 for hk in catalog}
            all_win_sessions: set[str] = set()

            if win_id == "all":
                for hw_key, info in self._archived_totals.items():
                    durations[hw_key] += float(info.get("seconds") or 0.0)
                    archived_sess_cnt[hw_key] += int(info.get("session_count") or 0)

            for seg in self._segments:
                s_start = seg["start_ts"]
                s_end = max(s_start, seg["last_seen_ts"])
                hw_key = seg["hardware_key"]

                if win_id == "all":
                    dur = max(0.0, s_end - s_start)
                    include = True
                elif win_id == "today":
                    ov_start = max(s_start, today_start_ts)
                    ov_end = min(s_end, now)
                    dur = max(0.0, ov_end - ov_start)
                    include = dur > 0.0 or (bool(seg.get("active")) and s_end >= today_start_ts)
                elif win_id == "7d":
                    ov_start = max(s_start, seven_days_start_ts)
                    ov_end = min(s_end, now)
                    dur = max(0.0, ov_end - ov_start)
                    include = dur > 0.0 or (bool(seg.get("active")) and s_end >= seven_days_start_ts)
                else:  # current
                    if not online or not current_session_key or seg["session_key"] != current_session_key:
                        dur = 0.0
                        include = False
                    else:
                        dur = max(0.0, s_end - s_start)
                        include = True

                if include:
                    durations[hw_key] += dur
                    hw_sessions[hw_key].add(seg["session_key"])
                    all_win_sessions.add(seg["session_key"])

            total_seconds = sum(durations.values())
            gpu_seconds = sum(dur for hk, dur in durations.items() if catalog[hk] == "gpu")
            cpu_only_seconds = sum(dur for hk, dur in durations.items() if catalog[hk] == "cpu_only")
            unknown_seconds = sum(dur for hk, dur in durations.items() if catalog[hk] == "unknown")

            items = []
            for hw_key, hw_cat in catalog.items():
                sec = durations[hw_key]
                is_cur = bool(online and active_seg is not None and active_seg["hardware_key"] == hw_key)
                sess_cnt = len(hw_sessions[hw_key]) + archived_sess_cnt[hw_key]
                share_pct = round(sec * 100.0 / total_seconds, 1) if total_seconds > 0 else 0.0
                ls_ts = global_last_seen.get(hw_key)
                ls_iso = (
                    datetime.fromtimestamp(ls_ts, tz=timezone.utc).isoformat(timespec="seconds")
                    if ls_ts is not None
                    else None
                )
                items.append(
                    {
                        "hardware_key": hw_key,
                        "category": hw_cat,
                        "seconds": round(sec, 1),
                        "share_pct": share_pct,
                        "session_count": sess_cnt,
                        "is_current": is_cur,
                        "last_seen_at": ls_iso,
                    }
                )

            items.sort(
                key=lambda x: (
                    -x["seconds"],
                    0 if x["is_current"] else 1,
                    0 if x["category"] == "gpu" else (1 if x["category"] == "cpu_only" else 2),
                    x["hardware_key"],
                )
            )
            win_session_count = len(all_win_sessions) + (sum(archived_sess_cnt.values()) if win_id == "all" else 0)
            return {
                "total_seconds": round(total_seconds, 1),
                "gpu_seconds": round(gpu_seconds, 1),
                "cpu_only_seconds": round(cpu_only_seconds, 1),
                "unknown_seconds": round(unknown_seconds, 1),
                "session_count": win_session_count,
                "items": items,
            }

        win_all = compute_window("all")
        win_today = compute_window("today")
        win_7d = compute_window("7d")
        win_current = compute_window("current")

        return {
            "ledger_file": UPTIME_LEDGER_FILENAME,
            "updated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(timespec="seconds"),
            "today_start_at": today_start_dt.isoformat(timespec="seconds"),
            "current_online": online,
            "current_hardware": current_hw_key if online else None,
            "current_category": current_hw_cat if online else None,
            "current_session_seconds": win_current["total_seconds"] if online else 0.0,
            "total_sessions": win_all["session_count"],
            "windows": {
                "all": win_all,
                "today": win_today,
                "7d": win_7d,
                "current": win_current,
            },
        }

    def record_and_summarize(
        self,
        snap: dict,
        now_ts: float | None = None,
        force_flush: bool = False,
    ) -> dict:
        """Update uptime segments from a snapshot and return aggregated summary windows."""
        with self._lock:
            now = float(now_ts) if now_ts is not None else time.time()
            self._ensure_loaded_locked()

            if not snap.get("online") or snap.get("is_demo"):
                self._deactivate_all_locked()
            else:
                session_key = _make_session_key(snap)
                prior_hw: tuple[str, str] | None = None
                for seg in reversed(self._segments):
                    if seg["session_key"] == session_key and seg["category"] != "unknown":
                        prior_hw = (seg["hardware_key"], seg["category"])
                        break

                hw_key, hw_cat = _classify_hardware(snap, prior_hw)
                if hw_cat != "unknown":
                    for seg in self._segments:
                        if seg["session_key"] == session_key and seg["category"] == "unknown":
                            seg["hardware_key"] = hw_key
                            seg["category"] = hw_cat
                            self._dirty_immediate = True

                active_seg = None
                for seg in reversed(self._segments):
                    if seg.get("active"):
                        active_seg = seg
                        break

                if (
                    active_seg is not None
                    and active_seg["session_key"] == session_key
                    and 0.0 <= now - active_seg["last_seen_ts"] <= MAX_HEARTBEAT_GAP_SEC
                ):
                    for seg in self._segments:
                        if seg is not active_seg and seg.get("active"):
                            seg["active"] = False
                            self._dirty_immediate = True
                    if active_seg["hardware_key"] != hw_key and hw_cat != "unknown":
                        active_seg["last_seen_ts"] = max(active_seg["last_seen_ts"], now)
                        active_seg["active"] = False
                        self._segments.append(
                            {
                                "session_key": session_key,
                                "hardware_key": hw_key,
                                "category": hw_cat,
                                "start_ts": now,
                                "last_seen_ts": now,
                                "active": True,
                            }
                        )
                        self._dirty_immediate = True
                    else:
                        active_seg["last_seen_ts"] = max(active_seg["last_seen_ts"], now)
                        self._dirty_periodic = True
                else:
                    self._deactivate_all_locked()
                    already_seen = (session_key in self._seen_session_keys) or any(
                        s["session_key"] == session_key for s in self._segments
                    )
                    seed_sec = 0.0 if already_seen else _extract_initial_seed_seconds(snap)
                    start_ts = max(0.0, now - max(0.0, seed_sec))
                    self._segments.append(
                        {
                            "session_key": session_key,
                            "hardware_key": hw_key,
                            "category": hw_cat,
                            "start_ts": start_ts,
                            "last_seen_ts": now,
                            "active": True,
                        }
                    )
                    self._seen_session_keys.add(session_key)
                    self._dirty_immediate = True

            self._compact_locked(now)
            self._flush_locked(now, force_flush=force_flush)
            return self._build_summary_locked(snap, now)


def _parse_output_limit(query: dict[str, list[str]]) -> int:
    """Parse and clamp output limit query param (0 means all; positive capped at 200)."""
    raw_limit = (query.get("limit") or ["12"])[0]
    try:
        limit = int(raw_limit)
    except ValueError:
        return 12
    if limit < 0:
        return 12
    return min(limit, 200)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _extract_hostname(host_header: str | None) -> str:
    """Extract normalized hostname (without port or IPv6 brackets) from a Host header."""
    raw = (host_header or "").strip()
    if not raw or any(ch in raw for ch in (" ", "\t", "/", "\\", "@")):
        return ""
    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            return ""
        return raw[1:end].lower()
    if raw.count(":") == 1:
        return raw.split(":", 1)[0].lower()
    return raw.lower()


def _is_loopback_host(host: str | None) -> bool:
    """Return True when host is a local loopback address."""
    return _extract_hostname(host) in _LOOPBACK_HOSTS


def create_handler(
    ep_path: Path,
    allow_remote: bool = False,
    *,
    monitor_root: Path | None = None,
    state_dir: Path | None = None,
    scan_roots: list[Path] | None = None,
    notebook_roots: list[Path] | None = None,
    enable_storage: bool = False,
):
    """生成绑定指定 active_endpoint.json 与 v2 多实例管控平面的 HTTP 请求处理器。"""
    resolved_ep = ep_path.resolve()
    tracker = UptimeTracker(resolved_ep.parent / UPTIME_LEDGER_FILENAME)

    resolved_monitor_root = (
        monitor_root.resolve() if monitor_root is not None else auto_discover_monitor_root(resolved_ep)
    )
    local_storage: LocalControlStorage | None = None
    resolved_state_dir: Path | None = None
    v2_ledger_path = resolved_ep.parent / UPTIME_LEDGER_FILENAME
    if state_dir is not None or enable_storage:
        resolved_state_dir = resolve_local_state_dir(state_dir)
        local_storage = LocalControlStorage(resolved_state_dir)
        v2_ledger_path = resolved_state_dir / UPTIME_LEDGER_FILENAME

    discovered_nb_roots = auto_discover_notebook_roots(resolved_monitor_root, extra_roots=notebook_roots)
    launcher_holder: list[NotebookLauncher | None] = [
        NotebookLauncher(roots=discovered_nb_roots, state_dir=resolved_state_dir) if resolved_state_dir is not None else None
    ]

    def _get_launcher() -> NotebookLauncher:
        if launcher_holder[0] is None:
            sdir = resolve_local_state_dir(state_dir)
            launcher_holder[0] = NotebookLauncher(roots=discovered_nb_roots, state_dir=sdir)
        return launcher_holder[0]

    agent_src_path = (HERE / "agent" / "colab_agent.py").resolve()

    v2_uptime_tracker = MultiInstanceUptimeTracker(
        v2_ledger_path,
        legacy_import_path=resolved_ep.parent / UPTIME_LEDGER_FILENAME if v2_ledger_path != resolved_ep.parent / UPTIME_LEDGER_FILENAME else None,
    )
    registry = InstanceRegistry(
        monitor_root=resolved_monitor_root,
        primary_ep=resolved_ep,
        scan_roots=scan_roots,
    )
    router = ActionRouter(registry, monitor_root=resolved_monitor_root, storage=local_storage)
    collector = ControlPlaneCollector(
        registry=registry,
        router=router,
        uptime_tracker=v2_uptime_tracker,
        storage=local_storage,
        monitor_root=resolved_monitor_root,
    )

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "MiniMaxH3Monitor/2.0"
        uptime_tracker = tracker
        control_registry = registry
        control_router = router
        control_collector = collector
        control_storage = local_storage
        v2_uptime = v2_uptime_tracker

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            # 保持终端日志简洁，避免轮询刷屏
            return

        def _check_request_origin(self, is_write: bool = False) -> bool:
            """Validate Host and Origin headers to mitigate DNS rebinding and cross-origin access."""
            host_hdr = (self.headers.get("Host") or "").strip()
            host_name = _extract_hostname(host_hdr)
            if not host_name:
                self._send_json({"ok": False, "error": "Forbidden: missing or invalid Host header"}, status=HTTPStatus.FORBIDDEN)
                return False
            if not allow_remote and host_name not in _LOOPBACK_HOSTS:
                self._send_json(
                    {"ok": False, "error": "Forbidden: non-loopback Host header rejected"},
                    status=HTTPStatus.FORBIDDEN,
                )
                return False

            origin_hdr = (self.headers.get("Origin") or "").strip()
            if origin_hdr:
                parsed_origin = urlparse(origin_hdr)
                origin_host = (parsed_origin.hostname or "").lower()
                if parsed_origin.scheme not in ("http", "https") or not origin_host:
                    self._send_json(
                        {"ok": False, "error": "Forbidden: invalid Origin header"},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return False
                if not allow_remote and origin_host not in _LOOPBACK_HOSTS:
                    self._send_json(
                        {"ok": False, "error": "Forbidden: cross-origin request rejected"},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return False
                if allow_remote and is_write and parsed_origin.netloc.lower() != host_hdr.lower():
                    self._send_json(
                        {"ok": False, "error": "Forbidden: Origin does not match Host"},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return False
            return True

        def _write_response(
            self,
            status: int,
            content_type: str,
            cache_control: str,
            data: bytes,
        ) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", cache_control)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
                    "object-src 'none'; frame-ancestors 'none'; base-uri 'self'",
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except CLIENT_DISCONNECT_ERRORS:
                # 浏览器取消在途轮询或刷新关闭标签页属正常行为，静默忽略断连
                return

        def _send_error_safe(self, status: int, message: str) -> None:
            try:
                self.send_error(status, message)
            except CLIENT_DISCONNECT_ERRORS:
                return

        def _send_json(self, payload: dict, status: int = HTTPStatus.OK, *, redact: bool = False) -> None:
            data = redact_payload(payload, registry.get_all_secrets()) if redact else payload
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._write_response(status, "application/json; charset=utf-8", "no-store", body)

        def _strip_internal_fields(self, inst: dict) -> dict:
            return {k: v for k, v in inst.items() if not str(k).startswith("_")}

        def _handle_v2_get(self, path: str, query: dict[str, list[str]]) -> bool:
            if not path.startswith("/api/v2/"):
                return False

            if path == "/api/v2/notebooks":
                inc_bak = (query.get("include_backups") or ["0"])[0] in ("1", "true", "yes")
                launcher = _get_launcher()
                items = launcher.list_notebooks(include_backups=inc_bak)
                self._send_json(
                    {
                        "ok": True,
                        "roots": [str(r) for r in launcher.roots],
                        "agent_drive_path": str(resolved_monitor_root / "colab_agent.py"),
                        "notebooks": items,
                    },
                    redact=True,
                )
                return True

            if path == "/api/v2/instances":
                limit = _parse_output_limit(query)
                instances = registry.discover_all(output_limit=limit)
                up_summary = v2_uptime_tracker.record_instances(instances)
                default_inst = registry.select_default_instance(instances)
                public_list = [self._strip_internal_fields(i) for i in instances]
                self._send_json(
                    {
                        "schema_version": "2.0",
                        "monitor_root": str(resolved_monitor_root),
                        "default_instance_id": default_inst["instance_id"] if default_inst else None,
                        "instances": public_list,
                        "uptime_summary": up_summary,
                    },
                    redact=True,
                )
                return True

            if path.startswith("/api/v2/commands/"):
                cmd_id = unquote(path[len("/api/v2/commands/") :]).strip("/")
                if not SAFE_ID_RE.match(cmd_id):
                    self._send_json({"ok": False, "error": "Invalid cmd_id"}, HTTPStatus.BAD_REQUEST)
                    return True
                rec = router.get_command(cmd_id)
                if not rec:
                    self._send_json({"ok": False, "error": "Command not found"}, HTTPStatus.NOT_FOUND)
                    return True
                self._send_json({"ok": True, "command": rec}, redact=True)
                return True

            parts = [p for p in path[len("/api/v2/") :].split("/") if p]
            if len(parts) == 3 and parts[0] == "instances":
                iid = unquote(parts[1])
                sub = parts[2]
                if not SAFE_ID_RE.match(iid):
                    self._send_json({"ok": False, "error": "Invalid instance_id"}, HTTPStatus.BAD_REQUEST)
                    return True

                limit = _parse_output_limit(query)
                instances = registry.discover_all(output_limit=limit)
                up_summary = v2_uptime_tracker.record_instances(instances)
                inst = next((i for i in instances if i["instance_id"] == iid), None)
                if not inst:
                    self._send_json({"ok": False, "error": f"Instance not found: {iid}"}, HTTPStatus.NOT_FOUND)
                    return True

                if sub == "snapshot":
                    public_inst = self._strip_internal_fields(inst)
                    cmds = router.list_commands(iid, limit=20)
                    raw_legacy = inst.get("_raw_legacy_snapshot")
                    if isinstance(raw_legacy, dict):
                        v1_view = dict(raw_legacy)
                        v1_view["uptime_summary"] = up_summary
                    else:
                        v1_view = v2_to_v1_snapshot(
                            public_inst,
                            uptime_summary=up_summary,
                            recent_outputs=public_inst.get("recent_outputs") or [],
                            total_output_files=int(public_inst.get("total_output_files") or 0),
                        )
                    self._send_json(
                        {
                            "ok": True,
                            "instance": public_inst,
                            "commands": cmds,
                            "uptime_summary": up_summary,
                            "v1_snapshot": v1_view,
                        },
                        redact=True,
                    )
                    return True

                if sub == "timeseries":
                    win = (query.get("window") or ["1h"])[0]
                    samples: list[dict] = []
                    if local_storage is not None:
                        samples = local_storage.query_timeseries(iid, window=win)
                    tunnel = inst.get("tunnel") or {}
                    api_key = registry.get_secret(iid)
                    if (
                        not inst.get("is_legacy")
                        and tunnel.get("healthy")
                        and tunnel.get("base_url")
                        and api_key
                        and win in ("15m", "1h")
                    ):
                        try:
                            prefix = str(tunnel.get("agent_prefix") or "/agent/v1").rstrip("/")
                            res = _agent_http_get_json(
                                f"{tunnel['base_url'].rstrip('/')}{prefix}/timeseries?window={win}",
                                api_key=api_key,
                                timeout=2.5,
                            )
                            agent_samples = res.get("samples") or []
                            if len(agent_samples) > len(samples):
                                samples = agent_samples
                        except Exception:
                            pass
                    self._send_json({"ok": True, "instance_id": iid, "window": win, "samples": samples}, redact=True)
                    return True

                if sub == "processes":
                    try:
                        plimit = max(1, min(50, int((query.get("limit") or ["20"])[0])))
                    except ValueError:
                        plimit = 20
                    tunnel = inst.get("tunnel") or {}
                    api_key = registry.get_secret(iid)
                    if not inst.get("is_legacy") and tunnel.get("healthy") and tunnel.get("base_url") and api_key:
                        try:
                            prefix = str(tunnel.get("agent_prefix") or "/agent/v1").rstrip("/")
                            res = _agent_http_get_json(
                                f"{tunnel['base_url'].rstrip('/')}{prefix}/processes?limit={plimit}",
                                api_key=api_key,
                                timeout=2.5,
                            )
                            self._send_json(
                                {
                                    "ok": True,
                                    "instance_id": iid,
                                    "channel": "tunnel",
                                    "processes": res.get("processes") or [],
                                },
                                redact=True,
                            )
                            return True
                        except Exception as exc:
                            self._send_json(
                                {"ok": False, "instance_id": iid, "processes": [], "error": f"拉取进程失败：{exc}"},
                                status=HTTPStatus.BAD_GATEWAY,
                                redact=True,
                            )
                            return True
                    self._send_json(
                        {
                            "ok": True,
                            "instance_id": iid,
                            "channel": inst.get("active_channel") or "none",
                            "processes": inst.get("processes") or [],
                            "note": "实时进程列表仅在 ONLINE_LIVE 隧道模式下实时拉取。",
                        },
                        redact=True,
                    )
                    return True

                if sub == "logs":
                    src = (query.get("source") or ["agent"])[0]
                    if src not in ALLOWED_LOG_SOURCES:
                        self._send_json(
                            {"ok": False, "error": f"Invalid log source (allowlist: {sorted(ALLOWED_LOG_SOURCES)})"},
                            status=HTTPStatus.BAD_REQUEST,
                        )
                        return True
                    try:
                        tail = max(1, min(2000, int((query.get("tail") or ["200"])[0])))
                    except ValueError:
                        tail = 200
                    tunnel = inst.get("tunnel") or {}
                    api_key = registry.get_secret(iid)
                    if not inst.get("is_legacy") and tunnel.get("healthy") and tunnel.get("base_url") and api_key:
                        try:
                            prefix = str(tunnel.get("agent_prefix") or "/agent/v1").rstrip("/")
                            res = _agent_http_get_json(
                                f"{tunnel['base_url'].rstrip('/')}{prefix}/logs?source={src}&tail={tail}",
                                api_key=api_key,
                                timeout=3.0,
                            )
                            self._send_json({"ok": True, "instance_id": iid, "channel": "tunnel", **res}, redact=True)
                            return True
                        except Exception as exc:
                            self._send_json(
                                {"ok": False, "instance_id": iid, "source": src, "lines": [], "error": f"拉取日志失败：{exc}"},
                                status=HTTPStatus.BAD_GATEWAY,
                                redact=True,
                            )
                            return True
                    # Fallback to latest Drive snapshot if available
                    snap_dir = resolved_monitor_root / "snapshots"
                    if snap_dir.is_dir():
                        snaps = sorted(snap_dir.glob(f"{iid}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                        if snaps:
                            try:
                                sdata = json.loads(snaps[0].read_text(encoding="utf-8"))
                                slog = (sdata.get("logs") or {}).get(src) or {}
                                self._send_json(
                                    {
                                        "ok": True,
                                        "instance_id": iid,
                                        "channel": "drive_snapshot",
                                        "source": src,
                                        "lines": (slog.get("lines") or [])[-tail:],
                                        "truncated": bool(slog.get("truncated")),
                                        "redacted": True,
                                        "snapshot_file": snaps[0].name,
                                    },
                                    redact=True,
                                )
                                return True
                            except Exception:
                                pass
                    self._send_json(
                        {
                            "ok": True,
                            "instance_id": iid,
                            "channel": inst.get("active_channel") or "none",
                            "source": src,
                            "lines": [],
                            "truncated": False,
                            "redacted": True,
                            "note": "当前无可用隧道日志或云盘日志快照，可点击「保存日志快照至云盘」。",
                        },
                        redact=True,
                    )
                    return True

            self._send_json({"ok": False, "error": "Not Found"}, HTTPStatus.NOT_FOUND)
            return True

        def _handle_v2_post(self, path: str, query: dict[str, list[str]]) -> bool:
            if not path.startswith("/api/v2/"):
                return False

            action_hdr = (self.headers.get("X-Colab-Action") or "").strip()
            if not action_hdr:
                self._send_json(
                    {"ok": False, "error": "Forbidden: missing X-Colab-Action header"},
                    status=HTTPStatus.FORBIDDEN,
                )
                return True

            try:
                content_length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._send_json({"ok": False, "error": "Invalid Content-Length"}, HTTPStatus.BAD_REQUEST)
                return True
            if content_length < 0 or content_length > 16384:
                self._send_json({"ok": False, "error": "Payload Too Large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return True
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                body = json.loads(raw_body.decode("utf-8")) if raw_body.strip() else {}
            except Exception:
                self._send_json({"ok": False, "error": "Invalid JSON body"}, HTTPStatus.BAD_REQUEST)
                return True
            if not isinstance(body, dict):
                self._send_json({"ok": False, "error": "JSON body must be an object"}, HTTPStatus.BAD_REQUEST)
                return True

            req_action = str(body.get("action") or "").strip()
            if not req_action or req_action != action_hdr:
                self._send_json(
                    {"ok": False, "error": "Forbidden: X-Colab-Action header does not match body.action"},
                    status=HTTPStatus.FORBIDDEN,
                )
                return True

            if path in (
                "/api/v2/notebooks/inject",
                "/api/v2/notebooks/remove",
                "/api/v2/notebooks/bind-url",
                "/api/v2/notebooks/launch",
            ):
                expected_nb_action = {
                    "/api/v2/notebooks/inject": "notebooks.inject",
                    "/api/v2/notebooks/remove": "notebooks.remove",
                    "/api/v2/notebooks/bind-url": "notebooks.bind_url",
                    "/api/v2/notebooks/launch": "notebooks.launch",
                }[path]
                if req_action != expected_nb_action:
                    self._send_json(
                        {"ok": False, "error": f"Forbidden: expected action {expected_nb_action}"},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return True
                launcher = _get_launcher()
                raw_nb_path = str(body.get("path") or "")
                try:
                    if path == "/api/v2/notebooks/inject":
                        target = launcher.resolve_allowed_notebook(raw_nb_path)
                        res = inject_notebook(
                            target,
                            backup_dir=launcher.backup_dir,
                            monitor_root=resolved_monitor_root,
                            agent_src_path=agent_src_path,
                            dry_run=bool(body.get("dry_run", True)),
                            project=body.get("project"),
                            label=body.get("label"),
                            idle_timeout_minutes=int(body.get("idle_timeout_minutes", 60)),
                        )
                        self._send_json(res, status=HTTPStatus.OK, redact=True)
                        return True
                    if path == "/api/v2/notebooks/remove":
                        target = launcher.resolve_allowed_notebook(raw_nb_path)
                        res = remove_injection(
                            target,
                            backup_dir=launcher.backup_dir,
                            dry_run=bool(body.get("dry_run", True)),
                        )
                        self._send_json(res, status=HTTPStatus.OK, redact=True)
                        return True
                    if path == "/api/v2/notebooks/bind-url":
                        res = launcher.bind_url(raw_nb_path, str(body.get("colab_url") or ""))
                        self._send_json(res, status=HTTPStatus.OK, redact=True)
                        return True
                    if path == "/api/v2/notebooks/launch":
                        res = launcher.launch(raw_nb_path, open_browser=bool(body.get("open_browser", True)))
                        self._send_json(res, status=HTTPStatus.OK, redact=True)
                        return True
                except PermissionError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                    return True
                except FileNotFoundError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                    return True
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return True

            if path == "/api/v2/fleet/actions":
                inst_ids = body.get("instance_ids")
                if inst_ids is not None and not isinstance(inst_ids, list):
                    self._send_json({"ok": False, "error": "instance_ids must be a list or null"}, HTTPStatus.BAD_REQUEST)
                    return True
                code, res = router.dispatch_fleet_action(
                    req_action,
                    instance_ids=inst_ids,
                    params=body.get("params") if isinstance(body.get("params"), dict) else {},
                    issued_by=str(body.get("issued_by") or "local_web_ui"),
                )
                self._send_json(res, status=code, redact=True)
                return True

            parts = [p for p in path[len("/api/v2/") :].split("/") if p]
            if len(parts) == 3 and parts[0] == "instances" and parts[2] == "actions":
                iid = unquote(parts[1])
                if not SAFE_ID_RE.match(iid):
                    self._send_json({"ok": False, "error": "Invalid instance_id"}, HTTPStatus.BAD_REQUEST)
                    return True
                limit = _parse_output_limit(query)
                code, res = router.dispatch_action(
                    iid,
                    req_action,
                    params=body.get("params") if isinstance(body.get("params"), dict) else {},
                    issued_by=str(body.get("issued_by") or "local_web_ui"),
                    output_limit=limit,
                )
                self._send_json(res, status=code, redact=True)
                return True

            self._send_json({"ok": False, "error": "Not Found"}, HTTPStatus.NOT_FOUND)
            return True

        def _send_file(self, file_path: Path, cache_control: str = "no-cache") -> None:
            if not file_path.is_file():
                self._send_error_safe(HTTPStatus.NOT_FOUND, "Not Found")
                return
            mime, _ = mimetypes.guess_type(str(file_path))
            if file_path.suffix == ".svg":
                mime = "image/svg+xml"
            elif file_path.suffix == ".css":
                mime = "text/css; charset=utf-8"
            elif file_path.suffix == ".js":
                mime = "application/javascript; charset=utf-8"
            elif file_path.suffix == ".html":
                mime = "text/html; charset=utf-8"
            elif file_path.suffix == ".woff2":
                mime = "font/woff2"
            elif file_path.suffix == ".woff":
                mime = "font/woff"
            elif file_path.suffix == ".ttf":
                mime = "font/ttf"
            elif not mime:
                mime = "application/octet-stream"

            data = file_path.read_bytes()
            self._write_response(HTTPStatus.OK, mime, cache_control, data)

        def do_GET(self) -> None:  # noqa: N802
            if not self._check_request_origin(is_write=False):
                return
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            if self._handle_v2_get(path, query):
                return

            if path == "/api/snapshot":
                limit = _parse_output_limit(query)
                requested_iid = (query.get("instance_id") or [""])[0].strip()
                if requested_iid:
                    inst = registry.get_instance(requested_iid, refresh=True, output_limit=limit)
                    if inst and not inst.get("is_legacy"):
                        up_summary = v2_uptime_tracker.record_instances(registry.discover_all(output_limit=limit))
                        v1_view = v2_to_v1_snapshot(
                            inst,
                            uptime_summary=up_summary,
                            recent_outputs=inst.get("recent_outputs") or [],
                            total_output_files=int(inst.get("total_output_files") or 0),
                        )
                        self._send_json(v1_view, redact=True)
                        return
                snap = fetch_snapshot(resolved_ep, output_limit=limit, include_details=True)
                snap["uptime_summary"] = tracker.record_and_summarize(snap)
                self._send_json(snap, redact=True)
                return

            if path == "/api/output-image":
                filename = (query.get("file") or [""])[0]
                if not SAFE_OUTPUT_PNG.match(filename):
                    self._send_error_safe(HTTPStatus.BAD_REQUEST, "Invalid image filename")
                    return
                base_dir = resolved_ep.parent
                iid = (query.get("instance_id") or [""])[0].strip()
                if iid:
                    ep_for_iid = registry.get_legacy_ep_path(iid)
                    if ep_for_iid:
                        base_dir = ep_for_iid.parent
                out_dir = (base_dir / "outputs").resolve()
                target = (out_dir / filename).resolve()
                if not target.is_relative_to(out_dir) or not target.is_file():
                    self._send_error_safe(HTTPStatus.NOT_FOUND, "Image not found")
                    return
                self._send_file(target, cache_control="private, max-age=60")
                return

            if path == "/" or path == "/index.html":
                self._send_file(WEB_ROOT / "index.html")
                return

            rel = path.lstrip("/")
            candidate = (WEB_ROOT / rel).resolve()
            if not candidate.is_relative_to(WEB_ROOT):
                self._send_error_safe(HTTPStatus.FORBIDDEN, "Forbidden")
                return
            cache_ctrl = "public, max-age=86400" if candidate.suffix in (".woff2", ".woff", ".ttf") else "no-cache"
            self._send_file(candidate, cache_control=cache_ctrl)

        def do_POST(self) -> None:  # noqa: N802
            if not self._check_request_origin(is_write=True):
                return
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            if self._handle_v2_post(path, query):
                return

            actions = {
                "/api/keepalive": ("keepalive", send_keepalive),
                "/api/shutdown": ("shutdown", send_shutdown),
            }
            if path not in actions:
                self._send_error_safe(HTTPStatus.NOT_FOUND, "Not Found")
                return

            expected_action, action_fn = actions[path]
            if self.headers.get("X-H3-Action") != expected_action:
                self._send_json(
                    {"ok": False, "error": f"Forbidden: missing X-H3-Action: {expected_action} header"},
                    status=HTTPStatus.FORBIDDEN,
                )
                return

            try:
                content_length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._send_error_safe(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
                return
            if content_length < 0 or content_length > 4096:
                self._send_error_safe(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Payload Too Large")
                return
            if content_length > 0:
                self.rfile.read(content_length)

            limit = _parse_output_limit(query)
            res = action_fn(resolved_ep, output_limit=limit, include_details=True)
            if isinstance(res.get("snapshot"), dict):
                res["snapshot"]["uptime_summary"] = tracker.record_and_summarize(
                    res["snapshot"], force_flush=True
                )
            status_code = res.pop("status_code", None)
            status = HTTPStatus.OK if res.get("ok") else (status_code or HTTPStatus.BAD_GATEWAY)
            self._send_json(res, status=status, redact=True)

    return DashboardHandler


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """屏蔽浏览器主动中止请求导致的断连堆栈刷屏，并输出简洁友好的异常提示。"""

    def handle_error(self, request, client_address) -> None:
        _, exc, _ = sys.exc_info()
        if isinstance(exc, CLIENT_DISCONNECT_ERRORS):
            return
        addr = (
            f"{client_address[0]}:{client_address[1]}"
            if isinstance(client_address, tuple) and len(client_address) >= 2
            else str(client_address)
        )
        err_desc = f"{type(exc).__name__}: {exc}" if exc is not None else "未知错误"
        print(f"[服务端提示] 处理来自 {addr} 的请求时出现异常: {err_desc}", file=sys.stderr)


def run_server(
    ep_path: Path = DEFAULT_EP_FILE,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    allow_remote: bool = False,
    monitor_root: Path | None = None,
    state_dir: Path | None = None,
    scan_roots: list[Path] | None = None,
    notebook_roots: list[Path] | None = None,
) -> None:
    if not _is_loopback_host(host) and not allow_remote:
        print(
            f"[安全拒绝] 拒绝绑定非回环地址 {host}：本服务无内置鉴权，暴露到局域网/公网将允许他人读取 Prompt 或触发远程关机。"
            "如确需在受信任内网监听，请显式传入 --allow-remote。",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        handler = create_handler(
            ep_path.resolve(),
            allow_remote=allow_remote,
            monitor_root=monitor_root,
            state_dir=state_dir,
            scan_roots=scan_roots,
            notebook_roots=notebook_roots,
            enable_storage=True,
        )
    except ValueError as exc:
        print(f"[配置拒绝] {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        server = QuietThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        print(
            f"[启动失败] 无法在 {host}:{port} 启动监听 ({exc})；如端口已被占用，请改用 --port 指定其他端口（例如 --port 8766）。",
            file=sys.stderr,
        )
        sys.exit(1)
    url = f"http://{host}:{server.server_port}/"
    print(f"Colab 服务器监控与远程管控台 v2.0 已启动: {url}")
    print(f"Legacy 端点配置路径: {ep_path}")
    print(f"v2 云盘交换目录: {handler.control_registry.monitor_root}")
    if handler.control_storage:
        print(f"纯本地状态库: {handler.control_storage.db_path}")
    print("按 Ctrl+C 停止服务。")

    handler.control_collector.start()
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止 Web Dashboard。")
    finally:
        handler.control_collector.stop()
        handler.uptime_tracker.flush()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Colab 服务器全量监控与远程管控台服务端 (v2.0)")
    parser.add_argument("--ep", type=Path, default=DEFAULT_EP_FILE, help="Legacy active_endpoint.json 路径")
    parser.add_argument("--monitor-root", type=Path, default=None, help="v2 _colab_monitor 云盘共享交换目录")
    parser.add_argument("--state-dir", type=Path, default=None, help="纯本地状态目录（严禁位于云盘同步目录）")
    parser.add_argument("--scan-root", type=Path, action="append", default=None, help="附加 Legacy 扫描目录（可多次传入）")
    parser.add_argument("--notebook-root", type=Path, action="append", default=None, help="附加 Colab .ipynb 扫描目录（可多次传入）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    parser.add_argument("--no-open", action="store_true", help="启动时不自动打开浏览器")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="显式允许绑定非回环地址（服务无内置鉴权，仅限受信任内网）",
    )
    args = parser.parse_args()
    run_server(
        ep_path=args.ep,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        allow_remote=args.allow_remote,
        monitor_root=args.monitor_root,
        state_dir=args.state_dir,
        scan_roots=args.scan_root,
        notebook_roots=args.notebook_root,
    )


if __name__ == "__main__":
    main()
