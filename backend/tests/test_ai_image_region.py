"""The picture a page shows, as the server draws it (gamma/ai_pictures.py
``image_region``, ``render_region``): the whole picture and a region cut
out of it, scaled so the longer side is RENDER_MAX_SIDE and zoomed at most
IMAGE_MAX_ZOOM, a crop at least REGION_MIN_PX a side; an EXIF-rotated
JPEG as the browser shows it, an animation's first frame, transparency on
white, an SVG through MuPDF; the marks drawn as the viewer draws a region;
what does not draw (a file not here, one past the size, a decompression
bomb, a truncated file, no picture at all); and the page-image route and
the chat's region picture of a picture page."""

import io

import pytest
from PIL import Image, ImageDraw

from conftest import login, make_page, make_user, workspace_of
from gamma import ai_pictures, storage
from gamma.ai_pictures import IMAGE_MAX_ZOOM, REGION_MIN_PX, image_region
from gamma.db import connect_pages_db
from gamma.documents.model import document
from gamma.pdf_text import RENDER_MAX_SIDE

OWNER = "air_owner"
BLUE = (0, 0, 200)
RED = (220, 30, 30)


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture
def ws(owner):
    return workspace_of(OWNER)


def _picture(width, height, color="white", boxes=(), mode="RGB", fmt="PNG", **save) -> bytes:
    """A ``width`` × ``height`` picture of ``color`` with ``boxes``
    (``(box, fill)``) drawn on it, saved as ``fmt``."""
    image = Image.new(mode, (width, height), color)
    draw = ImageDraw.Draw(image)
    for box, fill in boxes:
        draw.rectangle(box, fill=fill)
    out = io.BytesIO()
    image.save(out, fmt, **save)
    return out.getvalue()


def _stored(ws, data, ext) -> str:
    name, _ = storage.store_file(ws, data, ext)
    return name


def _image(shown):
    return Image.open(io.BytesIO(shown[0])).convert("RGB")


def _blue(pixel) -> bool:
    return pixel[2] > 150 and pixel[0] < 80 and pixel[1] < 80


def _white(pixel) -> bool:
    return min(pixel) > 240


def test_the_whole_picture_and_a_region_are_scaled_within_the_caps(ws):
    name = _stored(ws, _picture(1200, 800, boxes=[((300, 200, 600, 400), BLUE)]), ".png")
    whole = image_region(ws, name)
    assert whole[1] == "image/jpeg" and (whole[2], whole[3]) == (RENDER_MAX_SIDE, round(800 * RENDER_MAX_SIDE / 1200))
    image = _image(whole)
    assert _blue(image.getpixel((round(450 / 1200 * image.width), round(300 / 800 * image.height))))
    assert _white(image.getpixel((10, 10)))
    region = image_region(ws, name, (0.25, 0.25, 0.5, 0.5))
    assert (region[2], region[3]) == (1200, 800)                     # 300 × 200 of the picture's pixels at 4×
    image = _image(region)
    assert _blue(image.getpixel((600, 400))) and _blue(image.getpixel((5, 5))) and _blue(image.getpixel((1190, 790)))
    corner = image_region(ws, name, (0.99, 0.99, 1.0, 1.0))           # a tiny region grows to REGION_MIN_PX a side
    assert (corner[2], corner[3]) == (REGION_MIN_PX * IMAGE_MAX_ZOOM, REGION_MIN_PX * IMAGE_MAX_ZOOM)
    small = image_region(ws, _stored(ws, _picture(100, 60), ".png"))
    assert (small[2], small[3]) == (400, 240)                         # zoomed 4× at most


