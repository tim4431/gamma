"""The Gamma side of a Zotero link's round (gamma/zotero_local.py) against a
real workspace: the one-transaction harness and its compare-and-set, a block
on another page refused, a page made with its rows, the page reader, every
writer (record, filing, folders, trash and restore, a move to another page,
note-region edits and removals that keep what the note does not carry, note
pictures both ways, annotation blocks of every kind, the link's marks) and a
PDF made the page's through routers.pages.replace_attachment as the engine
calls it. The engine's rows are stood in for by a table of this module's
own; the conftest drift checks (page_id, page_changes, the notes index) run
after each test."""

import json
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_user
from gamma import blocks_store, highlights, jobs, ops, storage
from gamma import zotero_annotations as za
from gamma import zotero_local as zl
from gamma import zotero_merge as zm
from gamma.db import connect_pages_db
from gamma.zotero_notes import note_html

USER = "zl_owner"
NOTE_URI = "http://zotero.org/users/123/items/NOTE2345"
# A 1x1 PNG (a note's picture).
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c63f8cfc0f00f0004850180a4"
                    "a98c210000000049454e44ae426082")


@pytest.fixture(scope="module")
def side(client):
    ws = make_user(USER, "pw")
    c = login(USER, "pw")
    c.headers["X-Gamma-Workspace"] = ws
    with connect_pages_db(ws) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS zl_rows (key TEXT PRIMARY KEY, block_id TEXT NOT NULL DEFAULT '')")
        conn.commit()
    return SimpleNamespace(ws=ws, c=c, owner=account_of(USER))


def _page(c, title, **props):
    r = c.post("/api/pages", json={"title": title, "properties": props})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _ops(c, page_id, batch):
    r = c.post(f"/api/pages/{page_id}/ops", json={"client": "t", "ops": batch})
    assert r.status_code == 200, r.text
    return r.json()


def _insert(c, page_id, bid, content="", parent=None, **props):
    _ops(c, page_id, [{"op": "insert", "id": bid, "parent": parent or page_id, "content": content, "props": props}])
    return bid


def _tree(c, page_id):
    r = c.get(f"/api/blocks/{page_id}/subtree")
    return r.json()["block"] if r.status_code == 200 else None


def _kids(c, block_id, page_id=None):
    """``[(id, content)]`` of a block's children, in order."""
    tree = _tree(c, page_id or block_id)
    stack = [tree]
    while stack:
        node = stack.pop()
        if node["id"] == block_id:
            return [(k["id"], k["content"]) for k in node["children"]]
        stack += node["children"]
    return None


def _block(ws, bid):
    with connect_pages_db(ws) as conn:
        row = conn.execute(f"SELECT {blocks_store.BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (bid,)).fetchone()
    return blocks_store.block_to_dict(row) if row else None


def _row(key, block_id=""):
    def write(conn):
        conn.execute("INSERT OR REPLACE INTO zl_rows (key, block_id) VALUES (?, ?)", (key, block_id))
    return write


def _rows(ws):
    with connect_pages_db(ws) as conn:
        return dict(conn.execute("SELECT key, block_id FROM zl_rows").fetchall())


def _last_log(ws, page_id):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT actor, client FROM page_ops WHERE page_id = ? ORDER BY seq DESC LIMIT 1",
                            (page_id,)).fetchone()


def _view(ws, page_id, **kw):
    with connect_pages_db(ws) as conn:
        return zl.read_page(conn, page_id, **kw)


class _Heard:
    """The commit listeners' calls while the block is open."""

    def __enter__(self):
        self.calls = []
        self._fn = lambda ws, client, page_id: self.calls.append((client, page_id))
        ops.commit_listeners.append(self._fn)
        return self

    def __exit__(self, *exc):
        ops.commit_listeners.remove(self._fn)


def _placed(quote="a passage", page=1):
    return {"color": "#ffe28fa6", "quote": quote,
            "pdf_position": highlights.position(page, 612, 792, [(100, 100, 300, 115)])}


# --- the transaction ------------------------------------------------------------------

def test_apply_writes_ops_and_rows_in_one_transaction_logged_as_zotero(side):
    page = _page(side.c, "Harness")
    with _Heard() as heard:
        results = zl.apply(side.ws, page, [{"op": "insert", "id": "zlA1", "parent": page, "content": "from Zotero"}],
                           rows=_row("KEYAAAA1", "zlA1"))
    assert [r["client"] for r in results] == ["zotero"] and results[0]["actor"] == "zotero"
    assert _kids(side.c, page) == [("zlA1", "from Zotero")]
    assert _rows(side.ws)["KEYAAAA1"] == "zlA1"
    assert tuple(_last_log(side.ws, page)) == ("zotero", "zotero")
    assert ("zotero", page) in heard.calls
    # rows alone (a version bump): no batch, the row written
    assert zl.apply(side.ws, page, [], rows=_row("KEYAAAA1", "again")) == []
    assert _rows(side.ws)["KEYAAAA1"] == "again"


def test_a_failed_check_writes_nothing_and_raises_changed(side):
    page = _page(side.c, "Compare and set")
    bid = _insert(side.c, page, "zlCas1", "as read")
    view = _view(side.ws, page)
    _ops(side.c, page, [{"op": "set", "id": bid, "content": "typed meanwhile"}])
    still = lambda conn: zl.stamps(conn, [page, bid]) == view.stamps([page, bid])  # noqa: E731
    with pytest.raises(zl.Changed):
        zl.apply(side.ws, page, [{"op": "set", "id": bid, "content": "Zotero's"}], check=still,
                 rows=_row("KEYCAS01", bid))
    assert _kids(side.c, page) == [(bid, "typed meanwhile")]
    assert "KEYCAS01" not in _rows(side.ws)
    view = _view(side.ws, page)  # read again: the decision is made afresh and goes through
    zl.apply(side.ws, page, [{"op": "set", "id": bid, "content": "Zotero's"}], check=still,
             rows=_row("KEYCAS01", bid))
    assert _kids(side.c, page) == [(bid, "Zotero's")] and _rows(side.ws)["KEYCAS01"] == bid


