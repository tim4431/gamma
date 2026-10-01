"""The event loop never waits on a database. Every request of every user,
and every page socket, shares one loop: an ``async def`` endpoint that
reads SQLite runs on it, and one waiting on a workspace's write lock (the
busy timeout is 10 s) froze the whole server — another account's GET
/api/session took 3.8 s while an import held a lock. Endpoints that touch
a database are sync defs (FastAPI runs them in the threadpool); the async
ones — they await a body, a socket or an SDK — hand their database work to
a worker thread, and so does the session middleware."""

import ast
import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest

from conftest import login, make_user

PASSWORD = "el-pass-1234"
GAMMA = Path(__file__).resolve().parent.parent / "gamma"

# Async handlers that await nothing, on purpose: no database, no file read
# (the SPA route stats a file; its FileResponse streams in the threadpool).
IN_MEMORY = {"health", "spa", "get_logs"}


def test_async_endpoints_are_async_for_a_reason():
    """An async route handler that awaits nothing runs its whole body on the
    loop — make it a def (docs/dev/debugging.md "Gotchas")."""
    methods = {"get", "post", "put", "delete", "patch", "websocket", "api_route"}
    offenders = []
    for path in sorted(GAMMA.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.AsyncFunctionDef) or node.name in IN_MEMORY:
                continue
            routed = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in methods
                         for d in node.decorator_list)
            if routed and not any(isinstance(n, ast.Await) for n in ast.walk(node)):
                offenders.append(f"{path.relative_to(GAMMA)}:{node.lineno} {node.name}")
    assert offenders == []


@pytest.fixture(scope="module")
def writer():
    make_user("el_writer", PASSWORD)
    return login("el_writer", PASSWORD)


def _off_loop_probe(monkeypatch, module, name, seen: list):
    """Record, for each call of ``module.name``, whether it ran on a thread
    with a running event loop."""
    real = getattr(module, name)

    def probe(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            seen.append("loop")
        except RuntimeError:
            seen.append("thread")
        return real(*args, **kwargs)
    monkeypatch.setattr(module, name, probe)


def test_the_session_read_and_an_op_batch_run_off_the_loop(writer, monkeypatch):
    from gamma import auth
    from gamma.routers import collab as collab_router
    seen_session, seen_ops = [], []
    _off_loop_probe(monkeypatch, auth, "_middleware_session", seen_session)
    _off_loop_probe(monkeypatch, collab_router, "commit_ops", seen_ops)
    page = writer.post("/api/pages", json={"title": "Off the loop"}).json()["id"]
    r = writer.post(f"/api/pages/{page}/ops", json={"client": "el", "ops": [{"op": "set", "id": page, "content": "x"}]})
    assert r.status_code == 200, r.text
    assert seen_session and set(seen_session) == {"thread"}
    assert seen_ops == ["thread"]


def test_the_page_socket_reads_off_the_loop(writer, monkeypatch):
    from fastapi.testclient import TestClient
    from gamma.app import app
    from gamma.routers import collab as collab_router
    seen = []
    _off_loop_probe(monkeypatch, collab_router, "_socket_access", seen)
    _off_loop_probe(monkeypatch, collab_router, "latest_seq", seen)
    page = writer.post("/api/pages", json={"title": "Socket off the loop"}).json()["id"]
    with TestClient(app) as c:
        c.cookies.update(writer.cookies)
        with c.websocket_connect(f"/api/ws/page/{page}?client=el-sock") as sock:
            assert sock.receive_json()["t"] == "hello"
    assert seen == ["thread", "thread"]


def test_a_held_write_lock_does_not_stall_other_requests(writer):
    """One loop, as uvicorn runs it: while another connection holds the
    workspace's write lock, an op batch waits for it — in a worker thread —
    and another account's GET /api/session is answered at once."""
    from gamma.app import app
    from gamma.db import ws_db_path
    from gamma.workspaces import default_workspace
    make_user("el_other", PASSWORD)
    other = login("el_other", PASSWORD).cookies.get("session")
    page = writer.post("/api/pages", json={"title": "Held lock"}).json()["id"]
    hold_s = 2.0

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                     cookies={"session": writer.cookies.get("session")}) as w, \
                httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                  cookies={"session": other}) as o:
            holder = sqlite3.connect(ws_db_path(default_workspace("el_writer"), "pages.db"),
                                     check_same_thread=False)
            holder.execute("BEGIN IMMEDIATE")
            release = threading.Timer(hold_s, holder.rollback)
            release.start()
            try:
                started = time.perf_counter()
                write = asyncio.create_task(w.post(f"/api/pages/{page}/ops", json={
                    "client": "el", "ops": [{"op": "set", "id": page, "content": "after the lock"}]}))
                await asyncio.sleep(0.2)  # the batch is waiting on the lock by now (a stalled loop: longer)
                session = await o.get("/api/session")
                elapsed = time.perf_counter() - started - 0.2
                written = await write
            finally:
                release.join()
                holder.close()
        return session, elapsed, written

    session, elapsed, written = asyncio.run(scenario())
    assert session.status_code == 200 and session.json()["user"] == "el_other"
    assert elapsed < 0.75, f"GET /api/session took {elapsed:.2f} s behind a held write lock"
    assert written.status_code == 200, written.text
