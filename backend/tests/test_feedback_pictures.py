"""The region pictures of feedback threads (gamma/feedback/pictures.py):
what the picture now draws (the page's own highlights measured on its file,
not a lost one, one still placed in another file, a link region or one the
file embeds; its handwriting), its content key (the same for the same
marks, moved by a mark on its page, not by one on another; a PDF's the key
of step 2), the picture as commented on (the held version, the thread's
own rectangles), a picture's regions and the whole picture cut from the
current version and from the held one, the renderer's refusals and the
data.db cache's cap."""

import hashlib
import io
import json

import pymupdf
import pytest
from PIL import Image, ImageDraw

from conftest import doc_props, login, make_page, make_user, workspace_of
from gamma import storage
from gamma.blocks_store import load_props, new_block_id
from gamma.db import connect_data_db, connect_pages_db
from gamma.documents.model import document
from gamma.feedback import anchors, pictures
from gamma.pdf_text import RENDER_MAX_SIDE
from test_highlight_relocate import SIZE, _line_rect, _position

LINE = "the quick brown wombat digs a long burrow"
FIGURE = (300, 420, 420, 520)   # a black figure on the first version, top-left origin
COORDS = ("x1", "y1", "x2", "y2")
BLUE = (0, 0, 200)


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


def _png(width, height, figure=None, color=BLUE) -> bytes:
    """A white PNG with a ``figure`` (a box, top-left origin) in ``color``."""
    image = Image.new("RGB", (width, height), "white")
    if figure:
        ImageDraw.Draw(image).rectangle(figure, fill=color)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _picture(c, ws, data, name="fig.png") -> tuple[str, str]:
    """A page showing the stored picture ``data``: ``(page id, stored name)``."""
    stored, _ = storage.store_file(ws, data, ".png")
    page = make_page(c, name, {"doc": document(stored[:-4], name=name, format="image", ext=".png")})["id"]
    return page, stored


def _set_doc(c, page, doc):
    r = c.put(f"/api/blocks/{page}", json={"properties": {"doc": doc}})
    assert r.status_code == 200, r.text


def _region(*rect, hash, color="#ff0000"):
    """A region over ``rect`` of a 1200 × 800 picture, drawn on its version ``hash``."""
    box = dict(zip(COORDS, rect))
    return {"image_position": {"width": 1200, "height": 800, "boundingRect": box, "rects": [box], "hash": hash},
            "color": color}


