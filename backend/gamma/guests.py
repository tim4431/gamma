"""Guest accounts: throwaway accounts that keep nothing (docs/dev/guests.md).

``POST /api/login-guest`` mints a fresh account per visitor (``new_guest``):
``guest-<8 url-safe chars>``, ``is_guest = 1``, an empty password hash, its
own personal workspace with the welcome page, and — when
``GAMMA_GUEST_SEED`` names a workspace backup zip — that zip restored into
it. A guest account lives ``guest_ttl_hours`` (gamma/server_settings.py)
after ``users.created_at``; then the session middleware treats it as signed
out and deletes it on the spot (gamma/auth.py), and ``lifespan``'s sweeper
deletes the ones nobody came back for, every ``SWEEP_INTERVAL_S``. Both go
through ``workspaces.delete_account``, the one account deletion.
"""

import asyncio
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

from . import config, workspaces
from .db import connect_users_db, page_now
from .logbuf import log
from .server_settings import guest_ttl_hours

SWEEP_INTERVAL_S = 600
NAME_PREFIX = "guest-"


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def expires_at(created_at: str, ttl_hours: int | None = None) -> str:
    """When a guest account created at ``created_at`` goes (UTC ISO, the
    ``page_now`` shape); "" for an unparseable time."""
    created = _parse(created_at)
    if created is None:
        return ""
    hours = guest_ttl_hours() if ttl_hours is None else ttl_hours
    return (created + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def is_expired(created_at: str, *, now: datetime | None = None, ttl_hours: int | None = None) -> bool:
    """True once a guest account created at ``created_at`` has outlived the
    guest lifetime. An unparseable time counts as expired (fail closed)."""
    created = _parse(created_at)
    if created is None:
        return True
    hours = guest_ttl_hours() if ttl_hours is None else ttl_hours
    return (now or _now()) >= created + timedelta(hours=hours)


def account_expires_at(username: str) -> str:
    """``expires_at`` of a guest account by name ("" when it is not one)."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT created_at FROM users WHERE username = ? AND is_guest = 1",
                           (username,)).fetchone()
    return expires_at(row[0]) if row else ""


def _insert_account() -> str:
    """The users row of a new guest, refused with 503 once the live guest
    accounts reach ``GAMMA_GUEST_MAX`` (count and insert in one statement,
    so two logins cannot both take the last place)."""
    cap = config.guest_max()
    for _ in range(5):
        name = NAME_PREFIX + secrets.token_urlsafe(6)  # 6 bytes = 8 url-safe chars
        try:
            with connect_users_db() as conn:
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, is_guest, is_admin, created_at) "
                    "SELECT ?, '', 1, 0, ? WHERE (SELECT COUNT(*) FROM users WHERE is_guest = 1) < ?",
                    (name, page_now(), cap))
                conn.commit()
        except sqlite3.IntegrityError:
            continue  # the name is taken (by another guest, or a real account)
        if cur.rowcount != 1:
            log.warning(f"[guests] a guest login was refused: {cap} guest accounts are live (GAMMA_GUEST_MAX)")
            raise HTTPException(503, "This server has as many guests as it can hold right now. "
                                     "Try again later.")
        return name
    raise HTTPException(503, "Could not create a guest account. Try again.")


def new_guest() -> str:
    """Mint a guest account with its own workspace (the welcome page, then
    the ``GAMMA_GUEST_SEED`` library when one is set); returns its name. The
    caller mints the session. A seed that cannot be restored is logged and
    skipped: the visitor still gets the welcome page."""
    name = _insert_account()
    try:
        ws = workspaces.ensure_personal(name, welcome=True)
    except Exception:
        workspaces.delete_account(name)
        raise
    seed = config.guest_seed_path()
    if seed:
        from . import ws_backup  # local: only a seeded server needs it

        try:
            ws_backup.restore_zip(ws, Path(seed), "replace")
        except Exception as e:  # a broken seed must not lock visitors out
            log.warning(f"[guests] GAMMA_GUEST_SEED {seed} could not be restored into {name}'s workspace: {e}")
    return name


def delete_expired(*, now: datetime | None = None, everyone: bool = False) -> list[str]:
    """Delete every guest account past its lifetime (``everyone``: every
    guest account); returns their names."""
    ttl = guest_ttl_hours()
    with connect_users_db() as conn:
        rows = conn.execute("SELECT username, created_at FROM users WHERE is_guest = 1").fetchall()
    gone = [u for u, created in rows if everyone or is_expired(created, now=now, ttl_hours=ttl)]
    for username in gone:
        workspaces.delete_account(username)
    if gone:
        log.info(f"[guests] deleted {len(gone)} guest account(s)" + ("" if everyone else " past their lifetime"))
    return gone


@asynccontextmanager
async def lifespan():
    """The sweeper: ``delete_expired`` at startup and every
    ``SWEEP_INTERVAL_S`` while the app runs."""
    stop = asyncio.Event()

    async def loop():
        while not stop.is_set():
            try:
                await asyncio.to_thread(delete_expired)
            except Exception:
                log.exception("[guests] sweep failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=SWEEP_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        stop.set()
        await task
