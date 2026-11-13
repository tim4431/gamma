"""Sub-pages (docs/research/sub-pages.md): a page of the library whose
``properties.parent_page`` names the page it is inside. The effective-parent
rule and its helpers (gamma/blocks_store.py: a dangling parent, one in
Recently deleted, a cycle and an over-deep chain read as no parent), the
row writers' and the op path's rules for the key, POST /api/pages with
``parent_page``, GET /api/pages/{id}/family, the library listing's
``parent``, the filing rule (a sub-page is filed where its top is), the
share scope's reach, a duplicate, a work folder's file (never a sub-page,
and no page is one of it), and migration step 47."""

import json
import sqlite3
from contextlib import closing

import pytest

from conftest import (account_of, doc_props, fresh_client, login, make_folder, make_label, make_user,
                      mark_work_folder, work_props, workspace_of)
from gamma import blocks_store, db, migrations, seed
from gamma.auth import DISK_CLIENT, ShareScope
from gamma.blocks_store import (MAX_PAGE_DEPTH, create_page, page_ancestors, page_descendants, page_in_folder,
                                page_parent, page_parents, page_root_props, page_top, pages_in_folder, pages_in_scope,
                                root_pages, sub_pages, valid_parent_value, with_sub_pages)
from gamma.db import connect_pages_db, connect_users_db
from gamma.integrations import create_token
from gamma.ops import LOCKED, SYNC_CLIENT, OpError, commit_ops

USER, PASSWORD = "sp_owner", "pw-sp-1"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


def conn():
    return connect_pages_db(workspace_of(USER))


def _page(c, title, **body):
    r = c.post("/api/pages", json={"title": title, **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _set(c, page_id, props):
    return c.post(f"/api/pages/{page_id}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": page_id, "props": props}]})


def _props(page_id):
    with conn() as cx:
        return json.loads(cx.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()[0])


def _raw_parent(page_id, parent):
    """Store ``parent`` as the page's ``parent_page`` past every writer's
    rule (what merged copies could leave), its row's time kept."""
    with conn() as cx:
        cx.execute("UPDATE unified_blocks SET properties = json_set(properties, '$.parent_page', ?) WHERE id = ?",
                   (parent, page_id))
        cx.commit()


def _tree(c, tag):
    """A, with B and D inside it (in that order) and C inside B."""
    a = _page(c, f"{tag} A")
    b = _page(c, f"{tag} B", parent_page=a)
    c_ = _page(c, f"{tag} C", parent_page=b)
    d = _page(c, f"{tag} D", parent_page=a)
    return a, b, c_, d


# ---- the helpers ---------------------------------------------------------------

def test_the_helpers_follow_the_effective_chain(owner):
    a, b, c, d = _tree(owner, "help")
    lone = _page(owner, "help lone")
    with conn() as cx:
        parents = page_parents(cx)
        assert {k: parents[k] for k in (b, c, d)} == {b: a, c: b, d: a} and a not in parents and lone not in parents
        assert [k for k in parents if k in (b, c, d)] == [b, c, d]  # library order
        assert [page_parent(cx, p) for p in (a, b, c, d)] == ["", a, b, a]
        assert page_ancestors(cx, c) == [a, b] and page_ancestors(cx, a) == []
        assert page_top(cx, c) == a and page_top(cx, a) == a and page_top(cx, lone) == lone
        assert sub_pages(cx, a) == [b, d] and sub_pages(cx, b) == [c] and sub_pages(cx, c) == []
        assert page_descendants(cx, a) == [b, c, d] and page_descendants(cx, lone) == []
        assert with_sub_pages(cx, [a, lone]) == [a, b, c, d, lone]
        assert with_sub_pages(cx, [b, a]) == [b, c, a, d]  # no repeats
        assert with_sub_pages(cx, [a], parents) == [a, b, c, d]
        assert page_descendants(cx, "no-such-page") == [] and page_ancestors(cx, "no-such-page") == []


