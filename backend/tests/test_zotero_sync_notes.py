"""The Zotero link's notes (gamma/zotero_sync_blocks.py) against the fake
Zotero (zotero_fake.py): child notes as note regions and standalone notes as
note pages in the first fill, their pictures; a pulled note as a block-level
diff that keeps what Zotero never saw (a nested highlight, a commented
child, a reference, a picture, a drawing), rendering under the write lock
the regions it decided alone; a Zotero trash lifting those blocks; Gamma's
edits of a note, a bullet as a list item both ways, its own notes sent
together as "Notes from Gamma" (the one heading they are known by) once the
page is still; both sides changed and
the resolutions; a note's first line deleted here; lossy notes never written
back (adopt mine and force included), a force taking Zotero's version of
one counting it under "replaced here" and resolving its open row (also
when Zotero trashed it or its region was deleted here, not an unsettable
root's), Send anyway sent by a force push and spent by a force
pull; the 500,000-character limit; a key without notes access, and notes
access granted later (pairing what a relinked workspace holds); a
receive-only round's note create waiting; a create whose answer was lost;
a note page changed here whose note Zotero removed for good, going back as
a new note (an unchanged one to Recently deleted); a backup restored over a
note page Zotero changed; a first link of an imported note Zotero edited
since. Rounds run through ``POST …/sync?wait=1`` with the budget lifted;
background rounds are stubbed. Accounts are ``zn_…``."""

import time
from datetime import timedelta

import pytest
from conftest import doc_props, login
from zotero_engine import (LIB, _block, _block_of, _conflict_count, _conflicts, _due, _due_now, _edit,  # noqa: F401
                           _force, _kids, _link, _page_of, _rows, _sent_keys, _state, _sync, _writes, asked_soon,
                           background, bounded, drop_pending, fake, idle, made, me, no_relocation, owner_id, quiet)
from zotero_fake import FakeZotero
from zotero_fixtures import PICTURE, pdf_bytes, standard_library
from zotero_fixtures import _note as note_html
from zotero_idle import assert_idle

from gamma import (ops, storage, ws_backup, zotero_account, zotero_local, zotero_sync, zotero_sync_blocks,
                   zotero_sync_rows)
from gamma.blocks_store import TRASH
from gamma.db import connect_pages_db, format_stamp, parse_stamp
from gamma.zotero_web import item_uri

USER, PASSWORD = "zn_owner", "pw-zn_owner-123"


def _note(fake, key) -> str:
    return fake.data(LIB, key)["note"]


def _still(ws, page_id):
    """The page as if it had been still for an hour (its notes may go, and
    the page's wait for that is over)."""
    with connect_pages_db(ws) as conn:
        old = format_stamp(parse_stamp(conn.execute("SELECT updated_at FROM unified_blocks WHERE id = ?",
                                                    (page_id,)).fetchone()[0]) - timedelta(hours=1))
        conn.execute("UPDATE unified_blocks SET updated_at = ? WHERE id = ?", (old, page_id))
        conn.execute("UPDATE zotero_queue SET next_at = '' WHERE id = ?", (f"page:{page_id}",))
        conn.commit()


def _region_root(ws, key) -> str:
    return _rows(ws, "note")[key]["block_id"]


# --- the first fill ---------------------------------------------------------------------

