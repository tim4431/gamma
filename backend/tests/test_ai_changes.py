"""An agent's changes and the account's review mode (gamma/agent_changes.py,
/api/ai/changes): in Editing, the default, every change the chat's tools
make is journaled kept and no page draws it; in Reviewing, a tracked tool's
change is made without a card and is a suggestion its page draws until the
person accepts or rejects it, every other tool asks as its permission says,
and Off stays off; what Reject could not take back is refused there, and a
suggestion the journal cannot keep says it stands. A change undone anywhere
replays to the model as undone. Reading one change and accepting are the
person's: an integration token does neither."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import fresh_client, login, make_folder, make_user, token_for
from gamma import agent_changes, collab
from gamma.ai_tools import notes
from gamma.ai_tools.registry import TRACKED_TOOLS, run_agent_tool
from gamma.db import connect_pages_db, format_stamp

from ai_fixtures import ALLOW_ALL, FakeResp, ai_provider, children, org, props  # noqa: F401  (fixtures)


@pytest.fixture
def page(org):
    """A fresh page per test in the folder "sandbox", with two notes."""
    c, ids = org
    r = c.post("/api/blocks", json={"parent_id": "root", "content": "review playground", "properties": {}})
    assert r.status_code == 200, r.text
    page = r.json()["id"]
    sandbox = make_folder(c, "sandbox")
    assert c.put(f"/api/blocks/{page}", json={"properties": {"folders": [sandbox]}}).status_code == 200
    made = {}
    for key, content in (("first", "the first idea"), ("second", "the second idea")):
        last = children(c, page)
        r = c.post("/api/blocks", json={"parent_id": page, "content": content, "properties": {},
                                        "before": props(c, last[-1])["position"] if last else None})
        assert r.status_code == 200, r.text
        made[key] = r.json()["id"]
    return c, {**ids, "page": page, "sandbox": sandbox, **made}


def _scope(ids, actor=None, reviewing=True):
    """The chat's scope as its router makes it: whose, who writes, where it
    journals, the account's review mode."""
    return {"type": "folder", "folder": ids["sandbox"], "actor": actor or ids["user_id"],
            "agent": {"id": "gamma", "name": "Gamma AI", "for": ids["user"]},
            "journal": {"source": "chat", "token": ""}, "reviewing": reviewing}


def _edit(ids, block_id, content, reviewing=True, actor=None):
    text, action = run_agent_tool(ids["ws"], _scope(ids, actor, reviewing), "edit_block",
                                  {"block_id": block_id, "mode": "append", "content": content})
    assert text.startswith("ok"), text
    return action


def _suggested(c, page_id):
    """The page's suggestions, as its page reads them."""
    r = c.get("/api/ai/changes", params={"page_id": page_id})
    assert r.status_code == 200, r.text
    return r.json()["changes"]


def _states(c, *ids):
    return c.get("/api/ai/changes", params={"ids": ",".join(ids)}).json()["states"]


def _decide(c, change_id, decision, force=False):
    return c.post(f"/api/ai/changes/{change_id}", json={"decision": decision, "force": force})


def test_in_editing_a_change_is_kept_and_no_page_draws_it(page, monkeypatch):
    c, ids = page
    told = []
    monkeypatch.setattr(collab, "publish_account", lambda user, message: told.append((user, message)))
    action = _edit(ids, ids["first"], " (allowed)", reviewing=False)
    change = action["change_id"]
    assert action["change_state"] == "kept" and _states(c, change) == {change: "kept"}
    assert _suggested(c, ids["page"]) == []
    assert told == [(ids["user_id"], {"t": "ai_changes", "ws": ids["ws"], "page_id": ids["page"]})]
    # its row in the chat still shows and undoes it
    shown = c.get(f"/api/ai/changes/{change}").json()["change"]
    assert (shown["state"], shown["block_id"], shown["revert"]) == ("kept", ids["first"], action["revert"])
    assert _decide(c, change, "undo").json()["state"] == "undone"
    assert props(c, ids["first"])["content"] == "the first idea"


