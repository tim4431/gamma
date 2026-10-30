"""The Zotero link's engine under the conditions that break sync engines
(gamma/zotero_sync.py) against the fake Zotero: Gamma edits made while a unit
or the collections pass decides (the compare-and-set writes nothing on either
side, and both edits land the round after, which comes by itself a few
seconds later); Zotero's library moving at each step of a round (between the
version probe and the listing, the listing and the reads by key, a read and
its write); a highlight moved to another page; receive-only rounds, by the
link's direction or by a key that cannot write, then two-way; another group
member's annotation edited both here and in Zotero. Every test ends with a
round that writes nothing (``zotero_idle``). Accounts are ``zh_…``."""

import json

import pytest
from conftest import login
from zotero_engine import (GROUP, LIB, WRITES, Crash, _block, _conflicts, _edit, _highlight, _kids, _link,  # noqa: F401
                           _page_of, _rows, _sync, _writes, background, drop_pending, fake, idle, made, me,
                           no_relocation, owner_id)
from zotero_fake import FakeZotero
from zotero_fixtures import TITLE_RECT, pdf_bytes, standard_library
from zotero_idle import assert_idle

from gamma import (ops, storage, zotero_account, zotero_local, zotero_sync, zotero_sync_blocks, zotero_sync_folders,
                   zotero_sync_items, zotero_sync_rows)
from gamma.blocks_store import FOLDERS, LABELS, tree_rows
from gamma.db import connect_pages_db
from gamma.zotero_web import object_key

USER, PASSWORD = "zh_owner", "pw-zh_owner-123"


def _link_ops(ws, since: dict) -> list:
    """The ops the link logged on any page since ``since`` ({page: seq})."""
    with connect_pages_db(ws) as conn:
        return [(p, s) for p, s in conn.execute("SELECT page_id, seq FROM page_ops WHERE client = 'zotero'")
                if s > since.get(p, 0)]


def _seqs(ws) -> dict:
    with connect_pages_db(ws) as conn:
        return dict(conn.execute("SELECT page_id, MAX(seq) FROM page_ops GROUP BY page_id").fetchall())


def _soon(ws) -> bool:
    """Whether the link asked its clock for a round within a few seconds."""
    import time
    due = zotero_sync._clock.pending.get(ws)
    return due is not None and due <= time.monotonic() + 6


# --- Gamma edits while a round decides (the compare-and-set) --------------------------------

def _meanwhile(monkeypatch, cls, name, edit):
    """Run ``edit()`` once, right after ``cls.name`` decided."""
    real = getattr(cls, name)
    done = []

    def wrapped(self, *a, **kw):
        out = real(self, *a, **kw)
        if not done and edit(self) is not False:
            done.append(1)
        return out

    monkeypatch.setattr(cls, name, wrapped)
    return lambda: monkeypatch.setattr(cls, name, real)


