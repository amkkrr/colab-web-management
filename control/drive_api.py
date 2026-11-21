"""Pure-stdlib Google Drive v3 OAuth2 client (zero third-party deps, zero FUSE)."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from control.protocol import DEFAULT_DRIVE_MONITOR_REL, atomic_write_json, redact_text

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
COLAB_NOTEBOOK_MIME = "application/vnd.google.colaboratory"
_SAFE_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,128}$")
_ALLOWED_API_PREFIXES = (
    "https://oauth2.googleapis.com/",
    "https://www.googleapis.com/",
)


def validate_drive_file_id(file_id: str) -> str:
    """Strictly validate a Google Drive file ID against traversal/injection characters."""
    clean = str(file_id or "").strip()
    if not _SAFE_DRIVE_ID_RE.match(clean):
        raise PermissionError(f"Invalid Google Drive file ID: {clean!r}")
    return clean


def _validate_google_api_url(url: str) -> str:
    clean = (url or "").strip()
    if not clean.startswith(_ALLOWED_API_PREFIXES):
        raise ValueError(f"Disallowed Google API URL: {clean!r}")
    return clean


def _escape_drive_q(val: str) -> str:
    return val.replace("\\", "\\\\").replace("'", "\\'")


@dataclass(frozen=True)
class DriveOAuthConfig:
    """OAuth2 credentials for headless Google Drive v3 API access."""

    client_id: str
    client_secret: str
    refresh_token: str
    monitor_folder_path: str = DEFAULT_DRIVE_MONITOR_REL
    source: str = "env"

    def __repr__(self) -> str:
        return (
            f"DriveOAuthConfig(client_id={self.client_id[:8] + '...' if self.client_id else ''!r}, "
            f"monitor_folder_path={self.monitor_folder_path!r}, source={self.source!r}, "
            "client_secret='[REDACTED]', refresh_token='[REDACTED]')"
        )


def _parse_env_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return parsed
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
            val = val[1:-1]
        if key:
            parsed[key] = val
    return parsed


def load_gdrive_oauth_config(
    env_file: Path | str | None = None,
    monitor_folder_path: str | None = None,
) -> DriveOAuthConfig | None:
    """Load Drive OAuth2 credentials from process env and/or an env file."""
    client_id = (os.environ.get("GDRIVE_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GDRIVE_CLIENT_SECRET") or "").strip()
    refresh_token = (os.environ.get("GDRIVE_REFRESH_TOKEN") or "").strip()
    source = "env"

    candidate_file: Path | None = None
    if env_file is not None and str(env_file).strip():
        candidate_file = Path(env_file).expanduser()
    elif os.environ.get("COLAB_GDRIVE_OAUTH_ENV"):
        candidate_file = Path(os.environ["COLAB_GDRIVE_OAUTH_ENV"]).expanduser()

    file_vars: dict[str, str] = {}
    if (not client_id or not client_secret or not refresh_token) and candidate_file and candidate_file.is_file():
        file_vars = _parse_env_file(candidate_file)
        client_id = client_id or (file_vars.get("GDRIVE_CLIENT_ID") or "").strip()
        client_secret = client_secret or (file_vars.get("GDRIVE_CLIENT_SECRET") or "").strip()
        refresh_token = refresh_token or (file_vars.get("GDRIVE_REFRESH_TOKEN") or "").strip()
        if file_vars:
            source = str(candidate_file)

    if not (client_id and client_secret and refresh_token):
        return None

    resolved_folder = (
        monitor_folder_path
        or os.environ.get("COLAB_DRIVE_MONITOR_PATH")
        or file_vars.get("COLAB_DRIVE_MONITOR_PATH")
        or DEFAULT_DRIVE_MONITOR_REL
    )
    clean_folder = str(resolved_folder).strip().strip("/") or DEFAULT_DRIVE_MONITOR_REL
    return DriveOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        monitor_folder_path=clean_folder,
        source=source,
    )


class StdlibOAuthDriveClient:
    """Thread-safe Google Drive v3 API client built strictly on Python stdlib."""

    def __init__(
        self,
        config: DriveOAuthConfig,
        *,
        opener: Any | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.config = config
        self._opener = opener
        self.timeout = float(timeout)
        self._token_lock = threading.Lock()
        self._api_lock = threading.RLock()
        self._sync_lock = threading.Lock()
        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._folder_ids: dict[str, str] = {}
        self._folder_meta: dict[str, tuple[str, str | None]] = {}
        self._file_ids: dict[str, str] = {}
        self._synced_mtimes: dict[str, str] = {}

    def _secrets(self) -> tuple[str, ...]:
        items = [self.config.client_secret, self.config.refresh_token]
        if self._access_token:
            items.append(self._access_token)
        return tuple(s for s in items if s)

    def _open(self, req: urllib.request.Request) -> bytes:
        if self._opener is not None:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.read()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 - URL strictly validated
            return resp.read()

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        with self._token_lock:
            now = time.time()
            if not force_refresh and self._access_token and now < (self._token_expires_at - 60.0):
                return self._access_token

            form = urllib.parse.urlencode(
                {
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "refresh_token": self.config.refresh_token,
                    "grant_type": "refresh_token",
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                _validate_google_api_url(OAUTH_TOKEN_URL),
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Colab-Control-Plane/2.0"},
                method="POST",
            )
            try:
                raw = self._open(req)
                payload = json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                err_body = ""
                try:
                    err_body = exc.read().decode("utf-8", errors="replace")[:200]
                except Exception:
                    err_body = ""
                raise RuntimeError(
                    redact_text(f"OAuth2 token refresh failed (HTTP {exc.code}): {err_body}", self._secrets())
                ) from exc
            except Exception as exc:
                raise RuntimeError(redact_text(f"OAuth2 token refresh failed: {exc}", self._secrets())) from exc

            token = str(payload.get("access_token") or "").strip()
            if not token:
                raise RuntimeError("OAuth2 token endpoint returned empty access_token")
            expires_in = float(payload.get("expires_in") or 3600.0)
            self._access_token = token
            self._token_expires_at = time.time() + max(60.0, expires_in)
            return token

    def _request_bytes(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> bytes:
        safe_url = _validate_google_api_url(url)
        for attempt in range(2):
            token = self._get_access_token(force_refresh=(attempt == 1))
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": "Colab-Control-Plane/2.0",
            }
            if content_type:
                headers["Content-Type"] = content_type
            req = urllib.request.Request(safe_url, data=body, headers=headers, method=method)
            try:
                return self._open(req)
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    continue
                err_body = ""
                try:
                    err_body = exc.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    err_body = ""
                raise RuntimeError(
                    redact_text(f"Drive API {method} failed (HTTP {exc.code}): {err_body}", self._secrets())
                ) from exc
            except Exception as exc:
                raise RuntimeError(redact_text(f"Drive API {method} error: {exc}", self._secrets())) from exc
        raise RuntimeError("Drive API request failed after token refresh retry")

    def _api_json(
        self,
        method: str,
        url: str,
        *,
        body: bytes | dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        payload_bytes: bytes | None
        resolved_ct = content_type
        if isinstance(body, dict):
            payload_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
            resolved_ct = resolved_ct or "application/json; charset=utf-8"
        else:
            payload_bytes = body
        raw = self._request_bytes(method, url, body=payload_bytes, content_type=resolved_ct)
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    def check_health(self) -> dict[str, Any]:
        try:
            self._get_access_token()
            return {"ok": True, "monitor_folder_path": self.config.monitor_folder_path}
        except Exception as exc:
            return {
                "ok": False,
                "monitor_folder_path": self.config.monitor_folder_path,
                "error": redact_text(str(exc), self._secrets()),
            }

    def _find_or_create_folder(self, parent_id: str, name: str, *, create: bool = True) -> str | None:
        q = (
            f"name = '{_escape_drive_q(name)}' and '{parent_id}' in parents "
            f"and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        qs = urllib.parse.urlencode(
            {
                "q": q,
                "fields": "files(id, modifiedTime)",
                "orderBy": "modifiedTime desc",
                "pageSize": 5,
            }
        )
        res = self._api_json("GET", f"{DRIVE_FILES_URL}?{qs}")
        files = res.get("files") or []
        if files:
            return str(files[0]["id"])
        if not create:
            return None
        created = self._api_json(
            "POST",
            f"{DRIVE_FILES_URL}?fields=id",
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        )
        return str(created["id"])

    def resolve_folder_id(self, rel_dir: str, *, create: bool = True) -> str | None:
        norm = rel_dir.strip("/")
        with self._api_lock:
            if norm in self._folder_ids:
                return self._folder_ids[norm]
            root_parts = [p for p in self.config.monitor_folder_path.split("/") if p]
            sub_parts = [p for p in norm.split("/") if p] if norm else []
            parts = root_parts + sub_parts
            cur_id = "root"
            for idx, part in enumerate(parts):
                if idx < len(root_parts) - 1:
                    cache_key = "__root__/" + "/".join(parts[: idx + 1])
                elif idx == len(root_parts) - 1:
                    cache_key = ""
                else:
                    cache_key = "/".join(parts[len(root_parts) : idx + 1])
                if cache_key in self._folder_ids:
                    cur_id = self._folder_ids[cache_key]
                    continue
                found = self._find_or_create_folder(cur_id, part, create=create)
                if not found:
                    return None
                cur_id = found
                self._folder_ids[cache_key] = cur_id
            self._folder_ids[norm] = cur_id
            return cur_id

    def list_files(self, rel_dir: str) -> list[dict[str, Any]]:
        norm = rel_dir.strip("/")
        with self._api_lock:
            folder_id = self.resolve_folder_id(norm, create=False)
            if not folder_id:
                return []
            q = f"'{folder_id}' in parents and mimeType != '{FOLDER_MIME}' and trashed = false"
            qs = urllib.parse.urlencode(
                {
                    "q": q,
                    "fields": "files(id, name, modifiedTime, size)",
                    "orderBy": "modifiedTime desc",
                    "pageSize": 100,
                }
            )
            res = self._api_json("GET", f"{DRIVE_FILES_URL}?{qs}")
            seen: set[str] = set()
            out: list[dict[str, Any]] = []
            for item in res.get("files") or []:
                name = str(item.get("name") or "")
                fid = str(item.get("id") or "")
                if not name or not fid or name.startswith("._tmp_") or name in seen:
                    continue
                seen.add(name)
                rel = f"{norm}/{name}" if norm else name
                self._file_ids[rel] = fid
                out.append(
                    {
                        "id": fid,
                        "name": name,
                        "modifiedTime": str(item.get("modifiedTime") or ""),
                        "size": item.get("size"),
                    }
                )
            return out

    def get_bytes(self, file_id: str) -> bytes:
        safe_id = urllib.parse.quote(str(file_id), safe="")
        return self._request_bytes("GET", f"{DRIVE_FILES_URL}/{safe_id}?alt=media")

    def put_bytes(self, rel_path: str, data: bytes, mime_type: str = "application/json") -> str:
        clean = rel_path.strip("/")
        parent_rel, _, name = clean.rpartition("/")
        with self._api_lock:
            folder_id = self.resolve_folder_id(parent_rel, create=True)
            if not folder_id:
                raise RuntimeError(f"Unable to resolve Drive folder for {clean}")
            file_id = self._file_ids.get(clean)
            if not file_id:
                q = (
                    f"name = '{_escape_drive_q(name)}' and '{folder_id}' in parents "
                    f"and mimeType != '{FOLDER_MIME}' and trashed = false"
                )
                qs = urllib.parse.urlencode(
                    {
                        "q": q,
                        "fields": "files(id, modifiedTime)",
                        "orderBy": "modifiedTime desc",
                        "pageSize": 10,
                    }
                )
                res = self._api_json("GET", f"{DRIVE_FILES_URL}?{qs}")
                matches = res.get("files") or []
                if matches:
                    file_id = str(matches[0]["id"])
                    self._file_ids[clean] = file_id

            if file_id:
                safe_id = urllib.parse.quote(file_id, safe="")
                try:
                    updated = self._api_json(
                        "PATCH",
                        f"{DRIVE_UPLOAD_URL}/{safe_id}?uploadType=media&fields=id",
                        body=data,
                        content_type=mime_type,
                    )
                    return str(updated.get("id") or file_id)
                except RuntimeError as exc:
                    if "HTTP 404" not in str(exc):
                        raise
                    self._file_ids.pop(clean, None)

            boundary = f"colab_mon_{secrets.token_hex(12)}"
            meta_bytes = json.dumps({"name": name, "parents": [folder_id]}, ensure_ascii=False).encode("utf-8")
            multipart_body = (
                f"--{boundary}\r\n"
                "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            ).encode("utf-8") + meta_bytes + (
                f"\r\n--{boundary}\r\n"
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
            created = self._api_json(
                "POST",
                f"{DRIVE_UPLOAD_URL}?uploadType=multipart&fields=id",
                body=multipart_body,
                content_type=f"multipart/related; boundary={boundary}",
            )
            new_id = str(created["id"])
            self._file_ids[clean] = new_id
            return new_id

    def put_json(self, rel_path: str, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return self.put_bytes(rel_path, raw, mime_type="application/json")

    def get_json(self, rel_path: str) -> dict[str, Any] | None:
        clean = rel_path.strip("/")
        parent_rel, _, name = clean.rpartition("/")
        with self._api_lock:
            folder_id = self.resolve_folder_id(parent_rel, create=False)
            if not folder_id:
                return None
            q = (
                f"name = '{_escape_drive_q(name)}' and '{folder_id}' in parents "
                f"and mimeType != '{FOLDER_MIME}' and trashed = false"
            )
            qs = urllib.parse.urlencode(
                {
                    "q": q,
                    "fields": "files(id, modifiedTime)",
                    "orderBy": "modifiedTime desc",
                    "pageSize": 5,
                }
            )
            res = self._api_json("GET", f"{DRIVE_FILES_URL}?{qs}")
            matches = res.get("files") or []
            if not matches:
                return None
            file_id = str(matches[0]["id"])
            self._file_ids[clean] = file_id
            raw = self.get_bytes(file_id)
            try:
                data = json.loads(raw.decode("utf-8"))
                return data if isinstance(data, dict) else None
            except Exception:
                return None

    def sync_mailbox_to_local(
        self,
        local_monitor_root: Path,
        *,
        pending_ack_targets: list[tuple[str, str]] | None = None,
    ) -> dict[str, int]:
        """Pull instances, secrets, and requested pending ACKs from Drive into local cache."""
        root = local_monitor_root.resolve()
        for sub in ("instances", "secrets", "acks", "commands"):
            (root / sub).mkdir(parents=True, exist_ok=True)

        counts = {"instances": 0, "secrets": 0, "acks": 0}
        with self._sync_lock:
            # 1. Sync instances/*.json
            remote_instances = self.list_files("instances")
            remote_inst_names: set[str] = set()
            for item in remote_instances:
                fname = item["name"]
                if not fname.endswith(".json"):
                    continue
                remote_inst_names.add(fname)
                rel = f"instances/{fname}"
                target = root / "instances" / fname
                mtime = item.get("modifiedTime") or ""
                if mtime and self._synced_mtimes.get(rel) == mtime and target.is_file():
                    continue
                try:
                    raw = self.get_bytes(item["id"])
                    data = json.loads(raw.decode("utf-8"))
                    if isinstance(data, dict):
                        atomic_write_json(target, data)
                        if mtime:
                            self._synced_mtimes[rel] = mtime
                        counts["instances"] += 1
                except Exception:
                    continue

            # Prune local instance cache files that no longer exist on Drive
            for local_fp in (root / "instances").glob("*.json"):
                if local_fp.name.startswith("._tmp_") or local_fp.name in remote_inst_names:
                    continue
                try:
                    local_fp.unlink(missing_ok=True)
                    self._synced_mtimes.pop(f"instances/{local_fp.name}", None)
                except OSError:
                    pass

            # 2. Sync secrets/*.json
            remote_secrets = self.list_files("secrets")
            for item in remote_secrets:
                fname = item["name"]
                if not fname.endswith(".json"):
                    continue
                rel = f"secrets/{fname}"
                target = root / "secrets" / fname
                mtime = item.get("modifiedTime") or ""
                if mtime and self._synced_mtimes.get(rel) == mtime and target.is_file():
                    continue
                try:
                    raw = self.get_bytes(item["id"])
                    data = json.loads(raw.decode("utf-8"))
                    if isinstance(data, dict):
                        atomic_write_json(target, data)
                        try:
                            os.chmod(target, 0o600)
                        except OSError:
                            pass
                        if mtime:
                            self._synced_mtimes[rel] = mtime
                        counts["secrets"] += 1
                except Exception:
                    continue

            # 3. Sync requested pending ACKs
            if pending_ack_targets:
                by_inst: dict[str, set[str]] = {}
                for iid, cid in pending_ack_targets:
                    by_inst.setdefault(iid, set()).add(f"{cid}.json")
                for iid, wanted_files in by_inst.items():
                    remote_acks = self.list_files(f"acks/{iid}")
                    for item in remote_acks:
                        fname = item["name"]
                        if fname not in wanted_files:
                            continue
                        target = root / "acks" / iid / fname
                        if target.is_file():
                            continue
                        try:
                            raw = self.get_bytes(item["id"])
                            data = json.loads(raw.decode("utf-8"))
                            if isinstance(data, dict):
                                atomic_write_json(target, data)
                                counts["acks"] += 1
                        except Exception:
                            continue

        return counts

    def push_command_json(self, instance_id: str, cmd_id: str, envelope: dict[str, Any]) -> str:
        return self.put_json(f"commands/{instance_id}/{cmd_id}.json", envelope)

    def ensure_agent_uploaded(self, agent_src_path: Path) -> bool:
        src = agent_src_path.resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Missing colab_agent.py source: {src}")
        existing = self.list_files("")
        if any(item["name"] == "colab_agent.py" for item in existing):
            return False
        self.put_bytes("colab_agent.py", src.read_bytes(), mime_type="text/x-python")
        return True

    def export_vm_payload(self) -> dict[str, str]:
        return {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "refresh_token": self.config.refresh_token,
            "monitor_folder_path": self.config.monitor_folder_path,
        }

    def _get_folder_meta(self, folder_id: str) -> tuple[str, str | None] | None:
        fid = validate_drive_file_id(folder_id)
        if fid in self._folder_meta:
            return self._folder_meta[fid]
        qs = urllib.parse.urlencode({"fields": "id, name, parents"})
        res = self._api_json("GET", f"{DRIVE_FILES_URL}/{urllib.parse.quote(fid, safe='')}?{qs}")
        name = str(res.get("name") or "").strip()
        if not name:
            return None
        parents = res.get("parents") or []
        parent_id = str(parents[0]) if isinstance(parents, list) and parents else None
        meta = (name, parent_id)
        self._folder_meta[fid] = meta
        return meta

    def _resolve_parent_path(self, parent_id: str | None) -> str:
        if not parent_id or parent_id == "root":
            return "My Drive"
        parts: list[tuple[str, bool]] = []
        seen: set[str] = set()
        cur: str | None = str(parent_id)
        for _ in range(6):
            if not cur or cur == "root" or cur in seen:
                break
            seen.add(cur)
            try:
                meta = self._get_folder_meta(cur)
            except Exception:
                break
            if not meta:
                break
            name, nxt = meta
            is_drive_root = not nxt or nxt == "root"
            parts.append((name, is_drive_root))
            cur = nxt
        if not parts:
            return ""
        chain = list(reversed(parts))
        if len(chain) > 1 and chain[0][1] and chain[0][0].lower() in ("my drive", "我的云端硬盘"):
            chain = chain[1:]
        return "/".join(p[0] for p in chain if p[0])

    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        safe_id = validate_drive_file_id(file_id)
        qs = urllib.parse.urlencode({"fields": "id, name, mimeType, modifiedTime, size, parents, trashed"})
        with self._api_lock:
            try:
                return self._api_json("GET", f"{DRIVE_FILES_URL}/{urllib.parse.quote(safe_id, safe='')}?{qs}")
            except RuntimeError as exc:
                if "HTTP 404" in str(exc):
                    raise FileNotFoundError(f"Drive file not found: {safe_id}") from exc
                raise

    def update_file_bytes(self, file_id: str, data: bytes, mime_type: str = "application/json") -> str:
        safe_id = validate_drive_file_id(file_id)
        with self._api_lock:
            try:
                updated = self._api_json(
                    "PATCH",
                    f"{DRIVE_UPLOAD_URL}/{urllib.parse.quote(safe_id, safe='')}?uploadType=media&fields=id",
                    body=data,
                    content_type=mime_type,
                )
            except RuntimeError as exc:
                if "HTTP 404" in str(exc):
                    raise FileNotFoundError(f"Drive file not found: {safe_id}") from exc
                raise
            return str(updated.get("id") or safe_id)

    def list_notebooks(
        self,
        *,
        folder_paths: list[str] | None = None,
        max_results: int = 80,
    ) -> list[dict[str, Any]]:
        """List non-trashed Colab/Jupyter .ipynb files in My Drive via Drive v3 API."""
        cap = max(1, min(int(max_results), 200))
        wanted_folders = [f.strip().strip("/").lower() for f in (folder_paths or []) if f and f.strip().strip("/")]
        q = (
            f"(mimeType = '{COLAB_NOTEBOOK_MIME}' or name contains '.ipynb') "
            f"and mimeType != '{FOLDER_MIME}' and trashed = false"
        )
        seen_ids: set[str] = set()
        out: list[dict[str, Any]] = []
        page_token: str | None = None

        with self._api_lock:
            while len(out) < cap:
                params: dict[str, Any] = {
                    "q": q,
                    "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size, parents)",
                    "orderBy": "modifiedTime desc",
                    "pageSize": min(cap - len(out), 100),
                }
                if page_token:
                    params["pageToken"] = page_token
                res = self._api_json("GET", f"{DRIVE_FILES_URL}?{urllib.parse.urlencode(params)}")
                for item in res.get("files") or []:
                    fid = str(item.get("id") or "").strip()
                    name = str(item.get("name") or "").strip()
                    mime = str(item.get("mimeType") or "").strip()
                    if not fid or not name or fid in seen_ids or name.startswith("._tmp_"):
                        continue
                    if not (name.lower().endswith(".ipynb") or mime == COLAB_NOTEBOOK_MIME):
                        continue
                    try:
                        validate_drive_file_id(fid)
                    except PermissionError:
                        continue
                    seen_ids.add(fid)

                    parents = item.get("parents") or []
                    parent_id = str(parents[0]) if isinstance(parents, list) and parents else None
                    folder_path = self._resolve_parent_path(parent_id)
                    if wanted_folders:
                        fp_low = folder_path.lower()
                        fp_with_root = fp_low if fp_low.startswith("my drive") else f"my drive/{fp_low}".strip("/")
                        matched = any(
                            fp_low == wf
                            or fp_low.startswith(f"{wf}/")
                            or fp_with_root == wf
                            or fp_with_root.startswith(f"{wf}/")
                            for wf in wanted_folders
                        )
                        if not matched:
                            continue

                    display_path = f"{folder_path}/{name}" if folder_path else name
                    out.append(
                        {
                            "id": fid,
                            "name": name,
                            "mimeType": mime or COLAB_NOTEBOOK_MIME,
                            "modifiedTime": str(item.get("modifiedTime") or ""),
                            "size": item.get("size"),
                            "folder_path": folder_path,
                            "display_path": display_path,
                        }
                    )
                    if len(out) >= cap:
                        break
                page_token = res.get("nextPageToken")
                if not page_token:
                    break
        return out
