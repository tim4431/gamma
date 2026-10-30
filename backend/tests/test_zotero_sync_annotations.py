"""The Zotero link's annotations (gamma/zotero_sync_blocks.py) against the
fake Zotero (zotero_fake.py): every kind in the first fill once its PDF is
here ("When opened": once fetched, and one Zotero trashes as it waits
only once restored); an incremental pull of each change (a
new annotation, a colour, a comment, an ink stroke) and a push of each; both
sides changed and "theirs" (refused once Zotero changed it again); lossy
conversions never written back (a 2,400-character passage, a ``<sub>``
comment, a turned text annotation, a drawing Zotero only tagged); a held
highlight across a PDF replacement, or without a frame; the undo of a synced
delete; a group's locked annotation, kept on the old PDF when a new primary
is made; Merge Items; drawings by stroke ids; a create whose answer was
lost; force (its side's trash, the side each resolved row took, no sticky
note repainted); a first link of an existing workspace; a workspace backup
restore (sending nothing when restored at once, winning over what Zotero did
since); the RDF import guard and the embedded import's skip (none outside a
link); receive only (its conflicts keep Gamma's here until it sends);
removals for good, also as the page is edited; a web snapshot's annotations
set aside. Rounds run through ``POST …/sync?wait=1`` with the budget lifted;
background rounds are stubbed. Accounts are ``za_…``."""

import io
import json

import pytest
from zotero_engine import (GROUP, LIB, _block, _block_of, _conflicts, _due, _edit, _force, _highlight,  # noqa: F401
                           _kids, _link, _page_of, _pages, _rows, _sent_keys, _sync, _writes, background, fake, idle,
                           made, me, no_relocation, owner_id, quiet)
from zotero_fixtures import CAROL, HIGHLIGHT_RECT, PAGE_SIZE, TITLE_RECT, pdf_bytes

from gamma import (storage, text_box, ws_backup, zotero_local, zotero_sync, zotero_sync_blocks, zotero_sync_pdfs,
                   zotero_sync_rows)
from gamma import zotero_annotations as za
from gamma import zotero_remote as zr
from gamma.blocks_store import write_lock
from gamma.db import connect_pages_db, page_now
from gamma.zotero_web import item_uri, object_key

USER, PASSWORD = "za_owner", "pw-za_owner-123"


def _annotations_of(fake, att) -> dict:
    return {d["key"]: d for d in fake.items(LIB) if d.get("itemType") == "annotation" and d.get("parentItem") == att}


# --- the first fill -----------------------------------------------------------------------

KINDS = ("highlight", "underline", "note_annotation", "image_annotation", "ink_annotation", "text_annotation",
         "spanning")


def test_every_kind_of_annotation_arrives_once_its_pdf_is_here_and_a_round_after_writes_nothing(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    status = _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    rows = _rows(ws, "annotation")
    for name in KINDS:
        row = rows[keys[name]]
        assert row["state"] == "live" and row["page_id"] == page and row["parent"] == keys["pdf"], name
        block = _block(ws, row["block_id"])
        assert block["page"] == page and block["props"]["zotero_annotation"] == keys[name], name
        assert block["props"]["annot_stripped"] is True and block["props"]["imported_annot"], name
        assert row["zbase"]["type"] == fake.data(LIB, keys[name])["annotationType"], name
    hl = _block(ws, rows[keys["highlight"]]["block_id"])
    assert hl["content"] == "great point" and hl["props"]["quote"] == "dominant sequence transduction models"
    assert _block(ws, rows[keys["underline"]]["block_id"])["content"] == "see *Section 3*"
    assert _block(ws, rows[keys["text_annotation"]]["block_id"])["props"]["text_box"]
    ink = _block(ws, rows[keys["ink_annotation"]]["block_id"])
    assert ink["props"]["ink_url"] and rows[keys["ink_annotation"]]["detail"]["stroke_ids"] == [
        f"z{keys['ink_annotation']}-0", f"z{keys['ink_annotation']}-1"]
    assert status["annotations_pulled"] == len(KINDS) and _writes(fake) == []
    for _ in range(2):  # a round after a round writes nothing on either side
        calls = len(fake.calls)
        status = _sync(me, ws)
        assert _writes(fake, calls) == [] and status["annotations_pulled"] == status["annotations_pushed"] == 0
        assert status["blocks_added"] == status["blocks_changed"] == status["blocks_removed"] == 0


def test_when_opened_the_annotations_wait_for_the_pdf_and_come_with_it(me, made, fake):
    keys = made["keys"]
    ws = _link(me, download="open")
    zotero_sync_pdfs.NEWEST_FILES, newest = 0, zotero_sync_pdfs.NEWEST_FILES
    try:
        status = _sync(me, ws)
    finally:
        zotero_sync_pdfs.NEWEST_FILES = newest
    page = _page_of(ws, keys["attention"])
    rows = _rows(ws, "annotation")
    assert {rows[keys[n]]["state"] for n in KINDS} == {"waiting"}
    assert status["waiting"]["annotations"]["n"] >= len(KINDS)
    assert not [bid for bid, _ in _kids(ws, page) if (_block(ws, bid)["props"].get("zotero_annotation"))]
    r = me.post(f"/api/zotero/links/{ws}/fetch", json={"page_id": page})
    assert r.status_code == 200, r.text
    _sync(me, ws)
    rows = _rows(ws, "annotation")
    assert {rows[keys[n]]["state"] for n in KINDS} == {"live"}
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


@pytest.mark.parametrize("restored", [True, False], ids=["restored", "still-trashed"])
def test_a_waiting_annotation_zotero_trashes_stops_waiting_and_comes_with_its_pdf_only_once_restored(
        me, made, fake, restored):
    keys = made["keys"]
    ws = _link(me, download="open")
    zotero_sync_pdfs.NEWEST_FILES, newest = 0, zotero_sync_pdfs.NEWEST_FILES
    try:
        status = _sync(me, ws)
        waiting = status["waiting"]["annotations"]["n"]
        fake.trash(LIB, keys["underline"])
        status = _sync(me, ws)
        row = _rows(ws, "annotation")[keys["underline"]]
        assert row["state"] == "trashed" and status["waiting"]["annotations"]["n"] == waiting - 1
        if restored:
            fake.restore(LIB, keys["underline"])
            _sync(me, ws)
            assert _rows(ws, "annotation")[keys["underline"]]["state"] == "waiting"
    finally:
        zotero_sync_pdfs.NEWEST_FILES = newest
    page = _page_of(ws, keys["attention"])
    assert me.post(f"/api/zotero/links/{ws}/fetch", json={"page_id": page}).status_code == 200
    _sync(me, ws)
    rows = _rows(ws, "annotation")
    assert {rows[keys[n]]["state"] for n in KINDS if n != "underline"} == {"live"}
    if restored:
        assert rows[keys["underline"]]["state"] == "live"
        assert _block(ws, rows[keys["underline"]]["block_id"])["page"] == page
    else:
        assert rows[keys["underline"]]["state"] == "trashed" and not rows[keys["underline"]]["block_id"]
        assert keys["underline"] not in {_block(ws, bid)["props"].get("zotero_annotation") for bid, _ in _kids(ws, page)}


# --- Zotero's changes ------------------------------------------------------------------------

def test_zoteros_annotation_changes_reach_only_their_fields(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    hl = _block_of(ws, keys["highlight"])
    alpha_before = _block(ws, hl)["props"]["color"]
    fake.edit(LIB, keys["highlight"], annotationColor="#ff6666")
    fake.edit(LIB, keys["underline"], annotationComment="see <b>Section 4</b>")
    ink = fake.data(LIB, keys["ink_annotation"])
    position = json.loads(ink["annotationPosition"])
    moved = {**position, "paths": [[v + 5 for v in path] for path in position["paths"]]}
    fake.edit(LIB, keys["ink_annotation"], annotationPosition=json.dumps(moved, separators=(",", ":")))
    new = fake.add_item(LIB, {"itemType": "annotation", "parentItem": keys["pdf"], "annotationType": "highlight",
                              "annotationText": "Attention Is All You Need", "annotationComment": "",
                              "annotationColor": "#5fb236", "annotationPageLabel": "1",
                              "annotationSortIndex": "00000|000000|00060",
                              "annotationPosition": json.dumps({"pageIndex": 0, "rects": [TITLE_RECT]})})
    ink_url = _block(ws, _block_of(ws, keys["ink_annotation"]))["props"]["ink_url"]
    calls = len(fake.calls)
    status = _sync(me, ws)
    after = _block(ws, hl)["props"]
    assert after["color"].startswith("#ff6666") and after["color"][7:] == alpha_before[7:]  # its alpha kept
    assert _block(ws, hl)["content"] == "great point"
    assert _block(ws, _block_of(ws, keys["underline"]))["content"] == "see **Section 4**"
    inked = _block(ws, _block_of(ws, keys["ink_annotation"]))["props"]
    assert inked["ink_url"] != ink_url  # its strokes replaced, in a new file
    assert _rows(ws, "annotation")[new]["state"] == "live"
    assert _block(ws, _block_of(ws, new))["props"]["quote"] == "Attention Is All You Need"
    assert status["annotations_pulled"] == 4 and _writes(fake, calls) == []
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_an_annotation_zotero_trashes_goes_alone_and_a_restore_brings_it_back(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl = _block_of(ws, keys["highlight"])
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaUnder", "parent": hl, "content": "my note under it"}])
    _sync(me, ws)
    fake.trash(LIB, keys["highlight"])
    _sync(me, ws)
    assert _block(ws, hl) is None and _block(ws, "zaUnder")["parent"] == page  # what it held stays, lifted
    assert _rows(ws, "annotation")[keys["highlight"]]["state"] == "trashed"
    fake.restore(LIB, keys["highlight"])
    _sync(me, ws)
    row = _rows(ws, "annotation")[keys["highlight"]]
    assert row["state"] == "live" and _block(ws, row["block_id"])["props"]["zotero_annotation"] == keys["highlight"]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- Gamma's changes ----------------------------------------------------------------------------

def test_a_highlight_made_here_goes_to_zotero_and_its_edits_go_field_by_field(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    made_here = _highlight([TITLE_RECT], quote="Attention Is All You Need", content="the title")
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaMine", "parent": page, **made_here}])
    status = _sync(me, ws)
    key = object_key(ws, LIB, "annotation", keys["pdf"], "zaMine", "")
    data = fake.data(LIB, key)
    assert data["annotationType"] == "highlight" and data["parentItem"] == keys["pdf"]
    assert data["annotationText"] == "Attention Is All You Need" and data["annotationComment"] == "the title"
    assert data["annotationColor"] == "#2ea8e5" and data["annotationPageLabel"] == "1"
    assert data["annotationSortIndex"].startswith("00000|")
    assert _rows(ws, "annotation")[key]["state"] == "live" and status["annotations_pushed"] == 1
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # its colour changed here: only the colour goes
    label_before = fake.data(LIB, key)["annotationPageLabel"]
    _edit(ws, owner_id, page, [{"op": "set", "id": "zaMine", "props": {"color": "#ff6666"}}])
    calls, bodies = len(fake.calls), len(fake.sent)
    _sync(me, ws)
    [(method, _)] = _writes(fake, calls)
    [sent] = _bodies(fake, bodies)
    assert method == "POST" and set(sent) == {"key", "version", "annotationColor"}
    assert fake.data(LIB, key)["annotationPageLabel"] == label_before
    # a note under it: the comment carries it, as the RDF export's popup does
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaMineNote", "parent": "zaMine", "content": "and more"}])
    _sync(me, ws)
    assert fake.data(LIB, key)["annotationComment"] == "the title\n- and more"
    # deleted here: trashed in Zotero, never deleted for good
    _edit(ws, owner_id, page, [{"op": "delete", "id": "zaMine"}])
    _sync(me, ws)
    assert fake.data(LIB, key).get("deleted") and _rows(ws, "annotation")[key]["state"] == "trashed"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def _bodies(fake, since=0) -> list:
    """The objects the writes sent since ``since`` sent bodies (``fake.sent``)."""
    out = []
    for _, _, body in fake.sent[since:]:
        out += body if isinstance(body, list) else [body]
    return out


def test_an_undo_of_a_synced_delete_restores_the_annotation_in_zotero(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl = _block_of(ws, keys["highlight"])
    block = _block(ws, hl)
    _edit(ws, owner_id, page, [{"op": "delete", "id": hl}])
    _sync(me, ws)
    assert fake.data(LIB, keys["highlight"]).get("deleted")
    # Ctrl+Z: the same block again, the same id
    _edit(ws, owner_id, page, [{"op": "insert", "id": hl, "parent": page, "content": block["content"],
                                "props": block["props"]}])
    before = len(_annotations_of(fake, keys["pdf"]))
    _sync(me, ws)
    data = fake.data(LIB, keys["highlight"])
    assert not data.get("deleted") and data["annotationComment"] == "great point"
    assert len(_annotations_of(fake, keys["pdf"])) == before  # brought back, never made twice
    assert _rows(ws, "annotation")[keys["highlight"]]["state"] == "live"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- both sides changed ------------------------------------------------------------------------

def test_an_annotation_changed_on_both_sides_keeps_gammas_and_theirs_converges(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl = _block_of(ws, keys["highlight"])
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "props": {"color": "#5fb236"}}])
    fake.edit(LIB, keys["highlight"], annotationColor="#a28ae5")
    _sync(me, ws)
    assert fake.data(LIB, keys["highlight"])["annotationColor"] == "#5fb236"  # Gamma's wins
    [conflict] = _conflicts(me, ws, "zotero_annotation")
    assert conflict["block_id"] == hl and conflict["mine"].startswith("green") and conflict["theirs"].startswith(
        "purple")
    assert "great point" in conflict["mine"]
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    assert _block(ws, hl)["props"]["color"].startswith("#a28ae5")
    _sync(me, ws)  # sent back: both hold Zotero's
    assert fake.data(LIB, keys["highlight"])["annotationColor"] == "#a28ae5"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def _theirs(me, ws, conflict_id):
    return me.post(f"/api/zotero/links/{ws}/conflicts/{conflict_id}", json={"choice": "theirs"})


def _note_conflict(me, ws, fake, owner_id, keys) -> tuple[dict, str]:
    page = _page_of(ws, keys["attention"])
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    _edit(ws, owner_id, page, [{"op": "set", "id": _kids(ws, root)[0][0], "content": "Gamma's reading"}])
    fake.edit(LIB, keys["note"], note=fake.data(LIB, keys["note"])["note"].replace("Self-attention",
                                                                                     "Self-attention, Zotero's"))
    _sync(me, ws)
    [conflict] = _conflicts(me, ws, "zotero_note")
    return conflict, root


def _annotation_conflict(me, ws, fake, owner_id, keys) -> tuple[dict, str]:
    page, hl = _page_of(ws, keys["attention"]), _block_of(ws, keys["highlight"])
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "props": {"color": "#5fb236"}}])
    fake.edit(LIB, keys["highlight"], annotationColor="#a28ae5")
    _sync(me, ws)
    [conflict] = _conflicts(me, ws, "zotero_annotation")
    return conflict, hl


