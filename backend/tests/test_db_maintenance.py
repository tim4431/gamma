"""Every connection's pragmas, and the maintenance tick (gamma/db_maintenance.py,
docs/dev/user_db.md "Connections"): a WAL grown past the threshold is
truncated, one a reader still holds is left for the next round without a
word, ``PRAGMA optimize`` waits until a database in use is due, and idle
cached connections close."""

import os
import sqlite3
import time
from contextlib import closing

import pytest

import gamma.app  # noqa: F401 — the app wires the log buffer these tests read
from conftest import make_user
from gamma import db, db_maintenance
from gamma.logbuf import tail


@pytest.fixture(scope="module")
def ws():
    return make_user("dm_owner", "dm-pass-1234")


def _last_seq():
    seen = tail(0)
    return seen[-1]["seq"] if seen else 0


def _db_lines(seq):
    return [e["msg"] for e in tail(seq) if e["msg"].startswith("[db]")]


def _pragmas(conn) -> dict:
    return {name: conn.execute(f"PRAGMA {name}").fetchone()[0]
            for name in ("cache_size", "temp_store", "journal_size_limit", "mmap_size", "synchronous")}


def test_every_connection_gets_the_pragmas_once_it_opens(ws):
    common = {"cache_size": -db.CACHE_KIB, "temp_store": 2, "journal_size_limit": db.JOURNAL_SIZE_LIMIT,
              "synchronous": 1}  # MEMORY; NORMAL, what WAL makes safe
    for open_db, mmap in ((db.connect_users_db, 0), (lambda: db.connect_pages_db(ws), db.MMAP_BYTES),
                          (lambda: db.connect_data_db(ws), 0)):
        with open_db() as conn:
            assert _pragmas(conn) == {**common, "mmap_size": mmap}


def test_a_wal_grown_past_the_threshold_is_truncated(ws, monkeypatch):
    monkeypatch.setattr(db_maintenance, "WAL_TRUNCATE_BYTES", 1 << 20)
    wal = db.ws_db_path(ws, "data.db") + "-wal"
    blob = "x" * (3 << 20)
    with db.connect_data_db(ws) as conn:  # one transaction: no automatic checkpoint cuts it short
        conn.execute("INSERT OR REPLACE INTO page_snaps (page_id, img, at) VALUES ('dm-big', ?, 'x')", (blob,))
    assert os.path.getsize(wal) > 1 << 20
    seq = _last_seq()
    done = db_maintenance.tick()
    assert f"{ws}/data.db" in done["truncated"] and os.path.getsize(wal) == 0
    assert len(_db_lines(seq)) == 1 and f"{ws}/data.db" in _db_lines(seq)[0]
    with db.connect_data_db(ws) as again:  # the cached connection reads on
        assert again is conn
        assert again.execute("SELECT length(img) FROM page_snaps WHERE page_id = 'dm-big'").fetchone() == (len(blob),)
        again.execute("DELETE FROM page_snaps WHERE page_id = 'dm-big'")


def test_a_wal_a_reader_holds_is_left_for_the_next_round(ws, monkeypatch):
    """TRUNCATE must wait for readers on an older snapshot; the tick never
    waits (it would hold new writers back meanwhile): it gives up without a
    word, and the next round truncates."""
    monkeypatch.setattr(db_maintenance, "WAL_TRUNCATE_BYTES", 1 << 20)
    path = db.ws_db_path(ws, "data.db")
    with closing(sqlite3.connect(path)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM page_snaps").fetchone()
        with db.connect_data_db(ws) as conn:
            conn.execute("INSERT OR REPLACE INTO page_snaps (page_id, img, at) VALUES ('dm-held', ?, 'x')",
                         ("y" * (3 << 20),))
        seq = _last_seq()
        started = time.monotonic()
        done = db_maintenance.tick()
        assert time.monotonic() - started < db.BUSY_TIMEOUT_S / 2
        assert f"{ws}/data.db" not in done["truncated"] and os.path.getsize(path + "-wal") > 1 << 20
        assert not [m for m in _db_lines(seq) if f"{ws}/data.db" in m]
        assert not [e for e in tail(seq) if e["level"] == "ERROR"]
        reader.rollback()
    assert f"{ws}/data.db" in db_maintenance.tick()["truncated"]
    with db.connect_data_db(ws) as conn:
        conn.execute("DELETE FROM page_snaps WHERE page_id = 'dm-held'")


def test_optimize_is_a_no_op_while_nothing_is_due(ws, monkeypatch):
    """A database is due OPTIMIZE_EVERY_S after this process first saw it
    in use: the rounds before that run nothing and log nothing."""
    calls = []
    monkeypatch.setattr(db_maintenance, "_optimize", lambda path, ws: calls.append(path) or True)
    monkeypatch.setattr(db_maintenance, "WAL_TRUNCATE_BYTES", 1 << 62)
    with db.connect_pages_db(ws):
        pass
    seq = _last_seq()
    for _ in range(2):
        assert db_maintenance.tick()["optimized"] == []
    assert calls == [] and _db_lines(seq) == []


def test_optimize_runs_on_a_database_in_use_once_it_is_due(ws, monkeypatch):
    path = db.ws_db_path(ws, "pages.db")
    with db.connect_pages_db(ws):
        pass
    now = time.monotonic()
    for cached, _ in db.cached_files():  # every other file in use: optimized just now
        monkeypatch.setitem(db_maintenance._optimized, cached, now)
    monkeypatch.setitem(db_maintenance._optimized, path, now - db_maintenance.OPTIMIZE_EVERY_S - 1)
    seq = _last_seq()
    assert db_maintenance.tick()["optimized"] == [f"{ws}/pages.db"]
    assert any("optimized" in m and f"{ws}/pages.db" in m for m in _db_lines(seq))
    assert db_maintenance.tick()["optimized"] == []  # not again for OPTIMIZE_EVERY_S


def test_the_tick_closes_idle_connections(ws, monkeypatch):
    with db.connect_data_db(ws) as conn:
        pass
    monkeypatch.setattr(db, "CACHE_IDLE_S", 0)
    assert db_maintenance.tick()["evicted"] >= 1
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
