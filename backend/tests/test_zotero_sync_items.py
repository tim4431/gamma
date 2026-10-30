"""The Zotero link's items and pages (gamma/zotero_sync_items.py,
zotero_sync_pages.py, zotero_sync_gone.py) against the fake Zotero
(zotero_fake.py): the first fill of a new workspace (an item first seen in
Zotero's trash brings its children once restored), the incremental pull of
each change of a record, its tags and collections, its trash, restore and
removal for good (a child removed for good leaves its item's page alone; a
page edited as its item goes stays and goes as a new item), a retype and a
kind the record only infers, the push of each Gamma change (a record,
labels, folders, new papers fifty a request, a PDF without a record, the
trash and an undo), both sides changed and the field conflicts resolved
(receive only, a standalone PDF's title, a conflict Zotero moved on from), a
create whose answer was lost, re-parenting (Create Parent Item, a standalone
PDF given a parent from here whatever its create and its move meet, Merge
Items), and the round's own guarantees (an edit made as a unit reads or
writes reaches Zotero, the change feed passes by the link's own writes and
never a person's edit). The PDF is test_zotero_sync_pdfs's, a first link and
a force test_zotero_sync_first_link's. Rounds run through
``POST …/sync?wait=1`` with the budget lifted; background rounds are
stubbed. Accounts are ``zi_…``."""

import pytest
from zotero_engine import (LIB, _block, _conflicts, _due, _edit, _folder_ops, _link, _page_of,  # noqa: F401
                           _pages, _paper, _paths, _rows, _stored_pdf, _sync, _writes, background, drop_pending, fake,
                           idle, made, me, no_relocation, owner_id)
from zotero_fixtures import pdf_bytes

from gamma import ops, storage, zotero_local, zotero_sync, zotero_sync_items, zotero_sync_pages, zotero_sync_rows
from gamma import zotero_remote as zr
from gamma.blocks_store import FOLDERS, LABELS, TRASH, label_names, tree_rows
from gamma.db import connect_pages_db
from gamma.zotero_annotations import file_md5
from gamma.zotero_web import ZoteroUnreachable, item_uri, object_key

USER, PASSWORD = "zi_owner", "pw-zi_owner-123"


def _queue(ws) -> dict:
    with connect_pages_db(ws) as conn:
        return {row[0]: row[1] for row in conn.execute("SELECT id, kind FROM zotero_queue")}


def _labels_of(ws, page_id) -> list:
    with connect_pages_db(ws) as conn:
        names = label_names(conn)
    return sorted(names[lid] for lid in _block(ws, page_id)["props"].get(LABELS) or ())


def _folder_paths(ws, page_id) -> list:
    with connect_pages_db(ws) as conn:
        folders = {fid: (name, parent) for fid, parent, name, _ in tree_rows(conn, FOLDERS)}

    def path(fid):
        names = []
        while fid in folders:
            names.append(folders[fid][0])
            fid = folders[fid][1]
        return tuple(reversed(names))
    return sorted(path(f) for f in _block(ws, page_id)["props"].get(FOLDERS) or ())


def _by_title(fake, title) -> dict:
    found = [d for d in fake.items(LIB) if d.get("title") == title]
    assert len(found) == 1, found
    return found[0]


# --- the first fill ---------------------------------------------------------------------

def test_a_new_link_brings_every_item_as_a_page_with_its_record_labels_folders_and_pdf(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    status = _sync(me, ws)
    items = _rows(ws, "item")
    assert set(items) == {keys[n] for n in ("attention", "ppo", "deep_learning", "thesis", "conference")}
    standalone = _rows(ws, "attachment")[keys["standalone_pdf"]]
    assert standalone["parent"] == "" and standalone["page_id"]
    assert keys["trashed"] not in _rows(ws)
    note = _rows(ws, "note")[keys["standalone_note"]]  # a standalone note is a note page (test_zotero_sync_notes)
    assert note["parent"] == "" and note["page_id"] == note["block_id"]
    page = _page_of(ws, keys["attention"])
    block = _block(ws, page)
    meta = block["props"]["meta"]
    assert block["content"] == "Attention is all you need" == meta["title"]
    assert meta["authors"] == ["Ashish Vaswani", "Noam Shazeer"] and meta["year"] == "2017"
    assert meta["doi"] == "10.1038/s41586-000-00000-0" and meta["source"] == "zotero"
    assert block["props"]["cite_key"] == "vaswani2017attention"
    assert block["props"]["bibtex"].startswith("@article{vaswani2017attention,")
    assert block["props"]["zotero_key"] == item_uri(LIB, keys["attention"])
    assert _labels_of(ws, page) == ["transformers"]  # the automatic tag stays in Zotero
    assert _folder_paths(ws, page) == [("ML", "Transformers")]
    assert _folder_paths(ws, _page_of(ws, keys["conference"])) == [("ML", "Transformers", "Efficient"), ("Reading",)]
    # the primary PDF (the oldest) came; the supplement stays in Zotero
    pdf = _rows(ws, "attachment")[keys["pdf"]]
    assert pdf["page_id"] == page and pdf["parent"] == keys["attention"] and pdf["state"] == "live"
    assert pdf["detail"]["md5"] == pdf["detail"]["gmd5"] == fake.data(LIB, keys["pdf"])["md5"]
    assert block["props"]["doc_id"] == pdf["detail"]["doc_id"] and "zotero_pdf" not in block["props"]
    assert keys["supplement"] not in _rows(ws)
    assert _block(ws, standalone["page_id"])["props"]["doc_id"] and _block(ws, standalone["page_id"])["content"] == (
        "Scanned handout")
    # the conference paper's PDF is a linked file: it waits, shown on the page
    linked = _rows(ws, "attachment")[keys["linked_pdf"]]
    assert linked["state"] == "waiting" and linked["detail"]["file"] == "linked"
    mark = _block(ws, _page_of(ws, keys["conference"]))["props"]["zotero_pdf"]
    assert mark["key"] == keys["linked_pdf"] and mark["state"] == "unavailable" and mark["reason"] == "linked"
    assert status["pages_pulled"] == 7 and status["files_pulled"] == 2 and status["backlog"] == 0
    assert status["waiting"]["files"] == {"linked": 1}
    assert _writes(fake) == []
    # a round after a round writes nothing on either side
    for _ in range(2):
        calls = len(fake.calls)
        status = _sync(me, ws)
        assert _writes(fake, calls) == [] and status["pages_pulled"] == status["pages_pushed"] == 0
        assert status["blocks_added"] == status["blocks_changed"] == 0
    assert len(_pages(ws)) == 7  # the six items' and the standalone note's


def test_bounded_rounds_resume_the_fill_without_duplicates(me, made, fake, monkeypatch):
    monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 3)
    ws = _link(me)
    rounds = 0
    while True:
        drop_pending(ws)
        status = _sync(me, ws)
        rounds += 1
        if not status["backlog"]:
            break
        assert "progress" not in status and ws in zotero_sync._clock.pending and rounds < 30  # the next, at once
    assert rounds > 2 and len(_pages(ws)) == 7 and len(_rows(ws, "item")) == 5
    assert _writes(fake) == []


def test_an_item_first_seen_in_zoteros_trash_brings_every_child_once_it_is_restored(me, fake):
    from zotero_fixtures import _annotation, _position, generated_library
    generated_library(fake, 6, pdfs=2, annotations=1, notes=2)
    key = fake.add_item(LIB, {"itemType": "journalArticle", "title": "Back from the trash", "date": "2020"})
    att = fake.add_item(LIB, {"itemType": "attachment", "parentItem": key, "linkMode": "imported_file",
                              "title": "Full Text PDF", "contentType": "application/pdf", "filename": "back.pdf"})
    fake.add_file(LIB, att, pdf_bytes("Back from the trash"))
    note = fake.add_item(LIB, {"itemType": "note", "parentItem": key, "note": "<p>Read again</p>"})
    ann = fake.add_item(LIB, _annotation(att, "highlight", "#ffd400", "00000|000000|00000",
                                         _position(0, rects=[[72, 700, 160, 714]]), annotationText="Back"))
    fake.trash(LIB, key)
    ws = _link(me)
    _sync(me, ws)
    assert not {key, att, note, ann} & set(_rows(ws))  # (no page: its children went off the queue with it)
    fake.restore(LIB, key)
    _sync(me, ws)
    _sync(me, ws)
    rows = _rows(ws)
    page = rows[key]["page_id"]
    assert rows[att]["page_id"] == rows[note]["page_id"] == rows[ann]["page_id"] == page
    assert rows[ann]["state"] == "live" and _block(ws, page)["props"].get("doc_id")


