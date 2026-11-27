"""Drawing on pages (gamma/ai_draw.py; the registry's draw, add_text_box,
place_picture and add_sheet, docs/dev/ai_tools.md "Drawing on pages"):
shapes as one new handwriting group anchored to a passage or placed by
fractions, on a PDF page and on a page of paper; a text box in the empty
space beside a passage; a picture fitted with its shape kept; a page of
paper after another; what is refused; and the revert that takes a drawing
back, puts it back with its properties, and stops once someone drew into
it or commented on it."""

import io
import json

import pytest

from conftest import account_of, doc_props, login, make_page, make_user, thread_on, workspace_of
from gamma import agents, ai_revert, ink as inkmod, storage
from gamma.ai_tools.registry import run_agent_tool
from gamma.blocks_store import load_props
from gamma.db import connect_pages_db
from gamma.ops import apply_ops

USER = "draw_tim"
LINE = "The loss is defined as the mean squared error of the fit."
QUOTE = "mean squared error"


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for n, text in enumerate((LINE, "We train the network by gradient descent on the data.")):
        page = doc.new_page(width=600, height=800)
        page.insert_text((72, 100), text, fontsize=11)
        page.insert_text((72, 140), f"A second sentence on page {n + 1} of the paper.", fontsize=11)
    return doc.tobytes()


def _png(width: int, height: int) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture(scope="module")
def tim(client):
    make_user(USER, "pw")
    c = login(USER, "pw")
    up = c.post("/api/uploads", files={"file": ("paper.pdf", _pdf(), "application/pdf")})
    assert up.status_code == 200, up.text
    pdf = make_page(c, "The paper", properties=doc_props(up.json()["doc_id"]))["id"]
    notes = make_page(c, "Sketches")["id"]
    sheet = c.post("/api/blocks", json={"parent_id": notes, "content": "", "properties": {
        "sheet": {"width": 400, "height": 600, "pattern": "grid"}, "collapsed": True}}).json()["id"]
    c.pages = {"pdf": pdf, "notes": notes, "sheet": sheet}
    yield c
    c.close()


def _chat(page_id=None, **more):
    """Gamma AI's scope, as routers/ai.py builds it: every tool allowed."""
    where = {"type": "page", "page_id": page_id} if page_id else {"type": "folder", "folder": ""}
    return {**where, "actor": account_of(USER), "can_write": True, "can_comment": True,
            "agent": agents.stamp(agents.gamma(), USER), **more}


def _run(name, args, scope=None):
    return run_agent_tool(workspace_of(USER), scope or _chat(), name, args)


def _block(block_id):
    with connect_pages_db(workspace_of(USER)) as conn:
        row = conn.execute("SELECT parent_id, content, properties FROM unified_blocks WHERE id = ?",
                           (block_id,)).fetchone()
    return (row[0], row[1], load_props(row[2])) if row else None


def _ink(props) -> dict:
    """The group's stored file as a client reads it, checked as an upload is."""
    name = props["ink_url"].rsplit("/", 1)[1]
    data = storage.find_upload_file(name, workspace_of(USER)).read_bytes()
    inkmod.parse_ink(data)
    return json.loads(data)


def _quote_box(page_id):
    """The quote's box on PDF page 1, as the writing tools find it."""
    from gamma.ai_context import pdf_path
    from gamma.annotations.places import passage_at
    from gamma.documents.model import file_hash, page_document

    with connect_pages_db(workspace_of(USER)) as conn:
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    position, _ = passage_at(pdf_path(workspace_of(USER), file_hash(page_document(load_props(row[0])))), QUOTE, 1)
    r = position["boundingRect"]
    return r["x1"], r["y1"], r["x2"], r["y2"]


