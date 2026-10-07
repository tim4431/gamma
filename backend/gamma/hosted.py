"""Hosted containers: a Gamma server the account server runs for a paying
customer (``GAMMA_HOSTED=1``, docs/dev/cloud_accounts.md "Hosted
containers") learns its plan's limits from the account server.

``sync_now`` POSTs this server's report to ``<issuer>/api/hosted/sync``
with HTTP Basic auth by its own OIDC client (``GAMMA_CLOUD_CLIENT_ID`` /
``GAMMA_CLOUD_CLIENT_SECRET``): build and schema version, non-guest
accounts, upload bytes over every workspace, the data directory's bytes,
the confirmed public URL, and whether the server is used and failing (the
accounts active in the last week, the last write, the 5xx answers since
the last sync and the uptime; the read-only gate notes the answers through
``answered``). The answer is the limits (``plan``, ``status``,
``read_only``, ``policy``, ``max_accounts``, ``quota_mb``,
``max_upload_mb``, ``offsite``, ``grace_until``, ``message``), checked
field by field and kept with the time in the users.db ``settings`` KV
under ``hosted_limits``: the last answer survives a restart, and a call
that fails changes nothing (a warning in the log). The app's ``every()``
loop runs ``tick`` at startup and hourly; Settings → Server's Sync now
(``POST /api/admin/hosted/sync``) and ``manage.py hosted-sync`` run one.

The answer is read on every write request (the read-only gate) and by
every ``cloud_auth.settings()``, so it is kept in memory: loaded from the
KV once, on first use (``tick`` does it at startup, in a worker thread),
and replaced by ``sync_now`` and ``forget``, the only writers of the key.
A ``manage.py hosted-sync`` in another process therefore reaches a running
server at the server's own next sync.

Where the limits land:

- ``quota_mb`` / ``max_upload_mb``: the storage defaults and caps
  (gamma/server_settings.py ``_plan_caps``): an admin may set lower ones,
  never higher.
- ``policy`` replaces ``GAMMA_CLOUD_POLICY`` (``cloud_auth.settings``),
  so a plan change switches it without a restart; ``max_accounts`` caps
  the accounts a sign-in provisions and an admin creates (``account_cap``).
- ``read_only``: every state-changing ``/api/*`` request but the ones a
  person needs to sign in and leave with their data is refused with 423
  (``read_only`` and ``READ_ONLY_ALLOWED``, enforced by
  ``app.read_only_gate``).
- ``status`` / ``plan`` / ``grace_until``: the Server pane's Plan rows
  (``pane``), ``GET /api/server-config`` and the ``hosted`` notice.
- ``offsite`` (copy interval and copies kept): gamma/offsite.py
  ``settings`` holds the environment's or the saved values to it — the
  plan's where the environment sets none, else the stricter of the two
  (``offsite._held_to_plan``).

Not hosted (``GAMMA_HOSTED`` unset), or hosted but never synced, ``limits``
is None and the server runs on its own settings.
"""

import base64
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from . import cloud_auth, cloud_sync, config, db, version
from .cloud_auth import CloudAuthError
from .db import connect_pages_db, connect_users_db, format_stamp, workspace_ids, ws_dir
from .logbuf import log
from .server_settings import (QUOTA_MB_MAX, UPLOAD_MB_MAX, _get_raw, _set_raw, public_url_settings,
                              workspace_bytes)

SYNC_INTERVAL = 3600          # seconds between syncs (the app lifespan runs tick)
SYNC_PATH = "/api/hosted/sync"
SETTINGS_KEY = "hosted_limits"
LAST_WRITE_KEY = "hosted_last_write"  # the report's last_write_at, kept over a restart
ACTIVE_DAYS = 7               # the window active_accounts counts
STATUSES = ("active", "grace", "read_only", "stopped")
READ_ONLY_MESSAGE = ("This server is read-only: its plan has lapsed. You can still sign in, read and export "
                     "your data.")
