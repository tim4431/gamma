"""The folder and label tools — rename_folder, create_label, rename_label,
label_page, delete_folder, delete_label: what each changes and refuses, the
reach rules of a label (library-wide), the deletion cards, and a deleted
folder or label put back from its revert record (POST /api/ai/revert) and
deleted again."""

import json

from conftest import folder_names, label_names, make_folder, make_label
from gamma import ai_tools
from gamma.ai_tools import approval_preview, mcp_tools, run_agent_tool
from gamma.blocks_store import folder_subtree_ids, label_by_name
from gamma.db import connect_pages_db
from gamma.ops import tree_deletion

from ai_fixtures import folder, org, props  # noqa: F401  (org is a fixture)

ORGANIZE_TOOLS = {"rename_folder", "create_label", "rename_label", "label_page", "delete_folder", "delete_label"}


def _page(c, title, **properties):
    r = c.post("/api/blocks", json={"parent_id": "root", "content": title, "properties": properties})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _revert(c, action, redo=False):
    return c.post("/api/ai/revert", json={"kind": action["kind"], "block_id": action["block_id"],
                                          "revert": action["revert"], "redo": redo})


def _filed(c, page_id, tree):
    return props(c, page_id)["properties"].get(tree)


def _sessions(c, bucket):
    r = c.get("/api/chat-history", params={"bucket": bucket})
    assert r.status_code == 200, r.text
    return r.json()["sessions"]


def _scope(folder_id=""):
    """What approval_preview is given: a folder chat that may write."""
    return {**folder(folder_id), "can_write": True}


# --- renames and labels -----------------------------------------------------------

def test_rename_folder_renames_one_tree_row_and_touches_no_page(org):
    c, ids = org
    sub = make_folder(c, "orgA/sub")
    page = _page(c, "orgA page", folders=[sub])
    stamp = props(c, page)["updated_at"]
    text, action = run_agent_tool(ids["ws"], folder(""), "rename_folder",
                                  {"folder": "orgA / sub", "name": "  Sub   two \n"})
    assert text.startswith("ok"), text
    assert (action["kind"], action["from"], action["to"]) == ("rename", "orgA / sub", "orgA / Sub two")
    assert folder_names(c)[sub] == ["orgA", "Sub two"]
    assert props(c, page)["updated_at"] == stamp, "a folder's rename touches none of its pages"
    _, action = run_agent_tool(ids["ws"], folder(""), "rename_folder", {"folder": sub, "name": "Sub two"})
    assert action["noop"] is True
    # A rename that changes only the case is a rename.
    text, _ = run_agent_tool(ids["ws"], folder(""), "rename_folder", {"folder": "orgA / sub two", "name": "SUB TWO"})
    assert text.startswith("ok"), text
    assert folder_names(c)[sub] == ["orgA", "SUB TWO"]


def test_rename_folder_refuses_a_taken_name_a_path_the_root_and_what_is_out_of_reach(org):
    c, ids = org
    x, y = make_folder(c, "orgB/x"), make_folder(c, "orgB/y")
    before = folder_names(c)
    for scope, args, said in [
        (folder(""), {"folder": y, "name": "X"}, 'already a folder "X"'),
        (folder(""), {"folder": y, "name": "a / b"}, "renaming never moves a folder"),
        (folder(""), {"folder": "", "name": "Top"}, "library root"),
        (folder(x), {"folder": y, "name": "z"}, "outside this chat's folder"),
    ]:
        text, action = run_agent_tool(ids["ws"], scope, "rename_folder", args)
        assert text.startswith("error") and said in text, (args, text)
    assert folder_names(c) == before, "nothing changed"
    # In a folder's chat, "" is the folder itself.
    text, _ = run_agent_tool(ids["ws"], folder(x), "rename_folder", {"folder": "", "name": "x2"})
    assert text.startswith("ok"), text
    assert (folder_names(c)[x], folder_names(c)[y]) == (["orgB", "x2"], ["orgB", "y"])


def test_create_label_finds_an_existing_name_ignoring_case(org):
    c, ids = org
    make_label(c, "Qubits")
    text, action = run_agent_tool(ids["ws"], folder(""), "create_label", {"label": "qubits"})
    assert action["noop"] is True and '"Qubits" exists already' in text
    text, action = run_agent_tool(ids["ws"], folder(""), "create_label", {"label": "Fresh label"})
    assert text.startswith("ok"), text
    assert (action["kind"], action["to"]) == ("label", "Fresh label")
    with connect_pages_db(ids["ws"]) as conn:
        assert len(label_by_name(conn, "Fresh label")) == 1
        client = conn.execute("SELECT client FROM page_ops WHERE page_id = 'labels' ORDER BY seq DESC LIMIT 1").fetchone()
    assert client == ("ai",), "the label is logged as the agent's"


