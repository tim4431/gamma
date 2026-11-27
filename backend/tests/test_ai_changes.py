"""Reviewing an agent's changes (gamma/agent_changes.py, /api/ai/changes):
every change the chat's tools make is journaled for the account it was made
for and waits for review until the person keeps or undoes it, from the
note or the chat alike; one allowed on its card is kept as it is written;
a kept one still marks its note until the mark is cleared, and a change
undone anywhere replays to the model as undone. Keeping and clearing are
the person's review: an integration token does neither."""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import fresh_client, make_folder, token_for
from gamma import agent_changes, collab
from gamma.ai_tools.registry import run_agent_tool
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


def _scope(ids, actor=None):
    """The chat's scope as its router makes it: whose, who writes, where it journals."""
    return {"type": "folder", "folder": ids["sandbox"], "actor": actor or ids["user_id"],
            "agent": {"id": "gamma", "name": "Gamma AI", "for": ids["user"]},
            "journal": {"source": "chat", "token": ""}}


def _edit(ids, block_id, content, asked=False, actor=None):
    text, action = run_agent_tool(ids["ws"], _scope(ids, actor), "edit_block",
                                  {"block_id": block_id, "mode": "append", "content": content}, asked=asked)
    assert text.startswith("ok"), text
    return action


def _marked(c, page_id):
    """The page's changes its note draws, as (id, state)."""
    r = c.get("/api/ai/changes", params={"page_id": page_id})
    assert r.status_code == 200, r.text
    return [(x["id"], x["state"]) for x in r.json()["changes"]]


def _open(c, page_id):
    r = c.get("/api/ai/changes", params={"page_id": page_id})
    return [x for x in r.json()["changes"] if x["state"] == "open"]


def _states(c, *ids):
    return c.get("/api/ai/changes", params={"ids": ",".join(ids)}).json()["states"]


def _decide(c, change_id, decision, force=False):
    return c.post(f"/api/ai/changes/{change_id}", json={"decision": decision, "force": force})


def test_a_change_waits_until_it_is_kept(page, monkeypatch):
    c, ids = page
    told = []
    monkeypatch.setattr(collab, "publish_account", lambda user, message: told.append((user, message)))
    action = _edit(ids, ids["first"], " (sharper)")
    change = action["change_id"]
    [drawn] = _open(c, ids["page"])
    assert drawn["id"] == change and drawn["block_id"] == ids["first"] and drawn["source"] == "chat"
    assert drawn["revert"] == action["revert"] and action["revert"]["before"] == "the first idea"
    assert drawn["agent"]["name"] == "Gamma AI"
    assert told == [(ids["user_id"], {"t": "ai_changes", "ws": ids["ws"], "page_id": ids["page"]})]
    r = _decide(c, change, "keep")
    assert r.status_code == 200 and r.json()["state"] == "kept", r.text
    assert _open(c, ids["page"]) == [] and _states(c, change) == {change: "kept"}
    assert props(c, ids["first"])["content"] == action["revert"]["after"]
    assert len(told) == 2
    # kept, it still marks its note until the mark is cleared or its time is up; the change stands
    assert _marked(c, ids["page"]) == [(change, "kept")]
    [kept] = c.get("/api/ai/changes", params={"page_id": ids["page"], "marks_for": 600}).json()["changes"]
    assert kept["id"] == change and 0 <= kept["kept_ago"] < 60
    with connect_pages_db(ids["ws"]) as conn:  # kept two minutes ago
        conn.execute("UPDATE agent_changes SET kept_at = ? WHERE id = ?",
                     (format_stamp(datetime.now(timezone.utc) - timedelta(minutes=2)), change))
        conn.commit()
    assert c.get("/api/ai/changes", params={"page_id": ids["page"], "marks_for": 60}).json()["changes"] == []
    assert _marked(c, ids["page"]) == [(change, "kept")], "without a time, every mark that stands"
    r = c.post("/api/ai/changes/clear", json={"ids": [change, "no-such-change"]})
    assert r.status_code == 200 and r.json() == {"cleared": [change]}, r.text
    assert _marked(c, ids["page"]) == [] and _states(c, change) == {change: "kept"}
    assert len(told) == 3


def test_undo_takes_it_back_and_redo_keeps_it(page):
    c, ids = page
    action = _edit(ids, ids["second"], "plus more")
    change = action["change_id"]
    r = _decide(c, change, "undo")
    assert r.status_code == 200 and r.json()["state"] == "undone", r.text
    assert props(c, ids["second"])["content"] == "the second idea"
    assert _open(c, ids["page"]) == [] and _states(c, change) == {change: "undone"}
    assert _decide(c, change, "undo").json()["noop"] is True
    # a Keep still drawn somewhere keeps nothing undone, and says so
    r = _decide(c, change, "keep")
    assert r.json() == {"state": "undone", "page_id": ids["page"], "noop": True}, r.text
    assert c.post("/api/ai/changes/keep", json={"ids": [change]}).json() == {"kept": []}
    assert _states(c, change) == {change: "undone"}
    r = _decide(c, change, "redo")
    assert r.status_code == 200 and r.json()["state"] == "kept", r.text
    assert props(c, ids["second"])["content"] == action["revert"]["after"]
    assert _decide(c, change, "keep").json() == {"state": "kept", "page_id": ids["page"], "noop": True}


