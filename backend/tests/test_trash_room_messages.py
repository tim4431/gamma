"""What an open tab of a page hears when the page goes to Recently deleted
and comes back: ``trashed`` (it keeps its typist's unsent edits instead of
reloading into a 404), then ``reload`` on a restore — from the trash, or a
merge restore that brings the page back (it refetches and sends what it
kept)."""

from fastapi.testclient import TestClient

from conftest import login, make_page, make_user, workspace_of
from gamma import ws_backup
from gamma.app import app

PRESENCE = ("join", "leave", "cursor", "ops")


def _next(sock, kind):
    for _ in range(20):
        msg = sock.receive_json()
        if msg["t"] == kind:
            return msg
        assert msg["t"] in PRESENCE, msg
    raise AssertionError(f"no {kind} message")


def _open(tab, page_id, ws):
    sock = tab.websocket_connect(f"/api/ws/page/{page_id}?ws={ws}&client=tab")
    sock.__enter__()
    assert sock.receive_json()["t"] == "hello"
    return sock


def test_trash_then_restore_tells_the_open_tab():
    make_user("trm_owner", "trmownerpw1")
    c = login("trm_owner", "trmownerpw1")
    ws = workspace_of("trm_owner")
    page = make_page(c, "Trashed while open")
    with TestClient(app, cookies=c.cookies) as tab:
        sock = _open(tab, page["id"], ws)
        try:
            assert c.delete(f"/api/blocks/{page['id']}").status_code == 200
            assert _next(sock, "trashed") == {"t": "trashed"}
            assert c.post(f"/api/trash/{page['id']}/restore").status_code == 200
            assert _next(sock, "reload")["t"] == "reload"
        finally:
            sock.__exit__(None, None, None)


def test_a_merge_restore_bringing_a_trashed_page_back_tells_the_open_tab():
    make_user("trm_merge", "trmmergepw1")
    c = login("trm_merge", "trmmergepw1")
    ws = workspace_of("trm_merge")
    page = make_page(c, "Back from a backup")
    snap = ws_backup.create(ws, label="before")
    with TestClient(app, cookies=c.cookies) as tab:
        sock = _open(tab, page["id"], ws)
        try:
            assert c.delete(f"/api/blocks/{page['id']}").status_code == 200
            _next(sock, "trashed")
            r = c.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore?mode=merge")
            assert r.status_code == 200 and r.json()["from_trash"] == 1, r.text
            _next(sock, "reload")
        finally:
            sock.__exit__(None, None, None)
