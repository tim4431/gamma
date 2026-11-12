"""The region pictures of feedback threads (gamma/feedback/pictures.py):
what the picture now draws (the page's own highlights measured on its file,
not a lost one, one still placed in another file, a link region or one the
file embeds; its handwriting), its content key (the same for the same
marks, moved by a mark on its page, not by one on another), the picture as
commented on (the held version, the thread's own rectangles), the
renderer's refusals and the data.db cache's cap."""

import io

import pymupdf
import pytest
from PIL import Image

from conftest import doc_props, login, make_page, make_user, workspace_of
from gamma import storage
from gamma.blocks_store import load_props, new_block_id
from gamma.db import connect_data_db, connect_pages_db
from gamma.feedback import anchors, pictures
from test_highlight_relocate import SIZE, _line_rect, _position

LINE = "the quick brown wombat digs a long burrow"
FIGURE = (300, 420, 420, 520)   # a black figure on the first version, top-left origin


@pytest.fixture(scope="module")
def owner(client):
    make_user("fpic_owner", "pw")
    return login("fpic_owner", "pw")


@pytest.fixture
def ws(owner):
    return workspace_of("fpic_owner")


def _paper(pages=1, *, line_at=100, figure=False) -> bytes:
    """A Letter PDF of ``pages`` pages, each with ``LINE`` at ``line_at``,
    the first with a black figure (``FIGURE``) when ``figure``."""
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, line_at), LINE, fontname="helv", fontsize=SIZE)
        if figure and n == 0:
            page.draw_rect(pymupdf.Rect(*FIGURE), color=(0, 0, 0), fill=(0, 0, 0))
    return doc.tobytes()


def _upload(c, data) -> str:
    r = c.post("/api/uploads", files={"file": ("p.pdf", io.BytesIO(data), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


def _insert(c, page, props) -> str:
    block = new_block_id()
    r = c.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": block, "parent": page, "content": "", "props": props}]})
    assert r.status_code == 200, r.text
    return block


def _set(c, page, block, props):
    r = c.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [{"op": "set", "id": block, "props": props}]})
    assert r.status_code == 200, r.text


def _mark(y=100, page=1, **more):
    return {"pdf_position": {**_position(page, _line_rect(72, y, LINE)), **more}, "quote": LINE, "color": "#ffe28fa6"}


def _now(ws, page, block, memo=None):
    with connect_pages_db(ws) as conn:
        props = load_props(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page,)).fetchone()[0])
        return pictures.now(conn, page, props, anchors.anchor_of(conn, page, block), memo=memo)


def _image(data):
    return Image.open(io.BytesIO(data)).convert("RGB")


def test_the_key_is_the_marks_of_the_page_and_moves_only_with_one_of_them(owner, ws):
    doc = _upload(owner, _paper(2))
    page = make_page(owner, "Keys", doc_props(doc))["id"]
    passage = _insert(owner, page, _mark())
    first = _now(ws, page, passage)
    assert first == _now(ws, page, passage) and pictures.key(first) == pictures.key(_now(ws, page, passage))
    assert first.file_hash == doc and first.page == 1 and first.box[1] < 100 / 792 < first.box[3]
    assert [d[1] for d in first.drawn if d[0] == "hl"] == [passage] and len(pictures.key(first)) == 16

    _insert(owner, page, _mark(page=2))                               # another page's mark
    assert pictures.key(_now(ws, page, passage)) == pictures.key(first)
    other = _insert(owner, page, _mark(y=300))                        # one on its page
    second = _now(ws, page, passage)
    assert pictures.key(second) != pictures.key(first) and {d[1] for d in second.drawn if d[0] == "hl"} == {passage, other}
    _set(owner, page, other, {"pdf_position": _position(1, _line_rect(72, 340, LINE))})
    moved = _now(ws, page, passage)
    assert pictures.key(moved) not in (pictures.key(first), pictures.key(second))
    _set(owner, page, other, {"color": "#aaebaaa6"})
    third = _now(ws, page, passage)
    assert pictures.key(third) != pictures.key(moved)

    memo = {}
    assert _now(ws, page, passage, memo) == third and _now(ws, page, other, memo).drawn == third.drawn
    assert len([k for k in memo if k[0] == "blocks"]) == 1           # the page read once for both


def test_a_lost_mark_one_of_another_file_a_link_and_one_the_file_draws_are_not_drawn(owner, ws):
    doc = _upload(owner, _paper(1))
    page = make_page(owner, "Marks", doc_props(doc))["id"]
    solid = _insert(owner, page, _mark())
    dashed = _insert(owner, page, _mark(y=150, approx=True, match=0.8))
    lost = _insert(owner, page, _mark(y=200, lost=True, doc_id="a" * 24))
    pending = _insert(owner, page, _mark(y=250, doc_id="b" * 24))
    link = _insert(owner, page, {**_mark(y=300), "link_url": "https://example.org"})
    embedded = _insert(owner, page, {**_mark(y=350), "imported_annot": "1:/Highlight:72:0:300"})
    drawn = _now(ws, page, solid).drawn
    assert {d[1] for d in drawn if d[0] == "hl"} == {solid, dashed}
    assert _now(ws, page, dashed) is not None
    assert _now(ws, page, lost) is None and _now(ws, page, pending) is None    # no picture of a place not here
    assert _now(ws, page, link).drawn == drawn and _now(ws, page, embedded).drawn == drawn

    # the picture: the solid mark's wash over its line, the text under it still dark, the paper above it white
    region = _now(ws, page, solid)
    image = _image(pictures.render(ws, connect_pages_db(ws), page, region))
    (width, height), (bx0, by0, bx1, by1) = image.size, region.box

    def at(x, y):  # page points → the picture's pixel
        return image.getpixel((int((x / 612 - bx0) / (bx1 - bx0) * width), int((y / 792 - by0) / (by1 - by0) * height)))
    under = [at(x / 2, y / 2) for x in range(150, 400) for y in range(184, 204)]
    assert sum(r > 230 and b < 205 for r, g, b in under) > 500           # the wash
    assert sum(max(p) < 90 for p in under) > 50                        # the glyphs under it
    assert min(at(100, 80)) > 240 and min(at(100, 115)) > 240


