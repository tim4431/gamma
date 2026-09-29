"""The live approval boundary: policy, identity, exact calls and cancellation.

Every model response is synthetic and HTTP requests use the in-process app.
"""

import json
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import anyio
import pytest
from fastapi import HTTPException

from ai_fixtures import FakeResp, ai_provider, org, props  # noqa: F401
from gamma import ai_permissions as permissions
from gamma.ai_tools import agent_tools, run_agent_tool, tool_approval_metadata


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))
    monkeypatch.setattr(permissions, "APPROVAL_TIMEOUT", 2)
    yield
    for pending in list(permissions._pending.values()):
        pending.run.close()


@pytest.mark.parametrize("key,default", list(permissions.DEFAULT_PERMISSIONS.items()))
def test_defaults_and_legacy_values(key, default):
    assert permissions.permission_state({}, key) == default
    assert permissions.permission_state({key: True}, key) == "allow"
    assert permissions.permission_state({key: False}, key) == "deny"
    for value in ("allow", "ask", "deny"):
        assert permissions.permission_state({key: value}, key) == value
    for invalid in (None, 1, 0, [], {}, "true", "ALLOW"):
        assert permissions.permission_state({key: invalid}, key) == "deny"
    assert permissions.permission_state({"unknown": "allow"}, "unknown") == "deny"


def test_asking_tools_stay_armed_denied_tools_do_not():
    assert "edit_block" in {t["name"] for t in agent_tools("page", {"block_edit": "ask"})}
    assert "edit_block" not in {t["name"] for t in agent_tools("page", {"block_edit": "deny"})}
    assert "edit_block" not in {t["name"] for t in agent_tools("page", {"block_edit": "allow"}, can_write=False)}


@pytest.mark.parametrize("policy", [{}, {"block_edit": "ask"}, {"block_edit": "deny"}, {"block_edit": False}])
def test_direct_dispatch_cannot_bypass_permissions(org, policy):
    _, ids = org
    scope = {"type": "page", "page_id": ids["note"], "permissions": policy}
    result, action = run_agent_tool(ids["ws"], scope, "create_block",
                                   {"parent_id": ids["note"], "content": "must not be created"},
                                   allowed_tools={"create_block"})
    assert result.startswith("error") and action["error"]


def test_grants_are_single_use_and_bound_to_tool_arguments_identity():
    access = permissions.AccessRun("alice", "workspace", {})
    args = {"parent_id": "page", "content": "reviewed"}
    approval = access.request("create_block", args, "block_edit", {"summary": "Add note"})
    args["content"] = "changed after proposal"
    shown = approval.event()
    assert shown["args"]["content"] == "reviewed"
    shown["args"]["content"] = "changed after display"
    reviewed = {"parent_id": "page", "content": "reviewed"}
    assert permissions.resolve(approval.id, "alice", "workspace", "allow_once", can_write=True)
    assert access.wait(approval) == "allow_once"
    assert not access.authorize("other", "alice", "create_block", reviewed, "block_edit")
    assert not access.authorize("workspace", "bob", "create_block", reviewed, "block_edit")
    assert not access.authorize("workspace", "alice", "edit_block", reviewed, "block_edit")
    assert not access.authorize("workspace", "alice", "create_block", args, "block_edit")
    assert access.authorize("workspace", "alice", "create_block", reviewed, "block_edit")
    assert not access.authorize("workspace", "alice", "create_block", reviewed, "block_edit")
    assert access.wait(approval) == "deny"  # cannot regenerate a spent grant


def test_pending_decision_is_private_atomic_and_cannot_elevate_viewer():
    access = permissions.AccessRun("alice", "workspace", {})
    approval = access.request("create_block", {}, "block_edit", {"summary": "Add note"})
    for user, ws, writable, status in [("bob", "workspace", True, 404),
                                       ("alice", "other", True, 404),
                                       ("alice", "workspace", False, 403)]:
        with pytest.raises(HTTPException) as failure:
            permissions.resolve(approval.id, user, ws, "allow_always", can_write=writable)
        assert failure.value.status_code == status
    assert permissions.resolve(approval.id, "alice", "workspace", "allow_always", can_write=True)
    with pytest.raises(HTTPException):
        permissions.resolve(approval.id, "alice", "workspace", "deny", can_write=True)
    assert access.wait(approval) == "allow_always"
    assert access.authorize("workspace", "alice", "edit_block", {"content": "next"}, "block_edit")
    assert access.state("rename") == "ask"
    assert permissions.AccessRun("alice", "workspace", {}).state("block_edit") == "ask"