def test_an_undo_that_would_lose_typing_stops_until_forced(page):
    c, ids = page
    change = _edit(ids, ids["first"], " by the AI")["change_id"]
    assert c.put(f"/api/blocks/{ids['first']}", json={"content": "the first idea by me"}).status_code == 200
    r = _decide(c, change, "undo")
    assert r.status_code == 409 and r.json()["conflict"] == "changed", r.text
    assert _states(c, change) == {change: "open"}
    assert _decide(c, change, "undo", force=True).status_code == 200
    assert _states(c, change) == {change: "undone"}


def test_a_change_allowed_on_its_card_waits_for_nothing(page):
    c, ids = page
    change = _edit(ids, ids["first"], " (asked)", asked=True)["change_id"]
    assert change and _open(c, ids["page"]) == [] and _states(c, change) == {change: "kept"}
    assert (change, "kept") in _marked(c, ids["page"]), "its note marks it, folded"
    # an open one is no mark to clear: it is kept or undone first
    waiting = _edit(ids, ids["second"], " (unasked)")["change_id"]
    assert c.post("/api/ai/changes/clear", json={"ids": [waiting]}).json() == {"cleared": []}


def test_keep_all_keeps_only_the_accounts_own(page):
    c, ids = page
    mine = [_edit(ids, ids["first"], " one")["change_id"], _edit(ids, ids["second"], " two")["change_id"]]
    theirs = _edit(ids, ids["first"], " three", actor="someone-else")["change_id"]
    assert [x["id"] for x in _open(c, ids["page"])] == mine
    r = c.post("/api/ai/changes/keep", json={"ids": [*mine, theirs]})
    assert r.status_code == 200 and sorted(r.json()["kept"]) == sorted(mine), r.text
    assert _states(c, *mine, theirs) == {mine[0]: "kept", mine[1]: "kept"}
    assert _decide(c, theirs, "undo").status_code == 404
    assert agent_changes.find(ids["ws"], theirs)["state"] == "open"


def test_a_change_undone_anywhere_replays_as_undone(page):
    """The replay reads the journal, not the mark the chat saved: an undo
    from the note reaches the model though the conversation was not
    marked, and a redo clears a stale mark."""
    c, ids = page
    undone = _edit(ids, ids["first"], " gone")["change_id"]
    redone = _edit(ids, ids["second"], " back")["change_id"]
    assert _decide(c, undone, "undo").status_code == 200
    assert _decide(c, redone, "undo").status_code == 200 and _decide(c, redone, "redo").status_code == 200
    assert (undone, "undone") not in _marked(c, ids["page"]) and (redone, "kept") in _marked(c, ids["page"])
    history = [{"role": "ai", "text": "done", "actions": [
        {"tool": "edit_block", "change_id": undone}, {"tool": "edit_block", "change_id": redone, "reverted": True},
        {"tool": "edit_block", "change_id": "gone-since", "reverted": True}, {"tool": "read_page"}]}]
    agent_changes.mark_history(ids["ws"], ids["user_id"], history)
    actions = history[0]["actions"]
    assert actions[0]["reverted"] is True and actions[1]["reverted"] is False
    assert actions[2]["reverted"] is True and "reverted" not in actions[3]


def test_the_chat_replays_a_change_undone_from_its_note_as_undone(page, ai_provider, monkeypatch):
    """Through the chat's own route: a reply's saved action naming a change
    undone since from its note, which the chat never marked, reaches the
    model as reverted."""
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


def test_a_token_keeps_and_clears_nothing(page):
    """The token an assistant works through, which may have made the
    changes, cannot mark them reviewed: Keep, Keep All and Clear marks
    answer it 403, and the change still waits for the person."""
    c, ids = page
    waiting = _edit(ids, ids["first"], " (unreviewed)")["change_id"]
    kept = _edit(ids, ids["second"], " (asked)", asked=True)["change_id"]
    agent = fresh_client()
    agent.headers["Authorization"] = f"Bearer {token_for(c, 'read', 'Codex')}"
    try:
        assert agent.get("/api/ai/changes", params={"ids": waiting}).json()["states"] == {waiting: "open"}
        for path, body in ((f"/api/ai/changes/{waiting}", {"decision": "keep"}),
                           ("/api/ai/changes/keep", {"ids": [waiting]}), ("/api/ai/changes/clear", {"ids": [kept]})):
            r = agent.post(path, json=body)
            assert r.status_code == 403, (path, r.text)
    finally:
        agent.close()
    assert _states(c, waiting, kept) == {waiting: "open", kept: "kept"}
    assert (kept, "kept") in _marked(c, ids["page"])
