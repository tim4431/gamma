"""Recently deleted (gamma/trash.py, ops.trash_page / restore_page): deleting
a page moves it under the reserved ``trash`` block for 30 days. It keeps
its blocks, folder labels and chats; nothing that lists, searches, shares,
reads or writes pages reaches it; a copy of the workspace sees it deleted
(its row of the change log) and, once restored, created again; the sweeper and "Delete
permanently" remove it for good through ops.delete_page."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fractional_indexing import generate_key_between

from conftest import account_of, login, make_folder, make_label, make_page, make_user, workspace_of
from gamma import trash
from gamma.ai_tools import run_agent_tool
from gamma.blocks_store import TRASH
from gamma.db import connect_pages_db


@pytest.fixture(scope="module")
def owner():
    make_user("tr_owner", "pw")
    return login("tr_owner", "pw")


@pytest.fixture(scope="module")
def lab(owner):
    """A shared workspace the owner owns: tr_editor edits, tr_viewer views."""
    make_user("tr_admin", "pw", is_admin=1)
    make_user("tr_editor", "pw")
    make_user("tr_viewer", "pw")
    r = login("tr_admin", "pw").post("/api/workspaces", json={"name": "Trash lab", "kind": "shared",
                                                              "owner": "tr_owner"})
    assert r.status_code == 200, r.text
    ws = r.json()["id"]
    for name, role in (("tr_editor", "editor"), ("tr_viewer", "viewer")):
        assert owner.put(f"/api/workspaces/{ws}/members/{name}", json={"role": role}).status_code == 200
    return ws


def _child(c, parent, content, **kw):
    r = c.post("/api/blocks", json={"parent_id": parent, "content": content}, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def _library(c, **kw):
    return {b["id"] for b in c.get("/api/blocks/root/children", **kw).json()["children"]}


def _trashed(c, **kw):
    r = c.get("/api/trash", **kw)
    assert r.status_code == 200, r.text
    return {p["id"]: p for p in r.json()["pages"]}


def _row(ws, block_id):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT parent_id, properties FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()


def _change(ws, page_id):
    """The page's row of the change log: ``(seq, kind, at, actor)``."""
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT seq, kind, at, actor FROM page_changes WHERE page_id = ?", (page_id,)).fetchone()


def test_trash_and_restore_round_trip(owner):
    ws = workspace_of("tr_owner")
    filed = {"folders": [make_folder(owner, "trips/2026")], "labels": [make_label(owner, "keep")]}
    page = make_page(owner, "Round trip", properties=filed)
    note = _child(owner, page["id"], "the note survives")
    assert owner.put(f"/api/chats/{page['id']}", json={"messages": [{"role": "user", "text": "hi"}]}).status_code == 200

    r = owner.delete(f"/api/blocks/{page['id']}")
    assert r.status_code == 200, r.text
    entry = r.json()["trashed"]
    assert entry["id"] == page["id"] and entry["deleted_by"] == "tr_owner" and entry["folders"] == filed["folders"]

    # gone from the library, listed in Recently deleted with its stamps
    assert page["id"] not in _library(owner)
    listed = _trashed(owner)[page["id"]]
    assert listed["title"] == "Round trip" and listed["deleted_at"] == entry["deleted_at"]
    assert listed["purge_at"] > listed["deleted_at"]
    # the page's content is all still there: blocks, chat
    assert _row(ws, note["id"])[0] == page["id"]
    with connect_pages_db(ws) as conn:
        assert conn.execute("SELECT 1 FROM chats WHERE bucket = ?", (page["id"],)).fetchone()

    restored = owner.post(f"/api/trash/{page['id']}/restore")
    assert restored.status_code == 200, restored.text
    back = restored.json()
    assert back["parent_id"] == "root" and back["properties"] == filed
    assert page["id"] in _library(owner) and page["id"] not in _trashed(owner)
    tree = owner.get(f"/api/blocks/{page['id']}/subtree").json()["block"]
    assert [c["content"] for c in tree["children"]] == ["the note survives"]
    assert owner.get(f"/api/chats/{page['id']}").json()["messages"] == [{"role": "user", "text": "hi"}]
    # a second restore, or a restore of a live page, is a 404
    assert owner.post(f"/api/trash/{page['id']}/restore").status_code == 404


