"""Storage limits admins edit at runtime, and their enforcement.

Two layers, both in users.db:
  - server-wide defaults in the `settings` KV: per-file upload cap and total
    storage quota per account;
  - per-user overrides in nullable `users` columns (NULL = inherit default).

`user_limits()` resolves the effective pair for an account; a missing or
corrupt value can never break request handling — it falls back to the
default. Quota 0 means unlimited.

What a workspace's uploads are checked against (`workspace_quota`):
  - a PERSONAL workspace: its account's limits, and the account's usage is
    the uploads/ of all its personal workspaces together — nothing anyone
    puts into a shared workspace counts against a person;
  - a SHARED workspace: the server-wide per-file cap and the workspace's own
    `workspaces.quota_mb` (NULL = unlimited), which admins set.
The databases are not metered.

The module also owns the admin-confirmed public server URL (`settings` key
`public_url`, or the `GAMMA_PUBLIC_URL` override) and the MCP host allowlist
derived from it ([mcp.md](../../docs/dev/mcp.md)), and the guest settings
(`guest_ttl_hours`, how long a guest account lives, `GAMMA_GUEST_TTL_HOURS`
overriding it; `demo_mode`, `GAMMA_DEMO` overriding it —
docs/dev/guests.md). Other modules keep their
server-wide values in the same KV through ``_get_raw``/``_set_raw``: the
cloud sign-in (`cloud_*`, gamma/cloud_auth.py) and the shared AI providers
(`ai_providers`, gamma/ai_settings.py).
"""

import re
from ipaddress import IPv6Address
from urllib.parse import urlsplit

from fastapi import HTTPException

from . import config
from .config import MAX_UPLOAD_BYTES
from .db import connect_users_db, page_now, ws_uploads_dir

MB = 1024 * 1024
DEFAULT_MAX_UPLOAD_MB = MAX_UPLOAD_BYTES // MB
DEFAULT_QUOTA_MB = 0  # unlimited
# Guest accounts are minted by anyone on the internet, so each gets a bounded
# default quota (an admin can still override it per account). Applied only
# when no explicit per-user override is set.
GUEST_DEFAULT_QUOTA_MB = 200
# How long a guest account lives after it was created (gamma/guests.py).
DEFAULT_GUEST_TTL_HOURS = 24
GUEST_TTL_MIN, GUEST_TTL_MAX = 1, 720
UPLOAD_MB_MIN, UPLOAD_MB_MAX = 1, 2048
QUOTA_MB_MIN, QUOTA_MB_MAX = 0, 1024 * 1024  # 0 = unlimited, cap 1 TB


def validate_upload_mb(mb) -> int:
    mb = int(mb)
    if not UPLOAD_MB_MIN <= mb <= UPLOAD_MB_MAX:
        raise ValueError(f"upload limit must be {UPLOAD_MB_MIN}-{UPLOAD_MB_MAX} MB")
    return mb


def validate_quota_mb(mb) -> int:
    mb = int(mb)
    if not QUOTA_MB_MIN <= mb <= QUOTA_MB_MAX:
        raise ValueError(f"storage quota must be {QUOTA_MB_MIN}-{QUOTA_MB_MAX} MB (0 = unlimited)")
    return mb


def _parse(raw, default: int, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if lo <= value <= hi else default


def _get_raw(key: str) -> str:
    with connect_users_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else ""


def _set_raw(key: str, value: str) -> None:
    with connect_users_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, page_now()),
        )
        conn.commit()


# Hosts that may use plain HTTP: the machine itself.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def validate_public_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        port = url.port
        local = host in LOOPBACK_HOSTS
        if (len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value)
                or url.scheme not in {"http", "https"} or not host
                or (url.scheme != "https" and not local)
                or url.username is not None or url.password is not None
                or url.path not in ("", "/") or "?" in value or "#" in value
                or (port is not None and port < 1)):
            raise ValueError
        if ":" in host:
            host = str(IPv6Address(host))
            if "%" in host:
                raise ValueError
        else:
            host = host.encode("idna").decode("ascii")
            if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                          for label in host.split(".")):
                raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port is not None and port != (443 if url.scheme == "https" else 80):
            authority += f":{port}"
        return f"{url.scheme}://{authority}"
    except (ValueError, UnicodeError):
        raise ValueError("Enter an HTTPS server address without a path, query, or fragment. HTTP is allowed only for localhost.") from None