# --- Zotero's changes ----------------------------------------------------------------------

def test_a_record_change_in_zotero_reaches_only_its_fields(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"ppt_cite": "x", "my_note": "kept"}}])
    fake.edit(LIB, keys["attention"], title="Attention Is All You Need", date="2018")
    calls = len(fake.calls)
    status = _sync(me, ws)
    block = _block(ws, page)
    assert block["content"] == "Attention Is All You Need"  # the title followed the record's
    assert block["props"]["meta"]["title"] == "Attention Is All You Need" and block["props"]["meta"]["year"] == "2018"
    assert block["props"]["meta"]["venue"] == "Nature" and block["props"]["my_note"] == "kept"
    assert "ppt_cite" not in block["props"] and "2018" in block["props"]["bibtex"]
    assert status["pages_pulled"] == 1 and _writes(fake, calls) == []
    row = _rows(ws, "item")[keys["attention"]]
    assert row["version"] == fake.data(LIB, keys["attention"])["version"]
    assert row["zbase"]["fields"]["year"] == row["gbase"]["fields"]["year"] == "2018"
    with connect_pages_db(ws) as conn:
        assert conn.execute("SELECT actor, client FROM page_ops WHERE page_id = ? ORDER BY seq DESC LIMIT 1",
                            (page,)).fetchone() == ("zotero", "zotero")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # a page renamed here keeps its own title when the record's changes
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "content": "My name for it"}])
    _sync(me, ws)
    fake.edit(LIB, keys["attention"], title="Attention (revised)")
    _sync(me, ws)
    assert _block(ws, page)["content"] == "My name for it"
    assert _block(ws, page)["props"]["meta"]["title"] == "Attention (revised)"


def test_tags_and_collections_from_zotero_file_the_page_and_automatic_tags_stay_out(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["ppo"])
    tags = fake.data(LIB, keys["ppo"])["tags"]
    fake.edit(LIB, keys["ppo"], tags=[*tags, {"tag": "RL"}, {"tag": "Computer Science - Learning", "type": 1}],
              collections=[keys["reading"], keys["efficient"]])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _labels_of(ws, page) == ["RL"]
    assert _folder_paths(ws, page) == [("ML", "Transformers", "Efficient"), ("Reading",)]
    assert _writes(fake, calls) == []
    fake.edit(LIB, keys["ppo"], tags=[{"tag": "Computer Science - Learning", "type": 1}], collections=[keys["ml"]])
    _sync(me, ws)
    assert _labels_of(ws, page) == [] and _folder_paths(ws, page) == [("ML",)]
    # the link set to bring automatic tags: every item looked at again, the automatic ones become labels
    me.patch(f"/api/zotero/links/{ws}", json={"auto_tags": True})
    _sync(me, ws)
    assert _labels_of(ws, page) == ["Computer Science - Learning"]
    assert _labels_of(ws, _page_of(ws, keys["attention"])) == ["attention", "transformers"]


def test_a_trash_in_zotero_trashes_the_page_and_a_restore_brings_it_back(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    fake.trash(LIB, keys["thesis"])
    calls = len(fake.calls)
    status = _sync(me, ws)
    assert _block(ws, page)["parent"] == TRASH and status["pages_deleted"] == 1
    assert _rows(ws, "item")[keys["thesis"]]["state"] == "trashed" and _writes(fake, calls) == []
    fake.restore(LIB, keys["thesis"])
    _sync(me, ws)
    assert _block(ws, page)["parent"] == "root" and _rows(ws, "item")[keys["thesis"]]["state"] == "live"
    assert _writes(fake, calls) == []
    # restored in Zotero after the page was purged here: the page is made anew
    fake.trash(LIB, keys["thesis"])
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        ops.delete_page(ws, conn, page, actor="zi")
    fake.restore(LIB, keys["thesis"])
    _sync(me, ws)
    again = _page_of(ws, keys["thesis"])
    assert again != page and _block(ws, again)["parent"] == "root"
    assert _block(ws, again)["content"] == "Quantum error correction with bosonic codes"
    assert _rows(ws, "item")[keys["thesis"]]["state"] == "live" and _writes(fake, calls) == []


def test_an_item_removed_for_good_goes_unless_its_page_changed_here(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    gone, kept = _page_of(ws, keys["deep_learning"]), _page_of(ws, keys["thesis"])
    _edit(ws, owner_id, kept, [{"op": "set", "id": kept, "props": {"meta": {
        **_block(ws, kept)["props"]["meta"], "year": "2020"}}}])
    fake.delete(LIB, keys["deep_learning"])
    fake.delete(LIB, keys["thesis"])
    _sync(me, ws)
    assert _block(ws, gone)["parent"] == TRASH and keys["deep_learning"] not in _rows(ws)
    # changed here: it stays, and goes back to Zotero as a new item under a key Zotero never had
    assert _block(ws, kept)["parent"] == "root" and keys["thesis"] not in _rows(ws)
    [(new_key, row)] = [(k, r) for k, r in _rows(ws, "item").items() if r["page_id"] == kept]
    assert new_key != keys["thesis"] and row["state"] == "live"
    assert fake.data(LIB, new_key)["title"] == "Quantum error correction with bosonic codes"
    assert fake.data(LIB, new_key)["date"] == "2020"
    assert _block(ws, kept)["props"]["zotero_key"] == item_uri(LIB, new_key)
    conflicts = me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
    assert [c["kind"] for c in conflicts] == ["zotero_deleted_edited"]


def test_a_page_edited_as_its_item_removed_for_good_is_trashed_stays_and_goes_as_a_new_item(me, made, fake, owner_id,
                                                                                         monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["thesis"]]["page_id"]
    real = zotero_local.trash_page

    def edited_meanwhile(ws_, page_id, **kw):
        if page_id == page:
            meta = _block(ws, page)["props"]["meta"]
            _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "year": "2001"}}}])
        return real(ws_, page_id, **kw)

    monkeypatch.setattr(zotero_local, "trash_page", edited_meanwhile)
    fake.delete(LIB, keys["thesis"])
    _sync(me, ws, ok=False)
    monkeypatch.setattr(zotero_local, "trash_page", real)
    assert _block(ws, page)["parent"] == "root"  # the edit made as it went keeps it
    assert _block(ws, page)["props"]["meta"]["year"] == "2001"
    _due(ws)
    _sync(me, ws)
    [item] = [k for k, row in _rows(ws, "item").items() if row["page_id"] == page]
    assert item != keys["thesis"] and fake.data(LIB, item)["date"] == "2001"


def _regions(ws, page) -> int:
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE page_id = ? AND id != ? AND "
                            "json_extract(properties, '$.zotero_note') IS NOT NULL", (page, page)).fetchone()[0]


def _live_annotations(fake) -> set:
    return {d["key"] for d in fake.items(LIB) if d.get("itemType") == "annotation" and not d.get("deleted")}


def test_an_annotation_removed_for_good_leaves_its_item_page_and_other_annotations_alone(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["attention"]]["page_id"]
    pages, regions = set(_pages(ws)), _regions(ws, page)
    others = _live_annotations(fake) - {keys["highlight"]}
    fake.delete(LIB, keys["highlight"])
    calls = len(fake.calls)
    _sync(me, ws)
    _sync(me, ws)
    assert set(_pages(ws)) == pages
    assert _regions(ws, page) == regions
    assert _writes(fake, calls) == []
    assert others <= _live_annotations(fake)
    assert _rows(ws)[keys["pdf"]]["page_id"] == page