def test_label_page_puts_labels_on_and_takes_them_off(org):
    c, ids = org
    keep, gone = make_label(c, "keep-l"), make_label(c, "gone-l")
    shelf = make_folder(c, "orgL")
    page = _page(c, "labelled page", folders=[shelf], labels=[keep, gone, "danglingId12"])
    args = {"page_id": page, "add": ["Brand new", "KEEP-L"], "remove": ["gone-l"]}
    preview, _ = approval_preview(ids["ws"], _scope(), "label_page", args)
    assert preview["diff"] == [["ctx", "keep-l"], ["ctx", ", "], ["del", "gone-l"], ["ctx", ", "], ["ins", "Brand new"]]
    assert _filed(c, page, "labels") == [keep, gone, "danglingId12"], "a preview changes nothing"
    text, action = run_agent_tool(ids["ws"], folder(shelf), "label_page", args)
    assert text.startswith("ok"), text
    with connect_pages_db(ids["ws"]) as conn:
        made = label_by_name(conn, "Brand new")
    # The id that is no label goes; KEEP-L is keep-l, not put on twice.
    assert _filed(c, page, "labels") == [keep, *made]
    assert (action["kind"], action["added"], action["removed"]) == ("label", ["Brand new"], ["gone-l"])
    _, action = run_agent_tool(ids["ws"], folder(shelf), "label_page", args)
    assert action["noop"] is True


def test_label_page_refusals(org):
    c, ids = org
    shelf = make_folder(c, "orgL2")
    page, loose = _page(c, "refused labels", folders=[shelf]), _page(c, "loose labels")
    for args, said in [
        ({"page_id": page, "remove": ["no such label here"]}, "there is no label"),
        ({"page_id": page, "add": ["Same"], "remove": ["same"]}, "added and removed in one call"),
        ({"page_id": page, "add": [], "remove": []}, "`add` or to `remove`"),
        ({"page_id": loose, "add": ["x-label"]}, "outside this chat's scope"),
    ]:
        text, _ = run_agent_tool(ids["ws"], folder(shelf), "label_page", args)
        assert text.startswith("error") and said in text, (args, text)
    text, _ = run_agent_tool(ids["ws"], {"type": "page", "page_id": page}, "label_page",
                             {"page_id": page, "add": ["x-label"]})
    assert "unknown tool" in text
    assert _filed(c, page, "labels") is None


def test_a_label_is_renamed_or_deleted_only_where_every_page_carrying_it_is_reachable(org):
    c, ids = org
    top, inner = make_folder(c, "orgC"), make_folder(c, "orgC/in")
    shared, local = make_label(c, "Shared"), make_label(c, "Local")
    _page(c, "orgC p1", folders=[inner], labels=[shared, local])
    _page(c, "orgC p2", labels=[shared])
    for name, args in [("rename_label", {"label": "Shared", "name": "X"}), ("delete_label", {"label": "Shared"})]:
        text, _ = run_agent_tool(ids["ws"], folder(top), name, args)
        assert text.startswith("error") and "outside this chat's folder" in text, text
    assert run_agent_tool(ids["ws"], folder(top), "rename_label", {"label": "Local", "name": "Local2"})[0].startswith("ok")
    assert run_agent_tool(ids["ws"], folder(""), "rename_label", {"label": "Shared", "name": "Shared2"})[0].startswith("ok")
    assert label_names(c)[local] == "Local2" and label_names(c)[shared] == "Shared2"
    assert run_agent_tool(ids["ws"], folder(top), "delete_label", {"label": "Local2"})[0].startswith("ok")
    unused = make_label(c, "Unused")
    assert run_agent_tool(ids["ws"], folder(top), "delete_label", {"label": "Unused"})[0].startswith("ok")
    assert local not in label_names(c) and unused not in label_names(c)
    # A page in Recently deleted counts by the folders a restore puts it back in.
    binned = make_label(c, "Binned")
    _page(c, "orgC p3", folders=[inner], labels=[binned])
    gone = _page(c, "orgC p4", labels=[binned])
    assert c.delete(f"/api/blocks/{gone}").status_code == 200
    for name, args in [("rename_label", {"label": "Binned", "name": "X"}), ("delete_label", {"label": "Binned"})]:
        text, _ = run_agent_tool(ids["ws"], folder(top), name, args)
        assert text.startswith("error") and "outside this chat's folder" in text, text
    assert label_names(c)[binned] == "Binned"