def test_in_reviewing_a_change_is_a_suggestion_until_accepted(page, monkeypatch):
    c, ids = page
    told = []
    monkeypatch.setattr(collab, "publish_account", lambda user, message: told.append((user, message)))
    action = _edit(ids, ids["first"], " (sharper)")
    change = action["change_id"]
    assert action["change_state"] == "open"
    [drawn] = _suggested(c, ids["page"])
    assert (drawn["id"], drawn["block_id"], drawn["page_id"]) == (change, ids["first"], ids["page"])
    assert (drawn["tool"], drawn["kind"], drawn["source"], drawn["state"]) == ("edit_block", "edit", "chat", "open")
    assert drawn["revert"] == action["revert"] and action["revert"]["before"] == "the first idea"
    assert drawn["agent"]["name"] == "Gamma AI" and drawn["at"]
    assert len(told) == 1
    r = _decide(c, change, "keep")
    assert r.status_code == 200 and r.json()["state"] == "kept", r.text
    assert _suggested(c, ids["page"]) == [] and _states(c, change) == {change: "kept"}
    assert props(c, ids["first"])["content"] == action["revert"]["after"]
    assert len(told) == 2


def test_reject_takes_it_back_and_redo_keeps_it(page):
    c, ids = page
    action = _edit(ids, ids["second"], "plus more")
    change = action["change_id"]
    r = _decide(c, change, "undo")
    assert r.status_code == 200 and r.json()["state"] == "undone", r.text
    assert props(c, ids["second"])["content"] == "the second idea"
    assert _suggested(c, ids["page"]) == [] and _states(c, change) == {change: "undone"}
    assert _decide(c, change, "undo").json()["noop"] is True
    # an Accept still drawn somewhere keeps nothing undone, and says so
    r = _decide(c, change, "keep")
    assert r.json() == {"state": "undone", "page_id": ids["page"], "noop": True}, r.text
    assert c.post("/api/ai/changes/keep", json={"ids": [change]}).json() == {"kept": []}
    r = _decide(c, change, "redo")
    assert r.status_code == 200 and r.json()["state"] == "kept", r.text
    assert props(c, ids["second"])["content"] == action["revert"]["after"]
    assert _suggested(c, ids["page"]) == []
    assert _decide(c, change, "keep").json() == {"state": "kept", "page_id": ids["page"], "noop": True}


def test_a_reject_that_would_lose_typing_stops_until_forced(page):
    c, ids = page
    change = _edit(ids, ids["first"], " by the AI")["change_id"]
    assert c.put(f"/api/blocks/{ids['first']}", json={"content": "the first idea by me"}).status_code == 200
    r = _decide(c, change, "undo")
    assert r.status_code == 409 and r.json()["conflict"] == "changed", r.text
    assert _states(c, change) == {change: "open"}
    assert _decide(c, change, "undo", force=True).status_code == 200
    assert _states(c, change) == {change: "undone"}


def test_accept_all_accepts_only_the_accounts_own(page):
    c, ids = page
    mine = [_edit(ids, ids["first"], " one")["change_id"], _edit(ids, ids["second"], " two")["change_id"]]
    theirs = _edit(ids, ids["first"], " three", actor="someone-else")["change_id"]
    assert [x["id"] for x in _suggested(c, ids["page"])] == mine
    r = c.post("/api/ai/changes/keep", json={"ids": [*mine, theirs]})
    assert r.status_code == 200 and sorted(r.json()["kept"]) == sorted(mine), r.text
    assert _states(c, *mine, theirs) == {mine[0]: "kept", mine[1]: "kept"}
    assert _decide(c, theirs, "undo").status_code == 404
    assert agent_changes.find(ids["ws"], theirs)["state"] == "open"


def test_the_gate_in_reviewing_asks_only_of_an_untracked_tool(org):
    """In Reviewing a tracked tool on Ask shows no card, while another
    change (a comment, a page made) still asks; a tool that is Off is armed
    for nothing and refused, Reviewing or not."""
    from gamma.ai_agent import ApprovalGate

    _, ids = org
    perms = {"block_edit": "ask", "comment": "ask", "draw": "ask", "create": "ask"}
    reviewing = ApprovalGate(ids["ws"], {"type": "folder", "folder": "", "permissions": perms, "reviewing": True},
                             ids["user_id"])
    editing = ApprovalGate(ids["ws"], {"type": "folder", "folder": "", "permissions": perms}, ids["user_id"])
    assert TRACKED_TOOLS == {"edit_block", "create_block", "move_block", "delete_block", "highlight", "draw",
                             "add_text_box", "place_picture", "add_sheet"}
    assert not any(reviewing.asks(name) for name in TRACKED_TOOLS)
    assert all(editing.asks(name) for name in TRACKED_TOOLS)
    assert reviewing.asks("comment") and reviewing.asks("create_page")
    off = ApprovalGate(ids["ws"], {"type": "folder", "folder": "", "permissions": {"block_edit": "off"},
                                   "reviewing": True}, ids["user_id"])
    run = off.run("edit_block", {"id": "c1", "arguments": {"block_id": ids["note"], "content": "x"}}, frozenset())
    with pytest.raises(StopIteration) as done:
        next(run)
    result, action = done.value.value
    assert result.startswith("error: tool not enabled") and action["error"]


