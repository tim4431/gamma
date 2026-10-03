"""Connections come from a cache kept per thread and database file
(db.Connection, docs/dev/user_db.md "Connections"): a ``with`` block
commits or rolls back and hands its connection back instead of closing it,
so a request pays for no open. What must hold: nothing a request opened is
left open outside the cache, the number open stays bounded however many
requests come, the next block finds the connection as a fresh one would
be, and a workspace's handles close before its directory goes — on Windows
an open handle keeps it on disk and its WAL files locked. The collector is
switched off where it counts, so nothing but the cache's own closing can
pass those tests."""

import gc
import os
import sqlite3
import threading
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from conftest import login, make_user
from gamma import db
from gamma.db import Connection, connect_data_db, connect_pages_db, connect_users_db, ws_dir

PASSWORD = "dbc-pass-1234"


@pytest.fixture(scope="module")
def member():
    make_user("dbc_member", PASSWORD)
    return login("dbc_member", PASSWORD)


@pytest.fixture
def served(member):
    """The member's session on an event loop that stays up, as uvicorn runs
    one: its worker threads, and their cached connections, go from one
    request to the next (a TestClient outside ``with`` starts a loop, and
    threads, per request)."""
    from gamma.app import app
    with TestClient(app) as c:
        c.cookies.update(member.cookies)
        yield c


@pytest.fixture(scope="module")
def ws():
    return make_user("dbc_unit", PASSWORD)


@pytest.fixture
def no_gc():
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


def _open_connections() -> list:
    """Every sqlite3 connection still open in the process."""
    return [obj for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection) and not _closed(obj)]


def _in_cache() -> set[int]:
    with db._lock:
        return {id(conn) for conn in [*db._idle.values(), *db._out]}


def _closed(conn) -> bool:
    try:
        conn.total_changes
        return False
    except sqlite3.ProgrammingError:  # closed (or another thread's raw one)
        return True


def _snaps(ws) -> set[str]:
    with closing(sqlite3.connect(db.ws_db_path(ws, "data.db"))) as other:
        return {r[0] for r in other.execute("SELECT page_id FROM page_snaps WHERE page_id LIKE 'dbc-%'")}


def test_a_with_block_commits_or_rolls_back_and_the_next_one_takes_the_same_connection(ws):
    for open_db in (connect_users_db, lambda: connect_pages_db(ws), lambda: connect_data_db(ws)):
        with open_db() as first:
            assert isinstance(first, Connection)
        with open_db() as again:
            assert again is first and not again.in_transaction
        with pytest.raises(KeyError):
            with open_db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                raise KeyError("boom")
        with open_db() as conn:
            assert conn is first and not conn.in_transaction
    with connect_data_db(ws) as conn:
        conn.execute("INSERT OR REPLACE INTO page_snaps (page_id, img, at) VALUES ('dbc-kept', '', 'x')")
    with pytest.raises(KeyError):
        with connect_data_db(ws) as conn:
            conn.execute("INSERT OR REPLACE INTO page_snaps (page_id, img, at) VALUES ('dbc-undone', '', 'x')")
            raise KeyError("boom")
    assert _snaps(ws) == {"dbc-kept"}


