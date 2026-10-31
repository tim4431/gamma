"""The Zotero link's PDFs (gamma/zotero_sync_pdfs.py) against the fake
Zotero (zotero_fake.py): a file changed in Zotero replaces the page's PDF
and starts its relocation; a PDF replaced here goes in place, or beside a
file whose annotations cannot move; a PDF replaced on both sides keeps
Gamma's, going over Zotero's only once Zotero's is kept here (its copy kept
while its conflict is open, then left to the uploads clean-up; "Take
Zotero's" refused once Zotero replaced it again), or beside it as a new
attachment when its annotations cannot move; a PDF replaced here waits,
neither in place nor beside, while its highlights are being placed, and is
decided again when the relocation ends, however it ends: in place over
annotations whose highlights were all placed (a force push too), beside
them otherwise, a new attachment held by Zotero's storage decided afresh
then; an upload whose answer was lost keeps its conflict once it landed;
a copy of Zotero's file beside which Gamma's went, or none when Zotero
cannot hand it out, and "Take Zotero's" of it making Zotero's attachment
the page's PDF again, its annotations neither twice here nor twice in
Zotero; a file Zotero
storage does not hold, the attach prompt while Zotero's comes, "When
opened" and Download now; Zotero's full storage (a receive-only round
restores no attachment it trashed) and this server's; a PDF removed for good
in Zotero is not sent back, and a standalone PDF's page changed here goes
back as a new PDF or stays here unlinked; a PDF is hashed only for a
decision that reads its MD5, and an unchanged one is not read again; a
transfer shows its progress. Rounds run
through ``POST …/sync?wait=1`` with the budget lifted; background rounds are
stubbed. Accounts are ``zp_…``."""

import json
import os
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
from zotero_engine import (LIB, Crash, _block, _conflict_count, _conflicts, _due, _due_now, _edit, _link,  # noqa: F401
                           _page_of, _paper, _queue_entries, _rows, _stored_pdf, _sync, _writes, asked_soon,
                           background, bounded, drop_pending, fake, idle, made, me, no_relocation, owner_id)
from zotero_fixtures import CAROL, HIGHLIGHT_RECT, HIGHLIGHT_TEXT, _annotation, _position, pdf_bytes

from gamma import (highlight_relocate, jobs, ops, storage, upload_gc, zotero_local, zotero_sync, zotero_sync_first,
                   zotero_sync_pdfs, zotero_sync_reads, zotero_sync_rows)
from gamma import zotero_remote as zr
from gamma.db import connect_pages_db, ws_uploads_dir
from gamma.routers import pages as pages_router
from gamma.zotero_annotations import file_md5
from gamma.zotero_web import ZoteroError, item_uri, object_key

USER, PASSWORD = "zp_owner", "pw-zp_owner-123"
DAY = 86400
REAL_RELOCATION = pages_router._start_relocation  # before ``no_relocation`` stubs it


def _replace(ws, owner, page_id, content: bytes, name="new.pdf"):
    """A person's Document → Replace of the page's PDF."""
    from gamma.routers.pages import replace_attachment
    doc = _stored_pdf(ws, content)
    replace_attachment(ws, page_id, doc, "", name, actor=owner, owner=owner)
    return doc


def _uploads(fake, key, since=0) -> list:
    return [(m, p) for m, p, _ in fake.calls[since:] if m == "POST" and p.endswith(f"/items/{key}/file")]


# --- Zotero's file changed, Gamma's replaced ------------------------------------------------------

