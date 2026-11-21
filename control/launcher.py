"""Plan A Notebook Launcher: discovery, local Colab URL bindings, and safe one-click browser launch."""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from control.injector import detect_notebook_profile
from control.protocol import assert_not_in_cloud_drive, atomic_write_json, utc_now_iso

_BACKUP_OR_CONFLICT_RE = re.compile(r"(_backup|backup_|\(\d+\)|\.bak)$", re.I)
_ALLOWED_COLAB_HOSTS = frozenset({"colab.research.google.com", "drive.google.com"})


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
    """Manages local notebook discovery, safe URL bindings, and one-click browser launch (Plan A)."""

    def __init__(
        self,
        *,
        roots: list[Path],
        state_dir: Path,
    ) -> None:
        self.roots = [r.resolve() for r in roots if r.is_dir()]
        self.state_dir = assert_not_in_cloud_drive(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state_dir / "notebooks_launcher.json"
        self.backup_dir = self.state_dir / "notebook_backups"
        self._lock = threading.Lock()

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
        items.sort(key=lambda x: (0 if x["injected"] else 1, 0 if x["colab_url"] else 1, x["name"].lower()))
        return items

    def bind_url(self, raw_path: str, colab_url: str) -> dict[str, Any]:
        target = self.resolve_allowed_notebook(raw_path)
        valid_url = validate_colab_url(colab_url)
        key = str(target)
        with self._lock:
            bindings = self._load_bindings_locked()
            if valid_url:
                bindings[key] = {"colab_url": valid_url, "updated_at": utc_now_iso()}
            else:
                bindings.pop(key, None)
            self._save_bindings_locked(bindings)
        return {"ok": True, "path": key, "colab_url": valid_url}

    def launch(self, raw_path: str, *, open_browser: bool = True) -> dict[str, Any]:
        target = self.resolve_allowed_notebook(raw_path)
        key = str(target)
        with self._lock:
            bindings = self._load_bindings_locked()
        colab_url = (bindings.get(key) or {}).get("colab_url") or ""
        if colab_url:
            url = validate_colab_url(colab_url)
            mode = "colab_direct"
            message = f"已在默认浏览器打开 Colab 笔记本，页面加载后按 ⌘+F9（Windows 为 Ctrl+F9）全部运行即可自动接入管控台。"
        else:
            url = f"https://drive.google.com/drive/search?q={quote(target.name)}"
            mode = "drive_search"
            message = f"该笔记本尚未绑定直达链接，已打开 Google Drive 搜索页定位 {target.name}；打开 Colab 后可复制链接粘贴到「绑定链接」永久一键直达。"
        if open_browser:
            webbrowser.open(url)
        return {"ok": True, "path": key, "mode": mode, "launched_url": url, "message": message}
