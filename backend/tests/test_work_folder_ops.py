"""The work-folder rule on the op path and the page writers
(docs/dev/work_folders.md "The op rule"; gamma/work_folders/filing.py is
its pure core, tests/shared/workfiling.json its cases): every shape (S),
root-write (R) and folder-tree (T) row, by each writer — a person in a
session, a share editor, a session that claims the vault's ``disk`` (a
person all the same), an integration token claiming ``disk`` (the vault)
and one claiming ``sync`` (the mirror). Then the page writers
(``create_page``, ``insert_page_tree``, POST /api/pages, POST /blocks at
the root), the library's bulk actions, Recently deleted, a folder's
deletion, the agent's preview of one, ``get_or_create_doc_page`` on a file,
the walks by name and the quiet stamps."""

import json
import secrets
from types import SimpleNamespace

import pytest
from fractional_indexing import generate_key_between

from conftest import (account_of, fresh_client, login, make_folder, make_label, make_user, mark_work_folder,
                      work_props, workspace_of)
from gamma import storage
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import (FOLDERS, create_page, folder_by_path, folder_inserts, get_or_create_doc_page,
                                insert_page_tree)
from gamma.db import connect_pages_db
from gamma.documents.model import document
from gamma.integrations import create_token
from gamma.ops import LOCKED, SYNC_CLIENT, OpError, commit_ops, restore_page, trash_page, tree_deletion
from gamma.work_folders.filing import refusal_text

USER, PASSWORD = "wfo_owner", "pw-wfo-1"
EDITOR = "wfo_editor"

PERSON, SHARE_EDITOR, SESSION_DISK, VAULT, MIRROR = (
    "person", "share editor", "session claiming disk", "token claiming disk", "mirror")
WRITERS = (PERSON, SHARE_EDITOR, SESSION_DISK, VAULT, MIRROR)
TREE_WRITERS = (PERSON, SESSION_DISK, VAULT, MIRROR)  # no share link reaches a tree


@pytest.fixture(scope="module")
def w():
    """The owner's session, a share editor's, a write token's; a work folder
    W that pulls (directories D and D2), one N that does not (ND), library
    folders L and L2 (inside L) and a label."""
    make_user(USER, PASSWORD)
    make_user(EDITOR, "pw-wfo-2")
    owner, ws = login(USER, PASSWORD), workspace_of(USER)
    token = fresh_client()
    token.headers["Authorization"] = "Bearer " + create_token(account_of(USER), ws, "wfo", 90, scope="write")["token"]
    token.headers["X-Gamma-Workspace"] = ws
    ids = {name: make_folder(owner, path) for name, path in (
        ("W", "wfo-W"), ("D", "wfo-W/dir"), ("D2", "wfo-W/dir2"), ("N", "wfo-N"), ("ND", "wfo-N/dir"),
        ("L", "wfo-L"), ("L2", "wfo-L/sub"))}
    mark_work_folder(owner, ids["W"], pull=True)
    mark_work_folder(owner, ids["N"], pull=False)
    return SimpleNamespace(owner=owner, editor=login(EDITOR, "pw-wfo-2"), token=token, ws=ws,
                           account=account_of(USER), label=make_label(owner, "wfo-label"), **ids)


def _conn(w):
    return connect_pages_db(w.ws)


def _props(w, page_id):
    with _conn(w) as cx:
        return json.loads(cx.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()[0])


def _row(w, block_id):
    with _conn(w) as cx:
        return cx.execute("SELECT parent_id, content, properties, updated_at FROM unified_blocks WHERE id = ?",
                          (block_id,)).fetchone()


def _stored_pdf(w) -> str:
    """The hash of a PDF whose bytes are stored in the workspace."""
    name, _ = storage.store_file(w.ws, b"%PDF-1.4\n" + secrets.token_bytes(16), ".pdf")
    return name[:-len(".pdf")]


def _vault_page(w, title, props) -> str:
    with _conn(w) as cx:
        return create_page(cx, title, props, actor=w.account, client=DISK_CLIENT)["id"]