def test_a_highlight_deleted_here_then_purged_from_zoteros_trash_changes_nothing_more(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["attention"]]["page_id"]
    block = _rows(ws)[keys["highlight"]]["block_id"]
    _edit(ws, owner_id, page, [{"op": "delete", "id": block}])
    _sync(me, ws)
    assert fake.data(LIB, keys["highlight"]).get("deleted")
    pages, regions = set(_pages(ws)), _regions(ws, page)
    others = _live_annotations(fake)
    fake.delete(LIB, keys["highlight"])
    calls = len(fake.calls)
    _sync(me, ws)
    assert set(_pages(ws)) == pages
    assert _regions(ws, page) == regions
    assert _writes(fake, calls) == []
    assert others <= _live_annotations(fake)


def test_a_child_attachment_listed_as_a_top_entry_takes_its_items_unit(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    pages = set(_pages(ws))
    with connect_pages_db(ws) as conn:
        zr.enqueue(conn, [{"id": keys["pdf"], "kind": "top", "version": 10_000}])
        conn.commit()
    calls = len(fake.calls)
    _sync(me, ws)
    assert set(_pages(ws)) == pages
    assert _writes(fake, calls) == []
    assert _rows(ws)[keys["pdf"]]["parent"] == keys["attention"]


# --- Gamma's changes ------------------------------------------------------------------------

def test_a_record_edit_here_goes_to_zotero_field_by_field(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "volume": "648", "pages": "5-9"},
                                                                    "cite_key": "attention2017"}}])
    calls = len(fake.calls)
    status = _sync(me, ws)
    data = fake.data(LIB, keys["attention"])
    assert (data["volume"], data["pages"], data["citationKey"]) == ("648", "5-9", "attention2017")
    assert data["creators"][2] == {"creatorType": "editor", "firstName": "Jane", "lastName": "Editor"}
    assert data["date"] == "2017-06-12"  # a fuller date in Gamma's year stays
    assert {"tag": "attention", "type": 1} in data["tags"]
    assert [m for m, _ in _writes(fake, calls)] == ["POST"] and status["pages_pushed"] == 1
    row = _rows(ws, "item")[keys["attention"]]
    assert row["version"] == data["version"] and row["zbase"] == row["gbase"]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # a kind changed here retypes the item (a PUT with the row's version)
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "kind": "conferencePaper"}}}])
    calls = len(fake.calls)
    _sync(me, ws)
    data = fake.data(LIB, keys["attention"])
    assert data["itemType"] == "conferencePaper" and data["proceedingsTitle"] == "Nature"
    assert [m for m, _ in _writes(fake, calls)] == ["PUT"]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_labels_and_folders_here_become_tags_and_collections_keeping_automatic_tags(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    with connect_pages_db(ws) as conn:
        folders = {name: fid for fid, _, name, _ in tree_rows(conn, FOLDERS)}
        labels = {name: lid for lid, name in label_names(conn).items()}
    _edit(ws, owner_id, LABELS, [{"op": "insert", "id": "ziLabelNew", "parent": LABELS, "content": "must read"}])
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {
        "labels": [labels["transformers"], "ziLabelNew"], "folders": [folders["Reading"]]}}])
    _sync(me, ws)
    data = fake.data(LIB, keys["attention"])
    assert sorted(t["tag"] for t in data["tags"]) == ["attention", "must read", "transformers"]
    assert {"tag": "attention", "type": 1} in data["tags"]
    assert data["collections"] == [keys["reading"]]
    # a label renamed here (one Zotero brought) renames the tag on every item carrying it
    _edit(ws, owner_id, LABELS, [{"op": "set", "id": labels["transformers"], "content": "Transformer models"}])
    _sync(me, ws)
    assert sorted(t["tag"] for t in fake.data(LIB, keys["attention"])["tags"]) == [
        "Transformer models", "attention", "must read"]
    # a label removed here
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"labels": ["ziLabelNew"]}}])
    _sync(me, ws)
    assert sorted(t["tag"] for t in fake.data(LIB, keys["attention"])["tags"]) == ["attention", "must read"]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_new_paper_page_becomes_an_item_with_its_pdf(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        folders = {name: fid for fid, _, name, _ in tree_rows(conn, FOLDERS)}
    doc = _stored_pdf(ws, pdf_bytes("A new paper written here."))
    page = _paper(ws, "ziNewPaper", "Deep residual learning", doc_id=doc, original_filename="resnet.pdf",
                  folders=[folders["ML"]], meta={"doi": "10.1109/CVPR.2016.90", "kind": "conferencePaper"})
    status = _sync(me, ws)
    key = object_key(ws, LIB, "item", "", page, "")
    data = fake.data(LIB, key)
    assert data["itemType"] == "conferencePaper" and data["title"] == "Deep residual learning"
    assert data["creators"] == [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}]
    assert data["collections"] == [keys["ml"]] and data["DOI"] == "10.1109/CVPR.2016.90"
    assert _block(ws, page)["props"]["zotero_key"] == item_uri(LIB, key)
    [att] = [d for d in fake.items(LIB) if d.get("parentItem") == key]
    assert att["linkMode"] == "imported_file" and att["filename"] == "resnet.pdf"
    assert att["md5"] == file_md5(storage.open_path(ws, f"{doc}.pdf"))
    row = _rows(ws, "attachment")[att["key"]]
    assert row["page_id"] == page and row["version"] == fake.data(LIB, att["key"])["version"]  # the register's
    assert row["detail"]["md5"] == row["detail"]["gmd5"] == att["md5"]
    assert status["pages_pushed"] == 1 and status["files_pushed"] == 1
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and len([d for d in fake.items(LIB) if d.get("parentItem") == key]) == 1
    # not downloaded back
    assert not any(m == "GET" and path.endswith(f"/items/{att['key']}/file") for m, path, _ in fake.calls)