def test_stamps_see_a_move_a_trash_and_a_delete(side):
    page = _page(side.c, "Stamps")
    a, b = _insert(side.c, page, "zlSt1", "a"), _insert(side.c, page, "zlSt2", "b")
    view = _view(side.ws, page)
    with connect_pages_db(side.ws) as conn:
        assert zl.stamps(conn, [page, a, b]) == view.stamps([page, a, b])
    _ops(side.c, page, [{"op": "move", "id": b, "parent": a}, {"op": "delete", "id": a}])
    with connect_pages_db(side.ws) as conn:
        now = zl.stamps(conn, [page, a, b])
    assert now[a] is None and now[b] is None
    other = _page(side.c, "Stamps trashed")
    view = _view(side.ws, other)
    assert side.c.delete(f"/api/blocks/{other}").status_code == 200
    with connect_pages_db(side.ws) as conn:
        assert zl.stamps(conn, [other]) != view.stamps([other])


def test_rows_that_fail_roll_the_ops_back(side):
    page = _page(side.c, "Rows fail")

    def broken(conn):
        raise RuntimeError("row write failed")
    with pytest.raises(RuntimeError):
        zl.apply(side.ws, page, [{"op": "insert", "id": "zlRf1", "parent": page, "content": "x"}], rows=broken)
    assert _kids(side.c, page) == []


def test_an_op_for_a_block_on_another_page_is_refused_as_moved(side):
    home, away = _page(side.c, "Moved from"), _page(side.c, "Moved to")
    bid = _insert(side.c, home, "zlMv1", "", **_placed())
    r = side.c.post(f"/api/blocks/{bid}/reorder", json={"parent_id": away})
    assert r.status_code == 200, r.text
    with pytest.raises(zl.Moved) as e:
        zl.apply(side.ws, home, [{"op": "set", "id": bid, "props": {"color": "#ff6666"}}],
                 rows=_row("KEYMOVE1", bid))
    assert (e.value.block_id, e.value.where, e.value.conflict, e.value.status) == (bid, away, "moved", 409)
    assert "KEYMOVE1" not in _rows(side.ws) and _block(side.ws, bid)["properties"]["color"] == "#ffe28fa6"
    with pytest.raises(zl.Moved):  # an insert under it, a move of it
        zl.apply(side.ws, home, [{"op": "insert", "id": "zlMv2", "parent": bid, "content": "x"}])
    with pytest.raises(zl.Moved):
        zl.apply(side.ws, home, [{"op": "move", "id": bid, "parent": home}])
    with pytest.raises(zl.Moved) as e:  # a block gone
        zl.apply(side.ws, home, [{"op": "set", "id": "zlGone1", "content": "x"}])
    assert e.value.where is None
    assert zl.apply(side.ws, home, [{"op": "delete", "id": "zlGone1"}])[0]["ops"] == []  # its delete is no change


def test_many_ops_go_in_chunks_of_one_transaction(side):
    page = _page(side.c, "Chunks")
    batch = [{"op": "insert", "id": f"zlCh{i}", "parent": page, "content": str(i)} for i in range(ops.MAX_OPS + 20)]
    with pytest.raises(ops.OpError):  # the last chunk refused: the first is not kept either
        zl.apply(side.ws, page, batch + [{"op": "insert", "id": "bad id!", "parent": page}])
    assert _kids(side.c, page) == []
    results = zl.apply(side.ws, page, batch)
    assert [len(r["ops"]) for r in results] == [ops.MAX_OPS, 20]
    assert len(_kids(side.c, page)) == ops.MAX_OPS + 20


def test_ops_built_under_the_lock_see_the_page_as_it_is(side):
    page = _page(side.c, "Built under the lock")
    _insert(side.c, page, "zlBu1", "", **_placed())
    _insert(side.c, page, "zlBu2", "a note after it")
    blocks = [{"id": "zlBu3", "content": "", "props": _placed("second")}]
    zl.apply(side.ws, page, lambda conn: zl.annotation_inserts(conn, page, blocks))
    assert [k for k, _ in _kids(side.c, page)] == ["zlBu1", "zlBu3", "zlBu2"]


# --- a new page ---------------------------------------------------------------------------

def test_create_page_writes_the_page_its_blocks_and_rows_in_one_transaction(side):
    name, _ = storage.store_file(side.ws, PNG, ".png")
    with connect_pages_db(side.ws) as conn:
        conn.execute("INSERT OR REPLACE INTO upload_orphans (name, since) VALUES (?, '2026-01-01T00:00:00Z')",
                     (name,))
        conn.commit()
    tree = [{"id": "zlNp1", "content": "Key points", "properties": {"zotero_note": NOTE_URI},
             "children": [{"content": "a paragraph"}, {"content": f"![](/api/uploads/{name})"}]},
            {"id": "zlNp2", "content": "", "properties": {**_placed(), "zotero_annotation": "ANNO2345"}}]
    with _Heard() as heard:
        page = zl.create_page(side.ws, page_id="zlNewPage1", title="A paper", tree=tree,
                              props={"meta": {"title": "A paper"}, "folders": "not a list", "labels": ["zlNoLbl"]},
                              rows=_row("ITEM2345", "zlNewPage1"))
    assert page["id"] == "zlNewPage1" and page["content"] == "A paper"
    assert "folders" not in page["properties"] and page["properties"]["labels"] == ["zlNoLbl"]
    assert [k for k, _ in _kids(side.c, "zlNewPage1")] == ["zlNp1", "zlNp2"]
    assert [c for _, c in _kids(side.c, "zlNp1", "zlNewPage1")] == ["a paragraph", f"![](/api/uploads/{name})"]
    assert _rows(side.ws)["ITEM2345"] == "zlNewPage1"
    assert ("zotero", "zlNewPage1") in heard.calls
    with connect_pages_db(side.ws) as conn:  # its picture is in use
        assert not conn.execute("SELECT 1 FROM upload_orphans WHERE name = ?", (name,)).fetchone()
    with pytest.raises(ops.OpError) as e:
        zl.create_page(side.ws, page_id="zlNewPage1", title="Again")
    assert e.value.status == 409


