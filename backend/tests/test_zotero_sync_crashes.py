"""A Zotero link's round killed after each kind of write
(gamma/zotero_sync.py; see docs/dev/zotero_sync.md#tests) converges on the
next round without duplicates or loss, on both sides: the process dies right
after the n-th multi-object write landed in Zotero (its answer never read),
or right before it was sent (the unit's Gamma side committed, Zotero's not),
after a download was stored but before it was attached, and after an upload
was sent to the storage but before it was registered (or registered, before
the row was written; over Zotero's file, the copy of it kept held). Each
crashed run is set beside a run of the same changes that was never killed:
once the crashed one has converged, both leave Zotero and Gamma alike, and
one more round writes nothing (``zotero_idle``). Rounds run in-process with
the budget lifted; a crash is a ``BaseException`` the round does not catch,
and the next start (``reset_interrupted``) goes on from what was committed.
Accounts are ``zc_…``."""

import json
import os

import pytest
from zotero_engine import (LIB, WRITES, Crash, _edit, _highlight, _kids, _link, _page_of, _rows,  # noqa: F401
                           _sync, background, drop_pending, fake, idle, me, no_relocation, owner_id, quiet)
from zotero_fake import FakeZotero
from zotero_fixtures import TITLE_RECT, pdf_bytes, standard_library

from gamma import ops, storage, upload_gc, zotero_account, zotero_local, zotero_sync, zotero_sync_rows
from gamma import zotero_annotations as za
from gamma import zotero_remote as zr
from gamma.blocks_store import FOLDERS, LABELS, TRASH, label_names, tree_rows
from gamma.db import connect_pages_db
from gamma.zotero_import import html_note_text
from gamma.zotero_web import item_uri

USER, PASSWORD = "zc_owner", "pw-zc_owner-123"
pytestmark = pytest.mark.usefixtures("quiet")  # a page's own notes go at once


# --- what both sides look like, keys and ids aside --------------------------------------------

def zotero_print(fake) -> list:
    """Zotero's library as a sorted list, keys, versions and dates left out
    (an object's parent and collections by their names)."""
    items = {d["key"]: d for d in fake.items(LIB)}
    collections = {d["key"]: d for d in fake.collections(LIB)}

    def name(key):
        d = items.get(key) or collections.get(key)
        if d is None:
            return key or ""
        return (d.get("title") or d.get("name") or d.get("annotationText") or d.get("annotationComment")
                or html_note_text(d.get("note") or "")[:40] or d.get("itemType"))

    out = []
    for d in items.values():
        out.append(repr((d["itemType"], bool(d.get("deleted")), d.get("title") or "", d.get("date") or "",
                         name(d.get("parentItem")), d.get("annotationType") or "", d.get("annotationColor") or "",
                         d.get("annotationComment") or "", d.get("annotationText") or "",
                         d.get("annotationPosition") or "", html_note_text(d.get("note") or ""), d.get("md5") or "",
                         d.get("filename") or "", sorted((t["tag"], t.get("type", 0)) for t in d.get("tags") or ()),
                         sorted(name(c) for c in d.get("collections") or ()))))
    for c in collections.values():
        out.append(repr(("collection", bool(c.get("deleted")), c["name"], name(c.get("parentCollection")))))
    return sorted(out)