def test_timeout_and_cancellation_release_pending(monkeypatch):
    monkeypatch.setattr(permissions, "APPROVAL_TIMEOUT", 0)
    access = permissions.AccessRun("alice", "workspace", {})
    approval = access.request("create_block", {}, "block_edit", {"summary": "Add note"})
    assert access.wait(approval) == "expired"
    assert approval.id not in permissions._pending
    assert not access.authorize("workspace", "alice", "create_block", {}, "block_edit")
    monkeypatch.setattr(permissions, "APPROVAL_TIMEOUT", 60)
    approval = access.request("create_block", {}, "block_edit", {"summary": "Add note"})
    with ThreadPoolExecutor() as executor:
        waiting = executor.submit(access.wait, approval)
        access.close()
        assert waiting.result(timeout=1) == "deny"
    assert approval.id not in permissions._pending
    with pytest.raises(HTTPException):
        permissions.resolve(approval.id, "alice", "workspace", "allow_once", can_write=True)


def test_stop_after_decision_before_execution_revokes_grant():
    access = permissions.AccessRun("alice", "workspace", {})
    approval = access.request("create_block", {}, "block_edit", {"summary": "Add note"})
    permissions.resolve(approval.id, "alice", "workspace", "allow_always", can_write=True)
    access.close()
    assert access.wait(approval) == "deny"
    assert not access.authorize("workspace", "alice", "create_block", {}, "block_edit")


def test_grant_never_expands_scope_or_workspace_role(org):
    _, ids = org
    args = {"parent_id": ids["note"], "content": "outside page"}
    access = permissions.AccessRun(ids["user"], ids["ws"], {})
    approval = access.request("create_block", args, "block_edit", {"summary": "Add note"})
    permissions.resolve(approval.id, ids["user"], ids["ws"], "allow_always", can_write=True)
    access.wait(approval)
    scope = {"type": "page", "page_id": ids["a"], "actor": ids["user"], "access": access,
             "context_pages": [ids["note"]]}
    text, action = run_agent_tool(ids["ws"], scope, "create_block", args)
    assert action["error"] and "scope" in text
    scope.update(page_id=ids["note"], can_write=False)
    text, action = run_agent_tool(ids["ws"], scope, "create_block", args)
    assert action["error"] and "only view" in text


def test_selection_proposal_names_the_actual_target(org):
    c, ids = org
    block = c.post("/api/blocks", json={"parent_id": ids["note"], "content": "quoted"}).json()["id"]
    scope = {"type": "page", "page_id": ids["note"],
             "note_selections": [{"label": "S1", "block_id": block, "text": "quoted", "from": 0, "to": 6}]}
    args = {"mode": "selection", "selection": "S1", "content": "replacement"}
    metadata = tool_approval_metadata(ids["ws"], scope, "edit_block", args)
    assert metadata["target"]["block_id"] == block
    assert metadata["target"]["page_id"] == ids["note"]
    assert tool_approval_metadata(ids["ws"], scope, "edit_block", {**args, "block_id": ids["a"]}) is None


@pytest.fixture
def fake_chat(org, ai_provider, monkeypatch):
    """Expose real pending requests while TestClient buffers the NDJSON body."""
    import gamma.routers.ai as ai
    c, ids = org
    proposals = queue.Queue()
    request = permissions.AccessRun.request

    def intercept(self, *args, **kwargs):
        approval = request(self, *args, **kwargs)
        proposals.put(approval)
        return approval

    monkeypatch.setattr(permissions.AccessRun, "request", intercept)

    def configure(calls):
        calls = iter(calls)

        def fake_open(*args, **kwargs):
            call = next(calls, None)
            if call is None:
                return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Finished."}}])
            tool, arguments = call
            return FakeResp([
                {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "call1", "name": tool}},
                {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": json.dumps(arguments)}},
                {"type": "content_block_stop"},
            ])

        monkeypatch.setattr(ai, "_open_ai", fake_open)

    return c, ids, proposals, configure