def test_a_highlight_edited_while_its_unit_decides_writes_nothing_and_both_edits_land_next(
        me, made, fake, owner_id, monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    block = _rows(ws, "annotation")[keys["highlight"]]["block_id"]
    fake.edit(LIB, keys["highlight"], annotationComment="Zotero's comment")

    def typed(unit):
        if unit.page_id != page:
            return False
        _edit(ws, owner_id, page, [{"op": "set", "id": block, "props": {"color": "#a28ae5"}}])

    undo = _meanwhile(monkeypatch, zotero_sync_items._Unit, "decide", typed)
    drop_pending(ws)
    calls, seqs = len(fake.calls), _seqs(ws)
    _sync(me, ws)
    undo()
    assert _writes(fake, calls) == [] and _link_ops(ws, seqs) == []  # nothing on either side
    assert _block(ws, block)["content"] == "great point" and _soon(ws)  # the next round asked for, seconds on
    _sync(me, ws)
    assert _block(ws, block)["content"] == "Zotero's comment"
    data = fake.data(LIB, keys["highlight"])
    assert data["annotationColor"] == "#a28ae5" and data["annotationComment"] == "Zotero's comment"

    # with the page in the change feed too, its page entry decides it afresh in the same round
    fake.edit(LIB, keys["highlight"], annotationComment="Zotero's, again")
    _edit(ws, owner_id, page, [{"op": "set", "id": _rows(ws, "annotation")[keys["underline"]]["block_id"],
                                "props": {"color": "#5fb236"}}])
    undo = _meanwhile(monkeypatch, zotero_sync_items._Unit, "decide", lambda unit: False if unit.page_id != page
                      else _edit(ws, owner_id, page, [{"op": "set", "id": block, "props": {"color": "#ffd400"}}]))
    _sync(me, ws)
    undo()
    data = fake.data(LIB, keys["highlight"])
    assert data["annotationColor"] == "#ffd400" and _block(ws, block)["content"] == "Zotero's, again"
    assert fake.data(LIB, keys["underline"])["annotationColor"] == "#5fb236"


def test_a_note_edited_while_its_unit_decides_is_decided_again_with_zoteros_edit(me, made, fake, owner_id,
                                                                                 monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    (first, _), (second, _) = _kids(ws, root)[:2]
    fake.edit(LIB, keys["note"], note=fake.data(LIB, keys["note"])["note"].replace("Self-attention",
                                                                                   "Self-attention (Zotero)"))

    def typed(unit):
        if unit.page_id != page:
            return False
        _edit(ws, owner_id, page, [{"op": "set", "id": first, "content": "Typed while it decided."}])

    undo = _meanwhile(monkeypatch, zotero_sync_items._Unit, "decide", typed)
    calls, seqs = len(fake.calls), _seqs(ws)
    _sync(me, ws)
    undo()
    assert _writes(fake, calls) == [] and _link_ops(ws, seqs) == []
    _sync(me, ws)
    # both changed the note: Gamma's goes (with its typing), Zotero's is kept as the conflict's "theirs"
    assert "Typed while it decided." in fake.data(LIB, keys["note"])["note"]
    [conflict] = _conflicts(me, ws, "zotero_note")
    assert "Self-attention (Zotero)" in conflict["theirs"]
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    _sync(me, ws)
    assert "Self-attention (Zotero)" in fake.data(LIB, keys["note"])["note"]
    assert _block(ws, second)["content"] != ""


def test_a_folder_renamed_while_the_collections_pass_decides_is_decided_again(me, made, fake, owner_id,
                                                                            monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        folders = {name: fid for fid, _, name, _ in tree_rows(conn, FOLDERS)}
    fake.edit(LIB, keys["ml"], name="Machine learning")

    def renamed(plan):
        ops.commit_ops(ws, FOLDERS, [{"op": "set", "id": folders["Reading"], "content": "Reading list"}],
                       actor=owner_id, client="t")

    undo = _meanwhile(monkeypatch, zotero_sync_folders._Folders, "push_new", renamed)
    calls, seqs = len(fake.calls), _seqs(ws)
    _sync(me, ws)
    undo()
    assert _writes(fake, calls) == [] and _link_ops(ws, seqs) == [] and _soon(ws)
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        names = {name for _, _, name, _ in tree_rows(conn, FOLDERS)}
    assert {"Machine learning", "Reading list"} <= names
    assert fake.data(LIB, keys["reading"])["name"] == "Reading list"


# --- Zotero's library moving at each step of a round -----------------------------------------

def test_zotero_changing_at_each_step_of_a_round_loses_nothing(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["conference"])
    meta = _block(ws, page)["props"]["meta"]
    _edit(ws, owner_id, page, [{"op": "set", "id": page, "props": {"meta": {**meta, "pages": "1-200"}}}])
    fake.edit(LIB, keys["ppo"], title="PPO, edited before the round")
    # between the version probe and the listing; between the listing and the read by key; between that read
    # and the write; while the collections are read
    fake.on_request("items?limit=1&format=versions", lambda: fake.edit(LIB, keys["deep_learning"], date="2017"),
                    method="GET")
    fake.on_request("itemKey=", lambda: fake.edit(LIB, keys["ppo"], title="PPO, edited mid-read"),
                    method="GET")  # (the first read by key: items are read fifty at a time)
    fake.on_request(f"/{LIB}/items", lambda: fake.edit(LIB, keys["conference"], date="2021"), method="POST")
    fake.on_request("collectionKey=", lambda: fake.edit(LIB, keys["reading"], name="Read next"), method="GET")
    fake.edit(LIB, keys["ml"], name="ML, renamed")
    for _ in range(3):
        _sync(me, ws)
    assert _block(ws, _page_of(ws, keys["ppo"]))["props"]["meta"]["title"] == "PPO, edited mid-read"
    assert _block(ws, _page_of(ws, keys["deep_learning"]))["props"]["meta"]["year"] == "2017"
    conference = _block(ws, page)["props"]["meta"]
    assert conference["year"] == "2021" and conference["pages"] == "1-200"
    data = fake.data(LIB, keys["conference"])
    assert data["date"] == "2021" and data["pages"] == "1-200"
    with connect_pages_db(ws) as conn:
        names = {name for _, _, name, _ in tree_rows(conn, FOLDERS)}
    assert {"ML, renamed", "Read next"} <= names


# --- a highlight moved to another page ------------------------------------------------------

@pytest.mark.parametrize("edited", [False, True])
def test_a_highlight_moved_to_another_page_leaves_its_annotation_and_fails_no_round(me, made, fake, owner_id,
                                                                                    edited):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page, other = _page_of(ws, keys["attention"]), _page_of(ws, keys["thesis"])
    block = _rows(ws, "annotation")[keys["highlight"]]["block_id"]
    with connect_pages_db(ws) as conn:
        ops.move_across_pages(ws, conn, block, other, "zz", page, other, actor=owner_id, client="t")
    if edited:
        fake.edit(LIB, keys["highlight"], annotationColor="#2ea8e5")
    _sync(me, ws)
    _sync(me, ws)
    row = _rows(ws, "annotation")[keys["highlight"]]
    assert _block(ws, block)["page"] == other  # the moved block stays where it went, Gamma's own now
    if edited:  # Zotero changed it after it left: it comes back on its page (an edit beats a delete)
        assert row["state"] == "live" and row["block_id"] != block and _block(ws, row["block_id"])["page"] == page
        assert _block(ws, row["block_id"])["props"]["color"].startswith("#2ea8e5")
        assert not fake.data(LIB, keys["highlight"]).get("deleted")
    else:  # gone from its page: trashed in Zotero
        assert row["state"] == "trashed" and fake.data(LIB, keys["highlight"]).get("deleted")


# --- receive only, then two-way ----------------------------------------------------------------

def _gamma_changes(ws, owner, keys) -> None:
    """One of each kind of Gamma change: a new paper with its PDF, a record
    edit, a new highlight, a colour, a note edited, a new folder and a
    label renamed, a page deleted."""
    doc_id, _ = storage.store_pdf(ws, pdf_bytes("A paper written while receiving only."))
    zotero_local.create_page(ws, page_id="zhNewPaper", title="Written while receiving", actor=owner, client="t",
                             props={"meta": {"title": "Written while receiving", "year": "2025", "source": "manual"},
                                    "doc_id": doc_id, "original_filename": "receiving.pdf"})
    conference = _page_of(ws, keys["conference"])
    with connect_pages_db(ws) as conn:
        meta = json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?",
                                       (conference,)).fetchone()[0])["meta"]
        labels = {name: lid for lid, name in conn.execute("SELECT id, content FROM unified_blocks "
                                                           "WHERE parent_id = ?", (LABELS,)).fetchall()}
    _edit(ws, owner, conference, [{"op": "set", "id": conference, "props": {"meta": {**meta, "pages": "1-99"}}}])
    page = _page_of(ws, keys["attention"])
    _edit(ws, owner, page, [
        {"op": "insert", "id": "zhMine", "parent": page,
         **_highlight([TITLE_RECT], color="#2ea8e5", quote="Attention Is All You Need")},
        {"op": "set", "id": _rows(ws, "annotation")[keys["highlight"]]["block_id"], "props": {"color": "#5fb236"}}])
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    _edit(ws, owner, page, [{"op": "set", "id": _kids(ws, root)[0][0], "content": "Edited while receiving."}])
    ops.commit_ops(ws, FOLDERS, [{"op": "insert", "id": "zhFolder", "parent": FOLDERS, "content": "Kept here"}],
                   actor=owner, client="t")
    ops.commit_ops(ws, LABELS, [{"op": "set", "id": labels["transformers"], "content": "attention models"}],
                   actor=owner, client="t")
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, _page_of(ws, keys["deep_learning"]), actor=owner, client="t")