def _page_of(w, kind) -> str:
    """A fresh page of ``kind``: a PDF of W read in place whose bytes are not
    stored here, the same with its bytes stored, a PDF of W in the library,
    a note of W (it follows), a note of N (it does not), a library page."""
    if kind == "page":
        with _conn(w) as cx:
            return create_page(cx, "a library page", {"folders": [w.L]}, actor=w.account)["id"]
    if kind == "fixed note":
        return _vault_page(w, "fixed", work_props(w.N, "fixed.md", directory=w.ND))
    if kind == "note":
        return _vault_page(w, "notes", work_props(w.W, "notes.md"))
    stored = kind != "pdf"
    digest = _stored_pdf(w) if stored else secrets.token_hex(12)
    doc = document(digest, name="main.pdf", at="path")
    if kind == "library pdf":
        return _vault_page(w, "main", {**work_props(w.W, "main.pdf", library=True, doc=doc), "folders": [w.W, w.L]})
    return _vault_page(w, "main", work_props(w.W, "main.pdf", doc=doc))


def _share(w, page_id) -> dict:
    r = w.owner.post(f"/api/share/{page_id}")
    assert r.status_code == 200, r.text
    r = w.owner.put(f"/api/share-settings/{page_id}", json={"audience": "list",
                                                            "users": [{"name": EDITOR, "role": "edit"}]})
    assert r.status_code == 200, r.text
    return {"share": r.json()["token"]}


def _send(w, writer, page_id, ops):
    """``ops`` on ``page_id`` (a page, or the folders tree) as ``writer``."""
    if writer == SHARE_EDITOR:
        return w.editor.post(f"/api/pages/{page_id}/ops", params=_share(w, page_id), json={"client": "t", "ops": ops})
    client, claimed = {PERSON: (w.owner, "t"), SESSION_DISK: (w.owner, "disk"), VAULT: (w.token, "disk"),
                       MIRROR: (w.token, "sync")}[writer]
    return client.post(f"/api/pages/{page_id}/ops", json={"client": claimed, "ops": ops})


def _refused(r, kind=None):
    assert r.status_code == 403 and r.json().get("conflict") == LOCKED, r.text
    if kind:
        assert r.json()["detail"] == refusal_text(kind)


# ---- a page root's set (S and R) ---------------------------------------------------------

def _doc(w):
    return document(secrets.token_hex(12), name="other.pdf", at="path")