@pytest.mark.parametrize("first_decision,requests", [("allow_once", 2), ("allow_always", 1)])
def test_stream_waits_and_once_vs_always(fake_chat, first_decision, requests):
    c, ids, proposals, configure = fake_chat
    configure([("rename_page", {"page_id": ids["note"], "title": "reviewed first"}),
               ("rename_page", {"page_id": ids["note"], "title": "reviewed second"})])
    old = props(c, ids["note"])["content"]
    with ThreadPoolExecutor() as executor:
        response = executor.submit(c.post, "/api/ai/chat", json={"prompt": "Rename twice", "agent_scope": "folder", "stream": True})
        first = proposals.get(timeout=2)
        assert not response.done() and props(c, ids["note"])["content"] == old
        assert first.event()["target"]["title"] == old
        decided = c.post(f"/api/ai/approvals/{first.id}", json={"decision": first_decision})
        assert decided.status_code == 200 and decided.json() == {"permission": "rename", "decision": first_decision}
        if requests == 2:
            second = proposals.get(timeout=2)
            assert props(c, ids["note"])["content"] == "reviewed first"
            assert c.post(f"/api/ai/approvals/{second.id}", json={"decision": "allow_once"}).status_code == 200
        result = response.result(timeout=3)
    lines = [json.loads(line) for line in result.text.splitlines()]
    assert len([line for line in lines if "approval" in line]) == requests
    assert len([line for line in lines if "approval_resolved" in line]) == requests
    assert props(c, ids["note"])["content"] == "reviewed second"
    assert c.post(f"/api/ai/approvals/{first.id}", json={"decision": "allow_once"}).status_code == 404


@pytest.mark.parametrize("decision", ["deny", "allow_once"])
def test_stream_never_previews_an_unapproved_edit(fake_chat, decision):
    c, ids, proposals, configure = fake_chat
    block = c.post("/api/blocks", json={"parent_id": ids["note"], "content": "original"}).json()["id"]
    args = {"block_id": block, "mode": "append", "content": "approved addition"}
    configure([("edit_block", args)])
    with ThreadPoolExecutor() as executor:
        response = executor.submit(c.post, "/api/ai/chat", json={"prompt": "Add detail", "agent_scope": "page", "page_id": ids["note"], "stream": True})
        approval = proposals.get(timeout=2)
        assert props(c, block)["content"] == "original"
        assert c.post(f"/api/ai/approvals/{approval.id}", json={"decision": decision}).status_code == 200
        result = response.result(timeout=3)
    lines = [json.loads(line) for line in result.text.splitlines()]
    assert not any("progress" in line for line in lines)
    resolved_index = next(i for i, line in enumerate(lines) if "approval_resolved" in line)
    action_index = next(i for i, line in enumerate(lines) if "action" in line)
    assert resolved_index < action_index
    action = lines[action_index]["action"]
    assert bool(action.get("error")) == (decision == "deny")
    assert props(c, block)["content"] == ("original" if decision == "deny" else "original\napproved addition")


def test_nonstream_ask_fails_closed_and_never_waits(fake_chat):
    c, ids, proposals, configure = fake_chat
    old = props(c, ids["note"])["content"]
    configure([("rename_page", {"page_id": ids["note"], "title": "not approved"})])
    result = c.post("/api/ai/chat", json={"prompt": "Rename", "agent_scope": "folder"})
    assert result.status_code == 200 and result.json()["actions"][0]["error"]
    assert proposals.empty() and props(c, ids["note"])["content"] == old


def test_stream_timeout_reports_expiry_without_mutating(fake_chat, monkeypatch):
    c, ids, _, configure = fake_chat
    monkeypatch.setattr(permissions, "APPROVAL_TIMEOUT", 0.01)
    old = props(c, ids["note"])["content"]
    configure([("rename_page", {"page_id": ids["note"], "title": "not approved"})])
    result = c.post("/api/ai/chat", json={"prompt": "Rename", "agent_scope": "folder", "stream": True})
    events = [json.loads(line) for line in result.text.splitlines()]
    assert next(event["approval_resolved"] for event in events if "approval_resolved" in event)["decision"] == "expired"
    assert props(c, ids["note"])["content"] == old