def test_create_page_whose_rows_fail_leaves_no_page(side):
    def broken(conn):
        raise RuntimeError("row write failed")
    with pytest.raises(RuntimeError):
        zl.create_page(side.ws, page_id="zlNewPage2", title="Never", tree=[{"content": "x"}], rows=broken)
    assert _block(side.ws, "zlNewPage2") is None
    with pytest.raises(zl.Changed):
        zl.create_page(side.ws, page_id="zlNewPage2", title="Never", check=lambda conn: False)
    assert _block(side.ws, "zlNewPage2") is None


# --- reading a page ----------------------------------------------------------------------

def test_read_page_gives_annotations_at_any_depth_regions_filing_and_the_pdf(side):
    folder = _insert(side.c, "folders", "zlRpF1", "Reading", parent="folders")
    label = _insert(side.c, "labels", "zlRpL1", "to read", parent="labels")
    doc_id, _ = storage.store_pdf(side.ws, b"%PDF-1.4 zotero local read page\n" + b"r" * 400)
    page = _page(side.c, "Read me", folders=[folder, "zlNoSuchFolder"], labels=[label], doc_id=doc_id)
    _insert(side.c, page, "zlRp1", "a Gamma note")
    _insert(side.c, page, "zlRp2", "my own note", **_placed())
    _insert(side.c, page, "zlRp3", "under the highlight", parent="zlRp2")
    _insert(side.c, page, "zlRp4", "Zotero's note", zotero_note=NOTE_URI)
    _insert(side.c, page, "zlRp5", "", parent="zlRp4", **_placed("nested"))
    _insert(side.c, page, "zlRp6", "", sheet={"w": 100, "h": 100})
    _insert(side.c, page, "zlRp7", "box on the sheet", parent="zlRp6", text_box={"x": 1, "y": 1, "w": 50, "h": 20},
            pdf_page=1)
    _insert(side.c, page, "zlRp8", "box on the page", text_box={"x": 1, "y": 1, "w": 50, "h": 20}, pdf_page=1)
    _insert(side.c, page, "zlRp9", "a link", link_url="https://example.org", **_placed())
    view = _view(side.ws, page)
    assert view.title == "Read me" and view.folders == [folder] and view.labels == {label: "to read"}
    assert view.doc_id == doc_id and view.pdf is not None and view.pdf.read_bytes().startswith(b"%PDF")
    found = {a.block["id"]: a for a in view.annotations}
    assert list(found) == ["zlRp2", "zlRp5", "zlRp8"]  # not the box on a sheet, not a link region
    assert found["zlRp2"].nested == [(0, "my own note", "zlRp2"), (1, "under the highlight", "zlRp3")]
    assert (found["zlRp5"].depth, found["zlRp5"].parent_id, found["zlRp5"].page_root) == (2, "zlRp4", page)
    assert set(view.regions) == {"zlRp4"} and "zlRp5" in view.regions["zlRp4"].kept
    assert view.regions["zlRp4"].html == "<p>Zotero's note</p>"
    gamma = view.gamma_notes
    assert gamma.root == page and [b[0] for b in gamma.blocks] == [page, "zlRp1"]
    assert "Notes from Gamma" in gamma.html and "a Gamma note" in gamma.html
    # the engine's rows name the regions: a block carrying zotero_note without one is a Gamma note
    assert set(_view(side.ws, page, note_roots=[]).regions) == set()
    assert [b[0] for b in _view(side.ws, page, note_roots=[]).gamma_notes.blocks] == [page, "zlRp1", "zlRp4"]
    side.c.delete(f"/api/blocks/{page}")
    assert _view(side.ws, page) is None


# --- the record and filing ---------------------------------------------------------------

def _record_page(c, title="Old title", **extra):
    meta = {"title": "Old title", "authors": ["A. Author"], "year": "2017", "venue": "Nature", "volume": "",
            "pages": "", "doi": "10.1/x", "arxiv_id": "", "publisher": "", "isbn": "", "source": "arxiv",
            "kind": "journalArticle", "at": "2026-01-01T00:00:00Z"}
    return _page(c, title, meta=meta, bibtex="@article{old, title={Old title}}", ppt_cite="stale",
                 meta_error="lookup failed", **extra)


def test_record_op_writes_the_changed_fields_and_rebuilds_the_entry(side):
    page = _record_page(side.c)
    op = zl.record_op(_block(side.ws, page), {"title": "New title", "year": "2018", "cite_key": "vaswani2017",
                                              "kind": "", "tags": ["x"], "type": "document"})
    zl.apply(side.ws, page, [op])
    got = _block(side.ws, page)
    meta, props = got["properties"]["meta"], got["properties"]
    assert (meta["title"], meta["year"], meta["venue"], meta["source"], meta["at"]) == (
        "New title", "2018", "Nature", "arxiv", "2026-01-01T00:00:00Z")
    assert "kind" not in meta and got["content"] == "New title"  # the title followed the record's
    assert props["cite_key"] == "vaswani2017" and props["bibtex"].startswith("@") and "vaswani2017" in props["bibtex"]
    assert "New title" in props["bibtex"] and "ppt_cite" not in props and "meta_error" not in props
    assert zl.record_op(got, {"title": "New title", "year": "2018"}) is None  # nothing changes
    # a page renamed by hand keeps its title; a cite key Zotero dropped is unpinned
    _ops(side.c, page, [{"op": "set", "id": page, "content": "My own name"}])
    op = zl.record_op(_block(side.ws, page), {"title": "Newer title", "cite_key": ""})
    assert "content" not in op and op["props"]["cite_key"] is None and op["props"]["meta"]["title"] == "Newer title"


