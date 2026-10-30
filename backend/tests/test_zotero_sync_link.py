"""A Zotero link's life as a link, against the fake Zotero (zotero_fake.py):
a first link or a force spent once nothing of it is left but the PDFs full
storage holds back (this server's, Zotero's), those PDFs then going as the
first link or the force decided them, and a pair still to decide (a
record Zotero refused) keeping the force's side until a newer force
decides it, while a page whose notes
wait to be still keeps it going; a force's log line for each round that
replaced something, and a force push taking Zotero's PDF whose
annotations Gamma cannot write; a first link's read of the library, and
the read of its notes when notes access comes later, in the order the
objects were added, so one edited while they are read is paired all the
same; a publication whose last page was unpublished no bar to a link, and
forgotten as it is made; a reattach taking no ``adopt``, and a first link
detached before it ran keeping its own. Rounds run through ``POST
…/sync?wait=1`` with the budget lifted; background rounds are stubbed.
Accounts are ``zlk_…``."""

import json
import re

import pytest
from fastapi import HTTPException
from zotero_engine import (LIB, _block, _due, _edit, _link, _pages, _paper, _queue_entries, _rows,  # noqa: F401
                           _state, _stored_pdf, _sync, background, fake, idle, made, me, no_relocation, owner_id,
                           quiet)
from zotero_fixtures import HIGHLIGHT_RECT, HIGHLIGHT_TEXT, _annotation, _position, pdf_bytes

from gamma import storage, sync_engine, workspaces, zotero_sync, zotero_sync_blocks
from gamma.db import connect_pages_db, connect_users_db, page_now
from gamma.zotero_annotations import file_md5
from gamma.zotero_sync_blocks import NOTES_WAIT
from gamma.zotero_sync_pdfs import STORAGE_WAIT
from gamma.zotero_web import item_uri

USER, PASSWORD = "zlk_owner", "pw-zlk_owner-123"


@pytest.fixture
def full(monkeypatch):
    """This server's storage full while ``full["on"]`` (off at first):
    every file stored is refused 507, as ``storage.check_upload_allowed``
    refuses it."""
    state = {"on": False}
    real = storage.check_upload_allowed

    def check(ws, nbytes):
        if state["on"]:
            raise HTTPException(status_code=507, detail="this server's storage is full")
        return real(ws, nbytes)

    monkeypatch.setattr(storage, "check_upload_allowed", check)
    return state


def _published(fake, owner_id, name):
    """An item with a PDF Zotero holds no annotation of, and an existing
    workspace whose paper page holds another PDF of it: ``(ws, page,
    attachment key, the page's doc id)``."""
    item = fake.add_item(LIB, {"itemType": "journalArticle", "title": f"Published {name}", "DOI": f"10.1000/{name}",
                               "date": "2022", "publicationTitle": "Science"})
    att = fake.add_item(LIB, {"itemType": "attachment", "parentItem": item, "linkMode": "imported_file",
                              "title": "Full Text PDF", "contentType": "application/pdf", "filename": "published.pdf"})
    fake.add_file(LIB, att, pdf_bytes(f"The publisher's typeset version {name}."))
    ws = workspaces.create(f"Published {name}", owner_id)["id"]
    doc = _stored_pdf(ws, pdf_bytes(f"My arXiv preprint copy {name}."))
    page = _paper(ws, f"zl{name}", f"Published {name}", doc_id=doc,
                  meta={"title": f"Published {name}", "year": "2022", "venue": "Science", "doi": f"10.1000/{name}",
                        "kind": "journalArticle"})
    return ws, page, att, doc


def _page_md5(ws, page) -> str:
    return file_md5(storage.open_path(ws, f"{_block(ws, page)['props']['doc_id']}.pdf"))


def _due_pages(ws) -> None:
    """The page entries due now (a quiet wait over), the files still held."""
    with connect_pages_db(ws) as conn:
        conn.execute("UPDATE zotero_queue SET next_at = '' WHERE kind = 'page'")
        conn.commit()