def test_new_paper_pages_go_to_zotero_fifty_a_request(me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    for n in range(60):
        _paper(ws, f"zfxMany{n:02d}", f"Batch paper {n}", meta={"title": f"Batch paper {n}", "year": "2020",
                                                                "kind": "journalArticle"})
    for n in range(4):
        _paper(ws, f"zfxManyPdf{n}", f"Batch paper with a PDF {n}", doc_id=_stored_pdf(ws, pdf_bytes(f"Batch {n}")),
               meta={"title": f"Batch paper with a PDF {n}", "year": "2021", "kind": "journalArticle"})
    calls = len(fake.calls)
    _sync(me, ws)
    posts = [p for m, p in _writes(fake, calls) if m == "POST" and p == f"/{LIB}/items"]
    assert len(posts) <= 3, posts  # 64 items in two, their 4 attachments in one (one by one: 68)
    made_here = {d["title"] for d in fake.items(LIB) if str(d.get("title") or "").startswith("Batch paper")}
    assert len(made_here) == 64
    pdfs = [d for d in fake.items(LIB) if d.get("itemType") == "attachment" and d.get("md5") and d.get("parentItem")
            and fake.data(LIB, d["parentItem"]).get("title", "").startswith("Batch paper with a PDF")]
    assert len(pdfs) == 4
    for n in range(60):
        assert _block(ws, f"zfxMany{n:02d}")["props"].get("zotero_key")


@pytest.mark.parametrize("stage", ["journalArticle", "attachment"])
def test_new_pages_whose_batch_meets_zotero_unreachable_go_once_it_answers(me, made, fake, owner_id, monkeypatch,
                                                                         stage):
    from gamma import zotero_web
    ws = _link(me)
    _sync(me, ws)
    real, failed = zotero_web.ZoteroClient.write, []

    def write(self, library, kind, objects):
        if not failed and any(o.get("version") == 0 and o.get("itemType") == stage for o in objects):
            failed.append(1)
            raise zotero_web.ZoteroUnreachable("Zotero could not be reached")
        return real(self, library, kind, objects)

    monkeypatch.setattr(zotero_web.ZoteroClient, "write", write)
    for n in range(3):
        _paper(ws, f"zfxDown{n}", f"Offline paper {n}", doc_id=_stored_pdf(ws, pdf_bytes(f"Offline {n}")),
               meta={"title": f"Offline paper {n}", "year": "2021", "kind": "journalArticle"})
    _sync(me, ws, ok=False)
    assert failed
    _due(ws)
    _sync(me, ws)
    _due(ws)
    _sync(me, ws)
    items = [d for d in fake.items(LIB) if str(d.get("title") or "").startswith("Offline paper")]
    assert len(items) == 3
    for item in items:
        [att] = [d for d in fake.items(LIB) if d.get("parentItem") == item["key"]]
        assert att.get("md5")


def test_a_pdf_without_a_record_waits_for_its_lookup_and_stays_unless_sent_as_a_document(me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    doc = _stored_pdf(ws, pdf_bytes("Some slides."))
    zotero_local.create_page(ws, page_id="ziSlides", title="slides.pdf", props={"doc_id": doc}, actor="zi",
                             client="t")
    calls = len(fake.calls)
    _sync(me, ws)
    entry = _queue(ws)
    assert entry.get("page:ziSlides") == "page" and _writes(fake, calls) == []  # it waits for its lookup
    with connect_pages_db(ws) as conn:  # the lookup came back without a record
        conn.execute("UPDATE zotero_queue SET next_at = '' WHERE id = 'page:ziSlides'")
        conn.commit()
    _edit(ws, owner_id, "ziSlides", [{"op": "set", "id": "ziSlides", "props": {"meta_error": "not found"}}])
    _sync(me, ws)
    assert _writes(fake, calls) == [] and "page:ziSlides" not in _queue(ws)  # kept in Gamma
    me.patch(f"/api/zotero/links/{ws}", json={"send_unrecorded": True})
    _sync(me, ws)
    item = _by_title(fake, "slides.pdf")
    assert item["itemType"] == "document"
    assert [d["filename"] for d in fake.items(LIB) if d.get("parentItem") == item["key"]] == ["slides.pdf"]


def test_a_page_trashed_here_trashes_its_item_and_an_undo_restores_it(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["ppo"])
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id)
    _sync(me, ws)
    assert fake.data(LIB, keys["ppo"])["deleted"] == 1
    assert _rows(ws, "item")[keys["ppo"]]["state"] == "trashed"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    with connect_pages_db(ws) as conn:  # restored from Recently deleted: the item comes out of Zotero's trash
        ops.restore_page(ws, conn, page, actor=owner_id)
    _sync(me, ws)
    assert not fake.data(LIB, keys["ppo"]).get("deleted") and _rows(ws, "item")[keys["ppo"]]["state"] == "live"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # purged here after its trash went: the item stays in Zotero's trash, its row too
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id)
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        ops.delete_page(ws, conn, page, actor=owner_id)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and fake.data(LIB, keys["ppo"])["deleted"] == 1
    assert _rows(ws, "item")[keys["ppo"]]["state"] == "trashed"


# --- a retype, a kind the record only infers ------------------------------------------------------

def test_a_webpage_retyped_in_zotero_to_a_journal_article_settles_with_every_field(me, made, fake):
    key = fake.add_item(LIB, {"itemType": "webpage", "title": "A web article",
                              "creators": [{"creatorType": "author", "firstName": "A", "lastName": "B"}],
                              "websiteTitle": "My site", "date": "2020", "url": "https://example.org/a"})
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[key]["page_id"]
    fake.edit(LIB, key, itemType="journalArticle", publicationTitle="J. Webs", volume="3", issue="2", pages="1-9",
              ISSN="1234-5678", websiteTitle=None, websiteType=None)
    calls = len(fake.calls)
    for _ in range(3):
        _sync(me, ws)
    assert _writes(fake, calls) == []
    data = fake.data(LIB, key)
    assert (data["itemType"], data.get("volume"), data.get("pages"), data.get("ISSN")) == \
        ("journalArticle", "3", "1-9", "1234-5678")
    meta = _block(ws, page)["props"]["meta"]
    assert (meta.get("kind"), meta.get("venue"), meta.get("volume"), meta.get("pages")) == \
        ("journalArticle", "J. Webs", "3", "1-9")
    row = _rows(ws)[key]
    assert row["zotero_type"] == "journalArticle" and row["gbase"]["type"] == "journalArticle"


def test_a_retype_sent_from_here_is_the_rows_type_once_it_lands(me, made, fake, owner_id):
    key = fake.add_item(LIB, {"itemType": "webpage", "title": "A web article", "date": "2020",
                              "creators": [{"creatorType": "author", "firstName": "A", "lastName": "B"}]})
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[key]["page_id"]
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "kind": "book",
                                                                             "publisher": "MIT Press"}}}])
    _sync(me, ws)
    assert fake.data(LIB, key)["itemType"] == "book"
    assert _rows(ws)[key]["zotero_type"] == "book"


def _kindless(me, ws, owner_id, page, **extra) -> str:
    meta = {"title": "A kindless paper", "authors": ["Ann Lee"], "year": "2020", "venue": "", "volume": "5",
            "pages": "1-10", "doi": "", "arxiv_id": "", "publisher": "", "isbn": "", "source": "manual",
            "kind": "paper", **extra}
    zotero_local.create_page(ws, page_id=page, title="A kindless paper", actor=owner_id, client="t",
                             props={"meta": meta})
    _sync(me, ws)
    return next(k for k, v in _rows(ws, "item").items() if v["page_id"] == page)


@pytest.mark.parametrize("edit", [{"extra": "arXiv: 2001.00001"}, {"publisher": "Springer"}])
def test_a_zotero_field_that_changes_what_a_kindless_record_implies_sends_no_retype(me, made, fake, owner_id, edit):
    ws = _link(me)
    _sync(me, ws)
    key = _kindless(me, ws, owner_id, "zfx-kind-a")
    assert fake.data(LIB, key)["itemType"] == "journalArticle"
    fake.edit(LIB, key, **edit)
    calls = len(fake.calls)
    for _ in range(3):
        _sync(me, ws)
    assert not [w for w in _writes(fake, calls) if w[0] == "PUT"]
    data = fake.data(LIB, key)
    assert (data["itemType"], data.get("volume"), data.get("pages")) == ("journalArticle", "5", "1-10")


def test_a_venue_cleared_here_on_a_kindless_record_goes_without_a_retype(me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    page = "zfx-kind-c"
    key = _kindless(me, ws, owner_id, page, venue="Nature", arxiv_id="2001.00001")
    assert fake.data(LIB, key)["itemType"] == "journalArticle"
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "venue": ""}}}])
    calls = len(fake.calls)
    _sync(me, ws)
    assert [m for m, _ in _writes(fake, calls)] == ["POST"]
    data = fake.data(LIB, key)
    assert (data["itemType"], data.get("publicationTitle"), data.get("volume"), data.get("pages")) == \
        ("journalArticle", "", "5", "1-10")


# --- both sides --------------------------------------------------------------------------------

def test_a_field_changed_on_both_sides_keeps_gammas_and_theirs_converges(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "year": "2016", "pages": "1-11"}}}])
    fake.edit(LIB, keys["attention"], date="2019", volume="650")
    _sync(me, ws)
    data = fake.data(LIB, keys["attention"])
    assert data["date"] == "2016" and data["pages"] == "1-11" and data["volume"] == "650"  # each side's own fields
    meta = _block(ws, page)["props"]["meta"]
    assert meta["year"] == "2016" and meta["volume"] == "650"
    [conflict] = me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
    assert conflict["kind"] == "zotero_field" and conflict["block_id"] == page
    assert (conflict["mine"], conflict["theirs"]) == ("Year: 2016", "Year: 2019")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200 and _block(ws, page)["props"]["meta"]["year"] == "2019"
    _sync(me, ws)
    assert fake.data(LIB, keys["attention"])["date"] == "2019"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and me.get(f"/api/zotero/links/{ws}").json()["conflicts_open"] == 0
    # a title both edited in other places merges as text
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {
        **meta, "title": "Attention is all you need, really"}}}])
    fake.edit(LIB, keys["attention"], title="Attention is what you need")
    _sync(me, ws)
    assert fake.data(LIB, keys["attention"])["title"] == "Attention is what you need, really"
    assert _block(ws, page)["props"]["meta"]["title"] == "Attention is what you need, really"


