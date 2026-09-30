"""AI tool permissions (gamma/ai_permissions.py): the three states and their
defaults, what each state arms, the approval card's preview of a change, the
waiting approvals and their answers, and the tool loop asking before a change:
allowed once or for the chat, declined, unanswered, answered over HTTP, and a
stream that closes while its card waits."""

import json
import threading
import time

import pytest

from gamma import ai_permissions
from gamma.ai_permissions import permission_state
from gamma.ai_tools import (MUTATING_TOOLS, TOOLS, agent_system, agent_tools, approval_preview,
                            text_diff, tool_states)
from gamma.routers.ai import WatchedStream, keepalive_lines

from conftest import login, make_user
from ai_fixtures import ALLOW_ALL, FakeResp, ai_provider, folder, notes, org, props  # noqa: F401  (fixtures)


@pytest.fixture(scope="module", autouse=True)
def _provider(ai_provider):
    """Every chat here needs a provider entry on the module's account."""


@pytest.fixture(autouse=True)
def _offline_model_facts(monkeypatch):
    # The chat loop also looks up model limits before calling _open_ai.
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))


def _names(specs):
    return {s["name"] for s in specs}


# --- states --------------------------------------------------------------------

def test_a_permission_left_out_allows_reading_and_asks_before_a_change():
    assert permission_state({}, "read", False) == "allow"
    assert permission_state({}, "rename", True) == "ask"
    assert permission_state(None, "block_edit", True) == "ask"
    # What tabs sent before Ask existed: on and off.
    assert permission_state({"rename": True}, "rename", True) == "allow"
    assert permission_state({"read": False}, "read", False) == "off"
    for state in ("allow", "ask", "off"):
        assert permission_state({"read": state}, "read", False) == state
    assert permission_state({"rename": "sometimes"}, "rename", True) == "ask"


def test_asking_tools_are_armed_and_tools_that_are_off_are_not():
    armed = _names(agent_tools("folder", {"rename": "ask", "move": "off"}))
    assert "rename_page" in armed and "move_page" not in armed
    assert _names(agent_tools("folder", {})) == _names(agent_tools("folder", ALLOW_ALL))
    states = tool_states({"rename": "ask", "move": "ask"}, granted=["move"])
    assert (states["rename"], states["move"], states["read"], states["block_edit"]) == (
        "ask", "allow", "allow", "ask")
    # A request that cannot show a card arms no asking tool.
    offline = tool_states({}, can_ask=False)
    assert (offline["block_edit"], offline["read"]) == ("off", "allow")
    assert not _names(agent_tools("page", offline)) & MUTATING_TOOLS


def test_the_prompt_names_the_tools_that_ask():
    scope = {**folder(""), "can_write": True}
    asking = agent_system(scope, tool_states({"rename": "ask", "move": "allow", "block_edit": "allow"}))
    assert "approves each call of rename_page before it runs" in asking
    assert "never ask for permission in your reply first" in asking
    assert "approves each call" not in agent_system(scope, tool_states(ALLOW_ALL))


def test_every_change_tool_previews_its_approval():
    for t in TOOLS:
        assert bool(t.get("preview")) == t["mutating"], t["spec"]["name"]
    kinds = {}
    for t in TOOLS:
        kinds.setdefault(t["perm"], set()).add(t["mutating"])
    assert all(len(v) == 1 for v in kinds.values()), "a permission either reads or changes"


# --- the card's preview ----------------------------------------------------------

def test_the_card_shows_a_change_word_by_word():
    assert text_diff("The cavity has a Q of 1e6.", "The cavity had a Q of 1e6.") == [
        ["ctx", "The cavity "], ["del", "has"], ["ins", "had"], ["ctx", " a Q of 1e6."]]
    assert text_diff("", "new note") == [["ins", "new note"]]
    # CJK text has no spaces: its characters compare one by one.
    assert text_diff("这是旧的笔记", "这是新的笔记") == [
        ["ctx", "这是"], ["del", "旧"], ["ins", "新"], ["ctx", "的笔记"]]
    # A long unchanged stretch keeps only its end next to the change.
    diff = text_diff("word " * 200 + "old", "word " * 200 + "new")
    assert diff[0][0] == "ctx" and diff[0][1].startswith("…") and len(diff[0][1]) < 200
    assert diff[-2:] == [["del", "old"], ["ins", "new"]]