def gamma_print(ws) -> list:
    """The workspace as a sorted list, ids and Zotero keys left out: each
    page (live or in Recently deleted) with its record, labels, folders,
    file and the blocks under it (by depth, content, colour, quote)."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute("SELECT id, parent_id, content, properties FROM unified_blocks").fetchall()
        labels = label_names(conn)
        folders = {fid: (name, parent) for fid, parent, name, _ in tree_rows(conn, FOLDERS)}
    kids: dict[str, list] = {}
    for block_id, parent, content, raw in rows:
        kids.setdefault(parent, []).append((block_id, content, json.loads(raw or "{}")))

    def path(fid):
        names = []
        while fid in folders:
            names.append(folders[fid][0])
            fid = folders[fid][1]
        return "/".join(reversed(names))

    def blocks(parent, depth):
        out = []
        for block_id, content, props in kids.get(parent, ()):
            out.append((depth, content, props.get("color") or "", props.get("quote") or "",
                        props.get("zotero_readonly") or ""))
            out += blocks(block_id, depth + 1)
        return out

    out = []
    for where in ("root", TRASH):
        for page_id, title, props in kids.get(where, ()):
            meta = props.get("meta") if isinstance(props.get("meta"), dict) else {}
            mark = props.get("zotero_pdf") if isinstance(props.get("zotero_pdf"), dict) else {}
            out.append(repr((where, title, meta.get("title"), meta.get("year"), meta.get("venue"),
                             sorted(labels.get(i, i) for i in props.get(LABELS) or ()),
                             sorted(path(f) for f in props.get(FOLDERS) or ()), props.get("doc_id") or "",
                             mark.get("state") or "", sorted(map(repr, blocks(page_id, 1))))))
    out += sorted(repr(("folder", path(fid))) for fid in folders)
    out += sorted(repr(("label", name)) for name in labels.values())
    return sorted(out)


# --- the changes a crashed round was making ---------------------------------------------------

NEW_PDF = pdf_bytes("A paper written here, with its own PDF.")


ZOTERO_PDF = pdf_bytes("A paper Zotero got meanwhile.")


def _changes(ws, owner, fake, keys) -> None:
    """Changes on both sides for one round: a new paper with its PDF, a
    record edit, a new highlight, a colour changed, an annotation deleted, a
    note edited, a new folder and a folder renamed, a page deleted (Gamma's);
    a record edit, an annotation's comment, a new collection, a new item
    with its PDF (Zotero's)."""
    doc_id, _ = storage.store_pdf(ws, NEW_PDF)
    zotero_local.create_page(ws, page_id="zcNewPaper", title="Written here", actor=owner, client="t", props={
        "meta": {"title": "Written here", "authors": ["Ada Lovelace"], "year": "2025", "source": "manual"},
        "doc_id": doc_id, "original_filename": "written-here.pdf"})
    thesis = _page_of(ws, keys["thesis"])
    with connect_pages_db(ws) as conn:
        meta = json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?",
                                       (thesis,)).fetchone()[0])["meta"]
    _edit(ws, owner, thesis, [{"op": "set", "id": thesis, "props": {"meta": {**meta, "year": "2020"}}}])
    page = _page_of(ws, keys["attention"])
    rows = _rows(ws, "annotation")
    _edit(ws, owner, page, [
        {"op": "insert", "id": "zcMine", "parent": page,
         **_highlight([TITLE_RECT], color="#2ea8e5", quote="Attention Is All You Need", content="the title")},
        {"op": "set", "id": rows[keys["highlight"]]["block_id"], "props": {"color": "#ff6666"}},
        {"op": "delete", "id": rows[keys["underline"]]["block_id"]}])
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    _edit(ws, owner, page, [{"op": "set", "id": _kids(ws, root)[0][0], "content": "Read this three times."}])
    reading = next(fid for fid, parent, name, _ in _tree(ws) if name == "Reading")
    _edit(ws, owner, FOLDERS, [{"op": "insert", "id": "zcFolder", "parent": FOLDERS, "content": "Made here"},
                               {"op": "set", "id": reading, "content": "To read"}])
    deep = _page_of(ws, keys["deep_learning"])
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, deep, actor=owner, client="t")
    fake.edit(LIB, keys["ppo"], title="PPO, as Zotero now has it")
    fake.edit(LIB, keys["image_annotation"], annotationComment="Figure 1, the model")
    fake.add_collection(LIB, "From Zotero")
    got = fake.add_item(LIB, {"itemType": "journalArticle", "title": "Zotero got this meanwhile",
                              "creators": [{"creatorType": "author", "firstName": "Grace", "lastName": "Hopper"}],
                              "date": "2024"})
    att = fake.add_item(LIB, {"itemType": "attachment", "parentItem": got, "linkMode": "imported_file",
                              "title": "Full Text PDF", "contentType": "application/pdf", "filename": "got.pdf"})
    fake.add_file(LIB, att, ZOTERO_PDF)