@pytest.mark.parametrize("spec_version", ["2.0", "2.4"])
def test_disconnect_wakes_waiter_immediately_for_all_asgi_versions(spec_version):
    from gamma.routers.ai import _ApprovalStream, keepalive_lines
    access = permissions.AccessRun("alice", "workspace", {})
    approval = access.request("create_block", {}, "block_edit", {"summary": "Add note"})
    stopped = threading.Event()
    outcome = []

    def lines():
        yield json.dumps({"approval": approval.event()}) + "\n"
        outcome.append(access.wait(approval))
        stopped.set()

    async def scenario():
        sent = anyio.Event()

        async def receive():
            await sent.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                sent.set()

        response = _ApprovalStream(keepalive_lines(lines(), interval=0.05, on_close=access.close), access)
        with anyio.fail_after(1):
            await response({"type": "http", "asgi": {"spec_version": spec_version}}, receive, send)

    anyio.run(scenario)
    assert stopped.wait(1) and outcome == ["deny"]
    assert approval.id not in permissions._pending


def test_approval_endpoint_requires_session_and_workspace_owner(org, anon):
    c, ids = org
    access = permissions.AccessRun(ids["user"], ids["ws"], {})
    approval = access.request("rename_page", {}, "rename", {"summary": "Rename"})
    url = f"/api/ai/approvals/{approval.id}"
    assert anon.post(url, json={"decision": "allow_once"}).status_code == 401
    assert c.post(url, json={"decision": "unknown"}).status_code == 422
    assert c.post(url, json={"decision": "deny"}).status_code == 200
    assert access.wait(approval) == "deny"


@pytest.mark.parametrize("decision", ["deny", "allow_once"])
def test_publisher_cookie_approval_is_separate_from_public_fetch(fake_chat, monkeypatch, decision):
    from gamma import publisher_sessions
    c, ids, proposals, configure = fake_chat
    monkeypatch.setattr(publisher_sessions, "list_sessions", lambda user: [{"host": "publisher.example"}])
    identities = []

    def fetch(source, **kwargs):
        identities.append(publisher_sessions.current_user.get())
        return {"kind": "pdf", "url": source, "title": "A paper", "pages": ["Paper content"], "chars": 13}

    monkeypatch.setattr("gamma.ai_web.fetch_document", fetch)
    configure([("fetch_paper", {"source": "https://publisher.example/paper.pdf", "publisher_user": "other-user"})])
    with ThreadPoolExecutor() as executor:
        response = executor.submit(c.post, "/api/ai/chat", json={"prompt": "Read", "agent_scope": "folder", "stream": True})
        approval = proposals.get(timeout=2)
        assert approval.permission == "publisher_cookies" and identities == []
        assert c.post(f"/api/ai/approvals/{approval.id}", json={"decision": decision}).status_code == 200
        result = response.result(timeout=3)
    assert identities == ([ids["user"]] if decision == "allow_once" else [None])
    assert "Paper content" in result.text and not proposals.qsize()
    assert publisher_sessions.current_user.get() is None


def test_publisher_cookies_nonstream_ask_stays_anonymous(fake_chat, monkeypatch):
    from gamma import publisher_sessions
    c, _, proposals, configure = fake_chat
    monkeypatch.setattr(publisher_sessions, "list_sessions", lambda user: [{"host": "publisher.example"}])
    identities = []

    def fetch(source, **kwargs):
        identities.append(publisher_sessions.current_user.get())
        return {"kind": "pdf", "url": source, "title": "A paper", "pages": ["Public content"], "chars": 14}

    monkeypatch.setattr("gamma.ai_web.fetch_document", fetch)
    configure([("fetch_paper", {"source": "https://publisher.example/paper.pdf"})])
    result = c.post("/api/ai/chat", json={"prompt": "Read", "agent_scope": "folder"})
    assert result.status_code == 200 and identities == [None] and proposals.empty()
