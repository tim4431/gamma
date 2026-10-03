"""Reverting one change the agent made to the notes (gamma/ai_revert.py,
POST /api/ai/revert): the note tools record what their change needs to be
undone, and a revert keeps what anyone wrote since — or stops and shows
what forcing it would do."""

import re

import pytest

from conftest import make_folder
from gamma.ai_context import build_messages
from gamma.ai_tools import run_agent_tool
from gamma.db import connect_pages_db

from ai_fixtures import children, folder, org, payload, props  # noqa: F401  (fixtures)


def _page(c, title):
    """A page filed in the folder "sandbox", the tests' folder chat."""
    r = c.post("/api/blocks", json={"parent_id": "root", "content": title, "properties": {}})
    assert r.status_code == 200, r.text
    page = r.json()["id"]
    assert c.put(f"/api/blocks/{page}", json={"properties": {"folders": [make_folder(c, "sandbox")]}}).status_code == 200
    return page


@pytest.fixture
def notes(org):
    """A fresh page per test: top (with child), other."""
    c, ids = org
    page = _page(c, "revert playground")
    made = {}
    for key, parent, content in (("top", page, "top-level idea"), ("child", "top", "supporting detail"),
                                 ("other", page, "second thread")):
        parent = made.get(parent, parent)
        last = children(c, parent)
        before = props(c, last[-1])["position"] if last else None
        r = c.post("/api/blocks", json={"parent_id": parent, "content": content, "properties": {},
                                        "before": before})
        assert r.status_code == 200, r.text
        made[key] = r.json()["id"]
    return c, {**ids, "page": page, **made, "sandbox": make_folder(c, "sandbox")}


def _edit(ids, block_id, **args):
    text, action = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "edit_block", {"block_id": block_id, **args})
    assert text.startswith("ok"), text
    return action


def _revert(c, action, force=False, redo=False):
    return c.post("/api/ai/revert", json={"kind": action["kind"], "block_id": action["block_id"],
                                          "revert": action["revert"], "force": force, "redo": redo})


def _type(c, block_id, content):
    """The user's own edit, as their tab sends it."""
    r = c.put(f"/api/blocks/{block_id}", json={"content": content})
    assert r.status_code == 200, r.text


def test_an_edit_reverts_and_keeps_what_the_user_typed_since(notes):
    c, ids = notes
    action = _edit(ids, ids["child"], mode="patch", find="supporting", content="sharper")
    assert action["revert"] == {"before": "supporting detail", "after": "sharper detail"}
    _type(c, ids["child"], "sharper detail, and my own words")
    r = _revert(c, action)
    assert r.status_code == 200, r.text
    assert r.json() == {"page_id": ids["page"], "noop": False}
    assert props(c, ids["child"])["content"] == "supporting detail, and my own words"
    # The write is the user's, logged apart from the agent's.
    with connect_pages_db(ids["ws"]) as conn:
        client = conn.execute("SELECT client FROM page_ops WHERE page_id = ? ORDER BY seq DESC LIMIT 1",
                              (ids["page"],)).fetchone()[0]
    assert client == "revert"
    # A second click changes nothing.
    assert _revert(c, action).json()["noop"] is True


def test_edits_to_one_block_revert_independently(notes):
    c, ids = notes
    _type(c, ids["child"], "alpha beta gamma delta")
    first = _edit(ids, ids["child"], mode="patch", find="alpha", content="ALPHA")
    _edit(ids, ids["child"], mode="patch", find="delta", content="DELTA")
    assert _revert(c, first).status_code == 200
    assert props(c, ids["child"])["content"] == "alpha beta gamma DELTA"


def test_an_edit_the_user_changed_inside_asks_before_forcing(notes):
    c, ids = notes
    _type(c, ids["child"], "one two three. tail")
    action = _edit(ids, ids["child"], mode="patch", find="one two three", content="the AI's sentence")
    _type(c, ids["child"], "the user's sentence. tail, and more")
    r = _revert(c, action)
    assert r.status_code == 409
    body = r.json()
    assert body["conflict"] == "changed"
    kinds = {kind for kind, _ in body["preview"]["diff"]}
    assert {"del", "ins"} <= kinds
    assert props(c, ids["child"])["content"] == "the user's sentence. tail, and more"  # nothing written
    # Forced: the agent's change goes, the user's change that doesn't overlap it stays.
    r = _revert(c, action, force=True)
    assert r.status_code == 200, r.text
    assert props(c, ids["child"])["content"] == "one two three. tail, and more"