def test_a_preview_names_the_change_and_changes_nothing(notes):
    c, ids = notes
    ws, scope = ids["ws"], {**folder("sandbox"), "can_write": True, "read_texts": {}}
    preview, answer = approval_preview(ws, scope, "edit_block",
                                       {"block_id": ids["top"], "mode": "append", "content": "more"})
    assert answer is None
    assert (preview["page_id"], preview["block_id"], preview["mode"], preview["title"]) == (
        ids["page"], ids["top"], "append", "notes playground")
    assert preview["diff"] == [["ctx", "top-level idea"], ["ins", "\nmore"]]
    assert props(c, ids["top"])["content"] == "top-level idea", "a preview changes nothing"
    renamed, _ = approval_preview(ws, scope, "rename_page",
                                  {"page_id": ids["page"], "title": "notes playground v2"})
    assert renamed["diff"] == [["ctx", "notes playground"], ["ins", " v2"]]
    moved, _ = approval_preview(ws, scope, "move_page", {"page_id": ids["page"], "folder": "archive"})
    assert (moved["from"], moved["to"]) == ("sandbox", "sandbox/archive")
    created, _ = approval_preview(ws, scope, "create_block", {"parent_id": ids["top"], "content": "a child"})
    assert (created["parent"], created["diff"]) == ("top-level idea", [["ins", "a child"]])
    placed, _ = approval_preview(ws, scope, "move_block", {"block_id": ids["other"], "parent_id": ids["top"]})
    assert (placed["parent"], placed["diff"]) == ("top-level idea", [["ctx", "second thread"]])
    # A reading tool the user set to ask has no preview of its own.
    assert approval_preview(ws, scope, "read_page", {"page_id": ids["page"]}) == ({}, None)


def test_a_call_that_cannot_change_anything_is_answered_without_a_card(notes):
    _, ids = notes
    ws, scope = ids["ws"], {**folder("sandbox"), "can_write": True, "read_texts": {}}
    for name, args, answer in [
        ("rename_page", {"page_id": "nope", "title": "x"}, "error: no such page"),
        ("rename_page", {"page_id": ids["page"], "title": "notes playground"}, "ok — title already"),
        ("edit_block", {"block_id": ids["top"], "content": "rewritten"}, "error: read the block first"),
        ("move_block", {"block_id": ids["top"], "parent_id": ids["child"]}, "error: cannot move a block into"),
    ]:
        preview, said = approval_preview(ws, scope, name, args)
        assert preview is None and said.startswith(answer), (name, said)


# --- waiting approvals -------------------------------------------------------------

def test_an_approval_takes_one_answer_from_its_own_account():
    aid = ai_permissions.open_approval("alice")
    assert not ai_permissions.answer(aid, "mallory", "once")
    assert not ai_permissions.answer(aid, "alice", "maybe")
    assert ai_permissions.answer(aid, "alice", "chat", "ignored unless declined")
    assert not ai_permissions.answer(aid, "alice", "once"), "answered already"
    assert ai_permissions.wait_for(aid, timeout=1) == ("chat", "")
    assert not ai_permissions.answer(aid, "alice", "once"), "gone once it was waited for"
    assert ai_permissions.wait_for("unknown", timeout=1) == ("expired", "")
    # A decline may say what to do instead.
    aid = ai_permissions.open_approval("alice")
    assert ai_permissions.answer(aid, "alice", "deny", "  use AuthorYear titles  ")
    assert ai_permissions.wait_for(aid, timeout=1) == ("deny", "use AuthorYear titles")


def test_an_unanswered_approval_expires_and_a_closed_stream_wins():
    aid = ai_permissions.open_approval("alice")
    started = time.monotonic()
    assert ai_permissions.wait_for(aid, timeout=0.2) == ("expired", "")
    assert time.monotonic() - started < 2
    stopped = threading.Event()
    aid = ai_permissions.open_approval("alice")
    threading.Timer(0.1, stopped.set).start()
    assert ai_permissions.wait_for(aid, stopped, timeout=5) == ("stopped", "")
    # Allowed a moment before the stream closed: nothing runs for it anyway.
    aid = ai_permissions.open_approval("alice")
    assert ai_permissions.answer(aid, "alice", "once")
    assert ai_permissions.wait_for(aid, stopped, timeout=5) == ("stopped", "")