def test_rename_label_refuses_a_taken_name(org):
    c, ids = org
    make_label(c, "A1")
    b1 = make_label(c, "B1")
    text, _ = run_agent_tool(ids["ws"], folder(""), "rename_label", {"label": "B1", "name": "a1"})
    assert text.startswith("error") and "already a label" in text, text
    _, action = run_agent_tool(ids["ws"], folder(""), "rename_label", {"label": "B1", "name": "B1"})
    assert action["noop"] is True
    assert label_names(c)[b1] == "B1"


def test_a_label_kept_with_a_run_of_spaces_is_found_by_its_one_line_name(org):
    """An import may keep a run of spaces in a label's name, and the model
    writes names on one line: the tools find that label either way and
    make no second one."""
    c, ids = org
    spaced, other = make_label(c, "quantum  optics"), make_label(c, "Optics2")
    page = _page(c, "spaced label page", labels=[spaced])
    before = label_names(c)
    for name in ("quantum  optics", "Quantum optics"):
        text, action = run_agent_tool(ids["ws"], folder(""), "create_label", {"label": name})
        assert action["noop"] is True and '"quantum  optics" exists already' in text, text
        _, action = run_agent_tool(ids["ws"], folder(""), "label_page", {"page_id": page, "add": [name]})
        assert action["noop"] is True
    text, _ = run_agent_tool(ids["ws"], folder(""), "rename_label", {"label": "Optics2", "name": "quantum optics"})
    assert text.startswith("error") and "already a label" in text, text
    assert label_names(c) == before
    text, _ = run_agent_tool(ids["ws"], folder(""), "label_page", {"page_id": page, "remove": ["quantum  optics"]})
    assert text.startswith("ok") and _filed(c, page, "labels") is None, text
    text, action = run_agent_tool(ids["ws"], folder(""), "delete_label", {"label": "quantum  optics"})
    assert text.startswith("ok") and action["block_id"] == spaced, text
    assert other in label_names(c)


# --- deletions and their reverts ---------------------------------------------------

def test_delete_label_takes_it_off_its_pages_and_reverts_under_its_id(org):
    c, ids = org
    doomed, other, new = make_label(c, "Doomed"), make_label(c, "Other"), make_label(c, "New")
    p1, p2 = _page(c, "doomed p1", labels=[doomed]), _page(c, "doomed p2", labels=[other, doomed])
    pos = props(c, doomed)["position"]
    text, action = run_agent_tool(ids["ws"], folder(""), "delete_label", {"label": "Doomed"})
    assert text.startswith("ok") and "no page was deleted" in text, text
    assert (action["kind"], action["block_id"], action["count"]) == ("delete", doomed, 2)
    revert = action["revert"]
    assert revert["tree"] == "labels" and revert["blocks"][0][:4] == [doomed, "labels", pos, "Doomed"]
    assert revert["pages"] == {p1: [doomed], p2: [doomed]}
    assert c.get(f"/api/blocks/{doomed}").status_code == 404
    assert (_filed(c, p1, "labels"), _filed(c, p2, "labels")) == (None, [other])
    assert c.put(f"/api/blocks/{p1}", json={"properties": {"labels": [new]}}).status_code == 200
    r = _revert(c, action)
    assert r.status_code == 200 and r.json()["noop"] is False, r.text
    back = props(c, doomed)
    assert (back["content"], back["parent_id"], back["position"]) == ("Doomed", "labels", pos)
    assert (_filed(c, p1, "labels"), _filed(c, p2, "labels")) == ([new, doomed], [other, doomed])
    assert _revert(c, action).json()["noop"] is True
    assert _revert(c, action, redo=True).status_code == 200
    assert c.get(f"/api/blocks/{doomed}").status_code == 404
    assert _revert(c, action).status_code == 200
    assert props(c, doomed)["content"] == "Doomed"


def test_a_label_revert_stops_when_the_name_is_taken_again(org):
    c, ids = org
    make_label(c, "Twice")
    _, action = run_agent_tool(ids["ws"], folder(""), "delete_label", {"label": "Twice"})
    make_label(c, "twice")
    r = _revert(c, action)
    assert r.status_code == 409 and r.json()["conflict"] == "gone", r.text
    assert [n.lower() for n in label_names(c).values()].count("twice") == 1