# What a read-only server still takes besides GET/HEAD/OPTIONS: signing in
# and out (the cloud sign-in's link and unlink too), the export jobs a
# person leaves with their data through (the downloads themselves are
# GETs: page and folder exports, workspace backups, server snapshots), the
# file chips' page lookup (a read sent as a POST), marking notices seen and
# the admin's Sync now, by which a paid-up server comes back. An entry
# ending in "/" is a prefix, any other the whole path.
READ_ONLY_ALLOWED = (
    "/api/login",
    "/api/logout",
    "/api/auth/cloud/",
    "/api/jobs/export",
    "/api/jobs/workspace-export",
    "/api/pages/by-docs",
    "/api/notices/",
    "/api/admin/hosted/sync",
)
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_lock = threading.Lock()      # one sync at a time
_last_failure: dict = {}      # {at, error} of the newest failed attempt since the last success (memory only)
# What the gate notes for the report (``answered``), in memory, on a hosted
# server only.
_errors = 0                   # 5xx answers since the last successful sync
_last_write: float | None = None  # time.time() of this process's newest accepted write
# The stored answer in memory: (the users.db it was read from, the stored
# record or None). A users.db at another path (a test's own data
# directory) is read afresh.
_cache: tuple | None = None
_cache_lock = threading.Lock()


def enabled() -> bool:
    return config.hosted()


def _stamp(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> str:
    return _stamp(datetime.now(timezone.utc))


# --- the cached answer -------------------------------------------------------------

def _read_kv(conn=None) -> dict | None:
    if conn is None:
        raw = _get_raw(SETTINGS_KEY)
    else:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (SETTINGS_KEY,)).fetchone()
        raw = row[0] if row else ""
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        return None
    return value if isinstance(value, dict) and isinstance(value.get("limits"), dict) else None


def _stored(conn=None) -> dict | None:
    """The stored record ``{limits, synced_at, status_since}`` from memory,
    read from the KV (on ``conn`` when given) the first time."""
    global _cache
    where = str(db.USERS_DB)
    cached = _cache
    if cached is not None and cached[0] == where:
        return cached[1]
    with _cache_lock:
        if _cache is None or _cache[0] != where:
            _cache = (where, _read_kv(conn))
        return _cache[1]


def _remember(record: dict | None) -> None:
    global _cache
    _set_raw(SETTINGS_KEY, json.dumps(record) if record else "")
    with _cache_lock:
        _cache = (str(db.USERS_DB), record)


def limits(conn=None) -> dict | None:
    """The last answer (``_normalized``) with ``synced_at`` and
    ``status_since`` (when the status took its value), or None: not a
    hosted server, or never synced. From memory; ``conn``: a users.db
    connection for the first read."""
    if not enabled():
        return None
    stored = _stored(conn)
    if not stored:
        return None
    return {**stored["limits"], "synced_at": stored.get("synced_at", ""), "status_since": stored.get("status_since", "")}


def read_only() -> str:
    """The refusal of a write while the plan says read-only (the account
    server's ``message``, else ``READ_ONLY_MESSAGE``); "" while this server
    takes writes."""
    found = limits()
    if not found or not found["read_only"]:
        return ""
    return found["message"] or READ_ONLY_MESSAGE


def allowed_when_read_only(path: str) -> bool:
    return any(path.startswith(entry) if entry.endswith("/") else path == entry for entry in READ_ONLY_ALLOWED)


def policy() -> str:
    """The sign-in policy the plan sets (``cloud_auth.POLICIES``), "" when
    no plan sets one."""
    found = limits()
    return found["policy"] if found else ""


def _accounts(conn) -> int:
    """The non-guest accounts: what ``max_accounts`` counts."""
    return conn.execute("SELECT COUNT(*) FROM users WHERE is_guest = 0").fetchone()[0]


def account_cap(conn) -> str:
    """"" while the plan lets one more account be made, else the refusal."""
    found = limits(conn)
    cap = found["max_accounts"] if found else 0
    if not cap:
        return ""
    return f"This server has reached its plan's {cap} accounts." if _accounts(conn) >= cap else ""


# --- the call -------------------------------------------------------------------------

def _int(value, least: int, most: int) -> int:
    if isinstance(value, bool):
        raise ValueError("a number is a boolean")
    value = int(value)
    return min(max(value, least), most)