def test_a_file_changed_in_zotero_replaces_the_pages_pdf_and_starts_its_relocation(me, made, fake, no_relocation):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["standalone_pdf"])
    old = _block(ws, page)["props"]["doc_id"]
    fake.add_file(LIB, keys["standalone_pdf"], pdf_bytes("The handout, corrected."))
    calls = len(fake.calls)
    status = _sync(me, ws)
    new = _block(ws, page)["props"]["doc_id"]
    assert new != old and status["files_pulled"] == 1 and _writes(fake, calls) == []
    row = _rows(ws, "attachment")[keys["standalone_pdf"]]
    assert row["detail"]["md5"] == row["detail"]["gmd5"] == fake.data(LIB, keys["standalone_pdf"])["md5"]
    assert row["version"] == fake.data(LIB, keys["standalone_pdf"])["version"]
    log = me.get(f"/api/zotero/links/{ws}/log").json()["changes"]
    assert any(e["page_id"] == page and e["action"] == "replaced here" for e in log)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_pdf_replaced_here_goes_in_place_or_beside_a_file_whose_annotations_cannot_move(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    # a standalone PDF with no annotations: Gamma's new file goes over Zotero's (If-Match its MD5)
    page = _page_of(ws, keys["standalone_pdf"])
    before = fake.data(LIB, keys["standalone_pdf"])["md5"]
    doc = _replace(ws, owner_id, page, pdf_bytes("The handout, version two."), "handout-v2.pdf")
    calls = len(fake.calls)
    status = _sync(me, ws)
    md5 = file_md5(storage.open_path(ws, f"{doc}.pdf"))
    assert fake.data(LIB, keys["standalone_pdf"])["md5"] == md5 != before and status["files_pushed"] == 1
    authorize = [h for m, path, h in fake.calls[calls:] if m == "POST" and path.endswith("/file")]
    assert authorize[0].get("if-match") == before
    row = _rows(ws, "attachment")[keys["standalone_pdf"]]
    assert row["detail"]["md5"] == row["detail"]["gmd5"] == md5
    assert row["version"] == fake.data(LIB, keys["standalone_pdf"])["version"]
    # a paper whose Zotero PDF holds annotations Gamma cannot move: a new attachment, the page's new PDF
    page = _page_of(ws, keys["attention"])
    original = fake.data(LIB, keys["pdf"])["md5"]
    doc = _replace(ws, owner_id, page, pdf_bytes("Attention, the camera-ready version."), "camera-ready.pdf")
    _sync(me, ws)
    assert fake.data(LIB, keys["pdf"])["md5"] == original  # Zotero's own file and its annotations stay
    md5 = file_md5(storage.open_path(ws, f"{doc}.pdf"))
    [new] = [d for d in fake.items(LIB) if d.get("parentItem") == keys["attention"] and d.get("md5") == md5]
    assert new["filename"] == "camera-ready.pdf" and new["key"] != keys["pdf"]
    bound = [r for r in _rows(ws, "attachment").values() if r["page_id"] == page]
    assert [r["key"] for r in bound] == [new["key"]]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- a PDF replaced on both sides -----------------------------------------------------------------

def test_a_pdf_replaced_on_both_sides_keeps_gammas_and_theirs_brings_zoteros(me, made, fake, owner_id, no_relocation):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["standalone_pdf"])
    mine = _replace(ws, owner_id, page, pdf_bytes("Gamma's handout."), "gammas.pdf")
    fake.add_file(LIB, keys["standalone_pdf"], pdf_bytes("Zotero's handout."))
    zoteros = fake.data(LIB, keys["standalone_pdf"])["md5"]
    _sync(me, ws)
    md5 = file_md5(storage.open_path(ws, f"{mine}.pdf"))
    assert fake.data(LIB, keys["standalone_pdf"])["md5"] == md5  # Gamma's wins
    [conflict] = me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
    assert conflict["kind"] == "zotero_file" and conflict["theirs"].endswith(zoteros)
    kept = json.loads(conflict["base"])["doc_id"]
    assert file_md5(storage.open_path(ws, f"{kept}.pdf")) == zoteros  # Zotero's file kept here, unattached
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200 and _block(ws, page)["props"]["doc_id"] == kept
    _sync(me, ws)
    assert fake.data(LIB, keys["standalone_pdf"])["md5"] == zoteros  # sent back: both hold Zotero's again
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def _both_replaced(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    key = keys["standalone_pdf"]
    page = _rows(ws)[key]["page_id"]
    mine = _replace(ws, owner_id, page, pdf_bytes("Gamma's handout."), "gammas.pdf")
    fake.add_file(LIB, key, pdf_bytes("Zotero's handout."))
    return ws, key, page, mine, fake.data(LIB, key)["md5"]


def test_a_pdf_replaced_on_both_sides_whose_zotero_copy_cannot_be_read_is_not_sent_over_it(me, made, fake, owner_id):
    ws, key, page, mine, zoteros = _both_replaced(me, made, fake, owner_id)
    fake.fail_next(404, f"/items/{key}/file", method="GET")
    calls = len(fake.calls)
    _sync(me, ws)
    assert fake.data(LIB, key)["md5"] == zoteros
    assert _uploads(fake, key, calls) == []
    row = _rows(ws)[key]
    assert row["state"] == "waiting" and row["detail"]["file"] == "unavailable"
    assert _block(ws, page)["props"]["doc_id"] == mine
    calls = len(fake.calls)
    _sync(me, ws)
    assert _uploads(fake, key, calls) == [] and fake.data(LIB, key)["md5"] == zoteros


def test_a_download_this_servers_full_storage_holds_still_counts_as_held_when_its_item_changes_again(
        me, made, fake, monkeypatch):
    from fastapi import HTTPException
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    key = keys["pdf"]
    fake.add_file(LIB, key, pdf_bytes("Attention, Zotero's version."))
    real = storage.check_upload_allowed

    def full(ws_, nbytes):
        raise HTTPException(507, "this server's storage is full")

    monkeypatch.setattr(storage, "check_upload_allowed", full)
    status = _sync(me, ws)
    assert status["waiting"]["files"].get("storage") == 1
    fake.edit(LIB, keys["attention"], title="Attention, retitled in Zotero")
    status = _sync(me, ws)
    row = _rows(ws)[key]
    assert row["state"] == "waiting" and row["detail"]["file"] == "storage"
    assert status["waiting"]["files"].get("storage") == 1
    monkeypatch.setattr(storage, "check_upload_allowed", real)
    with connect_pages_db(ws) as conn:  # the wait is over
        conn.execute("UPDATE zotero_queue SET next_at = ''")
        conn.commit()
    status = _sync(me, ws)
    assert _rows(ws)[key]["state"] == "live" and "storage" not in status["waiting"]["files"]
    page = _page_of(ws, keys["attention"])
    assert file_md5(storage.open_path(ws, f"{_block(ws, page)['props']['doc_id']}.pdf")) == fake.data(LIB, key)["md5"]


def test_a_pdf_replaced_on_both_sides_waits_while_this_servers_storage_is_full(me, made, fake, owner_id,
                                                                                monkeypatch):
    from fastapi import HTTPException
    ws, key, page, mine, zoteros = _both_replaced(me, made, fake, owner_id)
    real = storage.check_upload_allowed

    def full(ws_, nbytes):
        raise HTTPException(507, "this server's storage is full")

    monkeypatch.setattr(storage, "check_upload_allowed", full)
    calls = len(fake.calls)
    _sync(me, ws)
    assert fake.data(LIB, key)["md5"] == zoteros and _uploads(fake, key, calls) == []
    row = _rows(ws)[key]
    assert row["state"] == "waiting" and row["detail"]["file"] == "storage"
    monkeypatch.setattr(storage, "check_upload_allowed", real)
    with connect_pages_db(ws) as conn:  # the wait is over
        conn.execute("UPDATE zotero_queue SET next_at = ''")
        conn.commit()
    _sync(me, ws)
    md5 = file_md5(storage.open_path(ws, f"{mine}.pdf"))
    assert fake.data(LIB, key)["md5"] == md5
    [conflict] = me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
    kept = json.loads(conflict["base"])["doc_id"]
    assert kept and file_md5(storage.open_path(ws, f"{kept}.pdf")) == zoteros


def test_taking_zoteros_file_after_zotero_replaced_it_again_is_refused(me, made, fake, owner_id):
    ws, key, page, mine, zoteros = _both_replaced(me, made, fake, owner_id)
    _sync(me, ws)
    [conflict] = [c for c in me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
                  if c["kind"] == "zotero_file"]
    fake.add_file(LIB, key, pdf_bytes("Zotero's handout, once more."))
    newest = fake.data(LIB, key)["md5"]
    _sync(me, ws)
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 409, r.text
    _sync(me, ws)
    assert fake.data(LIB, key)["md5"] == newest


def test_a_pdf_replaced_on_both_sides_beside_annotations_that_cannot_move_goes_as_a_new_attachment(
        me, made, fake, owner_id, bounded):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    mine = _replace(ws, owner_id, page, pdf_bytes("Attention, Gamma's version."), "gammas.pdf")
    fake.add_file(LIB, keys["pdf"], pdf_bytes("Attention, Zotero's version."))
    zoteros = fake.data(LIB, keys["pdf"])["md5"]
    drop_pending(ws)
    _sync(me, ws)
    # the highlights being placed hold Zotero's annotations: Gamma's file goes beside Zotero's, which stays
    md5 = file_md5(storage.open_path(ws, f"{mine}.pdf"))
    assert fake.data(LIB, keys["pdf"])["md5"] == zoteros
    [new] = [d for d in _pdfs_of(fake, keys["attention"]) if d.get("md5") == md5]
    assert new["key"] != keys["pdf"] and new["filename"] == "gammas.pdf"
    assert [k for k, row in _rows(ws, "attachment").items() if row["page_id"] == page] == [new["key"]]
    # Zotero's file is kept here for its conflict, as when both went in place
    [conflict] = _conflicts(me, ws, "zotero_file")
    base = json.loads(conflict["base"])
    assert base["key"] == new["key"] and base["synced"] == md5 and conflict["theirs"].endswith(zoteros)
    assert file_md5(storage.open_path(ws, f"{base['doc_id']}.pdf")) == zoteros
    assert "new attachment" in conflict["result"]
    assert _due_now(ws) == [] and not asked_soon(ws)  # settled in one round
    calls, rows = len(fake.calls), _conflict_count(ws)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and _conflict_count(ws) == rows
    # Take Zotero's: its file becomes the page's, and goes to Zotero as the page's PDF
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    assert _block(ws, page)["props"]["doc_id"] == base["doc_id"]
    _sync(me, ws)
    [bound] = [k for k, row in _rows(ws, "attachment").items() if row["page_id"] == page]
    assert fake.data(LIB, bound)["md5"] == zoteros
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_pdf_replaced_here_waits_while_its_highlights_are_being_placed_then_goes_in_place(
        me, made, fake, owner_id, monkeypatch, bounded):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    key = keys["standalone_pdf"]
    page = _page_of(ws, key)
    placing = {page}
    monkeypatch.setattr(zotero_sync_pdfs, "_relocating", lambda r, side: side.page_id in placing)
    before = fake.data(LIB, key)["md5"]
    mine = _replace(ws, owner_id, page, pdf_bytes("The handout, placed slowly."), "slow.pdf")
    for _ in range(2):  # neither in place nor beside it, round after round, and nothing asked for at once
        drop_pending(ws)
        calls = len(fake.calls)
        status = _sync(me, ws)
        assert _writes(fake, calls) == [] and fake.data(LIB, key)["md5"] == before
        row = _rows(ws)[key]
        assert row["state"] == "waiting" and row["detail"]["file"] == zotero_sync_pdfs.FILE_PLACING
        assert status["waiting"]["files"].get("placing") == 1
        entry = _queue_entries(ws)[f"file:{key}"]
        assert entry["next_at"] and entry["reason"] == zotero_sync_pdfs.PLACING_WAIT
        assert _due_now(ws) == [] and not asked_soon(ws) and zotero_sync._clock.pending.get(ws)
    # the relocation ends: the PDF is due again at once, a round asked for, and it goes in place
    placing.clear()
    drop_pending(ws)
    zotero_sync_pdfs.relocation_ended(ws, page)
    assert _due_now(ws) == [f"file:{key}"] and asked_soon(ws)
    drop_pending(ws)
    status = _sync(me, ws)
    md5 = file_md5(storage.open_path(ws, f"{mine}.pdf"))
    assert fake.data(LIB, key)["md5"] == md5 and "placing" not in status["waiting"]["files"]
    assert _rows(ws)[key]["state"] == "live" and _queue_entries(ws) == {} and not asked_soon(ws)


def test_a_relocation_that_ends_without_writing_still_has_the_pdf_decided_again(me, made, fake, owner_id,
                                                                                 monkeypatch, bounded):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    old = _block(ws, page)["props"]["doc_id"]
    release = threading.Event()

    def relocate(ws_, page_id, new_doc, **kw):  # a run that moves no highlight and writes nothing
        release.wait(30)
        return {"highlights": 0, "moved": 0, "kept": 0, "lost": 0, "approx": 0, "changed": 0}

    monkeypatch.setattr(highlight_relocate, "relocate", relocate)
    mine = _replace(ws, owner_id, page, pdf_bytes("Attention, retypeset."), "retypeset.pdf")
    job, why = REAL_RELOCATION(owner_id, ws, page, "Attention", mine, outgoing=old)
    try:
        assert job is not None and job["state"] in jobs.ACTIVE, why
        drop_pending(ws)
        calls = len(fake.calls)
        _sync(me, ws)
        assert _writes(fake, calls) == []  # no attachment made, no file sent while it runs
        [row] = [row for row in _rows(ws, "attachment").values() if row["page_id"] == page]
        assert row["key"] == keys["pdf"] and row["detail"]["file"] == zotero_sync_pdfs.FILE_PLACING
    finally:
        release.set()
        ended = jobs.wait(job["id"]) if job else None
    assert ended["state"] == "done"
    # told by the run's end: the PDF, and the item, whose annotations wait for the highlights being placed
    assert sorted(_due_now(ws)) == sorted([f"file:{keys['pdf']}", keys["attention"]]) and asked_soon(ws)
    drop_pending(ws)
    _sync(me, ws)
    # the highlights it left being placed hold Zotero's annotations: Gamma's file goes as a new attachment
    md5 = file_md5(storage.open_path(ws, f"{mine}.pdf"))
    [new] = [d for d in _pdfs_of(fake, keys["attention"]) if d.get("md5") == md5]
    assert new["key"] != keys["pdf"] and _due_now(ws) == [] and not asked_soon(ws)


# --- Gamma's PDF over or beside Zotero's annotations, a lost answer, Take Zotero's ------------

def _simple_paper(fake, library=LIB, *, carols=False):
    """A paper with one PDF and one highlight on it in Zotero: the user's
    own, or another group member's (``carols``)."""
    item = fake.add_item(library, {"itemType": "journalArticle", "title": "A simple paper"})
    att = fake.add_item(library, {"itemType": "attachment", "parentItem": item, "linkMode": "imported_file",
                                  "title": "Full Text PDF", "contentType": "application/pdf",
                                  "filename": "simple.pdf"})
    fake.add_file(library, att, pdf_bytes())
    body = _annotation(att, "highlight", "#ffd400", "00000|000024|00108", _position(0, rects=[HIGHLIGHT_RECT]),
                       comment="from zotero", annotationText=HIGHLIGHT_TEXT)
    if carols:
        with fake.acting_as(CAROL):
            ann = fake.add_item(library, body)
    else:
        ann = fake.add_item(library, body)
    return item, att, ann


def _place_all(ws, owner) -> None:
    """A relocation's write: every highlight still marked with the file it
    was measured on placed where it was (its mark of that file gone)."""
    with connect_pages_db(ws) as conn:
        found = conn.execute(
            "SELECT b.page_id, b.id, json_extract(b.properties, '$.pdf_position') FROM unified_blocks b "
            "JOIN unified_blocks p ON p.id = b.page_id WHERE p.parent_id = 'root' "
            "AND json_extract(b.properties, '$.pdf_position.doc_id') IS NOT NULL").fetchall()
    by_page: dict = {}
    for page_id, block_id, pos in found:
        by_page.setdefault(page_id, []).append({"op": "set", "id": block_id, "props": {"pdf_position": {
            k: v for k, v in json.loads(pos).items() if k != "doc_id"}}})
    for page_id, batch in by_page.items():
        ops.commit_ops(ws, page_id, batch, actor=owner, client=highlight_relocate.CLIENT)


def _pdf_atts(fake, item, library=LIB) -> dict:
    """The item's PDF attachments in Zotero (not in its trash): key → MD5."""
    return {d["key"]: d.get("md5") for d in fake.items(library) if d.get("parentItem") == item
            and d.get("itemType") == "attachment" and d.get("contentType") == "application/pdf"
            and not d.get("deleted")}


def _annotation_keys(fake, att, library=LIB) -> set:
    return {d["key"] for d in fake.items(library) if d.get("itemType") == "annotation"
            and d.get("parentItem") == att and not d.get("deleted")}


def _annotation_blocks(ws, page) -> set:
    """The ids of the page's annotation blocks."""
    with connect_pages_db(ws) as conn:
        view = zotero_local.read_page(conn, page, notes=False)
    return {a.block["id"] for a in view.annotations}


def _page_md5(ws, page) -> str:
    return file_md5(storage.open_path(ws, f"{_block(ws, page)['props']['doc_id']}.pdf"))


@pytest.mark.parametrize("waited", [True, False], ids=["while-placing", "placed-before-the-round"])
def test_a_pdf_replaced_here_whose_highlights_are_all_placed_goes_in_place_over_its_annotations(
        me, made, fake, owner_id, monkeypatch, bounded, waited):
    item, att, ann = _simple_paper(fake)
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    placing = {page} if waited else set()
    monkeypatch.setattr(zotero_sync_pdfs, "_relocating", lambda r, side: side.page_id in placing)
    doc = _replace(ws, owner_id, page, pdf_bytes("Simple, retypeset."), "retypeset.pdf")
    if waited:  # the round waits, and holds the annotation while the files differ
        drop_pending(ws)
        calls = len(fake.calls)
        _sync(me, ws)
        assert _writes(fake, calls) == [] and _rows(ws)[att]["detail"]["file"] == zotero_sync_pdfs.FILE_PLACING
        assert _rows(ws)[ann]["state"] == "held"
    _place_all(ws, owner_id)  # every highlight placed: none left dashed or lost
    placing.clear()
    zotero_sync_pdfs.relocation_ended(ws, page)
    drop_pending(ws)
    _sync(me, ws)
    md5 = file_md5(storage.open_path(ws, f"{doc}.pdf"))
    assert _pdf_atts(fake, item) == {att: md5}  # in place: no second attachment
    assert _annotation_keys(fake, att) == {ann} and _rows(ws)[ann]["state"] == "live"
    assert _rows(ws)[ann]["parent"] == att and _rows(ws)[att]["detail"]["md5"] == md5
    assert _due_now(ws) == [] and not asked_soon(ws)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_real_relocation_that_places_every_highlight_lets_the_pdf_go_in_place(me, made, fake, owner_id,
                                                                                  monkeypatch, bounded):
    monkeypatch.setattr(pages_router, "_start_relocation", REAL_RELOCATION)
    item, att, ann = _simple_paper(fake)
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    doc = _replace(ws, owner_id, page, pdf_bytes() + b"\n% the same paper, saved again\n", "saved-again.pdf")
    job = jobs.latest(owner_id, pages_router.RELOCATE, f"{page}:{doc}")
    drop_pending(ws)
    _sync(me, ws)  # while it may still run
    ended = jobs.wait(job["id"])
    assert ended["state"] == "done" and ended["result"]["lost"] == ended["result"]["approx"] == 0
    for _ in range(2):
        drop_pending(ws)
        _sync(me, ws)
    assert _pdf_atts(fake, item) == {att: file_md5(storage.open_path(ws, f"{doc}.pdf"))}


def test_a_relocation_tells_the_link_once_its_job_reads_as_ended(me, made, fake, owner_id, monkeypatch):
    """Told while the job still read as running, a round in between found
    the page still placing and held its PDF back PLACING_RECHECK_S again,
    with nothing left to wake it sooner."""
    monkeypatch.setattr(pages_router, "_start_relocation", REAL_RELOCATION)
    told, seen = zotero_sync_pdfs.relocation_ended, []

    def recording(ws, page_id):
        seen.append(jobs.latest(owner_id, pages_router.RELOCATE, f"{page_id}:{doc}")["state"])
        told(ws, page_id)

    monkeypatch.setattr(zotero_sync_pdfs, "relocation_ended", recording)
    item, att, _ann = _simple_paper(fake)
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    doc = _replace(ws, owner_id, page, pdf_bytes() + b"\n% the same paper, saved again\n", "saved-again.pdf")
    jobs.wait(jobs.latest(owner_id, pages_router.RELOCATE, f"{page}:{doc}")["id"])
    assert seen == ["done"]
    drop_pending(ws)
    _sync(me, ws)
    assert _pdf_atts(fake, item) == {att: file_md5(storage.open_path(ws, f"{doc}.pdf"))}


@pytest.mark.parametrize("waited", [True, False], ids=["while-placing", "placed-before"])
def test_replace_zoteros_copy_after_a_replacement_here_sends_gammas_file_over_its_annotations(
        me, made, fake, owner_id, monkeypatch, bounded, waited):
    item, att, ann = _simple_paper(fake)
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    zoteros = fake.data(LIB, att)["md5"]
    placing = {page} if waited else set()
    monkeypatch.setattr(zotero_sync_pdfs, "_relocating", lambda r, side: side.page_id in placing)
    doc = _replace(ws, owner_id, page, pdf_bytes("Simple, Gamma's camera-ready."), "camera.pdf")
    mine = file_md5(storage.open_path(ws, f"{doc}.pdf"))
    if not waited:
        _place_all(ws, owner_id)
    r = me.post(f"/api/zotero/links/{ws}/force", json={"direction": "push"})
    assert r.status_code in (200, 202), r.text
    drop_pending(ws)
    _sync(me, ws)
    if waited:  # the pair waits for the highlights, Gamma's side kept
        assert _page_md5(ws, page) == mine and fake.data(LIB, att)["md5"] == zoteros
        _place_all(ws, owner_id)
        placing.clear()
        zotero_sync_pdfs.relocation_ended(ws, page)
    for _ in range(2):
        drop_pending(ws)
        _sync(me, ws)
    assert _page_md5(ws, page) == mine and _pdf_atts(fake, item) == {att: mine}
    [conflict] = _conflicts(me, ws, "zotero_file")  # Zotero's file, kept here
    assert file_md5(storage.open_path(ws, f"{json.loads(conflict['base'])['doc_id']}.pdf")) == zoteros


def _zoteros_storage_full(me, fake, ws) -> None:
    """Zotero's storage full, as a PDF that goes finds it (413)."""
    fake.quota = 1
    _paper(ws, "zpQuota1", "Big paper", doc_id=_stored_pdf(ws, pdf_bytes("A big paper.")))
    _sync(me, ws)


def _quota_held_new_attachment(me, fake, owner_id):
    """A paper whose PDF was replaced here with its highlight left being
    placed (so Gamma's goes beside Zotero's) while Zotero's storage is full:
    the new attachment's row waits, never created."""
    item, att, ann = _simple_paper(fake)
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    _zoteros_storage_full(me, fake, ws)
    first = _replace(ws, owner_id, page, pdf_bytes("Simple, Gamma's first."), "first.pdf")
    drop_pending(ws)
    zotero_sync.sync_workspace(ws)  # a clock's round: the wait stays
    [(made_key, made_row)] = [(k, row) for k, row in _rows(ws, "attachment").items() if row["page_id"] == page]
    assert made_key != att and made_row["state"] == "waiting" and made_row["detail"]["file"] == "quota"
    assert made_row["detail"]["beside"]["key"] == att and set(made_row["detail"]["beside"]["rows"]) == {ann}
    return ws, item, att, page, made_key, first


def test_a_new_attachment_held_by_zoteros_storage_is_decided_afresh_once_its_highlights_are_placed(
        me, made, fake, owner_id, monkeypatch, bounded):
    ws, item, att, page, made_key, _ = _quota_held_new_attachment(me, fake, owner_id)
    placing = {page}
    monkeypatch.setattr(zotero_sync_pdfs, "_relocating", lambda r, side: side.page_id in placing)
    second = _replace(ws, owner_id, page, pdf_bytes("Simple, Gamma's second."), "second.pdf")
    fake.quota = None
    drop_pending(ws)
    calls = len(fake.calls)
    status = _sync(me, ws)  # Sync now lifts the wait: no attachment is made while the highlights are placed
    assert set(_pdf_atts(fake, item)) == {att} and _uploads(fake, att, calls) == []  # (the big paper goes)
    assert status["waiting"]["files"].get("placing") == 1 and made_key not in _rows(ws)
    _place_all(ws, owner_id)
    placing.clear()
    zotero_sync_pdfs.relocation_ended(ws, page)
    drop_pending(ws)
    _sync(me, ws)
    # decided once they were placed: in place over Zotero's, whose annotation Gamma moves with it
    assert _pdf_atts(fake, item) == {att: file_md5(storage.open_path(ws, f"{second}.pdf"))}
    assert _due_now(ws) == [] and not asked_soon(ws)


def test_a_new_attachment_held_by_zoteros_storage_waits_while_its_highlights_are_placed_again(
        me, made, fake, owner_id, monkeypatch, bounded):
    ws, item, att, page, made_key, first = _quota_held_new_attachment(me, fake, owner_id)
    placing = {page}  # the same file's highlights placed again (Find highlights in this PDF)
    monkeypatch.setattr(zotero_sync_pdfs, "_relocating", lambda r, side: side.page_id in placing)
    fake.quota = None
    drop_pending(ws)
    calls = len(fake.calls)
    status = _sync(me, ws)
    assert set(_pdf_atts(fake, item)) == {att} and _uploads(fake, att, calls) == []  # (the big paper goes)
    assert status["waiting"]["files"].get("placing") == 1 and not asked_soon(ws)
    placing.clear()  # it ends with the highlight still marked: Gamma's goes beside Zotero's, under its key
    zotero_sync_pdfs.relocation_ended(ws, page)
    for _ in range(2):
        drop_pending(ws)
        _sync(me, ws)
    assert _pdf_atts(fake, item) == {att: fake.data(LIB, att)["md5"],
                                     made_key: file_md5(storage.open_path(ws, f"{first}.pdf"))}
    assert _due_now(ws) == [] and not asked_soon(ws)


def test_a_pdf_whose_item_had_none_waits_beside_zoteros_new_pdf_while_its_highlights_are_placed(
        me, made, fake, owner_id, monkeypatch, bounded):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["ppo"])  # a preprint with no PDF in Zotero
    _zoteros_storage_full(me, fake, ws)
    mine = _replace(ws, owner_id, page, pdf_bytes("PPO, Gamma's PDF."), "ppo.pdf")
    drop_pending(ws)
    zotero_sync.sync_workspace(ws)
    [(made_key, row)] = [(k, r) for k, r in _rows(ws, "attachment").items() if r["page_id"] == page]
    assert row["detail"]["file"] == "quota" and "beside" not in row["detail"]
    theirs = fake.add_item(LIB, {"itemType": "attachment", "parentItem": keys["ppo"], "linkMode": "imported_file",
                                 "title": "Full Text PDF", "contentType": "application/pdf", "filename": "arxiv.pdf"})
    fake.add_file(LIB, theirs, pdf_bytes("PPO, Zotero's PDF."))  # meanwhile Zotero found one of its own
    placing = {page}
    monkeypatch.setattr(zotero_sync_pdfs, "_relocating", lambda r, side: side.page_id in placing)
    fake.quota = None
    drop_pending(ws)
    status = _sync(me, ws)
    assert set(_pdf_atts(fake, keys["ppo"])) == {theirs}  # not made while the highlights are placed
    row = _rows(ws)[made_key]
    assert row["state"] == "waiting" and row["detail"]["file"] == zotero_sync_pdfs.FILE_PLACING
    assert status["waiting"]["files"].get("placing") == 1 and not asked_soon(ws)
    assert _queue_entries(ws)[f"file:{made_key}"]["reason"] == zotero_sync_pdfs.PLACING_WAIT
    placing.clear()
    zotero_sync_pdfs.relocation_ended(ws, page)
    for _ in range(2):
        drop_pending(ws)
        _sync(me, ws)
    assert _pdf_atts(fake, keys["ppo"]) == {theirs: fake.data(LIB, theirs)["md5"],
                                            made_key: file_md5(storage.open_path(ws, f"{mine}.pdf"))}
    assert _due_now(ws) == [] and not asked_soon(ws)


