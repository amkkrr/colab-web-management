#!/usr/bin/env python3
"""
MiniMax-H3 Colab L4 资源与任务监控工具 (h3_cli_monitor.py)
- 零额外依赖（仅用 Python 标准库），跨 macOS / Linux / Windows 直接可用
- 自动读取同目录或 Google Drive 中的 active_endpoint.json 连接当前 Colab 隧道
- 支持单次查询、--watch 实时终端刷新、--json 原始输出，以及 --web 启动网页版 Dashboard
- 所有监控轮询均为只读 GET（/health 与 /v1/metrics），不会重置 Colab 空闲自动关机计时器
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _validate_upstream_url(url: str) -> str:
    """Allow https:// or loopback http:// only; blocks file:// and other non-HTTP schemes."""
    clean = (url or "").strip()
    parsed = urlparse(clean)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("Invalid upstream URL: missing hostname")
    if scheme == "https" or (scheme == "http" and host in _LOOPBACK_HOSTS):
        return clean
    raise ValueError(f"Disallowed upstream URL scheme/host: {scheme}://{host}")


def _auto_find_ep_file() -> Path:
    """Auto-discover active_endpoint.json in local workspace or mounted Google Drive folders."""
    env_ep = os.environ.get("COLAB_MONITOR_EP_FILE")
    if env_ep:
        cand = Path(env_ep).expanduser()
        if cand.exists():
            return cand
    local_same_dir = HERE / "active_endpoint.json"
    if (HERE / "outputs").is_dir() and local_same_dir.exists():
        return local_same_dir
    parent_ep = HERE.parent / "active_endpoint.json"
    if parent_ep.exists() or (HERE.parent / "outputs").is_dir():
        return parent_ep
    # Search sibling project directories beside this repo first
    if HERE.parent.is_dir():
        for cand in sorted(HERE.parent.glob("*/active_endpoint.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if cand.is_file():
                return cand
    # Fallback: scan mounted Google Drive CloudStorage roots (depth 1-2)
    cloud_root = Path.home() / "Library" / "CloudStorage"
    if cloud_root.exists():
        matches: list[Path] = []
        for pattern in (
            "GoogleDrive-*/*/*/active_endpoint.json",
            "GoogleDrive-*/*/*/*/active_endpoint.json",
        ):
            matches.extend(p for p in cloud_root.glob(pattern) if p.is_file())
        if matches:
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return matches[0]
    return local_same_dir


DEFAULT_EP_FILE = _auto_find_ep_file()
_SHUTDOWN_STATE_FILE = Path(tempfile.gettempdir()) / "minimax_h3_shutdown_state.json"
_SHUTDOWN_MARKERS: dict[str, float] = {}


def _load_shutdown_markers() -> dict[str, float]:
    """Merge in-memory and temp-file shutdown markers (stores tunnel URL -> epoch only)."""
    merged = dict(_SHUTDOWN_MARKERS)
    try:
        if _SHUTDOWN_STATE_FILE.is_file():
            raw = json.loads(_SHUTDOWN_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, (int, float)):
                        merged[k] = max(merged.get(k, 0.0), float(v))
    except Exception:
        pass
    return merged


def _save_shutdown_markers(markers: dict[str, float]) -> None:
    try:
        _SHUTDOWN_STATE_FILE.write_text(json.dumps(markers, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _record_shutdown_marker(base_url: str) -> None:
    now = time.time()
    _SHUTDOWN_MARKERS[base_url] = now
    markers = _load_shutdown_markers()
    markers[base_url] = now
    _save_shutdown_markers(markers)


def _get_shutdown_marker_age(base_url: str) -> float | None:
    ts = _load_shutdown_markers().get(base_url)
    if ts is None:
        return None
    return max(0.0, time.time() - ts)


def _clear_shutdown_marker(base_url: str) -> None:
    _SHUTDOWN_MARKERS.pop(base_url, None)
    markers = _load_shutdown_markers()
    if base_url in markers:
        markers.pop(base_url, None)
        _save_shutdown_markers(markers)


def _http_request_json(
    url: str,
    method: str = "GET",
    api_key: str | None = None,
    payload: dict | None = None,
    timeout: float = 10.0,
) -> dict:
    """Send an HTTP JSON request and parse the JSON response (https:// or loopback http:// only)."""
    safe_url = _validate_upstream_url(url)
    headers = {"User-Agent": "MiniMax-H3-CLI-Monitor/1.1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(safe_url, data=data, headers=headers, method=method)
    if safe_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:  # nosec B310 - scheme/host strictly validated
            return json.loads(resp.read().decode("utf-8"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - https scheme strictly validated
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, api_key: str | None = None, timeout: float = 10.0) -> dict:
    return _http_request_json(url, method="GET", api_key=api_key, timeout=timeout)


def collect_local_drive_stats(out_dir: Path, limit: int = 6, include_details: bool = False) -> list[dict]:
    """读取 outputs/h3_*.json 本地归档元数据。limit <= 0 时返回全部记录。"""
    if not out_dir.exists():
        return []
    jsons = sorted(out_dir.glob("h3_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    total_available = len(jsons)
    if limit > 0:
        jsons = jsons[:limit]
    records = []
    for jp in jsons:
        try:
            d = json.loads(jp.read_text(encoding="utf-8"))
            w, h = d.get("width", 0), d.get("height", 0)
            png_name = jp.stem + ".png"
            created_full = d.get("created_at") or ""
            created_short = created_full[11:19] if len(created_full) >= 19 and "T" in created_full else created_full[-8:]
            rec = {
                "file": png_name,
                "mode": d.get("mode", "-"),
                "size": f"{w}x{h}" if w and h else "—",
                "mp": round((w * h) / 1e6, 2) if w and h else 0,
                "refs": d.get("num_refs", 0),
                "elapsed_sec": d.get("elapsed_sec"),
                "created_at": created_short,
            }
            if include_details:
                rec.update(
                    {
                        "width": w,
                        "height": h,
                        "dit_tier": d.get("dit_tier"),
                        "seed": d.get("seed"),
                        "prompt": d.get("prompt") or "",
                        "created_at_full": created_full,
                        "local_path": d.get("local_path"),
                        "drive_url": d.get("drive_url"),
                        "has_image": (out_dir / png_name).is_file(),
                        "total_available": total_available,
                    }
                )
            records.append(rec)
        except Exception:
            continue
    return records


def fetch_snapshot(
    ep_path: Path,
    output_limit: int = 6,
    include_details: bool = False,
    timeout: float = 5.0,
) -> dict:
    """采集当前端点状态、/health、/v1/metrics 及本地 outputs/*.json。绝不返回 api_key 原文。"""
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_dir = ep_path.parent / "outputs"
    recent_outputs = collect_local_drive_stats(out_dir, limit=output_limit, include_details=include_details)
    total_output_files = len(list(out_dir.glob("h3_*.json"))) if out_dir.exists() else 0

    if not ep_path.exists():
        return {
            "online": False,
            "offline_reason": "missing_config",
            "fetched_at": fetched_at,
            "ep_path": str(ep_path),
            "ep_exists": False,
            "has_api_key": False,
            "error": f"未找到 {ep_path}，请确认 Colab 已运行 Step 6 且云盘已同步。",
            "health": None,
            "metrics": None,
            "recent_outputs": recent_outputs,
            "total_output_files": total_output_files,
        }

    try:
        ep = json.loads(ep_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {
            "online": False,
            "offline_reason": "invalid_config",
            "fetched_at": fetched_at,
            "ep_path": str(ep_path),
            "ep_exists": True,
            "has_api_key": False,
            "error": f"解析 {ep_path.name} 失败: {e}",
            "health": None,
            "metrics": None,
            "recent_outputs": recent_outputs,
            "total_output_files": total_output_files,
        }

    base_url = (ep.get("base_url") or "").rstrip("/")
    api_key = (ep.get("api_key") or "").strip()

    snap: dict = {
        "online": False,
        "fetched_at": fetched_at,
        "ep_path": str(ep_path),
        "ep_exists": True,
        "base_url": base_url,
        "has_api_key": bool(api_key),
        "endpoint_updated_at": ep.get("updated_at"),
        "endpoint_meta": {
            "dit_tier": ep.get("dit_tier"),
            "default_size": ep.get("default_size"),
            "idle_timeout_minutes": ep.get("idle_timeout_minutes"),
        },
        "health": None,
        "metrics": None,
        "recent_outputs": recent_outputs,
        "total_output_files": total_output_files,
    }

    if not base_url:
        snap["offline_reason"] = "invalid_config"
        snap["error"] = f"{ep_path.name} 缺少有效的 base_url 字段。"
        return snap

    try:
        snap["health"] = _http_get_json(f"{base_url}/health", api_key=None, timeout=timeout)
        snap["online"] = True
        marker_age = _get_shutdown_marker_age(base_url)
        if marker_age is not None:
            if marker_age < 45.0:
                snap["shutdown_pending"] = True
            else:
                _clear_shutdown_marker(base_url)
    except Exception as e:
        marker_age = _get_shutdown_marker_age(base_url)
        if marker_age is not None:
            snap["offline_reason"] = "shutdown_confirmed"
            snap["error"] = f"Colab 实例已按指令关机并释放算力（隧道 {base_url} 已断开）。如需继续使用，请在 Colab 重新运行 Step 1–6。"
        elif isinstance(e, urllib.error.HTTPError) and e.code == 530:
            snap["offline_reason"] = "tunnel_closed"
            snap["error"] = "Colab 实例已释放或已关机（Cloudflare 隧道已关闭，HTTP 530）。如需继续使用，请在 Colab 重新运行 Step 1–6。"
        else:
            snap["offline_reason"] = "unreachable"
            snap["error"] = f"连接 {base_url}/health 失败 (实例可能已关机或正在重连): {e}"
        return snap

    # 尝试调用 /v1/metrics（只读鉴权，不重置空闲计时器）
    try:
        snap["metrics"] = _http_get_json(f"{base_url}/v1/metrics", api_key=api_key or None, timeout=timeout)
    except urllib.error.HTTPError as he:
        if he.code == 404:
            snap["metrics_note"] = "当前运行中的 Colab 实例尚未加载 /v1/metrics 路由（重启或执行热挂载单元后即可显示 GPU 显存/利用率详情）"
        else:
            snap["metrics_note"] = f"/v1/metrics HTTP {he.code}"
    except Exception as e:
        snap["metrics_note"] = f"/v1/metrics 暂不可用: {e}"

    return snap


def _action_fail(
    ep_path: Path,
    status_code: int,
    error: str,
    output_limit: int = 12,
    include_details: bool = True,
) -> dict:
    """Build a uniform action-failure response without exposing credentials."""
    return {
        "ok": False,
        "status_code": status_code,
        "error": error,
        "snapshot": fetch_snapshot(ep_path, output_limit=output_limit, include_details=include_details),
    }


def _load_ep_credentials(
    ep_path: Path,
    action_label: str,
    output_limit: int = 12,
    include_details: bool = True,
) -> tuple[str, str] | dict:
    """Load base_url and api_key from active_endpoint.json or return an action-failure dict."""
    if not ep_path.exists():
        return _action_fail(ep_path, 400, f"未找到 {ep_path.name}，请确认 Colab 已运行 Step 6。", output_limit, include_details)
    try:
        ep = json.loads(ep_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _action_fail(ep_path, 400, f"解析 {ep_path.name} 失败: {e}", output_limit, include_details)

    base_url = (ep.get("base_url") or "").rstrip("/")
    api_key = (ep.get("api_key") or "").strip()
    if not base_url or not api_key:
        return _action_fail(
            ep_path,
            400,
            f"active_endpoint.json 缺少有效的 base_url 或 api_key，无法发送鉴权{action_label}请求。",
            output_limit,
            include_details,
        )
    return base_url, api_key


def send_keepalive(ep_path: Path, output_limit: int = 12, include_details: bool = True) -> dict:
    """Send an authenticated keep-alive ping to reset Colab STATE['last_active']. Never exposes api_key."""
    creds = _load_ep_credentials(ep_path, "保活", output_limit=output_limit, include_details=include_details)
    if isinstance(creds, dict):
        return creds
    base_url, api_key = creds

    def _fail(status_code: int, error: str) -> dict:
        return _action_fail(ep_path, status_code, error, output_limit=output_limit, include_details=include_details)

    via = ""
    try:
        _http_request_json(f"{base_url}/v1/keepalive", method="POST", api_key=api_key, payload={}, timeout=10)
        via = "keepalive_endpoint"
    except urllib.error.HTTPError as he:
        if he.code in (401, 403):
            return _fail(he.code, f"Colab 网关鉴权失败 (HTTP {he.code})，请检查 api_key。")
        if he.code in (404, 405):
            # Fallback for already-running Colab instances without /v1/keepalive:
            # GET /v1/jobs/__keepalive__ triggers verify_bearer (updating STATE['last_active']) before returning 404 Job not found.
            try:
                _http_request_json(f"{base_url}/v1/jobs/__keepalive__", method="GET", api_key=api_key, timeout=10)
                via = "jobs_auth_fallback"
            except urllib.error.HTTPError as he2:
                err_body = he2.read().decode("utf-8", errors="ignore")
                try:
                    err_json = json.loads(err_body)
                except Exception:
                    err_json = None
                if he2.code == 404 and isinstance(err_json, dict) and err_json.get("detail") == "Job not found":
                    via = "jobs_auth_fallback"
                elif he2.code in (401, 403):
                    return _fail(he2.code, f"Colab 网关鉴权失败 (HTTP {he2.code})，请检查 api_key。")
                else:
                    return _fail(502, f"保活请求未生效 (HTTP {he2.code})")
            except Exception as e2:
                return _fail(502, f"连接 Colab 隧道失败: {e2}")
        else:
            return _fail(502, f"Colab 网关返回异常 (HTTP {he.code})")
    except Exception as e:
        return _fail(502, f"连接 Colab 隧道失败: {e}")

    _clear_shutdown_marker(base_url)
    snap = fetch_snapshot(ep_path, output_limit=output_limit, include_details=include_details)
    health = snap.get("health") or {}
    return {
        "ok": True,
        "kept_alive_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "via": via,
        "idle_seconds": health.get("idle_seconds"),
        "idle_timeout_minutes": health.get("idle_timeout_minutes"),
        "snapshot": snap,
    }


def send_shutdown(ep_path: Path, output_limit: int = 12, include_details: bool = True) -> dict:
    """Send an authenticated shutdown command to release the Colab runtime. Never exposes api_key."""
    creds = _load_ep_credentials(ep_path, "关机", output_limit=output_limit, include_details=include_details)
    if isinstance(creds, dict):
        return creds
    base_url, api_key = creds

    def _fail(status_code: int, error: str) -> dict:
        return _action_fail(ep_path, status_code, error, output_limit=output_limit, include_details=include_details)

    try:
        resp = _http_request_json(f"{base_url}/v1/shutdown", method="POST", api_key=api_key, payload={}, timeout=10)
    except urllib.error.HTTPError as he:
        if he.code in (401, 403):
            return _fail(he.code, f"Colab 网关鉴权失败 (HTTP {he.code})，请检查 api_key。")
        if he.code == 404:
            return _fail(404, "当前 Colab 网关未启用 /v1/shutdown 路由。")
        return _fail(502, f"Colab 网关返回异常 (HTTP {he.code})")
    except Exception as e:
        return _fail(502, f"连接 Colab 隧道失败: {e}")

    _record_shutdown_marker(base_url)
    snap = fetch_snapshot(ep_path, output_limit=output_limit, include_details=include_details)
    return {
        "ok": True,
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": resp.get("status", "shutting_down") if isinstance(resp, dict) else "shutting_down",
        "message": (resp.get("message") if isinstance(resp, dict) else None) or "Colab L4 实例将在数秒后安全释放",
        "snapshot": snap,
    }


def render_human(snap: dict) -> str:
    lines = []
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"=== MiniMax-H3 Colab L4 状态监控 [{ts}] ===")
    lines.append(f"隧道地址 : {snap.get('base_url', '-')}")

    if not snap.get("online"):
        if snap.get("offline_reason") in ("shutdown_confirmed", "tunnel_closed"):
            lines.append(f"在线状态 : ⚪ 已关机/离线 ({snap.get('error')})")
        else:
            lines.append(f"在线状态 : 🔴 离线 ({snap.get('error')})")
        return "\n".join(lines)

    h = snap["health"]
    active = h.get("active_jobs", 0)
    idle_s = h.get("idle_seconds", 0)
    timeout_m = h.get("idle_timeout_minutes", 0)
    rem_m = max(0.0, timeout_m - idle_s / 60.0) if timeout_m else None
    busy_tag = f"🟢 推理运行中 (active_jobs={active})" if active > 0 else "🟡 空闲待命"
    lines.append(
        f"网关状态 : {busy_tag} | ComfyUI存活={h.get('comfy_alive')} | 量化档位={h.get('dit_tier')} | 云盘模式={h.get('gdrive_mode')}"
    )
    if rem_m is not None:
        lines.append(f"空闲计时 : 已空闲 {idle_s}s ({idle_s/60.0:.1f}m) / 距自动释放剩余 {rem_m:.1f} 分钟 (监控轮询不重置此计时)")

    m = snap.get("metrics")
    if m:
        gpu = m.get("gpu") or {}
        ram = m.get("ram") or {}
        disk = m.get("disk") or {}
        q = m.get("jobs") or {}
        lines.append("-" * 68)
        if gpu.get("available"):
            lines.append(
                f"GPU 负载 : {gpu.get('name')} | 核心利用率 {gpu.get('util_gpu_pct')}% | "
                f"显存 {gpu.get('vram_used_mib')}/{gpu.get('vram_total_mib')} MiB ({gpu.get('vram_pct')}%) | "
                f"温度 {gpu.get('temp_c')}°C | 功耗 {gpu.get('power_w')}W"
            )
        lines.append(
            f"系统资源 : 内存 {ram.get('used_gib')}/{ram.get('total_gib')} GiB ({ram.get('pct')}%) | "
            f"磁盘已用 {disk.get('used_gib')}/{disk.get('total_gib')} GiB (剩余 {disk.get('free_gib')} GiB)"
        )
        lines.append(
            f"任务统计 : 运行中={q.get('running', 0)} | 排队={q.get('queued', 0)} | "
            f"已完成={q.get('succeeded', 0)} | 失败={q.get('failed', 0)}"
        )
    elif snap.get("metrics_note"):
        lines.append(f"硬件指标 : {snap['metrics_note']}")

    rec = snap.get("recent_outputs") or []
    if rec:
        lines.append("-" * 68)
        lines.append("最近云盘归档出图耗时 (outputs/*.json):")
        for r in rec:
            lines.append(
                f"  • {r['file']} | {r['size']} ({r['mp']}MP, refs={r['refs']}) | GPU耗时: {r['elapsed_sec']}s"
            )
    lines.append("=" * 68)
    return "\n".join(lines)


def _run_v2_cli(argv: list[str]) -> int:
    """Execute v2 multi-instance CLI subcommands (instances | status | action | logs)."""
    from control.protocol import ALLOWED_LOG_SOURCES, HIGH_RISK_ACTIONS, redact_payload
    from control.registry import InstanceRegistry, _http_get_json as agent_get, auto_discover_monitor_root
    from control.router import ActionRouter

    parser = argparse.ArgumentParser(prog="h3_cli_monitor.py", description="Colab 服务器多实例管控 CLI (v2.0)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--ep", type=Path, default=DEFAULT_EP_FILE, help="Legacy active_endpoint.json 路径")
        p.add_argument("--monitor-root", type=Path, default=None, help="_colab_monitor 云盘交换目录")
        p.add_argument("--json", action="store_true", help="输出脱敏后的 JSON")

    p_inst = sub.add_parser("instances", help="列出所有已发现的 Colab 实例")
    _add_common(p_inst)

    p_stat = sub.add_parser("status", help="查看指定实例实时状态")
    _add_common(p_stat)
    p_stat.add_argument("--instance", required=True, help="目标 instance_id")

    p_act = sub.add_parser("action", help="向指定实例下发枚举管理动作")
    _add_common(p_act)
    p_act.add_argument("--instance", required=True, help="目标 instance_id")
    p_act.add_argument("action_name", help="动作枚举名，如 lifecycle.drain_shutdown")
    p_act.add_argument("--params", default="{}", help="JSON 格式参数对象")
    p_act.add_argument("--wait", type=float, default=0.0, help="若降级到云盘信箱，最多轮询等待 ACK 秒数")
    p_act.add_argument("-y", "--yes", action="store_true", help="跳过高危动作交互确认")

    p_logs = sub.add_parser("logs", help="拉取脱敏后的白名单日志")
    _add_common(p_logs)
    p_logs.add_argument("--instance", required=True, help="目标 instance_id")
    p_logs.add_argument("--source", default="agent", choices=sorted(ALLOWED_LOG_SOURCES), help="日志源白名单键")
    p_logs.add_argument("--tail", type=int, default=200, help="最多拉取行数 (1..2000)")

    args = parser.parse_args(argv)
    mroot = args.monitor_root.resolve() if args.monitor_root else auto_discover_monitor_root(args.ep)
    reg = InstanceRegistry(monitor_root=mroot, primary_ep=args.ep)

    if args.cmd == "instances":
        insts = reg.discover_all()
        clean = [{k: v for k, v in i.items() if not str(k).startswith("_")} for i in insts]
        clean = redact_payload(clean, reg.get_all_secrets())
        if args.json:
            print(json.dumps({"instances": clean}, ensure_ascii=False, indent=2))
        else:
            if not clean:
                print("当前未发现任何 Colab 实例。")
            for i in clean:
                gpu_name = ((i.get("hardware") or {}).get("gpu") or {}).get("name") or "CPU/未知"
                print(
                    f"• {i['instance_id']} | {i.get('liveness_state')} | {i.get('label')} | "
                    f"GPU={gpu_name} | 通道={i.get('active_channel')}"
                )
        return 0

    if args.cmd == "status":
        inst = reg.get_instance(args.instance, refresh=True)
        if not inst:
            print(f"[错误] 未找到实例：{args.instance}", file=sys.stderr)
            return 1
        clean = redact_payload({k: v for k, v in inst.items() if not str(k).startswith("_")}, reg.get_all_secrets())
        if args.json:
            print(json.dumps(clean, ensure_ascii=False, indent=2))
        else:
            print(f"实例: {clean['instance_id']} ({clean.get('label')})")
            print(f"状态: {clean.get('liveness_state')} | 通道: {clean.get('active_channel')} | 模式: {clean.get('run_mode')}")
            lc = clean.get("lifecycle") or {}
            print(f"空闲: {lc.get('idle_seconds')}s / 上限 {lc.get('idle_timeout_minutes')}m | drain={lc.get('drain_requested')}")
        return 0

    if args.cmd == "action":
        try:
            params = json.loads(args.params)
            if not isinstance(params, dict):
                raise ValueError("params must be a JSON object")
        except Exception as exc:
            print(f"[参数错误] 无效的 --params JSON: {exc}", file=sys.stderr)
            return 2

        if args.action_name in HIGH_RISK_ACTIONS and not args.yes:
            if not sys.stdin.isatty():
                print(
                    f"[已取消] 非交互环境下执行高危动作 {args.action_name} 需显式传入 --yes (-y) 确认。",
                    file=sys.stderr,
                )
                return 1
            try:
                ans = input(f"确认向实例 {args.instance} 下发高危动作 {args.action_name}？[y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans not in ("y", "yes"):
                print("已取消操作。")
                return 1

        router = ActionRouter(reg, monitor_root=mroot)
        code, res = router.dispatch_action(args.instance, args.action_name, params=params, issued_by="local_cli")
        if args.wait > 0 and res.get("status") == "queued" and res.get("cmd_id"):
            deadline = time.time() + args.wait
            cid = res["cmd_id"]
            while time.time() < deadline:
                cur = router.get_command(cid)
                if cur and cur.get("status") not in ("queued", "accepted", "running"):
                    res.update(cur)
                    res["ok"] = cur.get("status") == "succeeded"
                    break
                time.sleep(1.0)
        clean = redact_payload(res, reg.get_all_secrets())
        if args.json:
            print(json.dumps(clean, ensure_ascii=False, indent=2))
        else:
            print(f"[{clean.get('status')}] ({clean.get('channel')}) {clean.get('message')}")
        return 0 if clean.get("ok") else 1

    if args.cmd == "logs":
        inst = reg.get_instance(args.instance, refresh=True)
        if not inst:
            print(f"[错误] 未找到实例：{args.instance}", file=sys.stderr)
            return 1
        tunnel = inst.get("tunnel") or {}
        api_key = reg.get_secret(args.instance)
        if tunnel.get("healthy") and tunnel.get("base_url") and api_key:
            prefix = str(tunnel.get("agent_prefix") or "/agent/v1").rstrip("/")
            res = agent_get(
                f"{tunnel['base_url'].rstrip('/')}{prefix}/logs?source={args.source}&tail={args.tail}",
                api_key=api_key,
                timeout=3.0,
            )
            clean = redact_payload(res, reg.get_all_secrets())
            if args.json:
                print(json.dumps(clean, ensure_ascii=False, indent=2))
            else:
                for line in clean.get("lines") or []:
                    print(line)
            return 0
        print("[提示] 实例当前无可用实时隧道，无法直接拉取实时日志。", file=sys.stderr)
        return 1

    return 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("instances", "status", "action", "logs"):
        sys.exit(_run_v2_cli(sys.argv[1:]))

    parser = argparse.ArgumentParser(description="MiniMax-H3 Colab L4 资源监控工具 (CLI / Web Dashboard)")
    parser.add_argument("--ep", type=Path, default=DEFAULT_EP_FILE, help="active_endpoint.json 路径")
    parser.add_argument("--watch", type=float, default=0.0, help="每隔 N 秒持续刷新终端面板（例如 --watch 3）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    parser.add_argument("--web", action="store_true", help="启动网页版 Sage Dashboard")
    parser.add_argument("--keepalive", action="store_true", help="手动发送一次保活请求重置 Colab 空闲自动关机计时器")
    parser.add_argument("--shutdown", action="store_true", help="远程一键关闭并释放 Colab L4 实例 (/v1/shutdown)")
    parser.add_argument("-y", "--yes", action="store_true", help="与 --shutdown 配合使用，跳过交互确认直接执行关机")
    parser.add_argument("--host", default="127.0.0.1", help="Web 服务监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8765, help="Web 服务端口（默认 8765）")
    parser.add_argument("--no-open", action="store_true", help="启动 Web 服务时不自动打开浏览器")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="与 --web 配合使用，显式允许绑定非回环地址（服务无内置鉴权）",
    )
    args = parser.parse_args()

    if args.web:
        from server import run_server

        run_server(
            ep_path=args.ep,
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
            allow_remote=args.allow_remote,
        )
        return

    if args.keepalive:
        res = send_keepalive(args.ep, output_limit=6, include_details=False)
        res.pop("status_code", None)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            if res["ok"]:
                print(f"[保活成功] 方式: {res['via']} | idle_seconds={res.get('idle_seconds')}")
            else:
                print(f"[保活失败: {res.get('error')}]")
            print(render_human(res["snapshot"]))
        sys.exit(0 if res["ok"] else 1)

    if args.shutdown:
        if not args.yes:
            if not sys.stdin.isatty():
                print("[已取消] 非交互环境下执行 --shutdown 需显式传入 --yes (-y) 确认。", file=sys.stderr)
                sys.exit(1)
            try:
                answer = input("确认向 Colab 实例发送远程关机指令以释放算力？[y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer not in ("y", "yes"):
                print("已取消关机。")
                sys.exit(1)
        res = send_shutdown(args.ep, output_limit=6, include_details=False)
        res.pop("status_code", None)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            if res["ok"]:
                print(f"[关机指令已发送] {res.get('message')}")
            else:
                print(f"[关机失败: {res.get('error')}]")
        sys.exit(0 if res["ok"] else 1)

    if args.watch <= 0:
        snap = fetch_snapshot(args.ep)
        print(json.dumps(snap, ensure_ascii=False, indent=2) if args.json else render_human(snap))
        return

    try:
        while True:
            snap = fetch_snapshot(args.ep)
            if args.json:
                print(json.dumps(snap, ensure_ascii=False), flush=True)
            else:
                sys.stdout.write("\033[2J\033[H")
                print(render_human(snap), flush=True)
            time.sleep(max(1.0, args.watch))
    except KeyboardInterrupt:
        print("\n已退出监控。")


if __name__ == "__main__":
    main()