def test_record_op_gives_a_page_without_a_record_zoteros(side):
    page = _page(side.c, "paper.pdf", auto_title="paper.pdf")
    op = zl.record_op(_block(side.ws, page), {"title": "A Found Paper", "authors": ["B. Writer"], "kind": "preprint"})
    zl.apply(side.ws, page, [op])
    got = _block(side.ws, page)
    meta = got["properties"]["meta"]
    assert list(meta)[:11] == list(zl.META_ORDER[:11]) and meta["source"] == "zotero" and meta["kind"] == "preprint"
    assert meta["authors"] == ["B. Writer"] and got["content"] == "A Found Paper"
    assert "auto_title" not in got["properties"]


def test_filing_matches_labels_ignoring_case_and_makes_the_missing_ones(side):
    ml = _insert(side.c, "labels", "zlLbML", "Machine Learning", parent="labels")
    twin = _insert(side.c, "labels", "zlLbML2", "machine learning", parent="labels")
    folder = _insert(side.c, "folders", "zlFi1", "Filed", parent="folders")
    page = _page(side.c, "Filed page", labels=[twin])
    with connect_pages_db(side.ws) as conn:
        filing = zl.filing_ops(conn, _block(side.ws, page), folders=[folder, "zlNoFolder"],
                               labels=["MACHINE LEARNING", "Brand New", "brand new"])
    assert len(filing.trees) == 1 and [o["content"] for o in filing.trees[0][1]] == ["Brand New"]
    zl.apply(side.ws, page, [filing.op], trees=filing.trees)
    props = _block(side.ws, page)["properties"]
    new_label = filing.trees[0][1][0]["id"]
    assert props["labels"] == [twin, new_label]  # the page's own spelling of the label kept, not ml's
    assert props["folders"] == [folder] and ml not in props["labels"]
    assert _block(side.ws, new_label)["content"] == "Brand New"
    with connect_pages_db(side.ws) as conn:
        again = zl.filing_ops(conn, _block(side.ws, page), folders=[folder], labels=["machine learning", "brand new"])
        cleared = zl.filing_ops(conn, _block(side.ws, page), folders=[], labels=[])
    assert again == zl.Filing([], None)
    zl.apply(side.ws, page, [cleared.op])
    props = _block(side.ws, page)["properties"]
    assert "folders" not in props and "labels" not in props


def test_folders_are_made_renamed_moved_and_deleted_with_their_rows(side):
    zl.apply(side.ws, blocks_store.FOLDERS, [zl.folder_insert("zlFo1", "ML"),
                                             zl.folder_insert("zlFo2", "Transformers", "zlFo1"),
                                             zl.folder_insert("zlFo3", "Elsewhere")],
             rows=_row("COLL0001", "zlFo1"))
    assert _block(side.ws, "zlFo2")["parent_id"] == "zlFo1" and _rows(side.ws)["COLL0001"] == "zlFo1"
    zl.apply(side.ws, blocks_store.FOLDERS, [zl.folder_rename("zlFo2", "Attention"), zl.folder_move("zlFo3", "zlFo1")])
    assert _block(side.ws, "zlFo2")["content"] == "Attention" and _block(side.ws, "zlFo3")["parent_id"] == "zlFo1"
    page = _page(side.c, "Filed under ML", folders=["zlFo2"])
    with pytest.raises(zl.Moved):  # a folder op never names a page's block
        zl.apply(side.ws, blocks_store.FOLDERS, [{"op": "set", "id": page, "content": "x"}])
    rows, carried, results = zl.delete_folder(side.ws, "zlFo1", rows=_row("COLL0001", ""))
    assert {r[0] for r in rows} == {"zlFo1", "zlFo2", "zlFo3"} and carried == {page: ["zlFo2"]}
    assert all(r["client"] == "zotero" for r in results)
    assert "folders" not in _block(side.ws, page)["properties"] and _rows(side.ws)["COLL0001"] == ""
    assert zl.delete_folder(side.ws, "zlFo1", rows=_row("COLL0001", "again")) is None  # a re-run
    assert _rows(side.ws)["COLL0001"] == "again"


def test_trash_and_restore_are_idempotent_and_carry_the_rows(side):
    page = _page(side.c, "Trash me")
    with _Heard() as heard:
        entry = zl.trash_page(side.ws, page, rows=_row("TRSH0001", "trashed"))
    assert entry["id"] == page and entry["deleted_by"] == "zotero" and ("zotero", page) in heard.calls
    assert _block(side.ws, page)["parent_id"] == "trash" and _rows(side.ws)["TRSH0001"] == "trashed"
    assert zl.trash_page(side.ws, page, rows=_row("TRSH0001", "again")) is None
    assert _rows(side.ws)["TRSH0001"] == "again"
    with pytest.raises(zl.Changed):
        zl.restore_page(side.ws, page, check=lambda conn: False)
    assert _block(side.ws, page)["parent_id"] == "trash"
    back = zl.restore_page(side.ws, page, rows=_row("TRSH0001", "live"))
    assert back["parent_id"] == "root" and _rows(side.ws)["TRSH0001"] == "live"
    assert zl.restore_page(side.ws, page) is None
    assert zl.trash_page(side.ws, "zlNoSuchPage") is None