def _group_both_new(me, made, fake, owner_id):
    """A group paper whose PDF holds another member's highlight (which Gamma
    may not move), replaced on both sides: Gamma's goes beside Zotero's."""
    group = made["group"]
    item, att, ann = _simple_paper(fake, group, carols=True)
    ws = _link(me, library=group)
    _sync(me, ws)
    page = _page_of(ws, item)
    _replace(ws, owner_id, page, pdf_bytes("Group, Gamma's version."), "gammas.pdf")
    fake.add_file(group, att, pdf_bytes("Group, Zotero's version."))
    drop_pending(ws)
    return ws, group, item, att, page


def test_a_new_attachment_that_got_its_file_before_the_process_died_keeps_no_copy_for_a_later_upload(
        me, made, fake, owner_id, monkeypatch, bounded):
    ws, group, item, att, page = _group_both_new(me, made, fake, owner_id)
    real, sent = zr.upload_pdf, []

    def upload(*args, **kw):
        out = real(*args, **kw)
        sent.append(args[2])
        if len(sent) == 1:
            raise Crash("killed once the upload landed")
        return out

    monkeypatch.setattr(zr, "upload_pdf", upload)
    with pytest.raises(Crash):
        zotero_sync.sync_workspace(ws)
    zotero_sync.reset_interrupted()
    zotero_sync.forget_access()
    drop_pending(ws)
    _sync(me, ws)
    [made_key] = sent
    assert "theirs" not in _rows(ws)[made_key]["detail"]  # bound: the copy it asked for is kept already
    assert len(_conflicts(me, ws, "zotero_file")) == 1
    # a later replacement here goes in place into Gamma's attachment: no copy of Zotero's old file again
    second = _replace(ws, owner_id, page, pdf_bytes("Group, Gamma's second version."), "gammas2.pdf")
    _place_all(ws, owner_id)
    zotero_sync_pdfs.relocation_ended(ws, page)
    drop_pending(ws)
    calls = len(fake.calls)
    _sync(me, ws)
    assert not [p for m, p, _ in fake.calls[calls:] if m == "GET" and p.endswith(f"/items/{att}/file")]
    assert fake.data(group, made_key)["md5"] == file_md5(storage.open_path(ws, f"{second}.pdf"))
    assert len(_conflicts(me, ws, "zotero_file")) == 1