def test_child_notes_arrive_as_regions_and_a_standalone_note_as_a_note_page(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    status = _sync(me, ws)
    notes = _rows(ws, "note")
    page = _page_of(ws, keys["attention"])
    # the child note: a region at the page's top level, marked with its key, its blocks the note's
    root = notes[keys["note"]]["block_id"]
    assert notes[keys["note"]]["page_id"] == page and notes[keys["note"]]["parent"] == keys["attention"]
    block = _block(ws, root)
    assert block["parent"] == page and block["content"] == "# Key points"
    assert block["props"]["zotero_note"] == item_uri(LIB, keys["note"]) and "zotero_readonly" not in block["props"]
    assert [c for _, c in _kids(ws, root)] == ["Read this **twice**, then *once more*.", "Self-attention",
                                                "Positional encoding"]
    # the picture note: its picture is an upload Gamma holds, its key kept on an image row
    picture_root = notes[keys["image_note"]]["block_id"]
    [(_, content)] = _kids(ws, picture_root)
    image = _rows(ws, "image")[keys["image"]]
    assert image["parent"] == keys["image_note"]
    assert content == f"![architecture|320](/api/uploads/{image['detail']['name']})"
    assert storage.open_path(ws, image["detail"]["name"]).read_bytes() == PICTURE
    # the standalone note: a note page with its title, its body, filed in its collection
    standalone = notes[keys["standalone_note"]]
    note_page = _block(ws, standalone["page_id"])
    assert standalone["block_id"] == standalone["page_id"] and note_page["parent"] == "root"
    assert note_page["content"] == "Ideas for the next project"
    uri = item_uri(LIB, keys["standalone_note"])
    assert note_page["props"]["zotero_note"] == note_page["props"]["zotero_key"] == uri
    assert [c for _, c in _kids(ws, standalone["page_id"])] == ["Try sparse attention on long documents."]
    assert status["notes_pulled"] == 2 and _writes(fake) == []  # the note page counts as a page
    # a round after a round writes nothing on either side
    for _ in range(2):
        calls = len(fake.calls)
        status = _sync(me, ws)
        assert _writes(fake, calls) == [] and status["notes_pulled"] == status["notes_pushed"] == 0
        assert status["blocks_added"] == status["blocks_changed"] == status["blocks_removed"] == 0


# --- a note pulled, a note trashed ----------------------------------------------------------

PNG = PICTURE


def _keepers(me, ws, owner, page, root, keys):
    """Under the note's root: the page's highlight (its Zotero annotation's
    block) with a note of its own under it, a reference, a picture of
    Gamma's and the page's drawing; a comment thread on a paragraph."""
    anns = _rows(ws, "annotation")
    hl, ink = anns[keys["highlight"]]["block_id"], anns[keys["ink_annotation"]]["block_id"]
    name, _ = storage.store_file(ws, PNG + b"gamma's", ".png")
    first = _kids(ws, root)[0][0]
    _edit(ws, owner, page, [
        {"op": "insert", "id": "znElse", "parent": page, "content": "a block elsewhere"},
        {"op": "move", "id": hl, "parent": root},
        {"op": "insert", "id": "znHlNote", "parent": hl, "content": "my thought on it"},
        {"op": "insert", "id": "znRef", "parent": root, "content": "See [[znElse]]"},
        {"op": "insert", "id": "znPic", "parent": root, "content": f"![](/api/uploads/{name})"},
        {"op": "move", "id": ink, "parent": root}])
    r = me.post(f"/api/pages/{page}/comments", json={"block_id": first, "body": "a thread"},
                headers={"X-Gamma-Workspace": ws})
    assert r.status_code == 201, r.text
    return {"hl": hl, "ink": ink, "first": first, "thread": r.json()["thread"]["id"], "picture": name}


def _thread_shown(ws, thread_id) -> bool:
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT orphaned_at FROM comments WHERE id = ?", (thread_id,)).fetchone()[0] is None


def test_a_note_pulled_keeps_a_nested_highlight_a_commented_child_a_ref_a_picture_and_a_drawing(
        me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    root = _region_root(ws, keys["note"])
    kept = _keepers(me, ws, owner_id, page, root, keys)
    _sync(me, ws)  # the highlight's comment now carries its nested note; the note itself did not change
    assert "my thought on it" in fake.data(LIB, keys["highlight"])["annotationComment"]
    assert _note(fake, keys["note"]).count("Positional encoding") == 1
    before = {bid: content for bid, content in _kids(ws, root)}
    fake.edit(LIB, keys["note"], note=_note(fake, keys["note"]).replace("Positional encoding", "Positional encodings"))
    calls = len(fake.calls)
    status = _sync(me, ws)
    after = _kids(ws, root)
    changed = [bid for bid, content in after if before.get(bid) != content]
    assert len(changed) == 1 and dict(after)[changed[0]] == "Positional encodings"
    assert [bid for bid, _ in after] == list(before)  # every block kept its id and its place
    assert _block(ws, kept["hl"])["parent"] == root and _kids(ws, kept["hl"]) == [("znHlNote", "my thought on it")]
    assert _block(ws, "znRef")["content"] == "See [[znElse]]"
    assert _block(ws, "znPic")["content"] == f"![](/api/uploads/{kept['picture']})"
    assert _block(ws, kept["ink"])["parent"] == root and _thread_shown(ws, kept["thread"])
    assert status["notes_pulled"] == 1 and _writes(fake, calls) == []
    assert not fake.data(LIB, keys["highlight"]).get("deleted")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


@pytest.fixture
def renders(monkeypatch):
    """How many note regions ``Blocks.rows_tx`` renders (under the unit's write lock)."""
    seen = {"in": False, "n": 0}
    region, rows_tx = zotero_local._region, zotero_sync_blocks.Blocks.rows_tx

    def counted(*a, **k):
        seen["n"] += seen["in"]
        return region(*a, **k)

    def tx(self, conn):
        seen["in"] = True
        try:
            return rows_tx(self, conn)
        finally:
            seen["in"] = False

    monkeypatch.setattr(zotero_local, "_region", counted)
    monkeypatch.setattr(zotero_sync_blocks.Blocks, "rows_tx", tx)
    return seen


def test_a_pulled_annotation_change_renders_no_note_region_under_the_lock(me, made, fake, renders):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    fake.edit(LIB, keys["highlight"], annotationColor="#5fb236")
    renders["n"] = 0
    _sync(me, ws)
    assert _block(ws, _block_of(ws, keys["highlight"]))["props"]["color"].lower().startswith("#5fb236")
    assert renders["n"] == 0


def test_a_pulled_note_renders_its_own_region_alone_under_the_lock(me, made, fake, renders):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    html = fake.data(LIB, keys["note"])["note"]
    fake.edit(LIB, keys["note"], note=html.replace("</ul>", "<li>Multi-head</li></ul>"))
    renders["n"] = 0
    _sync(me, ws)
    assert "Multi-head" in [c for _, c in _kids(ws, _rows(ws, "note")[keys["note"]]["block_id"])]
    assert renders["n"] == 1


def test_a_note_zotero_trashes_lifts_what_gamma_keeps_and_a_restore_brings_it_back(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    root = _region_root(ws, keys["note"])
    kept = _keepers(me, ws, owner_id, page, root, keys)
    _sync(me, ws)
    represented = [bid for bid, _ in _kids(ws, root) if bid not in (kept["hl"], kept["ink"], "znRef", "znPic")]
    fake.trash(LIB, keys["note"])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, root) is None and all(_block(ws, bid) is None for bid in represented)
    top = [bid for bid, _ in _kids(ws, page)]
    for bid in (kept["hl"], "znRef", "znPic", kept["ink"]):  # lifted to the page, after the note's place
        assert bid in top
    assert _kids(ws, kept["hl"]) == [("znHlNote", "my thought on it")]
    assert _rows(ws, "note")[keys["note"]]["state"] == "trashed" and _writes(fake, calls) == []
    assert not fake.data(LIB, keys["highlight"]).get("deleted")  # the annotation it held stays in Zotero
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # Zotero brings the note back: a region again, at the page's end
    fake.restore(LIB, keys["note"])
    _sync(me, ws)
    row = _rows(ws, "note")[keys["note"]]
    assert row["state"] == "live" and _block(ws, row["block_id"])["content"] == "# Key points"
    assert [c for _, c in _kids(ws, row["block_id"])] == ["Read this **twice**, then *once more*.",
                                                           "Self-attention", "Positional encoding"]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- Gamma's edits -----------------------------------------------------------------------

def test_an_edit_of_a_note_here_goes_to_zotero_with_its_picture_key(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    root = _region_root(ws, keys["note"])
    first = _kids(ws, root)[0][0]
    _edit(ws, owner_id, page, [{"op": "set", "id": first, "content": "Read this **three times**."}])
    calls = len(fake.calls)
    status = _sync(me, ws)
    assert "<strong>three times</strong>" in _note(fake, keys["note"]) and status["notes_pushed"] == 1
    assert [m for m, _ in _writes(fake, calls)] == ["POST"]
    # the picture note: its caption edited here, its picture goes back as Zotero's own
    picture_root = _region_root(ws, keys["image_note"])
    _edit(ws, owner_id, page, [{"op": "set", "id": picture_root, "content": "The whole architecture:"}])
    _sync(me, ws)
    html = _note(fake, keys["image_note"])
    assert "The whole architecture:" in html and 'data-attachment-key="PICT2345"' in html
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # deleted here (the whole region), unchanged in Zotero: trashed there
    _edit(ws, owner_id, page, [{"op": "delete", "id": root}])
    _sync(me, ws)
    assert fake.data(LIB, keys["note"]).get("deleted") and _rows(ws, "note")[keys["note"]]["state"] == "trashed"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_bullet_goes_to_zotero_as_a_list_item_and_keeps_its_marker_when_zotero_changes_it(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    first = _kids(ws, _region_root(ws, keys["note"]))[0][0]
    _edit(ws, owner_id, page, [{"op": "set", "id": first, "content": "- a bullet"}])
    _sync(me, ws)
    html = _note(fake, keys["note"])
    assert "<li>a bullet</li>" in html and "- a bullet" not in html
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # Zotero's words come in under the marker, and nothing goes back
    fake.edit(LIB, keys["note"], note=html.replace("a bullet", "a better bullet"))
    _sync(me, ws)
    assert _block(ws, first)["content"] == "- a better bullet"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_note_sent_before_bullets_were_list_items_taken_from_zotero_keeps_one_marker(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    first = _kids(ws, _region_root(ws, keys["note"]))[0][0]
    _edit(ws, owner_id, page, [{"op": "set", "id": first, "content": "- a bullet"}])
    _sync(me, ws)
    # the note as such a note stands: the dash its text, both bases on it, and Zotero edits another line
    old = _note(fake, keys["note"]).replace("<li>a bullet</li>", "<li>- a bullet</li>")
    with connect_pages_db(ws) as conn:
        row = zotero_sync_rows.read_rows(conn, "key = ?", (keys["note"],))[keys["note"]]
        hashed = {"note": zotero_sync_blocks.note_hash(old)}
        row.update(zbase={**row["zbase"], **hashed}, gbase={**row["gbase"], **hashed})
        zotero_sync_rows.write_rows(conn, [row])
        conn.commit()
    fake.edit(LIB, keys["note"], note=old.replace("Positional encoding", "Positional encoding, Zotero's"))
    _sync(me, ws)
    [conflict] = _conflicts(me, ws, "zotero_note")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    assert _block(ws, first)["content"] == "- a bullet"
    assert "Positional encoding, Zotero's" in [c for _, c in _kids(ws, _region_root(ws, keys["note"]))]
    _sync(me, ws)  # sent back as Zotero's, the dash gone from it
    assert "<li>a bullet</li>" in _note(fake, keys["note"]) and "- a bullet" not in _note(fake, keys["note"])


def test_zoteros_words_taken_for_a_bullet_root_gamma_cannot_set_keep_its_marker(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    root = _region_root(ws, keys["note"])
    line = _kids(ws, root)[1][0]
    _edit(ws, owner_id, page, [{"op": "set", "id": root, "content": f"- About [[{line}]]"}])
    _sync(me, ws)
    fake.edit(LIB, keys["note"], note=_note(fake, keys["note"]).replace("About Self-attention", "Zotero words"))
    _sync(me, ws)
    assert _block(ws, root)["content"] == f"- About [[{line}]]"
    [conflict] = _conflicts(me, ws, "zotero_lossy")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    assert _block(ws, root)["content"] == "- Zotero words"
    _sync(me, ws)
    assert "<li>Zotero words" in _note(fake, keys["note"])


def test_the_pages_own_notes_go_together_as_notes_from_gamma_once_the_page_is_still(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    _edit(ws, owner_id, page, [{"op": "insert", "id": "znOwn1", "parent": page, "content": "First thought"},
                               {"op": "insert", "id": "znOwn2", "parent": page, "content": "Second thought"},
                               {"op": "insert", "id": "znOwn3", "parent": "znOwn2", "content": "a detail"}])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []  # typed just now: nothing yet
    queued = {r[0]: r[1] for r in _queue_rows(ws)}
    assert queued.get(f"page:{page}")  # it is looked at again once the page has been still...
    assert zotero_sync._clock.pending[ws] - time.monotonic() > 30  # ...by a round the clock runs then
    _still(ws, page)
    _sync(me, ws)
    [note] = [d for d in fake.items(LIB) if d.get("itemType") == "note" and d.get("parentItem") == keys["thesis"]]
    assert note["note"].startswith("<h1>Notes from Gamma</h1>")
    assert "First thought" in note["note"] and "a detail" in note["note"]
    row = _rows(ws, "note")[note["key"]]
    assert row["slot"] == "page" and row["block_id"] == page and row["state"] == "live"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # edited in Zotero: the page's own notes take it, block by block
    fake.edit(LIB, note["key"], note=note["note"].replace("Second thought", "Second, better thought"))
    _sync(me, ws)
    assert _block(ws, "znOwn2")["content"] == "Second, better thought" and _block(ws, "znOwn3")["parent"] == "znOwn2"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # all of them deleted here: the note goes to Zotero's trash once the page is still
    _edit(ws, owner_id, page, [{"op": "delete", "id": "znOwn1"}, {"op": "delete", "id": "znOwn2"}])
    _still(ws, page)
    _sync(me, ws)
    assert fake.data(LIB, note["key"]).get("deleted")


def test_the_pages_own_note_is_known_by_the_one_heading_it_is_sent_with(monkeypatch):
    from gamma import zotero_merge
    from gamma.zotero_notes import note_tree
    for heading in (zotero_merge.NOTES_HEADING, "# Notes kept in Gamma"):
        monkeypatch.setattr(zotero_merge, "NOTES_HEADING", heading)
        sent = zotero_sync_blocks._empty_region("zfPage").html
        assert zotero_sync_blocks._is_gamma_notes(note_tree(sent)), heading
    assert not zotero_sync_blocks._is_gamma_notes(note_tree("<h1>Notes from Gamma</h1>"))
    assert zotero_sync_blocks.NOTE_TOO_LONG == "Zotero keeps notes of at most 500,000 characters"


def _queue_rows(ws):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT id, next_at FROM zotero_queue").fetchall()


def test_a_note_page_edited_here_and_in_zotero_goes_both_ways(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["standalone_note"])
    [(child, _)] = _kids(ws, page)
    _edit(ws, owner_id, page, [{"op": "set", "id": child, "content": "Try sparse attention on very long documents."}])
    _sync(me, ws)
    html = _note(fake, keys["standalone_note"])
    assert html.startswith("<h1>Ideas for the next project</h1>") and "very long documents" in html
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # Zotero renames it and adds a line: the title follows, the line comes as a block
    fake.edit(LIB, keys["standalone_note"], note=html.replace("Ideas for the next project", "Ideas for 2027")
              .replace("</ul>", "<li>Ask Carol</li></ul>"))
    _sync(me, ws)
    assert _block(ws, page)["content"] == "Ideas for 2027"
    assert [c for _, c in _kids(ws, page)] == ["Try sparse attention on very long documents.", "Ask Carol"]
    assert _kids(ws, page)[0][0] == child
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- both sides changed ---------------------------------------------------------------------

def test_a_note_changed_on_both_sides_keeps_gammas_and_theirs_converges(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    root = _region_root(ws, keys["note"])
    first = _kids(ws, root)[0][0]
    _edit(ws, owner_id, page, [{"op": "set", "id": first, "content": "Gamma's reading"}])
    fake.edit(LIB, keys["note"], note=_note(fake, keys["note"]).replace("Self-attention", "Self-attention, Zotero's"))
    _sync(me, ws)
    assert "Gamma's reading" in _note(fake, keys["note"]) and "Zotero's" not in _note(fake, keys["note"])
    [conflict] = _conflicts(me, ws, "zotero_note")
    assert conflict["block_id"] == root and "Zotero's" in conflict["theirs"] and "Gamma's reading" in conflict["mine"]
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    assert "Self-attention, Zotero's" in [c for _, c in _kids(ws, root)]
    _sync(me, ws)  # sent back: both hold Zotero's again
    assert "Self-attention, Zotero" in _note(fake, keys["note"]) and "Gamma's reading" not in _note(fake, keys["note"])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_an_edit_beats_a_delete_both_ways_for_notes(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    root = _region_root(ws, keys["note"])
    # deleted here, edited in Zotero: it comes back here
    _edit(ws, owner_id, page, [{"op": "delete", "id": root}])
    fake.edit(LIB, keys["note"], note=_note(fake, keys["note"]).replace("Self-attention", "Self-attention!"))
    _sync(me, ws)
    row = _rows(ws, "note")[keys["note"]]
    assert row["state"] == "live" and "Self-attention!" in [c for _, c in _kids(ws, row["block_id"])]
    assert not fake.data(LIB, keys["note"]).get("deleted") and _conflicts(me, ws, "zotero_deleted_edited")
    # edited here, trashed in Zotero: it comes back in Zotero
    _edit(ws, owner_id, page, [{"op": "set", "id": row["block_id"], "content": "# Key points, edited"}])
    fake.trash(LIB, keys["note"])
    _sync(me, ws)
    data = fake.data(LIB, keys["note"])
    assert not data.get("deleted") and "Key points, edited" in data["note"]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- a note's first line deleted here -------------------------------------------------------------

def _gamma_notes(fake) -> list:
    return [d for d in fake.items(LIB) if d.get("itemType") == "note" and "Notes from Gamma" in d.get("note", "")]


def _delete_keep_children(ws, owner_id, page, root):
    """"Delete line" on a block: it alone goes, its children lifted into its
    place (blockModel.removeBlockKeepChildren)."""
    kids = _kids(ws, root)
    _edit(ws, owner_id, page, [{"op": "move", "id": k, "parent": page} for k, _ in kids]
          + [{"op": "delete", "id": root}])
    return [k for k, _ in kids]


def test_a_notes_first_line_deleted_here_keeps_the_note_in_zotero_with_what_is_left(me, made, fake, owner_id, quiet):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    row = _rows(ws, "note")[keys["note"]]
    page, root = row["page_id"], row["block_id"]
    assert row["detail"]["rep"][0] == root  # the blocks the note stands for, root first
    first, *rest = _delete_keep_children(ws, owner_id, page, root)
    for _ in range(3):
        _due(ws)
        _sync(me, ws)
    data = fake.data(LIB, keys["note"])
    assert not data.get("deleted")
    assert "Read this" in data["note"] and "Key points" not in data["note"]
    assert _rows(ws, "note")[keys["note"]]["block_id"] == first
    assert _block(ws, first)["props"]["zotero_note"] == item_uri(LIB, keys["note"])
    assert not any("Read this" in d["note"] for d in _gamma_notes(fake))  # never sent twice
    assert all(any(_block(ws, k)["content"].split()[0] in d["note"] for d in _gamma_notes(fake)) for k in rest)


def test_a_lossy_notes_first_line_deleted_here_writes_nothing_to_it(me, made, fake, owner_id, quiet):
    keys = made["keys"]
    html = fake.data(LIB, keys["note"])["note"]
    fake.edit(LIB, keys["note"], note=html.replace("</ul>", "<li><span class=\"citation\" data-citation=\"%7B%7D\">"
                                                          "(Vaswani, 2017)</span></li></ul>"))
    ws = _link(me)
    _sync(me, ws)
    row = _rows(ws, "note")[keys["note"]]
    assert row["detail"].get("lossy")
    page, root = row["page_id"], row["block_id"]
    before = fake.data(LIB, keys["note"])["note"]
    _delete_keep_children(ws, owner_id, page, root)
    for _ in range(3):
        _due(ws)
        _sync(me, ws)
    data = fake.data(LIB, keys["note"])
    assert not data.get("deleted") and data["note"] == before
    assert [c for c in _conflicts(me, ws, "zotero_lossy")]


def test_a_note_deleted_here_with_all_its_lines_still_goes_to_zoteros_trash(me, made, fake, owner_id, quiet):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    row = _rows(ws, "note")[keys["note"]]
    _edit(ws, owner_id, row["page_id"], [{"op": "delete", "id": row["block_id"]}])
    _sync(me, ws)
    assert fake.data(LIB, keys["note"]).get("deleted")


# --- what Gamma cannot write back -----------------------------------------------------------------

CITED = ('<div data-schema-version="9"><p>As shown <span class="citation" data-citation="%7B%22citationItems%22'
         '%3A%5B%5D%7D">(Vaswani, 2017)</span>, attention suffices.</p><p>A second paragraph.</p></div>')


def test_a_lossy_note_is_read_only_here_and_never_written_back(me, made, fake, owner_id):
    keys = made["keys"]
    cited = fake.add_item(LIB, {"itemType": "note", "parentItem": keys["thesis"], "note": CITED})
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    root = _region_root(ws, cited)
    assert _block(ws, root)["props"]["zotero_readonly"] == "lossy"
    # Zotero's own edits come in
    fake.edit(LIB, cited, note=CITED.replace("attention suffices", "attention is all you need"))
    _sync(me, ws)
    assert "attention is all you need" in _block(ws, root)["content"]
    zoteros = _note(fake, cited)
    [(child, _)] = _kids(ws, root)
    for text in ("A second paragraph, edited.", "A second paragraph, edited twice."):
        _edit(ws, owner_id, page, [{"op": "set", "id": child, "content": text}])
        calls = len(fake.calls)
        _sync(me, ws)
        assert _writes(fake, calls) == [] and _note(fake, cited) == zoteros
    [conflict] = _conflicts(me, ws, "zotero_lossy")  # one open row, refreshed in place
    assert conflict["block_id"] == root and "edited twice" in conflict["mine"]
    # "mine": Gamma's goes anyway, once
    [conflict] = _conflicts(me, ws, "zotero_lossy")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "mine"})
    assert r.status_code == 200, r.text
    _sync(me, ws)
    assert "edited twice" in _note(fake, cited)


def test_adopt_mine_and_a_force_push_never_write_a_lossy_note(me, made, fake, owner_id):
    keys = made["keys"]
    cited = fake.add_item(LIB, {"itemType": "note", "parentItem": keys["thesis"], "note": CITED})
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    root = _region_root(ws, cited)
    [(child, _)] = _kids(ws, root)
    _edit(ws, owner_id, page, [{"op": "set", "id": child, "content": "Gamma's paragraph"}])
    me.post(f"/api/zotero/links/{ws}/force", json={"direction": "push"})
    _sync(me, ws)
    assert _note(fake, cited) == CITED  # the lossy note keeps Zotero's whatever the force says
    calls = len(fake.calls)
    _sync(me, ws)
    assert not [w for w in _writes(fake, calls) if cited in w[1]]


def _replaced(ws) -> list:
    """The link's "replaced here" and "replaced there" log lines, oldest first."""
    return [x["title"] for x in reversed(zotero_sync.list_log(ws, 500))
            if x["action"] in ("replaced here", "replaced there")]


def _resolved(me, ws, kind) -> list:
    return [c for c in me.get(f"/api/zotero/links/{ws}/conflicts?resolved=1").json()["conflicts"]
            if c["kind"] == kind]


def _lossy_edited_here(me, fake, owner_id, keys, *, standalone=False) -> tuple:
    """A lossy note (a child note of the thesis, or with ``standalone`` the
    standalone note's page) with its second line edited here: one open
    ``zotero_lossy`` row. ``(ws, the note's key, the block of its rows)``."""
    if standalone:
        key = keys["standalone_note"]
        fake.edit(LIB, key, note=CITED)
    else:
        key = fake.add_item(LIB, {"itemType": "note", "parentItem": keys["thesis"], "note": CITED})
    ws = _link(me)
    _sync(me, ws)
    row = _rows(ws, "note")[key]
    block = row["page_id"] if standalone else row["block_id"]
    [(child, _)] = _kids(ws, block)
    _edit(ws, owner_id, row["page_id"], [{"op": "set", "id": child, "content": "Gamma's paragraph"}])
    _sync(me, ws)
    [open_row] = _conflicts(me, ws, "zotero_lossy")
    assert open_row["block_id"] == block and "Gamma's paragraph" in open_row["mine"]
    return ws, key, block


@pytest.mark.parametrize("standalone", [False, True], ids=["child_note", "note_page"])
@pytest.mark.parametrize("direction", ["push", "pull"])
def test_a_force_taking_zoteros_lossy_note_counts_it_replaced_here_and_closes_its_open_row(
        me, made, fake, owner_id, direction, standalone):
    keys = made["keys"]
    ws, key, block = _lossy_edited_here(me, fake, owner_id, keys, standalone=standalone)
    paper = _page_of(ws, keys["thesis"])
    meta = _block(ws, paper)["props"]["meta"]
    _edit(ws, owner_id, paper, [{"op": "set", "id": paper, "props": {"meta": {**meta, "year": "1999"}}}])
    # open rows the force has nothing to do with: another kind on the note's block, a lossy row on another block
    page = _rows(ws, "note")[key]["page_id"]
    others = [zotero_sync_rows.record_conflict(ws, page, block, "zotero_rejected",
                                               result=f"Zotero refused this: {zotero_sync_blocks.NOTE_TOO_LONG}"),
              zotero_sync_rows.record_conflict(ws, paper, paper, "zotero_lossy", mine="See [[Some page]] here")]
    _force(me, ws, direction)
    assert [text for _, text in _kids(ws, block)] == ["A second paragraph."]  # Zotero's, whatever the force
    assert _note(fake, key) == CITED
    # the record went the forced side's way and the lossy note Zotero's: each counted where it was replaced
    assert _replaced(ws) == (["replaced here: 1 note", "replaced there: 1 record"] if direction == "push"
                             else ["replaced here: 1 record, 1 note"])
    # the open row of Gamma's edit is settled, kept for the record and naming the side taken; the others stay
    assert sorted(c["id"] for c in _conflicts(me, ws)) == sorted(others)
    [closed] = _resolved(me, ws, "zotero_lossy")
    assert closed["block_id"] == block and "Gamma's paragraph" in closed["mine"]
    assert closed["result"].endswith("Zotero's was taken here")
    [kept] = [c for c in _resolved(me, ws, "zotero_note") if c["block_id"] == block]
    assert "Gamma's paragraph" in kept["mine"]


@pytest.mark.parametrize("gone", ["trashed_in_zotero", "deleted_here"])
def test_a_force_pull_of_a_lossy_note_gone_on_one_side_closes_its_open_row(me, made, fake, owner_id, gone):
    ws, key, block = _lossy_edited_here(me, fake, owner_id, made["keys"])
    page = _rows(ws, "note")[key]["page_id"]
    if gone == "trashed_in_zotero":
        fake.trash(LIB, key)
    else:
        _edit(ws, owner_id, page, [{"op": "delete", "id": block}])
    _force(me, ws, "pull")
    row = _rows(ws, "note")[key]
    if gone == "trashed_in_zotero":  # Zotero's trash taken: the region goes, Gamma's words counted as replaced
        assert row["state"] == "trashed" and _block(ws, block) is None
        assert _replaced(ws) == ["replaced here: 1 note"]
    else:  # Zotero's note back as a new region: what it replaced is Gamma's delete, no words
        assert row["state"] == "live" and row["block_id"] != block
        assert [text for _, text in _kids(ws, row["block_id"])] == ["A second paragraph."]
        assert _replaced(ws) == []
    assert _note(fake, key) == CITED
    assert not _conflicts(me, ws)
    [closed] = _resolved(me, ws, "zotero_lossy")  # the old block's row, settled and kept for the record
    assert closed["block_id"] == block and "Gamma's paragraph" in closed["mine"]
    assert closed["result"].endswith("Zotero's was taken here")


@pytest.mark.parametrize("standalone", [False, True], ids=["child_note", "note_page"])
@pytest.mark.parametrize("direction", ["push", "pull"])
def test_a_force_after_send_anyway_counts_the_lossy_note_where_it_was_replaced_and_spends_the_choice(
        me, made, fake, owner_id, direction, standalone):
    ws, key, block = _lossy_edited_here(me, fake, owner_id, made["keys"], standalone=standalone)
    page = _rows(ws, "note")[key]["page_id"]
    [row] = _conflicts(me, ws, "zotero_lossy")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{row['id']}", json={"choice": "mine"})
    assert r.status_code == 200, r.text
    _force(me, ws, direction)
    if direction == "push":  # Gamma's goes, as chosen
        assert "Gamma's paragraph" in _note(fake, key)
        assert _replaced(ws) == ["replaced there: 1 note"]
    else:  # Zotero's is taken, as the force says
        assert _note(fake, key) == CITED and [text for _, text in _kids(ws, block)] == ["A second paragraph."]
        assert _replaced(ws) == ["replaced here: 1 note"]
    assert not _conflicts(me, ws)
    assert [c["id"] for c in _resolved(me, ws, "zotero_lossy")] == [row["id"]]  # the one the choice resolved
    assert not _rows(ws, "note")[key]["detail"].get("force")  # the choice is spent either way
    if direction == "pull":  # it was for the words the pull replaced: a later edit here stays in Gamma
        [(child, _)] = _kids(ws, block)
        _edit(ws, owner_id, page, [{"op": "set", "id": child, "content": "A later edit here"}])
        _sync(me, ws)
        assert _note(fake, key) == CITED
        [again] = _conflicts(me, ws, "zotero_lossy")
        assert "A later edit here" in again["mine"]


@pytest.mark.parametrize("gone", ["deleted_here", "trashed_in_zotero"])
def test_send_anyway_is_spent_when_a_force_pull_brings_the_lossy_note_back_as_a_new_region(
        me, made, fake, owner_id, gone):
    ws, key, block = _lossy_edited_here(me, fake, owner_id, made["keys"])
    page = _rows(ws, "note")[key]["page_id"]
    [row] = _conflicts(me, ws, "zotero_lossy")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{row['id']}", json={"choice": "mine"})
    assert r.status_code == 200, r.text
    if gone == "deleted_here":  # the force pull brings Zotero's note back
        _edit(ws, owner_id, page, [{"op": "delete", "id": block}])
        _force(me, ws, "pull")
    else:  # the force pull takes Zotero's trash, and Zotero's restore brings the note back
        fake.trash(LIB, key)
        _force(me, ws, "pull")
        assert not _rows(ws, "note")[key]["detail"].get("force")
        fake.restore(LIB, key)
        _sync(me, ws)
    row = _rows(ws, "note")[key]
    assert row["state"] == "live" and not row["detail"].get("force")
    [(child, text)] = _kids(ws, row["block_id"])
    assert text == "A second paragraph."
    _edit(ws, owner_id, page, [{"op": "set", "id": child, "content": "A later edit here"}])
    _sync(me, ws)
    assert _note(fake, key) == CITED  # the citation stays: the later edit is Gamma's alone
    [again] = _conflicts(me, ws, "zotero_lossy")
    assert "A later edit here" in again["mine"]


def test_a_force_pull_leaves_open_the_lossy_row_of_a_root_it_still_cannot_set(me, made, fake, owner_id):
    keys = made["keys"]
    key = fake.add_item(LIB, {"itemType": "note", "parentItem": keys["thesis"],
                              "note": "<p>Title line</p><p>Body here.</p>"})
    ws = _link(me)
    _sync(me, ws)
    row = _rows(ws, "note")[key]
    root, page = row["block_id"], row["page_id"]
    _edit(ws, owner_id, page, [{"op": "set", "id": root, "content": "See [[Some page]] here"}])  # Gamma's alone
    _due(ws)
    _still(ws, page)
    _sync(me, ws)
    fake.edit(LIB, key, note="<p>Zotero's title</p><p>Body here.</p>")
    _sync(me, ws)
    [before] = _conflicts(me, ws, "zotero_lossy")
    assert before["block_id"] == root
    _force(me, ws, "pull")
    assert _block(ws, root)["content"] == "See [[Some page]] here"  # a root Gamma may not set keeps its text
    [after] = _conflicts(me, ws, "zotero_lossy")  # ...and its row stays open, never on record as Zotero's taken
    assert after["id"] == before["id"] and after["mine"] == before["mine"]
    assert not _resolved(me, ws, "zotero_lossy")


def test_a_force_push_over_an_unchanged_library_rewrites_no_note_page(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    _sync(me, ws)
    sent = len(fake.sent)
    _force(me, ws, "push")
    assert keys["standalone_note"] not in _sent_keys(fake, sent)


def test_a_note_over_zoteros_limit_is_refused_before_it_goes(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    long = "word " * 39_000
    _edit(ws, owner_id, page, [{"op": "insert", "id": f"znLong{n}", "parent": page, "content": long}
                               for n in range(3)])
    _still(ws, page)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    [conflict] = _conflicts(me, ws, "zotero_rejected")
    assert "500,000" in conflict["result"]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- access and identity ---------------------------------------------------------------------------

def test_a_key_without_notes_access_moves_no_note_either_way(owner_id, monkeypatch):
    fake = FakeZotero(access={"user": {"library": True, "files": True, "notes": False, "write": True}}).install(
        monkeypatch)
    made = standard_library(fake)
    keys = made["keys"]
    zotero_account.connect(owner_id, fake.api_key)
    me = login(USER, PASSWORD)
    try:
        ws = _link(me)
        status = _sync(me, ws)
        assert status["access"]["notes"] is False and not _rows(ws, "note")
        page = _page_of(ws, keys["attention"])
        assert not any(c.startswith("# Key points") for _, c in _kids(ws, page))
        _edit(ws, owner_id, page, [{"op": "insert", "id": "znNoAccess", "parent": page, "content": "mine alone"}])
        _still(ws, page)
        calls = len(fake.calls)
        _sync(me, ws)
        assert _writes(fake, calls) == [] and not _rows(ws, "note")
        assert_idle(fake, ws)
    finally:
        for link in zotero_sync.list_links(owner_id):
            zotero_sync.remove_link(link["workspace_id"])
        zotero_account.disconnect(owner_id, revoke=False)


NO_NOTES = {"user": {"library": True, "files": True, "notes": False, "write": True}}


def _connected(owner_id, fake):
    zotero_account.connect(owner_id, fake.api_key)
    return login(USER, PASSWORD)


def _forget_links(owner_id):
    for link in zotero_sync.list_links(owner_id):
        zotero_sync.remove_link(link["workspace_id"])
    zotero_account.disconnect(owner_id, revoke=False)


def _live_notes(fake) -> list:
    return [d for d in fake.items(LIB) if d.get("itemType") == "note" and not d.get("deleted")]


def test_notes_access_granted_later_brings_zoteros_notes_and_sends_gammas(owner_id, monkeypatch, quiet):
    fake = FakeZotero(access=NO_NOTES).install(monkeypatch)
    keys = standard_library(fake)["keys"]
    me = _connected(owner_id, fake)
    try:
        ws = _link(me)
        _sync(me, ws)
        assert not _rows(ws, "note")
        page = _page_of(ws, keys["attention"])
        _edit(ws, owner_id, page, [{"op": "insert", "id": "zbGrant", "parent": page, "content": "mine alone"}])
        _still(ws, page)
        _sync(me, ws)
        assert len(_live_notes(fake)) == 3  # nothing went while the key could not see notes
        fake.keys[fake.api_key]["user"]["notes"] = True
        zotero_sync.forget_access()
        for _ in range(3):
            _sync(me, ws)
        assert {keys["note"], keys["image_note"], keys["standalone_note"]} <= set(_rows(ws, "note"))
        notes = _live_notes(fake)
        assert len(notes) == 4 and sum("mine alone" in d["note"] for d in notes) == 1
        assert _state(ws)["first"] == ""
        assert_idle(fake, ws)
    finally:
        _forget_links(owner_id)


def _relinked_without_notes(me, ws, fake):
    """The link removed and made again into its workspace with a key that
    sees no notes, the workspace's note blocks left from the first link."""
    zotero_sync.remove_link(ws)
    fake.keys[fake.api_key]["user"]["notes"] = False
    zotero_sync.forget_access()
    assert me.post("/api/zotero/links", json={"library": LIB, "workspace_id": ws}).status_code == 201
    _sync(me, ws)
    _sync(me, ws)


def _texts(ws, page) -> list:
    with connect_pages_db(ws) as conn:
        return [c for (c,) in conn.execute("SELECT content FROM unified_blocks WHERE page_id = ? AND id != ?",
                                           (page, page))]


def test_notes_access_granted_to_a_relinked_workspace_pairs_the_notes_it_holds(owner_id, monkeypatch, quiet):
    fake = FakeZotero().install(monkeypatch)
    keys = standard_library(fake)["keys"]
    me = _connected(owner_id, fake)
    try:
        ws = _link(me)
        _sync(me, ws)
        _relinked_without_notes(me, ws, fake)
        fake.keys[fake.api_key]["user"]["notes"] = True
        zotero_sync.forget_access()
        for _ in range(3):
            _sync(me, ws)
        assert {keys["note"], keys["image_note"], keys["standalone_note"]} <= set(_rows(ws, "note"))
        page = _page_of(ws, keys["attention"])
        with connect_pages_db(ws) as conn:
            heads = conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE page_id = ? AND content LIKE "
                                 "'# Key points%'", (page,)).fetchone()[0]
        assert heads == 1 and len(_live_notes(fake)) == 3  # each note once on either side
        assert_idle(fake, ws)
    finally:
        _forget_links(owner_id)


def test_notes_access_granted_to_a_relinked_workspace_pairs_what_it_can_and_brings_or_sends_the_rest(
        owner_id, monkeypatch, quiet):
    fake = FakeZotero().install(monkeypatch)
    keys = standard_library(fake)["keys"]
    me = _connected(owner_id, fake)
    try:
        ws = _link(me)
        _sync(me, ws)
        _relinked_without_notes(me, ws, fake)
        page = _page_of(ws, keys["attention"])
        # while the key sees no notes: Zotero edits one and makes another, Gamma writes one of its own
        fake.edit(LIB, keys["note"], note=fake.data(LIB, keys["note"])["note"].replace("twice", "three times"))
        fake.add_item(LIB, {"itemType": "note", "parentItem": keys["attention"], "note": "<p>Fresh from Zotero</p>"})
        _edit(ws, owner_id, page, [{"op": "insert", "id": "zbMine", "parent": page, "content": "mine alone"}])
        _still(ws, page)
        _sync(me, ws)
        fake.keys[fake.api_key]["user"]["notes"] = True
        zotero_sync.forget_access()
        monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 1)
        status = _sync(me, ws)  # the pass that pairs them, cut short: it goes on pairing in the next round
        assert status["backlog"] and _state(ws)["first"] == "notes"
        monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 100_000)
        for _ in range(3):
            _sync(me, ws)
        assert _state(ws)["first"] == ""
        texts = _texts(ws, page)
        assert sum(t.startswith("# Key points") for t in texts) == 1 and texts.count("The architecture:") == 1
        assert sum("three times" in t for t in texts) == 1 and not any("twice" in t for t in texts)
        assert texts.count("Fresh from Zotero") == 1 and texts.count("mine alone") == 1
        notes = _live_notes(fake)
        [own] = [d["note"] for d in notes if "mine alone" in d["note"]]
        assert len(notes) == 5 and not any(word in own for word in ("Key points", "architecture", "Fresh"))
        assert_idle(fake, ws)
    finally:
        _forget_links(owner_id)


def test_notes_access_granted_to_a_relinked_workspace_makes_no_second_note_page(owner_id, monkeypatch):
    fake = FakeZotero().install(monkeypatch)
    keys = standard_library(fake)["keys"]
    me = _connected(owner_id, fake)
    try:
        ws = _link(me)
        _sync(me, ws)
        page = _rows(ws, "note")[keys["standalone_note"]]["page_id"]
        with connect_pages_db(ws) as conn:
            title = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (page,)).fetchone()[0]
        _relinked_without_notes(me, ws, fake)
        fake.keys[fake.api_key]["user"]["notes"] = True
        zotero_sync.forget_access()
        for _ in range(3):
            _sync(me, ws)
        with connect_pages_db(ws) as conn:
            pages = conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE parent_id = 'root' AND content = ?",
                                 (title,)).fetchone()[0]
        assert pages == 1 and len(_live_notes(fake)) == 3
        assert _rows(ws, "note")[keys["standalone_note"]]["page_id"] == page
        assert_idle(fake, ws)
    finally:
        _forget_links(owner_id)


def test_notes_access_granted_to_a_relinked_workspace_takes_zoteros_words_of_a_note_page_both_changed(
        owner_id, monkeypatch):
    fake = FakeZotero().install(monkeypatch)
    keys = standard_library(fake)["keys"]
    me = _connected(owner_id, fake)
    try:
        ws = _link(me)
        _sync(me, ws)
        page = _rows(ws, "note")[keys["standalone_note"]]["page_id"]
        with connect_pages_db(ws) as conn:
            [(line,)] = conn.execute("SELECT id FROM unified_blocks WHERE page_id = ? AND content LIKE "
                                     "'Try sparse%'", (page,)).fetchall()
        _relinked_without_notes(me, ws, fake)
        # while the key sees no notes: both sides change the note's one line
        key = keys["standalone_note"]
        fake.edit(LIB, key, note=fake.data(LIB, key)["note"].replace("sparse", "linear"))
        _edit(ws, owner_id, page, [{"op": "set", "id": line, "content": "Try local attention on long documents."}])
        fake.keys[fake.api_key]["user"]["notes"] = True
        zotero_sync.forget_access()
        calls = len(fake.calls)
        for _ in range(3):
            _sync(me, ws)
        assert _rows(ws, "note")[key]["page_id"] == page and _texts(ws, page) == [
            "Try linear attention on long documents."]  # Zotero's side, as a first link's pair takes it
        assert "linear" in fake.data(LIB, key)["note"] and not _writes(fake, calls)
        with connect_pages_db(ws) as conn:
            [(mine, resolved)] = conn.execute("SELECT mine, resolved FROM sync_conflicts WHERE kind = 'zotero_note' "
                                              "AND page_id = ?", (page,)).fetchall()
        assert "local" in mine and resolved == 1  # Gamma's words kept in a resolved row
        assert_idle(fake, ws)
    finally:
        _forget_links(owner_id)


@pytest.mark.parametrize("why", ["mode", "key"])
def test_a_note_create_that_did_not_land_waits_while_the_link_only_receives(me, made, fake, owner_id, quiet, why):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["ppo"])
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zfMine", "parent": page, "content": "my own note"}])
    fake.fail_next(500, f"/{LIB}/items", method="POST")
    _sync(me, ws, ok=False)
    assert [r for r in _rows(ws, "note").values() if r["page_id"] == page and int(r["version"]) <= 0]
    if why == "mode":
        assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 200
    else:  # the key lost write access: the round only receives
        fake.keys[fake.api_key]["user"]["write"] = False
        zotero_sync.forget_access()
    fake.edit(LIB, keys["ppo"], title="PPO renamed in Zotero")
    _due(ws)
    calls = len(fake.calls)
    status = _sync(me, ws, ok=False)
    assert _writes(fake, calls) == [] and status.get("error_kind") != "auth", status
    assert _block(ws, page)["content"] == "PPO renamed in Zotero"
    assert not _gamma_notes(fake)
    if why == "mode":
        assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    else:
        fake.keys[fake.api_key]["user"]["write"] = True
        zotero_sync.forget_access()
    _due(ws)
    _sync(me, ws)
    assert len(_gamma_notes(fake)) == 1


def test_a_note_create_whose_answer_was_lost_is_confirmed_and_never_made_twice(me, made, fake, owner_id,
                                                                               monkeypatch):
    from gamma import zotero_remote as zr
    from gamma.zotero_web import ZoteroUnreachable
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])
    _edit(ws, owner_id, page, [{"op": "insert", "id": "znLost", "parent": page, "content": "Lost in transit"}])
    _still(ws, page)
    real = zr.Writes.flush

    def lost(self):  # the create lands in Zotero; its answer never comes back
        real(self)
        raise ZoteroUnreachable("Zotero could not be reached: the connection dropped")

    monkeypatch.setattr(zr.Writes, "flush", lost)
    status = _sync(me, ws, ok=False)
    monkeypatch.setattr(zr.Writes, "flush", real)
    assert status["error_kind"] == "offline"
    [pending] = [row for row in _rows(ws, "note").values() if row["slot"] == "page"]
    assert pending["state"] == "pending"
    _sync(me, ws)
    assert _rows(ws, "note")[pending["key"]]["state"] == "live"
    made_there = [d for d in fake.items(LIB) if d.get("itemType") == "note" and "Lost in transit" in d.get("note", "")]
    assert len(made_there) == 1
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_note_page_deleted_here_and_edited_in_zotero_comes_back_with_zoteros_note(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["standalone_note"])
    [(child, _)] = _kids(ws, page)
    _trash(ws, owner_id, page)
    html = _note(fake, keys["standalone_note"])
    fake.edit(LIB, keys["standalone_note"], note=html.replace("long documents", "long books"))
    _sync(me, ws)
    assert _block(ws, page)["parent"] == "root"  # an edit beats a delete: the page is back...
    assert _kids(ws, page) == [(child, "Try sparse attention on long books.")]  # ...with Zotero's note in it
    assert "long books" in _note(fake, keys["standalone_note"]) and not fake.data(LIB, keys["standalone_note"]).get(
        "deleted")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # deleted here, unchanged in Zotero: Zotero's goes to its trash
    _trash(ws, owner_id, page)
    _sync(me, ws)
    assert fake.data(LIB, keys["standalone_note"]).get("deleted")


def _trash(ws, owner, page_id):
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page_id, actor=owner, client="t")
        conn.commit()


