"""How a Zotero link carries the way an author's name splits
(gamma/zotero_sync_items.py ``_Unit._names``, ``detail.names``; see
docs/dev/zotero_sync.md "Names"): a name compares given name first, so a
split is merged beside it, as a field is, through conflicts, receive-only
rounds, trashes, backup restores and forces; and a link stored before
release 72 (upgraded by ``migrations._v72_zotero_names``) reads its names
and a classed arXiv line as unchanged, yet takes each change made since on
either side. Against the fake Zotero (zotero_fake.py), rounds through
``POST …/sync?wait=1``. Accounts are ``zn_…``."""

import json

import pytest
from zotero_engine import (LIB, _block, _conflicts, _due, _edit, _force, _link, _page_of,  # noqa: F401
                           _paper, _rows, _sync, _writes, background, fake, idle, made, me, no_relocation,
                           owner_id)

from gamma import migrations, ops, ws_backup, zotero_local, zotero_web
from gamma.blocks_store import TRASH
from gamma.db import connect_pages_db
from gamma.zotero_web import item_uri

USER, PASSWORD = "zn_owner", "pw-zn_owner-123"

WRONG = {"creatorType": "author", "firstName": "Jorge Fernando Pereira", "lastName": "Campos"}
RIGHT = {"creatorType": "author", "firstName": "Jorge Fernando", "lastName": "Pereira Campos"}
OTHER = {"creatorType": "author", "firstName": "Floris", "lastName": "Den Hengst"}
SPLIT = "Pereira Campos, Jorge Fernando"  # RIGHT as the record writes it
JOINED = "Jorge Fernando Pereira Campos"  # WRONG as the record writes it, RIGHT as a sync read it before 72


def _authors(ws, page) -> list:
    return _block(ws, page)["props"]["meta"]["authors"]


def _set_meta(ws, owner_id, page, **changes) -> None:
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, **changes}}}])


def _thesis(fake, creators, **fields) -> str:
    return fake.add_item(LIB, {"itemType": "thesis", "title": "Data donation", "date": "2021",
                               "creators": [dict(c) for c in creators], **fields})


def _linked(me, fake, creators, **link) -> tuple[str, str, str]:
    key = _thesis(fake, creators)
    ws = _link(me, **link)
    _sync(me, ws)
    return key, ws, _page_of(ws, key)


def _restored(ws, owner_id, snapshot) -> None:
    ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snapshot["name"]), "replace", by=owner_id)


def test_a_name_zotero_splits_its_own_way_keeps_its_split_and_its_base_as_it_was(me, fake, owner_id):
    """A creator "Pereira Campos" / "Jorge Fernando" (the oracle's
    2228317-MJAQFMSC): the page's record keeps the split, made and pulled
    ("Pereira Campos, Jorge Fernando"); the bases hold the name as they
    did before it ("Jorge Fernando Pereira Campos"), so a link stored then
    reads nothing as changed and the page's trash here trashes the item."""
    author = {"creatorType": "author", "firstName": "Jorge Fernando", "lastName": "Pereira Campos"}
    key = fake.add_item(LIB, {"itemType": "thesis", "title": "The dynamics of data donation", "date": "2021",
                              "creators": [author], "university": "University of St Andrews"})
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, key)
    assert _block(ws, page)["props"]["meta"]["authors"] == ["Pereira Campos, Jorge Fernando"]
    row = _rows(ws, "item")[key]
    assert row["zbase"]["fields"]["authors"] == row["gbase"]["fields"]["authors"] == ["Jorge Fernando Pereira Campos"]
    fake.edit(LIB, key, creators=[author, {"creatorType": "author", "firstName": "Floris", "lastName": "Den Hengst"}])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, page)["props"]["meta"]["authors"] == ["Pereira Campos, Jorge Fernando", "Den Hengst, Floris"]
    assert _writes(fake, calls) == []
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id)
    _sync(me, ws)
    assert fake.data(LIB, key)["deleted"] == 1 and _rows(ws, "item")[key]["state"] == "trashed"
    assert _block(ws, page)["parent"] == TRASH


