"""Guest accounts: throwaway accounts that keep nothing (docs/dev/guests.md).

``POST /api/login-guest`` mints a fresh account per visitor (``new_guest``):
``guest-<8 url-safe chars>``, ``is_guest = 1``, an empty password hash, its
own personal workspace with the welcome page, and — when
``GAMMA_GUEST_SEED`` names a workspace backup zip — that zip restored into
it. A guest account lives ``guest_ttl_hours`` (gamma/server_settings.py)
after ``users.created_at``; then the session middleware treats it as signed
out and deletes it on the spot (gamma/auth.py), and ``delete_expired``, the
sweeper the app lifespan runs every ``SWEEP_INTERVAL_S`` (gamma/app.py),
deletes the ones nobody came back for. Both go through
``workspaces.delete_account``, the one account deletion.
"""

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

from . import cloud_auth, config, workspaces
from .db import connect_users_db, format_stamp, new_account_id, page_now, parse_stamp
from .logbuf import log
from .server_settings import guest_ttl_hours

SWEEP_INTERVAL_S = 600  # the app lifespan runs ``delete_expired`` at startup and this often
NAME_PREFIX = "guest-"


def logins_open() -> bool:
    """Whether this server takes guest logins: not with ``GAMMA_GUEST_MAX=0``,
    not on a hosted container (``config.guest_max``) and not on a share
    host, which holds strangers' published pages."""
    return config.guest_max() > 0 and not cloud_auth.settings()["share_host"]


def expires_at(created_at: str, ttl_hours: int | None = None) -> str:
    """When a guest account created at ``created_at`` goes (UTC ISO, the
    ``page_now`` shape); "" for an unparseable time."""
    created = parse_stamp(created_at)
    if created is None:
        return ""
    hours = guest_ttl_hours() if ttl_hours is None else ttl_hours
    return format_stamp(created + timedelta(hours=hours))


def is_expired(created_at: str, *, now: datetime | None = None, ttl_hours: int | None = None) -> bool:
    """True once a guest account created at ``created_at`` has outlived the
    guest lifetime. An unparseable time counts as expired (fail closed)."""
    created = parse_stamp(created_at)
    if created is None:
        return True
    hours = guest_ttl_hours() if ttl_hours is None else ttl_hours
    return (now or datetime.now(timezone.utc)) >= created + timedelta(hours=hours)


def account_expires_at(user_id: str) -> str:
    """``expires_at`` of a guest account by id ("" when it is not one)."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT created_at FROM users WHERE id = ? AND is_guest = 1",
                           (user_id,)).fetchone()
    return expires_at(row[0]) if row else ""


def _insert_account() -> tuple[str, str]:
    """The users row of a new guest — ``(id, username)`` —, refused with 503
    once the live guest accounts reach ``GAMMA_GUEST_MAX`` (count and insert
    in one statement, so two logins cannot both take the last place)."""
    cap = config.guest_max()
    for _ in range(5):
        user_id, name = new_account_id(), NAME_PREFIX + secrets.token_urlsafe(6)  # 6 bytes = 8 url-safe chars
        try:
            with connect_users_db() as conn:
                cur = conn.execute(
                    "INSERT INTO users (id, username, password_hash, is_guest, is_admin, created_at) "
                    "SELECT ?, ?, '', 1, 0, ? WHERE (SELECT COUNT(*) FROM users WHERE is_guest = 1) < ?",
                    (user_id, name, page_now(), cap))
                conn.commit()
        except sqlite3.IntegrityError:
            continue  # the name is taken (by another guest, or a real account)
        if cur.rowcount != 1:
            log.warning(f"[guests] a guest login was refused: {cap} guest accounts are live (GAMMA_GUEST_MAX)")
            raise HTTPException(503, "This server has as many guests as it can hold right now. "
                                     "Try again later.")
        return user_id, name
    raise HTTPException(503, "Could not create a guest account. Try again.")


def new_guest() -> tuple[str, str]:
    """Mint a guest account with its own workspace (the welcome page, then
    the ``GAMMA_GUEST_SEED`` library when one is set); returns its ``(id,
    username)``. The caller mints the session. A seed that cannot be
    restored is logged and skipped: the visitor still gets the welcome
    page."""
    user_id, name = _insert_account()
    try:
        ws = workspaces.ensure_personal(user_id, welcome=True)
    except Exception:
        workspaces.delete_account(user_id)
        raise
    seed = config.guest_seed_path()
    if seed:
        from . import ws_backup  # local: only a seeded server needs it

        try:
            ws_backup.restore_zip(ws, Path(seed), "replace")
        except Exception as e:  # a broken seed must not lock visitors out
            log.warning(f"[guests] GAMMA_GUEST_SEED {seed} could not be restored into {name}'s workspace: {e}")
    return user_id, name


def delete_expired(*, now: datetime | None = None, everyone: bool = False) -> list[str]:
    """Delete every guest account past its lifetime (``everyone``: every
    guest account); returns their names."""
    ttl = guest_ttl_hours()
    with connect_users_db() as conn:
        rows = conn.execute("SELECT id, username, created_at FROM users WHERE is_guest = 1").fetchall()
    gone = [(i, u) for i, u, created in rows if everyone or is_expired(created, now=now, ttl_hours=ttl)]
    for user_id, _name in gone:
        workspaces.delete_account(user_id)
    if gone:
        log.info(f"[guests] deleted {len(gone)} guest account(s)" + ("" if everyone else " past their lifetime"))
    return [name for _id, name in gone]