def test_a_dangling_trashed_or_non_page_parent_reads_as_no_parent(owner):
    gone = _page(owner, "dangle gone")
    assert owner.delete(f"/api/blocks/{gone}").status_code == 200  # to Recently deleted
    host = _page(owner, "dangle host")
    note = owner.post("/api/blocks", json={"parent_id": host, "content": "a note"}).json()["id"]
    cases = {}
    for name, target in (("dangling", "noSuchPageId"), ("trashed", gone), ("a note", note)):
        page = _page(owner, f"dangle {name}")
        assert _set(owner, page, {"parent_page": target}).status_code == 200  # stored as written
        assert _props(page)["parent_page"] == target
        cases[page] = target
    with conn() as cx:
        parents = page_parents(cx)
        for page in cases:
            assert page not in parents and page_parent(cx, page) == "" and page_ancestors(cx, page) == []
            assert page_top(cx, page) == page
        assert sub_pages(cx, gone) == [] and page_descendants(cx, gone) == []  # a page in the trash holds none
    listing = {p["id"]: p for p in owner.get("/api/blocks/root/children").json()["children"]}
    assert all(listing[page]["parent"] == "" for page in cases)


def test_a_cycle_reads_as_library_pages(owner):
    p, q = _page(owner, "cycle P"), _page(owner, "cycle Q")
    r = _page(owner, "cycle R", parent_page=p)
    _raw_parent(p, q)
    _raw_parent(q, p)
    with conn() as cx:
        parents = page_parents(cx)
        for page in (p, q, r):
            assert page not in parents and page_parent(cx, page) == "" and page_ancestors(cx, page) == []
            assert sub_pages(cx, page) == [] and page_descendants(cx, page) == []
    # a page's own parent is a cycle too
    s = _page(owner, "cycle S")
    _raw_parent(s, s)
    with conn() as cx:
        assert s not in page_parents(cx) and page_parent(cx, s) == "" and sub_pages(cx, s) == []


def test_a_chain_holds_at_most_max_page_depth_pages(owner):
    with conn() as cx:
        chain = [create_page(cx, "deep 0", actor=account_of(USER))["id"]]
        for i in range(1, MAX_PAGE_DEPTH + 1):  # one page more than a chain holds
            chain.append(create_page(cx, f"deep {i}", {"parent_page": chain[-1]}, actor=account_of(USER))["id"])
        parents = page_parents(cx)
        deepest = chain[MAX_PAGE_DEPTH - 1]
        assert page_ancestors(cx, deepest) == chain[:MAX_PAGE_DEPTH - 1] and parents[deepest] == chain[-3]
        assert chain[-1] not in parents and page_parent(cx, chain[-1]) == ""  # the 65th reads as a library page
        assert sub_pages(cx, deepest) == [] and page_descendants(cx, chain[0]) == chain[1:MAX_PAGE_DEPTH]
        for page in chain:  # one page's walk agrees with the workspace's read
            assert page_parent(cx, page) == parents.get(page, "")
    extra = _page(owner, "deep extra")
    r = _set(owner, extra, {"parent_page": deepest})
    assert r.status_code == 400 and r.json()["conflict"] == "cycle", r.text
    assert r.json()["detail"] == f"pages nest at most {MAX_PAGE_DEPTH} deep"
    assert _set(owner, extra, {"parent_page": chain[MAX_PAGE_DEPTH - 2]}).status_code == 200


def test_the_row_writers_keep_a_parent_of_the_right_shape():
    assert page_root_props({"parent_page": "pg1"}, "me") == {"parent_page": "pg1"}  # a dangling id is kept
    for bad in ("me", "root", "trash", "folders", "a b", "", 5, None, ["pg1"]):
        assert page_root_props({"parent_page": bad, "x": 1}, "me") == {"x": 1}, bad
        assert not valid_parent_value(bad, "me")
    assert valid_parent_value("pg1") and not valid_parent_value("x" * 65)


# ---- the op rule -------------------------------------------------------------

def test_the_op_path_checks_a_parent(owner):
    a, b, c, d = _tree(owner, "op")
    folder = make_folder(owner, "sp-op")
    page = _page(owner, "op page", folders=[folder])
    r = _set(owner, page, {"parent_page": page})
    assert r.status_code == 400 and r.json() == {"detail": "a page cannot be inside itself", "conflict": "cycle",
                                                 "index": 0}
    r = _set(owner, a, {"parent_page": c})  # c is inside a
    assert r.status_code == 400 and r.json()["conflict"] == "cycle", r.text
    assert r.json()["detail"] == "a page cannot be inside one of its own sub-pages"
    for bad in (5, "root", "a b", ["x"]):
        r = _set(owner, page, {"parent_page": bad})
        assert r.status_code == 400 and r.json() == {"detail": "parent_page must be a page id"}, r.text
    assert _props(page)["folders"] == [folder]

    r = _set(owner, page, {"parent_page": d})  # a parent drops the page's own folders
    assert r.status_code == 200 and r.json()["ops"][0]["props"] == {"parent_page": d, "folders": None}
    assert _props(page) == {"parent_page": d}
    assert _set(owner, page, {"parent_page": None, "folders": [folder]}).status_code == 200  # Move to library
    assert _props(page) == {"folders": [folder]}
    assert _set(owner, page, {"parent_page": "notArrivedYet"}).status_code == 200  # dangling: stored
    assert _props(page) == {"parent_page": "notArrivedYet"}
    assert _set(owner, page, {"parent_page": ""}).status_code == 200  # "" removes it too
    assert _props(page) == {}