# (row, the page's kind, its props patch (None for none), its new title (None for none),
#  {writer: allowed}); a session claiming disk answers as a person does, and so does a
#  share editor where only the title changes (a props patch of the root is never theirs)
ROOT_CASES = [
    ("R1 work_folder", "pdf", lambda w: {"work_folder": w.N, "folders": [w.ND]}, None,
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("R1 file_name", "pdf", lambda w: {"file_name": "renamed.pdf"}, None, {PERSON: False, VAULT: True, MIRROR: True}),
    ("R2 doc", "pdf", lambda w: {"doc": _doc(w)}, None, {PERSON: False, VAULT: True, MIRROR: True}),
    ("R3 a note that follows to another directory", "note", lambda w: {"folders": [w.D]}, None,
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("R3 a note that follows out of its work folder", "note", lambda w: {"folders": [w.L]}, None,
     {PERSON: False, VAULT: False, MIRROR: True}),
    ("R3 a PDF to another directory", "pdf", lambda w: {"folders": [w.D]}, None,
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("R3 a note where its work folder does not pull", "fixed note", lambda w: {"folders": [w.N]}, None,
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("R4 a library file into a library folder", "library pdf", lambda w: {"folders": [w.W, w.L, w.L2]}, None,
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("R4 a file not in the library into a library folder", "pdf", lambda w: {"folders": [w.W, w.L]}, None,
     {PERSON: False, VAULT: False, MIRROR: True}),
    ("R4 a library file into a second directory", "library pdf", lambda w: {"folders": [w.W, w.D]}, None,
     {PERSON: False, VAULT: False, MIRROR: True}),
    ("R5 a library file's title", "library pdf", None, "A paper", {PERSON: True, VAULT: True, MIRROR: True}),
    ("R5 a note that follows", "note", None, "Plan", {PERSON: True, VAULT: True, MIRROR: True}),
    ("R5 a file not in the library", "pdf", None, "Renamed", {PERSON: False, VAULT: True, MIRROR: True}),
    ("R5 a note that does not follow", "fixed note", None, "Renamed", {PERSON: False, VAULT: True, MIRROR: True}),
    ("R6 a note into the library", "note", lambda w: {"library": True}, None,
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("R6 a PDF whose bytes are stored here", "stored pdf", lambda w: {"library": True}, None,
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("R6 a PDF whose bytes are on another computer", "pdf", lambda w: {"library": True}, None,
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("R6 out of the library", "library pdf", lambda w: {"library": None}, None,
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("R7 labels and a pin", "pdf", lambda w: {"labels": [w.label], "pinned": "a0"}, None,
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("R8 a file inside a page", "pdf", lambda w: {"parent_page": _page_of(w, "page")}, None,
     {PERSON: False, VAULT: False, MIRROR: True}),
    ("S3 a page inside a file", "page", lambda w: {"parent_page": _page_of(w, "note")}, None,
     {PERSON: False, VAULT: False, MIRROR: True}),
    ("S2 a library page into a directory", "page", lambda w: {"folders": [w.D]}, None,
     {PERSON: False, VAULT: False, MIRROR: True}),
    ("S2 a library page into a library folder", "page", lambda w: {"folders": [w.L2]}, None,
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("S4 library on a library page", "page", lambda w: {"library": True}, None,
     {PERSON: False, VAULT: False, MIRROR: True}),
]


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("case", ROOT_CASES, ids=[c[0] for c in ROOT_CASES])
def test_a_page_root_set_by_each_writer(w, case, writer):
    _row_name, kind, patch_of, title, allowed = case
    page = _page_of(w, kind)
    before = _row(w, page)
    op = {"op": "set", "id": page}
    if patch_of:
        op["props"] = patch_of(w)
    if title:
        op["content"] = title
    r = _send(w, writer, page, [op])
    if writer == SHARE_EDITOR and patch_of:
        assert r.status_code == 403 and "conflict" not in r.json(), r.text  # never a share editor's
        return
    if allowed[MIRROR if writer == MIRROR else VAULT if writer == VAULT else PERSON]:
        assert r.status_code == 200, r.text
    else:
        _refused(r)
        assert _row(w, page) == before  # nothing written


def test_a_library_file_taken_out_of_the_library_leaves_its_library_folders(w):
    page = _page_of(w, "library pdf")
    assert _send(w, PERSON, page, [{"op": "set", "id": page, "props": {"library": None}}]).status_code == 200
    props = _props(w, page)
    assert "library" not in props and props["folders"] == [w.W]
    page = _page_of(w, "library pdf")  # the mirror stores what it is given
    assert _send(w, MIRROR, page, [{"op": "set", "id": page, "props": {"library": None}}]).status_code == 200
    assert _props(w, page)["folders"] == [w.W, w.L]


def test_a_file_whose_bytes_another_library_page_owns_stays_out_of_the_library(w):
    digest = _stored_pdf(w)
    with _conn(w) as cx:
        create_page(cx, "The owner", {"doc": document(digest)}, actor=w.account)
    page = _vault_page(w, "main", work_props(w.W, "main.pdf", doc=document(digest, name="main.pdf", at="path")))
    r = _send(w, PERSON, page, [{"op": "set", "id": page, "props": {"library": True}}])
    _refused(r)
    assert r.json()["detail"] == "Already in the library as “The owner”."
    page = _page_of(w, "pdf")
    r = _send(w, PERSON, page, [{"op": "set", "id": page, "props": {"library": True}}])
    _refused(r)
    assert r.json()["detail"] == "Its bytes are on another computer."


def test_a_refused_op_says_which_op_and_why(w):
    page = _page_of(w, "pdf")
    r = _send(w, PERSON, page, [{"op": "insert", "id": secrets.token_urlsafe(9), "parent": page, "content": "a note"},
                                {"op": "set", "id": page, "content": "Renamed"}])
    _refused(r, "named")
    assert r.json()["index"] == 1
    assert _send(w, PERSON, page, [{"op": "set", "id": page, "props": {"folders": [w.L]}}]).json()["detail"] \
        == refusal_text("filed")
    assert _send(w, PERSON, page, [{"op": "set", "id": page, "props": {"doc": _doc(w)}}]).json()["detail"] \
        == refusal_text("document")


def test_the_same_title_and_the_same_stamps_pass(w):
    page = _page_of(w, "pdf")
    props = _props(w, page)
    r = _send(w, PERSON, page, [{"op": "set", "id": page, "content": "main",
                                 "props": {"work_folder": props["work_folder"], "file_name": props["file_name"]}}])
    assert r.status_code == 200, r.text


# ---- the folder tree (T) ---------------------------------------------------------

def _work(w, tag):
    """A work folder that pulls (W) in a library folder (O), with its
    directories D and D2, and a library folder L with L2 inside it."""
    t = SimpleNamespace(**{name: make_folder(w.owner, path) for name, path in (
        ("O", f"wfo-{tag}"), ("W", f"wfo-{tag}/work"), ("D", f"wfo-{tag}/work/dir"),
        ("D2", f"wfo-{tag}/work/dir2"), ("L", f"wfo-{tag}-lib"), ("L2", f"wfo-{tag}-lib/sub"))})
    mark_work_folder(w.owner, t.W)
    return t


def _first_key(w, folder):
    with _conn(w) as cx:
        position = cx.execute("SELECT position FROM unified_blocks WHERE id = ?", (folder,)).fetchone()[0]
    return generate_key_between(None, position)


MARK = {"pull": True, "library": True}

# (row, the ops on the tree, {writer: allowed})
TREE_CASES = [
    ("T1 rename a directory", lambda w, t: [{"op": "set", "id": t.D, "content": "renamed"}],
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("T1 move a directory to another parent", lambda w, t: [{"op": "move", "id": t.D, "parent": t.L}],
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("T1 reorder a directory", lambda w, t: [{"op": "move", "id": t.D2, "parent": t.W,
                                               "position": _first_key(w, t.D)}],
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("T1 pin a directory", lambda w, t: [{"op": "set", "id": t.D, "props": {"pinned": "a0"}}],
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("T1 a folder in a directory", lambda w, t: [{"op": "insert", "id": secrets.token_urlsafe(9), "parent": t.D,
                                                   "content": "new"}],
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("T1 delete a directory", lambda w, t: [{"op": "delete", "id": t.D}], {PERSON: False, VAULT: True, MIRROR: True}),
    ("T2 rename the work folder", lambda w, t: [{"op": "set", "id": t.W, "content": "Paper"}],
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("T2 move the work folder to a library folder", lambda w, t: [{"op": "move", "id": t.W, "parent": t.L2}],
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("T2 a folder in the work folder", lambda w, t: [{"op": "insert", "id": secrets.token_urlsafe(9), "parent": t.W,
                                                      "content": "new"}],
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("T2 delete the work folder", lambda w, t: [{"op": "delete", "id": t.W}],
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("T3 a library folder into the work folder", lambda w, t: [{"op": "move", "id": t.L2, "parent": t.D}],
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("T3 delete a folder that holds a work folder", lambda w, t: [{"op": "delete", "id": t.O}],
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("T3 a library folder moved and reordered", lambda w, t: [{"op": "move", "id": t.L2, "parent": t.O}],
     {PERSON: True, VAULT: True, MIRROR: True}),
    ("T4 the mark on a library folder", lambda w, t: [{"op": "set", "id": t.L, "props": {"vault": MARK}}],
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("T4 the mark changed", lambda w, t: [{"op": "set", "id": t.W, "props": {"vault": MARK}}],
     {PERSON: False, VAULT: True, MIRROR: True}),
    ("T4 the mark in a new folder", lambda w, t: [{"op": "insert", "id": secrets.token_urlsafe(9), "parent": t.L,
                                                   "content": "new", "props": {"vault": MARK}}],
     {PERSON: False, VAULT: True, MIRROR: True}),
]


@pytest.mark.parametrize("writer", TREE_WRITERS)
@pytest.mark.parametrize("case", TREE_CASES, ids=[c[0] for c in TREE_CASES])
def test_a_folder_tree_op_by_each_writer(w, case, writer):
    _row_name, ops_of, allowed = case
    t = _work(w, secrets.token_hex(3))
    r = _send(w, writer, FOLDERS, ops_of(w, t))
    if allowed[MIRROR if writer == MIRROR else VAULT if writer == VAULT else PERSON]:
        assert r.status_code == 200, r.text
    else:
        _refused(r)


def test_a_batch_that_moves_a_work_folder_is_judged_by_the_tree_it_left(w):
    t = _work(w, "batch")
    n = secrets.token_urlsafe(9)
    r = _send(w, PERSON, FOLDERS, [{"op": "move", "id": t.W, "parent": t.L},
                                   {"op": "delete", "id": t.L}])
    _refused(r, "remove")
    r = _send(w, PERSON, FOLDERS, [{"op": "insert", "id": n, "parent": FOLDERS, "content": "wfo-n"},
                                   {"op": "move", "id": t.W, "parent": n}, {"op": "delete", "id": n}])
    _refused(r, "remove")
    assert _row(w, t.W)[0] == t.O and _row(w, t.D) and not _row(w, n)


def test_a_folder_of_a_work_folder_is_deleted_by_its_vault_alone(w):
    t = _work(w, "del")
    r = w.owner.delete(f"/api/folders/{t.W}")
    _refused(r, "remove")
    _refused(w.owner.delete(f"/api/folders/{t.D}"), "directories")
    _refused(w.owner.delete(f"/api/folders/{t.O}"), "remove")
    with _conn(w) as cx:
        for client in ("", "ai"):  # the agent's preview card refuses as its delete would
            with pytest.raises(OpError) as e:
                tree_deletion(cx, FOLDERS, t.D, client=client)
            assert e.value.status == 403 and e.value.conflict == LOCKED
        rows, _carried, _batches = tree_deletion(cx, FOLDERS, t.D, client=DISK_CLIENT)
        assert rows[0][0] == t.D
        assert tree_deletion(cx, FOLDERS, t.L, client="ai")[0][0][0] == t.L  # a library folder, as before
    assert w.owner.delete(f"/api/folders/{t.L}").status_code == 200
    assert _row(w, t.W) and _row(w, t.D)


# ---- the page writers ---------------------------------------------------------

def test_a_person_s_new_page_in_a_work_folder(w):
    with _conn(w) as cx:
        note = create_page(cx, "a new note", {"folders": [w.D, w.L]}, actor=w.account)
        assert note["properties"] == {"folders": [w.D], "work_folder": w.W}  # a note of W, which pulls
        for folders, doc in (([w.D], document(secrets.token_hex(12))), ([w.ND], None), ([w.L, w.D], None)):
            props = {"folders": folders, **({"doc": doc} if doc else {})}
            with pytest.raises(OpError) as e:
                create_page(cx, "refused", props, actor=w.account)
            assert (e.value.status, e.value.conflict, e.value.detail) == (403, LOCKED, refusal_text("directories"))
            assert not cx.in_transaction  # the lock let go, nothing written
        stale = create_page(cx, "an import", {"work_folder": w.W, "library": True, "file_name": "x.md",
                                               "folders": [w.L]}, actor=w.account)
        assert stale["properties"] == {"folders": [w.L]}  # a person's stamps are dropped
        made = create_page(cx, "the vault's", work_props(w.W, "made.md", directory=w.D2), actor=w.account,
                           client=DISK_CLIENT)
        assert made["properties"] == work_props(w.W, "made.md", directory=w.D2)
        with pytest.raises(OpError):  # the vault is held to the shape
            create_page(cx, "bad", work_props(w.W, "bad.md", directory=w.L), actor=w.account, client=DISK_CLIENT)
        mirrored = create_page(cx, "the mirror's", {"work_folder": w.W, "folders": [w.L]}, actor=w.account,
                               client=SYNC_CLIENT)
        assert mirrored["properties"] == {"work_folder": w.W, "folders": [w.L]}


def test_a_person_s_note_in_a_work_folder_of_the_library(w):
    t = SimpleNamespace(W=make_folder(w.owner, "wfo-libwork"), D=make_folder(w.owner, "wfo-libwork/dir"))
    mark_work_folder(w.owner, t.W, library=True)
    with _conn(w) as cx:
        note = create_page(cx, "in the library", {"folders": [t.D, w.L, w.L2]}, actor=w.account)
        assert note["properties"] == {"folders": [t.D, w.L, w.L2], "work_folder": t.W, "library": True}


def test_insert_page_tree_holds_an_import_to_the_rule(w):
    tree = [{"content": "a note"}]
    with _conn(w) as cx:
        page = secrets.token_urlsafe(9)
        with pytest.raises(OpError) as e:
            insert_page_tree(cx, page, "imported", {"folders": [w.D], "doc": document(secrets.token_hex(12))},
                             tree, w.account)
        assert e.value.conflict == LOCKED
        cx.rollback()
        assert not cx.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (page,)).fetchone()
        assert insert_page_tree(cx, page, "imported", {"folders": [w.D], "file_name": "x.md"}, tree, w.account) == 1
        vault_page = secrets.token_urlsafe(9)
        insert_page_tree(cx, vault_page, "the vault's", work_props(w.W, "v.md", directory=w.D), tree, w.account,
                         client=DISK_CLIENT)
        cx.commit()
    assert _props(w, page) == {"folders": [w.D], "work_folder": w.W}
    assert _props(w, vault_page) == work_props(w.W, "v.md", directory=w.D)


def test_the_page_routes_hold_a_new_page_to_the_rule(w):
    r = w.owner.post("/api/pages", json={"title": "a note", "properties": {"folders": [w.D]}})
    assert r.status_code == 200 and r.json()["properties"]["work_folder"] == w.W, r.text
    r = w.owner.post("/api/pages", json={"title": "a PDF", "properties": {
        "folders": [w.D], "doc": document(secrets.token_hex(12))}})
    _refused(r, "directories")
    r = w.owner.post("/api/blocks", json={"parent_id": "root", "content": "a duplicate", "properties": {
        "folders": [w.ND], "work_folder": w.N, "file_name": "x.md"}})
    _refused(r, "directories")
    r = w.owner.post("/api/pages", json={"title": "inside", "parent_page": _page_of(w, "note")})
    assert r.status_code == 403, r.text
    props = work_props(w.W, "brought.md", directory=w.D2)
    for client, claimed, stamped in ((w.owner, "disk", False), (w.token, "disk", True), (w.token, "sync", True)):
        r = client.post("/api/pages", json={"title": "brought", "properties": props},
                        headers={"X-Gamma-Client": claimed})
        assert r.status_code == 200, r.text
        assert ("file_name" in r.json()["properties"]) is stamped, (claimed, r.json())


def test_bulk_actions_are_refused_page_by_page(w):
    pdf, note, page = _page_of(w, "pdf"), _page_of(w, "note"), _page_of(w, "page")
    r = w.owner.post("/api/blocks/bulk", json={"patches": [{"id": pdf, "properties": {"folders": [w.L]}},
                                                          {"id": page, "properties": {"folders": [w.L2]}}]})
    assert r.status_code == 200, r.text
    refused, done = r.json()["results"]
    assert refused["ok"] is False and refused["status"] == 403 and refused["conflict"] == LOCKED
    assert done["ok"] is True and _props(w, page)["folders"] == [w.L2] and _props(w, pdf)["folders"] == [w.W]
    r = w.owner.post("/api/blocks/bulk", json={"trash": [pdf, note, page]})
    assert [(x["ok"], x.get("conflict")) for x in r.json()["results"]] == [(False, LOCKED), (True, None), (True, None)]
    assert _row(w, pdf)[0] == "root" and _row(w, note)[0] == "trash"


# ---- Recently deleted ------------------------------------------------------------

def _trash_entry(w, page_id):
    return next(p for p in w.owner.get("/api/trash").json()["pages"] if p["id"] == page_id)


def test_trash_and_restore_of_a_file(w):
    pdf, note = _page_of(w, "pdf"), _page_of(w, "note")
    _refused(w.owner.delete(f"/api/blocks/{pdf}"), "trash")
    assert w.owner.delete(f"/api/blocks/{note}").status_code == 200  # it follows: pull moves its note
    entry = _trash_entry(w, note)
    assert (entry["work_folder"], entry["deleted_by_vault"], entry["restorable"]) == (w.W, False, True)
    assert w.owner.post(f"/api/trash/{note}/restore").status_code == 200
    with _conn(w) as cx:  # the vault's trashing: only its file brings it back
        entry = trash_page(w.ws, cx, note, actor=w.account, client=DISK_CLIENT)
        assert entry["deleted_by_vault"] is True and entry["work_folder"] == w.W
        trash_page(w.ws, cx, pdf, actor=w.account, client=DISK_CLIENT)
    for page in (note, pdf):
        entry = _trash_entry(w, page)
        assert entry["deleted_by_vault"] is True and entry["restorable"] is False
        _refused(w.owner.post(f"/api/trash/{page}/restore"), "restore")
    with _conn(w) as cx:
        restore_page(w.ws, cx, note, actor=w.account, client=DISK_CLIENT)
    assert "deleted_by_vault" not in _props(w, note) and _props(w, note)["work_folder"] == w.W
    with _conn(w) as cx:  # the mirror replays the other copy's
        restore_page(w.ws, cx, pdf, actor=w.account, client=SYNC_CLIENT)
        trash_page(w.ws, cx, pdf, actor=w.account, client=SYNC_CLIENT)
    assert _row(w, pdf)[0] == "trash"


def test_a_file_whose_work_folder_went_comes_back_a_library_page(w):
    t = _work(w, "stale")
    page = _vault_page(w, "main", work_props(t.W, "main.pdf", library=True, doc=document(secrets.token_hex(12),
                                                                                           at="path")))
    with _conn(w) as cx:
        trash_page(w.ws, cx, page, actor=w.account, client=DISK_CLIENT)
    commit_ops(w.ws, FOLDERS, [{"op": "set", "id": t.W, "props": {"vault": None}}], actor=w.account,
               client=DISK_CLIENT)  # its vault removed meanwhile
    assert _trash_entry(w, page)["restorable"] is True
    assert w.owner.post(f"/api/trash/{page}/restore").status_code == 200
    props = _props(w, page)
    assert not {"work_folder", "library", "file_name", "deleted_by_vault"} & set(props) and props["folders"] == [t.W]


def test_a_library_page_comes_back_in_no_work_folder_and_inside_no_file(w):
    t = _work(w, "back")
    lib = make_folder(w.owner, "wfo-back-other")
    with _conn(w) as cx:
        page = create_page(cx, "filed", {"folders": [t.L, lib]}, actor=w.account)["id"]
        parent = create_page(cx, "a parent", {}, actor=w.account)["id"]
        kid = create_page(cx, "a kid", {"parent_page": parent}, actor=w.account)["id"]
        for p in (page, kid):
            trash_page(w.ws, cx, p, actor=w.account)
    mark_work_folder(w.owner, t.L)                      # the folder it was filed in became a work folder
    commit_ops(w.ws, parent, [{"op": "set", "id": parent, "props": work_props(t.W, "parent.md", directory=t.D)}],
               actor=w.account, client=DISK_CLIENT)     # its parent a file of W
    assert w.owner.post(f"/api/trash/{page}/restore").status_code == 200
    assert _props(w, page)["folders"] == [lib]
    assert w.owner.post(f"/api/trash/{kid}/restore").status_code == 200
    assert _row(w, kid)[0] == "root" and "parent_page" not in _props(w, kid)


# ---- the rest of the page writers ---------------------------------------------------

def test_get_or_create_doc_page_leaves_a_file_s_document_alone(w):
    digest = _stored_pdf(w)
    page = _vault_page(w, "main", {**work_props(w.W, "main.pdf", library=True, doc=document(digest, at="path"))})
    before = _props(w, page)
    with _conn(w) as cx:
        found = get_or_create_doc_page(cx, digest, original_filename="paper.pdf", source_url="https://example.org/p",
                                       ws=w.ws, actor=w.account)
    assert found["id"] == page and _props(w, page) == before  # no name, no address taken from the upload
    with _conn(w) as cx:
        with pytest.raises(OpError) as e:  # a new page filed in a directory: a PDF is no person's to file there
            get_or_create_doc_page(cx, secrets.token_hex(12), folder=w.D, ws=w.ws, actor=w.account)
        assert e.value.conflict == LOCKED


def test_a_walk_by_name_passes_by_a_work_folder(w):
    t = SimpleNamespace(W=make_folder(w.owner, "wfo-same"), D=make_folder(w.owner, "wfo-same/dir"))
    mark_work_folder(w.owner, t.W)
    with _conn(w) as cx:
        assert folder_by_path(cx, ["wfo-same"]) == [] and folder_by_path(cx, ["wfo-same"], into_work=True) == [t.W]
        ops, ids = folder_inserts(cx, [["wfo-same", "dir"]])
        assert [(op["parent"], op["content"]) for op in ops] == [(FOLDERS, "wfo-same"), (ops[0]["id"], "dir")]
        assert ids[("wfo-same", "dir")] == ops[1]["id"]
        assert folder_inserts(cx, [["wfo-same", "dir"]], into_work=True) == ([], {("wfo-same", "dir"): t.D})
        assert folder_inserts(cx, [["dir"]], t.W) == ([], {("dir",): t.D})  # the vault's walk from its folder


def test_quiet_stamps_leave_the_page_s_time(w):
    page = _page_of(w, "note")
    old = "2020-01-01T00:00:00.000Z"

    def aged():
        with _conn(w) as cx:
            cx.execute("UPDATE unified_blocks SET updated_at = ? WHERE id = ?", (old, page))
            cx.commit()
            return cx.execute("SELECT seq FROM page_changes WHERE page_id = ?", (page,)).fetchone()[0]

    def changed():
        with _conn(w) as cx:
            return cx.execute("SELECT seq FROM page_changes WHERE page_id = ?", (page,)).fetchone()[0]

    seq = aged()
    commit_ops(w.ws, page, [{"op": "set", "id": page, "props": {"file_name": "plan.md"}, "quiet": True}],
               actor=w.account, client=DISK_CLIENT)
    assert _row(w, page)[3] == old and changed() > seq  # the change log moved, the page's time did not
    seq = aged()
    r = w.token.post(f"/api/pages/{page}/ops", json={"client": "disk", "ops": [
        {"op": "set", "id": page, "props": {"labels": [w.label]}, "quiet": True}]})
    assert r.status_code == 200 and _row(w, page)[3] == old and changed() > seq, r.text
    for writer, ops in ((PERSON, [{"op": "set", "id": page, "props": {"pinned": "a1"}, "quiet": True}]),
                        (VAULT, [{"op": "set", "id": page, "props": {"pinned": "a2"}, "quiet": True},
                                 {"op": "insert", "id": secrets.token_urlsafe(9), "parent": page, "content": "x"}])):
        aged()
        assert _send(w, writer, page, ops).status_code == 200
        assert _row(w, page)[3] != old, writer  # a person's quiet is no quiet, nor is a batch that writes more