def _file_conflicts(me, ws) -> list:
    return [c for c in me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"] if c["kind"] == "zotero_file"]


# --- a first link or a force spent while full storage holds its PDFs --------------------------------

def test_a_first_link_is_spent_once_only_pdfs_this_servers_full_storage_holds_are_left(
        me, made, fake, owner_id, full, monkeypatch):
    ws, page, att, doc = _published(fake, owner_id, "Held")
    notes = _paper(ws, "zlNotes", "Attention", zotero_key=item_uri(LIB, made["keys"]["attention"]),
                   meta={"title": "Attention is all you need", "year": "2017"})
    _edit(ws, owner_id, notes, [{"op": "insert", "id": "zlOwnNote", "parent": notes, "content": "My own note."}])
    full["on"] = True
    _link(me, workspace_id=ws, adopt="theirs")
    status = _sync(me, ws)
    queue = _queue_entries(ws)
    assert queue[f"file:{att}"]["reason"] == STORAGE_WAIT  # Zotero's PDF waits for room here...
    assert queue[f"page:{notes}"]["reason"] == NOTES_WAIT  # ...and the page's own notes for it to be still:
    assert _state(ws)["first"] == "existing" and status["adopt"] == "theirs"  # still the first link's
    monkeypatch.setattr(zotero_sync_blocks, "NOTES_QUIET_S", -3600)
    _due_pages(ws)
    status = _sync(me, ws)
    queue = _queue_entries(ws)
    assert f"file:{att}" in queue and {e["reason"] for e in queue.values()} == {STORAGE_WAIT}
    assert status["backlog"] == len(queue)
    assert _state(ws)["first"] == "" and "adopt" not in status  # spent: only the held PDFs are left
    full["on"] = False
    _due(ws)
    _sync(me, ws)
    # decided under the first link's rules (Zotero's version), it goes so without them: Zotero's file comes
    assert _page_md5(ws, page) == fake.data(LIB, att)["md5"] != file_md5(storage.open_path(ws, f"{doc}.pdf"))
    assert _queue_entries(ws) == {}


def test_a_force_push_is_spent_while_this_servers_full_storage_holds_its_pdf_and_still_sends_gammas(
        me, made, fake, owner_id, full):
    ws, page, att, doc = _published(fake, owner_id, "Pushed")
    zoteros = fake.data(LIB, att)["md5"]
    _link(me, workspace_id=ws, adopt="mine", upload=False)
    _sync(me, ws)
    _sync(me, ws)
    assert fake.data(LIB, att)["md5"] == zoteros  # parked: both files stay
    me.patch(f"/api/zotero/links/{ws}", json={"upload": True})
    full["on"] = True  # Gamma's goes over Zotero's once Zotero's is kept here: no room for it
    zotero_sync.force_sync(ws, "push")
    status = _sync(me, ws)
    assert _queue_entries(ws)[f"file:{att}"]["reason"] == STORAGE_WAIT and status["backlog"] == 1
    assert "forced" not in status and "adopt" not in status  # the pill no longer says "Force push"
    row = _rows(ws)[att]
    assert row["state"] == "waiting" and row["detail"]["file"] == "storage" and row["detail"]["adopt"] == "mine"
    assert fake.data(LIB, att)["md5"] == zoteros
    full["on"] = False
    _due(ws)
    _sync(me, ws)
    # decided by the force (Gamma's version), it goes so once the force is over: Gamma's file in Zotero,
    # Zotero's kept here
    mine = file_md5(storage.open_path(ws, f"{doc}.pdf"))
    assert fake.data(LIB, att)["md5"] == mine and _page_md5(ws, page) == mine
    [conflict] = _file_conflicts(me, ws)
    kept = json.loads(conflict["base"])["doc_id"]
    assert file_md5(storage.open_path(ws, f"{kept}.pdf")) == zoteros


def test_a_force_push_held_by_zoteros_full_storage_still_sends_gammas_pdf_once_it_lifts(me, made, fake, owner_id):
    ws, page, att, doc = _published(fake, owner_id, "Quota")
    zoteros = fake.data(LIB, att)["md5"]
    _link(me, workspace_id=ws, adopt="mine", upload=False)
    _sync(me, ws)
    _sync(me, ws)
    me.patch(f"/api/zotero/links/{ws}", json={"upload": True})
    fake.quota = 1  # Zotero's storage has no room for Gamma's file
    zotero_sync.force_sync(ws, "push")
    status = _sync(me, ws)
    assert "forced" not in status and "adopt" not in status and status["waiting"]["files"]["quota"] == 1
    row = _rows(ws)[att]
    assert row["state"] == "waiting" and row["detail"]["file"] == "quota" and row["detail"]["adopt"] == "mine"
    fake.quota = None
    _sync(me, ws)  # Sync now lifts the wait
    _sync(me, ws)
    mine = file_md5(storage.open_path(ws, f"{doc}.pdf"))
    assert fake.data(LIB, att)["md5"] == mine and _page_md5(ws, page) == mine  # Gamma's, as the force decided
    [conflict] = _file_conflicts(me, ws)
    assert file_md5(storage.open_path(ws, f"{json.loads(conflict['base'])['doc_id']}.pdf")) == zoteros


@pytest.mark.parametrize("held", ["storage", "quota"])
def test_a_force_push_whose_record_zotero_refused_still_sends_gammas_pdf_and_record_once_full_storage_frees(
        me, made, fake, owner_id, full, held):
    ws, page, att, doc = _published(fake, owner_id, f"Refused{held.title()}")
    item = fake.data(LIB, att)["parentItem"]
    zoteros, mine = fake.data(LIB, att)["md5"], file_md5(storage.open_path(ws, f"{doc}.pdf"))
    _link(me, workspace_id=ws, adopt="mine", upload=False)
    _sync(me, ws)
    _sync(me, ws)
    me.patch(f"/api/zotero/links/{ws}", json={"upload": True})
    fake.edit(LIB, item, title="Zotero's own title")  # a record the force push sends Gamma's side of...
    fake.fail_next(400, f"/{LIB}/items", method="POST")  # ...which Zotero refuses
    if held == "storage":
        full["on"] = True  # no room here for Zotero's file, kept before Gamma's goes over it
    else:
        fake.quota = 1  # no room in Zotero for Gamma's file
    zotero_sync.force_sync(ws, "push")
    status = _sync(me, ws)
    assert "forced" not in status and "adopt" not in status  # spent: only the held PDF is left
    row = _rows(ws)[item]
    assert row["detail"]["rejected"] and row["detail"]["first"] and row["detail"]["adopt"] == "mine"
    assert fake.data(LIB, item)["title"] == "Zotero's own title"
    full["on"], fake.quota = False, None
    _due(ws)
    _sync(me, ws)  # (Sync now lifts Zotero's wait too)
    _sync(me, ws)
    # the PDF as the force decided it, though its item's row is still a pair: Gamma's in Zotero, Zotero's kept
    assert fake.data(LIB, att)["md5"] == mine and _page_md5(ws, page) == mine
    [conflict] = _file_conflicts(me, ws)
    assert file_md5(storage.open_path(ws, f"{json.loads(conflict['base'])['doc_id']}.pdf")) == zoteros
    # the record too, once the page changes: Gamma's side goes, Zotero's title does not come over the page's
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "year": "2023"}}}])
    _sync(me, ws)
    assert _block(ws, page)["props"]["meta"]["title"] == f"Published Refused{held.title()}"
    assert fake.data(LIB, item)["title"] == f"Published Refused{held.title()}"
    assert "first" not in _rows(ws)[item]["detail"]