def test_a_share_editor_cannot_move_a_page(owner):
    a, b, _, _ = _tree(owner, "shed")
    token = owner.post(f"/api/share/{a}", json={"audience": "anyone", "role": "edit"}).json()["token"]
    anon = fresh_client()
    r = anon.post(f"/api/pages/{b}/ops", params={"share": token}, json={"client": "t", "ops": [
        {"op": "set", "id": b, "props": {"parent_page": None}}]})
    assert r.status_code == 403, r.text
    r = anon.post(f"/api/pages/{b}/ops", params={"share": token}, json={"client": "t", "ops": [
        {"op": "insert", "id": "spShareNote", "parent": b, "content": "edited through the parent's link"}]})
    assert r.status_code == 200, r.text  # the page share of a reaches b
    assert _props(b) == {"parent_page": a}


# ---- creating one --------------------------------------------------------------

def test_post_pages_makes_a_sub_page_of_a_live_page(owner):
    folder = make_folder(owner, "sp-create")
    top = _page(owner, "create top", folders=[folder])
    r = owner.post("/api/pages", json={"title": "inside", "parent_page": top, "folders": [folder]})
    assert r.status_code == 200, r.text
    assert r.json()["properties"] == {"parent_page": top}  # its folders are its top's
    gone = _page(owner, "create gone")
    owner.delete(f"/api/blocks/{gone}")
    note = owner.post("/api/blocks", json={"parent_id": top, "content": "a note"}).json()["id"]
    for target in ("noSuchPage", gone, note, "root"):
        r = owner.post("/api/pages", json={"title": "orphan", "parent_page": target})
        assert r.status_code == 400 and r.json()["detail"] == "parent_page names no page", (target, r.text)
    # the publishing mirror's path: the key inside properties, stored by its shape alone
    r = owner.post("/api/pages", json={"title": "mirrored", "properties": {"parent_page": "notArrivedYet"}})
    assert r.status_code == 200 and r.json()["properties"] == {"parent_page": "notArrivedYet"}
    r = owner.post("/api/pages", json={"title": "mirrored", "id": "spSelf", "properties": {"parent_page": "spSelf"}})
    assert r.status_code == 200 and r.json()["properties"] == {}


def test_a_duplicate_is_a_sibling_sub_page(owner):
    a, b, c, _ = _tree(owner, "dup")
    src = owner.get(f"/api/blocks/{b}").json()
    r = owner.post("/api/blocks", json={"parent_id": "root", "content": f"{src['content']} (copy)",
                                        "properties": src["properties"]})
    assert r.status_code == 200, r.text
    copy = r.json()["id"]
    with conn() as cx:
        assert page_parent(cx, copy) == a and sub_pages(cx, copy) == [] and copy in sub_pages(cx, a)


# ---- the family and the listing ------------------------------------------------

def test_the_family_names_the_ancestors_and_the_sub_pages(owner):
    a, b, c, d = _tree(owner, "fam")
    with conn() as cx:
        cx.execute("UPDATE unified_blocks SET content = '' WHERE id = ?", (d,))
        cx.commit()
        stamps = dict(cx.execute("SELECT id, updated_at FROM unified_blocks WHERE id IN (?, ?)", (b, d)).fetchall())
    r = owner.get(f"/api/pages/{a}/family")
    assert r.status_code == 200, r.text
    assert r.json() == {"ancestors": [], "sub_pages": [
        {"id": b, "title": "fam B", "updated_at": stamps[b]}, {"id": d, "title": "Untitled", "updated_at": stamps[d]}]}
    assert owner.get(f"/api/pages/{c}/family").json() == {
        "ancestors": [{"id": a, "title": "fam A"}, {"id": b, "title": "fam B"}], "sub_pages": []}
    assert owner.get("/api/pages/noSuchPage/family").status_code == 404
    note = owner.post("/api/blocks", json={"parent_id": c, "content": "a note"}).json()["id"]
    assert owner.get(f"/api/pages/{note}/family").status_code == 404  # a block that is no page
    gone = _page(owner, "fam gone")
    owner.delete(f"/api/blocks/{gone}")
    assert owner.get(f"/api/pages/{gone}/family").status_code == 404


