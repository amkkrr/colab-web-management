"""Multi-instance discovery registry with Legacy active_endpoint.json adapter and deduplication."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from control.protocol import (
    LEGACY_SUPPORTED_ACTIONS,
    compute_liveness,
    parse_iso_ts,
    utc_now_iso,
    validate_upstream_url,
)
from h3_cli_monitor import collect_local_drive_stats, fetch_snapshot

_DRIVE_CONFLICT_RE = re.compile(r"\s*\(\d+\)$")


def auto_discover_monitor_root(ep_path: Path | None = None) -> Path:
    """Locate _colab_monitor exchange folder via env var, ep sibling, or mounted Google Drive."""
    import os

    env_root = os.environ.get("COLAB_MONITOR_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    if ep_path:
        parent = ep_path.resolve().parent
        if (parent / "_colab_monitor").exists():
            return (parent / "_colab_monitor").resolve()
        if (parent.parent / "_colab_monitor").exists():
            return (parent.parent / "_colab_monitor").resolve()
        if any(marker in str(parent.parent).lower() for marker in ("cloudstorage", "googledrive", "mydrive")):
            return (parent.parent / "_colab_monitor").resolve()
    cloud_root = Path.home() / "Library" / "CloudStorage"
    if cloud_root.exists():
        for pattern in ("GoogleDrive-*/*/_colab_monitor", "GoogleDrive-*/*/*/_colab_monitor"):
            for cand in cloud_root.glob(pattern):
                if cand.is_dir():
                    return cand.resolve()
        for drive_root in cloud_root.glob("GoogleDrive-*/*"):
            if drive_root.is_dir():
                return (drive_root / "_colab_monitor").resolve()
    if ep_path:
        return (ep_path.resolve().parent / "_colab_monitor").resolve()
    return (Path(__file__).resolve().parent.parent / "_colab_monitor").resolve()


def auto_discover_legacy_ep_files(primary_ep: Path | None = None, extra_scan_roots: list[Path] | None = None) -> list[Path]:
    """Discover candidate Legacy active_endpoint.json paths without duplicates."""
    found: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            found.append(rp)

    if primary_ep:
        _add(primary_ep)

    for root in extra_scan_roots or []:
        rp = root.resolve()
        if rp.is_file() and rp.name == "active_endpoint.json":
            _add(rp)
        elif rp.is_dir():
            ep = rp / "active_endpoint.json"
            if ep.is_file():
                _add(ep)

    # Scan sibling project folders beside primary_ep.parent when residing in a shared workspace/drive folder
    if primary_ep and primary_ep.resolve().parent.parent.is_dir():
        parent_workspace = primary_ep.resolve().parent.parent
        try:
            for ep in sorted(parent_workspace.glob("*/active_endpoint.json")):
                if ep.is_file():
                    _add(ep)
        except OSError:
            pass

    return found


def _http_get_json(url: str, api_key: str | None = None, timeout: float = 2.5) -> dict[str, Any]:
    safe_url = validate_upstream_url(url)
    headers = {"User-Agent": "Colab-Control-Plane/2.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(safe_url, headers=headers, method="GET")
    if safe_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:  # nosec B310 - scheme/host strictly validated
            return json.loads(resp.read().decode("utf-8"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - https scheme strictly validated
        return json.loads(resp.read().decode("utf-8"))


class InstanceRegistry:
    """Aggregates v2 _colab_monitor instances and Legacy active_endpoint.json instances with dedup."""

    def __init__(
        self,
        *,
        monitor_root: Path,
        primary_ep: Path | None = None,
        scan_roots: list[Path] | None = None,
        tunnel_timeout: float = 2.5,
    ) -> None:
        self.monitor_root = monitor_root.resolve()
        self.primary_ep = primary_ep.resolve() if primary_ep else None
        self.scan_roots = [p.resolve() for p in (scan_roots or [])]
        self.tunnel_timeout = float(tunnel_timeout)
        self._secrets: dict[str, str] = {}
        self._ep_by_instance: dict[str, Path] = {}
        self._cached_instances: dict[str, dict[str, Any]] = {}

    def get_secret(self, instance_id: str) -> str | None:
        return self._secrets.get(instance_id)

    def get_all_secrets(self) -> list[str]:
        return [s for s in self._secrets.values() if s]

    def get_legacy_ep_path(self, instance_id: str) -> Path | None:
        return self._ep_by_instance.get(instance_id)

    def _load_v2_secret(self, instance_id: str) -> str | None:
        sec_path = self.monitor_root / "secrets" / f"{instance_id}.json"
        if not sec_path.is_file():
            return self._secrets.get(instance_id)
        try:
            data = json.loads(sec_path.read_text(encoding="utf-8"))
            key = (data.get("api_key") or "").strip() if isinstance(data, dict) else ""
            if key:
                self._secrets[instance_id] = key
                return key
        except Exception:
            pass
        return self._secrets.get(instance_id)

    def _scan_v2_heartbeats(self) -> list[tuple[Path, dict[str, Any]]]:
        inst_dir = self.monitor_root / "instances"
        if not inst_dir.is_dir():
            return []
        by_id: dict[str, tuple[float, Path, dict[str, Any]]] = {}
        for fp in inst_dir.glob("*.json"):
            if fp.name.startswith("._tmp_"):
                continue
            try:
                raw = json.loads(fp.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    continue
                iid = str(raw.get("instance_id") or _DRIVE_CONFLICT_RE.sub("", fp.stem)).strip()
                if not iid:
                    continue
                hb_ts = parse_iso_ts(raw.get("heartbeat_at")) or fp.stat().st_mtime
                prev = by_id.get(iid)
                if prev is None or hb_ts >= prev[0]:
                    by_id[iid] = (hb_ts, fp, raw)
            except Exception:
                continue
        return [(fp, data) for _, fp, data in by_id.values()]

    def _probe_v2_instance(self, hb_path: Path, hb: dict[str, Any], now_ts: float) -> dict[str, Any]:
        iid = str(hb["instance_id"])
        api_key = self._load_v2_secret(iid)
        tunnel = dict(hb.get("tunnel") or {})
        base_url = str(tunnel.get("base_url") or "").rstrip("/")
        prefix = str(tunnel.get("agent_prefix") or "/agent/v1").rstrip("/")
        tunnel_enabled = bool(tunnel.get("enabled")) and bool(base_url)

        live_data: dict[str, Any] | None = None
        tunnel_ok = False
        tunnel_error: str | None = None

        if tunnel_enabled and api_key:
            try:
                live_data = _http_get_json(f"{base_url}{prefix}/metrics", api_key=api_key, timeout=self.tunnel_timeout)
                tunnel_ok = True
            except urllib.error.HTTPError as he:
                tunnel_error = f"HTTP {he.code}"
            except Exception as exc:
                tunnel_error = str(exc)
        elif tunnel_enabled:
            try:
                _http_get_json(f"{base_url}{prefix}/health", api_key=None, timeout=self.tunnel_timeout)
                tunnel_ok = True
            except Exception as exc:
                tunnel_error = str(exc)

        merged = dict(hb)
        if isinstance(live_data, dict):
            merged.update(live_data)

        merged_tunnel = dict(merged.get("tunnel") or tunnel)
        merged_tunnel["healthy"] = tunnel_ok
        merged["tunnel"] = merged_tunnel

        lc = merged.get("lifecycle") or {}
        liveness, age_sec, clock_skew = compute_liveness(
            heartbeat_at=merged.get("heartbeat_at"),
            tunnel_ok=tunnel_ok,
            drain_requested=bool(lc.get("drain_requested")),
            lifecycle_state=lc.get("state"),
            now_ts=now_ts,
        )

        merged["instance_id"] = iid
        merged["is_legacy"] = False
        merged["liveness_state"] = liveness
        merged["heartbeat_age_sec"] = round(age_sec, 1) if age_sec is not None else None
        merged["clock_skew_warning"] = clock_skew
        merged["active_channel"] = "tunnel" if tunnel_ok else ("drive" if liveness in ("ONLINE_DRIVE_ONLY", "DRAINING", "DEGRADED_STALE") else "none")
        merged["has_api_key"] = bool(api_key)
        merged["source_path"] = str(hb_path)
        merged["fetched_at"] = utc_now_iso(now_ts)
        if tunnel_error and not tunnel_ok:
            merged["tunnel_error"] = tunnel_error
        return merged

    def _build_legacy_instance(self, ep_path: Path, now_ts: float, output_limit: int = 12) -> dict[str, Any]:
        snap = fetch_snapshot(ep_path, output_limit=output_limit, include_details=True, timeout=min(5.0, max(2.5, self.tunnel_timeout)))
        key_hash = hashlib.sha1(str(ep_path.resolve()).encode("utf-8")).hexdigest()[:8]
        folder_slug = re.sub(r"[^a-z0-9]+", "_", ep_path.parent.name.lower()).strip("_") or "legacy"
        iid = f"legacy_{folder_slug}_{key_hash}"

        # Load secret into memory only for redaction
        try:
            if ep_path.is_file():
                ep_raw = json.loads(ep_path.read_text(encoding="utf-8"))
                k = (ep_raw.get("api_key") or "").strip()
                if k:
                    self._secrets[iid] = k
        except Exception:
            pass
        self._ep_by_instance[iid] = ep_path.resolve()

        online = bool(snap.get("online"))
        shutdown_pending = bool(snap.get("shutdown_pending"))
        if shutdown_pending:
            liveness = "SHUTTING_DOWN"
        elif online:
            liveness = "ONLINE_LIVE"
        else:
            liveness = "OFFLINE"

        h = snap.get("health") or {}
        m = snap.get("metrics") or {}
        gpu = dict(m.get("gpu") or {"available": False})
        if "power_w" in gpu and "power_draw_w" not in gpu:
            gpu["power_draw_w"] = gpu["power_w"]
        jobs = m.get("jobs") or {}

        return {
            "schema_version": "1.1-legacy",
            "instance_id": iid,
            "is_legacy": True,
            "project": folder_slug,
            "label": f"{ep_path.parent.name} (Legacy)",
            "notebook_name": "",
            "run_mode": "online_gateway",
            "started_at": snap.get("endpoint_updated_at"),
            "heartbeat_at": snap.get("fetched_at"),
            "heartbeat_interval_sec": 10,
            "capabilities": sorted(LEGACY_SUPPORTED_ACTIONS),
            "managed_services": [],
            "tunnel": {
                "enabled": bool(snap.get("base_url")),
                "healthy": online,
                "base_url": snap.get("base_url") or "",
                "agent_prefix": "",
                "updated_at": snap.get("endpoint_updated_at"),
            },
            "lifecycle": {
                "state": "shutting_down" if shutdown_pending else ("running" if online else "terminated"),
                "idle_seconds": h.get("idle_seconds", 0),
                "idle_timeout_minutes": h.get("idle_timeout_minutes", (snap.get("endpoint_meta") or {}).get("idle_timeout_minutes") or 0),
                "keepalive_lease_until": None,
                "deadline_shutdown_at": None,
                "auto_shutdown_after_batch": False,
                "drain_requested": False,
            },
            "hardware": {
                "gpu": gpu,
                "cpu": {"cores_logical": None, "util_pct": None, "load_avg_1m": None},
                "ram": m.get("ram") or {},
                "disk": m.get("disk") or {},
                "drive": {"mode": h.get("gdrive_mode") or "legacy", "healthy": online},
            }
            if online and m
            else {},
            "services": {
                "comfyui": {"status": "running" if h.get("comfy_alive") else "unknown", "pid": None, "port": 8188}
            }
            if online
            else {},
            "workload": {
                "kind": "job_queue",
                "paused": False,
                "running": jobs.get("running", h.get("active_jobs", 0)),
                "queued": jobs.get("queued", 0),
                "succeeded": jobs.get("succeeded", 0),
                "failed": jobs.get("failed", 0),
                "progress": {},
            },
            "liveness_state": liveness,
            "heartbeat_age_sec": 0.0 if online else None,
            "clock_skew_warning": False,
            "active_channel": "tunnel" if online else "none",
            "has_api_key": bool(snap.get("has_api_key")),
            "source_path": str(ep_path.resolve()),
            "legacy_ep_path": str(ep_path.resolve()),
            "fetched_at": snap.get("fetched_at") or utc_now_iso(now_ts),
            "offline_reason": snap.get("offline_reason"),
            "error": snap.get("error"),
            "metrics_note": snap.get("metrics_note"),
            "dit_tier": h.get("dit_tier") or (snap.get("endpoint_meta") or {}).get("dit_tier"),
            "recent_outputs": snap.get("recent_outputs") or [],
            "total_output_files": snap.get("total_output_files", 0),
            "_raw_legacy_snapshot": snap,
        }

    def discover_all(self, *, now_ts: float | None = None, output_limit: int = 12) -> list[dict[str, Any]]:
        """Discover and deduplicate all v2 and Legacy instances."""
        now = time.time() if now_ts is None else float(now_ts)
        v2_list: list[dict[str, Any]] = []
        v2_by_id: dict[str, dict[str, Any]] = {}
        v2_by_url: dict[str, dict[str, Any]] = {}
        v2_by_ep_hint: dict[str, dict[str, Any]] = {}

        for hb_path, hb in self._scan_v2_heartbeats():
            inst = self._probe_v2_instance(hb_path, hb, now)
            v2_list.append(inst)
            v2_by_id[inst["instance_id"]] = inst
            url = (inst.get("tunnel") or {}).get("base_url") or ""
            if url:
                v2_by_url[url.rstrip("/")] = inst
            hint = inst.get("legacy_ep_hint")
            if isinstance(hint, str) and hint:
                v2_by_ep_hint[hint] = inst
                try:
                    v2_by_ep_hint[str(Path(hint).resolve())] = inst
                except Exception:
                    pass

        legacy_list: list[dict[str, Any]] = []
        ep_files = auto_discover_legacy_ep_files(self.primary_ep, self.scan_roots)
        has_any_existing_ep = any(p.is_file() for p in ep_files)
        for ep_path in ep_files:
            if not ep_path.is_file():
                # Only emit a missing-config Legacy placeholder when no v2 instances or existing ep files were found
                if v2_list or has_any_existing_ep or ep_path != self.primary_ep:
                    continue
            ep_raw: dict[str, Any] = {}
            if ep_path.is_file():
                try:
                    ep_raw = json.loads(ep_path.read_text(encoding="utf-8"))
                except Exception:
                    ep_raw = {}

            # Deduplication check against discovered v2 instances (§6.2)
            ep_url = str(ep_raw.get("base_url") or "").rstrip("/")
            ep_iid = str(ep_raw.get("instance_id") or "").strip()
            ep_str = str(ep_path.resolve())
            matched_v2 = (
                (v2_by_id.get(ep_iid) if ep_iid else None)
                or (v2_by_url.get(ep_url) if ep_url else None)
                or v2_by_ep_hint.get(ep_str)
                or next((v for h, v in v2_by_ep_hint.items() if ep_str.endswith(h)), None)
            )
            if matched_v2 is not None:
                # Attach local outputs from matched legacy project dir to the single v2 instance
                out_dir = ep_path.parent / "outputs"
                matched_v2["legacy_ep_path"] = ep_str
                self._ep_by_instance[matched_v2["instance_id"]] = ep_path.resolve()
                matched_v2["recent_outputs"] = collect_local_drive_stats(out_dir, limit=output_limit, include_details=True)
                matched_v2["total_output_files"] = len(list(out_dir.glob("h3_*.json"))) if out_dir.exists() else 0
                continue

            legacy_inst = self._build_legacy_instance(ep_path, now, output_limit=output_limit)
            legacy_list.append(legacy_inst)

        # Attach primary outputs to v2 instances that didn't match a legacy file but have a project_drive_dir
        for inst in v2_list:
            if "recent_outputs" not in inst:
                inst["recent_outputs"] = []
                inst["total_output_files"] = 0

        combined = v2_list + legacy_list
        rank_map = {
            "ONLINE_LIVE": 0,
            "DRAINING": 1,
            "ONLINE_DRIVE_ONLY": 2,
            "DEGRADED_STALE": 3,
            "SHUTTING_DOWN": 4,
            "OFFLINE": 5,
        }
        combined.sort(
            key=lambda x: (
                rank_map.get(x.get("liveness_state") or "OFFLINE", 9),
                0 if not x.get("is_legacy") else 1,
                -(parse_iso_ts(x.get("heartbeat_at")) or 0.0),
                x.get("instance_id") or "",
            )
        )
        self._cached_instances = {inst["instance_id"]: inst for inst in combined}
        return combined

    def get_instance(self, instance_id: str, *, refresh: bool = True, output_limit: int = 12) -> dict[str, Any] | None:
        if refresh or instance_id not in self._cached_instances:
            self.discover_all(output_limit=output_limit)
        return self._cached_instances.get(instance_id)

    def select_default_instance(
        self, instances: list[dict[str, Any]], requested_id: str | None = None
    ) -> dict[str, Any] | None:
        """Deterministic v1 default instance selection (§6.1)."""
        if not instances:
            return None
        if requested_id:
            hit = next((i for i in instances if i["instance_id"] == requested_id), None)
            if hit:
                return hit
        if self.primary_ep:
            prim = str(self.primary_ep.resolve())
            hit = next((i for i in instances if i.get("legacy_ep_path") == prim or i.get("source_path") == prim), None)
            if hit:
                return hit
        return instances[0]