def test_a_force_pull_takes_zoteros_side_of_a_pair_an_earlier_force_push_left_to_decide(me, made, fake, owner_id):
    ws, page, att, doc = _published(fake, owner_id, "Repulled")
    item = fake.data(LIB, att)["parentItem"]
    _link(me, workspace_id=ws, adopt="mine", upload=False)
    _sync(me, ws)
    fake.edit(LIB, item, title="Zotero's own title")
    fake.fail_next(400, f"/{LIB}/items", method="POST")
    zotero_sync.force_sync(ws, "push")
    status = _sync(me, ws)
    assert "forced" not in status and _rows(ws)[item]["detail"]["adopt"] == "mine"  # left to decide, as pushed
    zotero_sync.force_sync(ws, "pull")  # the newer force decides it with its own side
    _sync(me, ws)
    assert _block(ws, page)["props"]["meta"]["title"] == "Zotero's own title"
    assert "first" not in _rows(ws)[item]["detail"] and "adopt" not in _rows(ws)[item]["detail"]


# --- what a force writes down ---------------------------------------------------------------------

def test_a_force_writes_a_log_line_for_each_round_that_replaced_something(me, made, fake, owner_id, monkeypatch):
    ws = _link(me)
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        papers = [r[0] for r in conn.execute("SELECT page_id FROM zotero_objects WHERE kind = 'item' "
                                             "AND page_id != ''")]
    edited = 0
    for pid in papers:
        meta = _block(ws, pid)["props"].get("meta")
        if isinstance(meta, dict) and meta.get("title"):
            _edit(ws, owner_id, pid, [{"op": "set", "id": pid, "props": {"meta": {**meta, "year": "1999"}}}])
            edited += 1
    assert edited > 2
    monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 2)
    zotero_sync.force_sync(ws, "pull")
    for _ in range(40):
        status = _sync(me, ws)
        if "forced" not in status and not status.get("backlog"):
            break
    assert "forced" not in status
    lines = [x["title"] for x in zotero_sync.list_log(ws, 500) if x["action"] == "replaced here"]
    assert len(lines) > 1  # a line per round, not one for the force
    assert sum(int(re.match(r"replaced here: (\d+) record", line).group(1)) for line in lines) == edited