def test_a_trashed_page_reads_as_gone(owner):
    live = make_page(owner, "Stays around")
    target = _child(owner, live["id"], "a live block")
    page = make_page(owner, "Vanishing act")
    note = _child(owner, page["id"], f"zyxwvu hidden words, see [[{target['id']}]]")
    assert owner.get("/api/block-search?q=zyxwvu").json()["blocks"]
    assert [b["id"] for b in owner.get(f"/api/blocks/{target['id']}/backlinks").json()["backlinks"]] == [note["id"]]
    owner.delete(f"/api/blocks/{page['id']}").raise_for_status()

    # the page and its blocks 404; a member is told where the page went
    r = owner.get(f"/api/blocks/{page['id']}/subtree")
    assert r.status_code == 404 and r.json()["trashed"]["id"] == page["id"]
    r = owner.get(f"/api/blocks/{note['id']}")
    assert r.status_code == 404 and r.json()["trashed"]["title"] == "Vanishing act"
    assert owner.get(f"/api/blocks/{page['id']}/children").status_code == 404
    assert owner.get(f"/api/blocks/{TRASH}/subtree").status_code == 404
    assert owner.get(f"/api/blocks/{TRASH}/children").status_code == 404
    assert owner.get("/api/blocks/nothing-here/subtree").json() == {"detail": "block not found"}
    # searches, [[ref]] resolution and backlinks pass it by
    assert owner.get("/api/block-search?q=zyxwvu").json()["blocks"] == []
    assert owner.get(f"/api/block-search?ids={note['id']},{page['id']}").json()["blocks"] == []
    assert page["id"] not in {b["id"] for b in owner.get("/api/block-search").json()["blocks"]}
    assert owner.get(f"/api/blocks/{target['id']}/backlinks").json()["backlinks"] == []
    assert owner.get("/api/search", params={"q": "zyxwvu"}).json()["results"] == []
    # exports of the page itself
    assert owner.get(f"/api/pages/{page['id']}/export", params={"mode": "readable"}).status_code == 404
    # the agent's page tools
    ws = workspace_of("tr_owner")
    scope = {"type": "folder", "folder": ""}
    text, _ = run_agent_tool(ws, scope, "list_pages", {})
    assert page["id"] not in text and live["id"] in text
    text, _ = run_agent_tool(ws, scope, "read_page", {"page_id": page["id"]})
    assert text.startswith("error")
    text, _ = run_agent_tool(ws, scope, "read_block", {"block_id": note["id"]})
    assert text.startswith("error")
    text, _ = run_agent_tool(ws, scope, "search_library", {"query": "zyxwvu"})
    assert page["id"] not in text


def test_ops_to_a_trashed_page_are_refused(owner):
    page = make_page(owner, "Closed for edits")
    note = _child(owner, page["id"], "frozen")
    owner.delete(f"/api/blocks/{page['id']}").raise_for_status()
    r = owner.post(f"/api/pages/{page['id']}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": note["id"], "content": "thawed"}]})
    assert r.status_code == 404
    assert owner.put(f"/api/blocks/{note['id']}", json={"content": "thawed"}).status_code == 404
    assert owner.post("/api/blocks", json={"parent_id": note["id"], "content": "x"}).status_code == 404
    assert owner.delete(f"/api/blocks/{note['id']}").status_code == 404
    assert owner.put(f"/api/blocks/{page['id']}/children", json={"blocks": []}).status_code == 404
    assert owner.post(f"/api/blocks/{note['id']}/reorder", json={}).status_code == 404
    assert owner.delete(f"/api/blocks/{page['id']}").status_code == 404  # already gone
    # a live page cannot pull a trashed block in, nor take the reserved ids
    other = make_page(owner, "Live one")
    r = owner.post(f"/api/pages/{other['id']}/ops", json={"client": "t", "ops": [
        {"op": "move", "id": note["id"], "parent": other["id"]}]})
    assert r.status_code == 403
    for reserved in ("trash", "root"):
        r = owner.post(f"/api/pages/{other['id']}/ops", json={"client": "t", "ops": [
            {"op": "insert", "id": reserved, "parent": other["id"], "position": generate_key_between(None, None)}]})
        assert r.status_code == 400
    assert owner.post("/api/pages", json={"id": "trash"}).status_code == 400
    assert owner.post(f"/api/trash/{page['id']}/restore").status_code == 200
    assert owner.get(f"/api/blocks/{note['id']}").json()["content"] == "frozen"