def test_an_edit_beats_a_delete_both_ways(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["deep_learning"])
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "year": "2017"}}}])
    fake.trash(LIB, keys["deep_learning"])
    _sync(me, ws)
    data = fake.data(LIB, keys["deep_learning"])
    assert not data.get("deleted") and data["date"] == "2017"  # back out of Zotero's trash, with the edit
    assert _block(ws, page)["parent"] == "root"
    kinds = [c["kind"] for c in me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]]
    assert kinds == ["zotero_deleted_edited"]
    # the other way: the page deleted here, the item edited in Zotero meanwhile
    page = _page_of(ws, keys["thesis"])
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id)
    fake.edit(LIB, keys["thesis"], title="Bosonic codes")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, page)["parent"] == "root" and _block(ws, page)["props"]["meta"]["title"] == "Bosonic codes"
    assert not fake.data(LIB, keys["thesis"]).get("deleted") and _writes(fake, calls) == []


def test_receive_only_keeps_gammas_record_edits_until_two_way(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"})
    page = _page_of(ws, keys["ppo"])
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "title": "PPO, edited here"}}}])
    fake.edit(LIB, keys["ppo"], date="2018")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and _block(ws, page)["props"]["meta"]["year"] == "2018"
    assert _block(ws, page)["props"]["meta"]["title"] == "PPO, edited here"
    me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"})
    _sync(me, ws)
    data = fake.data(LIB, keys["ppo"])
    assert data["title"] == "PPO, edited here" and data["date"] == "2018"  # Gamma's title went, Zotero's date kept


def test_a_write_zotero_refuses_as_stale_is_decided_again_and_gammas_edit_still_goes(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "title": "Mine"}}}])
    # a Zotero user saves the item between Gamma's read and its write: the write gets 412
    fake.on_request(f"/{LIB}/items", lambda: fake.edit(LIB, keys["thesis"], date="2021"), method="POST")
    calls = len(fake.calls)
    _sync(me, ws)
    assert [m for m, _ in _writes(fake, calls)] == ["POST"]
    assert fake.data(LIB, keys["thesis"])["title"] != "Mine"  # refused as stale: nothing written
    _sync(me, ws)
    data = fake.data(LIB, keys["thesis"])
    assert data["title"] == "Mine" and data["date"] == "2021"
    assert _block(ws, page)["props"]["meta"]["year"] == "2021"


def test_an_item_create_whose_answer_was_lost_is_confirmed_and_never_made_twice(me, made, fake, owner_id,
                                                                                monkeypatch):
    ws = _link(me)
    _sync(me, ws)
    page = _paper(ws, "ziLost", "Lost in transit")
    key = object_key(ws, LIB, "item", "", page, "")
    real = zr.Writes.flush

    def lost(self, **kw):  # the create lands in Zotero; its answer never comes back
        real(self, **kw)
        raise ZoteroUnreachable("Zotero could not be reached: the connection dropped")

    monkeypatch.setattr(zr.Writes, "flush", lost)
    status = _sync(me, ws, ok=False)
    monkeypatch.setattr(zr.Writes, "flush", real)
    assert status["error_kind"] == "offline" and _rows(ws, "item")[key]["state"] == "pending"
    assert fake.data(LIB, key)["title"] == "Lost in transit"
    _sync(me, ws)
    row = _rows(ws, "item")[key]
    assert row["state"] == "live" and row["version"] == fake.data(LIB, key)["version"]
    assert len([d for d in fake.items(LIB) if d.get("title") == "Lost in transit"]) == 1
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def _answer_lost_once(monkeypatch, item_type, status=500):
    from gamma import zotero_web
    real = zotero_web.ZoteroClient.write
    state = {"lost": False}

    def write(self, library, kind, objects):
        out = real(self, library, kind, objects)
        if not state["lost"] and any(o.get("version") == 0 and o.get("itemType") == item_type for o in objects):
            state["lost"] = True
            raise zotero_web.ZoteroError("no answer", status)  # saved, but the answer never came
        return out

    monkeypatch.setattr(zotero_web.ZoteroClient, "write", write)


LOST = {"title": "A lost answer", "authors": ["Ann Lee"], "year": "2020", "venue": "Nature", "volume": "",
        "pages": "", "doi": "", "arxiv_id": "", "publisher": "", "isbn": "", "source": "manual",
        "kind": "journalArticle"}


@pytest.mark.parametrize("status", [None, 504])
def test_a_create_whose_answer_was_lost_takes_zoteros_tags_and_trash_after(me, made, fake, owner_id, monkeypatch,
                                                                          status):
    ws = _link(me)
    _sync(me, ws)
    _answer_lost_once(monkeypatch, "journalArticle", status)
    page = "zfxLost"
    zotero_local.create_page(ws, page_id=page, title="A lost answer", actor=owner_id, client="t", props={"meta": LOST})
    _sync(me, ws, ok=False)
    key = next(k for k, v in _rows(ws, "item").items() if v["page_id"] == page)
    assert _rows(ws)[key]["state"] == "pending"
    fake.edit(LIB, key, tags=[{"tag": "to-read"}, {"tag": "Physics", "type": 1}], volume="12")
    fake.trash(LIB, key)
    sent = len(fake.sent)
    _due(ws)
    _sync(me, ws)
    for _, _, body in fake.sent[sent:]:
        for obj in body if isinstance(body, list) else [body]:
            if obj.get("key") == key:
                assert obj.get("deleted") is not False and "tags" not in obj, obj
    data = fake.data(LIB, key)
    assert data.get("deleted") and {t["tag"] for t in data["tags"]} == {"to-read", "Physics"}
    assert _rows(ws)[key]["state"] == "trashed"
    assert len([d for d in fake.items(LIB) if d.get("title") == "A lost answer"]) == 1


def test_a_create_whose_answer_was_lost_and_zotero_retyped_makes_no_second_item(me, made, fake, owner_id,
                                                                                monkeypatch):
    ws = _link(me)
    _sync(me, ws)
    _answer_lost_once(monkeypatch, "journalArticle", 504)
    page = "zfxLostRetyped"
    zotero_local.create_page(ws, page_id=page, title="A lost answer", actor=owner_id, client="t", props={"meta": LOST})
    _sync(me, ws, ok=False)
    key = next(k for k, v in _rows(ws, "item").items() if v["page_id"] == page)
    from gamma import zotero_fields
    data = fake.data(LIB, key)
    retyped = zotero_fields.retype(data, "preprint")
    fields = {**{k: None for k in data if k not in retyped}, **retyped, "repository": "arXiv"}
    fake.edit(LIB, key, **{k: v for k, v in fields.items() if k not in ("key", "version")})
    _due(ws)
    _sync(me, ws)
    _sync(me, ws)
    assert [d["key"] for d in fake.items(LIB) if d.get("title") == "A lost answer"] == [key]
    assert _rows(ws)[key]["page_id"] == page