def test_a_box_around_a_passage_and_an_arrow_to_it(tim):
    pdf = tim.pages["pdf"]
    text, action = _run("draw", {"page_id": pdf, "pdf_page": 1, "shapes": [
        {"kind": "rect", "around": QUOTE}, {"kind": "arrow", "from": [0.8, 0.3], "to_quote": QUOTE}]})
    assert not action.get("error"), text
    assert text.startswith("ok — drew 2 shapes: a rectangle around “mean squared error”, an arrow to "
                           "“mean squared error” on PDF page 1 of “The paper”")
    # one handwriting group at the page's top level, the agent's, placed on page 1
    parent, content, props = _block(action["block_id"])
    assert (parent, content, props["ink_strokes"]) == (pdf, "", 2)
    assert props["agent"] == {"id": "gamma", "name": "Gamma AI", "for": USER}
    assert props["pdf_position"]["pageNumber"] == 1
    ink = _ink(props)
    assert ink["space"] == {"kind": "pdf-page", "page": 1, "width": 600, "height": 800}
    rect, arrow = ink["strokes"]
    x0, y0, x1, y1 = _quote_box(pdf)
    p = rect["shape"]["params"]
    # the rectangle holds the passage with room around it, in the pens' red
    assert p["cx"] - p["w"] / 2 < x0 and p["cx"] + p["w"] / 2 > x1 and p["cy"] - p["h"] / 2 < y0 < y1 < p["cy"] + p["h"] / 2
    assert (rect["color"], rect["size"], rect["pen"]) == ("#dc2626", 2, False)
    # the arrow's tip lands on the passage's padded box, on the side facing its tail
    a = arrow["shape"]["params"]
    assert arrow["shape"]["kind"] == "arrow" and (a["x1"], a["y1"]) == (480, 240)
    assert x0 - 4 <= a["x2"] <= x1 + 4 and y1 <= a["y2"] <= y1 + 4
    # the model sees what it drew; the chip shows the group, and the reply can revert it
    assert action["picture"] == f"/api/ai/ink-image/{action['block_id']}" and action["images"]
    assert action["kind"] == "create" and action["revert"]["props"] == props


def test_shapes_on_a_page_of_paper(tim):
    sheet = tim.pages["sheet"]
    text, action = _run("draw", {"sheet_id": sheet, "color": "blue", "shapes": [
        {"kind": "ellipse", "box": [0.1, 0.1, 0.5, 0.3], "fill": True},
        {"kind": "curve", "points": [[0.1, 0.5], [0.3, 0.6], [0.5, 0.5]], "smooth": True, "dash": "dashed"}]})
    assert not action.get("error"), text
    parent, _, props = _block(action["block_id"])
    assert parent == sheet and "pdf_position" not in props  # a sheet's group is its child
    ink = _ink(props)
    assert ink["space"] == {"kind": "canvas", "width": 400, "height": 600}
    ellipse, curve = ink["strokes"]
    assert ellipse["shape"]["params"]["cx"] == 120 and ellipse["fill"] == {"color": "#1d4ed8"}
    assert curve["shape"]["params"]["smooth"] is True and curve["dash"] == "dashed"