def test_taking_zoteros_note_after_zotero_changed_it_again_is_refused(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    conflict, root = _note_conflict(me, ws, fake, owner_id, keys)
    fake.edit(LIB, keys["note"], note=fake.data(LIB, keys["note"])["note"].replace("Positional encoding",
                                                                                     "Positional encoding, again"))
    _sync(me, ws)  # Zotero's newer note comes here
    assert "Positional encoding, again" in [c for _, c in _kids(ws, root)]
    r = _theirs(me, ws, conflict["id"])
    assert r.status_code == 409, r.text
    _sync(me, ws)
    assert "Positional encoding, again" in fake.data(LIB, keys["note"])["note"]
    assert "Positional encoding, again" in [c for _, c in _kids(ws, root)]
    assert [c["id"] for c in _conflicts(me, ws, "zotero_note")] == [conflict["id"]]  # open still, to keep


def test_taking_zoteros_annotation_after_zotero_changed_it_again_is_refused(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    conflict, hl = _annotation_conflict(me, ws, fake, owner_id, keys)
    fake.edit(LIB, keys["highlight"], annotationColor="#ff6666")
    _sync(me, ws)  # Zotero's newer colour comes here
    assert _block(ws, hl)["props"]["color"].lower().startswith("#ff6666")
    r = _theirs(me, ws, conflict["id"])
    assert r.status_code == 409, r.text
    _sync(me, ws)
    assert fake.data(LIB, keys["highlight"])["annotationColor"] == "#ff6666"
    assert _block(ws, hl)["props"]["color"].lower().startswith("#ff6666")


@pytest.mark.parametrize("kind", ["note", "annotation"])
def test_taking_zoteros_side_of_an_older_conflict_without_zoteros_synced_side_still_converges(
        me, made, fake, owner_id, kind):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    conflict, where = (_note_conflict if kind == "note" else _annotation_conflict)(me, ws, fake, owner_id, keys)
    with connect_pages_db(ws) as conn:  # as a conflict recorded before Zotero's side was kept with it
        base = json.loads(conn.execute("SELECT base FROM sync_conflicts WHERE id = ?", (conflict["id"],)).fetchone()[0])
        assert "zbase" in base
        base.pop("zbase")
        conn.execute("UPDATE sync_conflicts SET base = ? WHERE id = ?", (json.dumps(base), conflict["id"]))
        conn.commit()
    r = _theirs(me, ws, conflict["id"])
    assert r.status_code == 200, r.text
    _sync(me, ws)
    if kind == "note":
        assert "Self-attention, Zotero's" in fake.data(LIB, keys["note"])["note"]
        assert "Gamma's reading" not in fake.data(LIB, keys["note"])["note"]
    else:
        assert fake.data(LIB, keys["highlight"])["annotationColor"] == "#a28ae5"
        assert _block(ws, where)["props"]["color"].lower().startswith("#a28ae5")


# --- what a mapping loses is never written back ------------------------------------------------

def test_what_gamma_reads_lossily_is_never_written_back(me, made, fake, owner_id):
    keys = made["keys"]
    long_text = ("A passage of the paper read over many lines. " * 60)[:2400]
    long = fake.add_item(LIB, {"itemType": "annotation", "parentItem": keys["pdf"], "annotationType": "highlight",
                               "annotationText": long_text, "annotationComment": "H<sub>2</sub>O is water",
                               "annotationColor": "#ffd400", "annotationPageLabel": "1",
                               "annotationSortIndex": "00000|000024|00108",
                               "annotationPosition": json.dumps({"pageIndex": 0, "rects": [HIGHLIGHT_RECT]})})
    turned = fake.add_item(LIB, {"itemType": "annotation", "parentItem": keys["pdf"], "annotationType": "text",
                                 "annotationComment": "Turned", "annotationColor": "#2ea8e5",
                                 "annotationPageLabel": "1", "annotationSortIndex": "00000|000189|00300",
                                 "annotationPosition": json.dumps({"pageIndex": 0, "fontSize": 14, "rotation": 30,
                                                                   "rects": [[350, 400, 500, 422]]})})
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl, box = _block_of(ws, long), _block_of(ws, turned)
    assert _block(ws, hl)["props"]["quote"] == long_text and _block(ws, hl)["content"] == "H2O is water"
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "props": {"color": "#ff6666"}},
                               {"op": "set", "id": box, "content": "Turned, and edited"}])
    _sync(me, ws)
    data = fake.data(LIB, long)
    assert data["annotationColor"] == "#ff6666"
    assert data["annotationText"] == long_text and data["annotationComment"] == "H<sub>2</sub>O is water"
    text = fake.data(LIB, turned)
    assert text["annotationComment"] == "Turned, and edited"
    assert json.loads(text["annotationPosition"])["rotation"] == 30  # the turn Gamma cannot draw stays
    # a drawing Zotero only tags: Gamma's file is left as it is
    ink = _block_of(ws, keys["ink_annotation"])
    url = _block(ws, ink)["props"]["ink_url"]
    fake.edit(LIB, keys["ink_annotation"], tags=[{"tag": "sketch"}])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, ink)["props"]["ink_url"] == url and _writes(fake, calls) == []
    version = fake.data(LIB, keys["ink_annotation"])["version"]
    assert _rows(ws, "annotation")[keys["ink_annotation"]]["version"] == version  # its version alone moved


# --- a held highlight ----------------------------------------------------------------------------

def test_a_highlight_held_across_a_pdf_replacement_sends_nothing_until_it_is_placed(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl = _block_of(ws, keys["highlight"])
    fake.add_file(LIB, keys["pdf"], pdf_bytes() + b"\n% the publisher's version\n")  # Zotero's file changed
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, page)["props"]["doc_id"] == _rows(ws, "attachment")[keys["pdf"]]["detail"]["doc_id"]
    stamped = _block(ws, hl)["props"]["pdf_position"]
    assert stamped.get("doc_id")  # being placed in the new file: held
    assert _rows(ws, "annotation")[keys["highlight"]]["state"] == "held"
    assert not [w for w in _writes(fake, calls)]  # nothing sent, nothing trashed
    status = _sync(me, ws)
    assert status["waiting"]["annotations"]["reasons"].get("being placed in the new PDF")
    assert not any(d.get("deleted") for d in _annotations_of(fake, keys["pdf"]).values())
    # the relocation found it a little lower: released, its new place goes as a PATCH of the position alone
    moved = {k: v for k, v in stamped.items() if k != "doc_id"}
    moved["rects"] = [{**rect, "y1": rect["y1"] + 2, "y2": rect["y2"] + 2} for rect in stamped["rects"]]
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "props": {"pdf_position": moved}}])
    bodies = len(fake.sent)
    _sync(me, ws)
    [body] = _bodies(fake, bodies)
    assert set(body) == {"key", "version", "annotationPosition", "annotationSortIndex"}
    assert _rows(ws, "annotation")[keys["highlight"]]["state"] == "live"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_highlight_without_a_frame_is_held_and_its_zotero_annotation_left_as_it_is(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl = _block_of(ws, keys["highlight"])
    pos = {k: v for k, v in _block(ws, hl)["props"]["pdf_position"].items() if k not in ("width", "height")}
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "props": {"pdf_position": pos}}])
    assert set(_block(ws, hl)["props"]["pdf_position"]) == set(pos)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    row = _rows(ws, "annotation")[keys["highlight"]]
    assert row["state"] == "held" and row["detail"]["reason"] == za.WAIT_NO_PLACE
    assert not fake.data(LIB, keys["highlight"]).get("deleted") and _block(ws, hl) is not None
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- a group's annotations ---------------------------------------------------------------------

def test_another_members_annotation_is_read_only_and_a_delete_here_is_put_back(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me, library=GROUP)
    _sync(me, ws)
    rows = _rows(ws, "annotation")
    carols, mine = rows[keys["carols"]], rows[keys["mine"]]
    assert carols["state"] == "readonly" and mine["state"] == "live"
    block = _block(ws, carols["block_id"])
    assert block["props"]["zotero_readonly"] == "locked" and block["props"]["zotero_author"] == "carol"
    page = carols["page_id"]
    # an edit here stays here: one open conflict row however often it changes
    for color in ("#5fb236", "#2ea8e5"):
        _edit(ws, owner_id, page, [{"op": "set", "id": carols["block_id"], "props": {"color": color}}])
        calls = len(fake.calls)
        _sync(me, ws)
        assert _writes(fake, calls) == []
        assert fake.libraries[GROUP].items[keys["carols"]]["annotationColor"] == "#ff6666"
    [locked] = _conflicts(me, ws, "zotero_locked")
    assert locked["block_id"] == carols["block_id"] and "carol" in locked["result"]
    # deleted here: put back in the same round, Zotero's untouched
    _edit(ws, owner_id, page, [{"op": "delete", "id": carols["block_id"]}])
    calls = len(fake.calls)
    _sync(me, ws)
    row = _rows(ws, "annotation")[keys["carols"]]
    assert row["state"] == "readonly" and _block(ws, row["block_id"])["props"]["zotero_author"] == "carol"
    assert _writes(fake, calls) == [] and not fake.libraries[GROUP].items[keys["carols"]].get("deleted")
    # the user's own annotation in the group goes as usual
    _edit(ws, owner_id, page, [{"op": "set", "id": mine["block_id"], "content": "my comment"}])
    _sync(me, ws)
    assert fake.libraries[GROUP].items[keys["mine"]]["annotationComment"] == "my comment"