def _tree(ws):
    with connect_pages_db(ws) as conn:
        return tree_rows(conn, FOLDERS)


def _settle(c, ws, fake) -> None:
    """Rounds until one writes nothing to Zotero (at most a few)."""
    for _ in range(4):
        calls = len(fake.calls)
        _sync(c, ws)
        if not any(m in WRITES for m, _, _ in fake.calls[calls:]):
            return
    raise AssertionError("the link never settled")


def _existing(owner, fake, keys) -> str:
    """A workspace of the owner's before any link, for a first link into it:
    the paper of an RDF import (its page names the item, its PDF is
    Zotero's, a highlight carries the key the embedded import gave it, its
    note the note's text) with a highlight and a note of its own, a paper
    the library has by its DOI, and one it has not (with its PDF)."""
    from gamma import workspaces
    ws = workspaces.create("Before the link", owner)["id"]
    doc_id, _ = storage.store_pdf(ws, pdf_bytes())
    meta = {"title": "Attention is all you need", "authors": ["Ashish Vaswani", "Noam Shazeer"], "year": "2017",
            "venue": "Nature", "doi": "10.1038/s41586-000-00000-0", "source": "zotero"}
    zotero_local.create_page(ws, page_id="zcImported", title="Attention is all you need", actor=owner, client="t",
                             props={"meta": meta, "doc_id": doc_id, "zotero_key": item_uri(LIB, keys["attention"])})
    with za.Pages(pdf_bytes()) as pages:
        imported = za.from_item(fake.data(LIB, keys["highlight"]), pages.page(1))
    _edit(ws, owner, "zcImported", [
        {"op": "insert", "id": "zcImpHl", "parent": "zcImported", "content": imported["content"],
         "props": imported["props"]},
        {"op": "insert", "id": "zcImpNote", "parent": "zcImported", "content": "# Key points"},
        {"op": "insert", "id": "zcImpN1", "parent": "zcImpNote", "content": "Read this **twice**, then *once more*."},
        {"op": "insert", "id": "zcImpN2", "parent": "zcImpNote", "content": "Self-attention"},
        {"op": "insert", "id": "zcImpN3", "parent": "zcImpNote", "content": "Positional encoding"},
        {"op": "insert", "id": "zcOwnHl", "parent": "zcImported",
         **_highlight([TITLE_RECT], color="#2ea8e5", quote="Attention Is All You Need", content="mine")},
        {"op": "insert", "id": "zcOwnNote", "parent": "zcImported", "content": "A thought of my own"}])
    zotero_local.create_page(ws, page_id="zcByDoi", title="RAG, as I filed it", actor=owner, client="t", props={
        "meta": {"title": "Retrieval-augmented generation for knowledge-intensive tasks", "year": "2020",
                 "doi": "10.5555/rag", "source": "manual"}})
    doc_id, _ = storage.store_pdf(ws, NEW_PDF)
    zotero_local.create_page(ws, page_id="zcMineOnly", title="Mine only", actor=owner, client="t", props={
        "meta": {"title": "Mine only", "year": "2025", "source": "manual"}, "doc_id": doc_id,
        "original_filename": "mine-only.pdf"})
    with connect_pages_db(ws) as conn:  # the page has been still a long while: its own notes may go
        conn.execute("UPDATE unified_blocks SET updated_at = '2020-01-01T00:00:00.000000Z' WHERE id = 'zcImported'")
        conn.commit()
    return ws


