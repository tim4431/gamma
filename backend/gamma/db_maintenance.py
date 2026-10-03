"""Upkeep of the SQLite files, run by the app's ``every()`` loop
(docs/dev/user_db.md "Connections"). ``tick`` closes the idle cached
connections due to go (``db.evict_idle``), checkpoints and truncates a WAL
that grew past WAL_TRUNCATE_BYTES (a reader kept the automatic checkpoints
from finishing, or one large transaction grew it), and runs ``PRAGMA
optimize`` on each database in use at most every OPTIMIZE_EVERY_S. It looks
at file sizes and at the connection cache and never opens every workspace:
with thousands of them a round costs a stat or two each.
"""

import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from . import db
from .logbuf import log

EVERY_S = 300                  # the app lifespan runs ``tick`` at startup and this often
WAL_TRUNCATE_BYTES = 16 << 20  # a WAL larger than this is checkpointed and truncated to nothing
OPTIMIZE_EVERY_S = 4 * 3600    # per database: this long after it is first seen in use, then this often
ANALYSIS_LIMIT = 1000          # rows ANALYZE reads per index, so an optimize takes milliseconds

# path -> monotonic time of its last optimize, or of when this process first
# saw it in use (a short-lived process never optimizes)
_optimized: dict[str, float] = {}
_optimized_lock = threading.Lock()


def tick() -> dict:
    """One round. Returns what it did: ``evicted`` (idle connections
    closed), ``truncated`` and ``optimized`` (the databases, ``users.db`` or
    ``<workspace>/<file>``). Logs one line when it truncated or optimized
    anything; a round that only closed idle connections is not worth one."""
    evicted = db.evict_idle()
    truncated = [_label(path, ws) for path, ws in _databases()
                 if _wal_size(path) > WAL_TRUNCATE_BYTES and truncate_wal(path)]
    optimized = [_label(path, ws) for path, ws in _due() if _optimize(path, ws)]
    if truncated or optimized:
        done = ([f"truncated the WAL of {', '.join(truncated)}"] if truncated else []) + \
               ([f"optimized {', '.join(optimized)}"] if optimized else [])
        log.info(f"[db] {'; '.join(done)} ({evicted} idle connection(s) closed)")
    return {"evicted": evicted, "truncated": truncated, "optimized": optimized}


def _databases() -> list[tuple[str, str]]:
    """``(path, ws)`` of users.db and of every workspace's two databases."""
    return [(str(db.USERS_DB), "")] + [(db.ws_db_path(ws, name), ws)
                                       for ws in db.workspace_ids() for name in ("pages.db", "data.db")]


def _label(path: str, ws: str) -> str:
    return f"{ws}/{os.path.basename(path)}" if ws else "users.db"


def _wal_size(path: str) -> int:
    try:
        return os.stat(path + "-wal").st_size
    except OSError:
        return 0


def truncate_wal(path: str) -> bool:
    """``PRAGMA wal_checkpoint(TRUNCATE)`` on a connection of its own that
    never waits: a writer at work, or a reader still on an older snapshot,
    makes it give up at once (the next round tries again) instead of holding
    new writers back while it waits. Whether the WAL was emptied."""
    try:
        uri = Path(path).resolve().as_uri() + "?mode=rw"  # never creates a file a delete just took away
        with closing(sqlite3.connect(uri, uri=True, timeout=0)) as conn:
            busy, _frames, _copied = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return not busy
    except sqlite3.Error:
        return False


def _due() -> list[tuple[str, str]]:
    """The databases the cache holds a connection to whose last optimize
    (or first sighting) is OPTIMIZE_EVERY_S ago."""
    now = time.monotonic()
    with _optimized_lock:
        return [(path, ws) for path, ws in db.cached_files()
                if now - _optimized.setdefault(path, now) >= OPTIMIZE_EVERY_S]


def _optimize(path: str, ws: str) -> bool:
    """``PRAGMA optimize`` on a ``db.connect_*`` connection, closed after it
    (past the cache): ANALYZE where the query planner's statistics are missing or
    stale, over every table (0x10000; SQLite before 3.46 looks only at the
    tables the connection queried, so it finds nothing to do), each index
    read for at most ANALYSIS_LIMIT rows. A locked or vanished file is
    passed by until the next round."""
    if not os.path.exists(path):
        return False
    try:
        if not ws:
            conn = db.connect_users_db()
        elif os.path.basename(path) == "pages.db":
            conn = db.connect_pages_db(ws)
        else:
            conn = db.connect_data_db(ws)
        with closing(conn):
            conn.execute(f"PRAGMA analysis_limit = {ANALYSIS_LIMIT}")
            conn.execute("PRAGMA optimize = 0x10002")
    except (sqlite3.Error, OSError, ValueError):
        return False
    with _optimized_lock:
        _optimized[path] = time.monotonic()
    return True