def test_trash_deletes_the_page_for_a_copy_and_restore_makes_it_again(owner):
    ws = workspace_of("tr_owner")
    page = make_page(owner, "Mirror me")
    made = _change(ws, page["id"])
    owner.delete(f"/api/blocks/{page['id']}").raise_for_status()
    trashed = _change(ws, page["id"])
    assert trashed[0] > made[0] and trashed[1] == "deleted" and trashed[3] == account_of("tr_owner")
    feed = owner.get("/api/sync/changes", params={"since": made[0], "limit": 2000}).json()
    assert page["id"] not in {p["id"] for p in feed["pages"]}
    assert [(d["id"], d["actor"]) for d in feed["deleted"]] == [(page["id"], account_of("tr_owner"))]

    owner.post(f"/api/trash/{page['id']}/restore").raise_for_status()
    assert _change(ws, page["id"])[:2] == (trashed[0] + 1, "live")
    feed = owner.get("/api/sync/changes", params={"since": trashed[0], "limit": 2000}).json()
    entry = next(p for p in feed["pages"] if p["id"] == page["id"])
    assert entry["updated_at"] > trashed[2]  # touched: to a copy it is a page (re)created
    assert feed["deleted"] == []

    # Deleted for good from the trash, the row keeps its trashing: to a copy it went then.
    owner.delete(f"/api/blocks/{page['id']}").raise_for_status()
    first = _change(ws, page["id"])
    assert owner.delete(f"/api/trash/{page['id']}").json() == {"ok": True, "id": page["id"]}
    assert _change(ws, page["id"]) == first
    assert _row(ws, page["id"]) is None


def test_delete_permanently_and_empty_drop_everything(owner):
    ws = workspace_of("tr_owner")
    one, two = make_page(owner, "Forever one"), make_page(owner, "Forever two")
    note = _child(owner, one["id"], "gone for good")
    owner.put(f"/api/chats/{one['id']}", json={"messages": [{"role": "user", "text": "bye"}]}).raise_for_status()
    assert owner.delete(f"/api/trash/{one['id']}").status_code == 404  # not in the trash yet
    for p in (one, two):
        owner.delete(f"/api/blocks/{p['id']}").raise_for_status()
    assert owner.delete(f"/api/trash/{one['id']}").status_code == 200
    assert _row(ws, one["id"]) is None and _row(ws, note["id"]) is None
    assert owner.get(f"/api/chats/{one['id']}").json()["messages"] == []
    assert two["id"] in _trashed(owner)
    emptied = owner.delete("/api/trash")
    assert emptied.status_code == 200 and two["id"] in emptied.json()["deleted"]
    assert _trashed(owner) == {} and _row(ws, two["id"]) is None


