"""Two writers, one text box (docs/dev/text_boxes.md "Merge"): a `set` of
`text_box` whose base_props names the box it was made from is merged key
by key into the box stored now (gamma/text_box.py merge_text_box, wired in
gamma/ops.py). The rule's own cases are tests/shared/textboxmerge.json
(test_shared_fixtures.py)."""

from conftest import make_page

BOX = {"x": 10, "y": 10, "w": 60, "h": 23, "auto": True, "size": 12, "color": "#1f1f1f", "bg": None}


def _ops(client, page_id, ops, client_id):
    r = client.post(f"/api/pages/{page_id}/ops", json={"client": client_id, "ops": ops})
    assert r.status_code == 200, r.text
    return r.json()


def _box_page(guest, title, box_id):
    page = make_page(guest, title)["id"]
    _ops(guest, page, [{"op": "insert", "id": box_id, "parent": page, "position": "a0", "content": "Hello",
                        "props": {"text_box": BOX, "pdf_page": 1}}], "alice")
    return page


def test_a_keystroke_made_before_a_move_arrived_keeps_the_move(guest):
    page = _box_page(guest, "Box merge", "tbm1")
    # Alice drags the box to x=200 ...
    _ops(guest, page, [{"op": "set", "id": "tbm1", "props": {"text_box": {**BOX, "x": 200}},
                        "base_props": {"text_box": BOX}}], "alice")
    # ... while Bob, who has not got the move yet, types: his flush carries
    # the whole box as he measured it, and the box he measured it from
    res = _ops(guest, page, [{"op": "set", "id": "tbm1", "content": "Hello world", "base": "Hello",
                              "props": {"text_box": {**BOX, "w": 101.5}}, "base_props": {"text_box": BOX}}], "bob")
    merged = {**BOX, "x": 200, "w": 101.5}
    assert res["ops"][0]["props"]["text_box"] == merged, "the echo carries the merged box"
    b = guest.get("/api/blocks/tbm1").json()
    assert b["content"] == "Hello world"
    assert b["properties"]["text_box"] == merged
    assert b["properties"]["pdf_page"] == 1, "other keys are untouched"
    # the others follow the log: its batch names the merged box too
    log = guest.get(f"/api/pages/{page}/ops?since={res['seq'] - 1}").json()["batches"]
    assert log[-1]["ops"][0]["props"]["text_box"] == merged


def test_a_restyle_and_a_resize_made_at_once_both_hold(guest):
    page = _box_page(guest, "Box merge restyle", "tbm2")
    _ops(guest, page, [{"op": "set", "id": "tbm2", "props": {"text_box": {**BOX, "size": 24, "h": 38, "w": 120}},
                        "base_props": {"text_box": BOX}}], "alice")
    _ops(guest, page, [{"op": "set", "id": "tbm2", "props": {"text_box": {**BOX, "w": 150, "auto": False}},
                        "base_props": {"text_box": BOX}}], "bob")
    got = guest.get("/api/blocks/tbm2").json()["properties"]["text_box"]
    # the width both set is the later writer's; the size and height are the restyle's
    assert got == {**BOX, "size": 24, "h": 38, "w": 150, "auto": False}


def test_a_writer_that_names_no_base_box_replaces_it(guest):
    page = _box_page(guest, "Box merge whole", "tbm3")
    _ops(guest, page, [{"op": "set", "id": "tbm3", "props": {"text_box": {**BOX, "x": 200}}}], "alice")
    _ops(guest, page, [{"op": "set", "id": "tbm3", "props": {"text_box": {**BOX, "w": 101.5}}}], "bob")
    assert guest.get("/api/blocks/tbm3").json()["properties"]["text_box"] == {**BOX, "w": 101.5}
    # nor does a base for another key make it a merge
    _ops(guest, page, [{"op": "set", "id": "tbm3", "props": {"text_box": {**BOX, "y": 90}},
                        "base_props": {"ink_url": ""}}], "alice")
    assert guest.get("/api/blocks/tbm3").json()["properties"]["text_box"] == {**BOX, "y": 90}


def test_removing_the_box_with_a_base_removes_it(guest):
    page = _box_page(guest, "Box merge delete", "tbm4")
    _ops(guest, page, [{"op": "set", "id": "tbm4", "props": {"text_box": {**BOX, "x": 200}},
                        "base_props": {"text_box": BOX}}], "alice")
    _ops(guest, page, [{"op": "set", "id": "tbm4", "props": {"text_box": None}, "base_props": {"text_box": BOX}}], "bob")
    assert "text_box" not in guest.get("/api/blocks/tbm4").json()["properties"]


def test_the_block_endpoint_merges_with_base_properties(guest):
    page = _box_page(guest, "Box merge put", "tbm5")
    _ops(guest, page, [{"op": "set", "id": "tbm5", "props": {"text_box": {**BOX, "x": 200}}}], "alice")
    r = guest.put("/api/blocks/tbm5", json={"properties": {"text_box": {**BOX, "bg": "#fff4b8"}, "pdf_page": 1},
                                           "base_properties": {"text_box": BOX}})
    assert r.status_code == 200, r.text
    assert guest.get("/api/blocks/tbm5").json()["properties"]["text_box"] == {**BOX, "x": 200, "bg": "#fff4b8"}


def test_the_mirror_diff_names_the_box_a_change_was_made_from():
    """gamma/sync_tree.py: a copy's push merges key by key on the remote too
    (the fixture's cases pin the rest, tests/shared/synctree.json)."""
    from gamma import sync_tree
    base = {"pg": {"parent": "root", "position": "a0", "content": "P", "props": {}},
            "t": {"parent": "pg", "position": "a0", "content": "x", "props": {"text_box": BOX, "pdf_page": 1}}}
    target = {**base, "t": {**base["t"], "props": {"text_box": {**BOX, "x": 40}, "pdf_page": 1}}}
    assert sync_tree.diff(base, target, "pg") == [
        {"op": "set", "id": "t", "props": {"text_box": {**BOX, "x": 40}}, "base_props": {"text_box": BOX}}]
    assert sync_tree.diff(base, target, "pg", with_base=False) == [
        {"op": "set", "id": "t", "props": {"text_box": {**BOX, "x": 40}}}]
