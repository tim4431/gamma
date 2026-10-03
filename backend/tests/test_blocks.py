"""Core data model: block CRUD, tree replacement, ordering, search, replace,
and the cleanup that must happen on delete."""

import json

from conftest import guest_name, make_page, workspace_of
from gamma.db import connect_pages_db


def test_create_and_subtree(guest):
    page = make_page(guest, "Tree page")
    tree = [
        {"id": "n1", "content": "parent note", "properties": {}, "children": [
            {"id": "n2", "content": "child note", "properties": {}, "children": []},
        ]},
    ]
    r = guest.put(f"/api/blocks/{page['id']}/children", json={"blocks": tree})
    assert r.status_code == 200
    r = guest.get(f"/api/blocks/{page['id']}/subtree")
    assert r.status_code == 200
    kids = r.json()["block"]["children"]
    assert kids[0]["content"] == "parent note"
    assert kids[0]["children"][0]["content"] == "child note"


def test_sibling_order_is_lexicographic_on_position(guest):
    page = make_page(guest, "Order page")
    first = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "first"}).json()
    second = guest.post("/api/blocks", json={
        "parent_id": page["id"], "content": "second", "before": first["position"],
    }).json()
    assert first["position"] < second["position"]
    # insert BETWEEN first and second
    middle = guest.post("/api/blocks", json={
        "parent_id": page["id"], "content": "middle",
        "before": first["position"], "after": second["position"],
    }).json()
    assert first["position"] < middle["position"] < second["position"]
    r = guest.get(f"/api/blocks/{page['id']}/children")
    contents = [b["content"] for b in r.json()["children"]]
    assert contents == ["first", "middle", "second"]


def test_block_search(guest):
    page = make_page(guest, "Search page")
    guest.post("/api/blocks", json={"parent_id": page["id"], "content": "the zorbly quux appears"})
    r = guest.get("/api/block-search", params={"q": "zorbly"})
    assert any("zorbly" in b["content"] for b in r.json()["blocks"])
    # case-sensitive: no match for wrong case
    r = guest.get("/api/block-search", params={"q": "ZORBLY", "case": 1})
    assert not any("zorbly" in b["content"] for b in r.json()["blocks"])


def test_block_search_is_separator_tolerant(guest):
    page = make_page(guest, "Continuous operation of a coherent 3,000-qubit system")
    r = guest.get("/api/block-search", params={"q": "3000"})
    hit = next((b for b in r.json()["blocks"] if b["id"] == page["id"]), None)
    assert hit, "'3000' should match the '3,000-qubit' title"
    assert hit["kind"] == "page"


def test_block_search_reports_kinds(guest):
    page = make_page(guest, "Kinds page")
    guest.post("/api/blocks", json={"parent_id": page["id"], "content": "a plaino note"})
    r = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "a hilite quote"})
    guest.put(f"/api/blocks/{r.json()['id']}", json={"properties": {"pdf_position": {"pageNumber": 1}}})
    r = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "a linky region"})
    guest.put(f"/api/blocks/{r.json()['id']}",
              json={"properties": {"pdf_position": {"pageNumber": 1}, "link_page_id": page["id"]}})
    # A text box on a PDF page has no quote: a text box, not a highlight
    # (the search panel lists it with the notes).
    guest.post("/api/blocks", json={"parent_id": page["id"], "content": "a boxy text",
                                    "properties": {"text_box": {"x": 10, "y": 20}, "pdf_page": 1}})

    kinds = {b["content"]: b["kind"]
             for q in ("plaino", "hilite", "linky", "boxy")
             for b in guest.get("/api/block-search", params={"q": q, "limit": 50}).json()["blocks"]}
    assert kinds["a plaino note"] == "note"
    assert kinds["a hilite quote"] == "highlight"
    assert kinds["a linky region"] == "link"
    assert kinds["a boxy text"] == "text_box"