def _gamma_changes_reached(fake, ws, keys) -> None:
    items = fake.items(LIB)
    [paper] = [d for d in items if d.get("title") == "Written while receiving"]
    [pdf] = [d for d in items if d.get("parentItem") == paper["key"]]
    assert pdf["md5"] and pdf["filename"] == "receiving.pdf"
    assert fake.data(LIB, keys["conference"])["pages"] == "1-99"
    mine = object_key(ws, LIB, "annotation", keys["pdf"], "zhMine", "")
    assert fake.data(LIB, mine)["annotationColor"] == "#2ea8e5"
    assert fake.data(LIB, keys["highlight"])["annotationColor"] == "#5fb236"
    assert "Edited while receiving." in fake.data(LIB, keys["note"])["note"]
    assert "Kept here" in {c["name"] for c in fake.collections(LIB)}
    assert {t["tag"] for t in fake.data(LIB, keys["attention"])["tags"]} == {"attention models", "attention"}
    assert fake.data(LIB, keys["deep_learning"]).get("deleted")


def test_receive_only_rounds_keep_every_kind_of_gamma_change_until_two_way(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 200
    _gamma_changes(ws, owner_id, keys)
    fake.edit(LIB, keys["ppo"], title="PPO, while Gamma only received")
    calls = len(fake.calls)
    for _ in range(2):
        _sync(me, ws)
    assert _writes(fake, calls) == []  # nothing went while receiving only...
    assert _block(ws, _page_of(ws, keys["ppo"]))["props"]["meta"]["title"] == "PPO, while Gamma only received"
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    _sync(me, ws)
    _sync(me, ws)
    _gamma_changes_reached(fake, ws, keys)  # ...and everything went once two-way


def test_a_key_that_regains_write_access_sends_what_its_receive_only_rounds_kept(owner_id, monkeypatch):
    fake = FakeZotero().install(monkeypatch)
    keys = standard_library(fake)["keys"]
    fake.keys[fake.api_key]["user"]["write"] = False
    zotero_account.connect(owner_id, fake.api_key)
    c = login(USER, PASSWORD)
    try:
        ws = _link(c)
        status = _sync(c, ws)
        assert status["mode"] == "pull"
        _gamma_changes(ws, owner_id, keys)
        calls = len(fake.calls)
        _sync(c, ws)
        assert _writes(fake, calls) == []
        fake.keys[fake.api_key]["user"]["write"] = True  # the person gave the key write access on zotero.org
        zotero_sync.forget_access(ws)
        assert _sync(c, ws)["mode"] == "two-way"
        _sync(c, ws)
        _gamma_changes_reached(fake, ws, keys)
        assert_idle(fake, ws)
    finally:
        for link in zotero_sync.list_links(owner_id):
            zotero_sync.remove_link(link["workspace_id"])
        zotero_account.disconnect(owner_id, revoke=False)


# --- another group member's annotation -------------------------------------------------------

def test_another_members_annotation_changed_on_both_sides_takes_theirs_and_keeps_one_conflict(
        me, made, fake, owner_id):
    from zotero_fixtures import CAROL
    keys = made["keys"]
    ws = _link(me, library=GROUP)
    _sync(me, ws)
    row = _rows(ws, "annotation")[keys["carols"]]
    page, block = row["page_id"], row["block_id"]
    _edit(ws, owner_id, page, [{"op": "set", "id": block, "content": "my reading of carol's",
                                "props": {"color": "#5fb236"}}])
    with fake.acting_as(CAROL):
        fake.edit(GROUP, keys["carols"], annotationColor="#2ea8e5")
    calls = len(fake.calls)
    for _ in range(3):
        _sync(me, ws)
        assert _writes(fake, calls) == []  # never written: it is carol's
    after = _block(ws, block)
    assert after["props"]["color"].startswith("#2ea8e5") and after["props"]["zotero_readonly"] == "locked"
    assert fake.libraries[GROUP].items[keys["carols"]]["annotationComment"] == "carol's reading"
    assert len(_conflicts(me, ws, "zotero_locked")) == 1
    # carol edits it again: still one open row, Zotero's taken again
    with fake.acting_as(CAROL):
        fake.edit(GROUP, keys["carols"], annotationComment="carol, later")
    _sync(me, ws)
    assert len(_conflicts(me, ws, "zotero_locked")) == 1 and _writes(fake, calls) == []


# --- a first link that binds a page's note -----------------------------------------------------

def test_a_first_link_that_binds_a_note_still_sends_the_pages_own_notes(me, made, fake, owner_id, monkeypatch):
    """The page's note pairs with Zotero's by its words, and the page's other
    notes go as "Notes from Gamma", though the link writes nothing on the
    page that would bring it back to the next round."""
    from gamma import workspaces
    from gamma.zotero_web import item_uri
    monkeypatch.setattr(zotero_sync_blocks, "NOTES_QUIET_S", -3600)
    keys = made["keys"]
    ws = workspaces.create("Notes before the link", owner_id)["id"]
    meta = {"title": "Proximal Policy Optimization Algorithms", "authors": ["John Schulman", "Filip Wolski"],
            "year": "2017", "arxiv_id": "1707.06347", "source": "manual"}
    zotero_local.create_page(ws, page_id="zhPpo", title="Proximal Policy Optimization Algorithms", actor=owner_id,
                             client="t", props={"meta": meta, "zotero_key": item_uri(LIB, keys["ppo"])})
    fake.add_item(LIB, {"itemType": "note", "parentItem": keys["ppo"],
                        "note": "<h1>Clipping</h1><p>It bounds the step.</p>"})
    _edit(ws, owner_id, "zhPpo", [{"op": "insert", "id": "zhClip", "parent": "zhPpo", "content": "# Clipping"},
                                  {"op": "insert", "id": "zhClip1", "parent": "zhClip",
                                   "content": "It bounds the step."},
                                  {"op": "insert", "id": "zhMine", "parent": "zhPpo", "content": "My own thought"}])
    r = me.post("/api/zotero/links", json={"library": LIB, "workspace_id": ws, "adopt": "theirs"})
    assert r.status_code == 201, r.text
    _sync(me, ws)
    notes = [d for d in fake.items(LIB) if d.get("itemType") == "note" and d.get("parentItem") == keys["ppo"]]
    assert len(notes) == 2 and any("My own thought" in d["note"] and "Notes from Gamma" in d["note"] for d in notes)
    assert sum("Clipping" in d["note"] for d in notes) == 1


# --- an upload Zotero already had the bytes of ------------------------------------------------

def test_a_pdf_zotero_already_holds_the_bytes_of_is_recorded_at_its_version_and_never_fetched_back(
        me, made, fake, owner_id):
    """Zotero answers the upload's authorization with ``{exists: 1}`` (it
    has those bytes already, as another attachment's file) and moves the
    attachment's version without a register: the engine reads it back."""
    ws = _link(me)
    _sync(me, ws)
    doc_id, _ = storage.store_pdf(ws, pdf_bytes("Supplementary material."))  # the supplement's own bytes
    zotero_local.create_page(ws, page_id="zhSame", title="Same bytes", actor=owner_id, client="t", props={
        "meta": {"title": "Same bytes", "year": "2024", "source": "manual"}, "doc_id": doc_id,
        "original_filename": "same.pdf"})
    calls = len(fake.calls)
    _sync(me, ws)
    [att] = [k for k, row in _rows(ws, "attachment").items() if row["page_id"] == "zhSame"]
    assert not any("/__upload/" in path for _, path, _ in fake.calls[calls:])  # no bytes went: Zotero had them
    row, data = _rows(ws, "attachment")[att], fake.data(LIB, att)
    assert row["version"] == data["version"] and row["detail"]["md5"] == data["md5"] == row["detail"]["gmd5"]
    calls = len(fake.calls)
    _sync(me, ws)
    assert not any(path.endswith(f"/items/{att}/file") for _, path, _ in fake.calls[calls:])  # never fetched back


# --- a page purged here before a round saw it in Recently deleted -------------------------------

def test_a_page_purged_before_a_round_saw_it_trashes_its_item_and_a_restore_brings_it_all_back(
        me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id)
        ops.delete_page(ws, conn, page, actor=owner_id)
    _sync(me, ws)
    assert fake.data(LIB, keys["attention"])["deleted"] == 1
    assert not fake.data(LIB, keys["highlight"]).get("deleted")  # its children stay as they are, as Zotero keeps them
    assert _rows(ws, "item")[keys["attention"]]["state"] == "trashed"
    fake.restore(LIB, keys["attention"])
    _sync(me, ws)
    _sync(me, ws)
    again = _page_of(ws, keys["attention"])
    assert again != page and _block(ws, again)["props"]["doc_id"]
    annotations = {k: r for k, r in _rows(ws, "annotation").items() if r["page_id"] == again}
    assert keys["highlight"] in annotations and all(r["state"] == "live" for r in annotations.values())
    assert _rows(ws, "note")[keys["note"]]["page_id"] == again


# --- trashes, restores and cut-off writes the randomized runs (test_zotero_sync_fuzz.py) meet ------

def _killed(fake, ws, *, after_commit=0, before_write=0):
    """One round killed right after its n-th commit here, or right before its
    n-th write to Zotero went; then the next start."""
    real, seen = fake.handle, []

    def handle(method, url, headers, body):
        if before_write and method in WRITES:
            seen.append(url)
            if len(seen) == before_write:
                raise Crash(url)
        return real(method, url, headers, body)

    def listener(ws_, client, page_id):
        if after_commit and ws_ == ws and client == zotero_sync_rows.CLIENT:
            seen.append(page_id)
            if len(seen) == after_commit:
                raise Crash(page_id)

    fake.handle = handle
    ops.commit_listeners.append(listener)
    try:
        drop_pending(ws)
        with pytest.raises(Crash):
            zotero_sync.sync_workspace(ws)
    finally:
        fake.handle = real
        ops.commit_listeners.remove(listener)
    zotero_sync.reset_interrupted()
    zotero_sync.forget_access()


@pytest.mark.parametrize("comes_back", ["here", "there"])
def test_what_zotero_changes_while_an_item_is_in_both_trashes_reaches_the_page_when_it_comes_back(
        me, made, fake, owner_id, comes_back):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["ppo"])
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id)
    _sync(me, ws)
    assert fake.data(LIB, keys["ppo"])["deleted"]
    fake.edit(LIB, keys["ppo"], title="PPO, edited in the trash", tags=[{"tag": "rl"}])
    _sync(me, ws)  # in both trashes: the page takes no edit, the change waits
    if comes_back == "here":
        with connect_pages_db(ws) as conn:
            ops.restore_page(ws, conn, page, actor=owner_id)
    else:
        fake.restore(LIB, keys["ppo"])
    _sync(me, ws)
    block = _block(ws, page)
    assert block["parent"] == "root" and block["props"]["meta"]["title"] == "PPO, edited in the trash"
    with connect_pages_db(ws) as conn:
        from gamma.blocks_store import label_names
        names = label_names(conn)
    assert [names[lid] for lid in block["props"].get(LABELS) or ()] == ["rl"]
    assert not fake.data(LIB, keys["ppo"]).get("deleted")