def test_the_picture_as_commented_on_draws_the_held_version_and_the_thread_s_own_rectangles(owner, ws):
    old, new = _paper(figure=True), _paper(line_at=300)
    held, current = _upload(owner, old), _upload(owner, new)
    page = make_page(owner, "Versions", doc_props(current))["id"]
    x0, y0, x1, y1 = FIGURE
    rect = {"x1": x0 - 10, "y1": y0 - 10, "x2": x1 + 10, "y2": y1 + 10}   # the figure with a margin of paper
    area = _insert(owner, page, {"pdf_position": {**_position(1, rect, area=True), "doc_id": held}, "color": "#ff0000"})
    with connect_pages_db(ws) as conn:
        anchor = anchors.anchor_of(conn, page, area)
    assert anchor["kind"] == "area" and anchor["doc_id"] == held
    assert _now(ws, page, area) is None                               # still placed in the old file: not drawn now

    region = pictures.made(anchor, "#ff0000")
    assert region.file_hash == held and region.drawn == (("anchor", "area", "#ff0000", tuple(map(tuple, anchor["rects"]))),)
    assert pictures.key(region) != pictures.key(pictures.made(anchor))          # the colour is in it
    image = _image(pictures.render(ws, connect_pages_db(ws), page, region))
    width, height = image.size
    assert max(image.getpixel((width // 2, height // 2))) < 60                # the figure of the held version
    data = image.tobytes()
    assert any(r > 200 and g < 90 and b < 90 for r, g, b in zip(data[0::3], data[1::3], data[2::3]))  # its border

    _set(owner, page, area, {"pdf_position": _position(1, rect, area=True)})  # placed on the new build
    now = _now(ws, page, area)
    assert now.file_hash == current and now.box == region.box
    image = _image(pictures.render(ws, connect_pages_db(ws), page, now))
    assert min(image.getpixel((image.size[0] // 2, image.size[1] // 2))) > 150  # no figure on the new build

    ink = dict(anchor, kind="ink")
    assert pictures.now(connect_pages_db(ws), page, doc_props(current), ink) is None   # ink on an earlier build
    assert pictures.made({**anchor, "kind": "note"}) is None and pictures.made({**anchor, "box": None}) is None
    assert pictures.made({**anchor, "doc_id": storage.UNSTORED}) is None


def test_a_page_past_the_end_or_a_file_not_here_has_no_picture(owner, ws):
    doc = _upload(owner, _paper())
    page = make_page(owner, "Short", doc_props(doc))["id"]
    conn = connect_pages_db(ws)
    assert pictures.render(ws, conn, page, pictures.Region(doc, 1, (0, 0, 1, 1), ())) is not None
    assert pictures.render(ws, conn, page, pictures.Region(doc, 3, (0, 0, 1, 1), ())) is None
    assert pictures.render(ws, conn, page, pictures.Region("c" * 24, 1, (0, 0, 1, 1), ())) is None
    broken = storage.content_digest(b"%PDF-1.7\nnot a pdf at all")
    storage.put(ws, f"{broken}.pdf", b"%PDF-1.7\nnot a pdf at all")
    assert pictures.render(ws, conn, page, pictures.Region(broken, 1, (0, 0, 1, 1), ())) is None


def test_the_cache_answers_by_key_and_keeps_the_newest_within_its_cap(owner, ws, monkeypatch):
    doc = _upload(owner, _paper())
    page = make_page(owner, "Cached", doc_props(doc))["id"]
    passage = _insert(owner, page, _mark())
    region = _now(ws, page, passage)
    with connect_data_db(ws) as db:
        db.execute("DELETE FROM feedback_pictures")
        db.commit()
    drawn = pictures.cached(ws, connect_pages_db(ws), page, region)
    assert drawn[:2] == b"\xff\xd8"                                   # a JPEG, as render_page makes it
    monkeypatch.setattr(pictures, "render", lambda *a: pytest.fail("drawn again"))
    assert pictures.cached(ws, connect_pages_db(ws), page, region) == drawn

    monkeypatch.setattr(pictures, "CACHE_BYTES", 2500)
    for n in range(4):
        pictures._put(ws, f"k{n}", bytes([n]) * 1000)
    pictures._put(ws, "huge", b"x" * 3000)                            # past the cap on its own: not kept
    with connect_data_db(ws) as db:
        kept = dict(db.execute("SELECT key, bytes FROM feedback_pictures").fetchall())
    assert kept == {"k2": 1000, "k3": 1000}