def test_a_block_moves_to_another_page_after_its_last_annotation(side):
    home, away = _page(side.c, "Annotation home"), _page(side.c, "Annotation new home")
    hl = _insert(side.c, home, "zlMa1", "", **_placed())
    _insert(side.c, home, "zlMa2", "my note on it", parent=hl)
    _insert(side.c, away, "zlMa3", "intro")
    _insert(side.c, away, "zlMa4", "", **_placed("theirs"))
    _insert(side.c, away, "zlMa5", "outro")
    with _Heard() as heard:
        position = zl.move_block_across(side.ws, hl, away, rows=_row("ANNMOVE1", hl))
    assert position and [k for k, _ in _kids(side.c, away)] == ["zlMa3", "zlMa4", hl, "zlMa5"]
    # both halves are the link's own writes: logged and heard as client "zotero"
    assert tuple(_last_log(side.ws, home)) == tuple(_last_log(side.ws, away)) == ("zotero", "zotero")
    assert heard.calls == [("zotero", home), ("zotero", away)]
    assert _kids(side.c, hl, away) == [("zlMa2", "my note on it")] and _kids(side.c, home) == []
    assert _rows(side.ws)["ANNMOVE1"] == hl
    assert zl.move_block_across(side.ws, hl, away) is None  # there already
    with pytest.raises(zl.Moved):
        zl.move_block_across(side.ws, "zlNoSuchBlock", away)
    # a note region goes under a block of its new page, last there
    region = _insert(side.c, home, "zlMa6", "a Zotero note", zotero_note=NOTE_URI)
    zl.move_block_across(side.ws, region, away, parent="zlMa5")
    assert _kids(side.c, "zlMa5", away) == [(region, "a Zotero note")]
    with pytest.raises(zl.Moved):  # a parent not on that page
        zl.move_block_across(side.ws, region, home, parent="zlMa4")


def test_marks_set_and_drop(side):
    page = _page(side.c, "Marks")
    bid = _insert(side.c, page, "zlMk1", "", **_placed())
    pdf = {"key": "ATTA2345", "state": "remote", "reason": "", "size": 1234, "filename": "x.pdf"}
    zl.apply(side.ws, page, [zl.zotero_pdf_op(page, pdf), zl.readonly_op(bid, "locked", "carol")])
    assert _block(side.ws, page)["properties"]["zotero_pdf"] == pdf
    assert {k: _block(side.ws, bid)["properties"].get(k) for k in ("zotero_readonly", "zotero_author")} == {
        "zotero_readonly": "locked", "zotero_author": "carol"}
    zl.apply(side.ws, page, [zl.zotero_pdf_op(page, None), zl.readonly_op(bid, None)])
    assert "zotero_pdf" not in _block(side.ws, page)["properties"]
    assert not {"zotero_readonly", "zotero_author"} & set(_block(side.ws, bid)["properties"])


# --- note regions -------------------------------------------------------------------------

def _note_page(side, title):
    """A paper page whose Zotero note's region holds a nested highlight with
    a note under it, a commented paragraph, a [[ref]], a picture and an ink
    group, between plain paragraphs."""
    page = _page(side.c, title)
    other = _insert(side.c, page, f"{page}o", "a block elsewhere")
    root = _insert(side.c, page, f"{page}r", "Key points", zotero_note=NOTE_URI)
    name, _ = storage.store_file(side.ws, PNG, ".png")
    ink_name, _ = storage.store_file(side.ws, b'{"format":"gamma-ink","version":2,"space":{"kind":"pdf-page",'
                                     b'"page":1,"width":612,"height":792},"strokes":[]}', ".ink")
    ids = {"first": "First paragraph", "hl": "", "commented": "Commented paragraph", "ref": f"See [[{other}]]",
           "picture": f"![](/api/uploads/{name})", "ink": "", "last": "Last paragraph"}
    props = {"hl": _placed(), "ink": {"ink_url": f"/api/uploads/{ink_name}", "ink_strokes": 0}}
    out = {"page": page, "root": root}
    for key, content in ids.items():
        out[key] = _insert(side.c, page, f"{page}{key}", content, parent=root, **props.get(key, {}))
    _insert(side.c, page, f"{page}hn", "my note on the highlight", parent=out["hl"])
    r = side.c.post(f"/api/pages/{page}/comments", json={"block_id": out["commented"], "body": "a thread"})
    assert r.status_code == 201, r.text
    out["thread"] = r.json()["thread"]["id"]
    return out


def _thread_shown(ws, thread_id):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT orphaned_at FROM comments WHERE id = ?", (thread_id,)).fetchone()[0] is None


def test_a_note_pull_keeps_the_blocks_the_note_does_not_carry_and_their_ids(side):
    n = _note_page(side, "Note pull")
    region = _view(side.ws, n["page"]).regions[n["root"]]
    assert {n["hl"], n["ref"], n["picture"], n["ink"]} <= region.kept
    assert [b[0] for b in region.blocks] == [n["root"], n["first"], n["commented"], n["last"]]
    # Zotero: the first paragraph edited, one added after it, the last deleted
    pulled = note_html({"content": "Key points", "children": [
        {"content": "First paragraph, edited"}, {"content": "A new paragraph"}, {"content": "Commented paragraph"}]})
    edits = zm.note_edits(region, pulled)
    with connect_pages_db(side.ws) as conn:
        planned = zl.note_ops(conn, region, edits)
    assert planned.lossy == [] and list(planned.ids) == ["new:2"]
    assert [o["op"] for o in planned.ops] == ["set", "insert", "delete"]
    zl.apply(side.ws, n["page"], lambda conn: zl.note_ops(conn, region, edits).ops, rows=_row("NOTE2345", n["root"]))
    kids = _kids(side.c, n["root"], n["page"])
    new_id = next(k for k, c in kids if c == "A new paragraph")
    assert kids == [(n["first"], "First paragraph, edited"), (new_id, "A new paragraph"), (n["hl"], ""),
                    (n["commented"], "Commented paragraph"), (n["ref"], f"See [[{n['page']}o]]"),
                    (n["picture"], _block(side.ws, n["picture"])["content"]), (n["ink"], "")]
    assert _block(side.ws, n["last"]) is None and _rows(side.ws)["NOTE2345"] == n["root"]
    assert _kids(side.c, n["hl"], n["page"]) == [(f"{n['page']}hn", "my note on the highlight")]
    assert _thread_shown(side.ws, n["thread"])
    # the same note again: nothing to do
    again = _view(side.ws, n["page"]).regions[n["root"]]
    assert zm.note_edits(again, pulled) == []


