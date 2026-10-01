"""One write rule for sessions, integration tokens and the AI agent
(auth.can_write / require_ws(write=True)): a viewer or a read-scope token
reads, never writes — chats, cover snapshots and the agent's tools included.
And an account rename carries everything the account controls, so none of
it passes to a later account that takes the old name."""

import json
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

import gamma.routers.ai as ai_mod
from ai_fixtures import FakeResp
from conftest import login, make_user, workspace_of
from gamma import ai_usage, backup_schedule, sync_engine, workspaces
from gamma import publisher_sessions as sessions
from gamma.app import app
from gamma.db import connect_users_db, page_now
from gamma.integrations import create_token

SNAP = {"img": "data:image/jpeg;base64,/9j/AAA=", "at": "2026-01-01T00:00:00.000Z"}


def _provider(c):
    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-test-key-123",
                                          "models": "claude-solo"})
    assert r.status_code == 200, r.text


@pytest.fixture(scope="module")
def lab(client):
    """A shared workspace with an owner, an editor and a viewer, one page."""
    for name in ("wa_owner", "wa_editor", "wa_viewer"):
        make_user(name, "pw")
    lab_ws = workspaces.create("WA lab", "wa_owner", kind="shared")["id"]
    workspaces.set_member(lab_ws, "wa_editor", "editor", by="wa_owner")
    workspaces.set_member(lab_ws, "wa_viewer", "viewer", by="wa_owner")
    clients = {}
    for name in ("wa_owner", "wa_editor", "wa_viewer"):
        c = login(name, "pw")
        c.headers["X-Gamma-Workspace"] = lab_ws
        clients[name.removeprefix("wa_")] = c
    page = clients["owner"].post("/api/pages", json={"title": "Lab paper", "properties": {"folder": "lab"}})
    return lab_ws, clients, page.json()["id"]


def _agent_chat(c, monkeypatch, page_id):
    """One agent chat whose model tries to rename the page; returns the tool
    names the chat armed."""
    armed = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        armed.append({t["name"] for t in kw.get("tools") or []})
        if len(armed) == 1:
            return FakeResp([
                {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1", "name": "rename_page"}},
                {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": json.dumps(
                    {"page_id": page_id, "title": "RENAMED"})}},
                {"type": "content_block_stop"}])
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "done"}}])
    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    r = c.post("/api/ai/chat", json={"prompt": "rename it", "agent_scope": "folder", "folder": "",
                                     "permissions": {"rename": True}, "stream": False})
    assert r.status_code == 200, r.text
    return armed[0]


# --- viewers and read-scope tokens never write --------------------------------

def test_a_viewer_reads_chats_but_never_changes_them(lab):
    _, clients, page_id = lab
    owner, viewer = clients["owner"], clients["viewer"]
    assert owner.put(f"/api/chats/{page_id}", json={"messages": [{"role": "user", "text": "kept"}]}).status_code == 200
    entry = owner.post("/api/chat-history/archive", json={
        "bucket": page_id, "messages": [{"role": "user", "text": "owner's archived conversation"}]}).json()["id"]
    assert viewer.get(f"/api/chats/{page_id}").status_code == 200
    assert viewer.get("/api/chat-history", params={"bucket": page_id}).status_code == 200
    for method, path, body in (
        ("PUT", f"/api/chats/{page_id}", {"messages": []}),
        ("DELETE", f"/api/chats/{page_id}", None),
        ("POST", "/api/chat-history/archive", {"bucket": page_id, "messages": []}),
        ("POST", f"/api/chat-history/{entry}/open", {"bucket": page_id}),
        ("PUT", f"/api/chat-history/{entry}", {"title": "mine now"}),
        ("DELETE", f"/api/chat-history/{entry}", None),
    ):
        assert viewer.request(method, path, json=body).status_code == 403, (method, path)
    sessions_ = owner.get("/api/chat-history", params={"bucket": page_id}).json()["sessions"]
    assert [s["id"] for s in sessions_] == [entry]
    # An editor writes them.
    assert clients["editor"].put(f"/api/chats/{page_id}", json={"messages": []}).status_code == 200


def test_a_viewer_cannot_revert_an_ai_change(lab):
    _, clients, page_id = lab
    body = {"kind": "edit", "block_id": page_id, "revert": {"before": "", "after": "x"}}
    assert clients["viewer"].post("/api/ai/revert", json=body).status_code == 403