def test_delete_folder_keeps_its_pages_and_files_its_chats(org):
    c, ids = org
    top, sub, keep = make_folder(c, "orgT"), make_folder(c, "orgT/sub"), make_folder(c, "orgT-keep")
    a, b = _page(c, "orgT page a", folders=[sub, keep]), _page(c, "orgT page b", folders=[top])
    assert c.put(f"/api/chats/{sub}", json={"messages": [{"role": "user", "text": "orgT sub chat"}]}).status_code == 200
    text, action = run_agent_tool(ids["ws"], folder(""), "delete_folder", {"folder": "orgT"})
    assert text.startswith("ok"), text
    assert (action["count"], action["chats"]) == (2, 1)
    revert = action["revert"]
    assert revert["tree"] == "folders" and revert["blocks"][0][0] == top
    assert {r[0] for r in revert["blocks"]} == {top, sub} and revert["pages"] == {a: [sub], b: [top]}
    with connect_pages_db(ids["ws"]) as conn:
        assert folder_subtree_ids(conn, top) == set()
    assert (_filed(c, a, "folders"), _filed(c, b, "folders")) == ([keep], None)
    assert props(c, a)["parent_id"] == props(c, b)["parent_id"] == "root", "no page was deleted"
    assert any(s["preview"].startswith("orgT sub chat") for s in _sessions(c, "home"))


def test_delete_folder_refuses_the_chats_own_folder_the_root_and_a_shared_folder(org):
    c, ids = org
    top, inner = make_folder(c, "orgD"), make_folder(c, "orgD/inner")
    for scope, args, said in [
        (folder(top), {"folder": ""}, "own folder"),
        (folder(top), {"folder": "orgD"}, "own folder"),
        (folder(inner), {"folder": top}, "outside this chat's folder"),
        (folder(""), {"folder": ""}, "library root"),
    ]:
        text, _ = run_agent_tool(ids["ws"], scope, "delete_folder", args)
        assert text.startswith("error") and said in text, (args, text)
    assert c.post(f"/api/share/folder/{inner}").status_code == 200
    text, _ = run_agent_tool(ids["ws"], folder(""), "delete_folder", {"folder": "orgD"})
    assert text.startswith("error") and '"orgD / inner"' in text and "stop sharing" in text, text
    assert {top, inner} <= set(folder_names(c))
    assert c.delete(f"/api/share-settings/folder/{inner}").status_code == 200
    text, _ = run_agent_tool(ids["ws"], folder(""), "delete_folder", {"folder": "orgD"})
    assert text.startswith("ok"), text
    assert not {top, inner} & set(folder_names(c))


def test_the_approval_card_names_what_a_folder_deletion_takes(org):
    c, ids = org
    top, s1, s2 = make_folder(c, "orgP"), make_folder(c, "orgP/s1"), make_folder(c, "orgP/s2")
    tagged = make_label(c, "orgP-label")
    one = _page(c, "P one", folders=[top], labels=[tagged])
    _page(c, "P two", folders=[s1])
    three = _page(c, "P three", folders=[s2])
    assert c.put(f"/api/blocks/{three}", json={"properties": {"labels": [tagged]}}).status_code == 200
    archived = c.post("/api/chat-history/archive", json={"bucket": s1, "messages": [{"role": "user", "text": "old"}]})
    assert archived.status_code == 200
    for bucket in (s1, s2):
        assert c.put(f"/api/chats/{bucket}", json={"messages": [{"role": "user", "text": "hi"}]}).status_code == 200
    preview, answer = approval_preview(ids["ws"], _scope(), "delete_folder", {"folder": "orgP"})
    assert answer is None
    assert (preview["title"], preview["diff"]) == ("orgP", [["del", "orgP"]])
    assert (preview["subfolders"], preview["subfolder_count"]) == (["orgP / s1", "orgP / s2"], 2)
    assert (preview["pages"], preview["page_count"]) == (["P one", "P two", "P three"], 3)
    assert (preview["chats"], preview["chat_folders"], preview["chat_folder_count"]) == (
        3, ["orgP / s1", "orgP / s2"], 2)
    assert {top, s1, s2} <= set(folder_names(c)) and _filed(c, one, "folders") == [top], "nothing was deleted"
    labelled, _ = approval_preview(ids["ws"], _scope(), "delete_label", {"label": "orgP-label"})
    assert (labelled["pages"], labelled["page_count"]) == (["P one", "P three"], 2)
    # A call its plan refuses shows no card.
    assert c.post(f"/api/share/folder/{s2}").status_code == 200
    try:
        preview, answer = approval_preview(ids["ws"], _scope(), "delete_folder", {"folder": "orgP"})
        assert preview is None and answer.startswith("error:"), answer
    finally:
        c.delete(f"/api/share-settings/folder/{s2}")