# A link as release 71 kept it: the creators Gamma's split at the last space
# wrote for a page's "Carrasco Kind, M." ("Carrasco Kind," / "M."), Zotero's
# suffix in the first name, a single-field "Last, First"; the page's names
# and both bases the item's fields joined "First Last", no splits kept.
_CREATORS_71 = [{"creatorType": "author", "firstName": "Carrasco Kind,", "lastName": "M."},
                {"creatorType": "author", "firstName": "Martin Luther, Jr.", "lastName": "King"},
                {"creatorType": "author", "name": "De Silva, Ana"}]
_NAMES_71 = ["Carrasco Kind, M.", "Martin Luther, Jr. King", "De Silva, Ana"]


def _stored_by_71(ws, key, page, owner_id, names=_NAMES_71, **fields) -> None:
    """The link and the page as release 71 left them, then the upgrade's
    step (``migrations._v72_zotero_names``): the page's authors ``names``,
    as 71 pulled them, both bases' too, the record's other ``fields`` (as
    71 read them) on the page and in both bases, no splits kept."""
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, **fields, "authors": names}}}])
    with connect_pages_db(ws) as conn:
        z, g, detail = conn.execute("SELECT zbase, gbase, detail FROM zotero_objects WHERE key = ?", (key,)).fetchone()
        z, g, detail = json.loads(z), json.loads(g), json.loads(detail)
        for base in (z, g):
            base["fields"].update(fields, authors=list(names))
        detail.pop("names", None)
        conn.execute("UPDATE zotero_objects SET zbase = ?, gbase = ?, detail = ? WHERE key = ?",
                     (json.dumps(z), json.dumps(g), json.dumps(detail), key))
        migrations._v72_zotero_names(conn)
        conn.commit()


@pytest.mark.parametrize("gone", ["trashed here", "trashed in Zotero", "removed in Zotero"])
def test_a_link_stored_before_the_names_read_as_now_trashes_and_removes_as_before(me, fake, owner_id, gone):
    """The bases of a link stored before the names were compared given name
    first are moved to that form at the upgrade, so the item, unchanged on
    both sides, reads as unchanged: a round after it sends nothing, and a
    trash here, a trash in Zotero and a removal in Zotero each go to the
    other side (none comes back with a zotero_deleted_edited row)."""
    key = fake.add_item(LIB, {"itemType": "journalArticle", "title": "Photometric redshifts", "date": "2013",
                              "creators": _CREATORS_71})
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, key)
    _stored_by_71(ws, key, page, owner_id)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and _block(ws, page)["props"]["meta"]["authors"] == _NAMES_71
    if gone == "trashed here":
        with connect_pages_db(ws) as conn:
            ops.trash_page(ws, conn, page, actor=owner_id)
    elif gone == "trashed in Zotero":
        fake.trash(LIB, key)
    else:
        fake.delete(LIB, key)
    _sync(me, ws)
    assert _block(ws, page)["parent"] == TRASH and not _conflicts(me, ws)
    if gone == "removed in Zotero":
        assert not [k for k, row in _rows(ws, "item").items() if row["page_id"] == page]
    else:
        assert fake.data(LIB, key)["deleted"] == 1


def test_a_name_split_anew_on_either_side_goes_to_the_other(me, fake, owner_id):
    """Names compare given name first, so how one splits is kept beside
    (``detail.names``): a split made anew here goes to Zotero, and outlasts
    Zotero's next change of the authors; one made anew in Zotero comes into
    the page; neither sends anything once it settled."""
    wrong = {"creatorType": "author", "firstName": "Jorge Fernando Pereira", "lastName": "Campos"}
    right = {"creatorType": "author", "firstName": "Jorge Fernando", "lastName": "Pereira Campos"}
    other = {"creatorType": "author", "firstName": "Floris", "lastName": "Den Hengst"}
    key = fake.add_item(LIB, {"itemType": "thesis", "title": "Data donation", "date": "2021", "creators": [wrong]})
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, key)
    assert _block(ws, page)["props"]["meta"]["authors"] == ["Jorge Fernando Pereira Campos"]
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {
        **meta, "authors": ["Pereira Campos, Jorge Fernando"]}}}])
    _sync(me, ws)
    assert fake.data(LIB, key)["creators"] == [right]
    fake.edit(LIB, key, creators=[right, other])
    _sync(me, ws)
    assert _block(ws, page)["props"]["meta"]["authors"] == ["Pereira Campos, Jorge Fernando", "Den Hengst, Floris"]
    # split back here, in the form a page read before join_name keeps the item's name in: it goes all the same
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {
        **meta, "authors": ["Jorge Fernando Pereira Campos", "Den Hengst, Floris"]}}}])
    _sync(me, ws)
    assert fake.data(LIB, key)["creators"] == [wrong, other]
    fake.edit(LIB, key, creators=[right, other])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, page)["props"]["meta"]["authors"] == ["Pereira Campos, Jorge Fernando", "Den Hengst, Floris"]
    assert _writes(fake, calls) == []
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and not _conflicts(me, ws)


