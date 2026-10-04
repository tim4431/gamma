"""users.db and a workspace's data.db are WAL like pages.db, and every
connection waits db.BUSY_TIMEOUT_S for a writer's lock: a reader never
waits on a writer (a search rebuilding the notes index, a chat save), and a
writer waits long enough for the short index transactions to finish."""

import sqlite3
from contextlib import closing

import pytest

from conftest import account_of, make_user
from gamma import auth, db
from gamma.db import BUSY_TIMEOUT_S, connect_data_db, connect_pages_db, connect_users_db

USER = "wal_owner"


@pytest.fixture(scope="module")
def ws():
    return make_user(USER, "pw-wal-1")


def test_every_database_is_wal_with_the_busy_timeout(ws):
    assert BUSY_TIMEOUT_S >= 10
    for open_db in (connect_users_db, lambda: connect_data_db(ws), lambda: connect_pages_db(ws)):
        with closing(open_db()) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_S * 1000


def test_readers_do_not_wait_on_a_writer(ws):
    """Even an EXCLUSIVE writer (what a rollback journal takes to commit)
    leaves readers reading: they are answered at once, with no wait."""
    for path in (str(db.USERS_DB), db.ws_db_path(ws, "data.db")):
        with closing(sqlite3.connect(path)) as writer, closing(sqlite3.connect(path, timeout=0)) as reader:
            writer.execute("BEGIN EXCLUSIVE")
            assert reader.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
            writer.rollback()


def test_the_request_path_reads_users_db_with_the_timeout(monkeypatch):
    """The session middleware's reads (``auth`` through
    ``db.connect_users_db``) take the cached users.db connections, which
    have WAL and the busy timeout like every other."""
    seen = []
    real = auth.connect_users_db

    def users_db():
        conn = real()
        seen.append((conn.execute("PRAGMA journal_mode").fetchone()[0],
                     conn.execute("PRAGMA busy_timeout").fetchone()[0]))
        return conn

    monkeypatch.setattr(auth, "connect_users_db", users_db)
    auth.session_lookup("no-such-token")
    auth.share_lookup("no-such-ws.no-such-share")  # the token's shape, so the lookup reaches users.db
    assert seen == [("wal", BUSY_TIMEOUT_S * 1000)] * 2


def test_a_new_workspace_is_wal_before_anyone_opens_it():
    """Switching a file to WAL needs it to itself: requests opening a fresh
    workspace's databases together must never be the ones to switch it."""
    from gamma import workspaces
    ws = workspaces.create("WAL from the start", account_of(USER))["id"]
    for name in ("pages.db", "data.db"):
        with closing(sqlite3.connect(db.ws_db_path(ws, name))) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal", name


def test_a_rollback_file_opened_while_in_use_is_switched_later(ws):
    """A file still in rollback mode that another connection has open can't
    be switched (SQLite says "database is locked" at once, no busy wait):
    the connection works in the file's mode, and one opening it alone
    switches it. The one that could not is closed at the end of its block,
    not cached, so the next one opens the file afresh and tries again."""
    db.close_workspace_connections(ws)  # leaving the file to the test's own connections
    path = db.ws_db_path(ws, "data.db")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
    with closing(sqlite3.connect(path)) as holder:
        holder.execute("BEGIN")
        holder.execute("SELECT count(*) FROM sqlite_master").fetchone()
        with connect_data_db(ws) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert conn.execute("SELECT count(*) FROM page_snaps").fetchone()[0] >= 0
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        holder.rollback()
    with connect_data_db(ws) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