def test_another_members_annotation_stays_on_the_old_pdf_when_a_new_primary_is_made(me, made, fake, owner_id):
    from gamma.routers.pages import replace_attachment
    keys = made["keys"]
    ws = _link(me, library=GROUP)
    _sync(me, ws)
    rows = _rows(ws, "annotation")
    page, carols = rows[keys["carols"]]["page_id"], rows[keys["carols"]]["block_id"]
    mine = rows[keys["mine"]]["block_id"]
    doc, _ = storage.store_pdf(ws, pdf_bytes("A corrected copy."))  # carol's keeps the old file: a new primary
    replace_attachment(ws, page, doc, "", "corrected.pdf", actor=owner_id, client="t", owner=owner_id)
    placed = []  # what the relocation does once each highlight is found in the new file
    for bid in (carols, mine):
        pos = dict(_block(ws, bid)["props"]["pdf_position"])
        pos.pop("doc_id", None)
        placed.append({"op": "set", "id": bid, "props": {"pdf_position": pos}})
    _edit(ws, owner_id, page, placed)
    for _ in range(4):
        _due(ws)
        _sync(me, ws)
    atts = [d for d in fake.items(GROUP) if d.get("itemType") == "attachment" and d.get("parentItem") ==
            keys["group_item"] and d["key"] != keys["group_pdf"]]
    assert len(atts) == 1  # the new primary
    under = [d for d in fake.items(GROUP) if d.get("itemType") == "annotation"
             and d.get("parentItem") == atts[0]["key"]]
    assert [d.get("annotationText") for d in under] == [fake.data(GROUP, keys["mine"]).get("annotationText")]
    assert not any("carol" in (d.get("annotationComment") or "") for d in under)
    assert not [r for r in _rows(ws, "annotation").values() if r["block_id"] == carols]
    assert _block(ws, carols)["props"]["zotero_readonly"] == "locked"
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "content": "Group reading, renamed"}])
    bound = next(r for r in _rows(ws, "attachment").values() if r["page_id"] == page)
    r = type("Round", (), {"ws": ws, "library": GROUP, "access": {"notes": True}})()
    assert zotero_sync_blocks.blocks_dirty(r, page, bound["detail"]["md5"], bound) is False  # no new block


# --- Merge Items ------------------------------------------------------------------------------

def test_merge_items_moves_the_annotations_to_the_kept_items_page_before_the_others_trash(me, made, fake):
    keys = made["keys"]
    twin = fake.add_item(LIB, {"itemType": "journalArticle", "title": "Attention is all you need (duplicate)",
                               "date": "2017"})
    twin_pdf = fake.add_item(LIB, {"itemType": "attachment", "parentItem": twin, "linkMode": "imported_file",
                                   "title": "PDF", "contentType": "application/pdf", "filename": "dup.pdf"})
    fake.add_file(LIB, twin_pdf, pdf_bytes())
    moved = fake.add_item(LIB, {"itemType": "annotation", "parentItem": twin_pdf, "annotationType": "highlight",
                                "annotationText": "Attention Is All You Need", "annotationComment": "on the twin",
                                "annotationColor": "#5fb236", "annotationPageLabel": "1",
                                "annotationSortIndex": "00000|000000|00060",
                                "annotationPosition": json.dumps({"pageIndex": 0, "rects": [TITLE_RECT]})})
    twin_note = fake.add_item(LIB, {"itemType": "note", "parentItem": twin, "note": "<p>A note on the twin</p>"})
    ws = _link(me)
    _sync(me, ws)
    kept, gone = _page_of(ws, keys["attention"]), _page_of(ws, twin)
    block, note_root = _block_of(ws, moved), _rows(ws, "note")[twin_note]["block_id"]
    assert _block(ws, block)["page"] == gone
    # Zotero's Merge Items: the twin's annotations onto the kept PDF, its notes onto the kept item, then the trash
    fake.edit(LIB, moved, parentItem=keys["pdf"])
    fake.edit(LIB, twin_note, parentItem=keys["attention"])
    fake.trash(LIB, twin_pdf)
    fake.trash(LIB, twin)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, block)["page"] == kept and _block(ws, note_root)["page"] == kept
    row = _rows(ws, "annotation")[moved]
    assert row["page_id"] == kept and row["parent"] == keys["pdf"] and row["state"] == "live"
    assert gone not in _pages(ws)  # the twin's page went to Recently deleted
    assert not fake.data(LIB, moved).get("deleted") and not fake.data(LIB, twin_note).get("deleted")
    assert [w for w in _writes(fake, calls) if "items" in w[1]] == []
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- drawings ------------------------------------------------------------------------------------

def test_a_drawing_keeps_its_items_by_stroke_and_a_new_look_is_an_item_of_its_own(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    ink_block = _block_of(ws, keys["ink_annotation"])
    props = _block(ws, ink_block)["props"]
    drawn = zotero_local.read_ink(ws, props)
    from gamma import ink as inkmod
    blue = {**inkmod.to_dict(drawn.strokes[0]), "id": "gammaBlue1", "color": "#2ea8e5"}
    added = inkmod.parse_ink({**inkmod.to_dict(drawn), "strokes": [*map(inkmod.to_dict, drawn.strokes), blue]})
    name, _ = storage.store_file(ws, inkmod.dumps(added), ".ink")
    _edit(ws, owner_id, page, [{"op": "set", "id": ink_block, "props": {
        "ink_url": f"/api/uploads/{name}", "ink_strokes": len(added.strokes),
        "pdf_position": inkmod.pdf_position(added)}}])
    calls = len(fake.calls)
    _sync(me, ws)
    rows = [r for r in _rows(ws, "annotation").values() if r["block_id"] == ink_block]
    assert len(rows) == 2  # Zotero's item keeps its strokes; the blue stroke is an item of its own
    new = next(r for r in rows if r["key"] != keys["ink_annotation"])
    assert new["detail"]["stroke_ids"] == ["gammaBlue1"] and fake.data(LIB, new["key"])["annotationColor"] == "#2ea8e5"
    assert fake.data(LIB, keys["ink_annotation"])["version"] < fake.version(LIB)  # untouched: a create alone
    assert [m for m, _ in _writes(fake, calls)] == ["POST"]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # Zotero recolours its item: exactly its strokes change here
    fake.edit(LIB, keys["ink_annotation"], annotationColor="#a28ae5")
    _sync(me, ws)
    strokes = zotero_local.read_ink(ws, _block(ws, ink_block)["props"]).strokes
    assert {s.id: s.color for s in strokes}["gammaBlue1"] == "#2ea8e5"
    assert {s.color for s in strokes if s.id != "gammaBlue1"} == {"#a28ae5"}
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- a create whose answer was lost ------------------------------------------------------------------

def test_an_annotation_create_whose_answer_was_lost_is_confirmed_and_never_made_twice(me, made, fake, owner_id,
                                                                                     monkeypatch):
    from gamma import zotero_remote as zr
    from gamma.zotero_web import ZoteroUnreachable
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaLost", "parent": page,
                                **_highlight([TITLE_RECT], quote="Attention Is All You Need")}])
    real = zr.Writes.flush

    def lost(self, **kw):  # the create lands in Zotero; its answer never comes back
        real(self, **kw)
        raise ZoteroUnreachable("Zotero could not be reached: the connection dropped")

    monkeypatch.setattr(zr.Writes, "flush", lost)
    status = _sync(me, ws, ok=False)
    monkeypatch.setattr(zr.Writes, "flush", real)
    key = object_key(ws, LIB, "annotation", keys["pdf"], "zaLost", "")
    assert status["error_kind"] == "offline" and _rows(ws, "annotation")[key]["state"] == "pending"
    _sync(me, ws)
    row = _rows(ws, "annotation")[key]
    assert row["state"] == "live" and row["version"] == fake.data(LIB, key)["version"]
    assert len([d for d in _annotations_of(fake, keys["pdf"]).values() if d.get("annotationText") ==
                "Attention Is All You Need"]) == 1
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- force ---------------------------------------------------------------------------------------

def test_force_pull_and_push_take_one_sides_annotations_and_delete_nothing(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl, ul = _block_of(ws, keys["highlight"]), _block_of(ws, keys["underline"])
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "content": "Gamma's comment"},
                               {"op": "delete", "id": ul}])
    me.post(f"/api/zotero/links/{ws}/detach")
    fake.edit(LIB, keys["highlight"], annotationComment="Zotero's comment")
    me.post(f"/api/zotero/links/{ws}/relink", json={})
    me.post(f"/api/zotero/links/{ws}/force", json={"direction": "pull"})
    _sync(me, ws)
    assert _block(ws, hl)["content"] == "Zotero's comment"
    row = _rows(ws, "annotation")[keys["underline"]]
    assert row["state"] == "live" and _block(ws, row["block_id"])  # nothing deleted: it came back here
    assert not fake.data(LIB, keys["underline"]).get("deleted")
    decided = [c for c in me.get(f"/api/zotero/links/{ws}/conflicts?resolved=1").json()["conflicts"]
               if c["kind"] == "zotero_annotation" and c["resolved"]]
    assert decided  # what the force replaced, kept for the record
    log = me.get(f"/api/zotero/links/{ws}/log").json()["changes"]
    assert any(e["action"] == "replaced here" and "annotation" in e["title"] for e in log)
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "content": "Gamma's again"}])
    me.post(f"/api/zotero/links/{ws}/detach")
    fake.edit(LIB, keys["highlight"], annotationComment="Zotero's again")
    me.post(f"/api/zotero/links/{ws}/relink", json={})
    me.post(f"/api/zotero/links/{ws}/force", json={"direction": "push"})
    _sync(me, ws)
    assert fake.data(LIB, keys["highlight"])["annotationComment"] == "Gamma's again"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def _decided(me, ws) -> list:
    return [c for c in me.get(f"/api/zotero/links/{ws}/conflicts?resolved=1").json()["conflicts"]
            if c["resolved"]]