def _decided(me, ws) -> list:
    return [c for c in me.get(f"/api/zotero/links/{ws}/conflicts?resolved=1").json()["conflicts"] if c["resolved"]]


def test_a_restore_a_force_and_a_first_link_read_a_page_as_the_item_it_made(me, fake, owner_id):
    """A name typed "Last, First" goes out split at its comma and compares
    as it reads back, and an arXiv line with its class compares as the
    record reads it: where both sides are compared without a base (a
    workspace restored from a backup, a force, a first link), the page and
    its item read alike, nothing is sent or decided, and a trash in Zotero
    afterwards trashes the page."""
    ws = _link(me)
    _sync(me, ws)
    page = _paper(ws, "zxAbbott", "Dark Energy Survey", meta={"authors": ["Abbott, T. M. C.", "Ada Lovelace"]})
    _sync(me, ws)
    [key] = [k for k, row in _rows(ws, "item").items() if row["page_id"] == page]
    assert fake.data(LIB, key)["creators"][0] == {"creatorType": "author", "firstName": "T. M. C.",
                                                  "lastName": "Abbott"}
    fake.edit(LIB, key, extra="arXiv:1002.2136 [astro-ph]")
    _sync(me, ws)
    assert _block(ws, page)["props"]["meta"]["arxiv_id"] == "1002.2136"
    for direction in ("pull", "push"):
        _force(me, ws, direction)
        assert not _decided(me, ws)
        assert _block(ws, page)["props"]["meta"]["authors"] == ["Abbott, T. M. C.", "Ada Lovelace"]
    with connect_pages_db(ws) as conn:  # what a workspace restore does to the rows (zotero_sync._own_library)
        conn.execute("UPDATE zotero_objects SET gsig = '', gbase = '{}' WHERE kind = 'item'")
        conn.execute("UPDATE zotero_state SET local_cursor = ''")
        conn.commit()
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    fake.trash(LIB, key)
    _sync(me, ws)
    assert _block(ws, page)["parent"] == TRASH and not _conflicts(me, ws)


def test_a_first_link_of_a_workspace_with_gammas_version_reads_its_pages_as_their_items(me, fake, owner_id):
    """A workspace whose page came out of Gamma's RDF export into Zotero
    ("Abbott, T. M. C." split at its comma, an arXiv line with its class),
    linked afresh with Gamma's version taken: nothing to send or decide,
    and Zotero's trash of the item trashes the page."""
    from gamma import workspaces

    key = fake.add_item(LIB, {"itemType": "journalArticle", "title": "IA", "date": "2010", "DOI": "10.1/ia",
                              "extra": "arXiv:1002.2136 [astro-ph]",
                              "creators": [{"creatorType": "author", "firstName": "T. M. C.", "lastName": "Abbott"}]})
    ws = workspaces.create("Existing", owner_id)["id"]
    _paper(ws, "zxIA", "IA", zotero_key=item_uri(LIB, key),
           meta={"title": "IA", "year": "2010", "doi": "10.1/ia", "arxiv_id": "1002.2136",
                 "authors": ["Abbott, T. M. C."]})
    _link(me, workspace_id=ws, adopt="mine")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and not _decided(me, ws)
    fake.trash(LIB, key)
    _sync(me, ws)
    assert _block(ws, "zxIA")["parent"] == TRASH