def test_the_family_says_which_sub_pages_carry_a_pdf(owner):
    """A sub-page carrying a document (``page_document``: stored, at an
    address, a copy) is marked with its ``format``, for its glyph under a
    share too."""
    a = _page(owner, "fampdf A")
    with conn() as cx:
        stored = create_page(cx, "fampdf stored", {"parent_page": a, **doc_props("d" * 24)},
                             actor=account_of(USER))["id"]
        linked = create_page(cx, "fampdf linked", {"parent_page": a, **doc_props(url="https://example.org/x.pdf")},
                             actor=account_of(USER))["id"]
        copy = create_page(cx, "fampdf copy", {"parent_page": a, **doc_props(url=f"/api/uploads/{'d' * 24}.pdf")},
                           actor=account_of(USER))["id"]
    note = _page(owner, "fampdf note", parent_page=a)
    kids = {x["id"]: x for x in owner.get(f"/api/pages/{a}/family").json()["sub_pages"]}
    assert [kids[k].get("format") for k in (stored, linked, copy, note)] == ["pdf", "pdf", "pdf", None]
    assert not any("pdf" in kid for kid in kids.values())
    anon = fresh_client()
    share = {"share": owner.post(f"/api/share/{a}", json={"audience": "anyone"}).json()["token"]}
    shared = anon.get(f"/api/pages/{a}/family", params=share).json()["sub_pages"]
    assert [(x["id"], x.get("format")) for x in shared] == [(stored, "pdf"), (linked, "pdf"), (copy, "pdf"),
                                                             (note, None)]


def test_a_share_reads_the_family_it_reaches(owner):
    a, b, c, d = _tree(owner, "famshare")
    anon = fresh_client()
    top = {"share": owner.post(f"/api/share/{a}", json={"audience": "anyone"}).json()["token"]}
    r = anon.get(f"/api/pages/{c}/family", params=top)  # a page share of the parent reaches the child
    assert r.status_code == 200 and [x["id"] for x in r.json()["ancestors"]] == [a, b], r.text
    assert anon.get(f"/api/blocks/{c}/subtree", params=top).status_code == 200
    mid = {"share": owner.post(f"/api/share/{b}", json={"audience": "anyone"}).json()["token"]}
    r = anon.get(f"/api/pages/{b}/family", params=mid)  # the ancestors above the share are left out
    assert r.status_code == 200 and r.json()["ancestors"] == [] and [x["id"] for x in r.json()["sub_pages"]] == [c]
    assert [x["id"] for x in anon.get(f"/api/pages/{c}/family", params=mid).json()["ancestors"]] == [b]
    assert anon.get(f"/api/pages/{a}/family", params=mid).status_code == 403
    assert anon.get(f"/api/pages/{d}/family", params=mid).status_code == 403
    assert anon.get("/api/pages/noSuchPage/family", params=mid).status_code == 403

    folder = make_folder(owner, "sp-famshare")
    assert _set(owner, a, {"folders": [folder]}).status_code == 200
    filed = {"share": owner.post(f"/api/share/folder/{folder}", json={"audience": "anyone"}).json()["token"]}
    r = anon.get(f"/api/pages/{c}/family", params=filed)  # a folder share reaches a sub-page of a filed page
    assert r.status_code == 200 and [x["id"] for x in r.json()["ancestors"]] == [a, b], r.text
    listing = anon.get("/api/blocks/root/children", params=filed).json()["children"]
    assert [(p["id"], p["parent"]) for p in listing] == [(a, ""), (b, a), (c, b), (d, a)]


def test_the_listing_names_the_parent_and_previews_a_sub_page_too(owner):
    """A sub-page's card (the pinned and recents strips, a label view)
    shows its first notes as a library page's does."""
    a, b, c, _ = _tree(owner, "list")
    for page in (a, b):
        owner.post("/api/blocks", json={"parent_id": page, "content": f"note of {page}"})
    listing = {p["id"]: p for p in owner.get("/api/blocks/root/children").json()["children"]}
    assert listing[a]["parent"] == "" and listing[a]["preview"] == f"note of {a}"
    assert listing[b]["parent"] == a and listing[b]["preview"] == f"note of {b}"
    assert listing[c]["parent"] == b and listing[c]["preview"] == ""  # no notes, no preview