def test_what_draw_refuses(tim):
    pdf, notes = tim.pages["pdf"], tim.pages["notes"]
    refusals = [
        ({"page_id": pdf, "pdf_page": 1, "shapes": [{"kind": "rect", "around": "a sentence nobody wrote here"}]},
         "that quote is not on PDF page 1 or beside it"),
        ({"page_id": pdf, "pdf_page": 1, "shapes": [{"kind": "rect", "around": "gradient descent on the data"}]},
         "that quote is on PDF page 2, not on page 1"),
        ({"page_id": pdf, "pdf_page": 1, "shapes": [{"kind": "rect", "box": [0.5, 0.5, 1.4, 0.6]}]},
         "shape 1's box is [x0, y0, x1, y1]"),
        ({"page_id": pdf, "pdf_page": 3, "shapes": [{"kind": "line", "from": [0, 0], "to": [1, 1]}]},
         "PDF page 3 does not exist — the PDF has 2 pages"),
        ({"page_id": pdf, "pdf_page": 1, "shapes": [{"kind": "line", "from": [0, 0], "to": [1, 1]}] * 21},
         "at most 20 shapes"),
        ({"page_id": notes, "pdf_page": 1, "shapes": [{"kind": "line", "from": [0, 0], "to": [1, 1]}]},
         "“Sketches” has no PDF".replace("“", '"').replace("”", '"')),
        ({"sheet_id": pdf, "shapes": [{"kind": "line", "from": [0, 0], "to": [1, 1]}]},
         "sheet_id names no page of paper"),
    ]
    with connect_pages_db(workspace_of(USER)) as conn:
        before = conn.execute("SELECT COUNT(*) FROM unified_blocks").fetchone()[0]
    for args, said in refusals:
        text, action = _run("draw", args)
        assert action["error"] and said in text, text
    with connect_pages_db(workspace_of(USER)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM unified_blocks").fetchone()[0] == before  # nothing was made


def test_a_text_box_goes_in_the_space_beside_a_passage(tim):
    pdf = tim.pages["pdf"]
    x0, y0, x1, y1 = _quote_box(pdf)
    boxes = []
    for _ in range(2):
        text, action = _run("add_text_box", {"page_id": pdf, "pdf_page": 1, "text": "Which **error**?",
                                             "near": QUOTE, "background": "yellow"})
        assert not action.get("error"), text
        _, content, props = _block(action["block_id"])
        box = props["text_box"]
        assert (content, props["pdf_page"], box["bg"], box["auto"]) == ("Which **error**?", 1, "#fff4b8", True)
        boxes.append(box)
    for box in boxes:  # clear of the passage's line, and near it
        assert box["y"] + box["h"] <= y0 or box["y"] >= y1 or box["x"] >= x1 or box["x"] + box["w"] <= x0
        assert abs(box["y"] - y0) < 120
    a, b = boxes  # the second keeps clear of the first
    assert a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"] or a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
    text, action = _run("add_text_box", {"page_id": pdf, "pdf_page": 2, "text": "A margin note", "at": [0.1, 0.9],
                                         "width": 0.3})
    box = _block(action["block_id"])[2]["text_box"]
    assert (box["x"], box["y"], box["w"], box["auto"]) == (60, 720, 180, False)
    text, action = _run("add_text_box", {"page_id": pdf, "pdf_page": 1, "text": "word " * 700, "at": [0, 0],
                                         "size": 36})
    assert action["error"] and "does not fit the page" in text


def test_a_picture_is_fitted_with_its_shape_kept(tim):
    from gamma.ai_pictures import store_picture

    url = store_picture(workspace_of(USER), _png(200, 100))["url"]
    text, action = _run("place_picture", {"page_id": tim.pages["pdf"], "pdf_page": 1, "url": url,
                                          "box": [0.1, 0.1, 0.5, 0.5], "caption": "The fit"})
    assert not action.get("error"), text
    _, content, props = _block(action["block_id"])
    pic = props["picture"]
    # 240 wide (the box's width), half as tall, in the middle of the box
    assert (content, pic["url"], pic["x"], pic["y"], pic["w"], pic["h"]) == ("The fit", url, 60, 180, 240, 120)
    text, action = _run("place_picture", {"page_id": tim.pages["pdf"], "pdf_page": 1,
                                          "url": "/api/uploads/" + "0" * 24 + ".png", "at": [0.1, 0.1]})
    assert action["error"] and "names no picture this workspace holds" in text


def test_a_card_carries_what_its_surface_draws_while_it_waits(tim):
    """A placing call's preview names its surface and the shape it would
    put there, in the surface's points: a drawing's strokes as point lists,
    a text box's frame and text, a picture's frame and file; on a page of
    paper, the sheet."""
    from gamma.ai_pictures import store_picture
    from gamma.ai_tools.registry import approval_preview

    pdf, sheet = tim.pages["pdf"], tim.pages["sheet"]
    ws = workspace_of(USER)
    preview, answer = approval_preview(ws, _chat(), "draw", {"page_id": pdf, "pdf_page": 1, "shapes": [
        {"kind": "rect", "around": QUOTE}, {"kind": "line", "from": [0.1, 0.9], "to": [0.5, 0.9], "dash": "dashed"}]})
    assert answer is None and preview["pdf_page"] == 1 and "sheet_id" not in preview
    shape = preview["shape"]
    assert (shape["kind"], shape["frame"]) == ("ink", [600, 800]) and len(shape["strokes"]) == 2
    rect, line = shape["strokes"]
    assert rect["color"] == "#dc2626" and len(rect["points"]) > 4 and "dash" not in rect
    assert line["dash"] == "dashed" and line["points"][0] == [60, 720] and line["points"][-1] == [300, 720]
    preview, _ = approval_preview(ws, _chat(), "add_text_box", {"page_id": pdf, "pdf_page": 2, "text": "A margin note",
                                                                "at": [0.1, 0.9], "width": 0.3})
    assert preview["shape"]["kind"] == "box" and preview["shape"]["text"] == "A margin note"
    assert (preview["shape"]["box"]["x"], preview["shape"]["box"]["y"], preview["shape"]["box"]["w"]) == (60, 720, 180)
    url = store_picture(ws, _png(200, 100))["url"]
    preview, _ = approval_preview(ws, _chat(), "place_picture", {"page_id": pdf, "pdf_page": 1, "url": url,
                                                                 "box": [0.1, 0.1, 0.5, 0.5]})
    assert preview["shape"] == {"kind": "picture", "frame": [600, 800], "url": url,
                                "box": {"x": 60, "y": 180, "w": 240, "h": 120}}
    preview, _ = approval_preview(ws, _chat(), "draw", {"sheet_id": sheet, "shapes": [
        {"kind": "ellipse", "box": [0.2, 0.2, 0.4, 0.3]}]})
    assert preview["sheet_id"] == sheet and preview["shape"]["frame"] == [400, 600]


def test_a_page_of_paper_after_another(tim):
    notes, sheet = tim.pages["notes"], tim.pages["sheet"]
    later = tim.post("/api/blocks", json={"parent_id": notes, "content": "after the sheet"}).json()["id"]
    text, action = _run("add_sheet", {"page_id": notes, "after_id": sheet, "size": "letter", "landscape": True,
                                      "pattern": "dots", "title": "Derivation"})
    assert not action.get("error"), text
    parent, content, props = _block(action["block_id"])
    assert (parent, content, props["collapsed"]) == (notes, "Derivation", True)
    assert (props["sheet"]["width"], props["sheet"]["height"], props["sheet"]["pattern"]) == (792, 612, "dots")
    with connect_pages_db(workspace_of(USER)) as conn:
        order = [r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE parent_id = ? ORDER BY position",
                                            (notes,))]
    assert order.index(sheet) < order.index(action["block_id"]) < order.index(later)
    assert "after_id names no page of paper" in _run("add_sheet", {"page_id": notes, "after_id": later})[0]