def test_a_viewer_keeps_its_cover_snapshots_to_itself(lab):
    _, clients, page_id = lab
    assert clients["viewer"].put(f"/api/page-snaps/{page_id}", json=SNAP).status_code == 403
    assert clients["viewer"].delete(f"/api/page-snaps/{page_id}").status_code == 403
    assert clients["editor"].put(f"/api/page-snaps/{page_id}", json=SNAP).status_code == 200
    assert page_id in clients["viewer"].get("/api/page-snaps").json()["snaps"]


def test_a_viewer_chats_with_the_ai_without_writing_tools(lab, monkeypatch):
    _, clients, page_id = lab
    _provider(clients["viewer"])
    armed = _agent_chat(clients["viewer"], monkeypatch, page_id)
    assert "read_page" in armed and not armed & {"rename_page", "move_page", "edit_block", "create_block"}
    assert clients["owner"].get(f"/api/blocks/{page_id}").json()["content"] == "Lab paper"
    # An editor's chat arms them.
    _provider(clients["editor"])
    assert "rename_page" in _agent_chat(clients["editor"], monkeypatch, page_id)


def test_a_read_token_reads_but_never_writes(client, monkeypatch):
    ws = make_user("wa_tokens", "pw")
    c = login("wa_tokens", "pw")
    _provider(c)
    page_id = c.post("/api/pages", json={"title": "Token paper"}).json()["id"]
    c.put(f"/api/chats/{page_id}", json={"messages": [{"role": "user", "text": "keep me"}]})
    bearer = TestClient(app)
    bearer.headers["Authorization"] = "Bearer " + create_token("wa_tokens", ws, "assistant", 90, scope="read")["token"]
    armed = _agent_chat(bearer, monkeypatch, page_id)
    assert "read_page" in armed and not armed & {"rename_page", "move_page", "edit_block"}
    assert c.get(f"/api/blocks/{page_id}").json()["content"] == "Token paper"
    assert bearer.get(f"/api/chats/{page_id}").status_code == 200
    for method, path, body in (
        ("PUT", f"/api/chats/{page_id}", {"messages": []}),
        ("DELETE", f"/api/chats/{page_id}", None),
        ("POST", "/api/chat-history/archive", {"bucket": page_id, "messages": []}),
        ("PUT", f"/api/page-snaps/{page_id}", SNAP),
    ):
        r = bearer.request(method, path, json=body)
        assert r.status_code == 403 and r.json()["detail"] == "this token is read-only", (method, path)
    assert c.get(f"/api/chats/{page_id}").json()["messages"] == [{"role": "user", "text": "keep me"}]


# --- a renamed account takes what it controls along -------------------------

@pytest.fixture
def transport(monkeypatch):
    """Mirrors talk to "the remote" through a cookie-less TestClient."""
    remote = TestClient(app)

    def fetch(method, path, body, headers):
        r = remote.request(method, path, content=body, headers=headers)
        return r.status_code, r.content
    monkeypatch.setattr(sync_engine, "default_fetch", fetch)
    monkeypatch.setattr(sync_engine, "sync_in_background", lambda ws: None)


COOKIE = {"name": "access", "value": "renamed-publisher-secret", "domain": ".aps.org",
          "hostOnly": False, "path": "/"}


def _cookie_for(username):
    token = sessions.current_user.set(username)
    try:
        jar = sessions.cookie_jar()
    finally:
        sessions.current_user.reset(token)
    req = Request("https://journals.aps.org/paper.pdf")
    jar.add_cookie_header(req)
    return req.get_header("Cookie") or ""


def _task(owner):
    return backup_schedule.save(owner, {
        "name": "nightly", "enabled": True, "scope": "all_owned", "workspaces": [],
        "cron": "0 3 * * *", "uploads": False, "retention_mode": "count", "retention_value": 3})