def test_a_force_push_takes_zoteros_pdf_whose_annotations_gamma_cannot_write(me, made, fake, owner_id):
    ws, page, att, doc = _published(fake, owner_id, "Annotated")
    zoteros, mine = fake.data(LIB, att)["md5"], file_md5(storage.open_path(ws, f"{doc}.pdf"))
    _link(me, workspace_id=ws, adopt="mine", upload=False)
    _sync(me, ws)
    _sync(me, ws)
    fake.add_item(LIB, _annotation(att, "highlight", "#ffd400", "00000|000024|00108",
                                   _position(0, rects=[HIGHLIGHT_RECT]), annotationText=HIGHLIGHT_TEXT))
    _sync(me, ws)
    assert _page_md5(ws, page) == mine and fake.data(LIB, att)["md5"] == zoteros
    me.patch(f"/api/zotero/links/{ws}", json={"upload": True})
    zotero_sync.force_sync(ws, "push")
    _sync(me, ws)
    _sync(me, ws)
    assert _page_md5(ws, page) == zoteros and fake.data(LIB, att)["md5"] == zoteros  # Zotero's file, on both


# --- a first link's read of the library -----------------------------------------------------------

def test_a_first_link_pairs_an_item_edited_between_two_pages_of_its_read(me, made, fake, owner_id):
    keys = made["keys"]
    for n in range(120):  # more than one page (100 objects) of top-level items
        fake.add_item(LIB, {"itemType": "book", "title": f"Filler book {n}", "date": "2000"})
    ws = workspaces.create("Existing, read across pages", owner_id)["id"]
    _paper(ws, "zlAttn", "My copy of attention", zotero_key=item_uri(LIB, keys["attention"]),
           meta={"title": "Attention, as I filed it", "year": "2017"})
    # an item of the second page in Zotero's default order (the last modified first) edited once the first
    # page is read: in that order it moves to the first page, read already
    fake.on_request("start=100", lambda: fake.edit(LIB, keys["attention"], extra="Edited meanwhile"))
    _link(me, workspace_id=ws, adopt="theirs")
    _sync(me, ws)
    assert _rows(ws)[keys["attention"]]["page_id"] == "zlAttn"
    assert [t for t in _pages(ws).values() if "ttention" in t] == ["My copy of attention"]  # no second page
    assert [d["key"] for d in fake.items(LIB) if "ttention" in str(d.get("title"))] == [keys["attention"]]