def test_a_restore_that_never_landed_is_sent_again_rather_than_the_page_trashed(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["ppo"])
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id)
    _sync(me, ws)
    with connect_pages_db(ws) as conn:  # brought back here...
        ops.restore_page(ws, conn, page, actor=owner_id)
    fake.edit(LIB, keys["ppo"], date="2018")  # (so the unit writes on the page before its restore goes)
    _killed(fake, ws, before_write=1)  # ...and the round dies before Zotero hears it
    assert fake.data(LIB, keys["ppo"])["deleted"]
    _sync(me, ws)
    assert not fake.data(LIB, keys["ppo"]).get("deleted") and _block(ws, page)["parent"] == "root"


def test_a_receive_only_page_brought_back_takes_zoteros_changes_and_its_restore_goes_once_two_way(
        me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["ppo"])
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id)
    _sync(me, ws)
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 200
    with connect_pages_db(ws) as conn:
        ops.restore_page(ws, conn, page, actor=owner_id)
    fake.edit(LIB, keys["ppo"], title="PPO, edited in Zotero's trash")
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == [] and _block(ws, page)["props"]["meta"]["title"] == "PPO, edited in Zotero's trash"
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    _sync(me, ws)
    assert not fake.data(LIB, keys["ppo"]).get("deleted") and _block(ws, page)["parent"] == "root"