def test_the_chat_in_reviewing_makes_a_tracked_change_without_its_card(page, ai_provider, monkeypatch):
    """Through the chat's own route, with the account in Reviewing (its
    ``ai-review`` pref, written through the generic prefs route) and note
    changes on Ask: the edit is made with no approval card, its action
    names its change as a suggestion, and its page draws it."""
    import gamma.routers.ai as ai_mod

    c, ids = page
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *args: (None, ""))
    rounds = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        rounds.append(1)
        if len(rounds) == 1:
            args = {"block_id": ids["first"], "mode": "append", "content": " (suggested)"}
            return FakeResp([{"type": "content_block_start", "content_block": {"type": "tool_use", "id": "e1",
                                                                               "name": "edit_block"}},
                             {"type": "content_block_delta",
                              "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)}},
                             {"type": "content_block_stop"}])
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "done"}}])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    assert c.put("/api/prefs/ai-review", json={"value": True}).status_code == 200
    try:
        assert c.get("/api/prefs/ai-review").json()["value"] is True
        r = c.post("/api/ai/chat", json={"prompt": "sharpen it", "agent_scope": "folder", "folder": "",
                                        "stream": True, "permissions": {**ALLOW_ALL, "block_edit": "ask"}})
        assert r.status_code == 200, r.text
    finally:
        assert c.put("/api/prefs/ai-review", json={"value": False}).status_code == 200
    lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert not [line for line in lines if "approval" in line]
    [action] = [line["action"] for line in lines if "action" in line]
    assert action["change_state"] == "open" and action["change_id"]
    assert [x["id"] for x in _suggested(c, ids["page"])] == [action["change_id"]]
    assert props(c, ids["first"])["content"] == action["revert"]["after"]
    assert action["revert"]["after"].endswith("(suggested)")


def test_the_prune_spares_suggestions(page, monkeypatch):
    """Past a connection's newest changes, or older than the days kept, a
    decided change goes; a suggestion stays until the person decides it."""
    c, ids = page
    monkeypatch.setattr(agent_changes, "KEEP_PER_CONNECTION", 1)
    waiting = _edit(ids, ids["first"], " (waits)")["change_id"]
    old_kept = _edit(ids, ids["second"], " (kept)", reviewing=False)["change_id"]
    with connect_pages_db(ids["ws"]) as conn:
        conn.execute("UPDATE agent_changes SET at = ? WHERE id IN (?, ?)",
                     (format_stamp(datetime.now(timezone.utc) - timedelta(days=agent_changes.KEEP_DAYS + 1)),
                      waiting, old_kept))
        conn.commit()
    newest = _edit(ids, ids["second"], " (newest)", reviewing=False)["change_id"]
    _edit(ids, ids["second"], " (newer still)", reviewing=False)
    found = _states(c, waiting, old_kept, newest)
    assert found == {waiting: "open"}, found
    assert [x["id"] for x in _suggested(c, ids["page"])] == [waiting]


def test_one_change_reads_for_its_own_account_alone(page):
    """GET /ai/changes/{id}: the account's own change in the request's
    workspace, with its state (what a connection's row in Settings opens);
    another account's or a gone one is no change, and a token reads none."""
    c, ids = page
    action = _edit(ids, ids["first"], " (shown)", reviewing=False)
    r = c.get(f"/api/ai/changes/{action['change_id']}")
    assert r.status_code == 200, r.text
    shown = r.json()["change"]
    assert {k: shown[k] for k in ("id", "tool", "kind", "block_id", "page_id", "state", "source")} == {
        "id": action["change_id"], "tool": "edit_block", "kind": "edit", "block_id": ids["first"],
        "page_id": ids["page"], "state": "kept", "source": "chat"}
    assert shown["revert"] == action["revert"] and shown["agent"]["name"] == "Gamma AI" and shown["at"]
    theirs = _edit(ids, ids["second"], " (theirs)", actor="someone-else")["change_id"]
    assert c.get(f"/api/ai/changes/{theirs}").status_code == 404
    assert c.get("/api/ai/changes/no-such-change").status_code == 404
    agent = fresh_client()
    agent.headers["Authorization"] = f"Bearer {token_for(c, 'read', 'Codex')}"
    try:
        assert agent.get(f"/api/ai/changes/{action['change_id']}").status_code == 403
    finally:
        agent.close()