def public_url_settings() -> dict:
    override = config.public_url_override().rstrip("/")
    with connect_users_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'public_url'").fetchone()
    saved = row[0] if row else ""
    return {"public_url": override or saved,
            "public_url_source": "environment" if override else "saved" if saved else "unset"}


def set_public_url(value: str) -> None:
    if config.public_url_override():
        raise ValueError("The public server URL is managed by GAMMA_PUBLIC_URL on this server.")
    _set_raw("public_url", validate_public_url(value))


def mcp_allowed_hosts(public_url: str | None = None) -> list[str]:
    hosts = config.mcp_extra_hosts()
    if public_url is None:
        public_url = public_url_settings()["public_url"]
    if public_url:
        try:
            hosts.append(urlsplit(validate_public_url(public_url)).netloc)
        except ValueError:
            pass  # An invalid configured URL must never widen the allowlist.
    return ["127.0.0.1", "localhost", "[::1]", "127.0.0.1:*", "localhost:*", "[::1]:*", *hosts]


def validate_guest_ttl_hours(hours) -> int:
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = 0
    if not GUEST_TTL_MIN <= hours <= GUEST_TTL_MAX:
        raise ValueError(f"the guest lifetime must be {GUEST_TTL_MIN}-{GUEST_TTL_MAX} hours")
    return hours


def guest_ttl_settings() -> dict:
    """``{guest_ttl_hours, guest_ttl_source}``: ``GAMMA_GUEST_TTL_HOURS``
    ("environment") when it holds a valid number, else the saved value
    ("saved"), else the default ("default")."""
    raw = config.guest_ttl_override()
    if raw:
        try:
            return {"guest_ttl_hours": validate_guest_ttl_hours(raw), "guest_ttl_source": "environment"}
        except ValueError:
            pass  # an invalid override is ignored, like an unset one
    saved = _get_raw("guest_ttl_hours")
    if saved:
        return {"guest_ttl_hours": _parse(saved, DEFAULT_GUEST_TTL_HOURS, GUEST_TTL_MIN, GUEST_TTL_MAX),
                "guest_ttl_source": "saved"}
    return {"guest_ttl_hours": DEFAULT_GUEST_TTL_HOURS, "guest_ttl_source": "default"}


def guest_ttl_hours() -> int:
    return guest_ttl_settings()["guest_ttl_hours"]


def set_guest_ttl_hours(hours) -> None:
    hours = validate_guest_ttl_hours(hours)
    if guest_ttl_settings()["guest_ttl_source"] == "environment":
        raise ValueError("The guest lifetime is managed by GAMMA_GUEST_TTL_HOURS on this server.")
    _set_raw("guest_ttl_hours", str(hours))


def demo_settings() -> dict:
    """``{demo_mode, demo_mode_source}``: on when ``GAMMA_DEMO`` is truthy
    ("environment"), else the saved switch ("saved" / "default")."""
    if config.demo_override():
        return {"demo_mode": True, "demo_mode_source": "environment"}
    saved = _get_raw("demo_mode")
    return {"demo_mode": saved == "1", "demo_mode_source": "saved" if saved else "default"}


def demo_mode() -> bool:
    return demo_settings()["demo_mode"]


def set_demo_mode(on: bool) -> None:
    if config.demo_override():
        raise ValueError("Demo mode is turned on by GAMMA_DEMO on this server.")
    _set_raw("demo_mode", "1" if on else "")


def guest_settings() -> dict:
    """The admin Settings rows: guest lifetime and demo mode with sources."""
    return {**guest_ttl_settings(), **demo_settings()}


def _defaults(conn) -> tuple[int, int]:
    rows = dict(conn.execute("SELECT key, value FROM settings WHERE key IN ('max_upload_mb', 'quota_mb')"))
    return (_parse(rows.get("max_upload_mb"), DEFAULT_MAX_UPLOAD_MB, UPLOAD_MB_MIN, UPLOAD_MB_MAX),
            _parse(rows.get("quota_mb"), DEFAULT_QUOTA_MB, QUOTA_MB_MIN, QUOTA_MB_MAX))


