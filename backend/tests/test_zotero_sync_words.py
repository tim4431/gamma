"""What the Zotero link's words on screen say it does
(frontend/src/collaboration/syncLinks.js, tested in
frontend/tests/syncLinks.test.mjs), held against the engine and the fake
Zotero (zotero_fake.py): a force push still takes Zotero's version of a
note Gamma cannot write back and of a PDF whose Zotero annotations Gamma
cannot carry over (ZOTERO_ADOPT_LIMITS); a force pull deletes here a note
Zotero trashed, edited here or not, and Recently deleted does not keep it;
with "Send PDFs to Zotero" on, a PDF replaced on a paper whose Zotero PDF
is a linked file stays here (ZOTERO_UPLOAD_HINT), while a copy attached
while Zotero's PDF cannot come yet goes to Zotero once Gamma can read
Zotero's file, Zotero's kept here under Conflicts (zoteroPdfCard's note);
a Manual link still rounds after an edit, and that round brings Zotero's
changes (ZOTERO_CADENCE_TITLE). Rounds run through ``POST …/sync?wait=1``
with the budget lifted; background rounds are stubbed. Accounts are
``zw_…``."""

import copy

import pytest
from fastapi import HTTPException
from zotero_engine import (LIB, _block, _conflicts, _due, _edit, _force, _kids, _link, _page_of,  # noqa: F401
                           _rows, _stored_pdf, _sync, background, drop_pending, fake, idle, made, me, no_relocation,
                           owner_id)
from zotero_fake import FULL_ACCESS
from zotero_fixtures import pdf_bytes

from gamma import storage, zotero_sync
from gamma.db import connect_pages_db
from gamma.routers.pages import replace_attachment

USER, PASSWORD = "zw_owner", "pw-zw_owner-123"

# A note whose citation Gamma cannot write back (zotero_local.note_lossy)
CITED = ('<div data-schema-version="9"><p>As shown <span class="citation" data-citation="%7B%22citationItems%22'
         '%3A%5B%5D%7D">(Vaswani, 2017)</span>, attention suffices.</p><p>A second paragraph.</p></div>')


def _resolved(c, ws) -> list:
    """The link's resolved conflict rows: what a force decided."""
    return c.get(f"/api/zotero/links/{ws}/conflicts?resolved=1").json()["conflicts"]


def _logged(c, ws, page) -> list:
    """The actions the link's log names for ``page``, newest first."""
    return [x["action"] for x in c.get(f"/api/zotero/links/{ws}/log").json()["changes"] if x.get("page_id") == page]


def _uploads(fake, att, since=0) -> list:
    return [p for m, p, _ in fake.calls[since:] if m == "POST" and p.endswith(f"/items/{att}/file")]


def test_a_force_push_takes_zoteros_version_of_a_note_gamma_cannot_write_back(me, made, fake, owner_id):
    keys = made["keys"]
    cited = fake.add_item(LIB, {"itemType": "note", "parentItem": keys["thesis"], "note": CITED})
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    root = _rows(ws, "note")[cited]["block_id"]
    [(child, _)] = _kids(ws, root)
    _edit(ws, owner_id, page, [{"op": "set", "id": child, "content": "Gamma's paragraph, typed here"}])
    _sync(me, ws)
    _force(me, ws, "push")
    _sync(me, ws)
    assert [text for _, text in _kids(ws, root)] == ["A second paragraph."]  # Zotero's, here too
    assert fake.data(LIB, cited)["note"] == CITED
    # Gamma's words stay only in a resolved row, which Conflicts does not list
    [kept] = [c for c in _resolved(me, ws) if c["kind"] == "zotero_note"]
    assert "Gamma's paragraph, typed here" in kept["mine"]
    assert not [c for c in _conflicts(me, ws, "zotero_note")]