def test_an_arxiv_line_with_its_class_reads_as_the_id(me, fake, owner_id):
    """Zotero's arXiv translator writes the id's primary class after it: a
    class added in Zotero keeps the page's id, and a base stored when that
    line read as no id is read as it reads now while the item still holds
    it, so a trash here trashes the item."""
    key = fake.add_item(LIB, {"itemType": "journalArticle", "title": "IA", "date": "2010", "extra": "arXiv: 1002.2136",
                              "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}]})
    classed = fake.add_item(LIB, {"itemType": "journalArticle", "title": "IB", "date": "2010",
                                  "extra": "arXiv:1003.0001 [hep-th]",
                                  "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}]})
    ws = _link(me)
    _sync(me, ws)
    page, other = _page_of(ws, key), _page_of(ws, classed)
    fake.edit(LIB, key, extra="arXiv:1002.2136 [astro-ph]")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _block(ws, page)["props"]["meta"]["arxiv_id"] == "1002.2136" and _writes(fake, calls) == []
    meta = _block(ws, other)["props"]["meta"]
    assert meta["arxiv_id"] == "1003.0001"
    _edit(ws, owner_id, other, [{"op": "set", "id": other, "props": {"meta": {**meta, "arxiv_id": ""}}}])
    with connect_pages_db(ws) as conn:  # as release 71 stored the row, and the page read the line
        z, g, detail = (json.loads(x) for x in conn.execute(
            "SELECT zbase, gbase, detail FROM zotero_objects WHERE key = ?", (classed,)).fetchone())
        z["fields"]["arxiv_id"] = g["fields"]["arxiv_id"] = ""
        detail.pop("names", None)
        conn.execute("UPDATE zotero_objects SET zbase = ?, gbase = ?, detail = ? WHERE key = ?",
                     (json.dumps(z), json.dumps(g), json.dumps(detail), classed))
        conn.commit()
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, other, actor=owner_id)
    _sync(me, ws)
    assert fake.data(LIB, classed)["deleted"] == 1 and not _conflicts(me, ws)


# --- a split merged as a field is -------------------------------------------------------

@pytest.mark.parametrize("mode", ["two-way", "pull"])
def test_a_name_split_anew_here_while_zotero_changes_the_authors_is_both_sides_changing_them(
        me, fake, owner_id, mode):
    """The page splits a name anew while Zotero adds a co-author: both
    changed the authors, as any edit of them here would be, so Gamma's go
    and a zotero_field row keeps Zotero's; a receive-only link keeps
    Gamma's here and sends them once it sends again. Taking "theirs" brings
    Zotero's list in as its record writes it."""
    key, ws, page = _linked(me, fake, [WRONG], mode=mode)
    _set_meta(ws, owner_id, page, authors=[SPLIT])
    fake.edit(LIB, key, creators=[WRONG, OTHER])
    _sync(me, ws)
    assert _authors(ws, page) == [SPLIT]
    [conflict] = _conflicts(me, ws, "zotero_field")
    if mode == "pull":
        assert fake.data(LIB, key)["creators"] == [WRONG, OTHER]
        assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
        _sync(me, ws)
    assert fake.data(LIB, key)["creators"] == [RIGHT]
    if mode == "two-way":
        r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
        assert r.status_code == 200
        assert _authors(ws, page) == [JOINED, "Den Hengst, Floris"]
        _sync(me, ws)
        assert fake.data(LIB, key)["creators"] == [WRONG, OTHER]


def test_a_split_zotero_made_the_same_way_while_changing_the_authors_is_no_conflict(me, fake, owner_id):
    key, ws, page = _linked(me, fake, [WRONG])
    _set_meta(ws, owner_id, page, authors=[SPLIT])
    fake.edit(LIB, key, creators=[RIGHT, OTHER])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _authors(ws, page) == [SPLIT, "Den Hengst, Floris"]
    assert _writes(fake, calls) == [] and not _conflicts(me, ws)


def test_a_split_made_on_a_receive_only_link_waits_as_a_field_does(me, fake, owner_id):
    """Zotero edits another field meanwhile, so the round reads the item:
    the split is kept here, not taken as synced, and goes once the link
    sends again."""
    key, ws, page = _linked(me, fake, [WRONG], mode="pull")
    _set_meta(ws, owner_id, page, authors=[SPLIT])
    fake.edit(LIB, key, title="Data donation (revised)")
    _sync(me, ws)
    assert _authors(ws, page) == [SPLIT] and fake.data(LIB, key)["creators"] == [WRONG]
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    _sync(me, ws)
    assert fake.data(LIB, key)["creators"] == [RIGHT] and not _conflicts(me, ws)


def test_a_split_made_anew_beats_a_trash_on_the_other_side(me, fake, owner_id):
    """An edit beats a delete, a name's split too: split here and trashed in
    Zotero, the item comes back with the split; split in Zotero and trashed
    here, the page comes back with it. Each leaves a zotero_deleted_edited
    row."""
    here, ws, page = _linked(me, fake, [WRONG])
    there = _thesis(fake, [WRONG], title="Other thesis")
    _sync(me, ws)
    other = _page_of(ws, there)
    _set_meta(ws, owner_id, page, authors=[SPLIT])
    fake.trash(LIB, here)
    fake.edit(LIB, there, creators=[RIGHT])
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, other, actor=owner_id)
    _sync(me, ws)
    _sync(me, ws)
    assert _block(ws, page)["parent"] != TRASH and not fake.data(LIB, here).get("deleted")
    assert fake.data(LIB, here)["creators"] == [RIGHT]
    assert _block(ws, other)["parent"] != TRASH and not fake.data(LIB, there).get("deleted")
    assert _authors(ws, other) == [SPLIT]
    assert len(_conflicts(me, ws, "zotero_deleted_edited")) == 2


HARMELEN = {"creatorType": "author", "firstName": "Frank van", "lastName": "Harmelen"}  # Zotero's own split


@pytest.mark.parametrize("since", ["split here and sent", "a slip sent", "split in Zotero"])
def test_a_backup_restore_undoes_a_split_as_any_field(me, fake, owner_id, since):
    """The restored page wins its differences: a name split anew on either
    side since the backup goes back to the backup's split in Zotero, and
    Zotero's split stays in a zotero_field row; a name nobody split anew
    keeps Zotero's own split."""
    slip = {"creatorType": "author", "firstName": "Jorge", "lastName": "Fernando Pereira Campos"}
    backed = RIGHT if since == "a slip sent" else WRONG
    key, ws, page = _linked(me, fake, [backed, HARMELEN])
    before = _authors(ws, page)
    snapshot = ws_backup.create(ws, label="before", by=owner_id)
    if since == "split in Zotero":
        fake.edit(LIB, key, creators=[RIGHT, HARMELEN])
    else:
        _set_meta(ws, owner_id, page, authors=[SPLIT if since == "split here and sent" else
                                               "Fernando Pereira Campos, Jorge", "Frank van Harmelen"])
    _sync(me, ws)
    assert fake.data(LIB, key)["creators"] == [slip if since == "a slip sent" else RIGHT, HARMELEN]
    _restored(ws, owner_id, snapshot)
    _sync(me, ws)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    assert _authors(ws, page) == before and fake.data(LIB, key)["creators"] == [backed, HARMELEN]
    assert [c["mine"].split(":")[0] for c in _conflicts(me, ws, "zotero_field")] == ["Authors"]


def test_a_force_takes_the_forced_sides_split(me, fake, owner_id):
    """A force push sends the page's split where it moved since it last
    synced and leaves every other creator as Zotero splits it; a force
    pull takes Zotero's names where its record writes them otherwise."""
    kept = {"creatorType": "author", "firstName": "Frank van", "lastName": "Harmelen"}
    key, ws, page = _linked(me, fake, [WRONG, kept], mode="pull")
    _set_meta(ws, owner_id, page, authors=[SPLIT, "Frank van Harmelen"])
    _sync(me, ws)
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    _force(me, ws, "push")
    assert fake.data(LIB, key)["creators"] == [RIGHT, kept]
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    fake.edit(LIB, key, creators=[WRONG, kept])
    _set_meta(ws, owner_id, page, authors=[SPLIT, "Frank van Harmelen"])
    calls = len(fake.calls)
    _force(me, ws, "pull")
    assert _authors(ws, page) == [JOINED, "Frank van Harmelen"] and _writes(fake, calls) == []


# --- a link stored before release 72 ------------------------------------------------------

@pytest.mark.parametrize("zotero_edits", [False, True])
def test_a_name_split_anew_here_goes_on_a_link_stored_before_72(me, fake, owner_id, zotero_edits):
    """71 kept no split: the step gives the row the page's as it last
    synced (from its Gamma base), so "Pereira Campos, Jorge Fernando" typed
    here after the upgrade for the item's "Campos" / "Jorge Fernando
    Pereira" goes to Zotero, also when Zotero edits the item meanwhile, and
    outlasts Zotero's next change of the authors."""
    key, ws, page = _linked(me, fake, [WRONG])
    _stored_by_71(ws, key, page, owner_id, [JOINED])
    calls = len(fake.calls)
    _sync(me, ws)  # the first round after the upgrade
    assert _writes(fake, calls) == []
    _set_meta(ws, owner_id, page, authors=[SPLIT])
    if zotero_edits:
        fake.edit(LIB, key, title="Data donation (revised)")
    _sync(me, ws)
    assert fake.data(LIB, key)["creators"] == [RIGHT]
    fake.edit(LIB, key, creators=[RIGHT, OTHER])
    _sync(me, ws)
    assert _authors(ws, page) == [SPLIT, "Den Hengst, Floris"] and not _conflicts(me, ws)


@pytest.mark.parametrize("how", ["a Zotero edit", "a force pull"])
def test_a_page_linked_before_72_takes_its_names_as_the_record_writes_them_once_its_item_is_read(
        me, fake, owner_id, how):
    """71 read Zotero's "Jorge Fernando" / "Pereira Campos" as "Jorge
    Fernando Pereira Campos": the next round that reads the item (or a
    force pull) writes the record's "Pereira Campos, Jorge Fernando" into
    the page, which compares alike, so nothing goes to Zotero."""
    key, ws, page = _linked(me, fake, [RIGHT, OTHER])
    _stored_by_71(ws, key, page, owner_id, [JOINED, "Floris Den Hengst"])
    _sync(me, ws)
    assert _authors(ws, page) == [JOINED, "Floris Den Hengst"]
    if how == "a Zotero edit":
        fake.edit(LIB, key, title="Data donation (revised)")
    calls = len(fake.calls)
    if how == "a Zotero edit":
        _sync(me, ws)
    else:
        _force(me, ws, "pull")
    assert _authors(ws, page) == [SPLIT, "Den Hengst, Floris"]
    assert _writes(fake, calls) == [] and not _conflicts(me, ws)
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []


def test_a_force_push_of_a_link_stored_before_72_rewrites_no_creator(me, fake, owner_id):
    harmelen = {"creatorType": "author", "firstName": "Frank van", "lastName": "Harmelen"}
    key, ws, page = _linked(me, fake, [*_CREATORS_71, RIGHT, harmelen])
    _stored_by_71(ws, key, page, owner_id, [*_NAMES_71, JOINED, "Frank van Harmelen"])
    calls = len(fake.calls)
    _force(me, ws, "push")
    assert _writes(fake, calls) == []


def test_a_classed_arxiv_line_reaches_a_page_linked_before_72(me, fake, owner_id):
    """71 read "arXiv:1002.2136 [astro-ph]" as no id. A page linked then
    takes the id as Zotero's from its item's next round, one its item held
    all along and one Zotero adds after the upgrade, and nothing goes; a
    line added in Zotero is an edit of the item, which beats a trash here.
    A backup restored after the upgrade keeps Zotero's line."""
    held = fake.add_item(LIB, {"itemType": "journalArticle", "title": "IA", "date": "2010",
                               "extra": "arXiv:1002.2136 [astro-ph]", "creators": [OTHER]})
    added = fake.add_item(LIB, {"itemType": "journalArticle", "title": "IB", "date": "2010", "creators": [OTHER]})
    gone = fake.add_item(LIB, {"itemType": "journalArticle", "title": "IC", "date": "2010", "creators": [OTHER]})
    still = fake.add_item(LIB, {"itemType": "journalArticle", "title": "ID", "date": "2010",
                                "extra": "arXiv:1003.0001 [hep-th]", "creators": [OTHER]})
    ws = _link(me)
    _sync(me, ws)
    pages = {key: _page_of(ws, key) for key in (held, added, gone, still)}
    for key, page in pages.items():
        _stored_by_71(ws, key, page, owner_id, ["Floris Den Hengst"], arxiv_id="")
    _sync(me, ws)
    snapshot = ws_backup.create(ws, label="upgraded", by=owner_id)
    fake.edit(LIB, held, title="IA (revised)")
    fake.edit(LIB, added, extra="arXiv:1003.0002 [hep-th]")
    fake.edit(LIB, gone, extra="arXiv:1003.0003 [hep-th]")
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, pages[gone], actor=owner_id)
    calls = len(fake.calls)
    _sync(me, ws)
    meta = {key: _block(ws, page)["props"]["meta"] for key, page in pages.items()}
    assert (meta[held]["arxiv_id"], meta[added]["arxiv_id"], meta[gone]["arxiv_id"]) == (
        "1002.2136", "1003.0002", "1003.0003")
    assert _block(ws, pages[gone])["parent"] != TRASH and not fake.data(LIB, gone).get("deleted")
    assert [c["kind"] for c in _conflicts(me, ws)] == ["zotero_deleted_edited"]
    assert _writes(fake, calls) == []
    _restored(ws, owner_id, snapshot)
    _sync(me, ws)
    assert fake.data(LIB, still)["extra"] == "arXiv:1003.0001 [hep-th]"
    assert _block(ws, pages[still])["props"]["meta"]["arxiv_id"] == "1003.0001"


def _answer_lost_once(monkeypatch):
    real = zotero_web.ZoteroClient.write
    state = {"lost": False}

    def write(self, library, kind, objects):
        out = real(self, library, kind, objects)
        if not state["lost"] and any(o.get("version") == 0 for o in objects):
            state["lost"] = True
            raise zotero_web.ZoteroError("no answer", 504)  # saved, but the answer never came
        return out

    monkeypatch.setattr(zotero_web.ZoteroClient, "write", write)


@pytest.mark.parametrize("gone", ["trashed here", "trashed in Zotero"])
def test_a_create_pending_across_the_upgrade_is_confirmed_as_it_reads_now(me, fake, owner_id, monkeypatch, gone):
    """A create 71 sent for "Lovelace, Ada" (split at the last space:
    "Lovelace," / "Ada") whose answer was lost: the step moves what it was
    sent as (``detail.sent``), which its confirmation takes as both bases,
    so a trash on either side afterwards goes to the other."""
    ws = _link(me)
    _sync(me, ws)
    _answer_lost_once(monkeypatch)
    page = "znLost"
    zotero_local.create_page(ws, page_id=page, title="A lost answer", actor=owner_id, client="t",
                             props={"meta": {"title": "A lost answer", "authors": ["Lovelace, Ada"], "year": "2020",
                                             "kind": "journalArticle", "source": "manual"}})
    _sync(me, ws, ok=False)
    key = next(k for k, v in _rows(ws, "item").items() if v["page_id"] == page)
    fake.edit(LIB, key, creators=[{"creatorType": "author", "firstName": "Lovelace,", "lastName": "Ada"}])
    with connect_pages_db(ws) as conn:  # as 71 wrote the row
        detail = json.loads(conn.execute("SELECT detail FROM zotero_objects WHERE key = ?", (key,)).fetchone()[0])
        detail["sent"]["fields"]["authors"] = ["Lovelace, Ada"]
        detail.pop("names")
        conn.execute("UPDATE zotero_objects SET detail = ? WHERE key = ?", (json.dumps(detail), key))
        migrations._v72_zotero_names(conn)
        conn.commit()
    if gone == "trashed here":
        with connect_pages_db(ws) as conn:
            ops.trash_page(ws, conn, page, actor=owner_id)
    else:
        fake.trash(LIB, key)
    _due(ws)
    _sync(me, ws)
    assert _block(ws, page)["parent"] == TRASH and fake.data(LIB, key).get("deleted") and not _conflicts(me, ws)