# ---- filing and shares ---------------------------------------------------------

def test_a_sub_page_is_filed_where_its_top_is(owner):
    folder, sub = make_folder(owner, "sp-file"), make_folder(owner, "sp-file/sub")
    other = make_folder(owner, "sp-other")
    tag = make_label(owner, "sp-tag")
    a = _page(owner, "file A", folders=[sub])
    b = _page(owner, "file B", properties={"parent_page": a, "folders": [other], "labels": [tag]})  # its own folders
    c = _page(owner, "file C", parent_page=b)
    beside = _page(owner, "file beside", folders=[folder])
    with conn() as cx:
        assert pages_in_folder(cx, folder) == [a, b, c, beside]
        assert pages_in_folder(cx, other) == []
        assert page_in_folder(cx, c, folder) and page_in_folder(cx, b, sub) and not page_in_folder(cx, b, other)
        assert pages_in_scope(cx, [folder]) == {a, b, c, beside}
        assert pages_in_scope(cx, [folder, tag]) == {b}  # a label is the page's own
        assert set(root_pages(cx, folder)) == {a, b, c, beside} and {a, b, c} <= set(root_pages(cx))
        # a sub-page whose parent goes reads as a library page, filed by its own folders again
        _raw_parent(b, "noSuchPage")
        assert pages_in_folder(cx, folder) == [a, beside] and pages_in_folder(cx, other) == [b, c]
        _raw_parent(b, a)


def test_the_share_scope_reaches_the_pages_inside(owner):
    a, b, c, d = _tree(owner, "scope")
    lone = _page(owner, "scope lone")
    note = owner.post("/api/blocks", json={"parent_id": c, "content": "a note"}).json()["id"]
    folder = make_folder(owner, "sp-scope")
    assert _set(owner, a, {"folders": [folder]}).status_code == 200
    with conn() as cx:
        top, mid = ShareScope(page=a), ShareScope(page=b)
        assert all(top.allows_page(cx, p) for p in (a, b, c, d)) and not top.allows_page(cx, lone)
        assert mid.allows_page(cx, c) and not mid.allows_page(cx, a) and not mid.allows_page(cx, d)
        assert top.pages(cx) == [a, b, c, d] and mid.pages(cx) == [b, c]
        filed = ShareScope(folder=folder)
        assert filed.pages(cx) == [a, b, c, d] and filed.allows_page(cx, c) and not filed.allows_page(cx, lone)
        assert top.allows_block(cx, note) and mid.allows_block(cx, note)
        assert not ShareScope(page=d).allows_block(cx, note)
        assert mid.block_ids(cx) == {b, c, note}
    assert owner.delete(f"/api/blocks/{b}").status_code == 200
    with conn() as cx:
        assert not ShareScope(page=b).allows_page(cx, b) and ShareScope(page=b).pages(cx) == []


def test_a_parent_in_recently_deleted_is_walked_too(owner):
    folder = make_folder(owner, "sp-trashwalk")
    p = _page(owner, "trashwalk P", folders=[folder])
    q = _page(owner, "trashwalk Q", parent_page=p)
    kid = _page(owner, "trashwalk kid", parent_page=q)
    assert owner.delete(f"/api/blocks/{q}").status_code == 200  # q goes on its own (kid with it)
    r = _set(owner, p, {"parent_page": q})  # its restore would close the cycle
    assert r.status_code == 400 and r.json()["conflict"] == "cycle", r.text
    assert _props(p) == {"folders": [folder]}
    # a cycle stored past the op path (merged copies): the restore leaves the parent out
    _raw_parent(p, q)
    assert owner.post(f"/api/trash/{q}/restore").status_code == 200
    assert "parent_page" not in _props(q) and _props(kid)["parent_page"] == q
    with conn() as cx:
        assert page_parents(cx).get(p) == q and page_parent(cx, kid) == q and page_top(cx, kid) == q