def test_a_note_page_changed_here_whose_note_zotero_removed_for_good_goes_back_as_a_new_note(me, made, fake,
                                                                                             owner_id, bounded):
    keys = made["keys"]
    other = fake.add_item(LIB, {"itemType": "note", "note": note_html("<h1>Left alone</h1><p>Never edited.</p>")})
    ws = _link(me)
    _sync(me, ws)
    page, untouched = _page_of(ws, keys["standalone_note"]), _page_of(ws, other)
    [(child, _)] = _kids(ws, page)
    _edit(ws, owner_id, page, [{"op": "set", "id": child, "content": "Edited here before Zotero emptied its trash."}])
    fake.delete(LIB, keys["standalone_note"])
    fake.delete(LIB, other)
    drop_pending(ws)
    _sync(me, ws)
    # unchanged here: to Recently deleted, its row with it
    assert _block(ws, untouched)["parent"] == TRASH and other not in _rows(ws)
    # changed here: it stays, its row for the removed note gone, and goes back as a new standalone note
    assert _block(ws, page)["parent"] == "root" and keys["standalone_note"] not in _rows(ws)
    [(new, row)] = [(k, r) for k, r in _rows(ws).items() if r["page_id"] == page]
    data = fake.data(LIB, new)
    assert new != keys["standalone_note"] and row["kind"] == "note" and row["state"] == "live"
    assert data["itemType"] == "note" and not data.get("parentItem") and "emptied its trash" in data["note"]
    assert data["collections"] == [keys["reading"]]
    props = _block(ws, page)["props"]
    assert props["zotero_key"] == props["zotero_note"] == item_uri(LIB, new)
    [conflict] = _conflicts(me, ws)
    assert conflict["kind"] == "zotero_deleted_edited"
    assert conflict["result"].endswith("it goes back as a new note")
    assert _due_now(ws) == [] and not asked_soon(ws)  # settled in one round
    calls = len(fake.calls)
    for _ in range(2):
        _sync(me, ws)
    assert _writes(fake, calls) == [] and _conflict_count(ws) == 1
    # and it is a note page like any other: an edit here goes to the new note
    _edit(ws, owner_id, page, [{"op": "set", "id": child, "content": "Edited once more."}])
    _sync(me, ws)
    assert "Edited once more." in fake.data(LIB, new)["note"]