def test_the_sweeper_purges_after_thirty_days(owner):
    ws = workspace_of("tr_owner")
    old, recent = make_page(owner, "Old news"), make_page(owner, "Fresh news")
    owner.post("/api/chat-history/archive", json={"bucket": old["id"], "messages": [{"role": "user", "text": "older"}]}
               ).raise_for_status()
    owner.put(f"/api/chats/{old['id']}", json={"messages": [{"role": "user", "text": "old"}]}).raise_for_status()
    for p in (old, recent):
        owner.delete(f"/api/blocks/{p['id']}").raise_for_status()
    deleted_at = datetime.fromisoformat(_trashed(owner)[old["id"]]["deleted_at"].replace("Z", "+00:00"))
    # recent is five days newer: at old's 30th day, only old goes
    with connect_pages_db(ws) as conn:
        props = json.loads(_row(ws, recent["id"])[1])
        props["deleted_at"] = (deleted_at + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        conn.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?", (json.dumps(props), recent["id"]))
        conn.commit()
    assert trash.purge_expired(ws, now=deleted_at + timedelta(days=29)) == []
    swept = trash.sweep(now=deleted_at + timedelta(days=30, minutes=1))
    assert swept.get(ws) == [old["id"]]
    assert _row(ws, old["id"]) is None and _row(ws, recent["id"])[0] == TRASH
    with connect_pages_db(ws) as conn:  # its chats went with it: the active one and the history
        assert conn.execute("SELECT 1 FROM chats WHERE bucket = ? UNION ALL "
                            "SELECT 1 FROM chat_history WHERE bucket = ?", (old["id"], old["id"])).fetchone() is None
    assert trash.purge_expired(ws, now=datetime.now(timezone.utc) + timedelta(days=36)) == [recent["id"]]


def test_permissions(owner, lab):
    editor, viewer = login("tr_editor", "pw"), login("tr_viewer", "pw")
    h = {"X-Gamma-Workspace": lab}
    page = owner.post("/api/blocks", json={"parent_id": "root", "content": "Lab page"}, headers=h).json()
    doomed = owner.post("/api/blocks", json={"parent_id": "root", "content": "Lab doomed"}, headers=h).json()
    # a viewer can neither trash nor restore nor purge; everyone in it may look
    assert viewer.delete(f"/api/blocks/{page['id']}", headers=h).status_code == 403
    assert editor.delete(f"/api/blocks/{doomed['id']}", headers=h).status_code == 200
    assert _trashed(viewer, headers=h)[doomed["id"]]["deleted_by"] == "tr_editor"
    assert viewer.post(f"/api/trash/{doomed['id']}/restore", headers=h).status_code == 403
    assert viewer.delete(f"/api/trash/{doomed['id']}", headers=h).status_code == 403
    assert viewer.delete("/api/trash", headers=h).status_code == 403
    assert editor.post(f"/api/trash/{doomed['id']}/restore", headers=h).status_code == 200

    # a share editor (an invited account, not a member) reaches no trash at all
    make_user("tr_outsider", "pw")
    outsider = login("tr_outsider", "pw")
    token = owner.post(f"/api/share/{page['id']}", headers=h).json()["token"]
    owner.put(f"/api/share-settings/{page['id']}", headers=h,
              json={"audience": "list", "users": [{"name": "tr_outsider", "role": "edit"}]}).raise_for_status()
    q = {"share": token}
    assert outsider.delete(f"/api/blocks/{page['id']}", params=q).status_code == 403
    owner.delete(f"/api/blocks/{page['id']}", headers=h).raise_for_status()
    assert outsider.get("/api/trash", params={"ws": lab}).status_code == 403
    assert outsider.post(f"/api/trash/{page['id']}/restore", params={"ws": lab}).status_code == 403


def test_deleted_by_is_stored_as_the_id_and_shown_by_name(owner, lab):
    """A trashed page's ``deleted_by`` is an actor, like the op log's: the
    account's id in the page's properties, its username wherever it is
    shown — the delete's answer, Recently deleted, a 404's trash entry, the
    agent's list_deleted. An account deleted since shows as nobody; the
    labels of writers that are no account show as they are."""
    ws, owner_id = workspace_of("tr_owner"), account_of("tr_owner")
    page = make_page(owner, "Whodunit")
    entry = owner.delete(f"/api/blocks/{page['id']}").json()["trashed"]
    assert json.loads(_row(ws, page["id"])[1])["deleted_by"] == owner_id
    assert entry["deleted_by"] == "tr_owner"
    assert _trashed(owner)[page["id"]]["deleted_by"] == "tr_owner"
    assert owner.get(f"/api/blocks/{page['id']}/subtree").json()["trashed"]["deleted_by"] == "tr_owner"
    text, _ = run_agent_tool(ws, {"type": "folder", "folder": ""}, "list_deleted", {"title_contains": "whodunit"})
    assert " by tr_owner " in text and owner_id not in text

    # an editor of the lab deletes a page there, then the account goes
    from gamma import workspaces
    make_user("tr_gone", "pw")
    assert owner.put(f"/api/workspaces/{lab}/members/tr_gone", json={"role": "editor"}).status_code == 200
    h = {"X-Gamma-Workspace": lab}
    gone = login("tr_gone", "pw")
    doomed = gone.post("/api/blocks", json={"parent_id": "root", "content": "Orphaned"}, headers=h).json()
    assert gone.delete(f"/api/blocks/{doomed['id']}", headers=h).status_code == 200
    gone_id = account_of("tr_gone")
    workspaces.delete_account(gone_id)
    assert json.loads(_row(lab, doomed["id"])[1])["deleted_by"] == gone_id  # nothing rewritten
    assert _trashed(owner, headers=h)[doomed["id"]]["deleted_by"] == ""

    # a share-link visitor's and a mirror round's labels (what ops.trash_page
    # stamps for them), set on a trashed page's properties as they would be
    for actor in ("link:Visitor", "mirror"):
        other = make_page(owner, f"Deleted by {actor}")
        owner.delete(f"/api/blocks/{other['id']}").raise_for_status()
        props = json.loads(_row(ws, other["id"])[1])
        with connect_pages_db(ws) as conn:
            conn.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?",
                         (json.dumps({**props, "deleted_by": actor}), other["id"]))
        assert _trashed(owner)[other["id"]]["deleted_by"] == actor