def test_delete_purges_chats(guest):
    page = make_page(guest, "Doomed page")
    r = guest.put(f"/api/chats/{page['id']}", json={"messages": [{"role": "user", "text": "hi"}]})
    assert r.status_code == 200
    assert guest.get(f"/api/chats/{page['id']}").json()["messages"]
    r = guest.delete(f"/api/blocks/{page['id']}")
    assert r.status_code == 200
    # in Recently deleted the chat stays; deleted for good, it goes
    assert guest.get(f"/api/chats/{page['id']}").json()["messages"]
    assert guest.delete(f"/api/trash/{page['id']}").status_code == 200
    assert guest.get(f"/api/chats/{page['id']}").json()["messages"] == []


def test_properties_merge_not_replace(guest):
    page = make_page(guest, "Props page", properties={"folder": "A"})
    guest.put(f"/api/blocks/{page['id']}", json={"properties": {"category": "x"}})
    r = guest.get(f"/api/blocks/{page['id']}/subtree")
    props = r.json()["block"]["properties"]
    assert props["folder"] == "A" and props["category"] == "x"


def _store_props(block_id, raw):
    with connect_pages_db(workspace_of(guest_name())) as conn:
        conn.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?", (raw, block_id))


def _stored_props(block_id):
    with connect_pages_db(workspace_of(guest_name())) as conn:
        return conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()[0]


def test_tree_reads_answer_what_the_stored_text_says(guest):
    # orjson parses and encodes the tree reads (blocks_store.load_json,
    # routers/blocks.TreeJSON). The answers are the stored values; what
    # orjson refuses and json.dumps wrote (NaN, half an emoji) is read as
    # before, then sent as null and U+FFFD.
    page = make_page(guest, "Stored JSON page")
    plain = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "plain"}).json()
    odd = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "odd"}).json()
    emoji = chr(0x1F600)
    plain_raw = json.dumps({"emoji": emoji, "word": "café", "n": 1.5, "big": 10 ** 20})
    odd_raw = json.dumps({"half": "a" + chr(0xD83D) + "b", "nan": float("nan"), "word": "café"})
    assert plain_raw.isascii() and odd_raw.isascii() and "NaN" in odd_raw  # as json.dumps stores them
    _store_props(plain["id"], plain_raw)
    _store_props(odd["id"], odd_raw)
    want = {plain["id"]: {"emoji": emoji, "word": "café", "n": 1.5, "big": 10 ** 20},
            odd["id"]: {"half": "a" + chr(0xFFFD) + "b", "nan": None, "word": "café"}}

    sub = guest.get(f"/api/blocks/{page['id']}/subtree")
    assert sub.status_code == 200, sub.text
    assert {b["id"]: b["properties"] for b in sub.json()["block"]["children"]} == want
    kids = guest.get(f"/api/blocks/{page['id']}/children")
    assert kids.status_code == 200, kids.text
    assert {b["id"]: b["properties"] for b in kids.json()["children"]} == want
    assert guest.get(f"/api/blocks/{plain['id']}").json()["properties"] == want[plain["id"]]
    # reads never rewrite the stored text, and a write keeps json.dumps' shape
    assert (_stored_props(plain["id"]), _stored_props(odd["id"])) == (plain_raw, odd_raw)
    assert guest.put(f"/api/blocks/{plain['id']}", json={"properties": {"word": "naïve"}}).status_code == 200
    stored = _stored_props(plain["id"])
    assert stored.isascii() and '"word": ' + json.dumps("naïve") in stored


def test_a_tree_deeper_than_orjson_nests_still_answers(guest):
    # orjson stops at 255 levels of nesting, about 125 blocks; the subtree
    # read falls back to the standard encoder.
    page = make_page(guest, "Deep page")
    depth = 140
    tree = node = {"id": "deep0", "content": "level 0", "properties": {}, "children": []}
    for i in range(1, depth):
        child = {"id": f"deep{i}", "content": f"level {i}", "properties": {}, "children": []}
        node["children"].append(child)
        node = child
    assert guest.put(f"/api/blocks/{page['id']}/children", json={"blocks": [tree]}).status_code == 200
    r = guest.get(f"/api/blocks/{page['id']}/subtree")
    assert r.status_code == 200, r.text
    node, levels = r.json()["block"], 0
    while node["children"]:
        node, levels = node["children"][0], levels + 1
    assert levels == depth and node["content"] == f"level {depth - 1}"
