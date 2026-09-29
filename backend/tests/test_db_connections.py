"""Connections are closed when their ``with`` block ends, not when the
garbage collector gets round to them (db.Connection). sqlite3's own
Connection only commits there; one a traceback or a reference cycle held
stayed open until the cyclic collector ran — hundreds after a burst of
requests, and on Windows an open handle keeps a workspace directory from
being deleted and its WAL files locked. The collector is switched off in
these tests, so nothing but the closing can pass them."""

import gc
import sqlite3

import pytest

from conftest import login, make_user
from gamma.db import Connection, connect_data_db, connect_pages_db, connect_users_db, ws_dir

PASSWORD = "dbc-pass-1234"


@pytest.fixture(scope="module")
def member():
    make_user("dbc_member", PASSWORD)
    return login("dbc_member", PASSWORD)


@pytest.fixture
def no_gc():
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


def _open_connections() -> int:
    count = 0
    for obj in gc.get_objects():
        if isinstance(obj, sqlite3.Connection):
            try:
                obj.total_changes
            except sqlite3.ProgrammingError:  # closed
                continue
            count += 1
    return count


def test_a_with_block_closes_the_connection_even_on_an_error():
    ws = make_user("dbc_unit", PASSWORD)
    for open_db in (connect_users_db, lambda: connect_pages_db(ws), lambda: connect_data_db(ws)):
        with open_db() as conn:
            assert isinstance(conn, Connection)
            conn.execute("SELECT 1")
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        with pytest.raises(KeyError):
            with open_db() as conn:
                raise KeyError("boom")
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


def test_requests_leave_no_connection_open(member, no_gc):
    page = member.post("/api/pages", json={"title": "Read a lot"}).json()["id"]
    before = _open_connections()
    for _ in range(40):
        member.get("/api/session")
        member.get("/api/blocks/root/children")
        member.get(f"/api/blocks/{page}/subtree")
        member.get("/api/blocks/dbc-nope")           # a 404 raised inside the with block
        member.get("/api/blocks/dbc-nope/subtree")
        member.put(f"/api/chats/{page}", json={"messages": [{"role": "user", "text": "hi"}]})
    # background threads (the index refresher, the orphan check) may hold one or two a moment
    assert _open_connections() - before <= 3


def test_a_workspace_deleted_right_after_use_leaves_nothing_behind(member, no_gc):
    ws = member.post("/api/workspaces", json={"name": "Gone at once"}).json()["id"]
    h = {"X-Gamma-Workspace": ws}
    page = member.post("/api/pages", json={"title": "Briefly here"}, headers=h).json()["id"]
    for _ in range(3):
        member.get(f"/api/blocks/{page}/subtree", headers=h)
        member.get("/api/blocks/dbc-nope", headers=h)
        member.put(f"/api/chats/{page}", json={"messages": [{"role": "user", "text": "x"}]}, headers=h)
        member.post(f"/api/pages/{page}/ops", headers=h, json={
            "client": "dbc", "ops": [{"op": "set", "id": page, "content": "Renamed"}]})
    r = member.delete(f"/api/workspaces/{ws}")
    assert r.status_code == 200, r.text
    assert r.json()["warning"] == "" and not ws_dir(ws).exists()