def test_an_account_has_a_bounded_number_of_waiting_approvals(monkeypatch):
    monkeypatch.setattr(ai_permissions, "MAX_PENDING_PER_USER", 2)
    waiting = [ai_permissions.open_approval("bob") for _ in range(2)]
    assert ai_permissions.open_approval("bob") is None
    other = ai_permissions.open_approval("carol")
    assert other, "another account is not affected"
    for aid in [*waiting, other]:
        assert ai_permissions.wait_for(aid, timeout=0) == ("expired", "")


def test_a_closed_stream_releases_its_waiting_approval():
    """What the chat relies on when the client leaves while a card waits:
    closing the relay (Starlette does, for a gone client) sets the event
    the waiting call watches."""
    stopped, seen = threading.Event(), {}

    def source():
        yield '{"approval": {}}\n'
        seen["decision"] = ai_permissions.wait_for(ai_permissions.open_approval("dave"), stopped, timeout=10)
        yield '{"never": 1}\n'

    relay = keepalive_lines(source(), "t", interval=0.05, abandoned=stopped)
    assert next(relay) == '{"approval": {}}\n'
    relay.close()
    deadline = time.monotonic() + 5
    while "decision" not in seen and time.monotonic() < deadline:
        time.sleep(0.02)
    assert seen.get("decision") == ("stopped", "")


def test_a_watched_stream_hears_the_client_leave_at_once():
    """Under uvicorn's ASGI spec 2.3 Starlette's disconnect listener fires
    as the client leaves; WatchedStream sets its event then, while the source
    is still blocked between two lines (a card waiting for its answer)."""
    import anyio

    gone, release, sent = threading.Event(), threading.Event(), []

    def source():
        yield '{"approval": {}}\n'
        release.wait(5)
        yield '{"never": 1}\n'

    async def receive():
        await anyio.sleep(0.2)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    response = WatchedStream(source(), gone, media_type="application/x-ndjson")
    runner = threading.Thread(target=anyio.run,
                              args=(response, {"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send))
    runner.start()
    try:
        assert gone.wait(2), "the client's leaving is heard at once"
        assert not release.is_set()
    finally:
        release.set()
        runner.join(10)
    assert any(m.get("body") == b'{"approval": {}}\n' for m in sent)


def test_the_answer_endpoint_takes_only_the_asking_accounts_decision(org):
    c, ids = org
    aid = ai_permissions.open_approval(ids["user"])
    try:
        assert c.post(f"/api/ai/approvals/{aid}", json={"decision": "sometimes"}).status_code == 422
        make_user("permissions-other", "pw")
        other = login("permissions-other", "pw")
        assert other.post(f"/api/ai/approvals/{aid}", json={"decision": "once"}).status_code == 404
        assert c.post(f"/api/ai/approvals/{aid}", json={"decision": "deny", "note": "x" * 2001}).status_code == 422
        assert c.post(f"/api/ai/approvals/{aid}", json={"decision": "deny", "note": "file it under Reviews"}).json() == {"ok": True}
        assert c.post(f"/api/ai/approvals/{aid}", json={"decision": "once"}).status_code == 404
    finally:
        assert ai_permissions.wait_for(aid, timeout=0) == ("deny", "file it under Reviews")


# --- the tool loop ------------------------------------------------------------------

def _tool_use(call_id, name, args):
    return [{"type": "content_block_start", "content_block": {"type": "tool_use", "id": call_id, "name": name}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta",
                                                      "partial_json": json.dumps(args)}},
            {"type": "content_block_stop"}]


def _agent(monkeypatch, *calls):
    """Fake the provider: the first round makes ``calls`` ((id, name, args)),
    the next answers "done". Returns what each round was opened with."""
    import gamma.routers.ai as ai_mod

    opened = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        opened.append({"messages": [dict(m) for m in messages], "system": system,
                       "tools": _names(kw.get("tools") or [])})
        if len(opened) == 1 and calls:
            return FakeResp([e for call in calls for e in _tool_use(*call)])
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "done"}}])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    return opened


def _answers(monkeypatch, user, *decisions):
    """Answer the chat's approval cards in turn, as ``user`` would. Returns
    the ids the chat waited on."""
    real, left, asked = ai_permissions.wait_for, list(decisions), []

    def wait_for(approval_id, stopped=None, timeout=None):
        asked.append(approval_id)
        decision = left.pop(0)
        assert ai_permissions.answer(approval_id, user, *(decision if isinstance(decision, tuple) else (decision,)))
        return real(approval_id, stopped, timeout)

    monkeypatch.setattr(ai_permissions, "wait_for", wait_for)
    return asked