def test_a_folder_deletion_reverts_with_its_subfolders_and_filings_and_redoes(org):
    c, ids = org
    top, sub, keep = make_folder(c, "orgF"), make_folder(c, "orgF/sub"), make_folder(c, "orgF-keep")
    a, b = _page(c, "orgF page a", folders=[sub, keep]), _page(c, "orgF page b", folders=[top])
    assert c.put(f"/api/chats/{sub}", json={"messages": [{"role": "user", "text": "orgF sub chat"}]}).status_code == 200
    _, action = run_agent_tool(ids["ws"], folder(""), "delete_folder", {"folder": "orgF"})
    r = _revert(c, action)
    assert r.status_code == 200 and r.json() == {"page_id": "", "noop": False}, r.text
    names = folder_names(c)
    assert (names[top], names[sub]) == (["orgF"], ["orgF", "sub"])
    assert (_filed(c, a, "folders"), _filed(c, b, "folders")) == ([keep, sub], [top])
    # The folder's chat stays where the deletion filed it.
    assert any(s["preview"].startswith("orgF sub chat") for s in _sessions(c, "home"))
    assert c.get(f"/api/chats/{sub}").json()["messages"] == []
    # A redo does not stop a share made since.
    assert c.post(f"/api/share/folder/{top}").status_code == 200
    r = _revert(c, action, redo=True)
    assert r.status_code == 409 and r.json()["conflict"] == "gone", r.text
    assert top in folder_names(c)
    assert c.delete(f"/api/share-settings/folder/{top}").status_code == 200
    assert _revert(c, action, redo=True).status_code == 200
    assert not {top, sub} & set(folder_names(c))
    assert (_filed(c, a, "folders"), _filed(c, b, "folders")) == ([keep], None)


def test_a_folder_revert_stops_when_the_folder_above_is_gone(org):
    c, ids = org
    top = make_folder(c, "orgE")
    make_folder(c, "orgE/child")
    _, action = run_agent_tool(ids["ws"], folder(""), "delete_folder", {"folder": "orgE / child"})
    assert c.delete(f"/api/folders/{top}").status_code == 200
    r = _revert(c, action)
    assert r.status_code == 409 and r.json()["conflict"] == "gone" and "was in was deleted" in r.json()["detail"]


def test_the_folder_and_label_tools_are_folder_chat_tools(org):
    c, ids = org
    page = _page(c, "page chat page")
    for name in ORGANIZE_TOOLS:
        text, _ = run_agent_tool(ids["ws"], {"type": "page", "page_id": page}, name, {"page_id": page})
        assert "unknown tool" in text, (name, text)
    assert not ORGANIZE_TOOLS & mcp_tools()


def test_a_deletion_too_large_to_revert_is_refused_without_a_card(org, monkeypatch):
    c, ids = org
    big, spare = make_label(c, "Big label"), make_label(c, "Spare")
    p1, p2 = _page(c, "big p1", labels=[big]), _page(c, "big p2", labels=[big])
    with connect_pages_db(ids["ws"]) as conn:
        rows, _, _ = tree_deletion(conn, "labels", big)
    # Room for the label's own record, not for its two pages (~34 characters each).
    monkeypatch.setattr(ai_tools, "_DELETE_REVERT_MAX", len(json.dumps(ai_tools._tree_revert("labels", rows, {}))) + 10)
    preview, answer = approval_preview(ids["ws"], _scope(), "delete_label", {"label": "Big label"})
    assert preview is None and "too many to record for a revert" in answer, answer
    text, _ = run_agent_tool(ids["ws"], folder(""), "delete_label", {"label": "Big label"})
    assert text == answer
    assert big in label_names(c) and _filed(c, p1, "labels") == _filed(c, p2, "labels") == [big]
    # A label on no page fits under the same limit.
    text, _ = run_agent_tool(ids["ws"], folder(""), "delete_label", {"label": "Spare"})
    assert text.startswith("ok") and spare not in label_names(c), text
    monkeypatch.undo()
    monkeypatch.setattr(ai_tools, "MAX_OPS", 1)
    top, sub = make_folder(c, "orgBig"), make_folder(c, "orgBig/sub")
    text, _ = run_agent_tool(ids["ws"], folder(""), "delete_folder", {"folder": "orgBig"})
    assert text.startswith("error") and "too large to record for a revert" in text, text
    assert {top, sub} <= set(folder_names(c))
    monkeypatch.undo()
    text, action = run_agent_tool(ids["ws"], folder(""), "delete_label", {"label": "Big label"})
    assert text.startswith("ok") and action["revert"]["tree"] == "labels", text