def test_a_force_push_over_an_unchanged_library_repaints_no_sticky_note(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    _sync(me, ws)
    sent = len(fake.sent)
    _force(me, ws, "push")
    assert keys["note_annotation"] not in _sent_keys(fake, sent)
    assert fake.data(LIB, keys["note_annotation"])["annotationColor"] == "#5fb236"
    assert not [c for c in _decided(me, ws) if c["kind"] == "zotero_annotation"]
    assert not _rows(ws, "annotation")[keys["note_annotation"]]["detail"].get("first")


def _bodies_by_key(fake, since=0) -> dict:
    """``{key: body}`` of the objects the writes since ``since`` sent by key."""
    out = {}
    for _, _, body in fake.sent[since:]:
        for obj in body if isinstance(body, list) else [body]:
            if isinstance(obj, dict) and obj.get("key"):
                out[obj["key"]] = obj
    return out


def test_a_force_pull_takes_zoteros_trash_of_a_note_and_an_annotation_and_writes_nothing_there(
        me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    root, hl = _rows(ws, "note")[keys["note"]]["block_id"], _block_of(ws, keys["highlight"])
    lines = [bid for bid, _ in _kids(ws, root)]
    fake.edit(LIB, keys["note"], note=fake.data(LIB, keys["note"])["note"].replace("twice", "twice, ZOTERO EDIT"))
    fake.edit(LIB, keys["highlight"], annotationComment="Zotero's newer comment")
    fake.trash(LIB, keys["note"])
    fake.trash(LIB, keys["highlight"])
    calls = len(fake.calls)
    _force(me, ws, "pull")
    _sync(me, ws)
    assert _writes(fake, calls) == []
    assert fake.data(LIB, keys["note"]).get("deleted") and fake.data(LIB, keys["highlight"]).get("deleted")
    assert "ZOTERO EDIT" in fake.data(LIB, keys["note"])["note"]
    assert fake.data(LIB, keys["highlight"])["annotationComment"] == "Zotero's newer comment"
    assert _block(ws, root) is None and _block(ws, hl) is None and all(_block(ws, b) is None for b in lines)
    note, ann = _rows(ws, "note")[keys["note"]], _rows(ws, "annotation")[keys["highlight"]]
    assert note["state"] == ann["state"] == "trashed"
    assert not ({"first", "adopt"} & (set(note["detail"]) | set(ann["detail"])))
    # what Gamma held of them, which Zotero's trashed copies do not, is kept for the record
    [kept] = [c for c in _decided(me, ws) if c["kind"] == "zotero_note" and c["block_id"] == root]
    assert "twice" in kept["mine"] and "ZOTERO EDIT" not in kept["mine"] and "ZOTERO EDIT" in kept["theirs"]
    assert kept["result"] == TAKEN["zotero_note"]
    [kept] = [c for c in _decided(me, ws) if c["kind"] == "zotero_annotation" and c["block_id"] == hl]
    assert "great point" in kept["mine"] and "Zotero's newer comment" in kept["theirs"]
    assert kept["result"] == TAKEN["zotero_annotation"]


def test_a_force_push_brings_back_what_zotero_trashed_with_a_note_body_only_where_the_words_differ(
        me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page, hl = _page_of(ws, keys["attention"]), _block_of(ws, keys["highlight"])
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    line = next(bid for bid, text in _kids(ws, root) if "twice" in text)
    image_root = _rows(ws, "note")[keys["image_note"]]["block_id"]
    _edit(ws, owner_id, page, [{"op": "set", "id": line, "content": "Read this twice, then once more."},  # same words
                               {"op": "set", "id": image_root, "content": "The architecture, as drawn:"},
                               {"op": "set", "id": hl, "content": "Gamma's comment"}])
    for name in ("note", "image_note", "highlight"):
        fake.trash(LIB, keys[name])
    sent = len(fake.sent)
    _force(me, ws, "push")
    _sync(me, ws)
    bodies = _bodies_by_key(fake, sent)
    assert not any(fake.data(LIB, keys[name]).get("deleted") for name in ("note", "image_note", "highlight"))
    assert "note" not in bodies[keys["note"]] and "<strong>twice</strong>" in fake.data(LIB, keys["note"])["note"]
    assert "as drawn" in bodies[keys["image_note"]]["note"]
    assert "Gamma's comment" in fake.data(LIB, keys["highlight"])["annotationComment"]


def test_a_force_push_sends_to_zoteros_trash_what_was_deleted_here(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page, hl = _page_of(ws, keys["attention"]), _block_of(ws, keys["highlight"])
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    _edit(ws, owner_id, page, [{"op": "delete", "id": root}, {"op": "delete", "id": hl}])
    _force(me, ws, "push")
    _sync(me, ws)
    assert fake.data(LIB, keys["note"]).get("deleted") and fake.data(LIB, keys["highlight"]).get("deleted")
    assert _block(ws, root) is None and _block(ws, hl) is None
    assert _rows(ws, "note")[keys["note"]]["state"] == _rows(ws, "annotation")[keys["highlight"]]["state"] == "trashed"


TAKEN = {"zotero_note": "The note differed from Zotero's: Zotero's was taken here",
         "zotero_annotation": "The annotation differed from Zotero's: Zotero's was taken here"}


SENT = {"zotero_note": "The note changed on both sides: Gamma's went to Zotero",
        "zotero_annotation": "The annotation changed on both sides: Gamma's went to Zotero"}


@pytest.mark.parametrize("direction", ["pull", "push"])
def test_a_forces_resolved_note_and_annotation_rows_say_which_side_it_took(me, made, fake, owner_id, direction):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page, hl = _page_of(ws, keys["attention"]), _block_of(ws, keys["highlight"])
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    line = next(bid for bid, text in _kids(ws, root) if "twice" in text)
    note_page = _rows(ws, "note")[keys["standalone_note"]]["page_id"]
    [own] = [bid for bid, text in _kids(ws, note_page) if "sparse" in text]
    _edit(ws, owner_id, page, [{"op": "set", "id": line, "content": "Read this once, slowly."},
                               {"op": "set", "id": hl, "content": "Gamma's comment"}])
    _edit(ws, owner_id, note_page, [{"op": "set", "id": own, "content": "Try local attention on long documents."}])
    fake.edit(LIB, keys["note"], note=fake.data(LIB, keys["note"])["note"].replace("twice", "three times"))
    fake.edit(LIB, keys["highlight"], annotationComment="Zotero's comment")
    fake.edit(LIB, keys["standalone_note"], note=fake.data(LIB, keys["standalone_note"])["note"].replace(
        "sparse", "linear"))
    _force(me, ws, direction)
    _sync(me, ws)
    words = TAKEN if direction == "pull" else SENT
    decided = _decided(me, ws)
    for kind, block, ours, theirs in (("zotero_note", root, "slowly", "three times"),
                                      ("zotero_annotation", hl, "Gamma's comment", "Zotero's comment"),
                                      ("zotero_note", note_page, "local", "linear")):
        [row] = [c for c in decided if c["kind"] == kind and c["block_id"] == block]
        assert ours in row["mine"] and theirs in row["theirs"] and row["result"] == words[kind], (kind, block)
    taken = "linear" if direction == "pull" else "local"
    assert any(taken in text for _, text in _kids(ws, note_page)) and taken in fake.data(
        LIB, keys["standalone_note"])["note"]


# --- a first link of an existing workspace -------------------------------------------------------------

def test_a_first_link_pairs_the_imported_annotations_and_notes_and_makes_nothing_twice(me, made, fake, owner_id):
    """A workspace whose paper came from the RDF import: its page names the
    item, its PDF is Zotero's file, its highlights name their Zotero
    annotations (the embedded import's /Zotero:Key) and its note carries the
    note's text."""
    keys = made["keys"]
    from gamma import workspaces
    ws = workspaces.create("Imported", owner_id)["id"]
    doc, _ = storage.store_pdf(ws, pdf_bytes())
    page = "zaImported"
    meta = {"title": "Attention is all you need", "authors": ["Ashish Vaswani", "Noam Shazeer"], "year": "2017",
            "venue": "Nature", "volume": "647", "pages": "1-10", "doi": "10.1038/s41586-000-00000-0",
            "arxiv_id": "", "publisher": "", "isbn": "", "source": "zotero"}
    zotero_local.create_page(ws, page_id=page, title="Attention is all you need", actor=owner_id, client="t",
                             props={"meta": meta, "doc_id": doc, "zotero_key": item_uri(LIB, keys["attention"])})
    with za.Pages(pdf_bytes()) as pages:
        hl = za.from_item(fake.data(LIB, keys["highlight"]), pages.page(1))
    _edit(ws, owner_id, page, [
        {"op": "insert", "id": "zaImpHl", "parent": page, "content": hl["content"],
         "props": {**hl["props"], "annot_stripped": None}},  # still drawn in the stored file ("hide")
        {"op": "insert", "id": "zaImpNote", "parent": page, "content": "# Key points",
         "props": {"zotero_note": "#item_77"}},
        {"op": "insert", "id": "zaImpN1", "parent": "zaImpNote", "content": "Read this **twice**, then *once more*."},
        {"op": "insert", "id": "zaImpN2", "parent": "zaImpNote", "content": "Self-attention"},
        {"op": "insert", "id": "zaImpN3", "parent": "zaImpNote", "content": "Positional encoding"}])
    r = me.post("/api/zotero/links", json={"library": LIB, "workspace_id": ws, "adopt": "theirs"})
    assert r.status_code == 201, r.text
    _sync(me, ws)
    assert _rows(ws, "item")[keys["attention"]]["page_id"] == page
    hl_row = _rows(ws, "annotation")[keys["highlight"]]
    assert hl_row["block_id"] == "zaImpHl" and hl_row["page_id"] == page
    assert hl_row["state"] == "held" and hl_row["detail"]["reason"] == "still drawn in the PDF"  # kept out of writes
    assert _rows(ws, "note")[keys["note"]]["block_id"] == "zaImpNote"
    on_page = [bid for bid, _ in _kids(ws, page)]
    assert sum(1 for bid in on_page if (_block(ws, bid)["props"].get("zotero_annotation") == keys["highlight"])) == 1
    assert sum(1 for _, c in _kids(ws, page) if c == "# Key points") == 1  # the note once
    texts = [d.get("annotationText") for d in _annotations_of(fake, keys["pdf"]).values()]
    assert len(texts) == 7  # nothing new in Zotero either
    assert not [d for d in fake.items(LIB) if d.get("itemType") == "note" and "Notes from Gamma" in d.get("note", "")]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_first_link_adopting_gammas_repaints_no_sticky_note(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    with connect_pages_db(ws) as conn:  # the link's rows gone: its pages are matched again, Gamma's adopted
        for table in ("zotero_objects", "zotero_queue", "zotero_state"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("INSERT INTO zotero_state (id, restored_at) VALUES (1, '2026-01-01 00:00:00')")
        conn.commit()
    sent = len(fake.sent)
    _sync(me, ws)
    assert zotero_sync.get_link(ws)["status"].get("adopt") in (None, "", "mine")
    assert _sent_keys(fake, sent) == set()  # the note page too: its words agree
    assert fake.data(LIB, keys["note_annotation"])["annotationColor"] == "#5fb236"
    assert not [c for c in _decided(me, ws) if c["kind"] == "zotero_annotation"]
    assert 'height="200"' in fake.data(LIB, keys["image_note"])["note"]  # its picture keyed, never sent anew


# --- a workspace backup restored ------------------------------------------------------------------------

def test_a_restored_backup_wins_over_what_zotero_did_since_and_doubles_nothing(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    snapshot = ws_backup.create(ws, label="before", by=owner_id)
    hl = _block_of(ws, keys["highlight"])
    _edit(ws, owner_id, page, [{"op": "delete", "id": hl}])
    _sync(me, ws)
    assert fake.data(LIB, keys["highlight"]).get("deleted")
    items = len(fake.items(LIB))
    ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snapshot["name"]), "replace", by=owner_id)
    _sync(me, ws)
    assert _block(ws, hl) is not None  # the restore stands, never undone by the next pull
    assert not fake.data(LIB, keys["highlight"]).get("deleted")  # and Zotero takes it back
    assert len(fake.items(LIB)) == items and len(_pages(ws)) == 7
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_restore_without_the_links_rows_matches_again_and_doubles_nothing(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    pages, items = len(_pages(ws)), len(fake.items(LIB))
    with connect_pages_db(ws) as conn:  # what restoring a backup from before the link leaves
        for table in ("zotero_objects", "zotero_queue", "zotero_state"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("INSERT INTO zotero_state (id, restored_at) VALUES (1, ?)", (page_now(),))
        conn.commit()
    _sync(me, ws)
    assert len(_pages(ws)) == pages and len(fake.items(LIB)) == items
    assert _rows(ws, "item")[keys["attention"]]["page_id"] == _page_of(ws, keys["attention"])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_backup_restored_at_once_sends_no_note_and_no_annotation(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    _sync(me, ws)
    snapshot = ws_backup.create(ws, label="now", by=owner_id)
    ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snapshot["name"]), "replace", by=owner_id)
    sent = len(fake.sent)
    _sync(me, ws)
    assert _sent_keys(fake, sent) == set()  # the note page too
    assert "height=\"200\"" in fake.data(LIB, keys["image_note"])["note"]
    assert fake.data(LIB, keys["note_annotation"])["annotationColor"] == "#5fb236"
    assert fake.data(LIB, keys["note"])["note"].startswith("<div data-schema-version")
    sent = len(fake.sent)
    _sync(me, ws)
    assert _sent_keys(fake, sent) == set()


def test_a_backup_restored_at_once_writes_nothing(me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    _sync(me, ws)
    snapshot = ws_backup.create(ws, label="now", by=owner_id)
    ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snapshot["name"]), "replace", by=owner_id)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_backup_restored_over_zoteros_later_edits_wins_and_keeps_theirs_open(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    snapshot = ws_backup.create(ws, label="before", by=owner_id)
    html = fake.data(LIB, keys["note"])["note"]
    fake.edit(LIB, keys["note"], note=html.replace("</ul>", "<li>Multi-head</li></ul>"))
    fake.edit(LIB, keys["highlight"], annotationComment="Zotero's later comment")
    fake.edit(LIB, keys["note_annotation"], annotationColor="#ff6666")
    _sync(me, ws)
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    assert "Multi-head" in [c for _, c in _kids(ws, root)]
    ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snapshot["name"]), "replace", by=owner_id)
    sent = len(fake.sent)
    _sync(me, ws)
    assert "Multi-head" not in [c for _, c in _kids(ws, root)]  # the restore stands
    assert "Multi-head" not in fake.data(LIB, keys["note"])["note"]  # and Zotero takes it
    assert fake.data(LIB, keys["highlight"])["annotationComment"] == "great point"
    assert fake.data(LIB, keys["note_annotation"])["annotationColor"] == "#ff6666"  # no place here: Zotero's stays
    assert _sent_keys(fake, sent) == {keys["note"], keys["highlight"]}
    open_ = {c["kind"] for c in _conflicts(me, ws)}
    assert {"zotero_note", "zotero_annotation"} <= open_  # Zotero's values kept for "theirs"
    assert not any(row["detail"].get("restore") for row in _rows(ws).values())
    assert _block(ws, page) is not None


# --- the RDF import on a linked workspace ----------------------------------------------------------------

def test_a_zotero_export_is_not_imported_into_a_linked_workspace(me, made, fake):
    ws = _link(me)
    r = me.post("/api/import/zotero/preview", headers={"X-Gamma-Workspace": ws},
                files={"file": ("lib.zip", io.BytesIO(b"PK"), "application/zip")})
    assert r.status_code == 409 and r.json()["detail"] == (
        "This workspace is kept in step with My Library; its items arrive by themselves.")
    r = me.post("/api/import/review", headers={"X-Gamma-Workspace": ws}, data={"source": "zotero"},
                files={"file": ("lib.zip", io.BytesIO(b"PK"), "application/zip")})
    assert r.status_code == 409


def test_the_embedded_import_leaves_the_annotations_the_link_keeps(me, made, fake, owner_id):
    from PyPDF2 import PdfReader, PdfWriter
    from PyPDF2.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject,
                                TextStringObject)
    from gamma.routers.imports import import_embedded_annotations
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])

    def annot(key, rect, text):
        return DictionaryObject({NameObject("/Type"): NameObject("/Annot"),
                                 NameObject("/Subtype"): NameObject("/Square"),
                                 NameObject("/Rect"): ArrayObject(FloatObject(v) for v in rect),
                                 NameObject("/F"): NumberObject(4), NameObject("/C"): ArrayObject(
                                     FloatObject(v) for v in (0, 0, 1)),
                                 NameObject("/Contents"): TextStringObject(text),
                                 NameObject("/Zotero:Key"): TextStringObject(key)})

    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(pdf_bytes())))
    w.add_annotation(page_number=0, annotation=annot(keys["image_annotation"], (80, 430, 290, 600), "kept by the link"))
    w.add_annotation(page_number=0, annotation=annot("NEWKEY23", (300, 100, 400, 200), "not the link's"))
    out = io.BytesIO()
    w.write(out)
    doc, _ = storage.store_pdf(ws, out.getvalue())
    result = import_embedded_annotations(ws, page, storage.open_path(ws, f"{doc}.pdf"), False, actor=owner_id)
    assert result["imported"] == 1
    contents = [c for _, c in _kids(ws, page)]
    assert "not the link's" in contents and "kept by the link" not in contents


