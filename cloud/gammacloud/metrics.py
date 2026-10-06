"""The fleet's history (docs/dev/hosted.md "History"): one sample per
host, server or container and hour in the ``metrics`` table, from the
agent's heartbeats and the containers' syncs, kept 30 days. The Servers
tab draws a server's; the Machines tab shows a host's latest CPU figure.
Counts and sizes only, never anything of a library.

``kind`` is ``host``, ``server`` or ``container``, ``ref`` its id (a
container's is ``<host id>:<name>``), ``at`` the start of the hour,
``data`` a JSON object of numbers. Every report in an hour merges into that
hour's row: a gauge keeps the last value, and a count of events since the
report before (``SUMMED``) adds up.
"""

import json

from . import db

KINDS = ("host", "server", "container")
SUMMED = ("errors",)
KEEP_DAYS = 30
MAX_HOURS = 720


def hour(ts: str = "") -> str:
    """The start of the hour ``ts`` (now when empty) falls in, as a
    ``db.now()`` timestamp."""
    return (ts or db.now())[:13] + ":00:00.000Z"


def _data(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def record(conn, kind: str, ref: str, data: dict) -> None:
    """Merge ``data`` into the row of the current hour, inside the caller's
    transaction. A value that is None (not known this time) is left out."""
    data = {k: v for k, v in data.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if not data:
        return
    at = hour()
    row = conn.execute("SELECT data FROM metrics WHERE kind = ? AND ref = ? AND at = ?", (kind, ref, at)).fetchone()
    merged = _data(row["data"]) if row else {}
    for k, v in data.items():
        had = merged.get(k)
        merged[k] = had + v if k in SUMMED and isinstance(had, (int, float)) else v
    conn.execute("INSERT INTO metrics (kind, ref, at, data) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (kind, ref, at) DO UPDATE SET data = excluded.data", (kind, ref, at, json.dumps(merged)))


def _points(rows) -> list[dict]:
    return [{"at": r["at"], **_data(r["data"])} for r in rows]


def series(conn, kind: str, ref: str, hours: int) -> list[dict]:
    """The samples of the last ``hours`` hours (this one included), oldest
    first, each ``{at, ...}``; an hour with no report has no point."""
    since = hour(db.after(-hours * 3600))
    return _points(conn.execute("SELECT at, data FROM metrics WHERE kind = ? AND ref = ? AND at > ? ORDER BY at",
                                (kind, ref, since)).fetchall())


def trends(conn, kind: str, keys: tuple[str, ...], hours: int = 48) -> dict[str, list[dict]]:
    """Per ``ref`` of ``kind``: ``series`` of the last ``hours`` with only
    ``keys``, in one read, for the rows of a table."""
    since = hour(db.after(-hours * 3600))
    out: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT ref, at, data FROM metrics WHERE kind = ? AND at > ? ORDER BY ref, at",
                          (kind, since)).fetchall():
        data = _data(r["data"])
        out.setdefault(r["ref"], []).append({"at": r["at"], **{k: data[k] for k in keys if k in data}})
    return out


def latest(conn, kind: str) -> dict[str, dict]:
    """Per ``ref`` of ``kind``: its newest sample, ``{at, ...}``, in one
    read (SQLite takes ``data`` from the row ``MAX`` picks)."""
    return {r["ref"]: {"at": r["at"], **_data(r["data"])} for r in conn.execute(
        "SELECT ref, MAX(at) AS at, data FROM metrics WHERE kind = ? GROUP BY ref", (kind,)).fetchall()}


def purge(conn, days: int = KEEP_DAYS) -> None:
    """Samples older than ``days`` go (from the hourly tick)."""
    conn.execute("DELETE FROM metrics WHERE at < ?", (hour(db.after(-days * 86400)),))