def test_the_mirror_stores_a_merged_cycle(owner):
    a, b, _, _ = _tree(owner, "mcycle")
    mirror = fresh_client()
    mirror.headers["Authorization"] = "Bearer " + create_token(account_of(USER), workspace_of(USER), "sp-mirror", 90,
                                                               scope="write")["token"]
    r = mirror.post(f"/api/pages/{a}/ops", json={"client": "sync", "ops": [
        {"op": "set", "id": a, "props": {"parent_page": b}}]})
    assert r.status_code == 200, r.text  # the engine's own batch: what two copies merged is stored
    with conn() as cx:
        assert a not in page_parents(cx) and b not in page_parents(cx)  # read as library pages
    assert _set(owner, a, {"parent_page": b}).status_code == 400  # any other writer is refused
    r = owner.post(f"/api/pages/{b}/ops", json={"client": "sync", "ops": [  # a session's "sync" is no mirror's
        {"op": "set", "id": b, "props": {"parent_page": a}}]})
    assert r.status_code == 400 and r.json()["conflict"] == "cycle", r.text


def test_no_page_is_inside_a_work_folder_s_file_and_no_file_inside_a_page(owner):
    work = make_folder(owner, "sp-work")
    mark_work_folder(owner, work)
    lib = _page(owner, "sp-work library page")
    with conn() as cx:
        file = create_page(cx, "notes", work_props(work, "notes.md"), actor=account_of(USER),
                           client=DISK_CLIENT)["id"]
        for client in ("", DISK_CLIENT):  # a person, and the vault: neither makes a page inside a file
            with pytest.raises(OpError) as e:
                create_page(cx, "inside a file", {"parent_page": file}, actor=account_of(USER), client=client)
            assert e.value.status == 403 and e.value.conflict == LOCKED
        assert create_page(cx, "the mirror's", {"parent_page": file}, actor=account_of(USER),
                           client=SYNC_CLIENT)["properties"] == {"parent_page": file}
    r = _set(owner, lib, {"parent_page": file})
    assert r.status_code == 403 and r.json()["conflict"] == LOCKED, r.text
    r = _set(owner, file, {"parent_page": lib})
    assert r.status_code == 403 and r.json()["conflict"] == LOCKED, r.text
    with pytest.raises(OpError) as e:  # the vault is held to the shape too
        commit_ops(workspace_of(USER), file, [{"op": "set", "id": file, "props": {"parent_page": lib}}],
                   actor=account_of(USER), client=DISK_CLIENT)
    assert e.value.conflict == LOCKED
    assert "parent_page" not in _props(file) and "parent_page" not in _props(lib)


def test_a_parent_change_revalidates_the_share_peers(owner, monkeypatch):
    from gamma import collab, ops
    assert ops._refiles({"page_id": "pg1", "ops": [{"op": "set", "id": "pg1", "props": {"parent_page": None}}]})
    assert not ops._refiles({"page_id": "pg1", "ops": [{"op": "set", "id": "pg1", "props": {"title": "x"}}]})
    a, b, _, _ = _tree(owner, "reval")
    calls = []
    monkeypatch.setattr(collab, "revalidate_shares", lambda ws: calls.append(ws))
    assert _set(owner, b, {"parent_page": None}).status_code == 200  # out of a's share, no folders sent
    assert calls == [workspace_of(USER)]


def test_the_inbox_reaches_an_invitee_of_a_parent_s_share(owner):
    from gamma import inbox
    make_user("sp_guest", "pw-sp-guest")
    guest = account_of("sp_guest")
    a, b, c, _ = _tree(owner, "inbox")
    r = owner.post(f"/api/share/{a}", json={"audience": "list", "users": [{"name": "sp_guest", "role": "edit"}]})
    assert r.status_code == 200, r.text
    outer = r.json()["token"]
    inner = owner.post(f"/api/share/{b}", json={"audience": "anyone"}).json()["token"]
    ws = workspace_of(USER)
    assert [s["token"] for s in inbox._shares_to(ws, c)] == [inner, outer]  # the nearest page share first
    assert inbox.reach(ws, c, [guest]) == {guest: outer}  # named by the parent's share
    assert inbox.reach(ws, c, [guest], via=outer) == {guest: outer}