def test_a_derived_item_key_zotero_keeps_in_its_trash_is_restored_and_another_kind_gets_a_fresh_key(
        me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    page = _paper(ws, "ziUndone", "Brought back")
    key = object_key(ws, LIB, "item", "", page, "")
    fake.add_item(LIB, {"itemType": "journalArticle", "title": "Old title"}, key=key)  # an earlier life of it
    fake.trash(LIB, key)
    with connect_pages_db(ws) as conn:  # its row forgotten (a backup of before the link restored, say)
        conn.execute("DELETE FROM zotero_objects WHERE key = ?", (key,))
        conn.commit()
    _sync(me, ws)
    data = fake.data(LIB, key)
    assert not data.get("deleted") and data["title"] == "Brought back"
    assert _rows(ws, "item")[key]["state"] == "live"
    # Zotero holds another kind of object under the key: Gamma's gets a fresh key
    other = _paper(ws, "ziClash", "Clashing", meta={"kind": "book"})
    clash = object_key(ws, LIB, "item", "", other, "")
    fake.add_item(LIB, {"itemType": "note", "note": "<p>someone's note</p>"}, key=clash)
    _sync(me, ws)
    _sync(me, ws)
    [(mine, row)] = [(k, r) for k, r in _rows(ws, "item").items() if r["page_id"] == other]
    assert mine != clash and row["state"] == "live" and fake.data(LIB, mine)["itemType"] == "book"
    assert fake.data(LIB, clash)["itemType"] == "note"


def test_a_create_zotero_refuses_is_sent_again_only_once_the_page_changed(me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    page = _paper(ws, "ziRefused", "Refused")
    fake.fail_next(400, f"/{LIB}/items", method="POST")
    _sync(me, ws)
    key = object_key(ws, LIB, "item", "", page, "")
    row = _rows(ws, "item")[key]
    assert row["state"] == "rejected"
    kinds = [c["kind"] for c in me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]]
    assert kinds == ["zotero_rejected"]
    _edit(ws, owner_id, LABELS, [{"op": "insert", "id": "ziTouch", "parent": LABELS, "content": "touch"}])
    calls = len(fake.calls)
    with connect_pages_db(ws) as conn:
        conn.execute("INSERT OR REPLACE INTO zotero_queue (id, kind) VALUES (?, 'page')", (f"page:{page}",))
        conn.commit()
    _sync(me, ws)
    assert _writes(fake, calls) == []  # the same page: not sent again
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "content": "Fixed", "props": {"meta": {**meta,
                                                                                               "title": "Fixed"}}}])
    _sync(me, ws)
    assert fake.data(LIB, key)["title"] == "Fixed" and _rows(ws, "item")[key]["state"] == "live"


# --- a field conflict resolved --------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["two-way", "pull"])
def test_a_receive_only_links_field_conflicts_say_gammas_side_stays_here(me, made, fake, owner_id, mode):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    if mode == "pull":
        assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 200
    page, pdf = _rows(ws)[keys["attention"]]["page_id"], _rows(ws)[keys["standalone_pdf"]]["page_id"]
    reading = _paths(ws)[("Reading",)]
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "volume": "31"}}}])
    _edit(ws, owner_id, pdf, [{"op": "set", "id": pdf, "content": "Scanned handout (mine)"}])
    _folder_ops(ws, owner_id, [{"op": "set", "id": reading, "content": "Gamma's name"}])
    fake.edit(LIB, keys["attention"], volume="32")
    fake.edit(LIB, keys["standalone_pdf"], title="Scanned handout (Zotero)")
    fake.edit(LIB, keys["reading"], name="Zotero's name")
    _sync(me, ws)
    said = {c["block_id"]: c["result"] for c in me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
            if c["kind"] == "zotero_field"}
    if mode == "two-way":  # Gamma's went: the card's own sentence says so
        assert said == dict.fromkeys((page, pdf, reading), "")
        assert fake.data(LIB, keys["attention"])["volume"] == "31"
        return
    kept = zotero_sync_rows.KEPT_HERE
    assert said == {page: f"The record changed on both sides: {kept}", pdf: f"The title changed on both sides: {kept}",
                    reading: f"The folder changed on both sides: {kept}"}
    # nothing went: Zotero holds its own until the link sends changes again, and Gamma's goes then
    assert fake.data(LIB, keys["attention"])["volume"] == "32"
    assert fake.data(LIB, keys["standalone_pdf"])["title"] == "Scanned handout (Zotero)"
    assert fake.data(LIB, keys["reading"])["name"] == "Zotero's name"
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    _sync(me, ws)
    assert fake.data(LIB, keys["attention"])["volume"] == "31"
    assert fake.data(LIB, keys["standalone_pdf"])["title"] == "Scanned handout (mine)"
    assert fake.data(LIB, keys["reading"])["name"] == "Gamma's name"


def test_taking_zoteros_title_of_a_standalone_pdf_sets_the_pages_title_and_makes_no_record(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["standalone_pdf"]]["page_id"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "content": "Scanned handout (mine)"}])
    fake.edit(LIB, keys["standalone_pdf"], title="Scanned handout (Zotero)")
    _sync(me, ws)
    [row] = _conflicts(me, ws, "zotero_field")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{row['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    block = _block(ws, page)
    assert block["content"] == "Scanned handout (Zotero)"
    assert "meta" not in block["props"] and "bibtex" not in block["props"]
    _sync(me, ws)
    assert fake.data(LIB, keys["standalone_pdf"])["title"] == "Scanned handout (Zotero)"


def test_taking_zoteros_value_of_a_conflict_zotero_changed_again_since_is_refused(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["ppo"]]["page_id"]
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "year": "2018"}}}])
    fake.edit(LIB, keys["ppo"], date="2019")
    _sync(me, ws)
    [row] = _conflicts(me, ws, "zotero_field")
    fake.edit(LIB, keys["ppo"], date="2020-05-01")  # the user goes on in Zotero: the date is right now
    _sync(me, ws)
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{row['id']}", json={"choice": "theirs"})
    assert r.status_code == 409, r.text
    _sync(me, ws)
    assert fake.data(LIB, keys["ppo"])["date"] == "2020-05-01"
    assert _block(ws, page)["props"]["meta"]["year"] == "2020"


def test_taking_zoteros_value_of_a_fresh_conflict_still_converges(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["ppo"]]["page_id"]
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "year": "2018"}}}])
    fake.edit(LIB, keys["ppo"], date="2019")
    _sync(me, ws)
    [row] = _conflicts(me, ws, "zotero_field")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{row['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    _sync(me, ws)
    assert fake.data(LIB, keys["ppo"])["date"] == "2019" and _block(ws, page)["props"]["meta"]["year"] == "2019"


# --- re-parenting ---------------------------------------------------------------------------

def test_create_parent_item_in_zotero_binds_the_item_to_the_standalone_pdfs_page(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["standalone_pdf"])
    doc = _block(ws, page)["props"]["doc_id"]
    pages = len(_pages(ws))
    # Zotero's Create Parent Item (or Retrieve Metadata): a new item, the PDF moved under it
    parent = fake.add_item(LIB, {"itemType": "report", "title": "Lecture handout",
                                 "creators": [{"creatorType": "author", "firstName": "Pat", "lastName": "Lecturer"}],
                                 "date": "2023", "collections": [keys["ml"]]})
    fake.edit(LIB, keys["standalone_pdf"], parentItem=parent, collections=None)
    calls = len(fake.calls)
    _sync(me, ws)
    assert len(_pages(ws)) == pages  # no second page
    item = _rows(ws, "item")[parent]
    assert item["page_id"] == page and not item["detail"].get("first")
    att = _rows(ws, "attachment")[keys["standalone_pdf"]]
    assert att["parent"] == parent and att["page_id"] == page
    block = _block(ws, page)
    assert block["props"]["meta"]["title"] == "Lecture handout" and block["props"]["meta"]["kind"] == "report"
    assert block["props"]["doc_id"] == doc and block["content"] == "Lecture handout"
    assert _writes(fake, calls) == []
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_standalone_pdf_whose_record_a_person_made_gets_a_parent_item(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["standalone_pdf"])
    # the automatic lookup's record alone does not make one
    auto = {"title": "A guess", "authors": [], "year": "", "venue": "", "volume": "", "pages": "", "doi": "",
            "arxiv_id": "", "publisher": "", "isbn": "", "source": "arxiv"}
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": auto}}], client="meta")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and not fake.data(LIB, keys["standalone_pdf"]).get("parentItem")
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {
        **auto, "title": "Handout on attention", "year": "2022", "source": "manual"}}}])
    _sync(me, ws)
    parent = fake.data(LIB, keys["standalone_pdf"]).get("parentItem")
    assert parent and fake.data(LIB, parent)["date"] == "2022"
    assert fake.data(LIB, parent)["title"] == "Scanned handout"  # a new item is named by its page
    assert fake.data(LIB, parent)["collections"] == [keys["ml"]]  # the PDF's collection, now its item's
    assert _rows(ws, "item")[parent]["page_id"] == page
    assert _rows(ws, "attachment")[keys["standalone_pdf"]]["parent"] == parent
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