def test_an_undo_of_a_synced_note_delete_restores_the_note_in_zotero(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    root = _region_root(ws, keys["note"])
    kids = _kids(ws, root)
    _edit(ws, owner_id, page, [{"op": "delete", "id": root}])
    _sync(me, ws)
    assert fake.data(LIB, keys["note"]).get("deleted")
    # Ctrl+Z: the same blocks again, the same ids
    _edit(ws, owner_id, page, [{"op": "insert", "id": root, "parent": page, "content": "# Key points",
                                "props": {"zotero_note": item_uri(LIB, keys["note"])}}]
          + [{"op": "insert", "id": bid, "parent": root, "content": content} for bid, content in kids])
    notes = len([d for d in fake.items(LIB) if d.get("itemType") == "note"])
    _sync(me, ws)
    assert not fake.data(LIB, keys["note"]).get("deleted") and "Self-attention" in _note(fake, keys["note"])
    assert len([d for d in fake.items(LIB) if d.get("itemType") == "note"]) == notes  # never made twice
    assert _rows(ws, "note")[keys["note"]]["state"] == "live"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


# --- a backup restored over a note page -----------------------------------------------------------

def _page_words(ws, page) -> str:
    return " ".join(c for _, c in _kids(ws, page))


@pytest.mark.parametrize("edit, sent", [
    ("<h1>Ideas for the next project</h1><p>Try dense attention instead.</p>", True),  # new words: the restore wins
    ("<h2>Ideas for the next project</h2><ul><li>Try sparse attention on long documents.</li></ul>", False),  # shape
])
def test_a_note_page_zotero_changed_since_a_backup_takes_the_restored_note_unless_it_reads_alike(
        me, made, fake, owner_id, edit, sent):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["standalone_note"])
    assert "sparse" in _page_words(ws, page)
    snapshot = ws_backup.create(ws, label="before", by=owner_id)
    fake.edit(LIB, keys["standalone_note"], note=f'<div data-schema-version="9">{edit}</div>')
    _sync(me, ws)
    ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snapshot["name"]), "replace", by=owner_id)
    since = len(fake.sent)
    _sync(me, ws)
    assert "sparse" in _page_words(ws, page) and "dense" not in _page_words(ws, page)  # the restore stands
    held = fake.data(LIB, keys["standalone_note"])["note"]
    if sent:
        assert _sent_keys(fake, since) == {keys["standalone_note"]}
        assert "sparse" in held and "dense" not in held
        assert "zotero_note" in {c["kind"] for c in _conflicts(me, ws)}  # Zotero's kept for "theirs"
    else:
        assert _sent_keys(fake, since) == set() and held == f'<div data-schema-version="9">{edit}</div>'
    assert not _rows(ws, "note")[keys["standalone_note"]]["detail"].get("restore")
    since = len(fake.sent)
    _sync(me, ws)
    assert _sent_keys(fake, since) == set()