def _run(c, owner, fake, crash=None, *, mode="changes") -> tuple[list, list, dict]:
    """Link the standard library and run one round with ``crash`` installed
    (a callable taking the fake, returning an undo): with ``mode``
    "changes", the round after ``_changes``; "fill", a new workspace's first
    round; "existing", the first round of a link into ``_existing``'s
    workspace. Then go on as after a restart until settled. ``(Zotero's
    print, Gamma's print, {"writes": write requests, "commits": the link's
    Gamma commits} of the round)``."""
    keys = standard_library(fake)["keys"]
    if mode == "existing":
        fake.edit(LIB, keys["conference"], DOI="10.5555/rag")
        ws = _existing(owner, fake, keys)
        r = c.post("/api/zotero/links", json={"library": LIB, "workspace_id": ws, "adopt": "theirs"})
        assert r.status_code == 201, r.text
    else:
        ws = _link(c)
    if mode == "changes":
        _sync(c, ws)
        _changes(ws, owner, fake, keys)
    calls, commits = len(fake.calls), []

    def counted(ws_, client, page_id):
        if ws_ == ws and client == zotero_sync_rows.CLIENT:
            commits.append(page_id)

    ops.commit_listeners.append(counted)
    undo = crash(fake) if crash else None
    try:
        drop_pending(ws)
        if crash is None:
            zotero_sync.sync_workspace(ws)
        else:
            with pytest.raises(Crash):
                zotero_sync.sync_workspace(ws)
    finally:
        ops.commit_listeners.remove(counted)
        if undo:
            undo()
    made = {"writes": sum(1 for m, _, _ in fake.calls[calls:] if m in WRITES), "commits": len(commits)}
    zotero_sync.reset_interrupted()  # the next start
    zotero_sync.forget_access()
    _settle(c, ws, fake)
    return zotero_print(fake), gamma_print(ws), made


_REFERENCE: dict = {}


def _reference(c, owner, monkeypatch, *, mode="changes") -> dict:
    """The same round never killed (``_run``'s ``mode``), run once for the
    module in a fake of its own, its link removed after."""
    if mode not in _REFERENCE:
        fake = FakeZotero().install(monkeypatch)
        zotero_account.connect(owner, fake.api_key)
        z, g, made = _run(c, owner, fake, mode=mode)
        _REFERENCE[mode] = {"zotero": z, "gamma": g, **made}
        for link in zotero_sync.list_links(owner):
            zotero_sync.remove_link(link["workspace_id"])
    return _REFERENCE[mode]


def _crash_at_write(n: int, *, sent: bool):
    """Kill the round at its n-th write request to Zotero (a batch of a
    multi-object write, a trash, an upload's step): right after Zotero
    saved it (``sent``: its answer lost) or right before it went."""
    def install(fake):
        real = fake.handle
        seen = []

        def handle(method, url, headers, body):
            if method in WRITES:
                seen.append(url)
                if len(seen) == n:
                    if sent:
                        real(method, url, headers, body)
                    raise Crash(f"killed at write {n}: {method} {url}")
            return real(method, url, headers, body)

        fake.handle = handle
        return lambda: setattr(fake, "handle", real)
    return install


def _crash_at_commit(n: int):
    """Kill the round right after its n-th commit on Gamma's side (a page
    made, a unit's ops and rows, a PDF attached, a folder deleted)."""
    def install(fake):
        seen = []

        def listener(ws, client, page_id):
            if client == zotero_sync_rows.CLIENT:
                seen.append(page_id)
                if len(seen) == n:
                    raise Crash(f"killed after commit {n} ({page_id})")

        ops.commit_listeners.append(listener)
        return lambda: ops.commit_listeners.remove(listener)
    return install


def _same_as_reference(c, owner, fake, monkeypatch, crash, *, mode="changes"):
    ref = _reference(c, owner, monkeypatch, mode=mode)
    fake.install(monkeypatch)  # the test's own fake again
    zotero_account.connect(owner, fake.api_key)
    z, g, _ = _run(c, owner, fake, crash, mode=mode)
    assert z == ref["zotero"], "\n".join(sorted(set(z) ^ set(ref["zotero"])))
    assert g == ref["gamma"], "\n".join(sorted(set(g) ^ set(ref["gamma"])))


WRITE_POINTS = range(1, 17)