def _pdf_with_keyed_annotation(key: str, text: str) -> bytes:
    import io
    from PyPDF2 import PdfReader, PdfWriter
    from PyPDF2.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
    annot = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Square"),
                              NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (80, 430, 290, 600)),
                              NameObject("/F"): NumberObject(4),
                              NameObject("/C"): ArrayObject(FloatObject(v) for v in (0, 0, 1)),
                              NameObject("/Contents"): TextStringObject(text),
                              NameObject("/Zotero:Key"): TextStringObject(key)})
    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(pdf_bytes())))
    w.add_annotation(page_number=0, annotation=annot)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def test_the_embedded_import_of_a_workspace_without_a_link_ignores_rows_left_behind(owner_id):
    from gamma import workspaces
    from gamma.routers.imports import import_embedded_annotations
    ws = workspaces.create("Restored copy", owner_id)["id"]
    page = "zfRestored"
    zotero_local.create_page(ws, page_id=page, title="A paper", actor=owner_id, client="t", props={})
    with connect_pages_db(ws) as conn:  # a backup of a linked workspace restored here: its rows, no link
        conn.execute("INSERT INTO zotero_objects (key, kind, page_id, block_id, state, version) "
                     "VALUES ('LEFT2345', 'annotation', ?, 'gone', 'live', 3)", (page,))
        conn.commit()
    assert zotero_sync.get_link(ws) is None
    doc, _ = storage.store_pdf(ws, _pdf_with_keyed_annotation("LEFT2345", "from Zotero's export"))
    result = import_embedded_annotations(ws, page, storage.open_path(ws, f"{doc}.pdf"), False, actor=owner_id)
    assert result["imported"] == 1
    assert "from Zotero's export" in [c for _, c in _kids(ws, page)]


# --- receive only, files apart ------------------------------------------------------------------

def test_receive_only_keeps_gammas_annotation_edits_until_two_way(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl = _block_of(ws, keys["highlight"])
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 200
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "props": {"color": "#5fb236"}}])
    fake.edit(LIB, keys["highlight"], annotationComment="Zotero's point")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and _block(ws, hl)["content"] == "Zotero's point"
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    _sync(me, ws)
    data = fake.data(LIB, keys["highlight"])
    assert data["annotationColor"] == "#5fb236" and data["annotationComment"] == "Zotero's point"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


@pytest.mark.parametrize("mode", ["two-way", "pull"])
def test_a_receive_only_links_note_and_annotation_conflicts_say_gammas_side_stays_here(me, made, fake, owner_id,
                                                                                       mode):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    if mode == "pull":
        assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 200
    note, _ = _note_conflict(me, ws, fake, owner_id, keys)
    annotation, _ = _annotation_conflict(me, ws, fake, owner_id, keys)
    if mode == "two-way":
        assert (note["result"], annotation["result"]) == (SENT["zotero_note"], SENT["zotero_annotation"])
        assert "Gamma's reading" in fake.data(LIB, keys["note"])["note"]
        return
    kept = zotero_sync_rows.KEPT_HERE
    assert note["result"] == f"The note changed on both sides: {kept}"
    assert annotation["result"] == f"The annotation changed on both sides: {kept}"
    # nothing went: Zotero holds its own until the link sends changes again, and Gamma's goes then
    assert "Gamma's reading" not in fake.data(LIB, keys["note"])["note"]
    assert fake.data(LIB, keys["highlight"])["annotationColor"] == "#a28ae5"
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    _sync(me, ws)
    assert "Gamma's reading" in fake.data(LIB, keys["note"])["note"]
    assert fake.data(LIB, keys["highlight"])["annotationColor"] == "#5fb236"


def test_annotations_travel_only_while_both_sides_hold_the_same_file(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    assert me.patch(f"/api/zotero/links/{ws}", json={"upload": False}).status_code == 200
    page = _page_of(ws, keys["attention"])
    from gamma.routers.pages import replace_attachment
    doc, _ = storage.store_pdf(ws, pdf_bytes() + b"\n% Gamma's own copy\n")
    replace_attachment(ws, page, doc, "", "mine.pdf", actor=owner_id, owner=owner_id)
    hl = _block_of(ws, keys["highlight"])
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "content": "said here"}])
    fake.edit(LIB, keys["underline"], annotationComment="said there")
    calls = len(fake.calls)
    status = _sync(me, ws)
    assert _writes(fake, calls) == []  # neither file goes, and no annotation either way
    assert _block(ws, _block_of(ws, keys["underline"]))["content"] == "see *Section 3*"
    assert _rows(ws, "annotation")[keys["highlight"]]["state"] == "held"
    assert status["waiting"]["annotations"]["reasons"].get("the PDFs differ")


# --- text boxes, drawings that join and split ------------------------------------------------------------

def test_a_text_box_goes_as_a_text_annotation_with_its_plain_text(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    box = {"x": 60, "y": 500, "w": 160, "h": 24, "auto": False, "size": 12, "color": "#a28ae5", "bg": None}
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaBox", "parent": page, "content": "**Bold** words",
                                "props": {"text_box": box, "pdf_page": 1}}])
    _sync(me, ws)
    key = object_key(ws, LIB, "annotation", keys["pdf"], "zaBox", "")
    data = fake.data(LIB, key)
    assert data["annotationType"] == "text" and data["annotationComment"] == "Bold words"
    assert json.loads(data["annotationPosition"])["fontSize"] == 12
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def _rect_box(data) -> dict:
    """An annotation's rect read as a box on the standard paper (unturned,
    its view box at the origin): x, y from the top-left, w, h."""
    x0, y0, x1, y1 = json.loads(data["annotationPosition"])["rects"][0]
    return {"x": round(x0, 2), "y": round(PAGE_SIZE[1] - y1, 2), "w": round(x1 - x0, 2), "h": round(y1 - y0, 2)}


@pytest.mark.parametrize("box, frame", [
    ({"x": 50, "y": 50, "w": 150, "h": 40, "auto": False, "size": 12}, {"x": 54, "y": 54, "w": 142, "h": 35.84}),
    ({"x": 433.57, "y": 127.85, "w": 75.34, "h": 22.99, "auto": True, "size": 12},
     {"x": 437.57, "y": 131.85, "w": 75.34, "h": 16.79}),
], ids=["fixed", "auto"])
def test_a_gamma_box_goes_to_zotero_as_a_zotero_box_with_its_text_where_it_was(me, made, fake, owner_id, box, frame):
    """A box made before the link (or by an older client) becomes its text
    area as it first goes: the padding off, an auto box keeping its width as
    room for Zotero's face, its lines at Zotero's line height. The block
    holds that box, the item is its rect, and the next round sends nothing."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaGammaBox", "parent": page, "content": "typed in Gamma",
                                "props": {"text_box": box, "pdf_page": 1}}])
    _sync(me, ws)
    key = object_key(ws, LIB, "annotation", keys["pdf"], "zaGammaBox", "")
    stored = _block(ws, "zaGammaBox")["props"]["text_box"]
    assert stored == text_box.zotero_frame(box, *PAGE_SIZE)
    assert {k: stored[k] for k in frame} == frame and stored["zotero"] is True and stored["auto"] == box["auto"]
    assert _rect_box(fake.data(LIB, key)) == frame
    assert _rows(ws, "annotation")[key]["state"] == "live" and not _dirty(ws, page)  # its signature the new box's
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and _block(ws, "zaGammaBox")["props"]["text_box"] == stored


def _drop_key(ws, owner_id, page, block_id) -> dict:
    """The box of ``block_id`` written back without ``zotero``, as a writer
    that knows no Zotero boxes writes it whole: the box as it now stands."""
    box = {k: v for k, v in _block(ws, block_id)["props"]["text_box"].items() if k != "zotero"}
    _edit(ws, owner_id, page, [{"op": "set", "id": block_id, "props": {"text_box": box}}])
    return box


def _stored_without_the_key(ws, page, key, comment=None, content=None) -> dict:
    """``key``'s box stored without ``zotero``, with no change of its page
    (so the local feed lists none), and its row as one synced with that
    block: its signature the block's, its Gamma base's comment ``comment``
    when given, the block's text ``content`` when given. The box as it now
    stands."""
    bid = _block_of(ws, key)
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        conn.execute("UPDATE unified_blocks SET properties = json_remove(properties, '$.text_box.zotero')"
                     + (", content = ?" if content is not None else "") + " WHERE id = ?",
                     ((content,) if content is not None else ()) + (bid,))
        row = zotero_sync_rows.read_rows(conn, "key = ?", (key,))[key]
        view = zotero_local.read_page(conn, page, notes=False)
        ann = next(a for a in view.annotations if a.block["id"] == bid)
        row["gsig"] = za.block_sig(ann.block, ann.nested)
        if comment is not None:
            row["gbase"] = {**row["gbase"], "comment": comment}
        zotero_sync_rows.write_rows(conn, [row])
        conn.commit()
    box = _block(ws, bid)["props"]["text_box"]
    assert "zotero" not in box
    return box


@pytest.mark.parametrize("dropped", [False, True], ids=["stored-without-the-key", "key-dropped"])
def test_a_linked_box_without_the_key_gains_it_with_no_request(me, made, fake, owner_id, dropped):
    """A text annotation's box that lacks the key gains the key alone on the
    next round: one stored so, its page unchanged since, which the local
    feed never lists (``box_checks`` brings it once), and one written back
    by a writer that drops the key. Its geometry stays, its rect being the
    box already, so Zotero hears nothing, and the round after has nothing
    to do."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    key = keys["text_annotation"]
    bid = _block_of(ws, key)
    assert _block(ws, bid)["props"]["text_box"]["zotero"] is True       # pulled as a Zotero box
    rect = _rect_box(fake.data(LIB, key))
    box = _drop_key(ws, owner_id, page, bid) if dropped else _stored_without_the_key(ws, page, key)
    assert _dirty(ws, page)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    after = _block(ws, bid)["props"]["text_box"]
    assert after == {**box, "zotero": True} and {k: after[k] for k in rect} == rect
    assert _rows(ws, "annotation")[key]["state"] == "live" and not _dirty(ws, page)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and _block(ws, bid)["props"]["text_box"] == after