def test_an_edit_of_a_deleted_note_is_gone(notes):
    c, ids = notes
    action = _edit(ids, ids["other"], mode="append", content="more")
    assert c.delete(f"/api/blocks/{ids['other']}").status_code == 200
    r = _revert(c, action)
    assert r.status_code == 404 and "deleted" in r.json()["detail"]


def test_a_new_note_reverts_unless_it_was_filled_in(notes):
    c, ids = notes
    text, action = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "create_block",
                                  {"parent_id": ids["page"], "content": "from the AI"})
    new_id = re.search(r"\[([^\]]+)\]", text).group(1)
    assert action["revert"]["after"] == "from the AI" and action["revert"]["parent"] == ids["page"]
    assert _revert(c, action).status_code == 200
    assert new_id not in children(c, ids["page"])
    assert _revert(c, action).json()["noop"] is True  # gone already
    # Typed in since: the revert says so, with what deleting it would lose.
    text, action = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "create_block",
                                  {"parent_id": ids["page"], "content": "from the AI"})
    new_id = re.search(r"\[([^\]]+)\]", text).group(1)
    _type(c, new_id, "from the AI, then mine")
    r = _revert(c, action)
    assert r.status_code == 409 and r.json()["conflict"] == "filled"
    assert r.json()["preview"]["children"] == 0
    assert _revert(c, action, force=True).status_code == 200
    assert new_id not in children(c, ids["page"])


def test_a_move_reverts_to_its_old_place(notes):
    c, ids = notes
    order = children(c, ids["page"])
    text, action = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "move_block",
                                  {"block_id": ids["other"], "parent_id": ids["top"]})
    assert text.startswith("ok"), text
    assert action["revert"]["parent"] == ids["page"] and action["revert"]["to_parent"] == ids["top"]
    assert _revert(c, action).status_code == 200
    assert children(c, ids["page"]) == order
    assert _revert(c, action).json()["noop"] is True
    # Moved on by the user since: asked first.
    run_agent_tool(ids["ws"], folder(ids["sandbox"]), "move_block", {"block_id": ids["other"], "parent_id": ids["top"]})
    _, action = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "move_block",
                               {"block_id": ids["other"], "parent_id": ids["child"]})
    r = c.post(f"/api/pages/{ids['page']}/ops", json={"client": "tab", "ops": [
        {"op": "move", "id": ids["other"], "parent": ids["page"]}]})
    assert r.status_code == 200, r.text
    r = _revert(c, action)
    assert r.status_code == 409 and r.json()["conflict"] == "moved"
    assert _revert(c, action, force=True).status_code == 200
    assert ids["other"] in children(c, ids["top"])


def test_a_move_whose_old_parent_is_gone_cannot_revert(notes):
    c, ids = notes
    _, action = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "move_block",
                               {"block_id": ids["child"], "parent_id": ids["other"]})
    assert c.delete(f"/api/blocks/{ids['top']}").status_code == 200
    r = _revert(c, action, force=True)
    assert r.status_code == 409 and r.json()["conflict"] == "gone"


def test_a_move_across_pages_reverts_across(notes):
    c, ids = notes
    other_page = _page(c, "second page")
    text, action = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "move_block",
                                  {"block_id": ids["top"], "parent_id": other_page})
    assert text.startswith("ok"), text
    assert ids["top"] in children(c, other_page)
    r = _revert(c, action)
    assert r.status_code == 200, r.text
    assert r.json()["page_id"] == ids["page"]
    assert ids["top"] in children(c, ids["page"]) and ids["top"] not in children(c, other_page)
    assert props(c, ids["child"])["parent_id"] == ids["top"]  # its subtree came along


def test_a_malformed_change_is_refused(notes):
    c, ids = notes
    r = c.post("/api/ai/revert", json={"kind": "edit", "block_id": ids["child"], "revert": {"before": 3}})
    assert r.status_code == 400
    r = c.post("/api/ai/revert", json={"kind": "rename", "block_id": ids["child"], "revert": {}})
    assert r.status_code == 422