def test_a_collection_restored_in_zotero_files_its_pages_again_though_the_pass_dies_after_the_folder(
        me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])  # filed in Reading
    fake.trash(LIB, keys["reading"])
    _sync(me, ws)
    assert not _block(ws, page)["props"].get(FOLDERS)
    fake.restore(LIB, keys["reading"])
    _killed(fake, ws, after_commit=1)  # the folder is back, then the process dies
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        folders = {name: fid for fid, _, name, _ in tree_rows(conn, FOLDERS)}
    assert _block(ws, page)["props"].get(FOLDERS) == [folders["Reading"]]


def test_a_folder_deleted_here_while_zotero_renamed_its_collection_comes_back_with_its_pages(
        me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["thesis"])  # filed in Reading
    with connect_pages_db(ws) as conn:
        reading = next(fid for fid, _, name, _ in tree_rows(conn, FOLDERS) if name == "Reading")
        ops.delete_tree(ws, conn, FOLDERS, reading, actor=owner_id, client="t")
    fake.edit(LIB, keys["reading"], name="Reading, renamed in Zotero")
    _sync(me, ws)
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        folders = {name: fid for fid, _, name, _ in tree_rows(conn, FOLDERS)}
    assert _block(ws, page)["props"].get(FOLDERS) == [folders["Reading, renamed in Zotero"]]  # an edit beats it
    data = fake.data(LIB, keys["reading"])
    assert not data.get("deleted") and keys["reading"] in fake.data(LIB, keys["thesis"])["collections"]