def test_the_next_block_finds_the_connection_as_a_fresh_one(ws):
    """An unfinished SELECT would hold its read snapshot into the next block
    (which would then read old rows, and keep a checkpoint from finishing):
    the block's cursors close at its end, and what it set on the connection
    is reset. One left with a database attached is closed, not cached."""
    with connect_data_db(ws) as conn:
        conn.executemany("INSERT OR REPLACE INTO page_snaps (page_id, img, at) VALUES (?, '', 'x')",
                         [(f"dbc-row{i}",) for i in range(5)])
    with connect_data_db(ws) as conn:
        conn.row_factory = sqlite3.Row
        unfinished = conn.execute("SELECT page_id FROM page_snaps")
        unfinished.fetchone()
        conn.set_progress_handler(lambda: 1, 1)  # one left behind would interrupt every statement
    with closing(sqlite3.connect(db.ws_db_path(ws, "data.db"))) as other:
        other.execute("INSERT OR REPLACE INTO page_snaps (page_id, img, at) VALUES ('dbc-after', '', 'x')")
        other.commit()
    with connect_data_db(ws) as again:
        assert again is conn and again.row_factory is None
        assert again.execute("SELECT 1 FROM page_snaps WHERE page_id = 'dbc-after'").fetchone() == (1,)
    with pytest.raises(sqlite3.ProgrammingError):
        unfinished.fetchone()
    with connect_data_db(ws) as conn:
        conn.execute("ATTACH DATABASE ':memory:' AS dbc_extra")
    assert _closed(conn)
    with connect_data_db(ws) as again:
        assert again is not conn


def test_requests_leave_nothing_open_outside_the_cache_and_the_count_stays_bounded(member, no_gc):
    """``member`` starts a loop and its worker threads per request, the
    worst case: every request's threads end with it, and the connections
    they cached are closed by the next acquire."""
    page = member.post("/api/pages", json={"title": "Read a lot"}).json()["id"]

    def requests():
        member.get("/api/session")
        member.get("/api/blocks/root/children")
        member.get(f"/api/blocks/{page}/subtree")
        member.get("/api/blocks/dbc-nope")           # a 404 raised inside the with block
        member.get("/api/blocks/dbc-nope/subtree")
        member.put(f"/api/chats/{page}", json={"messages": [{"role": "user", "text": "hi"}]})

    for _ in range(5):
        requests()
    settled = len(_open_connections())
    for _ in range(40):
        requests()
    still_open = _open_connections()
    cached = _in_cache()
    # background threads (the index refresher, the orphan check) may hold one or two a moment
    assert len([c for c in still_open if id(c) not in cached]) <= 3
    assert len(still_open) <= settled + 3 and len(still_open) <= db.CACHE_MAX_IDLE + 3


def test_requests_reuse_the_cached_connections(served, monkeypatch):
    page = served.post("/api/pages", json={"title": "Read again"}).json()["id"]
    for _ in range(3):
        served.get(f"/api/blocks/{page}/subtree")
    opened = []
    real = db._new_connection

    def counting(path, ws):
        if threading.current_thread().name.startswith("AnyIO"):  # a request's, not a background pass's
            opened.append(path)
        return real(path, ws)

    monkeypatch.setattr(db, "_new_connection", counting)
    for _ in range(20):
        assert served.get("/api/session").status_code == 200
        assert served.get(f"/api/blocks/{page}/subtree").status_code == 200
    # opening per request, these 40 requests opened users.db 40 times and pages.db 20
    assert len(opened) <= 4, opened


def test_a_block_inside_a_block_on_the_same_file_gets_a_connection_of_its_own(ws):
    """Sharing the thread's connection would let the inner block's commit
    end the outer block's transaction (and its rollback undo it)."""
    with connect_data_db(ws) as outer:
        outer.execute("BEGIN IMMEDIATE")
        outer.execute("INSERT OR REPLACE INTO page_snaps (page_id, img, at) VALUES ('dbc-nested', '', 'x')")
        with connect_data_db(ws) as inner:
            assert inner is not outer
            assert inner.execute("SELECT 1 FROM page_snaps WHERE page_id = 'dbc-nested'").fetchone() is None
        assert outer.in_transaction and _closed(inner)
        outer.rollback()
    with connect_data_db(ws) as again:
        assert again is outer
    assert "dbc-nested" not in _snaps(ws)


def test_idle_connections_close_after_a_while(ws, monkeypatch):
    with connect_data_db(ws) as conn:
        pass
    db.evict_idle()
    assert not _closed(conn)
    with monkeypatch.context() as m:
        m.setattr(db, "CACHE_IDLE_S", 0)  # as if it had been idle that long
        db.evict_idle()
    assert _closed(conn)
    # and any acquire closes what is due
    with connect_data_db(ws) as conn:
        pass
    monkeypatch.setattr(db, "CACHE_IDLE_S", 0)
    with connect_users_db():
        pass
    assert _closed(conn)