def _attention_both_new(me, made, fake, owner_id):
    """The standard paper replaced on both sides: its Zotero PDF holds
    annotations Gamma cannot move, so Gamma's goes beside it."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    made["synced"] = {k: r["block_id"] for k, r in _rows(ws, "annotation").items()
                      if r["parent"] == keys["pdf"] and r["state"] in ("live", "readonly", "held")}
    mine = _replace(ws, owner_id, page, pdf_bytes("Attention, Gamma's version."), "gammas.pdf")
    fake.add_file(LIB, keys["pdf"], pdf_bytes("Attention, Zotero's version."))
    drop_pending(ws)
    return ws, page, file_md5(storage.open_path(ws, f"{mine}.pdf")), fake.data(LIB, keys["pdf"])["md5"]


def _lose_the_answer(monkeypatch, *, landed: bool) -> list:
    """The next upload's answer lost: after Zotero stored the file
    (``landed``) or before it went. The keys uploaded to."""
    real, sent = zr.upload_pdf, []

    def upload(*args, **kw):
        sent.append(args[2])
        if len(sent) == 1:
            if landed:
                real(*args, **kw)
            raise ZoteroError("the connection dropped before the answer came")
        return real(*args, **kw)

    monkeypatch.setattr(zr, "upload_pdf", upload)
    return sent


@pytest.mark.parametrize("plan", ["both_new", "both"])
def test_an_upload_whose_answer_is_lost_keeps_its_conflict_once_it_landed(me, made, fake, owner_id, monkeypatch,
                                                                           bounded, plan):
    keys = made["keys"]
    if plan == "both_new":
        ws, page, mine, zoteros = _attention_both_new(me, made, fake, owner_id)
    else:
        ws, key, page, _, zoteros = _both_replaced(me, made, fake, owner_id)
        mine = _page_md5(ws, page)
    sent = _lose_the_answer(monkeypatch, landed=True)
    status = _sync(me, ws, ok=False)
    [target] = sent
    assert not status.get("last_error")  # Zotero holds Gamma's file: the upload is done
    assert fake.data(LIB, target)["md5"] == mine and _rows(ws)[target]["detail"]["md5"] == mine
    [conflict] = _conflicts(me, ws, "zotero_file")  # Zotero's replaced file reachable from here
    assert file_md5(storage.open_path(ws, f"{json.loads(conflict['base'])['doc_id']}.pdf")) == zoteros
    if plan == "both_new":
        assert fake.data(LIB, keys["pdf"])["md5"] == zoteros
    calls = len(fake.calls)
    _due(ws)
    drop_pending(ws)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and len(sent) == 1 and len(_conflicts(me, ws, "zotero_file")) == 1


def test_an_upload_whose_answer_is_lost_before_it_went_lets_its_conflict_go(me, made, fake, owner_id, monkeypatch,
                                                                            bounded):
    ws, key, page, _, zoteros = _both_replaced(me, made, fake, owner_id)
    mine = _page_md5(ws, page)
    sent = _lose_the_answer(monkeypatch, landed=False)
    status = _sync(me, ws, ok=False)
    assert status.get("last_error") and fake.data(LIB, key)["md5"] == zoteros
    assert _conflicts(me, ws, "zotero_file") == []  # nothing went over Zotero's file
    _due(ws)
    drop_pending(ws)
    _sync(me, ws)  # tried again: it goes, with one conflict
    assert len(sent) == 2 and fake.data(LIB, key)["md5"] == mine
    assert len(_conflicts(me, ws, "zotero_file")) == 1 and _conflict_count(ws) == 1


@pytest.mark.parametrize("status", [404, 400], ids=["not-in-zotero-storage", "download-fails"])
def test_a_pdf_replaced_on_both_sides_beside_a_file_zotero_cannot_hand_out_goes_without_a_copy(
        me, made, fake, owner_id, bounded, status):
    keys = made["keys"]
    ws, page, mine, zoteros = _attention_both_new(me, made, fake, owner_id)
    tries = 1 if status == 404 else zotero_sync_pdfs.COPY_TRIES  # a download that went wrong is tried again
    fake.fail_next(status, f"/items/{keys['pdf']}/file", method="GET", times=tries)
    for n in range(tries):
        if n:
            _due(ws)
            drop_pending(ws)
        _sync(me, ws)
        if n < tries - 1:  # waits for the copy
            [(made_key, row)] = [(k, r) for k, r in _rows(ws, "attachment").items() if r["page_id"] == page]
            assert row["state"] == "waiting" and row["detail"]["file"] == "failed"
    [new] = [k for k, md5 in _pdf_atts(fake, keys["attention"]).items() if md5 == mine]
    assert fake.data(LIB, keys["pdf"])["md5"] == zoteros  # Zotero's own stays in its attachment
    assert _conflicts(me, ws, "zotero_file") == []  # no "Take Zotero's" that could not bring it
    with connect_pages_db(ws) as conn:
        [(result, resolved)] = conn.execute("SELECT result, resolved FROM sync_conflicts "
                                            "WHERE kind = 'zotero_file'").fetchall()
    assert resolved == 1 and "new attachment beside Zotero's, which stays there" in result
    assert _due_now(ws) == [] and not asked_soon(ws)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_download_of_zoteros_file_that_fails_once_keeps_its_copy_on_the_next_try(me, made, fake, owner_id,
                                                                                   bounded):
    keys = made["keys"]
    ws, page, mine, zoteros = _attention_both_new(me, made, fake, owner_id)
    fake.fail_next(400, f"/items/{keys['pdf']}/file", method="GET")
    _sync(me, ws)
    _due(ws)
    drop_pending(ws)
    _sync(me, ws)
    [conflict] = _conflicts(me, ws, "zotero_file")
    assert file_md5(storage.open_path(ws, f"{json.loads(conflict['base'])['doc_id']}.pdf")) == zoteros
    assert mine in _pdf_atts(fake, keys["attention"]).values()


@pytest.mark.parametrize("again", [False, True], ids=["as-kept", "replaced-in-zotero-since"])
def test_taking_zoteros_file_beside_which_gammas_went_makes_its_attachment_the_pages_pdf_again(
        me, made, fake, owner_id, bounded, again):
    keys = made["keys"]
    ws, page, mine, zoteros = _attention_both_new(me, made, fake, owner_id)
    _sync(me, ws)
    [new] = [k for k, md5 in _pdf_atts(fake, keys["attention"]).items() if md5 == mine]
    [conflict] = _conflicts(me, ws, "zotero_file")
    blocks = _annotation_blocks(ws, page)
    theirs = _annotation_keys(fake, keys["pdf"])
    newest = zoteros
    if again:  # Zotero replaced its own file once more: Take Zotero's ends with that one
        fake.add_file(LIB, keys["pdf"], pdf_bytes("Attention, Zotero's third version."))
        newest = fake.data(LIB, keys["pdf"])["md5"]
        drop_pending(ws)
        _sync(me, ws)
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    for _ in range(2):
        drop_pending(ws)
        _sync(me, ws)
    # Zotero's attachment is the page's PDF again; Gamma's stays beside it; no copy of Zotero's goes up
    assert [k for k, row in _rows(ws, "attachment").items() if row["page_id"] == page] == [keys["pdf"]]
    assert _page_md5(ws, page) == newest
    pdfs = _pdf_atts(fake, keys["attention"])
    assert pdfs[keys["pdf"]] == newest and pdfs[new] == mine and list(pdfs.values()).count(newest) == 1
    # its annotations, by the rows the page synced them with, neither come in twice nor go to Zotero twice
    assert _annotation_keys(fake, keys["pdf"]) == theirs and _annotation_blocks(ws, page) == blocks
    rows = _rows(ws, "annotation")
    assert made["synced"] and {k: rows[k]["block_id"] for k in made["synced"]} == made["synced"]
    assert {rows[k]["parent"] for k in made["synced"]} == {keys["pdf"]}
    assert _due_now(ws) == [] and not asked_soon(ws)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


@pytest.fixture
def gc(monkeypatch):
    """No grace, and the clean-up's thread kept out of the way."""
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 0)
    monkeypatch.setattr(upload_gc, "DEBOUNCE_S", 3600)
    monkeypatch.setattr(upload_gc, "_next_full", None)