def test_a_highlight_create_cut_off_before_it_went_goes_from_a_page_visit_alone(me, made, fake, owner_id):
    """Its page's next visit comes from the change feed alone (Zotero
    listing nothing), as after a switch back to two-way."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    _edit(ws, owner_id, page, [{"op": "insert", "id": "zhCut", "parent": page,
                                **_highlight([TITLE_RECT], color="#2ea8e5", quote="Attention Is All You Need")}])
    _killed(fake, ws, before_write=1)
    key = object_key(ws, LIB, "annotation", keys["pdf"], "zhCut", "")
    assert _rows(ws, "annotation")[key]["state"] == "pending"
    with connect_pages_db(ws) as conn:  # every page looked at again, nothing queued from before
        conn.execute("DELETE FROM zotero_queue")
        conn.execute("UPDATE zotero_state SET local_cursor = ''")
        conn.commit()
    _sync(me, ws)
    assert fake.data(LIB, key)["annotationColor"] == "#2ea8e5" and _rows(ws, "annotation")[key]["state"] == "live"


def test_a_collection_write_zotero_refuses_as_stale_is_read_again_and_lands_next_round(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    with connect_pages_db(ws) as conn:
        reading = next(fid for fid, _, name, _ in tree_rows(conn, FOLDERS) if name == "Reading")
    ops.commit_ops(ws, FOLDERS, [{"op": "set", "id": reading, "content": "Read soon"}], actor=owner_id, client="t")
    fake.on_request(f"/{LIB}/collections", lambda: fake.edit(LIB, keys["reading"], parentCollection=keys["ml"]),
                    method="POST")  # Zotero moves it just before the rename lands: 412
    _sync(me, ws)
    assert fake.data(LIB, keys["reading"])["name"] == "Reading" and _soon(ws)
    _sync(me, ws)
    data = fake.data(LIB, keys["reading"])
    assert data["name"] == "Read soon" and data["parentCollection"] == keys["ml"]


@pytest.mark.parametrize("change", ["restored", "new"])
def test_an_annotation_zotero_changes_under_an_item_whose_page_was_deleted_here_brings_the_page_back(
        me, made, fake, owner_id, change):
    """Edit beats delete for an item's children too: an annotation Zotero
    brings back from its trash, or a new one, while the page is in Recently
    deleted here (receive only, so Zotero's item is live)."""
    from zotero_fixtures import _annotation, _position
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _page_of(ws, keys["attention"])
    if change == "restored":
        fake.trash(LIB, keys["underline"])
        _sync(me, ws)
        assert _rows(ws, "annotation")[keys["underline"]]["state"] == "trashed"
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 200
    with connect_pages_db(ws) as conn:
        ops.trash_page(ws, conn, page, actor=owner_id)
    _sync(me, ws)
    if change == "restored":
        fake.restore(LIB, keys["underline"])
        key = keys["underline"]
    else:
        key = fake.add_item(LIB, _annotation(keys["pdf"], "highlight", "#a28ae5", "00000|000300|00500",
                                             _position(0, rects=[[72.0, 280.0, 160.0, 294.0]]), comment="new one",
                                             annotationText="new"))
    _sync(me, ws)
    assert _block(ws, page)["parent"] == "root"  # back, with the annotation
    row = _rows(ws, "annotation")[key]
    assert row["state"] == "live" and _block(ws, row["block_id"])["page"] == page
    assert any(c["kind"] == "zotero_deleted_edited" for c in _conflicts(me, ws))


def test_a_direction_changed_back_to_two_way_may_force_a_push_before_any_round(me, made, fake):
    ws = _link(me)
    _sync(me, ws)
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).json()["status"]["mode"] == "pull"
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).json()["status"]["mode"] == "two-way"
    assert me.post(f"/api/zotero/links/{ws}/force", json={"direction": "push"}).status_code == 200
    _sync(me, ws)