def test_the_reference_round_makes_every_kind_of_write(me, owner_id, fake, monkeypatch):
    ref = _reference(me, owner_id, monkeypatch)
    assert ref["writes"] >= 10, ref["writes"]  # collections, items, attachments, a file, annotations, notes...
    zotero = "\n".join(ref["zotero"])
    for there in ("Written here", "PPO, as Zotero now has it", "Made here", "To read", "Read this three times.",
                  "#ff6666", "the title", "written-here.pdf"):
        assert there in zotero, there
    gamma = "\n".join(ref["gamma"])
    for here in ("Zotero got this meanwhile", "PPO, as Zotero now has it", "From Zotero", "Figure 1, the model"):
        assert here in gamma, here
    assert max(WRITE_POINTS) >= ref["writes"] and max(COMMIT_POINTS) >= ref["commits"] >= 5


@pytest.mark.parametrize("n", WRITE_POINTS)
def test_a_round_killed_right_after_a_write_landed_converges(me, owner_id, fake, monkeypatch, n):
    if n > _reference(me, owner_id, monkeypatch)["writes"]:
        pytest.skip("the round makes fewer writes")
    _same_as_reference(me, owner_id, fake, monkeypatch, _crash_at_write(n, sent=True))


@pytest.mark.parametrize("n", WRITE_POINTS)
def test_a_round_killed_before_a_write_went_converges(me, owner_id, fake, monkeypatch, n):
    if n > _reference(me, owner_id, monkeypatch)["writes"]:
        pytest.skip("the round makes fewer writes")
    _same_as_reference(me, owner_id, fake, monkeypatch, _crash_at_write(n, sent=False))


COMMIT_POINTS = range(1, 21)


@pytest.mark.parametrize("n", COMMIT_POINTS)
def test_a_round_killed_after_a_commit_here_converges(me, owner_id, fake, monkeypatch, n):
    if n > _reference(me, owner_id, monkeypatch)["commits"]:
        pytest.skip("the round makes fewer commits")
    _same_as_reference(me, owner_id, fake, monkeypatch, _crash_at_commit(n))


@pytest.mark.parametrize("n", COMMIT_POINTS)
def test_a_first_fill_killed_after_a_commit_here_converges(me, owner_id, fake, monkeypatch, n):
    ref = _reference(me, owner_id, monkeypatch, mode="fill")
    assert ref["commits"] <= max(COMMIT_POINTS) and not ref["writes"]
    if n > ref["commits"]:
        pytest.skip("the fill makes fewer commits")
    _same_as_reference(me, owner_id, fake, monkeypatch, _crash_at_commit(n), mode="fill")


def test_the_first_link_of_an_existing_workspace_pairs_what_both_hold_and_sends_the_rest(me, owner_id, fake,
                                                                                        monkeypatch):
    ref = _reference(me, owner_id, monkeypatch, mode="existing")
    assert ref["commits"] <= max(COMMIT_POINTS) and ref["writes"] <= max(WRITE_POINTS)
    zotero, gamma = "\n".join(ref["zotero"]), "\n".join(ref["gamma"])
    assert sum(1 for line in ref["zotero"] if line.startswith("('journalArticle', False, 'Attention")) == 1
    for there in ("Mine only", "mine-only.pdf", "'mine'", "A thought of my own"):
        assert there in zotero, there
    assert gamma.count("'dominant sequence transduction models'") == 1 and gamma.count("Key points") == 1
    assert sum(1 for line in ref["gamma"] if line.startswith("('root', 'RAG, as I filed it'")) == 1


@pytest.mark.parametrize("n", WRITE_POINTS)
@pytest.mark.parametrize("sent", [True, False])
def test_a_first_link_of_an_existing_workspace_killed_at_a_write_converges(me, owner_id, fake, monkeypatch, n, sent):
    if n > _reference(me, owner_id, monkeypatch, mode="existing")["writes"]:
        pytest.skip("the round makes fewer writes")
    _same_as_reference(me, owner_id, fake, monkeypatch, _crash_at_write(n, sent=sent), mode="existing")