def _normalized(answer) -> dict:
    """The account server's answer, field by field; ValueError when it is
    not one. A missing optional field takes its "no limit" value."""
    if not isinstance(answer, dict):
        raise ValueError("the answer is not an object")
    plan = answer.get("plan")
    status = answer.get("status")
    if not isinstance(plan, str) or not plan.strip() or len(plan) > 32:
        raise ValueError("the answer names no plan")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    policy_ = answer.get("policy") or ""
    if policy_ and policy_ not in cloud_auth.POLICIES:
        raise ValueError(f"unknown policy {policy_!r}")
    offsite = answer.get("offsite") if isinstance(answer.get("offsite"), dict) else {}
    grace = answer.get("grace_until")
    message = answer.get("message")
    return {
        "plan": plan.strip(),
        "status": status,
        # a stopped or read-only server takes no writes whatever the flag says
        "read_only": bool(answer.get("read_only")) or status in ("read_only", "stopped"),
        "policy": policy_,
        "max_accounts": _int(answer.get("max_accounts") or 0, 0, 1_000_000),
        "quota_mb": _int(answer.get("quota_mb") or 0, 0, QUOTA_MB_MAX),
        "max_upload_mb": _int(answer.get("max_upload_mb") or 0, 0, UPLOAD_MB_MAX),
        # What the off-site copies are held to (offsite._held_to_plan); 0 = no plan value.
        "offsite": {"interval_s": _int(offsite.get("interval_s") or 0, 0, 7 * 86400),
                    "keep": _int(offsite.get("keep") or 0, 0, 10_000)},
        "grace_until": grace.strip()[:40] if isinstance(grace, str) and grace.strip() else None,
        "message": message.strip()[:500] if isinstance(message, str) else "",
    }


def _data_bytes() -> int:
    total = 0
    for root, _dirs, files in os.walk(config.DATA_DIR):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                pass  # removed while walking
    return total


# --- how the server is used --------------------------------------------------------

def answered(status: int, write: bool) -> None:
    """The read-only gate's note of one answer on a hosted server: a 5xx
    counts toward ``errors``, and a write (a POST, PUT, PATCH or DELETE
    under /api/) answered below 400 moves the last write. A comparison and
    an assignment in memory, on the event loop. No lock: a 5xx noted while
    a sync subtracts the ones it reported may be lost, one in an hour's
    figure."""
    global _errors, _last_write
    if status >= 500:
        _errors += 1
    elif write and status < 400:
        _last_write = time.time()


def _last_write_at() -> str | None:
    """``last_write_at``: this process's newest accepted write, else the one
    an earlier sync saved (after a restart, or from ``manage.py
    hosted-sync`` in a process of its own). Saved to the KV when it moved,
    so a restart loses at most the writes since the last sync. None when
    nothing was ever written."""
    seen = _last_write
    at = _stamp(datetime.fromtimestamp(seen, timezone.utc)) if seen else ""
    stored = _get_raw(LAST_WRITE_KEY)
    if at > stored:
        _set_raw(LAST_WRITE_KEY, at)
        stored = at
    return stored or None


def _active_accounts() -> int:
    """The non-guest accounts active in the last ``ACTIVE_DAYS`` days, from
    what the server records anyway (nothing is written to learn it): a
    sign-in (a session made then), a preference saved (account-wide, or in
    a workspace: the app saves the open tabs and the recently viewed pages
    as a person opens pages) or a page whose newest write is theirs
    (``page_changes`` keeps one writer per page, so a later edit by someone
    else hides it). Someone who only reads in a tab left open, or reads
    through an integration token, is not seen. One query on users.db and
    one on each workspace's pages.db."""
    since = format_stamp(datetime.now(timezone.utc) - timedelta(days=ACTIVE_DAYS))
    with connect_users_db() as conn:
        accounts = {row[0] for row in conn.execute("SELECT id FROM users WHERE is_guest = 0")}
        seen = {row[0] for row in conn.execute(
            "SELECT user_id FROM sessions WHERE created_at >= ? "
            "UNION SELECT user_id FROM user_prefs WHERE updated_at >= ?", (since, since))}
    for ws in workspace_ids():
        if not (ws_dir(ws) / "pages.db").is_file():
            continue
        try:
            with connect_pages_db(ws) as conn:
                seen.update(row[0] for row in conn.execute(
                    "SELECT user_id FROM workspace_prefs WHERE updated_at >= ? "
                    "UNION SELECT actor FROM page_changes WHERE at >= ?", (since, since)))
        except Exception as e:  # noqa: BLE001 — one workspace never stops the report
            log.warning(f"[hosted] workspace {ws}: {e}")
    return len(seen & accounts)


