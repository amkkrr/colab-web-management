"""Plan A Notebook Launcher: local + OAuth2 Drive v3 discovery, URL bindings, and safe injection."""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from control.drive_api import COLAB_NOTEBOOK_MIME, validate_drive_file_id
from control.injector import (
    backup_notebook_bytes,
    detect_notebook_profile,
    inject_notebook_dict,
    remove_injection_dict,
    sync_agent_to_drive,
)
from control.protocol import DEFAULT_DRIVE_MONITOR_REL, assert_not_in_cloud_drive, atomic_write_json, utc_now_iso

_BACKUP_OR_CONFLICT_RE = re.compile(r"(_backup|backup_|\(\d+\)|\.bak)$", re.I)
_ALLOWED_COLAB_HOSTS = frozenset({"colab.research.google.com", "drive.google.com"})
DRIVE_URI_PREFIX = "gdrive://"
_MAX_DRIVE_INSPECT_BYTES = 10 * 1024 * 1024
_MAX_DRIVE_FRESH_INSPECT = 25


def auto_discover_notebook_roots(monitor_root: Path | None = None, extra_roots: list[Path] | None = None) -> list[Path]:
    """Find Google Drive notebook folders beside monitor_root and in MyDrive/Colab Notebooks."""
    roots: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        rp = p.resolve()
        if rp.is_dir() and rp not in seen:
            seen.add(rp)
            roots.append(rp)

    for r in extra_roots or []:
        _add(r)

    if monitor_root:
        workspace_dir = monitor_root.resolve().parent
        if workspace_dir.is_dir():
            _add(workspace_dir / "Colab Notebooks")
            if workspace_dir.parent.is_dir():
                _add(workspace_dir.parent / "Colab Notebooks")
            # Include any 1st-level project folders containing .ipynb files (or a colab/ subfolder)
            try:
                for child in workspace_dir.iterdir():
                    if child.is_dir() and not child.name.startswith((".", "_")):
                        if any(child.glob("*.ipynb")):
                            _add(child)
                        if (child / "colab").is_dir() and any((child / "colab").glob("*.ipynb")):
                            _add(child / "colab")
            except OSError:
                pass
    return roots


def validate_colab_url(url: str) -> str:
    """Validate bound Colab / Drive URL strictly (https only, allowed Google hosts only)."""
    clean = (url or "").strip()
    if not clean:
        return ""
    parsed = urlparse(clean)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_COLAB_HOSTS:
        raise ValueError(
            "仅允许绑定 https://colab.research.google.com/... 或 https://drive.google.com/... 链接。"
        )
    return clean