def _chat(c, **body):
    r = c.post("/api/ai/chat", json={"prompt": "tidy up", "agent_scope": "folder", "folder": "",
                                     "stream": True, **body})
    assert r.status_code == 200, r.text
    lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    return lines, [line["action"] for line in lines if "action" in line]


def _kinds(lines):
    return [next(iter(line)) for line in lines if next(iter(line)) in ("step", "approval", "action")]


def test_a_change_asks_first_and_runs_once_allowed(org, monkeypatch):
    c, ids = org
    opened = _agent(monkeypatch, ("t1", "rename_page", {"page_id": ids["a"], "title": "Ada2019 cavity"}))
    asked = _answers(monkeypatch, ids["user"], "once")
    lines, actions = _chat(c, permissions={"rename": "ask"})
    assert _kinds(lines) == ["step", "approval", "action"]
    card = next(line["approval"] for line in lines if "approval" in line)
    assert card["id"] == asked[0] and (card["tool"], card["perm"], card["call_id"]) == ("rename_page", "rename", "t1")
    assert card["preview"]["page_id"] == ids["a"]
    assert "".join(text for kind, text in card["preview"]["diff"] if kind != "del") == "Ada2019 cavity"
    assert (actions[0]["kind"], actions[0]["approval"]) == ("rename", "once")
    assert props(c, ids["a"])["content"] == "Ada2019 cavity"
    assert "rename_page" in opened[0]["tools"] and "approves each call of rename_page" in opened[0]["system"]
    assert opened[1]["messages"][-1]["content"].startswith("ok")


def test_a_declined_change_is_not_made_and_the_model_hears_why(org, monkeypatch):
    c, ids = org
    before = props(c, ids["b"])["content"]
    opened = _agent(monkeypatch, ("t1", "rename_page", {"page_id": ids["b"], "title": "Declined title"}))
    _answers(monkeypatch, ids["user"], "deny")
    lines, actions = _chat(c, permissions={"rename": "ask"})
    assert props(c, ids["b"])["content"] == before
    assert actions[0]["error"] and actions[0]["declined"] and actions[0]["approval"] == "deny"
    assert actions[0]["title"] == before, "the chip names the page the change was for"
    assert opened[1]["messages"][-1]["content"].startswith("declined: the user did not allow")
    assert "done" in "".join(line.get("delta", "") for line in lines), "the reply carries on"


def test_a_decline_can_say_what_to_do_instead(org, monkeypatch):
    c, ids = org
    opened = _agent(monkeypatch, ("t1", "rename_page", {"page_id": ids["b"], "title": "Not this one"}))
    _answers(monkeypatch, ids["user"], ("deny", "Call it Qec2021 instead"))
    _, actions = _chat(c, permissions={"rename": "ask"})
    assert (actions[0]["declined"], actions[0]["note"]) == (True, "Call it Qec2021 instead")
    heard = opened[1]["messages"][-1]["content"]
    assert heard.startswith("declined:") and '"Call it Qec2021 instead"' in heard


def test_allowing_for_the_chat_stops_asking_for_that_permission(org, monkeypatch):
    c, ids = org
    _agent(monkeypatch, ("t1", "rename_page", {"page_id": ids["a"], "title": "Chat title one"}),
           ("t2", "rename_page", {"page_id": ids["b"], "title": "Chat title two"}))
    asked = _answers(monkeypatch, ids["user"], "chat")
    lines, actions = _chat(c, permissions={"rename": "ask"})
    assert len(asked) == 1 and _kinds(lines) == ["step", "approval", "action", "step", "action"]
    assert [a.get("approval") for a in actions] == ["chat", None]
    assert [props(c, ids[k])["content"] for k in ("a", "b")] == ["Chat title one", "Chat title two"]


def test_a_permission_the_conversation_granted_runs_without_a_card(org, monkeypatch):
    c, ids = org
    _agent(monkeypatch, ("t1", "rename_page", {"page_id": ids["a"], "title": "Granted title"}))
    lines, actions = _chat(c, permissions={"rename": "ask"}, granted=["rename"])
    assert _kinds(lines) == ["step", "action"] and "approval" not in actions[0]
    assert props(c, ids["a"])["content"] == "Granted title"