MANUAL = {"title": "Handout on attention", "authors": ["Ann Lee"], "year": "2022", "venue": "", "volume": "",
          "pages": "", "doi": "", "arxiv_id": "", "publisher": "", "isbn": "", "source": "manual", "kind": "report"}


def _pdf_attachments(fake) -> list:
    return [d for d in fake.items(LIB) if d.get("itemType") == "attachment"
            and d.get("contentType") == "application/pdf" and not d.get("deleted")]


def _record_made(ws, owner_id, page):
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": MANUAL}}])


def _settles_under_its_item(me, ws, fake, keys, page, pages, pdfs):
    for _ in range(3):
        _due(ws)
        _sync(me, ws, ok=False)
    parent = fake.data(LIB, keys["standalone_pdf"]).get("parentItem")
    assert parent and _rows(ws, "item")[parent]["page_id"] == page
    assert fake.data(LIB, parent)["date"] == "2022"
    assert len(_pdf_attachments(fake)) == pdfs  # no second attachment of the same PDF
    assert [d["key"] for d in _pdf_attachments(fake) if d.get("parentItem") == parent] == [keys["standalone_pdf"]]
    assert set(_pages(ws)) == pages
    assert _rows(ws, "attachment")[keys["standalone_pdf"]]["parent"] == parent
    _due(ws)
    _sync(me, ws)
    return parent


def test_a_standalone_pdf_changed_in_zotero_as_it_gets_a_parent_item_moves_once_with_its_tags(me, made, fake,
                                                                                             owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["standalone_pdf"]]["page_id"]
    pages, pdfs = set(_pages(ws)), len(_pdf_attachments(fake))
    fake.edit(LIB, keys["standalone_pdf"], tags=[{"tag": "scanned"}, {"tag": "OCR-done", "type": 1}])
    _record_made(ws, owner_id, page)  # the move goes at a version Zotero no longer holds: 412
    parent = _settles_under_its_item(me, ws, fake, keys, page, pages, pdfs)
    tags = {t["tag"] for t in fake.data(LIB, keys["standalone_pdf"]).get("tags") or ()}
    assert {"scanned", "OCR-done"} <= tags  # Zotero's Create Parent Item leaves the PDF's tags alone
    assert "scanned" in {t["tag"] for t in fake.data(LIB, parent).get("tags") or ()}


def test_a_standalone_pdfs_move_under_its_new_item_that_does_not_land_is_sent_again(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["standalone_pdf"]]["page_id"]
    pages, pdfs = set(_pages(ws)), len(_pdf_attachments(fake))
    posts = []

    def second_post_fails():  # (a hook runs before the request is answered: the failure is its own)
        posts.append(1)
        if len(posts) == 2:
            fake.fail_next(500, "/users/123/items", method="POST")

    fake.on_request("/users/123/items", second_post_fails, times=2, method="POST")
    _record_made(ws, owner_id, page)
    _sync(me, ws, ok=False)
    assert not fake.data(LIB, keys["standalone_pdf"]).get("parentItem")  # the move did not land
    item = next(k for k, row in _rows(ws, "item").items() if row["page_id"] == page)
    fake.edit(LIB, item, institution="Gamma Lab")  # its item changes in Zotero before the move goes again
    _settles_under_its_item(me, ws, fake, keys, page, pages, pdfs)


def test_a_parent_item_whose_create_failed_is_made_again_for_the_same_pdf(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["standalone_pdf"]]["page_id"]
    pages, pdfs = set(_pages(ws)), len(_pdf_attachments(fake))
    fake.edit(LIB, keys["standalone_pdf"], tags=[{"tag": "scanned"}, {"tag": "OCR-done", "type": 1}])
    _sync(me, ws)
    fake.fail_next(500, "/users/123/items", method="POST")
    _record_made(ws, owner_id, page)
    _sync(me, ws, ok=False)
    _settles_under_its_item(me, ws, fake, keys, page, pages, pdfs)
    tags = {t["tag"] for t in fake.data(LIB, keys["standalone_pdf"]).get("tags") or ()}
    assert {"scanned", "OCR-done"} <= tags


def _page_entry(ws, page) -> tuple | None:
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT next_at, attempts, reason FROM zotero_queue WHERE id = ?",
                            (zr.PAGE_PREFIX + page,)).fetchone()


def test_a_parent_items_create_waiting_out_its_backoff_stays_queued_while_zotero_changes_the_pdf(me, made, fake,
                                                                                                owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["standalone_pdf"]]["page_id"]
    pages, pdfs = set(_pages(ws)), len(_pdf_attachments(fake))
    fake.fail_next(500, "/users/123/items", method="POST")
    _record_made(ws, owner_id, page)
    _sync(me, ws, ok=False)
    waits = _page_entry(ws, page)
    assert waits is not None and waits[0] and waits[1] == 1  # the create did not land: tried again a while later
    fake.edit(LIB, keys["standalone_pdf"], tags=[{"tag": "scanned"}])
    _sync(me, ws)  # the PDF's own unit takes Zotero's change; what only the page's unit does still waits
    assert "scanned" in _labels_of(ws, page)
    assert _page_entry(ws, page) == waits  # at its backoff time, its try counted
    assert not fake.data(LIB, keys["standalone_pdf"]).get("parentItem")
    _settles_under_its_item(me, ws, fake, keys, page, pages, pdfs)


def test_a_parent_item_whose_create_answer_was_lost_takes_the_pdf_it_was_made_for(me, made, fake, owner_id,
                                                                                  monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["standalone_pdf"]]["page_id"]
    pages, pdfs = set(_pages(ws)), len(_pdf_attachments(fake))
    _answer_lost_once(monkeypatch, "report")
    _record_made(ws, owner_id, page)
    _sync(me, ws, ok=False)
    _sync(me, ws, ok=False)  # the listing names the new item first: the page entry waits a while
    _settles_under_its_item(me, ws, fake, keys, page, pages, pdfs)


def test_merge_items_in_zotero_gives_the_kept_page_the_pdf_and_trashes_the_other(me, made, fake, owner_id):
    keys = made["keys"]
    duplicate = fake.add_item(LIB, {"itemType": "preprint", "title": "Proximal Policy Optimization (copy)",
                                    "date": "2017", "archiveID": "arXiv:1707.06347"})
    copy_pdf = fake.add_item(LIB, {"itemType": "attachment", "parentItem": duplicate, "linkMode": "imported_file",
                                   "title": "PDF", "contentType": "application/pdf", "filename": "ppo.pdf"})
    fake.add_file(LIB, copy_pdf, pdf_bytes("PPO, the PDF."))
    ws = _link(me)
    _sync(me, ws)
    kept, gone = _page_of(ws, keys["ppo"]), _page_of(ws, duplicate)
    assert "doc_id" not in _block(ws, kept)["props"] and _block(ws, gone)["props"]["doc_id"]
    # Zotero's Merge Items: the duplicate's PDF moved to the kept item, the duplicate trashed
    fake.edit(LIB, copy_pdf, parentItem=keys["ppo"])
    fake.trash(LIB, duplicate)
    downloads = len([1 for _, path, _ in fake.calls if path.endswith("/file")])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, gone)["parent"] == TRASH
    assert _block(ws, kept)["props"]["doc_id"] == _block(ws, gone)["props"]["doc_id"]  # the same file, not fetched
    assert len([1 for _, path, _ in fake.calls if path.endswith("/file")]) == downloads
    row = _rows(ws, "attachment")[copy_pdf]
    assert row["page_id"] == kept and row["parent"] == keys["ppo"]
    assert _writes(fake, calls) == []
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- the round's own guarantees ------------------------------------------------------------------

