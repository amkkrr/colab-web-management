"""Safe, idempotent, reversible Jupyter (.ipynb) injector for ColabAgent v2.0."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from control.protocol import DEFAULT_DRIVE_MONITOR_REL, assert_not_in_cloud_drive, utc_now_iso

INJECT_META_KEY = "colab_monitor_injected"
INJECT_VERSION = "2.0"
INJECT_CELL_ID = "colab-monitor-v2-agent"


def _infer_drive_rel_path(monitor_root: Path) -> str:
    """Dynamically infer Google Drive relative path from a mounted CloudStorage monitor_root."""
    parts = monitor_root.resolve().parts
    for idx, part in enumerate(parts):
        if part.lower().startswith("googledrive-") and idx + 2 < len(parts):
            # parts[idx] = GoogleDrive-<email>, parts[idx+1] = My Drive / localized name
            rel = "/".join(parts[idx + 2 :])
            if rel:
                return rel
    return DEFAULT_DRIVE_MONITOR_REL


def _sanitize_label(raw: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(raw or "")).strip()
    return cleaned[:80] or "colab_instance"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f"._tmp_{os.getpid()}_{threading.get_ident()}_{target.name}"
    written = False
    try:
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(3):
            try:
                os.replace(tmp, target)
                written = True
                break
            except OSError:
                if attempt < 2:
                    time.sleep(0.02)
        if not written:
            target.write_text(text, encoding="utf-8")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def sync_agent_to_drive(agent_src_path: Path, monitor_root: Path) -> Path:
    """Sync latest single-file agent/colab_agent.py to _colab_monitor/colab_agent.py if changed."""
    src = agent_src_path.resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Missing colab_agent.py source: {src}")
    dst = monitor_root.resolve() / "colab_agent.py"
    src_bytes = src.read_bytes()
    if dst.is_file():
        try:
            if _sha256_bytes(dst.read_bytes()) == _sha256_bytes(src_bytes):
                return dst
        except OSError:
            pass
    _atomic_write_text(dst, src_bytes.decode("utf-8"))
    return dst


def _cell_source_text(cell: dict[str, Any]) -> str:
    src = cell.get("source")
    if isinstance(src, list):
        return "".join(str(x) for x in src)
    return str(src or "")


def detect_notebook_profile(nb: dict[str, Any]) -> dict[str, Any]:
    """Inspect .ipynb structure to determine Drive mode, injection status, and optimal insert index."""
    cells = nb.get("cells")
    if not isinstance(cells, list):
        raise ValueError("Invalid .ipynb: missing 'cells' list")

    injected_indices: list[int] = []
    injected_meta: dict[str, Any] | None = None
    oauth_cell_idx: int | None = None
    fuse_cell_idx: int | None = None
    has_fastapi = False

    for idx, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        meta = cell.get("metadata") or {}
        if isinstance(meta, dict) and INJECT_META_KEY in meta:
            injected_indices.append(idx)
            if injected_meta is None and isinstance(meta[INJECT_META_KEY], dict):
                injected_meta = dict(meta[INJECT_META_KEY])
            continue
        if cell.get("cell_type") != "code":
            continue
        txt = _cell_source_text(cell)
        if "DriveSyncManager" in txt and ("GDRIVE =" in txt or "GDRIVE=" in txt):
            oauth_cell_idx = idx
        elif "drive.mount" in txt and fuse_cell_idx is None:
            fuse_cell_idx = idx
        if "FastAPI(" in txt or '@app.get("/health")' in txt:
            has_fastapi = True

    if oauth_cell_idx is not None:
        mode = "oauth_silent"
        insert_index = oauth_cell_idx + 1
    elif fuse_cell_idx is not None:
        mode = "fuse_mounted"
        insert_index = fuse_cell_idx + 1
    else:
        mode = "standalone_mount"
        # Place after leading markdown title cells if any
        insert_index = 0
        while insert_index < len(cells) and isinstance(cells[insert_index], dict) and cells[insert_index].get("cell_type") == "markdown":
            insert_index += 1

    return {
        "injected": bool(injected_indices),
        "injected_indices": injected_indices,
        "injected_version": injected_meta.get("version") if injected_meta else None,
        "injected_mode": injected_meta.get("mode") if injected_meta else None,
        "detected_mode": mode,
        "has_fastapi": has_fastapi,
        "insert_index": insert_index,
        "total_cells": len(cells),
    }


def _slugify_project(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return (slug[:48] or "colab_project")


def generate_injected_cell(
    nb_path: Path,
    profile: dict[str, Any],
    *,
    project: str | None = None,
    label: str | None = None,
    idle_timeout_minutes: int = 60,
    monitor_drive_rel: str = DEFAULT_DRIVE_MONITOR_REL,
) -> dict[str, Any]:
    """Generate the self-contained, marked Jupyter code cell for ColabAgent v2.0."""
    stem = nb_path.stem
    proj = _slugify_project(project or stem)
    lbl = _sanitize_label(label or stem)
    mode = profile["detected_mode"]
    timeout_min = max(0, min(360, int(idle_timeout_minutes)))
    nb_name = _sanitize_label(nb_path.name)

    if mode == "oauth_silent":
        lines = [
            f"# @title 📡 Colab Monitor v2.0 探针（自动注入 · 静默 OAuth 模式，请勿手动修改标记）\n",
            "# 本单元由本地管控台自动注入；支持一键升级或卸载，不改动原业务单元。\n",
            "import importlib.util, io, pathlib, sys\n",
            "\n",
            "def _load_colab_agent_via_oauth(drive_svc, folder_path):\n",
            "    cur = 'root'\n",
            "    for part in [p for p in folder_path.strip('/').split('/') if p]:\n",
            "        safe = part.replace(\"'\", \"\\\\'\")\n",
            "        q = f\"name = '{safe}' and '{cur}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false\"\n",
            "        res = drive_svc.files().list(q=q, fields='files(id)', pageSize=1).execute().get('files') or []\n",
            "        if not res:\n",
            "            raise FileNotFoundError(f'云盘未找到目录: {folder_path}')\n",
            "        cur = res[0]['id']\n",
            "    q = f\"name = 'colab_agent.py' and '{cur}' in parents and trashed = false\"\n",
            "    files = drive_svc.files().list(q=q, fields='files(id)', orderBy='modifiedTime desc', pageSize=1).execute().get('files') or []\n",
            "    if not files:\n",
            "        raise FileNotFoundError('云盘 _colab_monitor/colab_agent.py 尚未同步，请在本地重新点击注入或启动服务端')\n",
            "    raw = drive_svc.files().get_media(fileId=files[0]['id']).execute()\n",
            "    dst = pathlib.Path('/content/colab_agent.py')\n",
            "    dst.write_bytes(raw if isinstance(raw, bytes) else str(raw).encode('utf-8'))\n",
            "    spec = importlib.util.spec_from_file_location('colab_agent', dst)\n",
            "    mod = importlib.util.module_from_spec(spec)\n",
            "    spec.loader.exec_module(mod)\n",
            "    return mod\n",
            "\n",
            "try:\n",
            f"    _cm_mod = _load_colab_agent_via_oauth(GDRIVE._service(), {monitor_drive_rel!r})\n",
            f"    _cm_backend = _cm_mod.OAuthDriveBackend(service_factory=GDRIVE._service, monitor_folder_path={monitor_drive_rel!r})\n",
            "    COLAB_AGENT = _cm_mod.ColabAgent(\n",
            f"        project={proj!r},\n",
            f"        label={lbl!r},\n",
            f"        notebook_name={nb_name!r},\n",
            "        drive_backend=_cm_backend,\n",
            "        run_mode='online_gateway' if 'API_KEY' in globals() else 'offline_batch',\n",
            "        api_key=globals().get('API_KEY') or None,\n",
            f"        idle_timeout_minutes={timeout_min},\n",
            "    )\n",
            "    COLAB_AGENT.start_background_workers()\n",
            "    print(f'[Colab Monitor v2] 已上线 (OAuth): {COLAB_AGENT.instance_id} | 可选在循环中调用 COLAB_AGENT.checkpoint()')\n",
            "except Exception as _cm_err:\n",
            "    print(f'[Colab Monitor v2] 探针初始化跳过（不影响原业务运行）: {_cm_err}')\n",
        ]
    else:
        mount_snippet = (
            [
                "from google.colab import drive\n",
                "import os\n",
                "if not os.path.isdir('/content/drive/MyDrive'):\n",
                "    drive.mount('/content/drive')\n",
                "\n",
            ]
            if mode == "standalone_mount"
            else []
        )
        lines = [
            f"# @title 📡 Colab Monitor v2.0 探针（自动注入 · FUSE 模式，请勿手动修改标记）\n",
            "# 本单元由本地管控台自动注入；支持一键升级或卸载，不改动原业务单元。\n",
            "import importlib.util, pathlib\n",
            "\n",
            *mount_snippet,
            "try:\n",
            f"    _cm_root = pathlib.Path('/content/drive/MyDrive') / {monitor_drive_rel!r}\n",
            "    _cm_file = _cm_root / 'colab_agent.py'\n",
            "    if not _cm_file.is_file():\n",
            "        raise FileNotFoundError(f'未找到 {_cm_file}，请确认 Google Drive 已同步')\n",
            "    _cm_spec = importlib.util.spec_from_file_location('colab_agent', _cm_file)\n",
            "    _cm_mod = importlib.util.module_from_spec(_cm_spec)\n",
            "    _cm_spec.loader.exec_module(_cm_mod)\n",
            "    COLAB_AGENT = _cm_mod.ColabAgent(\n",
            f"        project={proj!r},\n",
            f"        label={lbl!r},\n",
            f"        notebook_name={nb_name!r},\n",
            "        drive_backend=_cm_mod.FuseDriveBackend(_cm_root),\n",
            "        run_mode='offline_batch',\n",
            f"        idle_timeout_minutes={timeout_min},\n",
            "    )\n",
            "    COLAB_AGENT.start_background_workers()\n",
            "    print(f'[Colab Monitor v2] 已上线 (FUSE): {COLAB_AGENT.instance_id} | 可选在循环中调用 COLAB_AGENT.checkpoint()')\n",
            "except Exception as _cm_err:\n",
            "    print(f'[Colab Monitor v2] 探针初始化跳过（不影响原业务运行）: {_cm_err}')\n",
        ]

    return {
        "cell_type": "code",
        "execution_count": None,
        "id": INJECT_CELL_ID,
        "metadata": {
            "cellView": "form",
            INJECT_META_KEY: {
                "version": INJECT_VERSION,
                "mode": mode,
                "project": proj,
                "label": lbl,
                "injected_at": utc_now_iso(),
            },
        },
        "outputs": [],
        "source": lines,
    }


def _backup_notebook(nb_path: Path, backup_dir: Path) -> Path:
    safe_backup_dir = assert_not_in_cloud_drive(backup_dir)
    safe_backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = safe_backup_dir / f"{nb_path.stem}.{stamp}.bak.ipynb"
    shutil.copy2(nb_path, dst)
    return dst


def inject_notebook(
    nb_path: Path,
    *,
    backup_dir: Path,
    monitor_root: Path,
    agent_src_path: Path,
    dry_run: bool = True,
    project: str | None = None,
    label: str | None = None,
    idle_timeout_minutes: int = 60,
) -> dict[str, Any]:
    """Preview (dry_run=True) or idempotently inject/upgrade ColabAgent cell in an .ipynb file."""
    resolved = nb_path.resolve()
    if not resolved.is_file() or resolved.suffix != ".ipynb":
        raise FileNotFoundError(f"Notebook not found: {resolved}")

    nb = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise ValueError("Invalid .ipynb JSON structure")

    profile = detect_notebook_profile(nb)
    monitor_rel = _infer_drive_rel_path(monitor_root)
    new_cell = generate_injected_cell(
        resolved,
        profile,
        project=project,
        label=label,
        idle_timeout_minutes=idle_timeout_minutes,
        monitor_drive_rel=monitor_rel,
    )
    cells_before = len(nb["cells"])
    action = "update" if profile["injected"] else "insert"
    target_idx = profile["injected_indices"][0] if profile["injected"] else profile["insert_index"]
    cells_after = cells_before - max(0, len(profile["injected_indices"]) - 1) if profile["injected"] else cells_before + 1

    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "action": action,
        "path": str(resolved),
        "mode": profile["detected_mode"],
        "insert_index": target_idx,
        "cells_before": cells_before,
        "cells_after": cells_after,
        "version": INJECT_VERSION,
        "cell_source": "".join(new_cell["source"]),
        "backup_path": None,
        "agent_synced_path": None,
    }
    if dry_run:
        return result

    backup_path = _backup_notebook(resolved, backup_dir)
    synced_agent = sync_agent_to_drive(agent_src_path, monitor_root)

    if profile["injected"]:
        # Replace first injected cell in place and strip any accidental duplicates
        new_cells: list[Any] = []
        replaced = False
        for idx, c in enumerate(nb["cells"]):
            if idx in profile["injected_indices"]:
                if not replaced:
                    new_cells.append(new_cell)
                    replaced = True
                continue
            new_cells.append(c)
        nb["cells"] = new_cells
    else:
        nb["cells"].insert(target_idx, new_cell)

    _atomic_write_text(resolved, json.dumps(nb, ensure_ascii=False, indent=1) + "\n")
    result["backup_path"] = str(backup_path)
    result["agent_synced_path"] = str(synced_agent)
    return result


def remove_injection(
    nb_path: Path,
    *,
    backup_dir: Path,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Preview or cleanly remove all Colab Monitor injected cells from an .ipynb file."""
    resolved = nb_path.resolve()
    if not resolved.is_file() or resolved.suffix != ".ipynb":
        raise FileNotFoundError(f"Notebook not found: {resolved}")

    nb = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise ValueError("Invalid .ipynb JSON structure")

    profile = detect_notebook_profile(nb)
    removed_cnt = len(profile["injected_indices"])
    cells_before = len(nb["cells"])

    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "path": str(resolved),
        "removed_count": removed_cnt,
        "cells_before": cells_before,
        "cells_after": cells_before - removed_cnt,
        "backup_path": None,
    }
    if dry_run or removed_cnt == 0:
        return result

    backup_path = _backup_notebook(resolved, backup_dir)
    keep = [c for idx, c in enumerate(nb["cells"]) if idx not in profile["injected_indices"]]
    nb["cells"] = keep
    _atomic_write_text(resolved, json.dumps(nb, ensure_ascii=False, indent=1) + "\n")
    result["backup_path"] = str(backup_path)
    return result