def test_a_thread_keeps_few_files_and_its_connections_close_when_it_ends(ws, monkeypatch):
    monkeypatch.setattr(db, "CACHE_FILES_PER_THREAD", 2)
    opened, checked, done = {}, threading.Event(), threading.Event()

    def worker():
        for name, open_db in (("users", connect_users_db), ("pages", lambda: connect_pages_db(ws)),
                              ("data", lambda: connect_data_db(ws))):
            with open_db() as conn:
                opened[name] = conn
        checked.set()
        done.wait(10)

    t = threading.Thread(target=worker)
    t.start()
    assert checked.wait(10)
    try:
        # the third file pushed out the least recently used
        assert _closed(opened["users"]) and not _closed(opened["pages"]) and not _closed(opened["data"])
    finally:
        done.set()
        t.join(10)
    db.evict_idle()
    assert _closed(opened["pages"]) and _closed(opened["data"])


def test_a_file_replaced_behind_the_cache_is_opened_afresh(ws):
    with connect_data_db(ws) as conn:
        pass
    conn._file = (-1, -1)  # as if another file now had the path (on Windows the open handle forbids it)
    with connect_data_db(ws) as again:
        assert again is not conn
    assert _closed(conn)


def test_closing_a_workspaces_connections_reaches_every_thread(ws):
    opened, ready, done = {}, threading.Event(), threading.Event()

    def worker():
        with connect_pages_db(ws) as conn:
            opened["idle"] = conn
        ready.set()
        done.wait(10)

    t = threading.Thread(target=worker)
    t.start()
    assert ready.wait(10)
    try:
        with connect_pages_db(ws) as in_use:
            db.close_workspace_connections(ws)
            assert _closed(opened["idle"])  # another thread's idle one: closed at once
            assert in_use.execute("SELECT 1").fetchone() == (1,)  # one in a block: until its end
        assert _closed(in_use)
    finally:
        done.set()
        t.join(10)


def test_a_workspace_deleted_right_after_use_leaves_nothing_behind(served, no_gc):
    ws = served.post("/api/workspaces", json={"name": "Gone at once"}).json()["id"]
    h = {"X-Gamma-Workspace": ws}
    page = served.post("/api/pages", json={"title": "Briefly here"}, headers=h).json()["id"]
    for _ in range(3):
        served.get(f"/api/blocks/{page}/subtree", headers=h)
        served.get("/api/blocks/dbc-nope", headers=h)
        served.put(f"/api/chats/{page}", json={"messages": [{"role": "user", "text": "x"}]}, headers=h)
        served.post(f"/api/pages/{page}/ops", headers=h, json={
            "client": "dbc", "ops": [{"op": "set", "id": page, "content": "Renamed"}]})
    with connect_data_db(ws):
        pass
    folder = str(ws_dir(ws))

    def handles():
        return [c for c in _open_connections() if os.path.dirname(getattr(c, "_path", "")) == folder]

    assert handles()  # the cache holds the workspace's files open
    r = served.delete(f"/api/workspaces/{ws}")
    assert r.status_code == 200, r.text
    assert r.json()["warning"] == "" and not ws_dir(ws).exists()
    assert handles() == []


def test_the_startup_pass_opens_every_workspace_past_the_cache(data_dir):
    from gamma import app as app_mod
    from gamma.seed import create_workspace_files
    for n in range(3):
        create_workspace_files(f"dbcStartup{n}")
    mine = db._thread_connections()
    before = set(mine.idle)
    app_mod._startup_maintenance()
    assert set(mine.idle) - before <= {str(db.USERS_DB)}
    assert not [c for c in _open_connections() if "dbcStartup" in getattr(c, "_path", "")]