def test_exif_orientation_is_applied_before_the_cut(ws):
    exif = Image.Exif()
    exif[0x0112] = 6   # rotated 90° clockwise to show: the left half goes to the top
    data = _picture(400, 200, boxes=[((0, 0, 200, 200), RED)], fmt="JPEG", exif=exif.tobytes(), quality=95)
    name = _stored(ws, data, ".jpg")
    whole = image_region(ws, name)
    assert (whole[2], whole[3]) == (round(200 * RENDER_MAX_SIDE / 400), RENDER_MAX_SIDE)
    image = _image(whole)
    top, bottom = image.getpixel((image.width // 2, image.height // 4)), image.getpixel((image.width // 2, 3 * image.height // 4))
    assert top[0] > 180 and top[1] < 90 and _white(bottom)
    cut = _image(image_region(ws, name, (0.0, 0.0, 1.0, 0.5)))          # the top half of the shown picture
    assert cut.getpixel((cut.width // 2, cut.height // 2))[0] > 180 and cut.getpixel((cut.width // 2, cut.height // 2))[1] < 90


def test_an_animations_first_frame_transparency_on_white_and_an_svg(ws):
    first, second = Image.new("RGB", (80, 80), RED), Image.new("RGB", (80, 80), BLUE)
    out = io.BytesIO()
    first.save(out, "GIF", save_all=True, append_images=[second], duration=100, loop=0)
    pixel = _image(image_region(ws, _stored(ws, out.getvalue(), ".gif"))).getpixel((160, 160))
    assert pixel[0] > 150 and pixel[2] < 100
    clear = _picture(80, 80, color=(0, 0, 0, 0), mode="RGBA")
    assert _white(_image(image_region(ws, _stored(ws, clear, ".png"))).getpixel((160, 160)))

    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200">'
           b'<rect width="300" height="200" fill="white"/><rect x="100" y="50" width="100" height="100" fill="#0000c8"/></svg>')
    name = _stored(ws, svg, ".svg")
    whole = image_region(ws, name)
    assert (whole[2], whole[3]) == (1200, 800)                        # 300 × 200 points at 4×
    image = _image(whole)
    assert _blue(image.getpixel((600, 400))) and _white(image.getpixel((10, 10)))
    region = image_region(ws, name, (1 / 3, 0.25, 2 / 3, 0.75), [((1 / 3, 0.25, 2 / 3, 0.75), "#ff0000", True)])
    assert abs(region[2] - 400) <= 1 and abs(region[3] - 400) <= 1
    image = _image(region)
    assert image.getpixel((1, 200))[0] > 150 and image.getpixel((1, 200))[1] < 90   # the outline on the vector picture


def test_marks_are_drawn_as_the_viewer_draws_a_region(ws):
    name = _stored(ws, _picture(400, 400), ".png")
    box = (0.25, 0.25, 0.75, 0.75)
    plain = _image(image_region(ws, name, None, [(box, "#ff0000ff", False)]))
    w = plain.width
    assert w == RENDER_MAX_SIDE
    inside = plain.getpixel((w // 2, w // 2))
    assert inside[0] > 240 and 170 < inside[1] < 215 and 170 < inside[2] < 215   # white under a 25 % red wash
    edge = plain.getpixel((round(0.25 * w) + 1, w // 2))
    assert edge[0] > 150 and edge[1] < 90                                       # the outline, red's line tone
    assert _white(plain.getpixel((w // 8, w // 8)))
    strong = _image(image_region(ws, name, None, [(box, "#ff0000", True)]))
    assert strong.getpixel((round(0.25 * w) + 2, w // 2))[1] < 90               # three pixels wide
    assert plain.getpixel((round(0.25 * w) + 2, w // 2))[1] > 150               # two
    yellow = _image(image_region(ws, name, (0.2, 0.2, 0.8, 0.8), [(box, "", False)]))   # no colour: the viewer's yellow
    assert not _white(yellow.getpixel((yellow.width // 2, yellow.height // 2)))
    assert image_region(ws, name, None, [(box, "no colour", False)]) is not None


def test_what_does_not_draw(ws, monkeypatch):
    assert image_region(ws, f"{'c' * 24}.png") is None                           # not here
    data = _picture(100, 100)
    name = _stored(ws, data, ".png")
    monkeypatch.setattr(ai_pictures, "STORED_MAX_BYTES", len(data) - 1)
    assert image_region(ws, name) is None                                         # past the size
    monkeypatch.setattr(ai_pictures, "STORED_MAX_BYTES", 25_000_000)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 5000)
    assert image_region(ws, name) is None                                         # a decompression bomb: Pillow refuses
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 6000)
    assert image_region(ws, name) is None                                         # past the limit: Pillow warns, we refuse
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10_000)
    assert image_region(ws, name) is not None
    assert image_region(ws, _stored(ws, data[: len(data) // 2], ".png")) is None  # truncated
    assert image_region(ws, _stored(ws, b"not a picture at all", ".png")) is None
    assert image_region(ws, _stored(ws, b"<svg xmlns='http://www.w3.org/2000/svg'><rect", ".svg")) is None


def test_the_page_image_route_and_the_chats_region_picture_of_a_picture_page(owner, ws):
    name = _stored(ws, _picture(1200, 800, boxes=[((300, 200, 600, 400), BLUE)]), ".png")
    page = make_page(owner, "fig.png", {"doc": document(name[:-4], name="fig.png", format="image", ext=".png")})["id"]
    r = owner.get(f"/api/ai/page-image/{page}", params={"page": 1})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and "max-age=86400" in r.headers["cache-control"]
    assert max(Image.open(io.BytesIO(r.content)).size) == RENDER_MAX_SIDE
    r = owner.get(f"/api/ai/page-image/{page}", params={"page": 1, "box": "0.25,0.25,0.5,0.5"})
    assert r.status_code == 200 and Image.open(io.BytesIO(r.content)).size == (1200, 800)
    r = owner.get(f"/api/ai/page-image/{page}", params={"page": 2})
    assert r.status_code == 404 and r.json()["detail"] == "no such page"
    assert owner.get(f"/api/ai/page-image/{page}", params={"page": 1, "box": "0.9,0,0.1,1"}).status_code == 400
    text = make_page(owner, "notes.tex", {"doc": document("d" * 24, name="notes.tex", format="text", ext=".tex")})["id"]
    assert owner.get(f"/api/ai/page-image/{text}", params={"page": 1}).status_code == 404
    gone = make_page(owner, "gone.png", {"doc": document("e" * 24, name="gone.png", format="image", ext=".png")})["id"]
    r = owner.get(f"/api/ai/page-image/{gone}", params={"page": 1})
    assert r.status_code == 404 and r.json()["detail"] == "not found"

    with connect_pages_db(ws) as conn:
        marked = ai_pictures.region_picture(ws, conn, {"kind": "area", "page_id": page, "page": 1, "box": [0.25, 0.25, 0.5, 0.5]})
        seen = ai_pictures.region_picture(ws, conn, {"kind": "view", "page_id": page, "page": 1, "ink": True})
        assert ai_pictures.region_picture(ws, conn, {"kind": "area", "page_id": page, "page": 2}) is None
        drawn = ai_pictures.picture_at(ws, conn, f"/api/ai/page-image/{page}?page=1&box=0.25,0.25,0.5,0.5")
    assert marked["label"] == "A region the user marked on the picture “fig.png”, 1200×800 px" and marked["page"] == 1
    assert seen["label"].startswith("What the user sees: the picture “fig.png”") and "handwriting" not in seen["label"]
    assert drawn[1] == "image/jpeg" and Image.open(io.BytesIO(drawn[0])).size == (1200, 800)