# --- the stream, started with the app -----------------------------------------------------------

def test_the_app_runs_zoteros_stream_while_it_runs(monkeypatch):
    """The stream starts with the app and stops with it (its own ``start``
    keeps it off while ``GAMMA_SYNC_INTERVAL=0`` turns the sync loops off)."""
    import asyncio

    from gamma import app as app_module
    from gamma import zotero_stream
    seen = []
    monkeypatch.setattr(zotero_stream, "start", lambda: seen.append("start"))
    monkeypatch.setattr(zotero_stream, "stop", lambda timeout=zotero_stream.STOP_S: seen.append("stop"))

    async def run():
        async with app_module.zotero_live():
            seen.append("serving")

    asyncio.run(run())
    assert seen == ["start", "serving", "stop"]


def test_a_colour_gamma_won_while_receiving_only_goes_once_two_way_even_set_back(me, made, fake, owner_id):
    """Both sides changed it while the link only received: Gamma's wins and
    waits; set back here to what it was, that still goes (Zotero's newer
    colour must not outlast it unseen on both sides)."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page, block = _page_of(ws, keys["attention"]), _rows(ws, "annotation")[keys["highlight"]]["block_id"]
    before = _block(ws, block)["props"]["color"]
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 200
    _edit(ws, owner_id, page, [{"op": "set", "id": block, "props": {"color": "#5fb23673"}}])
    fake.edit(LIB, keys["highlight"], annotationColor="#ff6666")
    _sync(me, ws)  # both changed: Gamma's wins, and waits for two-way
    _edit(ws, owner_id, page, [{"op": "set", "id": block, "props": {"color": before}}])  # set back here
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    _sync(me, ws)
    assert fake.data(LIB, keys["highlight"])["annotationColor"] == before[:7] == _block(ws, block)["props"]["color"][:7]


def test_a_note_a_force_pull_finds_in_zoteros_trash_while_receiving_only_goes_here_and_stays_there(
        me, made, fake, owner_id):
    """A force pull while the link only receives takes Zotero's version of
    every pair, its trash with it: a note Zotero trashed leaves the page
    at once, its row trashed, and once the link sends again nothing brings
    it back in Zotero."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    root = _rows(ws, "note")[keys["note"]]["block_id"]
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"}).status_code == 200
    fake.trash(LIB, keys["note"])
    assert me.post(f"/api/zotero/links/{ws}/force", json={"direction": "pull"}).status_code == 200
    _sync(me, ws)
    assert _block(ws, root) is None  # Zotero's version, its trash with it
    assert _rows(ws, "note")[keys["note"]]["state"] == "trashed"
    assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    calls = len(fake.calls)
    _sync(me, ws)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    data = fake.data(LIB, keys["note"])
    assert data.get("deleted") and "Key points" in data["note"]
    assert _rows(ws, "note")[keys["note"]]["state"] == "trashed" and _block(ws, root) is None