def test_a_reverted_edit_redoes_and_keeps_what_the_user_typed_since(notes):
    c, ids = notes
    action = _edit(ids, ids["child"], mode="patch", find="supporting", content="sharper")
    assert _revert(c, action).status_code == 200
    _type(c, ids["child"], "supporting detail, and my own words")
    r = _revert(c, action, redo=True)
    assert r.status_code == 200, r.text
    assert r.json() == {"page_id": ids["page"], "noop": False}
    assert props(c, ids["child"])["content"] == "sharper detail, and my own words"
    assert _revert(c, action, redo=True).json()["noop"] is True
    # Back and forth: it reverts again.
    assert _revert(c, action).status_code == 200
    assert props(c, ids["child"])["content"] == "supporting detail, and my own words"


def test_a_redo_over_a_rewrite_asks_before_forcing(notes):
    c, ids = notes
    _type(c, ids["child"], "one two three. tail")
    action = _edit(ids, ids["child"], mode="patch", find="one two three", content="the AI's sentence")
    assert _revert(c, action).status_code == 200
    _type(c, ids["child"], "my own sentence. tail")
    r = _revert(c, action, redo=True)
    assert r.status_code == 409 and r.json()["conflict"] == "changed"
    assert props(c, ids["child"])["content"] == "my own sentence. tail"
    assert _revert(c, action, redo=True, force=True).status_code == 200
    assert props(c, ids["child"])["content"] == "the AI's sentence. tail"


def test_a_reverted_new_note_comes_back_in_its_place(notes):
    c, ids = notes
    text, action = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "create_block",
                                  {"parent_id": ids["page"], "content": "from the AI", "after_id": ids["top"]})
    new_id = re.search(r"\[([^\]]+)\]", text).group(1)
    order = children(c, ids["page"])
    assert _revert(c, action).status_code == 200
    r = _revert(c, action, redo=True)
    assert r.status_code == 200, r.text
    assert r.json() == {"page_id": ids["page"], "noop": False}
    assert children(c, ids["page"]) == order  # the same id, between the same siblings
    assert props(c, new_id)["content"] == "from the AI"
    assert _revert(c, action, redo=True).json()["noop"] is True
    # Its parent gone, there is nowhere to put it.
    _, nested = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "create_block", {"parent_id": ids["other"], "content": "x"})
    assert _revert(c, nested).status_code == 200
    assert c.delete(f"/api/blocks/{ids['other']}").status_code == 200
    r = _revert(c, nested, redo=True)
    assert r.status_code == 409 and r.json()["conflict"] == "gone"


def test_a_reverted_move_redoes_unless_moved_since(notes):
    c, ids = notes
    _, action = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "move_block",
                               {"block_id": ids["other"], "parent_id": ids["top"]})
    assert action["revert"]["to_position"]
    assert _revert(c, action).status_code == 200
    assert _revert(c, action, redo=True).status_code == 200
    assert ids["other"] in children(c, ids["top"])
    assert _revert(c, action).status_code == 200
    r = c.post(f"/api/pages/{ids['page']}/ops", json={"client": "tab", "ops": [
        {"op": "move", "id": ids["other"], "parent": ids["child"]}]})
    assert r.status_code == 200, r.text
    r = _revert(c, action, redo=True)
    assert r.status_code == 409 and r.json()["conflict"] == "moved"
    # An action recorded without `to_position` goes last under its parent.
    old = {**action, "revert": {k: v for k, v in action["revert"].items() if k != "to_position"}}
    assert _revert(c, old, redo=True, force=True).status_code == 200
    assert children(c, ids["top"])[-1] == ids["other"]


def test_the_replay_tells_the_model_a_change_was_reverted():
    history = [
        {"role": "user", "text": "fix it"},
        {"role": "ai", "text": "Done.", "actions": [
            {"kind": "edit", "tool": "edit_block", "args": {"block_id": "b1"}, "result": "ok — block [b1] updated",
             "reverted": True},
            {"kind": "edit", "tool": "edit_block", "args": {"block_id": "b2"}, "result": "ok — block [b2] updated"},
        ]},
    ]
    messages = build_messages(payload(history), "", with_tools=True)
    tools = [m["content"] for m in messages if m["role"] == "tool"]
    assert tools[0].startswith("[the user reverted this change")
    assert not tools[1].startswith("[the user reverted")