@pytest.mark.parametrize("n", COMMIT_POINTS)
def test_a_first_link_of_an_existing_workspace_killed_after_a_commit_here_converges(
        me, owner_id, fake, monkeypatch, n):
    if n > _reference(me, owner_id, monkeypatch, mode="existing")["commits"]:
        pytest.skip("the round makes fewer commits")
    _same_as_reference(me, owner_id, fake, monkeypatch, _crash_at_commit(n), mode="existing")


def test_a_round_killed_after_a_download_was_stored_and_before_it_was_attached_converges(
        me, owner_id, fake, monkeypatch):
    def install(fake):
        real = zotero_local.set_pdf
        done = []

        def set_pdf(ws, page_id, doc_id, **kw):
            if not done and storage.open_path(ws, f"{doc_id}.pdf"):
                done.append(doc_id)
                raise Crash("killed with the PDF stored")
            return real(ws, page_id, doc_id, **kw)

        monkeypatch.setattr(zotero_local, "set_pdf", set_pdf)
        return lambda: monkeypatch.setattr(zotero_local, "set_pdf", real)
    _same_as_reference(me, owner_id, fake, monkeypatch, install)


@pytest.mark.parametrize("registered", [False, True])
def test_a_round_killed_in_an_upload_converges(me, owner_id, fake, monkeypatch, registered):
    """Killed once the file went to the storage (not registered yet), or
    once it was registered (the row not written yet)."""
    def install(fake):
        real = fake.handle
        done = []

        def handle(method, url, headers, body):
            reply = real(method, url, headers, body)
            uploading = "/__upload/" in url if not registered else (
                url.endswith("/file") and b"upload=" in (body or b""))
            if method == "POST" and uploading and not done:
                done.append(url)
                raise Crash("killed in the upload")
            return reply

        fake.handle = handle
        return lambda: setattr(fake, "handle", real)
    _same_as_reference(me, owner_id, fake, monkeypatch, install)


def test_a_round_killed_once_gammas_pdf_went_over_zoteros_keeps_zoteros_copy_held_and_its_conflict(
        me, owner_id, fake, monkeypatch):
    """Killed right after the upload over Zotero's file landed (both
    replaced: Zotero's is kept here for "theirs"), before anything here
    recorded it: the conflict holding the copy was written first, so the
    copy is never the clean-up's."""
    from gamma.routers.pages import replace_attachment
    keys = standard_library(fake)["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["standalone_pdf"])
    doc, _ = storage.store_pdf(ws, pdf_bytes("Gamma's handout, replaced here."))
    replace_attachment(ws, page, doc, "", "gammas.pdf", actor=owner_id, owner=owner_id)
    fake.add_file(LIB, keys["standalone_pdf"], pdf_bytes("Zotero's handout, replaced there."))
    zoteros = fake.data(LIB, keys["standalone_pdf"])["md5"]
    real = zr.upload_pdf

    def killed(*args, **kw):
        real(*args, **kw)
        raise Crash("killed once the upload landed")

    monkeypatch.setattr(zr, "upload_pdf", killed)
    drop_pending(ws)
    with pytest.raises(Crash):
        zotero_sync.sync_workspace(ws)
    monkeypatch.setattr(zr, "upload_pdf", real)
    zotero_sync.reset_interrupted()  # the next start
    zotero_sync.forget_access()
    _settle(me, ws, fake)
    assert fake.data(LIB, keys["standalone_pdf"])["md5"] == za.file_md5(storage.open_path(ws, f"{doc}.pdf"))
    [conflict] = [c for c in me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
                  if c["kind"] == "zotero_file"]
    kept = f"{json.loads(conflict['base'])['doc_id']}.pdf"
    assert za.file_md5(storage.open_path(ws, kept)) == zoteros
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 0)
    with connect_pages_db(ws) as conn:  # as if it had gone unreferenced a month ago
        conn.execute("INSERT OR REPLACE INTO upload_orphans (name, since) VALUES (?, '2020-01-01T00:00:00.000000Z')",
                     (kept,))
        conn.commit()
    os.utime(storage.open_path(ws, kept), (0, 0))
    assert kept not in upload_gc.reconcile(ws)["purged"] and storage.open_path(ws, kept)