class NotebookLauncher:
    """Manages local & OAuth2 Drive v3 notebook discovery, safe URL bindings, and launch (Plan A)."""

    def __init__(
        self,
        *,
        roots: list[Path],
        state_dir: Path,
        drive_client: Any | None = None,
        drive_folder_paths: list[str] | None = None,
    ) -> None:
        self.roots = [r.resolve() for r in roots if r.is_dir()]
        self.state_dir = assert_not_in_cloud_drive(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state_dir / "notebooks_launcher.json"
        self.backup_dir = self.state_dir / "notebook_backups"
        self.drive_client = drive_client
        self.drive_folder_paths = [p.strip() for p in (drive_folder_paths or []) if p and p.strip()]
        self.last_drive_error: str | None = None
        self._drive_profile_cache: dict[str, tuple[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def parse_drive_notebook_id(self, raw_path: str) -> str | None:
        """Return validated Drive file ID when raw_path uses gdrive://<id>, else None."""
        text = str(raw_path or "").strip()
        if not text.startswith(DRIVE_URI_PREFIX):
            return None
        return validate_drive_file_id(text[len(DRIVE_URI_PREFIX) :])

    def _load_bindings_locked(self) -> dict[str, dict[str, Any]]:
        if not self.config_path.is_file():
            return {}
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("bindings"), dict):
                return dict(raw["bindings"])
        except Exception:
            pass
        return {}

    def _save_bindings_locked(self, bindings: dict[str, dict[str, Any]]) -> None:
        atomic_write_json(
            self.config_path,
            {"version": 1, "updated_at": utc_now_iso(), "bindings": bindings},
        )

    def resolve_allowed_notebook(self, raw_path: str) -> Path:
        """Verify requested notebook path is a real .ipynb inside one of the allowed roots."""
        if not raw_path or not isinstance(raw_path, str):
            raise ValueError("Missing notebook path")
        target = Path(raw_path).expanduser().resolve()
        if target.suffix != ".ipynb" or not target.is_file():
            raise FileNotFoundError(f"Notebook not found: {raw_path}")
        if not any(target.is_relative_to(root) for root in self.roots):
            raise PermissionError("Notebook path escapes allowed notebook roots")
        return target

    def _inspect_drive_profile(self, file_id: str, mtime: str, size_bytes: int, allow_fetch: bool) -> dict[str, Any]:
        fallback = {
            "injected": False,
            "injected_version": None,
            "detected_mode": "standalone_mount",
            "has_fastapi": False,
            "total_cells": 0,
        }
        with self._lock:
            cached = self._drive_profile_cache.get(file_id)
        if cached and cached[0] == mtime:
            return dict(cached[1])
        if not allow_fetch or size_bytes > _MAX_DRIVE_INSPECT_BYTES:
            return fallback
        try:
            raw = self.drive_client.get_bytes(file_id)
            nb = json.loads(raw.decode("utf-8"))
            profile = detect_notebook_profile(nb)
        except Exception:
            return fallback
        clean = {
            "injected": bool(profile.get("injected")),
            "injected_version": profile.get("injected_version"),
            "detected_mode": str(profile.get("detected_mode") or "standalone_mount"),
            "has_fastapi": bool(profile.get("has_fastapi")),
            "total_cells": int(profile.get("total_cells") or 0),
        }
        if mtime:
            with self._lock:
                self._drive_profile_cache[file_id] = (mtime, dict(clean))
        return clean

    def _fetch_drive_notebook(self, file_id: str) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
        if self.drive_client is None:
            raise PermissionError("未配置 OAuth2 Google Drive 凭据，无法读写云端笔记本。")
        safe_id = validate_drive_file_id(file_id)
        meta = self.drive_client.get_file_metadata(safe_id)
        if meta.get("trashed"):
            raise FileNotFoundError(f"Drive notebook is trashed: {safe_id}")
        name = str(meta.get("name") or "").strip()
        mime = str(meta.get("mimeType") or "").strip()
        if not name or not (name.lower().endswith(".ipynb") or mime == COLAB_NOTEBOOK_MIME):
            raise PermissionError("Target Drive file is not a Colab/Jupyter notebook")
        raw_bytes = self.drive_client.get_bytes(safe_id)
        nb = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
            raise ValueError("Invalid .ipynb JSON structure")
        return meta, raw_bytes, nb

    def list_notebooks(self, *, include_backups: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            bindings = self._load_bindings_locked()

        items: list[dict[str, Any]] = []
        seen: set[Path] = set()
        for root in self.roots:
            try:
                candidates = sorted(root.rglob("*.ipynb"), key=lambda p: p.stat().st_mtime, reverse=True)
            except OSError:
                continue
            for fp in candidates:
                rp = fp.resolve()
                if rp in seen or fp.name.startswith("._tmp_"):
                    continue
                seen.add(rp)
                is_backup = bool(_BACKUP_OR_CONFLICT_RE.search(fp.stem))
                if is_backup and not include_backups:
                    continue
                try:
                    stat = rp.stat()
                    nb = json.loads(rp.read_text(encoding="utf-8"))
                    profile = detect_notebook_profile(nb)
                except Exception:
                    continue
                key = str(rp)
                bind = bindings.get(key) or {}
                try:
                    rel_display = f"{root.name}/{rp.relative_to(root).as_posix()}"
                except ValueError:
                    rel_display = fp.name
                items.append(
                    {
                        "path": key,
                        "source": "local",
                        "file_id": None,
                        "name": fp.name,
                        "stem": fp.stem,
                        "display_path": rel_display,
                        "is_backup": is_backup,
                        "size_kib": round(stat.st_size / 1024.0, 1),
                        "modified_at": utc_now_iso(stat.st_mtime),
                        "injected": profile["injected"],
                        "injected_version": profile["injected_version"],
                        "detected_mode": profile["detected_mode"],
                        "has_fastapi": profile["has_fastapi"],
                        "total_cells": profile["total_cells"],
                        "colab_url": bind.get("colab_url") or "",
                    }
                )

        if self.drive_client is not None:
            try:
                remote_files = self.drive_client.list_notebooks(folder_paths=self.drive_folder_paths)
                self.last_drive_error = None
            except Exception as exc:
                remote_files = []
                self.last_drive_error = str(exc)

            fresh_fetches = 0
            for rf in remote_files:
                fid = str(rf.get("id") or "")
                name = str(rf.get("name") or "")
                stem = Path(name).stem
                is_backup = bool(_BACKUP_OR_CONFLICT_RE.search(stem))
                if is_backup and not include_backups:
                    continue
                mtime = str(rf.get("modifiedTime") or "")
                try:
                    size_bytes = max(0, int(rf.get("size") or 0))
                except (TypeError, ValueError):
                    size_bytes = 0
                with self._lock:
                    cached_hit = fid in self._drive_profile_cache and self._drive_profile_cache[fid][0] == mtime
                allow_fetch = cached_hit or fresh_fetches < _MAX_DRIVE_FRESH_INSPECT
                if not cached_hit and allow_fetch and size_bytes <= _MAX_DRIVE_INSPECT_BYTES:
                    fresh_fetches += 1
                profile = self._inspect_drive_profile(fid, mtime, size_bytes, allow_fetch=allow_fetch)
                key = f"{DRIVE_URI_PREFIX}{fid}"
                bind = bindings.get(key) or {}
                items.append(
                    {
                        "path": key,
                        "source": "drive_api",
                        "file_id": fid,
                        "name": name,
                        "stem": stem,
                        "display_path": str(rf.get("display_path") or name),
                        "is_backup": is_backup,
                        "size_kib": round(size_bytes / 1024.0, 1),
                        "modified_at": mtime or utc_now_iso(),
                        "injected": profile["injected"],
                        "injected_version": profile["injected_version"],
                        "detected_mode": profile["detected_mode"],
                        "has_fastapi": profile["has_fastapi"],
                        "total_cells": profile["total_cells"],
                        "colab_url": bind.get("colab_url") or f"https://colab.research.google.com/drive/{fid}",
                    }
                )
        else:
            self.last_drive_error = None

        items.sort(key=lambda x: (0 if x["injected"] else 1, 0 if x["colab_url"] else 1, x["name"].lower()))
        return items

    def bind_url(self, raw_path: str, colab_url: str) -> dict[str, Any]:
        fid = self.parse_drive_notebook_id(raw_path)
        key = f"{DRIVE_URI_PREFIX}{fid}" if fid else str(self.resolve_allowed_notebook(raw_path))
        valid_url = validate_colab_url(colab_url)
        with self._lock:
            bindings = self._load_bindings_locked()
            if valid_url:
                bindings[key] = {"colab_url": valid_url, "updated_at": utc_now_iso()}
            else:
                bindings.pop(key, None)
            self._save_bindings_locked(bindings)
        return {"ok": True, "path": key, "colab_url": valid_url}

    def launch(self, raw_path: str, *, open_browser: bool = True) -> dict[str, Any]:
        fid = self.parse_drive_notebook_id(raw_path)
        if fid:
            key = f"{DRIVE_URI_PREFIX}{fid}"
            default_url = f"https://colab.research.google.com/drive/{fid}"
            fname = key
        else:
            target = self.resolve_allowed_notebook(raw_path)
            key = str(target)
            default_url = ""
            fname = target.name

        with self._lock:
            bindings = self._load_bindings_locked()
        colab_url = (bindings.get(key) or {}).get("colab_url") or default_url
        if colab_url:
            url = validate_colab_url(colab_url)
            mode = "colab_direct"
            message = "已在默认浏览器打开 Colab 笔记本，页面加载后按 ⌘+F9（Windows 为 Ctrl+F9）全部运行即可自动接入管控台。"
        else:
            url = f"https://drive.google.com/drive/search?q={quote(fname)}"
            mode = "drive_search"
            message = f"该笔记本尚未绑定直达链接，已打开 Google Drive 搜索页定位 {fname}；打开 Colab 后可复制链接粘贴到「绑定链接」永久一键直达。"
        if open_browser:
            webbrowser.open(url)
        return {"ok": True, "path": key, "mode": mode, "launched_url": url, "message": message}

    def inject_drive_notebook(
        self,
        file_id: str,
        *,
        monitor_root: Path,
        agent_src_path: Path,
        dry_run: bool = True,
        project: str | None = None,
        label: str | None = None,
        idle_timeout_minutes: int = 60,
    ) -> dict[str, Any]:
        """Preview or idempotently inject ColabAgent into a Google Drive notebook via Drive v3 API."""
        meta, raw_bytes, nb = self._fetch_drive_notebook(file_id)
        safe_id = str(meta.get("id") or file_id)
        name = str(meta["name"])
        mime = str(meta.get("mimeType") or COLAB_NOTEBOOK_MIME)
        monitor_rel = self.drive_client.config.monitor_folder_path or DEFAULT_DRIVE_MONITOR_REL

        result, updated_nb = inject_notebook_dict(
            nb,
            nb_name=name,
            path_label=f"{DRIVE_URI_PREFIX}{safe_id}",
            monitor_drive_rel=monitor_rel,
            dry_run=dry_run,
            project=project,
            label=label,
            idle_timeout_minutes=idle_timeout_minutes,
        )
        if dry_run:
            return result

        backup_path = backup_notebook_bytes(name, raw_bytes, self.backup_dir)
        agent_src = agent_src_path.resolve()
        if not agent_src.is_file():
            raise FileNotFoundError(f"Missing colab_agent.py source: {agent_src}")
        self.drive_client.put_bytes("colab_agent.py", agent_src.read_bytes(), mime_type="text/x-python")
        synced_local = sync_agent_to_drive(agent_src, monitor_root)
        encoded = (json.dumps(updated_nb, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        self.drive_client.update_file_bytes(safe_id, encoded, mime_type=mime)
        with self._lock:
            self._drive_profile_cache.pop(safe_id, None)
        result["backup_path"] = str(backup_path)
        result["agent_synced_path"] = f"gdrive://{monitor_rel}/colab_agent.py ({synced_local})"
        return result

    def remove_drive_injection(self, file_id: str, *, dry_run: bool = True) -> dict[str, Any]:
        """Preview or cleanly remove Colab Monitor injected cells from a Google Drive notebook."""
        meta, raw_bytes, nb = self._fetch_drive_notebook(file_id)
        safe_id = str(meta.get("id") or file_id)
        name = str(meta["name"])
        mime = str(meta.get("mimeType") or COLAB_NOTEBOOK_MIME)

        result, updated_nb = remove_injection_dict(
            nb,
            path_label=f"{DRIVE_URI_PREFIX}{safe_id}",
            dry_run=dry_run,
        )
        if dry_run or result["removed_count"] == 0:
            return result

        backup_path = backup_notebook_bytes(name, raw_bytes, self.backup_dir)
        encoded = (json.dumps(updated_nb, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        self.drive_client.update_file_bytes(safe_id, encoded, mime_type=mime)
        with self._lock:
            self._drive_profile_cache.pop(safe_id, None)
        result["backup_path"] = str(backup_path)
        return result