@pytest.mark.parametrize("case", ["pulled", "typed-here", "trashed-in-zotero"])
def test_a_box_stored_without_the_key_with_a_blank_line_gains_it_and_nothing_else(me, made, fake, owner_id, case):
    """A box with no key goes to Zotero with its blank lines dropped, so its
    row's base holds its text without them; with the key, that base reads
    as the text with them. Zotero's text with a blank line pulled into such
    a box sends nothing back, and goes when Zotero trashes it; Gamma's text
    with one, which Zotero holds without it, stays so in Zotero, its rect
    sized for the lines Zotero has."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    key = keys["text_annotation"]
    fake.edit(LIB, key, annotationComment="A text\nbox" if case == "typed-here" else "A text\n\nbox")
    _sync(me, ws)
    bid = _block_of(ws, key)
    _stored_without_the_key(ws, page, key, "A text\nbox", "A text\n\nbox" if case == "typed-here" else None)
    assert _block(ws, bid)["content"] == "A text\n\nbox"
    if case == "trashed-in-zotero":
        fake.trash(LIB, key)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    assert not _conflicts(me, ws, "zotero_deleted_edited")
    if case == "trashed-in-zotero":
        assert fake.data(LIB, key).get("deleted") and _block(ws, bid) is None
        return
    assert _block(ws, bid)["props"]["text_box"]["zotero"] is True
    assert fake.data(LIB, key)["annotationComment"] == ("A text\nbox" if case == "typed-here" else "A text\n\nbox")
    assert _rows(ws, "annotation")[key]["gbase"]["comment"] == "A text\n\nbox" and not _dirty(ws, page)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_gamma_boxs_blank_line_takes_a_line_of_its_zotero_rect(me, made, fake, owner_id):
    """A Gamma box gives a blank line no height, Zotero a line of its own:
    the box as it first goes is a line taller for each, so Zotero shows
    every line of the text it is sent."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    box = {"x": 50, "y": 50, "w": 150, "h": 38, "auto": False, "size": 12}  # two lines as Gamma draws it
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaGap", "parent": page, "content": "typed\n\nin Gamma",
                                "props": {"text_box": box, "pdf_page": 1}}])
    _sync(me, ws)
    key = object_key(ws, LIB, "annotation", keys["pdf"], "zaGap", "")
    frame = text_box.zotero_frame(box, *PAGE_SIZE)
    stored = _block(ws, "zaGap")["props"]["text_box"]
    assert stored == {**frame, "h": round(frame["h"] + 12 * text_box.ZOTERO_LINE, 2)}
    assert stored["h"] == 50.4                                        # three lines of 16.8
    data = fake.data(LIB, key)
    assert data["annotationComment"] == "typed\n\nin Gamma"
    assert _rect_box(data) == {k: stored[k] for k in ("x", "y", "w", "h")}
    assert not _dirty(ws, page)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_box_changed_here_without_the_key_and_trashed_in_zotero_comes_back_as_a_zotero_box(
        me, made, fake, owner_id):
    """Zotero's trash of a box changed here brings it back there: the box,
    written back without the key, gains it in that round, and its row's
    signature is the box's with the key, so the page reads as synced."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    key = keys["text_annotation"]
    bid = _block_of(ws, key)
    box = {k: v for k, v in _block(ws, bid)["props"]["text_box"].items() if k != "zotero"}
    _edit(ws, owner_id, page, [{"op": "set", "id": bid, "content": "kept here", "props": {"text_box": box}}])
    fake.trash(LIB, key)
    calls = len(fake.calls)
    _sync(me, ws)
    assert [m for m, _ in _writes(fake, calls)] == ["POST"]
    data = fake.data(LIB, key)
    assert not data.get("deleted") and data["annotationComment"] == "kept here"
    assert _block(ws, bid)["props"]["text_box"] == {**box, "zotero": True} and not _dirty(ws, page)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_refused_box_written_back_without_the_key_goes_again_as_it_stands(me, made, fake, owner_id):
    """A box Zotero refused to create is a Zotero box already; written back
    without the key, it gains the key alone as it goes again (its text area
    is not taken a second time), and its row's signature is that box's."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaRefused", "parent": page, "content": "refused",
                                "props": {"text_box": {"x": 50, "y": 50, "w": 150, "h": 23, "auto": False,
                                                       "size": 12}, "pdf_page": 1}}])
    fake.fail_next(400, f"/{LIB}/items", method="POST")
    _sync(me, ws)
    key = object_key(ws, LIB, "annotation", keys["pdf"], "zaRefused", "")
    assert _rows(ws, "annotation")[key]["state"] == "rejected"
    framed = _block(ws, "zaRefused")["props"]["text_box"]
    assert framed["zotero"] is True and (framed["x"], framed["w"]) == (54, 142)
    box = {k: v for k, v in framed.items() if k != "zotero"}
    _edit(ws, owner_id, page, [{"op": "set", "id": "zaRefused", "content": "accepted", "props": {"text_box": box}}])
    _sync(me, ws)
    data = fake.data(LIB, key)
    assert data["annotationComment"] == "accepted" and _rect_box(data) == {k: framed[k] for k in ("x", "y", "w", "h")}
    assert _block(ws, "zaRefused")["props"]["text_box"] == framed
    assert _rows(ws, "annotation")[key]["state"] == "live" and not _dirty(ws, page)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_text_merged_from_both_sides_fixes_an_auto_zotero_boxs_width(me, made, fake, owner_id):
    """Both sides' edits merged into an auto Zotero box's text are a text
    change Zotero wraps at its rect's width, as one pulled is."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    key = keys["text_annotation"]
    bid = _block_of(ws, key)
    fake.edit(LIB, key, annotationComment="first line\nsecond line")
    _sync(me, ws)
    box = {**_block(ws, bid)["props"]["text_box"], "auto": True}
    _edit(ws, owner_id, page, [{"op": "set", "id": bid, "props": {"text_box": box}}])
    _sync(me, ws)
    _edit(ws, owner_id, page, [{"op": "set", "id": bid, "content": "First line\nsecond line"}])
    fake.edit(LIB, key, annotationComment="first line\nsecond line, there")
    _sync(me, ws)
    merged = _block(ws, bid)
    assert merged["content"] == "First line\nsecond line, there"
    assert fake.data(LIB, key)["annotationComment"] == "First line\nsecond line, there"
    assert merged["props"]["text_box"] == {**box, "auto": False}
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_box_pulled_narrower_in_zotero_wraps_at_zoteros_width(me, made, fake, owner_id):
    """An auto Zotero box whose width Zotero changed is drawn at that width
    (Zotero has no auto width); one only moved there keeps its width mode."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    key = keys["text_annotation"]
    bid = _block_of(ws, key)
    box = {**_block(ws, bid)["props"]["text_box"], "auto": True}
    _edit(ws, owner_id, page, [{"op": "set", "id": bid, "props": {"text_box": box}}])
    _sync(me, ws)
    pos = json.loads(fake.data(LIB, key)["annotationPosition"])
    x0, y0, x1, y1 = pos["rects"][0]
    fake.edit(LIB, key, annotationPosition=json.dumps({**pos, "rects": [[x0 + 20, y0, x1 + 20, y1]]}))
    _sync(me, ws)
    moved = _block(ws, bid)["props"]["text_box"]
    assert (moved["auto"], moved["w"], moved["x"]) == (True, box["w"], box["x"] + 20)
    fake.edit(LIB, key, annotationPosition=json.dumps({**pos, "rects": [[x0 + 20, y0, x1 - 40, y1]]}))
    calls = len(fake.calls)
    _sync(me, ws)
    narrowed = _block(ws, bid)["props"]["text_box"]
    assert (narrowed["auto"], narrowed["w"], narrowed["zotero"]) == (False, round(x1 - x0 - 60, 2), True)
    assert _writes(fake, calls) == []                                  # Zotero's change, nothing to send back
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def _strokes(*specs):
    """Ink strokes as ``ink.parse_ink`` takes them: ``(id, colour, points)``."""
    return [{"id": sid, "tool": "pen", "color": color, "size": 2.0, "opacity": 1.0, "pen": False, "ch": "xy",
             "pts": pts} for sid, color, pts in specs]


def _drawing(ws, strokes) -> dict:
    from gamma import ink as inkmod
    drawn = inkmod.parse_ink({"format": inkmod.FORMAT, "version": inkmod.VERSION,
                              "space": {"kind": "pdf-page", "page": 1, "width": 612.0, "height": 792.0},
                              "strokes": strokes})
    name, _ = storage.store_file(ws, inkmod.dumps(drawn), ".ink")
    return {"ink_url": f"/api/uploads/{name}", "ink_strokes": len(drawn.strokes),
            "pdf_position": inkmod.pdf_position(drawn), "color": strokes[0]["color"]}


def test_a_stroke_in_a_drawings_look_joins_its_item_and_a_long_drawing_goes_in_pieces(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    ink_block = _block_of(ws, keys["ink_annotation"])
    from gamma import ink as inkmod
    drawn = zotero_local.read_ink(ws, _block(ws, ink_block)["props"])
    same_look = {**inkmod.to_dict(drawn.strokes[0]), "id": "gammaRed1", "pts": [20000, 30000, 100, 100]}
    _edit(ws, owner_id, page, [{"op": "set", "id": ink_block, "props": _drawing(
        ws, [*map(inkmod.to_dict, drawn.strokes), same_look])}])
    calls = len(fake.calls)
    _sync(me, ws)
    rows = [r for r in _rows(ws, "annotation").values() if r["block_id"] == ink_block]
    assert len(rows) == 1 and "gammaRed1" in rows[0]["detail"]["stroke_ids"]  # it joined Zotero's item
    assert len(json.loads(fake.data(LIB, keys["ink_annotation"])["annotationPosition"])["paths"]) == 3
    assert [m for m, _ in _writes(fake, calls)] == ["POST"]
    # a drawing too long for one Zotero item goes as several, each piece its own
    long = [(f"long{n}", "#2ea8e5", [10000 + 1000 * n, 10000] + [10, 5] * 2500) for n in range(3)]
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaLong", "parent": page, "content": "",
                                "props": _drawing(ws, _strokes(*long))}])
    _sync(me, ws)
    pieces = sorted(r["slot"] for r in _rows(ws, "annotation").values() if r["block_id"] == "zaLong")
    assert len(pieces) >= 2 and pieces[0] == "0" and all(p.startswith("0.") for p in pieces[1:])
    sizes = [len(fake.data(LIB, r["key"])["annotationPosition"].encode()) for r in _rows(ws, "annotation").values()
             if r["block_id"] == "zaLong"]
    assert max(sizes) <= 65000
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # Zotero only tags one of Gamma's pieces: nothing changes here
    [first] = [r for r in _rows(ws, "annotation").values() if r["block_id"] == "zaLong" and r["slot"] == "0"]
    url = _block(ws, "zaLong")["props"]["ink_url"]
    fake.edit(LIB, first["key"], tags=[{"tag": "sketch"}])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, "zaLong")["props"]["ink_url"] == url and _writes(fake, calls) == []