def test_shares_never_reach_a_trashed_page(owner, anon):
    sharedf = make_folder(owner, "sharedf")
    page = make_page(owner, "Shared then trashed", properties={"folders": [sharedf]})
    sibling = make_page(owner, "Stays shared", properties={"folders": [sharedf]})
    token = owner.post(f"/api/share/{page['id']}").json()["token"]
    owner.put(f"/api/share-settings/{page['id']}", json={"audience": "anyone", "role": "edit"}).raise_for_status()
    folder = owner.post(f"/api/share/folder/{sharedf}").json()["token"]
    assert anon.get(f"/api/share/{token}").status_code == 200
    owner.put(f"/api/chats/{page['id']}", json={"messages": [{"role": "user", "text": "x"}]}).raise_for_status()

    owner.delete(f"/api/blocks/{page['id']}").raise_for_status()
    assert anon.get(f"/api/share/{token}").status_code == 404
    q = {"share": token}
    assert anon.get(f"/api/blocks/{page['id']}/subtree", params=q).status_code == 403
    assert anon.get(f"/api/blocks/{page['id']}", params=q).status_code == 403
    assert anon.get(f"/api/chats/{page['id']}", params=q).status_code == 403
    r = anon.post(f"/api/pages/{page['id']}/ops", params=q, json={"client": "t", "ops": [
        {"op": "set", "id": page["id"], "content": "hijack"}]})
    assert r.status_code == 403
    # the folder share lists and opens only the live page
    fq = {"share": folder}
    assert _library(anon, params=fq) == {sibling["id"]}
    assert anon.get(f"/api/blocks/{page['id']}", params=fq).status_code == 403
    # the owner cannot manage the share of a page in the trash; a restore brings it back working
    assert owner.get(f"/api/share-settings/{page['id']}").status_code == 404
    owner.post(f"/api/trash/{page['id']}/restore").raise_for_status()
    assert anon.get(f"/api/share/{token}").status_code == 200
    assert anon.get(f"/api/blocks/{page['id']}/subtree", params=q).status_code == 200
    assert _library(anon, params=fq) == {page["id"], sibling["id"]}


