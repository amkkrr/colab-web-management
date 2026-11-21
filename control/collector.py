"""Background collector, pure-local SQLite storage, multi-instance concurrent UptimeTracker, and Drive GC."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from control.protocol import assert_not_in_cloud_drive, atomic_write_json, parse_iso_ts, utc_now_iso

MAX_HEARTBEAT_GAP_SEC = 300.0
DISK_FLUSH_INTERVAL_SEC = 30.0
MAX_STORED_SEGMENTS = 2000
COMPACT_AGE_SEC = 30 * 86400.0


class LocalControlStorage:
    """Pure-local SQLite (WAL) store for command history and metrics samples; refuses cloud-synced paths."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = assert_not_in_cloud_drive(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "monitor_state.sqlite3"
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metrics_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id TEXT NOT NULL,
                    sampled_at_ts REAL NOT NULL,
                    gpu_util REAL,
                    vram_used_mib REAL,
                    ram_used_gib REAL,
                    disk_free_gib REAL,
                    temp_c REAL,
                    power_w REAL,
                    active_jobs INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_metrics_inst_ts ON metrics_samples(instance_id, sampled_at_ts);

                CREATE TABLE IF NOT EXISTS command_history (
                    cmd_id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    acked_at TEXT,
                    message TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_cmd_inst_issued ON command_history(instance_id, issued_at);
                """
            )

    def upsert_command(self, rec: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO command_history(cmd_id, instance_id, action, params_json, channel, status, issued_at, acked_at, message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cmd_id) DO UPDATE SET
                    channel=excluded.channel,
                    status=excluded.status,
                    acked_at=excluded.acked_at,
                    message=excluded.message
                """,
                (
                    rec["cmd_id"],
                    rec["instance_id"],
                    rec["action"],
                    json.dumps(rec.get("params") or {}, ensure_ascii=False),
                    rec.get("channel") or "drive",
                    rec.get("status") or "queued",
                    rec.get("issued_at") or utc_now_iso(),
                    rec.get("acked_at"),
                    rec.get("message") or "",
                ),
            )

    def get_command(self, cmd_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT cmd_id, instance_id, action, params_json, channel, status, issued_at, acked_at, message "
                "FROM command_history WHERE cmd_id = ?",
                (cmd_id,),
            ).fetchone()
        if not row:
            return None
        try:
            params = json.loads(row[3])
        except Exception:
            params = {}
        return {
            "cmd_id": row[0],
            "instance_id": row[1],
            "action": row[2],
            "params": params,
            "channel": row[4],
            "status": row[5],
            "issued_at": row[6],
            "acked_at": row[7],
            "message": row[8],
        }

    def record_sample(self, inst: dict[str, Any], now_ts: float) -> None:
        hw = inst.get("hardware") or {}
        gpu = hw.get("gpu") or {}
        ram = hw.get("ram") or {}
        disk = hw.get("disk") or {}
        wl = inst.get("workload") or {}
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO metrics_samples(
                    instance_id, sampled_at_ts, gpu_util, vram_used_mib, ram_used_gib, disk_free_gib, temp_c, power_w, active_jobs
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inst["instance_id"],
                    now_ts,
                    gpu.get("util_gpu_pct"),
                    gpu.get("vram_used_mib"),
                    ram.get("used_gib"),
                    disk.get("free_gib"),
                    gpu.get("temp_c"),
                    gpu.get("power_draw_w") if "power_draw_w" in gpu else gpu.get("power_w"),
                    wl.get("running", 0),
                ),
            )

    def query_timeseries(self, instance_id: str, window: str = "1h", now_ts: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now_ts is None else float(now_ts)
        spans = {"15m": 900.0, "1h": 3600.0, "6h": 21600.0, "24h": 86400.0}
        cutoff = now - spans.get(window, 3600.0)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sampled_at_ts, gpu_util, vram_used_mib, ram_used_gib, disk_free_gib, temp_c, power_w, active_jobs
                FROM metrics_samples
                WHERE instance_id = ? AND sampled_at_ts >= ?
                ORDER BY sampled_at_ts ASC
                LIMIT 720
                """,
                (instance_id, cutoff),
            ).fetchall()
        return [
            {
                "sampled_at": utc_now_iso(r[0]),
                "sampled_at_ts": round(r[0], 1),
                "gpu_util": r[1],
                "vram_used_mib": r[2],
                "ram_used_gib": r[3],
                "disk_free_gib": r[4],
                "temp_c": r[5],
                "power_w": r[6],
                "active_jobs": r[7],
            }
            for r in rows
        ]


def _classify_v2_hardware(inst: dict[str, Any], prior_hw: tuple[str, str] | None = None) -> tuple[str, str]:
    hw = inst.get("hardware") or {}
    gpu = hw.get("gpu")
    if isinstance(gpu, dict):
        if gpu.get("available") is True:
            name = " ".join(str(gpu.get("name") or "").split()) or "未知 GPU"
            return name, "gpu"
        if gpu.get("available") is False:
            return "CPU Only", "cpu_only"
    if prior_hw is not None and prior_hw[1] != "unknown":
        return prior_hw
    return "待识别硬件", "unknown"


class MultiInstanceUptimeTracker:
    """
    Concurrent multi-instance UptimeTracker compatible with v1 uptime_ledger.json.
    Maintains independent active segments per instance/session_key so concurrently online
    instances never deactivate each other (§6.2).
    """

    def __init__(self, ledger_path: Path, legacy_import_path: Path | None = None) -> None:
        self.ledger_path = ledger_path.resolve()
        self.legacy_import_path = legacy_import_path.resolve() if legacy_import_path else None
        self._lock = threading.Lock()
        self._loaded = False
        self._seen_session_keys: set[str] = set()
        self._archived_totals: dict[str, dict[str, Any]] = {}
        self._segments: list[dict[str, Any]] = []
        self._last_flush_ts: float = 0.0
        self._dirty_immediate = False
        self._dirty_periodic = False

    def _ensure_loaded_locked(self) -> None:
        if self._loaded:
            return
        src = self.ledger_path
        if not src.is_file() and self.legacy_import_path and self.legacy_import_path.is_file():
            src = self.legacy_import_path
            self._dirty_immediate = True
        if src.is_file():
            try:
                raw = json.loads(src.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._load_payload_locked(raw)
            except Exception:
                pass
        self._loaded = True

    def _load_payload_locked(self, raw: dict[str, Any]) -> None:
        seen = {s for s in (raw.get("seen_session_keys") or []) if isinstance(s, str) and s}
        archived: dict[str, dict[str, Any]] = {}
        for hw_key, info in (raw.get("archived_totals") or {}).items():
            if not isinstance(hw_key, str) or not hw_key or not isinstance(info, dict):
                continue
            cat = info.get("category")
            if cat not in ("gpu", "cpu_only", "unknown"):
                cat = "cpu_only" if hw_key == "CPU Only" else "gpu"
            archived[hw_key] = {
                "category": cat,
                "seconds": max(0.0, float(info.get("seconds") or 0.0)),
                "session_count": max(0, int(info.get("session_count") or 0)),
                "last_seen_ts": float(info["last_seen_ts"]) if isinstance(info.get("last_seen_ts"), (int, float)) else None,
            }
        segments: list[dict[str, Any]] = []
        for seg in raw.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            skey = seg.get("session_key")
            hw_key = seg.get("hardware_key")
            st = seg.get("start_ts")
            ls = seg.get("last_seen_ts")
            if not isinstance(skey, str) or not isinstance(hw_key, str) or not isinstance(st, (int, float)) or not isinstance(ls, (int, float)):
                continue
            cat = seg.get("category")
            if cat not in ("gpu", "cpu_only", "unknown"):
                cat = "cpu_only" if hw_key == "CPU Only" else "gpu"
            segments.append(
                {
                    "session_key": skey,
                    "hardware_key": hw_key,
                    "category": cat,
                    "start_ts": max(0.0, float(st)),
                    "last_seen_ts": max(float(st), float(ls)),
                    "active": bool(seg.get("active")),
                }
            )
            seen.add(skey)
        self._seen_session_keys = seen
        self._archived_totals = archived
        self._segments = segments

    def _flush_locked(self, now: float, force_flush: bool = False) -> None:
        if not self._segments and not self._archived_totals and not self.ledger_path.exists():
            return
        if not (force_flush or self._dirty_immediate or (self._dirty_periodic and now - self._last_flush_ts >= DISK_FLUSH_INTERVAL_SEC)):
            return
        payload = {
            "version": 2,
            "updated_at": utc_now_iso(now),
            "seen_session_keys": sorted(self._seen_session_keys),
            "archived_totals": self._archived_totals,
            "segments": self._segments,
        }
        try:
            atomic_write_json(self.ledger_path, payload)
            self._last_flush_ts = now
            self._dirty_immediate = False
            self._dirty_periodic = False
        except OSError:
            pass

    def flush(self) -> None:
        with self._lock:
            if not self._loaded:
                return
            self._flush_locked(time.time(), force_flush=True)

    def record_instances(
        self,
        instances: list[dict[str, Any]],
        *,
        now_ts: float | None = None,
        force_flush: bool = False,
    ) -> dict[str, Any]:
        """Update active segments independently for EVERY online instance without cross-deactivating."""
        with self._lock:
            now = time.time() if now_ts is None else float(now_ts)
            self._ensure_loaded_locked()

            online_keys: set[str] = set()
            for inst in instances:
                liveness = inst.get("liveness_state") or "OFFLINE"
                if liveness not in ("ONLINE_LIVE", "ONLINE_DRIVE_ONLY", "DRAINING"):
                    continue
                skey = str(inst["instance_id"])
                online_keys.add(skey)
                prior_hw = None
                for seg in reversed(self._segments):
                    if seg["session_key"] == skey and seg["category"] != "unknown":
                        prior_hw = (seg["hardware_key"], seg["category"])
                        break
                hw_key, hw_cat = _classify_v2_hardware(inst, prior_hw)

                active_seg = next((s for s in reversed(self._segments) if s["session_key"] == skey and s.get("active")), None)
                if active_seg is not None and 0.0 <= now - active_seg["last_seen_ts"] <= MAX_HEARTBEAT_GAP_SEC:
                    if active_seg["hardware_key"] != hw_key and hw_cat != "unknown":
                        active_seg["last_seen_ts"] = max(active_seg["last_seen_ts"], now)
                        active_seg["active"] = False
                        self._segments.append(
                            {
                                "session_key": skey,
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
                    for s in self._segments:
                        if s["session_key"] == skey and s.get("active"):
                            s["active"] = False
                            self._dirty_immediate = True
                    already_seen = skey in self._seen_session_keys
                    seed_sec = 0.0
                    if not already_seen:
                        started_ts = parse_iso_ts(inst.get("started_at"))
                        if started_ts is not None and 0.0 < now - started_ts <= 86400.0:
                            seed_sec = now - started_ts
                        else:
                            idle = (inst.get("lifecycle") or {}).get("idle_seconds")
                            if isinstance(idle, (int, float)) and not isinstance(idle, bool) and idle > 0:
                                seed_sec = min(float(idle), 7200.0)
                    self._segments.append(
                        {
                            "session_key": skey,
                            "hardware_key": hw_key,
                            "category": hw_cat,
                            "start_ts": max(0.0, now - seed_sec),
                            "last_seen_ts": now,
                            "active": True,
                        }
                    )
                    self._seen_session_keys.add(skey)
                    self._dirty_immediate = True

            # Only deactivate segments whose session_key is no longer online
            for seg in self._segments:
                if seg.get("active") and seg["session_key"] not in online_keys:
                    seg["active"] = False
                    self._dirty_immediate = True

            self._flush_locked(now, force_flush=force_flush)
            return self._build_summary_locked(online_keys, now)

    def _build_summary_locked(self, online_keys: set[str], now: float) -> dict[str, Any]:
        active_segs = [s for s in self._segments if s.get("active") and s["session_key"] in online_keys]
        active_hws = {s["hardware_key"] for s in active_segs}
        local_now = datetime.fromtimestamp(now).astimezone()
        today_start_dt = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_ts = today_start_dt.timestamp()
        seven_days_start_ts = now - 7 * 86400.0

        catalog: dict[str, str] = {"CPU Only": "cpu_only"}
        global_last_seen: dict[str, float] = {}
        for hw_key, info in self._archived_totals.items():
            catalog.setdefault(hw_key, info.get("category", "gpu"))
            if isinstance(info.get("last_seen_ts"), (int, float)):
                global_last_seen[hw_key] = max(global_last_seen.get(hw_key, 0.0), float(info["last_seen_ts"]))
        for seg in self._segments:
            hk = seg["hardware_key"]
            catalog[hk] = "cpu_only" if hk == "CPU Only" else catalog.get(hk, seg["category"])
            global_last_seen[hk] = max(global_last_seen.get(hk, 0.0), seg["last_seen_ts"])

        def compute_win(win_id: str) -> dict[str, Any]:
            durations = {hk: 0.0 for hk in catalog}
            hw_sessions: dict[str, set[str]] = {hk: set() for hk in catalog}
            arch_cnt = {hk: 0 for hk in catalog}
            all_sess: set[str] = set()
            if win_id == "all":
                for hk, info in self._archived_totals.items():
                    durations[hk] += float(info.get("seconds") or 0.0)
                    arch_cnt[hk] += int(info.get("session_count") or 0)
            for seg in self._segments:
                st, en, hk = seg["start_ts"], max(seg["start_ts"], seg["last_seen_ts"]), seg["hardware_key"]
                if win_id == "all":
                    dur, inc = max(0.0, en - st), True
                elif win_id == "today":
                    dur = max(0.0, min(en, now) - max(st, today_start_ts))
                    inc = dur > 0.0 or (bool(seg.get("active")) and en >= today_start_ts)
                elif win_id == "7d":
                    dur = max(0.0, min(en, now) - max(st, seven_days_start_ts))
                    inc = dur > 0.0 or (bool(seg.get("active")) and en >= seven_days_start_ts)
                else:
                    inc = seg["session_key"] in online_keys
                    dur = max(0.0, en - st) if inc else 0.0
                if inc:
                    durations[hk] += dur
                    hw_sessions[hk].add(seg["session_key"])
                    all_sess.add(seg["session_key"])

            total = sum(durations.values())
            items = []
            for hk, hcat in catalog.items():
                sec = durations[hk]
                ls_ts = global_last_seen.get(hk)
                items.append(
                    {
                        "hardware_key": hk,
                        "category": hcat,
                        "seconds": round(sec, 1),
                        "share_pct": round(sec * 100.0 / total, 1) if total > 0 else 0.0,
                        "session_count": len(hw_sessions[hk]) + arch_cnt[hk],
                        "is_current": hk in active_hws,
                        "last_seen_at": utc_now_iso(ls_ts) if ls_ts is not None else None,
                    }
                )
            items.sort(key=lambda x: (-x["seconds"], 0 if x["is_current"] else 1, x["hardware_key"]))
            return {
                "total_seconds": round(total, 1),
                "gpu_seconds": round(sum(d for hk, d in durations.items() if catalog[hk] == "gpu"), 1),
                "cpu_only_seconds": round(sum(d for hk, d in durations.items() if catalog[hk] == "cpu_only"), 1),
                "unknown_seconds": round(sum(d for hk, d in durations.items() if catalog[hk] == "unknown"), 1),
                "session_count": len(all_sess) + (sum(arch_cnt.values()) if win_id == "all" else 0),
                "items": items,
            }

        win_all, win_today, win_7d, win_cur = compute_win("all"), compute_win("today"), compute_win("7d"), compute_win("current")
        primary_active = active_segs[-1] if active_segs else None
        return {
            "ledger_file": str(self.ledger_path.name),
            "updated_at": utc_now_iso(now),
            "today_start_at": today_start_dt.isoformat(timespec="seconds"),
            "current_online": bool(active_segs),
            "online_count": len(active_segs),
            "current_hardware": primary_active["hardware_key"] if primary_active else None,
            "current_category": primary_active["category"] if primary_active else None,
            "current_session_seconds": win_cur["total_seconds"] if active_segs else 0.0,
            "total_sessions": win_all["session_count"],
            "windows": {"all": win_all, "today": win_today, "7d": win_7d, "current": win_cur},
        }


def run_drive_gc(monitor_root: Path, now_ts: float | None = None) -> dict[str, int]:
    """Prune stale files in _colab_monitor/ per §6.2 retention rules."""
    now = time.time() if now_ts is None else float(now_ts)
    root = monitor_root.resolve()
    removed = {"commands": 0, "acks": 0, "instances": 0, "secrets": 0, "snapshots": 0}
    if not root.is_dir():
        return removed

    # commands > 24h
    cmd_root = root / "commands"
    if cmd_root.is_dir():
        for fp in cmd_root.rglob("*.json"):
            try:
                if now - fp.stat().st_mtime > 86400.0:
                    fp.unlink(missing_ok=True)
                    removed["commands"] += 1
            except OSError:
                pass

    # acks > 7d
    ack_root = root / "acks"
    if ack_root.is_dir():
        for fp in ack_root.rglob("*.json"):
            try:
                if now - fp.stat().st_mtime > 7 * 86400.0:
                    fp.unlink(missing_ok=True)
                    removed["acks"] += 1
            except OSError:
                pass

    # offline instances > 7d & offline secrets > 24h
    inst_root = root / "instances"
    offline_ages: dict[str, float] = {}
    if inst_root.is_dir():
        for fp in inst_root.glob("*.json"):
            if fp.name.startswith("._tmp_"):
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                hb_ts = parse_iso_ts(data.get("heartbeat_at")) or fp.stat().st_mtime
                age = now - hb_ts
                iid = str(data.get("instance_id") or fp.stem)
                if age > 300.0 or (data.get("lifecycle") or {}).get("state") == "terminated":
                    offline_ages[iid] = age
                if age > 7 * 86400.0:
                    fp.unlink(missing_ok=True)
                    removed["instances"] += 1
            except Exception:
                pass

    sec_root = root / "secrets"
    if sec_root.is_dir():
        for fp in sec_root.glob("*.json"):
            iid = fp.stem
            age = offline_ages.get(iid)
            try:
                if (age is not None and age > 86400.0) or (iid not in offline_ages and not (inst_root / f"{iid}.json").exists() and now - fp.stat().st_mtime > 86400.0):
                    fp.unlink(missing_ok=True)
                    removed["secrets"] += 1
            except OSError:
                pass

    # snapshots: keep latest 30 and <= 14d
    snap_root = root / "snapshots"
    if snap_root.is_dir():
        snaps = sorted(snap_root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for idx, fp in enumerate(snaps):
            try:
                if idx >= 30 or now - fp.stat().st_mtime > 14 * 86400.0:
                    fp.unlink(missing_ok=True)
                    removed["snapshots"] += 1
            except OSError:
                pass

    return removed


class ControlPlaneCollector:
    """Background daemon polling instances, tracking ACKs, recording metrics/uptime, and running GC."""

    def __init__(
        self,
        registry: Any,
        router: Any,
        uptime_tracker: MultiInstanceUptimeTracker,
        storage: LocalControlStorage | None = None,
        monitor_root: Path | None = None,
        normal_interval_sec: float = 15.0,
        fast_interval_sec: float = 5.0,
    ) -> None:
        self.registry = registry
        self.router = router
        self.uptime_tracker = uptime_tracker
        self.storage = storage
        self.monitor_root = monitor_root
        self.normal_interval_sec = float(normal_interval_sec)
        self.fast_interval_sec = float(fast_interval_sec)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_gc_ts = 0.0

    def tick_once(self) -> None:
        now = time.time()
        instances = self.registry.discover_all(now_ts=now)
        self.uptime_tracker.record_instances(instances, now_ts=now)
        self.router.poll_acks(now_ts=now)
        if self.storage is not None:
            for inst in instances:
                if inst.get("liveness_state") in ("ONLINE_LIVE", "ONLINE_DRIVE_ONLY", "DRAINING"):
                    try:
                        self.storage.record_sample(inst, now)
                    except Exception:
                        pass
        if self.monitor_root and now - self._last_gc_ts >= 3600.0:
            run_drive_gc(self.monitor_root, now_ts=now)
            self._last_gc_ts = now

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.is_set():
                try:
                    self.tick_once()
                except Exception:
                    pass
                interval = self.fast_interval_sec if self.router.has_pending_commands() else self.normal_interval_sec
                self._stop.wait(interval)

        self._thread = threading.Thread(target=_loop, daemon=True, name="colab-control-collector")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.uptime_tracker.flush()