# --- a first link of an imported note -------------------------------------------------------------

def _imported_workspace(owner_id, fake, keys) -> tuple[str, str]:
    """A workspace whose paper came from the RDF import (its note block
    marked with the memo's local reference, ``#item_77``)."""
    from gamma import workspaces
    ws = workspaces.create("Imported", owner_id)["id"]
    doc, _ = storage.store_pdf(ws, pdf_bytes())
    page = "zfImported"
    meta = {"title": "Attention is all you need", "authors": ["Ashish Vaswani", "Noam Shazeer"], "year": "2017",
            "venue": "Nature", "volume": "647", "pages": "1-10", "doi": "10.1038/s41586-000-00000-0",
            "arxiv_id": "", "publisher": "", "isbn": "", "source": "zotero"}
    zotero_local.create_page(ws, page_id=page, title="Attention is all you need", actor=owner_id, client="t",
                             props={"meta": meta, **doc_props(doc), "zotero_key": item_uri(LIB, keys["attention"])})
    _edit(ws, owner_id, page, [
        {"op": "insert", "id": "zfImpNote", "parent": page, "content": "# Key points",
         "props": {"zotero_note": "#item_77"}},
        {"op": "insert", "id": "zfImpN1", "parent": "zfImpNote", "content": "Read this **twice**, then *once more*."},
        {"op": "insert", "id": "zfImpN2", "parent": "zfImpNote", "content": "Self-attention"},
        {"op": "insert", "id": "zfImpN3", "parent": "zfImpNote", "content": "Positional encoding"}])
    return ws, page