def _orphans(ws) -> dict:
    with upload_gc.guard(ws), closing(connect_pages_db(ws)) as conn:
        return dict(conn.execute("SELECT name, since FROM upload_orphans").fetchall())


def _unreferenced_for(ws, name, days):
    """The file as if it went unreferenced ``days`` ago, and was written then."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with closing(connect_pages_db(ws)) as conn:
        conn.execute("INSERT OR REPLACE INTO upload_orphans (name, since) VALUES (?, ?)", (name, since))
        conn.commit()
    t = time.time() - days * DAY
    os.utime(ws_uploads_dir(ws) / name, (t, t))


def test_zoteros_kept_pdf_stays_while_its_conflict_is_open_and_goes_once_it_is_kept(
        me, made, fake, owner_id, no_relocation, gc):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["standalone_pdf"])
    _replace(ws, owner_id, page, pdf_bytes("Gamma's handout, kept apart."), "gammas.pdf")
    fake.add_file(LIB, keys["standalone_pdf"], pdf_bytes("Zotero's handout, kept apart."))
    _sync(me, ws)  # both replaced: Gamma's goes to Zotero, Zotero's is kept here for "theirs"
    [conflict] = [c for c in me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
                  if c["kind"] == "zotero_file"]
    kept = f"{json.loads(conflict['base'])['doc_id']}.pdf"
    assert storage.open_path(ws, kept)
    _unreferenced_for(ws, kept, 31)  # (a slow upload, or a full pass that saw it before the conflict)
    upload_gc.reconcile(ws)
    assert storage.open_path(ws, kept) and kept not in _orphans(ws)  # in use while the conflict is open
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "keep"})
    assert r.status_code == 200, r.text
    upload_gc.reconcile(ws)
    assert kept in _orphans(ws)  # unreferenced from now: kept 30 days, as any file
    _unreferenced_for(ws, kept, 31)
    assert kept in upload_gc.reconcile(ws)["purged"]
    assert not storage.open_path(ws, kept)


# --- a PDF that cannot come, one that comes when opened -------------------------------------------

def test_a_pdf_zotero_storage_does_not_hold_waits_and_gets_no_second_attachment(me, made, fake, owner_id):
    keys = made["keys"]
    webdav = fake.add_item(LIB, {"itemType": "attachment", "parentItem": keys["ppo"], "linkMode": "imported_file",
                                 "title": "PDF", "contentType": "application/pdf", "filename": "ppo.pdf"})
    fake.add_file(LIB, webdav, pdf_bytes("PPO, on WebDAV."), stored=False)
    ws = _link(me)
    status = _sync(me, ws)
    page = _page_of(ws, keys["ppo"])
    row = _rows(ws, "attachment")[webdav]
    assert row["state"] == "waiting" and row["detail"]["file"] == "unavailable"
    assert status["waiting"]["files"] == {"linked": 1, "unavailable": 1} and not status.get("last_error")
    mark = _block(ws, page)["props"]["zotero_pdf"]
    assert mark["state"] == "unavailable" and mark["reason"] == "unavailable"
    calls = len(fake.calls)
    _sync(me, ws)  # not tried again until Zotero's version changes
    assert not any(path.endswith(f"/items/{webdav}/file") for _, path, _ in fake.calls[calls:])
    # the person attaches their own copy: a page whose PDF cannot come takes one; it is not sent
    r = me.post(f"/api/pages/{page}/attachment?ws={ws}",
                json={"doc_id": _stored_pdf(ws, pdf_bytes("My own PPO copy.")), "original_filename": "mine.pdf"})
    assert r.status_code == 200, r.text
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and [d["key"] for d in fake.items(LIB) if d.get("parentItem") == keys["ppo"]] == [
        webdav]
    # the same file the WebDAV attachment names: bound, they are one file again
    other = _page_of(ws, keys["conference"])
    linked = _rows(ws, "attachment")[keys["linked_pdf"]]
    assert linked["state"] == "waiting" and "zotero_pdf" in _block(ws, other)["props"]


def test_the_attach_prompt_answers_409_while_zoteros_pdf_comes(me, made, fake, owner_id, monkeypatch):
    keys = made["keys"]
    monkeypatch.setattr(zotero_sync_pdfs, "NEWEST_FILES", 0)
    ws = _link(me, download="open")
    _sync(me, ws)
    page = _page_of(ws, keys["standalone_pdf"])
    assert _block(ws, page)["props"]["zotero_pdf"]["state"] == "remote"
    doc = _stored_pdf(ws, pdf_bytes("Someone else's file."))
    for method in ("post", "put"):
        r = getattr(me, method)(f"/api/pages/{page}/attachment?ws={ws}",
                                json={"doc_id": doc, "original_filename": "x.pdf"})
        assert r.status_code == 409 and "Zotero" in r.json()["detail"], (method, r.text)


def test_when_opened_brings_only_the_newest_pdfs_until_a_page_is_opened(me, made, fake, monkeypatch):
    keys = made["keys"]
    monkeypatch.setattr(zotero_sync_pdfs, "NEWEST_FILES", 1)
    ws = _link(me, download="open")
    status = _sync(me, ws)
    with_pdf = [p for p, row in ((row["page_id"], row) for row in _rows(ws, "attachment").values())
                if "doc_id" in _block(ws, p)["props"]]
    assert len(with_pdf) == 1 and status["files_pulled"] == 1
    page = _page_of(ws, keys["attention"])
    if page in with_pdf:
        page = _page_of(ws, keys["standalone_pdf"])
    mark = _block(ws, page)["props"]["zotero_pdf"]
    assert mark["state"] == "remote" and mark["size"] > 0 and mark["filename"]
    calls = len(fake.calls)
    _sync(me, ws)
    assert not any(path.endswith("/file") for _, path, _ in fake.calls[calls:])
    r = me.post(f"/api/zotero/links/{ws}/fetch", json={"page_id": page})
    assert r.status_code == 200
    _sync(me, ws)
    props = _block(ws, page)["props"]
    assert props.get("doc_id") and "zotero_pdf" not in props


def test_download_now_brings_back_a_pdf_the_page_let_go(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["standalone_pdf"]]["page_id"]
    r = me.delete(f"/api/pages/{page}/attachment?ws={ws}")
    assert r.status_code == 200, r.text
    _sync(me, ws)
    props = _block(ws, page)["props"]
    assert "doc_id" not in props and props["zotero_pdf"]["state"] == "remote"
    assert fake.data(LIB, keys["standalone_pdf"]).get("md5")  # Zotero keeps its file
    r = me.post(f"/api/zotero/links/{ws}/fetch", json={"page_id": page})
    assert r.status_code == 200
    _sync(me, ws)
    props = _block(ws, page)["props"]
    assert props.get("doc_id") and "zotero_pdf" not in props
    assert file_md5(storage.open_path(ws, f"{props['doc_id']}.pdf")) == fake.data(LIB, keys["standalone_pdf"])["md5"]


# --- full storage, Zotero's and this server's -----------------------------------------------------

def test_zoteros_full_storage_makes_no_fileless_attachments_and_uploads_resume(me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    fake.quota = 1  # no room for any new file
    first = _paper(ws, "ziQuota1", "Big paper", doc_id=_stored_pdf(ws, pdf_bytes("A big paper.")))
    status = _sync(me, ws)
    item = _rows(ws, "item")[object_key(ws, LIB, "item", "", first, "")]
    atts = [r for r in _rows(ws, "attachment").values() if r["page_id"] == first]
    assert len(atts) == 1 and atts[0]["state"] == "waiting" and atts[0]["detail"]["file"] == "quota"
    assert fake.data(LIB, atts[0]["key"]).get("deleted") == 1  # the fileless attachment went to Zotero's trash
    assert status["waiting"]["files"]["quota"] == 1 and status["waiting"]["quota_until"]
    second = _paper(ws, "ziQuota2", "Another paper", doc_id=_stored_pdf(ws, pdf_bytes("Another paper.")))
    zotero_sync.sync_workspace(ws)  # a round of the clock's: no attachment made while the storage is full
    key2 = object_key(ws, LIB, "item", "", second, "")
    assert [d for d in fake.items(LIB) if d.get("parentItem") == key2] == []
    assert zotero_sync.get_link(ws)["status"]["waiting"]["files"]["quota"] == 2
    fake.quota = None
    status = _sync(me, ws)  # Sync now lifts the wait
    assert "quota_until" not in status["waiting"] and status["waiting"]["files"].get("quota") is None
    for page, key in ((first, item["key"]), (second, key2)):
        [att] = [d for d in fake.items(LIB) if d.get("parentItem") == key]
        assert not att.get("deleted") and att["md5"] == file_md5(storage.open_path(
            ws, f"{_block(ws, page)['props']['doc_id']}.pdf"))
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_receive_only_round_restores_no_attachment_a_full_storage_trashed(me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    fake.quota = 1  # no room for any new file: the fileless attachment goes to Zotero's trash
    page = _paper(ws, "zfxQuotaPull", "A big paper", doc_id=_stored_pdf(ws, pdf_bytes("A big paper.")))
    _sync(me, ws)
    [att] = [r for r in _rows(ws, "attachment").values() if r["page_id"] == page]
    assert att["state"] == "waiting" and att["detail"].get("trashed") and fake.data(LIB, att["key"]).get("deleted")
    fake.quota = None
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 200
    calls = len(fake.calls)
    _sync(me, ws)  # Sync now lifts the wait; the round only receives
    assert _writes(fake, calls) == []
    row = _rows(ws, "attachment")[att["key"]]
    assert row["state"] == "waiting" and row["detail"].get("trashed") and fake.data(LIB, att["key"]).get("deleted")
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    _sync(me, ws)
    _sync(me, ws)
    back = fake.data(LIB, att["key"])
    assert not back.get("deleted") and back.get("md5") == file_md5(storage.open_path(
        ws, f"{_block(ws, page)['props']['doc_id']}.pdf"))


def test_this_servers_full_storage_holds_the_downloads_until_it_frees(me, made, fake, monkeypatch):
    from fastapi import HTTPException
    keys = made["keys"]
    full = {"on": True}

    def check(ws, nbytes):
        if full["on"]:
            raise HTTPException(status_code=507, detail="the storage of this server is full")

    monkeypatch.setattr(storage, "check_upload_allowed", check)
    ws = _link(me)
    status = _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    assert "doc_id" not in _block(ws, page)["props"]
    assert status["waiting"]["files"]["storage"] == 2 and not status.get("last_error")
    mark = _block(ws, page)["props"]["zotero_pdf"]
    assert mark["state"] == "unavailable" and mark["reason"] == "quota"
    full["on"] = False
    with connect_pages_db(ws) as conn:
        conn.execute("UPDATE zotero_queue SET next_at = '' WHERE kind = 'file'")
        conn.commit()
    status = _sync(me, ws)
    assert _block(ws, page)["props"].get("doc_id") and status["waiting"]["files"] == {"linked": 1}


# --- a PDF removed for good in Zotero -------------------------------------------------------------

def _paper_with_pdf(me, ws, owner_id, page):
    doc = _stored_pdf(ws, pdf_bytes(f"Gamma's own PDF of {page}."))
    _paper(ws, page, f"Paper {page}", doc_id=doc,
           meta={"title": f"Paper {page}", "year": "2021", "venue": "J. Tests", "kind": "journalArticle"})
    _sync(me, ws)
    item = next(k for k, row in _rows(ws, "item").items() if row["page_id"] == page)
    [att] = [k for k, row in _rows(ws, "attachment").items() if row["page_id"] == page]
    return item, att, doc


def _pdfs_of(fake, item) -> list:
    return [d for d in fake.items(LIB) if d.get("parentItem") == item and d.get("itemType") == "attachment"]


def test_a_pdf_removed_for_good_in_zotero_stays_here_and_is_not_sent_back(me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    item, att, doc = _paper_with_pdf(me, ws, owner_id, "zfxGonePdf")
    fake.delete(LIB, att)
    calls = len(fake.calls)
    for _ in range(3):
        _sync(me, ws)
    assert _pdfs_of(fake, item) == []
    assert not [p for m, p in _writes(fake, calls) if p.endswith("/file")]
    assert _block(ws, "zfxGonePdf")["props"]["doc_id"] == doc
    # Gamma's file changes: it goes again, as a new attachment
    _replace(ws, owner_id, "zfxGonePdf", pdf_bytes("A newer copy of zfxGonePdf."), "newer.pdf")
    _sync(me, ws)
    _sync(me, ws)
    [new] = _pdfs_of(fake, item)
    newer = _block(ws, "zfxGonePdf")["props"]["doc_id"]
    assert new["md5"] == file_md5(storage.open_path(ws, f"{newer}.pdf"))


@pytest.mark.parametrize("sends", [True, False], ids=["pdfs-without-a-record-sent", "kept-here"])
def test_a_standalone_pdf_changed_here_and_removed_for_good_in_zotero_goes_back_or_stays_unlinked(
        me, made, fake, owner_id, sends, bounded):
    keys = made["keys"]
    ws = _link(me, send_unrecorded=sends)
    _sync(me, ws)
    key = keys["standalone_pdf"]
    page = _page_of(ws, key)
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "content": "The handout, renamed here"}])
    fake.delete(LIB, key)
    drop_pending(ws)
    _sync(me, ws)
    assert _block(ws, page)["parent"] == "root" and key not in _rows(ws)
    [conflict] = _conflicts(me, ws)
    assert conflict["kind"] == "zotero_deleted_edited"
    on_page = {k: row for k, row in _rows(ws).items() if row["page_id"] == page}
    if sends:  # a new standalone PDF under a key Zotero never had, its file uploaded
        assert conflict["result"].endswith("it goes back as a new PDF")
        [(new, row)] = on_page.items()
        data = fake.data(LIB, new)
        assert new != key and row["kind"] == "attachment" and not row["parent"] and row["state"] == "live"
        assert data["itemType"] == "attachment" and not data.get("parentItem")
        assert data["title"] == "The handout, renamed here" and data["collections"] == [keys["ml"]]
        doc = _block(ws, page)["props"]["doc_id"]
        assert data["md5"] == file_md5(storage.open_path(ws, f"{doc}.pdf"))
        assert _block(ws, page)["props"]["zotero_key"] == item_uri(LIB, new)
    else:  # its rows go: a page of Gamma's from then on
        assert conflict["result"].endswith("it stays here, unlinked") and on_page == {}
        assert not [d for d in fake.items(LIB) if d.get("itemType") == "attachment" and not d.get("parentItem")
                    and d["title"] == "The handout, renamed here"]
    assert _due_now(ws) == [] and not asked_soon(ws)  # settled in one round
    calls = len(fake.calls)
    for _ in range(2):
        _sync(me, ws)
    assert _writes(fake, calls) == [] and _conflict_count(ws) == 1


def test_a_pdf_trashed_then_purged_in_zotero_is_not_sent_back_after_an_edit_of_its_item(me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    item, att, doc = _paper_with_pdf(me, ws, owner_id, "zfxPurgedPdf")
    fake.trash(LIB, att)
    _sync(me, ws)
    fake.delete(LIB, att)
    _sync(me, ws)
    fake.edit(LIB, item, title="Paper zfxPurgedPdf, retitled")
    calls = len(fake.calls)
    _sync(me, ws)
    _sync(me, ws)
    assert [d for d in _pdfs_of(fake, item) if not d.get("deleted")] == []
    assert not [p for m, p in _writes(fake, calls) if p.endswith("/file")]
    assert _block(ws, "zfxPurgedPdf")["props"]["doc_id"] == doc


# --- hashing and transfers ------------------------------------------------------------------------

def test_unchanged_pdfs_are_not_read_again_to_tell_whether_they_changed(me, made, fake, monkeypatch):
    ws = _link(me)
    _sync(me, ws)
    with connect_pages_db(ws) as conn:  # every page looked at again, as after a restart
        zr.set_state(conn, local_cursor="")
        conn.commit()
    hashed = []
    for module in (zotero_sync_reads, zotero_sync_pdfs, zotero_sync_first):  # every module that hashes a PDF
        monkeypatch.setattr(module, "file_md5", lambda path: hashed.append(path) or "")
    calls = len(fake.calls)
    _sync(me, ws)
    assert hashed == [] and _writes(fake, calls) == []


def test_a_pdf_page_that_never_syncs_is_not_hashed_when_its_page_is_visited(me, made, fake, owner_id, monkeypatch):
    ws = _link(me, upload=False)
    _sync(me, ws)
    doc = _stored_pdf(ws, pdf_bytes("A handout no record names."))
    zotero_local.create_page(ws, page_id="zfxNoRecord", title="handout.pdf", props={"doc_id": doc}, actor="zi",
                             client="t")
    paper = _paper(ws, "zfxNoUpload", "A paper whose PDF stays here", doc_id=_stored_pdf(ws, pdf_bytes("Mine.")),
                   meta={"title": "A paper whose PDF stays here", "year": "2020", "kind": "journalArticle"})
    _sync(me, ws)
    assert any(row["page_id"] == paper for row in _rows(ws, "item").values())
    real, hashed = zotero_sync_reads.file_md5, []
    for module in (zotero_sync_reads, zotero_sync_pdfs, zotero_sync_first):  # every module that hashes a PDF
        monkeypatch.setattr(module, "file_md5", lambda path: hashed.append(path) or real(path))
    _edit(ws, owner_id, "zfxNoRecord", [{"op": "set", "id": "zfxNoRecord", "content": "handout (read).pdf"}])
    _edit(ws, owner_id, paper, [{"op": "set", "id": paper, "content": "A paper whose PDF stays here (read)"}])
    _sync(me, ws)
    assert hashed == []


def test_a_transfer_shows_its_file_and_the_fills_bytes_while_it_runs(me, made, fake, monkeypatch):
    from gamma import zotero_web
    monkeypatch.setattr(zotero_web, "TICK_BYTES", 256)
    seen = []
    patch = zotero_sync_rows.patch_status

    def watch(ws, value, **kw):
        status = patch(ws, value, **kw)
        if (status.get("progress") or {}).get("file"):
            seen.append(status["progress"])
        return status

    for module in (zotero_sync, zotero_sync_pdfs):  # the round's progress and the file transfers' ticks
        monkeypatch.setattr(module, "patch_status", watch)
    ws = _link(me)
    _sync(me, ws)
    files = [p["file"] for p in seen]
    assert files and all(f["dir"] == "down" and 0 < f["done"] <= f["total"] for f in files)
    assert {f["name"] for f in files} >= {"Vaswani - 2017 - Attention is all you need.pdf"}
    assert all(p["bytes"]["done"] <= p["bytes"]["total"] for p in seen)
