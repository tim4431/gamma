"""Sub-pages and the Zotero link (docs/research/sub-pages.md "Integrations
and the agent"), against the fake Zotero: a linked paper moved inside
another page takes that page's top's folders' collections, never none;
refiling the top refiles the items of the pages inside it; Zotero's change
of a sub-page's collections stores no folders on it and gets the top's
back; a new sub-page with a record goes to Zotero in its top's collections.
Accounts are ``zsp_…``."""

from zotero_engine import (LIB, _block, _edit, _link, _paper, _paths, _rows, _sync, background, fake,  # noqa: F401
                           idle, made, me, no_relocation, owner_id)

from gamma.zotero_web import object_key

USER, PASSWORD = "zsp_owner", "pw-zsp_owner-123"


def _set(ws, owner, page_id, **props):
    _edit(ws, owner, page_id, [{"op": "set", "id": page_id, "props": props}])


def test_a_sub_page_is_filed_in_zotero_where_its_top_is(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    paths = _paths(ws)
    paper = _rows(ws, "item")[keys["attention"]]["page_id"]
    top = _paper(ws, "zspTop", "A review", folders=[paths[("Reading",)]])
    _sync(me, ws)
    # Moved inside the review: its item follows the review's folder, not out of every collection
    _set(ws, owner_id, paper, parent_page=top)
    _sync(me, ws)
    assert "folders" not in _block(ws, paper)["props"]
    assert fake.data(LIB, keys["attention"])["collections"] == [keys["reading"]]
    # Zotero edits the paper's title: pulled, so the paper's newest change is the link's own write
    fake.edit(LIB, keys["attention"], title="Attention, revised")
    _sync(me, ws)
    assert _block(ws, paper)["content"] == "Attention, revised"
    # The review refiled: the paper inside it, which no change of its own lists, is refiled in Zotero too
    _set(ws, owner_id, top, folders=[paths[("ML",)]])
    _sync(me, ws)
    assert fake.data(LIB, keys["attention"])["collections"] == [keys["ml"]]
    # Zotero files the item elsewhere: the sub-page gets no folders, and Zotero gets its top's back
    fake.edit(LIB, keys["attention"], collections=[keys["transformers"]])
    _sync(me, ws)
    assert "folders" not in _block(ws, paper)["props"]
    assert fake.data(LIB, keys["attention"])["collections"] == [keys["ml"]]
    # A new paper made inside the review goes to Zotero in the review's collections
    inner = _paper(ws, "zspInner", "A paper inside", parent_page=top, meta={"doi": "10.1000/zsp.inner"})
    _sync(me, ws)
    data = fake.data(LIB, object_key(ws, LIB, "item", "", inner, ""))
    assert data["collections"] == [keys["ml"]] and "folders" not in _block(ws, inner)["props"]