# --- a lossy note's "theirs", a first link adopting Gamma's ----------------------------------------------

def test_a_lossy_notes_theirs_takes_zoteros_and_nothing_travels_after(me, made, fake, owner_id):
    keys = made["keys"]
    cited = fake.add_item(LIB, {"itemType": "note", "parentItem": keys["thesis"], "note": (
        '<div data-schema-version="9"><p>As shown <span class="citation" data-citation="%7B%7D">(V, 2017)</span>.'
        '</p><p>A second paragraph.</p></div>')})
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    root = _rows(ws, "note")[cited]["block_id"]
    [(child, _)] = _kids(ws, root)
    _edit(ws, owner_id, page, [{"op": "set", "id": child, "content": "Mine"}])
    _sync(me, ws)
    [conflict] = _conflicts(me, ws, "zotero_lossy")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    assert _block(ws, child)["content"] == "A second paragraph."
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and not _conflicts(me, ws, "zotero_lossy")


def test_a_first_link_adopting_gammas_sends_its_annotation_and_records_the_difference(me, made, fake, owner_id):
    keys = made["keys"]
    from gamma import workspaces
    ws = workspaces.create("Mine first", owner_id)["id"]
    doc, _ = storage.store_pdf(ws, pdf_bytes())
    page = "zaMineFirst"
    zotero_local.create_page(ws, page_id=page, title="Attention is all you need", actor=owner_id, client="t",
                             props={"doc_id": doc, "zotero_key": item_uri(LIB, keys["attention"]), "meta": {
                                 "title": "Attention is all you need", "doi": "10.1038/s41586-000-00000-0",
                                 "authors": [], "year": "2017", "source": "manual"}})
    with za.Pages(pdf_bytes()) as pages:
        hl = za.from_item(fake.data(LIB, keys["highlight"]), pages.page(1))
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaMineHl", "parent": page, "content": "my own reading",
                                "props": hl["props"]}])
    r = me.post("/api/zotero/links", json={"library": LIB, "workspace_id": ws, "adopt": "mine"})
    assert r.status_code == 201, r.text
    _sync(me, ws)
    assert fake.data(LIB, keys["highlight"])["annotationComment"] == "my own reading"
    assert _rows(ws, "annotation")[keys["highlight"]]["block_id"] == "zaMineHl"
    decided = [c for c in me.get(f"/api/zotero/links/{ws}/conflicts?resolved=1").json()["conflicts"]
               if c["kind"] == "zotero_annotation"]
    assert decided and all(c["resolved"] for c in decided)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_drawing_recoloured_here_keeps_its_item_and_an_undo_keeps_it_again(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    ink_block = _block_of(ws, keys["ink_annotation"])
    from gamma import ink as inkmod
    drawn = zotero_local.read_ink(ws, _block(ws, ink_block)["props"])
    count = len(_annotations_of(fake, keys["pdf"]))
    for color in ("#2ea8e5", "#ff6666"):  # recoloured, then back (an undo)
        strokes = [{**inkmod.to_dict(s), "color": color} for s in drawn.strokes]
        _edit(ws, owner_id, page, [{"op": "set", "id": ink_block, "props": _drawing(ws, strokes)}])
        bodies = len(fake.sent)
        _sync(me, ws)
        [body] = _bodies(fake, bodies)
        assert body["key"] == keys["ink_annotation"] and body["annotationColor"] == color
        assert not fake.data(LIB, keys["ink_annotation"]).get("deleted")
    assert len(_annotations_of(fake, keys["pdf"])) == count  # one item all along, never trashed and made again
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_comment_edited_on_both_sides_apart_is_merged(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl = _block_of(ws, keys["highlight"])
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "content": "A great point"}])
    fake.edit(LIB, keys["highlight"], annotationComment="great point, see also section 2")
    _sync(me, ws)
    assert _block(ws, hl)["content"] == "A great point, see also section 2"
    assert fake.data(LIB, keys["highlight"])["annotationComment"] == "A great point, see also section 2"
    assert not _conflicts(me, ws, "zotero_annotation")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_lost_or_dashed_highlight_leaves_its_zotero_annotation_as_it_is(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl, ul = _block_of(ws, keys["highlight"]), _block_of(ws, keys["underline"])
    lost = {**_block(ws, hl)["props"]["pdf_position"], "lost": True}
    dashed = {**_block(ws, ul)["props"]["pdf_position"], "approx": True}
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "props": {"pdf_position": lost}},
                               {"op": "set", "id": ul, "props": {"pdf_position": dashed}}])
    calls = len(fake.calls)
    status = _sync(me, ws)
    assert _writes(fake, calls) == []
    reasons = status["waiting"]["annotations"]["reasons"]
    assert reasons.get("not found in the new PDF") == 1 and reasons.get("dashed in Gamma until accepted") == 1
    # deleting a held one here is a delete all the same: it goes to Zotero's trash
    _edit(ws, owner_id, page, [{"op": "delete", "id": hl}])
    _sync(me, ws)
    assert fake.data(LIB, keys["highlight"]).get("deleted") and not fake.data(LIB, keys["underline"]).get("deleted")


def test_a_block_gamma_cannot_read_as_an_annotation_now_is_held_never_trashed(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    box, ink = _block_of(ws, keys["text_annotation"]), _block_of(ws, keys["ink_annotation"])
    # the text box moved onto a sheet (a box there is the sheet's, no PDF annotation), the drawing's file lost
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaSheet", "parent": page, "content": "",
                                "props": {"sheet": {"w": 600, "h": 800}}},
                               {"op": "move", "id": box, "parent": "zaSheet"},
                               {"op": "set", "id": ink, "props": {"ink_url": "/api/uploads/" + "0" * 24 + ".ink"}}])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    rows = _rows(ws, "annotation")
    assert rows[keys["text_annotation"]]["state"] == rows[keys["ink_annotation"]]["state"] == "held"
    assert not fake.data(LIB, keys["text_annotation"]).get("deleted")
    assert not fake.data(LIB, keys["ink_annotation"]).get("deleted")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # back on the PDF page: in step again, nothing to send
    _edit(ws, owner_id, page, [{"op": "move", "id": box, "parent": page}])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and _rows(ws, "annotation")[keys["text_annotation"]]["state"] == "live"


def test_a_write_zotero_refuses_is_sent_again_only_once_the_block_changed(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl = _block_of(ws, keys["highlight"])
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "content": "refused"}])
    fake.fail_next(400, f"/{LIB}/items", method="POST")
    _sync(me, ws)
    [refused] = _conflicts(me, ws, "zotero_rejected")
    assert refused["block_id"] == hl and fake.data(LIB, keys["highlight"])["annotationComment"] == "great point"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []  # not sent again as it is
    _edit(ws, owner_id, page, [{"op": "set", "id": hl, "content": "accepted"}])
    _sync(me, ws)
    assert fake.data(LIB, keys["highlight"])["annotationComment"] == "accepted"


def test_annotations_and_notes_removed_for_good_go_unless_changed_here(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    hl, ul = _block_of(ws, keys["highlight"]), _block_of(ws, keys["underline"])
    note_root = _rows(ws, "note")[keys["note"]]["block_id"]
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaKeep", "parent": hl, "content": "my note under it"}])
    _sync(me, ws)
    _edit(ws, owner_id, page, [{"op": "set", "id": ul, "content": "changed here"}])
    for key in (keys["highlight"], keys["underline"], keys["note"]):
        fake.trash(LIB, key)
        fake.delete(LIB, key)  # Zotero's trash emptied
    _sync(me, ws)
    assert _block(ws, hl) is None and _block(ws, "zaKeep")["parent"] == page  # what it held stays
    assert _block(ws, note_root) is None
    assert keys["highlight"] not in _rows(ws) and keys["note"] not in _rows(ws)
    # the underline was changed here: it stays and goes to Zotero again as a new annotation
    assert _block(ws, ul)["content"] == "changed here"
    [again] = [r for r in _rows(ws, "annotation").values() if r["block_id"] == ul]
    assert again["key"] != keys["underline"] and again["state"] == "live"
    assert fake.data(LIB, again["key"])["annotationComment"] == "changed here"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


@pytest.mark.parametrize("where", ["it", "elsewhere"])
@pytest.mark.parametrize("name", ["highlight", "note"])
def test_a_removal_for_good_written_as_the_page_is_edited_deletes_nothing_edited(
        me, made, fake, owner_id, monkeypatch, quiet, name, where):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page, hl = _page_of(ws, keys["attention"]), _block_of(ws, keys["highlight"])
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    line = next(bid for bid, text in _kids(ws, root) if "twice" in text)
    block, anchor, other = (hl, hl, line) if name == "highlight" else (line, root, hl)
    edited = block if where == "it" else other
    typed = "typed as Zotero removed it"
    real, armed = zotero_local.apply, [True]

    def apply(ws_, page_id, ops, **kw):
        if armed and page_id == page:  # the person's edit lands between the removal's read and its write
            armed.clear()
            _edit(ws, owner_id, page, [{"op": "set", "id": edited, "content": typed}])
        return real(ws_, page_id, ops, **kw)

    monkeypatch.setattr(zotero_local, "apply", apply)
    fake.delete(LIB, keys[name])
    _sync(me, ws)
    assert not armed
    monkeypatch.setattr(zotero_local, "apply", real)
    assert _block(ws, edited)["content"] == typed
    _sync(me, ws)
    kind = "annotation" if name == "highlight" else "note"
    assert keys[name] not in _rows(ws, kind)
    again = [row for row in _rows(ws, kind).values() if row["block_id"] == anchor]
    if where == "it":  # it stays, and goes to Zotero again as a new object
        [row] = again
        assert row["state"] == "live" and typed in json.dumps(fake.data(LIB, row["key"]))
    else:  # unchanged here, it goes as Zotero removed it
        assert _block(ws, block) is None and again == []


# --- a web snapshot's annotations -----------------------------------------------------------------

def _queue_ids(ws) -> list:
    with connect_pages_db(ws) as conn:
        return [row[0] for row in conn.execute("SELECT id FROM zotero_queue")]


def test_annotations_on_a_web_snapshot_are_set_aside_without_their_items_unit(me, made, fake):
    from zotero_fixtures import _annotation, _position
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    web = [fake.add_item(LIB, _annotation(keys["snapshot"], "highlight", "#ffd400", f"00000|00000{n}|00000",
                                          _position(0, rects=[[1, 1, 9, 9]]), annotationText=f"web text {n}"))
           for n in range(3)]
    calls = len(fake.calls)
    _sync(me, ws)
    reads = [path for method, path, _ in fake.calls[calls:] if method == "GET"]
    assert not [p for p in reads if keys["attention"] in p], reads  # the item is never read whole for them
    assert _writes(fake, calls) == []
    rows = _rows(ws, "annotation")
    for key in web:
        assert rows[key]["state"] == "skipped" and not rows[key]["block_id"] and not rows[key]["page_id"]
        assert rows[key]["version"] == fake.data(LIB, key)["version"] and rows[key]["parent"] == keys["snapshot"]
    assert not [e for e in _queue_ids(ws) if e in web]
    calls = len(fake.calls)
    _sync(me, ws)  # passed by from now on
    assert not [p for m, p, _ in fake.calls[calls:] if m == "GET" and "itemKey" in p]
    fake.edit(LIB, web[0], annotationComment="seen in Zotero")
    calls = len(fake.calls)
    _sync(me, ws)  # a change in Zotero: read once, set aside again at its new version
    reads = [path for method, path, _ in fake.calls[calls:] if method == "GET"]
    assert not [p for p in reads if keys["attention"] in p], reads
    assert _rows(ws, "annotation")[web[0]]["version"] == fake.data(LIB, web[0])["version"]


# --- page labels ------------------------------------------------------------------------------------

LABELLED_TREE = "<</Nums[0<</P<FEFF0031>>>1<</P<FEFF0032>>>]>>"  # hyperref's UTF-16 hex prefixes


def _labelled_paper(fake, library=LIB, tree=LABELLED_TREE) -> tuple[str, str]:
    """A paper whose PDF labels its pages with ``tree``, by default UTF-16
    hex prefixes as hyperref writes them: pdf.js reads "1" and "2", PyMuPDF
    "<FEFF0031>" and "<FEFF0032>". ``(item key, attachment key)``."""
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes(), filetype="pdf")
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels", tree)
    item = fake.add_item(library, {"itemType": "journalArticle", "title": "Labelled pages", "date": "2020"})
    att = fake.add_item(library, {"itemType": "attachment", "parentItem": item, "linkMode": "imported_file",
                                  "title": "Full Text PDF", "contentType": "application/pdf",
                                  "filename": "labelled.pdf"})
    fake.add_file(library, att, doc.tobytes(no_new_id=True))
    return item, att