def test_notes_access_granted_later_pairs_a_note_edited_between_two_pages_of_its_read(me, made, fake, quiet):
    key = made["keys"]["standalone_note"]
    for n in range(120):  # more than one page (100 objects) of standalone notes
        fake.add_item(LIB, {"itemType": "note", "note": f"<p>Filler note {n}</p>"})
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws, "note")[key]["page_id"]
    zotero_sync.remove_link(ws)  # made again into its workspace with a key that sees no notes
    fake.keys[fake.api_key]["user"]["notes"] = False
    zotero_sync.forget_access()
    assert me.post("/api/zotero/links", json={"library": LIB, "workspace_id": ws}).status_code == 201
    _sync(me, ws)
    _sync(me, ws)
    fake.keys[fake.api_key]["user"]["notes"] = True
    zotero_sync.forget_access()
    # the oldest note, of the second page in Zotero's default order (the last modified first), edited once the
    # first page is read: in that order it moves to the first page, read already
    fake.on_request("start=100", lambda: fake.edit(LIB, key, note=fake.data(LIB, key)["note"] + "<p>Edited</p>"))
    for _ in range(3):
        _sync(me, ws)
    assert _rows(ws, "note")[key]["page_id"] == page
    uri = item_uri(LIB, key)
    with connect_pages_db(ws) as conn:
        pages = conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE parent_id = 'root' AND (json_extract("
                             "properties, '$.zotero_note') = ? OR json_extract(properties, '$.zotero_key') = ?)",
                             (uri, uri)).fetchone()[0]
    assert pages == 1  # no second note page


# --- what a link may be made in -------------------------------------------------------------------

@pytest.mark.parametrize("page_filter, linked", [("[]", True), ('["zlPublished"]', False), (None, False)])
def test_a_publication_whose_last_page_was_unpublished_is_no_bar_to_a_link(me, made, fake, owner_id, page_filter,
                                                                          linked):
    info = workspaces.create(f"Was published {page_filter}", owner_id)
    with connect_users_db() as conn:
        conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, created_at, page_filter) "
                     "VALUES (?, 'https://share.example', 'w1', 'x', ?, ?, ?)",
                     (info["id"], owner_id, page_now(), page_filter))
        conn.commit()
    ws = info["id"]
    _paper(ws, "zlOnce", "A page once published")
    sync_engine._note(ws, "zlOnce", "pushed")  # what the publication kept: a log line, a conflict
    with connect_pages_db(ws) as conn:
        sync_engine._conflict(conn, "zlOnce", "zlOnce", "merged", mine="mine", theirs="theirs")
        conn.commit()
    try:
        r = me.post("/api/zotero/links", json={"library": LIB, "workspace_id": ws})
        if linked:
            assert r.status_code == 201, r.text
            _sync(me, ws)
            # the emptied publication is forgotten: the link's conflicts and log are its own...
            assert sync_engine.get_mirror(ws) is None
            assert me.get(f"/api/zotero/links/{ws}").json()["conflicts_open"] == 0
            assert me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"] == []
            log = me.get(f"/api/zotero/links/{ws}/log?limit=500").json()["changes"]
            assert log and not any(x["action"] == "pushed" and x["page_id"] == "zlOnce" for x in log)
            # ...and no mirror's Forget reaches them
            assert me.delete(f"/api/mirrors/{ws}").status_code == 404
            assert len(zotero_sync.list_log(ws, 500)) == len(log)
        else:
            assert r.status_code == 400 and "clone or publishes pages" in r.json()["detail"]
            assert sync_engine.get_mirror(ws) is not None
    finally:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM mirrors WHERE workspace_id = ?", (ws,))
            conn.commit()


def test_a_reattach_takes_no_adopt(me, made, fake):
    ws = _link(me)
    _sync(me, ws)
    me.post(f"/api/zotero/links/{ws}/detach")
    info = me.post(f"/api/zotero/links/{ws}/relink", json={"adopt": "mine"}).json()
    assert info["mode"] == "two-way" and "adopt" not in info["status"]
    _sync(me, ws)


def test_a_first_link_detached_before_it_ran_keeps_its_own_adopt_across_a_reattach(me, made, fake, owner_id):
    keys = made["keys"]
    ws = workspaces.create("Mine, detached at once", owner_id)["id"]
    _paper(ws, "zlMine", "My copy of attention", zotero_key=item_uri(LIB, keys["attention"]),
           meta={"title": "Attention, as I filed it", "year": "2017"})
    _link(me, workspace_id=ws, adopt="mine")
    me.post(f"/api/zotero/links/{ws}/detach")
    info = me.post(f"/api/zotero/links/{ws}/relink", json={"adopt": "theirs"}).json()
    assert info["status"]["adopt"] == "mine"  # the first link's own, whatever the body says
    _sync(me, ws)
    assert fake.data(LIB, keys["attention"])["title"] == "Attention, as I filed it"
    assert _block(ws, "zlMine")["props"]["meta"]["title"] == "Attention, as I filed it"
