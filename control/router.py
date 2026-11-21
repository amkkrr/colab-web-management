"""Dual-channel (HTTPS Tunnel + Google Drive Mailbox) Action Router with same-cmd_id fallback & ACK tracking."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Any

from control.protocol import (
    ALL_WRITE_ACTIONS,
    ALLOWED_FLEET_ACTIONS,
    DEFAULT_ACK_TIMEOUT_SEC,
    DEFAULT_CMD_TTL_SEC,
    LEGACY_SUPPORTED_ACTIONS,
    atomic_write_json,
    build_action_envelope,
    parse_iso_ts,
    utc_now_iso,
    validate_upstream_url,
)
from control.registry import InstanceRegistry
from h3_cli_monitor import send_keepalive, send_shutdown

TUNNEL_ACTION_TIMEOUT_SEC = 2.5


def _post_json(url: str, payload: dict[str, Any], api_key: str, timeout: float = TUNNEL_ACTION_TIMEOUT_SEC) -> tuple[int, dict[str, Any]]:
    safe_url = validate_upstream_url(url)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Colab-Control-Plane/2.0",
    }
    req = urllib.request.Request(safe_url, data=data, headers=headers, method="POST")
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if safe_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
        else urllib.request.build_opener()
    )
    try:
        with opener.open(req, timeout=timeout) as resp:  # nosec B310 - scheme/host strictly validated
            body = json.loads(resp.read().decode("utf-8"))
            return int(resp.status), body if isinstance(body, dict) else {"raw": body}
    except urllib.error.HTTPError as he:
        raw = he.read().decode("utf-8", errors="ignore")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"error": raw or f"HTTP {he.code}"}
        return int(he.code), body if isinstance(body, dict) else {"error": str(body)}


class ActionRouter:
    """Routes actions to Channel A (tunnel) or Channel B (Drive mailbox) reusing the same cmd_id on fallback."""

    def __init__(
        self,
        registry: InstanceRegistry,
        *,
        monitor_root: Path,
        storage: Any | None = None,
        ack_timeout_sec: float = DEFAULT_ACK_TIMEOUT_SEC,
    ) -> None:
        self.registry = registry
        self.monitor_root = monitor_root.resolve()
        self.storage = storage
        self.ack_timeout_sec = float(ack_timeout_sec)
        self._lock = threading.RLock()
        self._commands: dict[str, dict[str, Any]] = {}

    def _record_command(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._commands[rec["cmd_id"]] = dict(rec)
        if self.storage is not None:
            try:
                self.storage.upsert_command(rec)
            except Exception:
                pass

    def has_pending_commands(self) -> bool:
        with self._lock:
            return any(c.get("status") in ("queued", "accepted", "running") for c in self._commands.values())

    def get_command(self, cmd_id: str) -> dict[str, Any] | None:
        self.poll_acks()
        with self._lock:
            rec = self._commands.get(cmd_id)
            if rec:
                return dict(rec)
        if self.storage is not None:
            try:
                return self.storage.get_command(cmd_id)
            except Exception:
                return None
        return None

    def list_commands(self, instance_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        self.poll_acks()
        with self._lock:
            items = list(self._commands.values())
        if instance_id:
            items = [c for c in items if c.get("instance_id") == instance_id]
        items.sort(key=lambda c: c.get("issued_at") or "", reverse=True)
        return items[:limit]

    def _enqueue_drive_command(self, envelope: dict[str, Any], *, fallback_reason: str | None = None) -> tuple[int, dict[str, Any]]:
        iid = envelope["instance_id"]
        cmd_id = envelope["cmd_id"]
        cmd_path = self.monitor_root / "commands" / iid / f"{cmd_id}.json"
        atomic_write_json(cmd_path, envelope)
        msg = (
            f"隧道不可达（{fallback_reason}），已使用相同 cmd_id 自动降级写入云盘信箱等待实例执行。"
            if fallback_reason
            else "已写入云盘带外信箱，等待实例轮询消费（约 15–60s）。"
        )
        rec = {
            "cmd_id": cmd_id,
            "instance_id": iid,
            "action": envelope["action"],
            "params": envelope.get("params") or {},
            "channel": "drive",
            "status": "queued",
            "issued_at": envelope["issued_at"],
            "expires_at": envelope["expires_at"],
            "acked_at": None,
            "message": msg,
            "data": {"fallback_reason": fallback_reason} if fallback_reason else {},
        }
        self._record_command(rec)
        return HTTPStatus.ACCEPTED, rec

    def _dispatch_legacy(self, inst: dict[str, Any], action: str, output_limit: int = 12) -> tuple[int, dict[str, Any]]:
        iid = inst["instance_id"]
        if action not in LEGACY_SUPPORTED_ACTIONS:
            return HTTPStatus.NOT_IMPLEMENTED, {
                "ok": False,
                "instance_id": iid,
                "action": action,
                "status": "rejected_unsupported_legacy",
                "message": f"Legacy 实例仅支持 lifecycle.keepalive 与 lifecycle.shutdown_now，不支持 {action}。",
            }
        ep_path = self.registry.get_legacy_ep_path(iid)
        if not ep_path:
            return HTTPStatus.BAD_REQUEST, {
                "ok": False,
                "instance_id": iid,
                "action": action,
                "status": "failed",
                "message": "未找到该 Legacy 实例的 active_endpoint.json。",
            }
        res = (
            send_keepalive(ep_path, output_limit=output_limit, include_details=True)
            if action == "lifecycle.keepalive"
            else send_shutdown(ep_path, output_limit=output_limit, include_details=True)
        )
        status_code = res.pop("status_code", None)
        ok = bool(res.get("ok"))
        code = HTTPStatus.OK if ok else (status_code or HTTPStatus.BAD_GATEWAY)
        return int(code), {
            "ok": ok,
            "instance_id": iid,
            "action": action,
            "channel": "legacy_tunnel",
            "status": "succeeded" if ok else "failed",
            "message": res.get("message") or res.get("error") or ("操作成功" if ok else "操作失败"),
            "data": {k: v for k, v in res.items() if k != "snapshot"},
            "snapshot": res.get("snapshot"),
        }

    def dispatch_action(
        self,
        instance_id: str,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        issued_by: str = "local_web_ui",
        ttl_sec: int = DEFAULT_CMD_TTL_SEC,
        output_limit: int = 12,
    ) -> tuple[int, dict[str, Any]]:
        if action not in ALL_WRITE_ACTIONS:
            return HTTPStatus.UNPROCESSABLE_ENTITY, {
                "ok": False,
                "status": "rejected_invalid",
                "message": f"不在白名单内的管理动作：{action}",
            }

        inst = self.registry.get_instance(instance_id, refresh=True, output_limit=output_limit)
        if not inst:
            return HTTPStatus.NOT_FOUND, {
                "ok": False,
                "status": "failed",
                "message": f"未找到实例：{instance_id}",
            }

        if inst.get("is_legacy"):
            return self._dispatch_legacy(inst, action, output_limit=output_limit)

        api_key = self.registry.get_secret(instance_id)
        if not api_key:
            return HTTPStatus.FORBIDDEN, {
                "ok": False,
                "instance_id": instance_id,
                "action": action,
                "status": "rejected_unauthorized",
                "message": f"缺少 _colab_monitor/secrets/{instance_id}.json 鉴权密钥，无法签名下发指令。",
            }

        envelope = build_action_envelope(
            instance_id=instance_id,
            action=action,
            params=params or {},
            api_key=api_key,
            issued_by=issued_by,
            ttl_sec=ttl_sec,
        )

        tunnel = inst.get("tunnel") or {}
        base_url = str(tunnel.get("base_url") or "").rstrip("/")
        prefix = str(tunnel.get("agent_prefix") or "/agent/v1").rstrip("/")
        try_tunnel = bool(tunnel.get("enabled")) and bool(base_url) and inst.get("liveness_state") in (
            "ONLINE_LIVE",
            "DRAINING",
            "DEGRADED_STALE",
        )

        if try_tunnel:
            try:
                code, ack = _post_json(f"{base_url}{prefix}/actions", envelope, api_key=api_key, timeout=TUNNEL_ACTION_TIMEOUT_SEC)
                if code in (HTTPStatus.OK, HTTPStatus.ACCEPTED):
                    rec = {
                        "cmd_id": envelope["cmd_id"],
                        "instance_id": instance_id,
                        "action": action,
                        "params": envelope["params"],
                        "channel": "tunnel",
                        "status": ack.get("status", "succeeded"),
                        "issued_at": envelope["issued_at"],
                        "expires_at": envelope["expires_at"],
                        "acked_at": ack.get("finished_at") or ack.get("accepted_at") or utc_now_iso(),
                        "message": ack.get("message") or "执行完成",
                        "data": ack.get("data") or {},
                    }
                    self._record_command(rec)
                    return code, {"ok": rec["status"] in ("succeeded", "accepted", "running"), **rec}
                # Explicit agent policy/validation rejection -> do NOT fall back to Drive
                if code in (400, 401, 403, 409, 410, 422):
                    rec = {
                        "cmd_id": envelope["cmd_id"],
                        "instance_id": instance_id,
                        "action": action,
                        "params": envelope["params"],
                        "channel": "tunnel",
                        "status": ack.get("status", "rejected_invalid"),
                        "issued_at": envelope["issued_at"],
                        "expires_at": envelope["expires_at"],
                        "acked_at": utc_now_iso(),
                        "message": ack.get("message") or ack.get("error") or f"Agent rejected (HTTP {code})",
                        "data": ack.get("data") or {},
                    }
                    self._record_command(rec)
                    return code, {"ok": False, **rec}
                # 502 / 503 / 504 / 530 -> fall back to Drive with the EXACT SAME envelope/cmd_id
                code_b, rec_b = self._enqueue_drive_command(envelope, fallback_reason=f"HTTP {code}")
                return code_b, {"ok": True, **rec_b}
            except Exception as exc:
                # Connection refused / timeout -> fall back to Drive with the EXACT SAME envelope/cmd_id
                code_b, rec_b = self._enqueue_drive_command(envelope, fallback_reason=type(exc).__name__)
                return code_b, {"ok": True, **rec_b}

        code_b, rec_b = self._enqueue_drive_command(envelope)
        return code_b, {"ok": True, **rec_b}

    def dispatch_fleet_action(
        self,
        fleet_action: str,
        *,
        instance_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
        issued_by: str = "local_web_ui",
    ) -> tuple[int, dict[str, Any]]:
        if fleet_action not in ALLOWED_FLEET_ACTIONS:
            return HTTPStatus.UNPROCESSABLE_ENTITY, {
                "ok": False,
                "error": f"不支持的批量动作：{fleet_action}",
            }
        target_action = "lifecycle.drain_shutdown" if fleet_action == "fleet.drain_all" else "lifecycle.shutdown_now"
        all_insts = self.registry.discover_all()
        if instance_ids is not None:
            wanted = set(instance_ids)
            targets = [i for i in all_insts if i["instance_id"] in wanted]
        else:
            targets = [i for i in all_insts if i.get("liveness_state") in ("ONLINE_LIVE", "ONLINE_DRIVE_ONLY", "DRAINING")]

        results: list[dict[str, Any]] = []
        any_ok = False
        for inst in targets:
            code, res = self.dispatch_action(inst["instance_id"], target_action, params or {}, issued_by=issued_by)
            ok = bool(res.get("ok"))
            any_ok = any_ok or ok
            results.append(
                {
                    "instance_id": inst["instance_id"],
                    "label": inst.get("label") or inst["instance_id"],
                    "cmd_id": res.get("cmd_id"),
                    "channel": res.get("channel"),
                    "http_status": code,
                    "status": res.get("status"),
                    "ok": ok,
                    "message": res.get("message"),
                }
            )
        return HTTPStatus.OK, {"ok": any_ok or len(results) == 0, "action": fleet_action, "results": results}

    def poll_acks(self, now_ts: float | None = None) -> int:
        """Read pending ACKs from _colab_monitor/acks/<id>/<cmd_id>.json and mark expired ones timed_out."""
        now = time.time() if now_ts is None else float(now_ts)
        updated = 0
        with self._lock:
            pending = [dict(c) for c in self._commands.values() if c.get("status") in ("queued", "accepted", "running")]

        for rec in pending:
            iid = rec["instance_id"]
            cid = rec["cmd_id"]
            ack_path = self.monitor_root / "acks" / iid / f"{cid}.json"
            if ack_path.is_file():
                try:
                    ack = json.loads(ack_path.read_text(encoding="utf-8"))
                    if isinstance(ack, dict):
                        rec["status"] = ack.get("status", "succeeded")
                        rec["acked_at"] = ack.get("finished_at") or ack.get("accepted_at") or utc_now_iso(now)
                        rec["message"] = ack.get("message") or rec["message"]
                        rec["data"] = ack.get("data") or {}
                        self._record_command(rec)
                        updated += 1
                        continue
                except Exception:
                    pass
            issued_ts = parse_iso_ts(rec.get("issued_at"))
            if issued_ts is not None and (now - issued_ts) > self.ack_timeout_sec:
                rec["status"] = "timed_out"
                rec["acked_at"] = utc_now_iso(now)
                rec["message"] = f"等待云盘回执超过 {int(self.ack_timeout_sec)}s 未收到响应（请检查 Colab 实例或云盘同步）。"
                self._record_command(rec)
                updated += 1
        return updated