def _revert(action, **more):
    return ai_revert.revert_change(workspace_of(USER), "create", action["block_id"], action["revert"],
                                   actor=account_of(USER), **more)


def test_a_drawing_reverts_and_comes_back_with_its_properties(tim):
    text, action = _run("draw", {"page_id": tim.pages["pdf"], "pdf_page": 2, "shapes": [
        {"kind": "line", "from": [0.1, 0.5], "to": [0.9, 0.5]}]})
    block_id, props = action["block_id"], _block(action["block_id"])[2]
    assert _revert(action)["noop"] is False and _block(block_id) is None
    assert _revert(action, redo=True)["noop"] is False
    assert _block(block_id)[2] == props  # a handwriting group again, not a bare note
    # strokes drawn into it since (its file another): the revert stops, and can be forced
    with connect_pages_db(workspace_of(USER)) as conn:
        apply_ops(conn, tim.pages["pdf"], [{"op": "set", "id": block_id, "props": {
            "ink_url": "/api/uploads/" + "1" * 24 + ".ink"}}], actor=account_of(USER))
    with pytest.raises(ai_revert.RevertError) as stop:
        _revert(action)
    assert (stop.value.conflict, stop.value.preview["drawn"]) == ("filled", True)
    assert _revert(action, force=True)["noop"] is False and _block(block_id) is None


def test_a_moved_box_reverts_but_a_commented_one_asks(tim):
    pdf = tim.pages["pdf"]
    _, action = _run("add_text_box", {"page_id": pdf, "pdf_page": 2, "text": "Moved", "at": [0.5, 0.5]})
    box = _block(action["block_id"])[2]["text_box"]
    with connect_pages_db(workspace_of(USER)) as conn:  # moved by hand: nothing of anyone's is lost
        apply_ops(conn, pdf, [{"op": "set", "id": action["block_id"], "props": {
            "text_box": {**box, "x": 10}}}], actor=account_of(USER))
    assert _revert(action)["noop"] is False
    _, action = _run("add_text_box", {"page_id": pdf, "pdf_page": 2, "text": "Commented", "at": [0.5, 0.6]})
    thread_on(tim, pdf, action["block_id"], "Why here?")
    with pytest.raises(ai_revert.RevertError) as stop:
        _revert(action)
    assert (stop.value.conflict, stop.value.preview["threads"]) == ("filled", 1)


def test_a_viewer_draws_nothing(tim, monkeypatch):
    from gamma import workspaces

    monkeypatch.setattr(workspaces, "role_of", lambda ws, user: "viewer")
    text, action = _run("draw", {"page_id": tim.pages["pdf"], "pdf_page": 1, "shapes": [
        {"kind": "line", "from": [0, 0], "to": [1, 1]}]})
    assert action["error"] and "can only view this workspace" in text