def test_a_page_changed_while_its_unit_decided_writes_nothing_on_either_side_and_is_decided_again(
        me, made, fake, owner_id, monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["deep_learning"])
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "publisher": "MIT"}}}])
    fake.edit(LIB, keys["deep_learning"], date="2017")
    decide = zotero_sync_items._Unit.decide
    typed = []

    def meanwhile(self):  # someone types on the page while the unit decides
        decide(self)
        if self.page_id == page and not typed:
            typed.append(1)
            _edit(ws, owner_id, page, [{"op": "set", "id": page, "content": "Deep Learning (typing)"}])

    monkeypatch.setattr(zotero_sync_items._Unit, "decide", meanwhile)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and _block(ws, page)["props"]["meta"]["year"] == "2016"
    assert keys["deep_learning"] in _queue(ws)  # decided again next round
    monkeypatch.setattr(zotero_sync_items._Unit, "decide", decide)
    _sync(me, ws)
    assert fake.data(LIB, keys["deep_learning"])["publisher"] == "MIT"
    assert _block(ws, page)["props"]["meta"]["year"] == "2017"
    assert _block(ws, page)["content"] == "Deep Learning (typing)"


def _set_volume(ws, owner_id, page, volume) -> None:
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "volume": volume}}}])


def test_an_edit_made_between_the_links_read_and_its_write_reaches_zotero(me, made, fake, owner_id, monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["attention"]]["page_id"]
    real, edited = zotero_sync_items._Unit.decide, []

    def decide(self):
        real(self)
        if self.page_id == page and not edited:  # a person's edit after the unit read the page, before it writes
            edited.append(1)
            _set_volume(ws, owner_id, page, "648")

    monkeypatch.setattr(zotero_sync_items._Unit, "decide", decide)
    fake.edit(LIB, keys["attention"], title="Attention Is All You Need")
    _sync(me, ws)
    _sync(me, ws)
    assert edited and fake.data(LIB, keys["attention"])["volume"] == "648"
    assert _block(ws, page)["props"]["meta"]["title"] == "Attention Is All You Need"
    assert _block(ws, page)["props"]["meta"]["volume"] == "648"


def test_an_edit_made_right_after_the_links_write_reaches_zotero(me, made, fake, owner_id, monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["attention"]]["page_id"]
    real, edited = zotero_local.apply, []

    def apply(ws_, page_id, ops_, **kw):
        out = real(ws_, page_id, ops_, **kw)
        if page_id == page and kw.get("rows") is not None and not edited:  # right after the unit's own write
            edited.append(1)
            _set_volume(ws, owner_id, page, "648")
        return out

    monkeypatch.setattr(zotero_local, "apply", apply)
    fake.edit(LIB, keys["attention"], title="Attention Is All You Need")
    _sync(me, ws)
    assert edited and _block(ws, page)["props"]["meta"]["title"] == "Attention Is All You Need"
    _sync(me, ws)
    assert fake.data(LIB, keys["attention"])["volume"] == "648"


def test_a_create_that_never_landed_is_sent_again_and_made_once(me, made, fake, owner_id):
    from gamma.zotero_web import MAX_RETRIES
    ws = _link(me)
    _sync(me, ws)
    page = _paper(ws, "ziNever", "Never landed")
    fake.fail_next(503, f"/{LIB}/items", times=MAX_RETRIES + 1, method="POST")
    status = _sync(me, ws, ok=False)
    key = object_key(ws, LIB, "item", "", page, "")
    assert status["error_kind"] == "busy" and _rows(ws, "item")[key]["state"] == "pending"
    assert f"page:{page}" in _queue(ws)  # the round stopped; the page waits for the next
    _sync(me, ws)
    assert [d["key"] for d in fake.items(LIB) if d.get("title") == "Never landed"] == [key]
    assert _rows(ws, "item")[key]["state"] == "live"


def test_a_library_moving_while_it_is_listed_loses_nothing(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    fake.edit(LIB, keys["ppo"], title="PPO, first edit")
    fake.on_request(f"/{LIB}/items/top?since=", lambda: fake.edit(LIB, keys["thesis"], title="Edited mid-walk"),
                    method="GET")
    _sync(me, ws)
    _sync(me, ws)
    assert _block(ws, _page_of(ws, keys["ppo"]))["props"]["meta"]["title"] == "PPO, first edit"
    assert _block(ws, _page_of(ws, keys["thesis"]))["props"]["meta"]["title"] == "Edited mid-walk"


def test_a_file_changed_in_zotero_brings_back_a_page_deleted_here(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id)
    fake.add_file(LIB, keys["pdf"], pdf_bytes("Attention, a new file in Zotero."))  # a child edited there
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, page)["parent"] == "root" and not fake.data(LIB, keys["attention"]).get("deleted")
    assert file_md5(storage.open_path(ws, f"{_block(ws, page)['props']['doc_id']}.pdf")) == fake.data(
        LIB, keys["pdf"])["md5"]
    assert _writes(fake, calls) == []


# --- the change feed passes by the link's own writes ----------------------------------------------

def _page_units(monkeypatch) -> list:
    """The pages whose units the rounds work (``unit_page``), as they run."""
    seen, real = [], zotero_sync_pages.unit_page

    def unit(r, entry):
        seen.append(entry.id[len(zr.PAGE_PREFIX):])
        return real(r, entry)

    monkeypatch.setattr(zotero_sync_pages, "unit_page", unit)
    return seen


def test_a_page_the_link_just_wrote_gets_no_page_unit_of_its_own(me, made, fake, monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    _sync(me, ws)
    page = _rows(ws)[keys["attention"]]["page_id"]
    fake.edit(LIB, keys["attention"], title="Attention Is All You Need")
    _sync(me, ws)
    assert _block(ws, page)["props"]["meta"]["title"] == "Attention Is All You Need"
    units = _page_units(monkeypatch)
    _sync(me, ws)
    assert page not in units  # its newest change is the link's own, decided whole by its item's unit
    zotero_sync._reset_local_cursor(ws)  # the feed started over (a settings change): every page is looked at
    _sync(me, ws)
    assert page in units


def test_a_label_the_link_made_queues_its_pages_again_only_once_renamed_here(me, made, fake, owner_id, monkeypatch):
    from gamma.blocks_store import LABELS, label_names
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    _sync(me, ws)
    page = _rows(ws)[keys["ppo"]]["page_id"]
    fake.edit(LIB, keys["ppo"], tags=[*fake.data(LIB, keys["ppo"])["tags"], {"tag": "Brand new tag"}])
    _sync(me, ws)  # the label is made here and the page filed under it, in one write of the link's
    with connect_pages_db(ws) as conn:
        [lid] = [lid for lid, name in label_names(conn).items() if name == "Brand new tag"]
    units = _page_units(monkeypatch)
    _sync(me, ws)
    assert page not in units
    _edit(ws, owner_id, LABELS, [{"op": "set", "id": lid, "content": "Renamed here"}])
    _sync(me, ws)
    assert page in units
    assert "Renamed here" in {t["tag"] for t in fake.data(LIB, keys["ppo"])["tags"]}


def test_a_record_confirmed_as_its_standalone_pdfs_unit_writes_the_page_still_gets_its_parent_item(
        me, made, fake, owner_id, monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    _sync(me, ws)
    page = _rows(ws)[keys["standalone_pdf"]]["page_id"]
    real, edited = zotero_sync._list_gamma, []

    def listed(r):  # a person confirms the record once the round has listed Gamma's changes
        real(r)
        if not edited:
            edited.append(1)
            _record_made(ws, owner_id, page)

    monkeypatch.setattr(zotero_sync, "_list_gamma", listed)
    units = _page_units(monkeypatch)
    fake.edit(LIB, keys["standalone_pdf"], title="Scan renamed in Zotero")  # its unit writes the page this round
    _sync(me, ws, ok=False)
    assert edited and _block(ws, page)["content"] == "Scan renamed in Zotero" and page not in units
    assert not fake.data(LIB, keys["standalone_pdf"]).get("parentItem")  # (its item's unit makes no parent)
    for _ in range(2):
        _due(ws)
        _sync(me, ws, ok=False)
    parent = fake.data(LIB, keys["standalone_pdf"]).get("parentItem")
    assert parent and _rows(ws, "item")[parent]["page_id"] == page