def test_mcp_lists_and_reads_no_trashed_page(client, owner):
    from test_mcp import call

    page = make_page(owner, "Mcp trashed paper")
    note = _child(owner, page["id"], "QwertyTrashEvidence")
    item = owner.post("/api/integrations/tokens", json={"name": "Codex"}).json()
    try:
        owner.delete(f"/api/blocks/{page['id']}").raise_for_status()
        listed = call(client, item["token"], "list_pages", {"title_contains": "Mcp trashed"})
        assert page["id"] not in listed["content"][0]["text"]
        for name, args in (("read_page", {"page_id": page["id"]}), ("read_block", {"block_id": note["id"]}),
                           ("export_page", {"page_id": page["id"]})):
            result = call(client, item["token"], name, args)
            assert result["isError"] or page["id"] not in result["content"][0]["text"], (name, result)
        found = call(client, item["token"], "search_library", {"query": "QwertyTrashEvidence"})
        assert page["id"] not in found["content"][0]["text"]
    finally:
        owner.delete(f"/api/integrations/tokens/{item['id']}")


def test_a_page_brought_back_under_its_id_replaces_the_trashed_copy(owner):
    ws = workspace_of("tr_owner")
    page = make_page(owner, "Comes back")
    stale = _child(owner, page["id"], "the trashed copy's block")
    owner.delete(f"/api/blocks/{page['id']}").raise_for_status()
    # a mirror pushing the page as new (POST /pages with its id): the trashed copy gives way
    r = owner.post("/api/pages", json={"id": page["id"], "title": "Comes back (theirs)"})
    assert r.status_code == 200, r.text
    assert _row(ws, page["id"])[0] == "root" and _row(ws, stale["id"]) is None
    assert page["id"] not in _trashed(owner) and _change(ws, page["id"])[1] == "live"
    assert owner.post("/api/pages", json={"id": page["id"]}).status_code == 409  # a live one does not

    # a block the trash still holds, inserted into a live page, leaves the trashed copy
    moved = make_page(owner, "Source page")
    block = _child(owner, moved["id"], "moved out before the delete")
    owner.delete(f"/api/blocks/{moved['id']}").raise_for_status()
    target = make_page(owner, "Target page")
    r = owner.post(f"/api/pages/{target['id']}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": block["id"], "parent": target["id"], "position": generate_key_between(None, None),
         "content": "moved out before the delete"}]})
    assert r.status_code == 200, r.text
    assert _row(ws, block["id"])[0] == target["id"]
    assert moved["id"] in _trashed(owner)


def test_a_share_host_deletes_for_good(owner, monkeypatch):
    from gamma import cloud_auth

    real = cloud_auth.settings
    monkeypatch.setattr(cloud_auth, "settings", lambda: {**real(), "share_host": True})
    ws = workspace_of("tr_owner")
    page = make_page(owner, "Published copy")
    r = owner.delete(f"/api/blocks/{page['id']}")
    assert r.status_code == 200 and "trashed" not in r.json()
    assert _row(ws, page["id"]) is None and page["id"] not in _trashed(owner)


def test_the_sweeper_leaves_an_unchanged_workspace_alone_until_a_page_is_due(owner, monkeypatch):
    """Between two sweeps nothing in the workspace changed and no page came
    due: the second sweep does not open it. A write (the WAL dates it), or
    the due time passing, brings it back into the sweep."""
    ws = workspace_of("tr_owner")
    page = make_page(owner, "Due later")
    owner.delete(f"/api/blocks/{page['id']}").raise_for_status()
    deleted_at = datetime.fromisoformat(_trashed(owner)[page["id"]]["deleted_at"].replace("Z", "+00:00"))
    reads = []
    real = trash._trashed
    monkeypatch.setattr(trash, "_trashed", lambda w: reads.append(w) or real(w))
    trash._due.clear()
    trash.sweep(now=deleted_at + timedelta(days=1))
    assert reads.count(ws) >= 1
    reads.clear()
    trash.sweep(now=deleted_at + timedelta(days=2))
    assert ws not in reads, "nothing changed, nothing due: not opened"
    make_page(owner, "A write")  # the workspace changed: looked at again
    trash.sweep(now=deleted_at + timedelta(days=3))
    assert ws in reads
    reads.clear()
    trash.sweep(now=deleted_at + timedelta(days=4))
    assert ws not in reads
    swept = trash.sweep(now=deleted_at + timedelta(days=30, minutes=1))
    assert ws in reads and page["id"] in swept.get(ws, []), "due: swept, and the page went"
    assert _row(ws, page["id"]) is None