def test_the_key_is_the_marks_of_the_page_and_moves_only_with_one_of_them(owner, ws):
    doc = _upload(owner, _paper(2))
    page = make_page(owner, "Keys", doc_props(doc))["id"]
    passage = _insert(owner, page, _mark())
    first = _now(ws, page, passage)
    assert first == _now(ws, page, passage) and pictures.key(first) == pictures.key(_now(ws, page, passage))
    assert first.file == f"{doc}.pdf" and first.page == 1 and first.box[1] < 100 / 792 < first.box[3]
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
    assert region.file == f"{held}.pdf"
    assert region.drawn == (("anchor", "area", "#ff0000", tuple(map(tuple, anchor["rects"]))),)
    assert pictures.key(region) != pictures.key(pictures.made(anchor))          # the colour is in it
    image = _image(pictures.render(ws, connect_pages_db(ws), page, region))
    width, height = image.size
    assert max(image.getpixel((width // 2, height // 2))) < 60                # the figure of the held version
    data = image.tobytes()
    assert any(r > 200 and g < 90 and b < 90 for r, g, b in zip(data[0::3], data[1::3], data[2::3]))  # its border

    _set(owner, page, area, {"pdf_position": _position(1, rect, area=True)})  # placed on the new build
    now = _now(ws, page, area)
    assert now.file == f"{current}.pdf" and now.box == region.box
    image = _image(pictures.render(ws, connect_pages_db(ws), page, now))
    assert min(image.getpixel((image.size[0] // 2, image.size[1] // 2))) > 150  # no figure on the new build

    ink = dict(anchor, kind="ink")
    assert pictures.now(connect_pages_db(ws), page, doc_props(current), ink) is None   # ink on an earlier build
    assert pictures.made({**anchor, "kind": "note"}) is None and pictures.made({**anchor, "box": None}) is None
    assert pictures.made({**anchor, "doc_id": storage.UNSTORED}) is None


def test_a_region_of_a_picture_is_cut_from_the_current_version_with_the_pages_regions_drawn(owner, ws):
    page, stored = _picture(owner, ws, _png(1200, 800, (200, 100, 400, 300)))
    region = _insert(owner, page, _region(180, 80, 420, 320, hash=stored[:-4]))
    now = _now(ws, page, region)
    assert now == pictures.Region(stored, 0, (0.145, 0.095, 0.355, 0.405),
                                  (("region", region, "#ff0000", ((0.15, 0.1, 0.35, 0.4),), True),))
    with connect_pages_db(ws) as conn:
        props = load_props(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page,)).fetchone()[0])
        whole = pictures.now(conn, page, props, anchors.anchor_of(conn, page, page))      # the page's thread
        assert pictures.now(conn, page, props, anchors.anchor_of(conn, page, _insert(owner, page, {}))) is None
    assert whole == pictures.Region(stored, 0, (0.0, 0.0, 1.0, 1.0), now.drawn)
    assert pictures.key(whole) != pictures.key(now) and len(pictures.key(now)) == 16

    # the crop: the blue figure under the region's red wash, the outline at its edge, white paper around
    image = _image(pictures.render(ws, connect_pages_db(ws), page, now))
    width, height = image.size
    assert (width, height) == (1008, 992)                              # 252 × 248 of the picture's pixels at 4×
    r, g, b = image.getpixel((width // 2, height // 2))
    assert b > 120 and g < 40 and 40 < r < 100                          # blue through a 25 % red wash
    assert min(image.getpixel((5, 5))) > 240                            # the padding around the region
    r, g, b = image.getpixel((round((0.15 - 0.145) / 0.21 * width) + 1, height // 2))
    assert r > 150 and g < 90 and b < 90                                # the outline
    drawn = _image(pictures.render(ws, connect_pages_db(ws), page, whole))
    assert max(drawn.size) == RENDER_MAX_SIDE

    other = _insert(owner, page, _region(600, 400, 900, 700, hash=stored[:-4], color="#00ff00"))
    later = _now(ws, page, region)
    assert pictures.key(later) != pictures.key(now) and {d[1] for d in later.drawn} == {region, other}
    memo = {}
    assert _now(ws, page, region, memo) == later and _now(ws, page, other, memo).drawn == later.drawn
    assert len([k for k in memo if k[0] == "blocks"]) == 1


def test_the_picture_as_commented_on_a_picture_is_the_held_versions_crop(owner, ws):
    page, held = _picture(owner, ws, _png(1200, 800, (200, 100, 400, 300), color=(0, 0, 0)))
    region = _insert(owner, page, _region(180, 80, 420, 320, hash=held[:-4]))
    with connect_pages_db(ws) as conn:
        anchor = anchors.anchor_of(conn, page, region)
    assert (anchor["kind"], anchor["ext"], anchors.held_file(anchor)) == ("region", ".png", held)
    current, _ = storage.store_file(ws, _png(1200, 800), ".png")
    _set_doc(owner, page, document(current[:-4], name="fig.png", format="image", ext=".png"))

    before = pictures.made(anchor, "#ff0000")
    assert before == pictures.Region(held, 0, tuple(anchor["box"]),
                                     (("anchor", "region", "#ff0000", ((0.15, 0.1, 0.35, 0.4),)),))
    image = _image(pictures.render(ws, connect_pages_db(ws), page, before))
    assert max(image.getpixel((image.width // 2, image.height // 2))) < 90   # the black figure of the held version
    now = _now(ws, page, region)
    assert now.file == current and now.drawn[0][4] is False   # drawn on an earlier version, cut from the current one
    image = _image(pictures.render(ws, connect_pages_db(ws), page, now))
    assert min(image.getpixel((image.width // 2, image.height // 2))) > 150    # no figure on the new version

    whole = pictures.made({**anchor, "kind": "file", "rects": [], "box": None})
    assert whole == pictures.Region(held, 0, (0.0, 0.0, 1.0, 1.0), (("anchor", "file", "", ()),))
    assert pictures.made({**anchor, "kind": "note"}) is None and pictures.made({**anchor, "kind": "area"}) is None
    binary = f"{held[:-4]}.bin"                       # a picture attached under .bin is a picture
    assert pictures.made({**anchor, "ext": ".bin"}, "#ff0000") == before._replace(file=binary)
    assert pictures.made({**anchor, "kind": "file", "ext": ".bin", "rects": [], "box": None}) == whole._replace(file=binary)
    assert pictures.made({**anchor, "kind": "file", "ext": ".pdf"}) is None and pictures.made({**anchor, "kind": "file", "ext": ""}) is None
    assert pictures.made({**anchor, "box": None}) is None


def test_a_pdfs_key_is_step_twos_and_a_pictures_differs():
    drawn = (("hl", "H", "#ffe28fa6", False, "highlight", ((0.1, 0.2, 0.3, 0.4),)),)
    region = pictures.Region(f"{'a' * 24}.pdf", 2, (0.1, 0.2, 0.3, 0.4), drawn)
    made_of = json.dumps([1, "a" * 24, 2, [0.1, 0.2, 0.3, 0.4], RENDER_MAX_SIDE, drawn], separators=(",", ":"))
    assert pictures.key(region) == hashlib.sha256(made_of.encode("utf-8")).hexdigest()[:16]
    assert pictures.key(region._replace(file=f"{'a' * 24}.png", page=0)) != pictures.key(region)


def test_a_page_past_the_end_or_a_file_not_here_has_no_picture(owner, ws):
    doc = _upload(owner, _paper())
    page = make_page(owner, "Short", doc_props(doc))["id"]
    conn = connect_pages_db(ws)
    assert pictures.render(ws, conn, page, pictures.Region(f"{doc}.pdf", 1, (0, 0, 1, 1), ())) is not None
    assert pictures.render(ws, conn, page, pictures.Region(f"{doc}.pdf", 3, (0, 0, 1, 1), ())) is None
    assert pictures.render(ws, conn, page, pictures.Region(f"{'c' * 24}.pdf", 1, (0, 0, 1, 1), ())) is None
    broken = storage.content_digest(b"%PDF-1.7\nnot a pdf at all")
    storage.put(ws, f"{broken}.pdf", b"%PDF-1.7\nnot a pdf at all")
    assert pictures.render(ws, conn, page, pictures.Region(f"{broken}.pdf", 1, (0, 0, 1, 1), ())) is None
    # a picture not here or that does not draw
    page, stored = _picture(owner, ws, _png(100, 100))
    assert pictures.render(ws, conn, page, pictures.Region(stored, 0, (0, 0, 1, 1), ())) is not None
    assert pictures.render(ws, conn, page, pictures.Region(f"{'c' * 24}.png", 0, (0, 0, 1, 1), ())) is None
    broken = storage.content_digest(b"\x89PNG\r\n\x1a\nnot a png")
    storage.put(ws, f"{broken}.png", b"\x89PNG\r\n\x1a\nnot a png")
    assert pictures.render(ws, conn, page, pictures.Region(f"{broken}.png", 0, (0, 0, 1, 1), ())) is None


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