def _zotero_highlight(fake, att, label, library=LIB) -> str:
    """A highlight made in Zotero on the title of ``att``'s first page,
    labelled ``label``; its key."""
    return fake.add_item(library, {
        "itemType": "annotation", "parentItem": att, "annotationType": "highlight",
        "annotationText": "Attention Is All You Need", "annotationComment": label, "annotationColor": "#ffd400",
        "annotationPageLabel": label, "annotationSortIndex": "00000|000000|00060",
        "annotationPosition": json.dumps({"pageIndex": 0, "rects": [TITLE_RECT]})})


def _highlight_sent_here(me, fake, owner_id, ws, page, att, block_id, library=LIB) -> str:
    """A highlight made here on the title of ``page`` and sent, whose Zotero
    annotation then holds the label as PyMuPDF reads it ("<FEFF0031>"), its
    row settled; its key."""
    made_here = _highlight([TITLE_RECT], quote="Attention Is All You Need", content="the title")
    _edit(ws, owner_id, page, [{"op": "insert", "id": block_id, "parent": page, **made_here}])
    _sync(me, ws)
    key = object_key(ws, library, "annotation", att, block_id, "")
    assert fake.data(library, key)["annotationPageLabel"] == "1"  # the prefix as pdf.js reads it
    assert _rows(ws, "annotation")[key]["detail"]["labels"] == za.LABELS
    fake.edit(library, key, annotationPageLabel="<FEFF0031>")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []  # a settled row: Zotero's label, whatever it is, stays
    return key


def _unsettle(ws, *keys) -> None:
    """The rows of ``keys`` without ``detail.labels``: rows whose page label
    is not settled, as a create that sent PyMuPDF's reading leaves them."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        rows = zotero_sync_rows.read_rows(conn)
        for key in keys:
            rows[key]["detail"].pop("labels", None)
        zotero_sync_rows.write_rows(conn, [rows[key] for key in keys])
        conn.commit()


def _round(ws, library=LIB):
    """What ``blocks_dirty`` and ``label_checks`` read of a round."""
    return type("Round", (), {"ws": ws, "library": library, "access": {"notes": True}})()


def _label_checks(ws, library=LIB) -> set:
    """The pages ``zotero_sync_blocks.label_checks`` gives, queued in its
    write transaction as its caller queues them."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        pages = zotero_sync_blocks.label_checks(_round(ws, library), conn)
        zr.enqueue(conn, [zr.page_entry(page) for page in sorted(pages)])
        conn.commit()
    return pages


def _dirty(ws, page, library=LIB) -> bool:
    bound = next(r for r in _rows(ws, "attachment").values() if r["page_id"] == page)
    return zotero_sync_blocks.blocks_dirty(_round(ws, library), page, bound["detail"]["md5"], bound)


def test_a_page_label_sent_undecoded_is_corrected_once_with_one_write_and_the_rounds_settle(
        me, made, fake, owner_id):
    item, att = _labelled_paper(fake)
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    key = _highlight_sent_here(me, fake, owner_id, ws, page, att, "zaLabelled")
    _unsettle(ws, key)
    assert _dirty(ws, page) is True
    assert _label_checks(ws) == {page} and _label_checks(ws) == set()  # its page, once
    calls, bodies = len(fake.calls), len(fake.sent)
    status = _sync(me, ws)
    [(method, _)] = _writes(fake, calls)
    [sent] = _bodies(fake, bodies)
    assert method == "POST" and set(sent) == {"key", "version", "annotationPageLabel"}
    assert fake.data(LIB, key)["annotationPageLabel"] == "1" and status["annotations_pushed"] == 1
    assert fake.data(LIB, key)["annotationComment"] == "the title"
    assert _rows(ws, "annotation")[key]["detail"]["labels"] == za.LABELS and _dirty(ws, page) is False
    for _ in range(2):
        calls = len(fake.calls)
        _sync(me, ws)
        assert _writes(fake, calls) == []
    assert _label_checks(ws) == set()


def test_a_page_label_sent_undecoded_is_corrected_by_the_next_round_without_an_edit(me, made, fake, owner_id):
    keys = made["keys"]
    item, att = _labelled_paper(fake)
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    key = _highlight_sent_here(me, fake, owner_id, ws, page, att, "zaIdle")
    pulled = tuple(keys[name] for name in ("highlight", "underline", "spanning"))
    _unsettle(ws, key, *pulled)  # without detail.labels: the row of a create that sent PyMuPDF's reading, and Zotero's
    calls, bodies = len(fake.calls), len(fake.sent)
    _sync(me, ws)
    [sent] = _bodies(fake, bodies)
    assert set(sent) == {"key", "version", "annotationPageLabel"} and fake.data(LIB, key)["annotationPageLabel"] == "1"
    assert len(_writes(fake, calls)) == 1
    reads = [path for method, path, _ in fake.calls[calls:] if method == "GET"]
    assert not [p for p in reads if keys["attention"] in p]  # Zotero's own rows: settled, their item not read
    rows = _rows(ws, "annotation")
    assert all(rows[k]["detail"]["labels"] == za.LABELS for k in (key, *pulled))
    for _ in range(2):
        calls = len(fake.calls)
        _sync(me, ws)
        assert _writes(fake, calls) == []


def test_a_page_label_due_goes_with_an_edit_of_the_annotation_here(me, made, fake, owner_id):
    item, att = _labelled_paper(fake)
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    key = _highlight_sent_here(me, fake, owner_id, ws, page, att, "zaLabelledEdit")
    _unsettle(ws, key)
    _edit(ws, owner_id, page, [{"op": "set", "id": "zaLabelledEdit", "content": "the title, again"}])
    bodies = len(fake.sent)
    _sync(me, ws)
    [sent] = _bodies(fake, bodies)
    assert set(sent) == {"key", "version", "annotationComment", "annotationPageLabel"}
    assert fake.data(LIB, key)["annotationPageLabel"] == "1"
    assert fake.data(LIB, key)["annotationComment"] == "the title, again"
    assert _rows(ws, "annotation")[key]["detail"]["labels"] == za.LABELS
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_row_not_settled_whose_label_is_right_is_settled_by_a_change_zotero_made(me, made, fake, owner_id):
    item, att = _labelled_paper(fake)
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    key = _highlight_sent_here(me, fake, owner_id, ws, page, att, "zaLabelledRight")
    fake.edit(LIB, key, annotationPageLabel="1")
    _sync(me, ws)
    _unsettle(ws, key)
    fake.edit(LIB, key, annotationComment="seen in Zotero")
    calls = len(fake.calls)
    _sync(me, ws)  # Zotero's change, merged: nothing to send, its label right
    assert _writes(fake, calls) == []
    row = _rows(ws, "annotation")[key]
    assert row["detail"]["labels"] == za.LABELS and _block(ws, row["block_id"])["content"] == "seen in Zotero"


def test_a_page_label_zotero_gave_its_own_annotation_is_never_sent_over(me, made, fake, owner_id):
    item, att = _labelled_paper(fake)
    theirs = {label: _zotero_highlight(fake, att, label) for label in ("523", "1", "<FEFF0031>")}
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    rows = _rows(ws, "annotation")
    assert all(rows[key]["detail"]["labels"] == za.LABELS for key in theirs.values())  # settled as they came
    _unsettle(ws, *theirs.values())  # rows of Zotero's own annotations without detail.labels
    assert _dirty(ws, page) is False and _label_checks(ws) == set()  # Zotero's own: settled, no page queued
    assert all(_rows(ws, "annotation")[key]["detail"]["labels"] == za.LABELS for key in theirs.values())
    _unsettle(ws, *theirs.values())
    edits = [{"op": "set", "id": rows[key]["block_id"], "props": {"color": "#5fb236"}} for key in theirs.values()]
    _edit(ws, owner_id, page, edits)
    bodies = len(fake.sent)
    _sync(me, ws)  # each sent for its colour, never its label
    sent = _bodies(fake, bodies)
    assert len(sent) == 3 and all(set(body) == {"key", "version", "annotationColor"} for body in sent)
    assert {label: fake.data(LIB, key)["annotationPageLabel"] for label, key in theirs.items()} == {
        "523": "523", "1": "1", "<FEFF0031>": "<FEFF0031>"}
    assert all(_rows(ws, "annotation")[key]["detail"]["labels"] == za.LABELS for key in theirs.values())


def test_a_page_label_zotero_read_from_a_tree_pdfjs_refuses_is_never_sent_over(me, made, fake, owner_id):
    # /Type is not /PageLabel: pdf.js reads no labels (the page number, "1"), MuPDF reads "523", as Zotero 9's
    # reader may label its own annotation from the page number printed on it
    item, att = _labelled_paper(fake, tree="<</Nums[0<</Type/Label/S/D/St 523>>]>>")
    theirs = _zotero_highlight(fake, att, "523")
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, item)
    row = _rows(ws, "annotation")[theirs]
    assert row["detail"]["labels"] == za.LABELS
    _unsettle(ws, theirs)
    assert _dirty(ws, page) is False and _label_checks(ws) == set()
    _unsettle(ws, theirs)
    _edit(ws, owner_id, page, [{"op": "set", "id": row["block_id"], "props": {"color": "#5fb236"}}])
    bodies = len(fake.sent)
    _sync(me, ws)
    [sent] = _bodies(fake, bodies)
    assert set(sent) == {"key", "version", "annotationColor"} and fake.data(LIB, theirs)["annotationPageLabel"] == "523"
    assert _rows(ws, "annotation")[theirs]["detail"]["labels"] == za.LABELS


def test_another_members_annotation_keeps_its_page_label(me, made, fake, owner_id):
    item, att = _labelled_paper(fake, GROUP)
    with fake.acting_as(CAROL):
        carols = _zotero_highlight(fake, att, "<FEFF0031>", GROUP)
    ws = _link(me, library=GROUP)
    _sync(me, ws)
    page = _page_of(ws, item)
    assert _rows(ws, "annotation")[carols]["state"] == "readonly"
    key = _highlight_sent_here(me, fake, owner_id, ws, page, att, "zaGroupLabelled", GROUP)
    # Gamma made this one under its key, but Zotero names carol its maker (the key a link of this workspace
    # had then was hers): only she may change it
    fake.libraries[GROUP].authors[key]["created"] = CAROL
    _unsettle(ws, key, carols)
    assert _label_checks(ws, GROUP) == {page}
    with fake.acting_as(CAROL):
        fake.edit(GROUP, carols, annotationComment="carol's again")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    assert fake.data(GROUP, key)["annotationPageLabel"] == fake.data(GROUP, carols)["annotationPageLabel"] == \
        "<FEFF0031>"
    rows = _rows(ws, "annotation")
    assert rows[key]["detail"]["labels"] == rows[carols]["detail"]["labels"] == za.LABELS


def test_a_row_gamma_made_on_a_pdf_whose_two_readings_agree_needs_no_round(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    made_here = _highlight([TITLE_RECT], quote="Attention Is All You Need", content="the title")
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zaAgreeing", "parent": page, **made_here}])
    _sync(me, ws)
    key = object_key(ws, LIB, "annotation", keys["pdf"], "zaAgreeing", "")
    pulled = tuple(keys[name] for name in ("highlight", "underline", "spanning"))
    _unsettle(ws, key, *pulled)
    assert _dirty(ws, page) is False  # no label of the file reads otherwise to PyMuPDF: nothing to send
    assert _label_checks(ws) == {page} and _label_checks(ws) == set()  # for the one Gamma made, once
    rows = _rows(ws, "annotation")
    assert rows[key]["detail"]["labels"] == 0 and all(rows[k]["detail"]["labels"] == za.LABELS for k in pulled)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