def test_a_connections_change_from_before_accounts_reads_for_its_owner(page):
    """A change an MCP connection journaled before migration step 73, with
    no ``account``, reads for the account whose connection made it (its
    Changes in Settings offer Open on it), and for no other account."""
    c, ids = page
    make_user("changes-other", "pw")
    other = login("changes-other", "pw")
    mine = c.post("/api/integrations/tokens", json={"name": "Codex"}).json()["id"]
    theirs = other.post("/api/integrations/tokens", json={"name": "Other"}).json()["id"]
    try:
        made = {}
        for token_id, block_id in ((mine, ids["first"]), (theirs, ids["second"])):
            scope = {**_scope(ids, reviewing=False), "journal": {"source": "mcp", "token": token_id}}
            text, action = run_agent_tool(ids["ws"], scope, "edit_block",
                                          {"block_id": block_id, "mode": "append", "content": " (before 73)"})
            assert text.startswith("ok"), text
            made[token_id] = action["change_id"]
        with connect_pages_db(ids["ws"]) as conn:
            conn.execute("UPDATE agent_changes SET account = '' WHERE id IN (?, ?)", tuple(made.values()))
            conn.commit()
        r = c.get(f"/api/ai/changes/{made[mine]}")
        assert r.status_code == 200, r.text
        shown = r.json()["change"]
        assert (shown["id"], shown["source"], shown["block_id"]) == (made[mine], "mcp", ids["first"])
        assert c.get(f"/api/ai/changes/{made[theirs]}").status_code == 404, "another account's connection"
    finally:
        c.delete(f"/api/integrations/tokens/{mine}")
        other.delete(f"/api/integrations/tokens/{theirs}")
        other.close()


def test_in_reviewing_what_reject_could_not_take_back_is_refused_unwritten(page, monkeypatch):
    """In Reviewing, a paste of several blocks or a deletion whose revert
    record would pass ``_DELETE_REVERT_MAX`` is refused as an error, with
    nothing written, journaled or sent to the page's room; in Editing both
    are made, unjournaled, and their results say so."""
    c, ids = page
    monkeypatch.setattr(notes, "_DELETE_REVERT_MAX", 20)
    published = []
    monkeypatch.setattr(collab, "publish_ops", lambda ws, result: published.append(result["page_id"]))
    paste = {"parent_id": ids["page"], "content": "## Pasted heading\n\nits first paragraph\n\nits second paragraph"}

    def run(name, args, reviewing):
        return run_agent_tool(ids["ws"], _scope(ids, reviewing=reviewing), name, args)

    text, action = run("create_block", paste, True)
    assert text.startswith("error: not pasted") and "paste it in parts" in text.lower(), text
    assert action["error"] and "change_id" not in action
    assert "switch Gamma to Editing" in text
    assert children(c, ids["page"]) == [ids["first"], ids["second"]] and published == []
    text, action = run("delete_block", {"block_id": ids["second"]}, True)
    assert text.startswith("error: not deleted") and action["error"] and "change_id" not in action, text
    assert props(c, ids["second"])["content"] == "the second idea" and published == []
    assert _suggested(c, ids["page"]) == []

    text, action = run("create_block", paste, False)
    assert text.startswith("ok") and "too large to record for a revert" in text, text
    assert "change_id" not in action and agent_changes.UNTRACKED not in text
    assert len(children(c, ids["page"])) == 3 and published == [ids["page"]]
    text, action = run("delete_block", {"block_id": ids["second"]}, False)
    assert text.startswith("ok") and "too large to record for a revert" in text, text
    assert children(c, ids["page"])[0] == ids["first"] and ids["second"] not in children(c, ids["page"])