def test_a_note_removal_lifts_the_kept_blocks_after_the_note(side):
    n = _note_page(side, "Note removal")
    region = _view(side.ws, n["page"]).regions[n["root"]]
    edits = zm.note_removal(region)
    zl.apply(side.ws, n["page"], lambda conn: zl.note_ops(conn, region, edits).ops)
    assert _kids(side.c, n["page"]) == [
        (f"{n['page']}o", "a block elsewhere"), (n["hl"], ""), (n["ref"], f"See [[{n['page']}o]]"),
        (n["picture"], _block(side.ws, n["picture"])["content"]), (n["ink"], "")]
    assert _kids(side.c, n["hl"], n["page"]) == [(f"{n['page']}hn", "my note on the highlight")]
    for gone in (n["root"], n["first"], n["commented"], n["last"]):
        assert _block(side.ws, gone) is None


def test_a_note_ops_delete_never_takes_a_kept_block_or_what_is_under_it(side):
    n = _note_page(side, "Defensive delete")
    region = _view(side.ws, n["page"]).regions[n["root"]]
    # a careless list: delete the root without lifting anything first, and a kept block too
    edits = [{"op": "delete", "id": n["hl"]}, {"op": "set", "id": n["ref"], "content": "x"},
             {"op": "delete", "id": n["root"]}]
    zl.apply(side.ws, n["page"], lambda conn: zl.note_ops(conn, region, edits).ops)
    top = [k for k, _ in _kids(side.c, n["page"])]
    assert n["root"] not in top and _block(side.ws, n["hl"]) is not None
    assert top[1:4] == [n["first"], n["hl"], n["commented"]]
    assert _block(side.ws, n["ref"])["content"] == f"See [[{n['page']}o]]"


def test_the_gamma_notes_region_takes_inserts_at_its_place_on_the_page(side):
    page = _page(side.c, "Gamma notes")
    _insert(side.c, page, "zlGn1", "", **_placed())
    _insert(side.c, page, "zlGn2", "Gamma note one")
    _insert(side.c, page, "zlGn3", "", **_placed("second"))
    _insert(side.c, page, "zlGn4", "Gamma note two")
    region = _view(side.ws, page).gamma_notes
    pulled = note_html({"content": zm.NOTES_HEADING, "children": [
        {"content": "Zotero's first"}, {"content": "Gamma note one"}, {"content": "Gamma note two"},
        {"content": "Zotero's last"}]})
    edits = zm.note_edits(region, pulled)
    zl.apply(side.ws, page, lambda conn: zl.note_ops(conn, region, edits).ops)
    assert [c for _, c in _kids(side.c, page)] == ["", "Zotero's first", "Gamma note one", "", "Gamma note two",
                                                   "Zotero's last"]


# --- note pictures ------------------------------------------------------------------------

def test_a_zotero_picture_goes_back_as_its_tag_and_a_pull_keeps_its_block(side):
    name, _ = storage.store_file(side.ws, PNG, ".png")
    mine, _ = storage.store_file(side.ws, PNG + b"gamma", ".png")
    page = _page(side.c, "Pictures")
    root = _insert(side.c, page, "zlPi0", "The architecture:", zotero_note=NOTE_URI)
    pic = _insert(side.c, page, "zlPi1", f"![architecture|320](/api/uploads/{name})", parent=root)
    native = _insert(side.c, page, "zlPi2", f"![](/api/uploads/{mine})", parent=root)
    keys = {name: "PICT2345"}
    view = _view(side.ws, page, image_key=keys.get)
    region = view.regions[root]
    assert pic in {b[0] for b in region.blocks} and native in region.kept
    assert region.html == ('<p>The architecture:</p><ul><li><img data-attachment-key="PICT2345" alt="architecture" '
                           'width="320"/></li></ul>')
    # Zotero edits the caption: the picture's block is left as it is
    asked = []
    pulled = region.html.replace("The architecture:", "The whole architecture:")
    pictures = zl.resolve_pictures(pulled, lambda key: asked.append(key) or {"PICT2345": name}.get(key))
    assert pictures.missing == [] and asked == ["PICT2345"]
    assert not zl.note_lossy(pulled, pictures) and zm.note_lossy(pulled)
    edits = zm.note_edits(region, pictures.html)
    assert edits == [{"op": "set", "id": root, "content": "The whole architecture:"}]
    zl.apply(side.ws, page, lambda conn: zl.note_ops(conn, region, edits, pictures=pictures).ops)
    assert _block(side.ws, pic)["content"] == f"![architecture|320](/api/uploads/{name})"
    # a new picture comes as an upload; one Zotero cannot give stays "(image)" and makes the note lossy
    pulled = pulled.replace("</ul>", '<li><img data-attachment-key="NEWP2345" width="50"/></li>'
                                     '<li><img data-attachment-key="MISS2345"/></li></ul>')
    pictures = zl.resolve_pictures(pulled, {"PICT2345": name, "NEWP2345": mine}.get)
    assert pictures.missing == ["MISS2345"] and zl.note_lossy(pulled, pictures)
    region = _view(side.ws, page, image_key=keys.get).regions[root]
    edits = zm.note_edits(region, pictures.html)
    planned = zl.apply(side.ws, page, lambda conn: zl.note_ops(conn, region, edits, pictures=pictures).ops)
    assert planned
    contents = [c for _, c in _kids(side.c, root, page)]
    assert contents == [f"![architecture|320](/api/uploads/{name})", f"![|50](/api/uploads/{mine})", "(image)",
                        f"![](/api/uploads/{mine})"]  # each new one right after the block before it in the note
    # a new region's tree reads the pictures as uploads
    tree = zl.pulled_tree(zl.resolve_pictures('<p>Fig</p><p><img data-attachment-key="PICT2345" alt="a"/></p>',
                                              {"PICT2345": name}.get))
    assert tree == {"content": "Fig", "children": [{"content": f"![a](/api/uploads/{name})", "children": []}]}