def test_an_unanswered_card_counts_as_declined(org, monkeypatch):
    c, ids = org
    before = props(c, ids["a"])["content"]
    monkeypatch.setattr(ai_permissions, "APPROVAL_TIMEOUT", 0.3)
    opened = _agent(monkeypatch, ("t1", "rename_page", {"page_id": ids["a"], "title": "Unanswered"}))
    lines, actions = _chat(c, permissions={"rename": "ask"})
    assert next(line["approval"] for line in lines if "approval" in line)["timeout"] == 0
    assert (actions[0]["declined"], actions[0]["approval"]) == (True, "expired")
    assert props(c, ids["a"])["content"] == before
    assert opened[1]["messages"][-1]["content"].startswith("declined: the user did not answer")


def test_a_call_that_cannot_change_anything_asks_nobody(org, monkeypatch):
    c, ids = org
    _agent(monkeypatch, ("t1", "rename_page", {"page_id": "no-such-page", "title": "x"}))
    lines, actions = _chat(c, permissions={"rename": "ask"})
    assert _kinds(lines) == ["step", "action"]
    assert actions[0]["error"] and not actions[0].get("declined")


def test_a_closed_stream_leaves_the_waiting_change_unmade(org, monkeypatch):
    c, ids = org
    before = props(c, ids["a"])["content"]
    opened = _agent(monkeypatch, ("t1", "rename_page", {"page_id": ids["a"], "title": "Never"}))
    gone, real = threading.Event(), ai_permissions.wait_for
    gone.set()  # the client left while the card waited
    monkeypatch.setattr(ai_permissions, "wait_for",
                        lambda approval_id, stopped=None, timeout=None: real(approval_id, gone, timeout))
    lines, actions = _chat(c, permissions={"rename": "ask"})
    assert _kinds(lines) == ["step", "approval"] and not actions
    assert len(opened) == 1, "no further round"
    assert props(c, ids["a"])["content"] == before
    assert not [a for a in ai_permissions._pending.values() if a["user"] == ids["user"]]


def test_an_asking_note_edit_is_not_typed_into_the_notes(notes, monkeypatch):
    c, ids = notes
    _agent(monkeypatch, ("t1", "edit_block", {"block_id": ids["other"], "mode": "append", "content": "extra"}))
    _answers(monkeypatch, ids["user"], "once")
    lines, actions = _chat(c, agent_scope="page", page_id=ids["page"], permissions={"block_edit": "ask"})
    assert not [line for line in lines if "progress" in line]
    card = next(line["approval"] for line in lines if "approval" in line)
    assert card["preview"]["diff"] == [["ctx", "second thread"], ["ins", "\nextra"]]
    assert actions[0]["approval"] == "once" and props(c, ids["other"])["content"] == "second thread\nextra"


def test_a_reply_that_cannot_ask_offers_no_asking_tool(org, monkeypatch):
    c, ids = org
    opened = _agent(monkeypatch)
    r = c.post("/api/ai/chat", json={"prompt": "tidy up", "agent_scope": "folder", "folder": "",
                                     "permissions": {"rename": "ask", "move": "allow"}})
    assert r.status_code == 200, r.text
    assert "rename_page" not in opened[0]["tools"] and "move_page" in opened[0]["tools"]


def test_the_card_is_answered_over_http_while_the_reply_waits(org, monkeypatch):
    c, ids = org
    _agent(monkeypatch, ("t1", "rename_page", {"page_id": ids["b"], "title": "Answered over HTTP"}))
    clicker, answered = login(ids["user"], "pw"), {}

    def user_clicks():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            waiting = [aid for aid, a in list(ai_permissions._pending.items()) if a["user"] == ids["user"]]
            if waiting:
                answered["status"] = clicker.post(f"/api/ai/approvals/{waiting[0]}",
                                                  json={"decision": "once"}).status_code
                return
            time.sleep(0.02)

    thread = threading.Thread(target=user_clicks)
    thread.start()
    _, actions = _chat(c, permissions={"rename": "ask"})
    thread.join(5)
    assert answered == {"status": 200}
    assert actions[0]["approval"] == "once" and props(c, ids["b"])["content"] == "Answered over HTTP"
