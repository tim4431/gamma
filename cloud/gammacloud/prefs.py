"""The preference profile: a person's settings as a few JSON values the
Gamma servers they sign in to pull and push, so the settings follow them.

One row per (account, key) in ``prefs``. ``updated_at`` is the version:
per-key last-writer-wins by timestamp. A write may carry the time the
change was made on the Gamma side; one older than the stored value is
refused with the stored value, so the writer takes the newer side. A
missing time means "now". A time in the future is clamped to now, so a
server with a fast clock cannot pin a value for good. Deletion is not
versioned: the key is gone, and a server still holding an older copy may
write it back.

Nothing here is audited: a preference change is not an account change.
"""

import json
import re
from datetime import datetime, timezone

from .accounts import Problem
from .db import begin_write, now

KEY_RE = re.compile(r"^[a-z0-9-]{1,40}$")
MAX_VALUE_BYTES = 64 * 1024
MAX_KEYS = 20


class Conflict(Exception):
    """The stored value is newer than the one being written."""

    def __init__(self, stored: dict):
        super().__init__("newer value stored")
        self.stored = stored


def check_key(key: str) -> str:
    if not KEY_RE.match(key or ""):
        raise Problem(400, "A preference key is 1 to 40 lowercase letters, digits and hyphens.")
    return key


def norm_time(raw) -> str:
    """A client's timestamp in the database's fixed-width UTC form
    (``2026-09-24T10:00:00.000Z``). Any ISO 8601 time with a zone, or
    without one (taken as UTC), is accepted; later than now becomes now."""
    if not isinstance(raw, str) or len(raw) > 40:
        raise Problem(400, "updated_at must be an ISO 8601 UTC time.")
    try:
        t = datetime.fromisoformat(raw.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise Problem(400, "updated_at must be an ISO 8601 UTC time.") from None
    t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
    ts = t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return min(ts, now())


def encode(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode()) > MAX_VALUE_BYTES:
        raise Problem(413, f"A preference value is at most {MAX_VALUE_BYTES // 1024} KB.")
    return raw


def listing(conn, account_id: str) -> list[dict]:
    rows = conn.execute("SELECT key, updated_at FROM prefs WHERE account_id = ? ORDER BY key", (account_id,)).fetchall()
    return [{"key": r["key"], "updated_at": r["updated_at"]} for r in rows]


def get(conn, account_id: str, key: str) -> dict | None:
    row = conn.execute("SELECT value, updated_at FROM prefs WHERE account_id = ? AND key = ?",
                       (account_id, key)).fetchone()
    return {"value": json.loads(row["value"]), "updated_at": row["updated_at"]} if row else None


def put(conn, account_id: str, key: str, value, updated_at=None) -> str:
    """Store a value; returns the stored ``updated_at``. Raises ``Conflict``
    when ``updated_at`` is older than the stored one. Takes the write lock
    before reading, so the comparison and the write are one step."""
    check_key(key)
    raw = encode(value)
    ts = norm_time(updated_at) if updated_at is not None else now()
    begin_write(conn)
    row = conn.execute("SELECT value, updated_at FROM prefs WHERE account_id = ? AND key = ?",
                       (account_id, key)).fetchone()
    if row:
        if ts < row["updated_at"]:
            raise Conflict({"value": json.loads(row["value"]), "updated_at": row["updated_at"]})
    elif conn.execute("SELECT COUNT(*) FROM prefs WHERE account_id = ?", (account_id,)).fetchone()[0] >= MAX_KEYS:
        raise Problem(400, f"An account keeps at most {MAX_KEYS} preference keys.")
    conn.execute("INSERT INTO prefs (account_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (account_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (account_id, key, raw, ts))
    return ts


def delete(conn, account_id: str, key: str) -> bool:
    check_key(key)
    return bool(conn.execute("DELETE FROM prefs WHERE account_id = ? AND key = ?", (account_id, key)).rowcount)