def test_a_first_link_pairs_an_imported_note_zotero_edited_since_and_doubles_it_nowhere(me, made, fake, owner_id,
                                                                                         quiet):
    keys = made["keys"]
    html = fake.data(LIB, keys["note"])["note"]
    fake.edit(LIB, keys["note"], note=html.replace("</ul>", "<li>Multi-head</li></ul>"))
    ws, page = _imported_workspace(owner_id, fake, keys)
    r = me.post("/api/zotero/links", json={"library": LIB, "workspace_id": ws})
    assert r.status_code == 201, r.text
    _sync(me, ws)
    _sync(me, ws)
    assert _rows(ws, "note")[keys["note"]]["block_id"] == "zfImpNote"
    assert "Multi-head" in [c for _, c in _kids(ws, "zfImpNote")]
    assert sum(1 for _, c in _kids(ws, page) if c == "# Key points") == 1
    assert not _gamma_notes(fake)


def test_a_first_link_holds_an_imported_note_zotero_rewrote_and_sends_it_nowhere(me, made, fake, owner_id, quiet):
    keys = made["keys"]
    fake.edit(LIB, keys["note"], note="<div data-schema-version=\"9\"><p>Entirely new thoughts on sparsity.</p></div>")
    ws, page = _imported_workspace(owner_id, fake, keys)
    r = me.post("/api/zotero/links", json={"library": LIB, "workspace_id": ws})
    assert r.status_code == 201, r.text
    _sync(me, ws)
    _sync(me, ws)
    assert not _gamma_notes(fake)
    assert _block(ws, "zfImpNote")["content"] == "# Key points"
    assert [c for _, c in _kids(ws, "zfImpNote")] == ["Read this **twice**, then *once more*.", "Self-attention",
                                                      "Positional encoding"]
    assert _rows(ws, "note")[keys["note"]]["block_id"] != "zfImpNote"  # Zotero's came as a region of its own