def test_a_renamed_account_keeps_its_copies_tasks_and_connections(client, transport):
    make_user("wa_admin", "pw", is_admin=1)
    remote_ws = make_user("wa_remote", "pw")
    login("wa_remote", "pw").post("/api/pages", json={"title": "Grant proposal (confidential)"})
    make_user("wa_alice", "pw")
    alice = login("wa_alice", "pw")
    token = create_token("wa_remote", remote_ws, "mirror", 90, scope="write")["token"]
    r = alice.post("/api/mirrors", json={"remote_url": "http://testserver", "token": token})
    assert r.status_code == 201, r.text
    copy_ws = r.json()["workspace_id"]
    sync_engine.sync_workspace(copy_ws)
    task = _task("wa_alice")
    ai_usage.record("wa_alice", "chat", "p1", "Provider", "model", {"input": 7, "output": 3})
    sessions.save("wa_alice", "journals.aps.org", [COOKIE])
    lab_ws = workspaces.create("WA invites", "wa_alice", kind="shared")["id"]
    with connect_users_db() as conn:
        conn.execute("INSERT INTO pending_memberships (workspace_id, subject, username, role, invited_by, "
                     "created_at) VALUES (?, 'sub-wa', 'someone', 'editor', 'wa_alice', ?)", (lab_ws, page_now()))
        conn.commit()

    admin = login("wa_admin", "pw")
    r = admin.post("/api/admin/users/wa_alice/rename", json={"new_username": "wa_alice.old"})
    assert r.status_code == 200, r.text

    renamed = login("wa_alice.old", "pw")
    assert [m["workspace_id"] for m in renamed.get("/api/mirrors").json()["mirrors"]] == [copy_ws]
    assert renamed.get(f"/api/mirrors/{copy_ws}/log").status_code == 200
    assert [t["id"] for t in renamed.get("/api/backup-tasks").json()["tasks"]] == [task["id"]]
    assert "renamed-publisher-secret" in _cookie_for("wa_alice.old")
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_usage WHERE username = 'wa_alice.old'").fetchone()[0] == 1
        assert conn.execute("SELECT invited_by FROM pending_memberships WHERE subject = 'sub-wa'"
                            ).fetchone()[0] == "wa_alice.old"
        assert not conn.execute("SELECT 1 FROM ai_usage WHERE username = 'wa_alice'").fetchone()

    # Someone new takes the old name: none of it is theirs.
    assert admin.post("/api/admin/users", json={"username": "wa_alice", "password": "newpass123"}).status_code == 200
    newcomer = login("wa_alice", "newpass123")
    assert newcomer.get("/api/mirrors").json()["mirrors"] == []
    for path in (f"/api/mirrors/{copy_ws}", f"/api/mirrors/{copy_ws}/log", f"/api/mirrors/{copy_ws}/conflicts"):
        assert newcomer.get(path).status_code == 404, path
    assert newcomer.post(f"/api/mirrors/{copy_ws}/force", json={"direction": "push"}).status_code == 404
    assert newcomer.get("/api/backup-tasks").json()["tasks"] == []
    assert _cookie_for("wa_alice") == ""


def test_mirror_controls_need_the_owner_of_the_copy(client, transport):
    remote_ws = make_user("wa_remote2", "pw")
    make_user("wa_bob", "pw")
    make_user("wa_carol", "pw")
    bob = login("wa_bob", "pw")
    token = create_token("wa_remote2", remote_ws, "mirror", 90, scope="write")["token"]
    copy_ws = bob.post("/api/mirrors", json={"remote_url": "http://testserver", "token": token}).json()["workspace_id"]
    # A mirror row naming an account that doesn't own the copy's workspace
    # (a stale owner) opens nothing for it.
    with connect_users_db() as conn:
        conn.execute("UPDATE mirrors SET owner = 'wa_carol' WHERE workspace_id = ?", (copy_ws,))
        conn.commit()
    carol = login("wa_carol", "pw")
    assert carol.get("/api/mirrors").json()["mirrors"] == []
    assert carol.get(f"/api/mirrors/{copy_ws}").status_code == 404


def test_a_rename_waits_for_a_running_backup_task(client):
    make_user("wa_admin2", "pw", is_admin=1)
    make_user("wa_dave", "pw")
    task = _task("wa_dave")
    admin = login("wa_admin2", "pw")
    with backup_schedule.locked(task["id"]) as held:
        assert held
        r = admin.post("/api/admin/users/wa_dave/rename", json={"new_username": "wa_dave2"})
    assert r.status_code == 409 and "backup task" in r.json()["detail"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT 1 FROM users WHERE username = 'wa_dave'").fetchone()
    assert backup_schedule.read(task["id"])["owner"] == "wa_dave"
    # Once it finished, the rename goes through and takes the task along.
    assert admin.post("/api/admin/users/wa_dave/rename", json={"new_username": "wa_dave2"}).status_code == 200
    assert backup_schedule.read(task["id"])["owner"] == "wa_dave2"
    assert workspace_of("wa_dave2")