def get_defaults() -> dict:
    with connect_users_db() as conn:
        upload_mb, quota_mb = _defaults(conn)
    return {"max_upload_mb": upload_mb, "quota_mb": quota_mb}


def set_default_max_upload_mb(mb: int) -> None:
    _set_raw("max_upload_mb", str(validate_upload_mb(mb)))


def set_default_quota_mb(mb: int) -> None:
    _set_raw("quota_mb", str(validate_quota_mb(mb)))


def user_limits(username: str) -> dict:
    """Effective limits for an account: per-user override, else server default."""
    with connect_users_db() as conn:
        default_upload, default_quota = _defaults(conn)
        row = conn.execute("SELECT max_upload_mb, quota_mb, is_guest FROM users WHERE username = ?",
                           (username,)).fetchone()
    upload_override = row[0] if row else None
    quota_override = row[1] if row else None
    # A guest account falls back to a bounded quota rather than the (often
    # unlimited) server default, unless an admin has set an explicit override.
    if row and row[2] and quota_override is None and default_quota == 0:
        default_quota = GUEST_DEFAULT_QUOTA_MB
    return {
        "max_upload_mb": _parse(upload_override, default_upload, UPLOAD_MB_MIN, UPLOAD_MB_MAX),
        "quota_mb": _parse(quota_override, default_quota, QUOTA_MB_MIN, QUOTA_MB_MAX),
    }


def workspace_bytes(ws: str) -> int:
    """Upload bytes stored in one workspace."""
    try:
        uploads = ws_uploads_dir(ws)
    except ValueError:
        return 0
    if not uploads.exists():
        return 0
    return sum(f.stat().st_size for f in uploads.iterdir() if f.is_file())


def usage_bytes(username: str) -> int:
    """Upload bytes that count against an account: its personal workspaces."""
    from . import workspaces  # local: workspaces imports seed → db

    return sum(workspace_bytes(ws) for ws in workspaces.personal_workspaces(username))


def workspace_quota(ws: str) -> dict:
    """The limits and usage that apply to uploads into ``ws``:
    ``{max_upload_mb, quota_mb, used_bytes, workspace_bytes, account}`` —
    ``account`` is the person whose limits these are (a personal
    workspace), "" for a shared workspace under its own quota."""
    from . import workspaces

    used = workspace_bytes(ws)
    owner = workspaces.personal_owner(ws)
    if owner:
        return {**user_limits(owner), "used_bytes": usage_bytes(owner), "workspace_bytes": used, "account": owner}
    info = workspaces.get(ws) or {}
    with connect_users_db() as conn:
        default_upload, _default_quota = _defaults(conn)
    return {"max_upload_mb": default_upload,
            "quota_mb": _parse(info.get("quota_mb"), 0, QUOTA_MB_MIN, QUOTA_MB_MAX),
            "used_bytes": used, "workspace_bytes": used, "account": ""}


def check_upload_allowed(ws: str, nbytes: int) -> None:
    """Hard gate for explicit uploads into a workspace: 413 over the
    per-file cap, 507 over the quota that applies (``workspace_quota``).

    Callers should skip this when the content hash already exists on disk —
    re-uploading a stored file costs nothing, so it is always allowed.
    """
    limits = workspace_quota(ws)
    if nbytes > limits["max_upload_mb"] * MB:
        raise HTTPException(status_code=413, detail=f"file too large (max {limits['max_upload_mb']} MB)")
    quota = limits["quota_mb"]
    if quota:
        used = limits["used_bytes"]
        if used + nbytes > quota * MB:
            raise HTTPException(
                status_code=507,
                detail=f"storage quota exceeded ({used // MB} of {quota} MB used — "
                       f"this file needs {max(1, nbytes // MB)} MB more)")


def can_store(ws: str, nbytes: int) -> bool:
    """Soft gate for best-effort caches (external-PDF save, AI re-download):
    same rules as check_upload_allowed, but the caller just skips the save."""
    try:
        check_upload_allowed(ws, nbytes)
        return True
    except HTTPException:
        return False