def report() -> dict:
    """The body of a sync: what the account server shows on its Servers tab.
    Besides the server's size, whether it is used and failing:
    ``active_accounts`` (``_active_accounts``), ``last_write_at``
    (``_last_write_at``), ``errors`` (the 5xx answers since the last
    successful sync) and ``uptime_s`` (this process's). Counts and times
    only: nothing about who, which page or what was written."""
    with connect_users_db() as conn:
        accounts = _accounts(conn)
    build = cloud_sync._build_report()
    return {"version": build["version"], "schema": build.get("schema"), "accounts": accounts,
            "active_accounts": _active_accounts(),
            "uploads_bytes": sum(workspace_bytes(ws) for ws in workspace_ids()),
            "data_bytes": _data_bytes(), "public_url": public_url_settings()["public_url"],
            "last_write_at": _last_write_at(), "errors": _errors,
            "uptime_s": int((datetime.now(timezone.utc) - version.STARTED_AT).total_seconds())}


def _failed(error: str) -> None:
    _last_failure.update(at=_now(), error=error)
    log.warning(f"[hosted] could not sync the plan's limits with Gamma Cloud (the last answer stands; "
                f"tried again in an hour): {error}")


def sync_now() -> dict | None:
    """One sync: the new limits (``limits``), or None when this is no
    hosted server or the call failed (a warning; the cached answer stands).
    A success takes the errors it reported off ``errors``; a failure leaves
    them for the next."""
    global _errors
    if not enabled():
        return None
    with _lock:
        cfg = cloud_auth.settings()
        secret = cloud_auth.client_secret()
        if not cfg["enabled"] or not secret or cfg["client_id"] == cloud_auth.DEFAULT_CLIENT_ID:
            _failed("GAMMA_HOSTED is set, but GAMMA_CLOUD_ISSUER, GAMMA_CLOUD_CLIENT_ID and "
                    "GAMMA_CLOUD_CLIENT_SECRET do not name this server's own client")
            return None
        basic = base64.b64encode(f"{cfg['client_id']}:{secret}".encode()).decode("ascii")
        try:
            sent = report()
            answer = _normalized(cloud_auth._http(
                cfg["issuer"] + SYNC_PATH, data=json.dumps(sent).encode(), method="POST",
                headers={"Authorization": f"Basic {basic}", "Content-Type": "application/json"}))
        except CloudAuthError as e:
            _failed(str(e))
            return None
        except (ValueError, TypeError) as e:
            _failed(f"the account server's answer is not a plan's limits: {e}")
            return None
        before = _stored()
        now = _now()
        same = before and before["limits"].get("status") == answer["status"]
        since = before.get("status_since", now) if same else now
        _remember({"limits": answer, "synced_at": now, "status_since": since})
        _last_failure.clear()
        _errors -= sent["errors"]  # any noted since the report wait for the next
        if not before or before["limits"] != answer:
            log.info(f"[hosted] plan {answer['plan']}, status {answer['status']}"
                     + (" (read-only)" if answer["read_only"] else ""))
        return limits()


def tick() -> None:
    """The app lifespan's round: one sync on a hosted server, nothing
    elsewhere. The first loads the stored answer before it calls out, so
    the read-only gate never reads it on the event loop."""
    if enabled():
        _stored()
        sync_now()


def pane() -> dict | None:
    """What Settings → Server's Plan rows show, None when not hosted: the
    last answer (``limits`` or nothing yet), the accounts there are, the
    newest failure since the last success and the account server's
    address (its /plan page)."""
    if not enabled():
        return None
    with connect_users_db() as conn:
        accounts = _accounts(conn)
        found = limits(conn)
    return {"limits": found, "accounts": accounts, "last_failure": dict(_last_failure) or None,
            "issuer": cloud_auth.settings()["issuer"]}


def forget() -> None:
    """Drop the stored answer, in the KV and in memory, the failure and
    what the report counts (the tests)."""
    global _errors, _last_write
    _remember(None)
    _set_raw(LAST_WRITE_KEY, "")
    _last_failure.clear()
    _errors, _last_write = 0, None


def reset() -> None:
    """Drop the answer in memory only: the next use reads the KV again
    (a test that wrote it itself; a restore runs with the server stopped)."""
    global _cache
    with _cache_lock:
        _cache = None