# --- annotation blocks ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def library():
    from zotero_fake import FakeZotero
    from zotero_fixtures import pdf_bytes, standard_library

    fake = FakeZotero()
    made = standard_library(fake)
    with za.Pages(pdf_bytes()) as pages:
        yield SimpleNamespace(fake=fake, made=made, pages=pages)


def _item(library, name):
    data = library.fake.data(library.made["library"], library.made["keys"][name])
    page = library.pages.page(json.loads(data["annotationPosition"])["pageIndex"] + 1)
    return data, page


KINDS = ("highlight", "underline", "note_annotation", "image_annotation", "ink_annotation", "text_annotation",
         "spanning")


def test_every_kind_of_annotation_lands_after_the_last_annotation_block(side, library):
    page = _page(side.c, "Annotations")
    _insert(side.c, page, "zlAn0", "intro")
    _insert(side.c, page, "zlAn1", "", **_placed())
    _insert(side.c, page, "zlAn2", "outro")
    blocks = {}
    for name in KINDS:
        data, pdf_page = _item(library, name)
        blocks[name] = zl.new_annotation(side.ws, za.from_item(data, pdf_page), block_id=f"zlAn{name[:6]}")
    zl.apply(side.ws, page, lambda conn: zl.annotation_inserts(conn, page, list(blocks.values())),
             rows=_row("ANNOTS01", page))
    order = [k for k, _ in _kids(side.c, page)]
    assert order == ["zlAn0", "zlAn1", *[b["id"] for b in blocks.values()], "zlAn2"]
    view = _view(side.ws, page)
    assert {a.block["id"] for a in view.annotations} == {"zlAn1", *[b["id"] for b in blocks.values()]}
    for name, made in blocks.items():
        props = _block(side.ws, made["id"])["properties"]
        assert props["zotero_annotation"] == library.made["keys"][name] and props["annot_stripped"] is True
        assert props["imported_annot"]
    ink_props = _block(side.ws, blocks["ink_annotation"]["id"])["properties"]
    assert ink_props["ink_url"].startswith("/api/uploads/") and ink_props["ink_url"].endswith(".ink")
    drawn = zl.read_ink(side.ws, ink_props)
    key = library.made["keys"]["ink_annotation"]
    assert [s.id for s in drawn.strokes] == [f"z{key}-0", f"z{key}-1"]
    assert _block(side.ws, blocks["note_annotation"]["id"])["properties"]["pdf_page"] == 1


def test_annotation_updates_restyle_and_replace_ink_strokes(side, library):
    page = _page(side.c, "Annotation updates")
    hl_data, pdf_page = _item(library, "highlight")
    ink_data, _ = _item(library, "ink_annotation")
    hl = zl.new_annotation(side.ws, za.from_item(hl_data, pdf_page), block_id="zlAu1")
    inked = zl.new_annotation(side.ws, za.from_item(ink_data, pdf_page), block_id="zlAu2")
    zl.apply(side.ws, page, lambda conn: zl.annotation_inserts(conn, page, [hl, inked]))
    block = _block(side.ws, "zlAu1")
    change = za.apply_fields(block, pdf_page, {"color": "#ff6666", "comment": "a better point"})
    zl.apply(side.ws, page, [zl.annotation_update(side.ws, "zlAu1", change)])
    got = _block(side.ws, "zlAu1")
    assert got["properties"]["color"].startswith("#ff6666") and got["content"] == "a better point"
    assert zl.annotation_update(side.ws, "zlAu1", {"props": {}}) is None
    # Zotero redrew the ink item: exactly its strokes are replaced, in a new file
    block = _block(side.ws, "zlAu2")
    drawn = zl.read_ink(side.ws, block["properties"])
    position = json.loads(ink_data["annotationPosition"])
    moved = {**position, "paths": [[v + 7 for v in path] for path in position["paths"]]}
    key = ink_data["key"]
    change = za.apply_fields(block, pdf_page, {"position": moved}, ink_file=drawn,
                             stroke_ids=[s.id for s in drawn.strokes], key=key)
    zl.apply(side.ws, page, [zl.annotation_update(side.ws, "zlAu2", change)])
    after = _block(side.ws, "zlAu2")["properties"]
    assert after["ink_url"] != block["properties"]["ink_url"]
    redrawn = zl.read_ink(side.ws, after)
    assert [s.id for s in redrawn.strokes] == list(change["stroke_ids"]) and redrawn.strokes != drawn.strokes
    assert storage.open_path(side.ws, after["ink_url"].rsplit("/", 1)[1]) is not None


