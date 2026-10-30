"""Two writers, one picture (docs/dev/pictures.md): a `set` of `picture`
whose base_props names the picture it was changed from is merged key by key
into the picture stored now (gamma/picture.py merge_picture, wired in
gamma/ops.py PLACED_KEYS beside the text box's), and the mirror's diff and
resend do the same (gamma/sync_tree.py, gamma/sync_engine.py). The rule's
own cases are tests/shared/picturemerge.json (test_shared_fixtures.py)."""

from conftest import make_page

PIC = {"url": "/api/uploads/3f2a9c.png", "x": 10, "y": 10, "w": 100, "h": 80, "angle": 0,
       "crop": {"x": 0, "y": 0, "w": 1, "h": 1}, "flip_h": False, "flip_v": False}
CROP = {"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.5}


def _ops(client, page_id, ops, client_id):
    r = client.post(f"/api/pages/{page_id}/ops", json={"client": client_id, "ops": ops})
    assert r.status_code == 200, r.text
    return r.json()


def _picture_page(guest, title, pic_id):
    page = make_page(guest, title)["id"]
    _ops(guest, page, [{"op": "insert", "id": pic_id, "parent": page, "position": "a0", "content": "A figure",
                        "props": {"picture": PIC, "pdf_page": 1}}], "alice")
    return page


def _picture(guest, pic_id):
    return guest.get(f"/api/blocks/{pic_id}").json()["properties"]["picture"]


def test_a_crop_made_before_a_turn_arrived_keeps_the_turn(guest):
    page = _picture_page(guest, "Picture merge", "pm1")
    # Alice turns the picture and mirrors it ...
    _ops(guest, page, [{"op": "set", "id": "pm1", "props": {"picture": {**PIC, "angle": 30, "flip_h": True}},
                        "base_props": {"picture": PIC}}], "alice")
    # ... while Bob, who has not got the turn yet, crops it (the frame
    # shrinks to the part kept): his set carries the whole picture as he
    # left it, and the picture he changed it from
    crop = {**PIC, "x": 30, "y": 25, "w": 60, "h": 40, "crop": CROP}
    res = _ops(guest, page, [{"op": "set", "id": "pm1", "props": {"picture": crop},
                              "base_props": {"picture": PIC}}], "bob")
    merged = {**crop, "angle": 30, "flip_h": True}
    assert res["ops"][0]["props"]["picture"] == merged, "the echo carries the merged picture"
    b = guest.get("/api/blocks/pm1").json()
    assert b["properties"]["picture"] == merged and b["kind"] == "picture"
    assert b["properties"]["pdf_page"] == 1 and b["content"] == "A figure", "other keys are untouched"
    log = guest.get(f"/api/pages/{page}/ops?since={res['seq'] - 1}").json()["batches"]
    assert log[-1]["ops"][0]["props"]["picture"] == merged


def test_a_move_keeps_a_crop_and_a_turn_keeps_a_flip(guest):
    page = _picture_page(guest, "Picture merge keys", "pm2")
    _ops(guest, page, [{"op": "set", "id": "pm2", "props": {"picture": {**PIC, "w": 60, "h": 40, "crop": CROP}},
                        "base_props": {"picture": PIC}}], "alice")
    _ops(guest, page, [{"op": "set", "id": "pm2", "props": {"picture": {**PIC, "x": 200}},
                        "base_props": {"picture": PIC}}], "bob")
    assert _picture(guest, "pm2") == {**PIC, "x": 200, "w": 60, "h": 40, "crop": CROP}, "the crop compared by value"
    now = _picture(guest, "pm2")
    _ops(guest, page, [{"op": "set", "id": "pm2", "props": {"picture": {**now, "flip_h": True}},
                        "base_props": {"picture": now}}], "alice")
    _ops(guest, page, [{"op": "set", "id": "pm2", "props": {"picture": {**now, "angle": -90}},
                        "base_props": {"picture": now}}], "bob")
    assert _picture(guest, "pm2") == {**now, "flip_h": True, "angle": -90}


def test_the_merge_stores_the_picture_normalized(guest):
    page = _picture_page(guest, "Picture merge normalized", "pm3")
    _ops(guest, page, [{"op": "set", "id": "pm3", "props": {"picture": {**PIC, "x": -20000, "angle": 200, "extra": 1}},
                        "base_props": {"picture": PIC}}], "alice")
    assert _picture(guest, "pm3") == {**PIC, "x": -14400, "angle": -160}  # x clamped to -MAX_COORD


def test_a_writer_that_names_no_base_picture_replaces_it(guest):
    page = _picture_page(guest, "Picture merge whole", "pm4")
    _ops(guest, page, [{"op": "set", "id": "pm4", "props": {"picture": {**PIC, "x": 200}}}], "alice")
    _ops(guest, page, [{"op": "set", "id": "pm4", "props": {"picture": {**PIC, "crop": CROP}}}], "bob")
    assert _picture(guest, "pm4") == {**PIC, "crop": CROP}
    # nor does a base for another key make it a merge
    _ops(guest, page, [{"op": "set", "id": "pm4", "props": {"picture": {**PIC, "y": 90}},
                        "base_props": {"text_box": {"x": 1}}}], "alice")
    assert _picture(guest, "pm4") == {**PIC, "y": 90}


def test_removing_the_picture_with_a_base_removes_it(guest):
    page = _picture_page(guest, "Picture merge delete", "pm5")
    _ops(guest, page, [{"op": "set", "id": "pm5", "props": {"picture": {**PIC, "x": 200}},
                        "base_props": {"picture": PIC}}], "alice")
    _ops(guest, page, [{"op": "set", "id": "pm5", "props": {"picture": None}, "base_props": {"picture": PIC}}], "bob")
    b = guest.get("/api/blocks/pm5").json()
    assert "picture" not in b["properties"] and b["kind"] == "note"


def test_a_text_box_and_a_picture_merge_in_one_batch(guest):
    """Each placed key by its own rule, side by side."""
    box = {"x": 10, "y": 10, "w": 60, "h": 23, "auto": True, "size": 12, "color": "#1f1f1f", "bg": None}
    page = _picture_page(guest, "Picture merge beside a box", "pm6")
    _ops(guest, page, [{"op": "insert", "id": "pm6box", "parent": page, "position": "a1", "content": "Hi",
                        "props": {"text_box": box, "pdf_page": 1}}], "alice")
    _ops(guest, page, [{"op": "set", "id": "pm6", "props": {"picture": {**PIC, "x": 200}}},
                       {"op": "set", "id": "pm6box", "props": {"text_box": {**box, "x": 200}}}], "alice")
    _ops(guest, page, [{"op": "set", "id": "pm6", "props": {"picture": {**PIC, "angle": 45}},
                        "base_props": {"picture": PIC}},
                       {"op": "set", "id": "pm6box", "props": {"text_box": {**box, "size": 24}},
                        "base_props": {"text_box": box}}], "bob")
    assert _picture(guest, "pm6") == {**PIC, "x": 200, "angle": 45}
    assert guest.get("/api/blocks/pm6box").json()["properties"]["text_box"] == {**box, "x": 200, "size": 24}


def test_the_block_endpoint_merges_with_base_properties(guest):
    page = _picture_page(guest, "Picture merge put", "pm7")
    _ops(guest, page, [{"op": "set", "id": "pm7", "props": {"picture": {**PIC, "x": 200}}}], "alice")
    r = guest.put("/api/blocks/pm7", json={"properties": {"picture": {**PIC, "flip_v": True}, "pdf_page": 1},
                                           "base_properties": {"picture": PIC}})
    assert r.status_code == 200, r.text
    assert r.json()["properties"]["picture"] == {**PIC, "x": 200, "flip_v": True}, "the answer names the merge"
    assert _picture(guest, "pm7") == {**PIC, "x": 200, "flip_v": True}


def test_the_mirror_diff_names_the_picture_a_change_was_made_from():
    """gamma/sync_tree.py: a copy's push merges key by key on the remote too."""
    from gamma import sync_tree
    base = {"pg": {"parent": "root", "position": "a0", "content": "P", "props": {}},
            "i": {"parent": "pg", "position": "a0", "content": "", "props": {"picture": PIC, "pdf_page": 1}}}
    target = {**base, "i": {**base["i"], "props": {"picture": {**PIC, "x": 40}, "pdf_page": 1}}}
    assert sync_tree.diff(base, target, "pg") == [
        {"op": "set", "id": "i", "props": {"picture": {**PIC, "x": 40}}, "base_props": {"picture": PIC}}]
    assert sync_tree.diff(base, target, "pg", with_base=False) == [
        {"op": "set", "id": "i", "props": {"picture": {**PIC, "x": 40}}}]
    # a picture made where there was none names no base
    bare = {**base, "i": {**base["i"], "props": {"pdf_page": 1}}}
    assert sync_tree.diff(bare, target, "pg") == [{"op": "set", "id": "i", "props": {"picture": {**PIC, "x": 40}}}]


def test_a_lost_push_of_a_picture_goes_again_key_by_key():
    """gamma/sync_engine.py _unlanded: a picture change whose answer was lost
    is judged key by key against the remote's picture now, its frame as a
    size and a centre (gamma/picture.py resend_picture). The keys of ours
    it still shows as they were go again, onto its picture and with it as
    the base; keys it shows as ours, or changed since, have landed."""
    from gamma import sync_engine
    op = {"op": "set", "id": "i", "props": {"picture": {**PIC, "x": 40, "crop": CROP}},
          "base_props": {"picture": PIC}}
    base = {"i": {"parent": "pg", "position": "a0", "content": "", "props": {"picture": PIC}}}

    def remote(pic):
        return {"i": {**base["i"], "props": {"picture": pic}}}

    # it never arrived, and someone turned the picture meanwhile: our move
    # and crop go again onto the turned picture
    turned = {**PIC, "angle": 90}
    landed, rest = sync_engine._unlanded(op, base, remote(turned))
    assert landed is None
    assert rest == {"op": "set", "id": "i", "props": {"picture": {**turned, "x": 40, "crop": CROP}},
                    "base_props": {"picture": turned}}
    # the remote shows it: landed, nothing to send
    assert sync_engine._unlanded(op, base, remote({**PIC, "x": 40, "crop": CROP})) == (
        {"op": "set", "id": "i", "props": {"picture": {**PIC, "x": 40, "crop": CROP}}}, None)
    # someone moved it since: the crop goes again, and the shift of the
    # centre that came with it goes onto the moved picture, as the merge
    # puts a crop's shift
    moved = {**PIC, "x": 300}
    landed, rest = sync_engine._unlanded(op, base, remote(moved))
    assert rest["props"]["picture"] == {**moved, "x": 330, "crop": CROP} and rest["base_props"] == {"picture": moved}
    # the remote's value is no picture any more: the plain rule
    landed, rest = sync_engine._unlanded(op, base, remote(None))
    assert rest is None and landed == {"op": "set", "id": "i", "props": op["props"]}


def test_a_lost_push_of_a_picture_goes_again_with_the_frame_the_merge_makes(guest):
    """gamma/picture.py resend_picture: the frame of a lost picture change
    goes again as merge_picture merges it, its size key by key and its
    centre as one, so a resize lost while the remote moved the picture
    lands about the moved centre, as the remote would have merged it had
    the push arrived; a centre both moved stays the remote's."""
    from gamma import sync_engine
    from gamma.picture import merge_picture

    page = _picture_page(guest, "Picture resend", "pr1")
    base = {"pr1": {"parent": page, "position": "a0", "content": "A figure", "props": {"picture": PIC, "pdf_page": 1}}}

    def unlanded(mine, now):
        op = {"op": "set", "id": "pr1", "props": {"picture": mine}, "base_props": {"picture": PIC}}
        return sync_engine._unlanded(op, base, {"pr1": {**base["pr1"], "props": {"picture": now, "pdf_page": 1}}})

    # a resize about the centre, lost while the remote moved the picture:
    # the new size goes again about the moved centre, not at the old top
    resized = {**PIC, "x": 35, "y": 30, "w": 50, "h": 40}
    moved = {**PIC, "x": 200}
    landed, rest = unlanded(resized, moved)
    sent = {**moved, "x": 225, "y": 30, "w": 50, "h": 40}
    assert landed is None and rest == {"op": "set", "id": "pr1", "props": {"picture": sent},
                                       "base_props": {"picture": moved}}
    assert sent == merge_picture(moved, resized, PIC)
    # and the remote takes it as sent
    _ops(guest, page, [{"op": "set", "id": "pr1", "props": {"picture": moved}}], "bob")
    _ops(guest, page, [rest], "sync")
    assert _picture(guest, "pr1") == sent
    # a move lost while the remote resized the picture about its centre:
    # the move goes again, onto the resized picture
    landed, rest = unlanded({**PIC, "x": 110}, resized)
    assert landed is None and rest["props"]["picture"] == {**resized, "x": 135}
    assert rest["props"]["picture"] == merge_picture(resized, {**PIC, "x": 110}, PIC)
    # two moves: the remote's place may be the later one, so ours has landed
    assert unlanded({**PIC, "x": 110}, {**PIC, "x": 300}) == (
        {"op": "set", "id": "pr1", "props": {"picture": {**PIC, "x": 110}}}, None)
    # the remote shows our resize, moved since: landed
    landed, rest = unlanded(resized, {**resized, "x": 300})
    assert rest is None and landed["props"]["picture"] == resized