def test_a_force_push_takes_zoteros_pdf_whose_annotations_cannot_move(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    zoteros = _block(ws, page)["props"]["doc_id"]
    mine = _stored_pdf(ws, pdf_bytes("Attention, Gamma's own camera-ready."))
    replace_attachment(ws, page, mine, "", "mine.pdf", actor=owner_id, owner=owner_id)
    me.patch(f"/api/zotero/links/{ws}", json={"upload": False})
    _sync(me, ws)
    assert _block(ws, page)["props"]["doc_id"] == mine  # parked: Zotero's annotations wait on the PDFs differing
    md5 = fake.data(LIB, keys["pdf"])["md5"]
    me.patch(f"/api/zotero/links/{ws}", json={"upload": True})
    _force(me, ws, "push")
    _sync(me, ws)
    assert _block(ws, page)["props"]["doc_id"] == zoteros  # Zotero's file replaced Gamma's here
    assert fake.data(LIB, keys["pdf"])["md5"] == md5  # and Gamma's never went over it
    assert "replaced here" in _logged(me, ws, page)


def test_a_force_pull_deletes_here_a_note_zotero_trashed_though_it_was_edited_here(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    first = _kids(ws, root)[0][0]
    _edit(ws, owner_id, page, [{"op": "set", "id": first, "content": "My own edit, made only in Gamma"}])
    fake.trash(LIB, keys["note"])
    _force(me, ws, "pull")
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        held = conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE content LIKE '%My own edit%'").fetchone()[0]
    assert held == 0 and _block(ws, root) is None
    trash = me.get(f"/api/trash?ws={ws}")
    assert trash.status_code == 200 and trash.json()["pages"] == []  # Recently deleted keeps pages, not notes
    assert "My own edit" not in fake.data(LIB, keys["note"])["note"]
    assert any("My own edit" in c["mine"] for c in _resolved(me, ws) if c["kind"] == "zotero_note")


def test_a_pdf_replaced_on_a_paper_whose_zotero_pdf_is_a_linked_file_stays_here_uploads_on(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    assert me.get(f"/api/zotero/links/{ws}").json()["upload"] is True
    page = _page_of(ws, keys["conference"])
    assert _block(ws, page)["props"]["zotero_pdf"]["reason"] == "linked"
    before = {d["key"] for d in fake.items(LIB)}
    mine = _stored_pdf(ws, pdf_bytes("Lewis 2020, my own copy."))
    replace_attachment(ws, page, mine, "", "lewis-mine.pdf", actor=owner_id, owner=owner_id)
    calls = len(fake.calls)
    _sync(me, ws)
    _sync(me, ws)
    assert not [p for m, p, _ in fake.calls[calls:] if m == "POST" and p.endswith("/file")]
    assert {d["key"] for d in fake.items(LIB)} == before  # no PDF beside the linked file either
    assert _block(ws, page)["props"]["doc_id"] == mine


@pytest.mark.parametrize("why", ["failed", "files", "quota"])
def test_a_copy_attached_while_zoteros_pdf_cannot_come_goes_to_zotero_once_it_can(
        me, fake, owner_id, monkeypatch, why):
    item = fake.add_item(LIB, {"itemType": "journalArticle", "title": "A paper whose PDF waits", "date": "2022"})
    att = fake.add_item(LIB, {"itemType": "attachment", "parentItem": item, "linkMode": "imported_file",
                              "title": "Full Text PDF", "contentType": "application/pdf", "filename": "waits.pdf"})
    fake.add_file(LIB, att, pdf_bytes("Zotero's copy of the paper."))
    full = {"on": why == "quota"}  # this server's storage

    def check(ws, nbytes):
        if full["on"]:
            raise HTTPException(status_code=507, detail="the storage of this server is full")

    monkeypatch.setattr(storage, "check_upload_allowed", check)
    if why == "failed":
        fake.fail_next(400, f"/items/{att}/file", method="GET", times=1)
    elif why == "files":
        fake.keys[fake.api_key] = {**copy.deepcopy(FULL_ACCESS), "user": {**FULL_ACCESS["user"], "files": False}}
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    mark = _block(ws, page)["props"]["zotero_pdf"]
    assert mark["state"] == "unavailable" and mark["reason"] == why  # the card offers the attach prompt
    zoteros = fake.data(LIB, att)["md5"]
    full["on"] = False  # room for the person's copy; none again for Zotero's after it
    mine = _stored_pdf(ws, pdf_bytes("My own copy of the paper."))
    replace_attachment(ws, page, mine, "", "mine.pdf", actor=owner_id, owner=owner_id)
    full["on"] = why == "quota"
    if why != "failed":
        _due(ws)
        _sync(me, ws)
        assert fake.data(LIB, att)["md5"] == zoteros  # nothing goes while Gamma cannot read Zotero's file
        fake.keys[fake.api_key] = copy.deepcopy(FULL_ACCESS)
        zotero_sync.forget_access(ws)
        full["on"] = False
    calls = len(fake.calls)
    for _ in range(3):
        _due(ws)
        _sync(me, ws)
    assert _uploads(fake, att, calls) and fake.data(LIB, att)["md5"] != zoteros  # Gamma's went to Zotero
    assert _block(ws, page)["props"]["doc_id"] == mine
    assert len([c for c in _conflicts(me, ws, "zotero_file") if c["page_id"] == page]) == 1  # Zotero's, kept here


def test_a_manual_link_still_rounds_after_an_edit_and_that_round_brings_zoteros_changes(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    info = me.patch(f"/api/zotero/links/{ws}", json={"poll_s": 0}).json()
    assert info["poll_s"] == 0 and info["on_change"] is True  # Manual, "Sync after an edit" on by default
    zotero_sync._clock.refresh()
    drop_pending(ws)
    page = _page_of(ws, keys["thesis"])
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "content": "Quantum error correction (edited)"}])
    assert ws in zotero_sync._clock.pending  # the edit asked for a round
    fake.edit(LIB, keys["ppo"], title="PPO, renamed in Zotero")
    _sync(me, ws)  # a round, whatever asked for it, lists Zotero's changes
    assert _block(ws, _page_of(ws, keys["ppo"]))["content"] == "PPO, renamed in Zotero"