def test_a_pulled_comment_and_colour_land_on_every_kind_and_again_change_nothing(side, library):
    page = _page(side.c, "Annotation kinds updated")
    made = {}
    for name in KINDS:
        data, pdf_page = _item(library, name)
        made[name] = (zl.new_annotation(side.ws, za.from_item(data, pdf_page), block_id=f"zlAk{name[:6]}"), pdf_page)
    zl.apply(side.ws, page, lambda conn: zl.annotation_inserts(conn, page, [b for b, _ in made.values()]))
    for name, (made_block, pdf_page) in made.items():
        block = _block(side.ws, made_block["id"])
        drawn = zl.read_ink(side.ws, block["properties"])
        ids = [s.id for s in drawn.strokes] if drawn else ()
        kind = _item(library, name)[0]["annotationType"]  # the row's zotero_type: a note's box takes no colour
        change = za.apply_fields(block, pdf_page, {"comment": "changed here", "color": "#a28ae5"}, ink_file=drawn,
                                 stroke_ids=ids, key=library.made["keys"][name], zotero_type=kind)
        op = zl.annotation_update(side.ws, block["id"], change)
        zl.apply(side.ws, page, [op])
        after = _block(side.ws, block["id"])
        assert after["content"] == "changed here", name
        props = after["properties"]
        if name == "ink_annotation":
            assert props["ink_url"] != block["properties"]["ink_url"]
            assert {s.color for s in zl.read_ink(side.ws, props).strokes} == {"#a28ae5"}
        elif name == "text_annotation":
            assert props["text_box"]["color"] == "#a28ae5"
        elif name == "note_annotation":  # a note's box has no colour of Zotero's
            assert props["text_box"] == block["properties"]["text_box"]
        else:
            assert props["color"].startswith("#a28ae5"), name
        drawn = zl.read_ink(side.ws, props)
        again = za.apply_fields(after, pdf_page, {"comment": "changed here", "color": "#a28ae5"}, ink_file=drawn,
                                stroke_ids=[s.id for s in drawn.strokes] if drawn else (), zotero_type=kind)
        assert zl.annotation_update(side.ws, block["id"], again) is None, name


def test_an_annotation_zotero_deleted_goes_alone(side):
    page = _page(side.c, "Annotation delete")
    hl = _insert(side.c, page, "zlAd1", "my note", **_placed())
    _insert(side.c, page, "zlAd2", "a note under it", parent=hl)
    _insert(side.c, page, "zlAd3", "", parent=hl, **_placed("nested annotation"))
    _insert(side.c, page, "zlAd4", "next")
    zl.apply(side.ws, page, lambda conn: zl.annotation_delete(conn, hl))
    assert [k for k, _ in _kids(side.c, page)] == ["zlAd2", "zlAd3", "zlAd4"]
    with connect_pages_db(side.ws) as conn:
        assert zl.annotation_delete(conn, hl) == []
    gone = _insert(side.c, page, "zlAd5", "", **_placed())
    _insert(side.c, page, "zlAd6", "under", parent=gone)
    zl.apply(side.ws, page, lambda conn: zl.annotation_delete(conn, gone, keep_children=False))
    assert _block(side.ws, "zlAd6") is None


# --- the PDF -------------------------------------------------------------------------------

def _pdf(text):
    from zotero_fixtures import pdf_bytes
    return pdf_bytes(text)


def test_set_pdf_replaces_the_file_as_zotero_without_the_one_page_check(side):
    from zotero_fixtures import pdf_bytes

    old_doc, _ = storage.store_pdf(side.ws, _pdf("dominant sequence transduction models in a first draft"))
    new_doc, _ = storage.store_pdf(side.ws, pdf_bytes())
    holder = _page(side.c, "Holds the new file")
    assert side.c.post(f"/api/pages/{holder}/attachment", json={"doc_id": new_doc}).status_code == 200
    page = _page(side.c, "Replaced from Zotero")
    assert side.c.post(f"/api/pages/{page}/attachment", json={"doc_id": old_doc}).status_code == 200
    zl.apply(side.ws, page, [zl.zotero_pdf_op(page, {"key": "ATTA2345", "state": "queued", "reason": "", "size": 1,
                                                    "filename": "x.pdf"})])  # the mark set_pdf takes off
    hl = _insert(side.c, page, "zlSp1", "", **_placed("dominant sequence transduction models"))
    # the request's path keeps its one-page-per-PDF answer
    r = side.c.put(f"/api/pages/{page}/attachment", json={"doc_id": new_doc})
    assert r.status_code == 409 and r.json() == {"detail": "attachment belongs to another page", "page_id": holder}
    with pytest.raises(zl.Changed):
        zl.set_pdf(side.ws, page, new_doc, owner=side.owner, check=lambda conn: False)
    assert _block(side.ws, page)["properties"]["doc_id"] == old_doc
    with _Heard() as heard:
        done = zl.set_pdf(side.ws, page, new_doc, owner=side.owner, original_filename="Vaswani 2017.pdf",
                          rows=_row("ATTA2345", page))
    assert (done["incoming"], done["outgoing"]) == (new_doc, old_doc) and done["job"] is not None
    assert [bid for bid, _ in done["placed"]] == [hl] and all(r["client"] == "zotero" for r in done["results"])
    assert ("zotero", page) in heard.calls and _rows(side.ws)["ATTA2345"] == page
    props = _block(side.ws, page)["properties"]
    assert props["doc_id"] == new_doc and "zotero_pdf" not in props
    assert tuple(_last_log(side.ws, page))[1] == "zotero"
    finished = jobs.wait(done["job"]["id"])
    assert (finished["state"], finished["owner"], finished["kind"]) == ("done", side.owner, "relocate-highlights")
    # the same file again: no op, the rows still written
    again = zl.set_pdf(side.ws, page, new_doc, owner=side.owner, original_filename="Vaswani 2017.pdf",
                       rows=_row("ATTA2345", "again"))
    assert again["results"] == [] and _rows(side.ws)["ATTA2345"] == "again"
    with pytest.raises(ops.OpError) as e:
        zl.set_pdf(side.ws, "zlNoSuchPage", new_doc, owner=side.owner)
    assert e.value.status == 404


def test_set_pdf_gives_a_page_without_a_pdf_its_first(side):
    doc, _ = storage.store_pdf(side.ws, _pdf("a first file from Zotero"))
    page = _page(side.c, "Untitled")
    done = zl.set_pdf(side.ws, page, doc, owner=side.owner, original_filename="From Zotero.pdf")
    got = _block(side.ws, page)
    assert done["job"] is None and done["relocation_error"] == "" and done["placed"] == []
    assert got["properties"]["doc_id"] == doc and got["content"] == "From Zotero.pdf"  # the automatic title