def test_a_suggestion_the_journal_cannot_keep_says_it_stands(page, monkeypatch):
    """When the journal fails, a change made in Reviewing stands untracked
    and the model is told it could not be kept as a suggestion; in Editing
    nothing is added."""
    c, ids = page

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(agent_changes, "record", broken)
    for reviewing in (True, False):
        text, action = run_agent_tool(ids["ws"], _scope(ids, reviewing=reviewing), "edit_block",
                                      {"block_id": ids["first"], "mode": "append", "content": " (untracked)"})
        assert text.startswith("ok") and "change_id" not in action, text
        assert text.endswith(agent_changes.UNTRACKED) == reviewing, text
        assert agent_changes.SUGGESTED not in text


def test_a_change_undone_anywhere_replays_as_undone(page):
    """The replay reads the journal, not the mark the chat saved: an undo
    from the page reaches the model though the conversation was not marked,
    and a redo clears a stale mark."""
    c, ids = page
    undone = _edit(ids, ids["first"], " gone")["change_id"]
    redone = _edit(ids, ids["second"], " back")["change_id"]
    assert _decide(c, undone, "undo").status_code == 200
    assert _decide(c, redone, "undo").status_code == 200 and _decide(c, redone, "redo").status_code == 200
    assert _states(c, undone, redone) == {undone: "undone", redone: "kept"}
    history = [{"role": "ai", "text": "done", "actions": [
        {"tool": "edit_block", "change_id": undone}, {"tool": "edit_block", "change_id": redone, "reverted": True},
        {"tool": "edit_block", "change_id": "gone-since", "reverted": True}, {"tool": "read_page"}]}]
    agent_changes.mark_history(ids["ws"], ids["user_id"], history)
    actions = history[0]["actions"]
    assert actions[0]["reverted"] is True and actions[1]["reverted"] is False
    assert actions[2]["reverted"] is True and "reverted" not in actions[3]


def test_the_chat_replays_a_change_undone_from_its_page_as_undone(page, ai_provider, monkeypatch):
    """Through the chat's own route: a reply's saved action naming a change
    rejected since where it landed, which the chat never marked, reaches
    the model as reverted."""
    import gamma.routers.ai as ai_mod

    c, ids = page
    monkeypatch.setattr("gamma.ai_catalog.context_window", lambda *args: (0, ""))
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *args: (None, ""))
    opened = []

    def fake_open(messages, system, entry, rt, pdf_b64s=None, **kw):
        opened.append([dict(m) for m in messages])
        return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "done"}}])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    change = _edit(ids, ids["first"], " gone")["change_id"]
    assert _decide(c, change, "undo").status_code == 200
    history = [{"role": "user", "text": "sharpen it"},
               {"role": "ai", "text": "done", "actions": [
                   {"kind": "edit", "tool": "edit_block", "args": {"block_id": ids["first"]},
                    "result": "ok — block updated", "change_id": change}]}]
    r = c.post("/api/ai/chat", json={"prompt": "now the second one", "agent_scope": "folder", "folder": "",
                                    "stream": True, "permissions": ALLOW_ALL, "history": history})
    assert r.status_code == 200 and opened, r.text
    [replayed] = [m for m in opened[0] if m.get("role") == "tool"]
    assert replayed["content"].startswith("[the user reverted this change"), replayed["content"]


def test_a_token_accepts_nothing(page):
    """The token an assistant works through, which may have made the
    changes, cannot accept them: Accept and Accept all answer it 403, and
    the suggestion still waits for the person. Clearing marks is no route."""
    c, ids = page
    waiting = _edit(ids, ids["first"], " (unreviewed)")["change_id"]
    agent = fresh_client()
    agent.headers["Authorization"] = f"Bearer {token_for(c, 'read', 'Codex')}"
    try:
        assert agent.get("/api/ai/changes", params={"ids": waiting}).json()["states"] == {waiting: "open"}
        for path, body in ((f"/api/ai/changes/{waiting}", {"decision": "keep"}),
                           ("/api/ai/changes/keep", {"ids": [waiting]})):
            r = agent.post(path, json=body)
            assert r.status_code == 403, (path, r.text)
    finally:
        agent.close()
    assert c.post("/api/ai/changes/clear", json={"ids": [waiting]}).status_code == 422
    assert _states(c, waiting) == {waiting: "open"}