def test_a_folder_share_lists_its_pages_and_the_pages_inside_them(owner):
    folder, below = make_folder(owner, "sp-flist"), make_folder(owner, "sp-flist/below")
    a = _page(owner, "flist A", folders=[folder])
    b = _page(owner, "flist B", parent_page=a)
    c = _page(owner, "flist C", folders=[below])
    d = _page(owner, "flist D", parent_page=c)
    _page(owner, "flist outside")
    _page(owner, "flist inside outside", parent_page=_page(owner, "flist other top"))
    token = owner.post(f"/api/share/folder/{folder}", json={"audience": "anyone"}).json()["token"]
    listing = fresh_client().get("/api/blocks/root/children", params={"share": token}).json()["children"]
    assert [(p["id"], p["parent"]) for p in listing] == [(a, ""), (b, a), (c, ""), (d, c)]


def test_a_clip_files_a_sub_page_where_its_top_is(owner):
    from gamma.routers import clip
    reading, physics = make_folder(owner, "sp-clip-reading"), make_folder(owner, "sp-clip-physics")
    top = _page(owner, "clip top", folders=[reading])
    sub = _page(owner, "clip sub", parent_page=top)
    tag = make_label(owner, "sp-clip-tag")
    ws = workspace_of(USER)
    with conn() as cx:
        block = blocks_store.block_to_dict(cx.execute(
            f"SELECT {blocks_store.BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (sub,)).fetchone())
        assert clip._result(cx, block, True)["folders"] == [reading]  # where the popup says it is
        block = clip._file_page(cx, ws, "x", block, physics, [tag])
        out = clip._result(cx, block, True)
        assert out["folders"] == [reading, physics] and out["labels"] == [tag]
        assert sub in pages_in_folder(cx, physics)
    assert _props(sub) == {"parent_page": top, "labels": [tag]}  # the folder went to the top
    assert _props(top)["folders"] == [reading, physics]


# ---- migration step 47 --------------------------------------------------------

def test_step_47_gives_a_workspace_stamped_46_the_parent_page_column(data_dir):
    connect_users_db().close()
    seed.create_workspace_files("spOld")
    path = data_dir / "workspaces" / "spOld" / "pages.db"
    db.close_workspace_connections("spOld")
    with closing(sqlite3.connect(str(path))) as old:  # the file as release 46 left it
        db.register_functions(old)
        for index in ("listing", "dir", "work_folder", "deleted_with", "parent_page"):
            old.execute(f"DROP INDEX idx_ub_{index}")
        for column in ("first_folder", "work_folder", "deleted_with", "parent_page"):  # (48's and 53's came after)
            old.execute(f"ALTER TABLE unified_blocks DROP COLUMN {column}")
        old.execute("INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, "
                    "updated_at, page_id) VALUES ('spKid', 'root', 'a5', 'kid', '{\"parent_page\": \"spTop\"}', "
                    "'t', 't', 'spKid')")
        old.execute("INSERT INTO page_changes (page_id, seq, kind, at, actor) "
                    "SELECT 'spKid', COALESCE(MAX(seq), 0) + 1, 'live', 't', '' FROM page_changes")
        old.execute("PRAGMA user_version = 46")
        old.commit()
        assert "parent_page" not in {r[1] for r in old.execute("PRAGMA table_xinfo(unified_blocks)")}
    try:
        with connect_pages_db("spOld") as cx:
            assert cx.execute("SELECT parent_page FROM unified_blocks WHERE id = 'spKid'").fetchone() == ("spTop",)
            assert cx.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_ub_parent_page'").fetchone()
            assert cx.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION  # the newest step
            ddl = cx.execute("SELECT sql FROM sqlite_master WHERE name = 'unified_blocks'").fetchone()[0]
            migrations._v47_parent_page("spOld", cx, None)  # re-runnable: nothing to do
            assert cx.execute("SELECT sql FROM sqlite_master WHERE name = 'unified_blocks'").fetchone()[0] == ddl
            cols = [r[1] for r in cx.execute("PRAGMA table_xinfo(unified_blocks)")]
            assert cols[-7:] == ["page_id", "kind", "doc_id", "parent_page", "deleted_with",  # a fresh table's order
                                 "work_folder", "first_folder"]
    finally:
        db.close_workspace_connections("spOld")
    migrations._v47_parent_page("", sqlite3.connect(":memory:"), None)  # a copy without the table
    assert (47, "parent_page", migrations._v47_parent_page) in migrations.WORKSPACE_STEPS
    assert 47 not in [v for v, _, _ in migrations.STEPS]  # a workspace step only
    # a derived column: copies on either side still sync (the format, 55, is step 55's positions)
    assert 47 in migrations.DERIVED_STEPS and migrations.workspace_format() == 55
    assert blocks_store.PARENT_PAGE == "parent_page"
