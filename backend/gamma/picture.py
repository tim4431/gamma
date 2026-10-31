"""Pictures: a photo, a screenshot or a figure placed on a PDF page or a
sheet of paper (docs/dev/pictures.md).

A picture is a block whose ``content`` is its caption, Markdown like any
note (it may be empty), and whose ``picture`` property places it:
``{url, x, y, w, h, angle, crop, flip_h, flip_v}`` in points on its
surface. It is placed as a text box is (gamma/text_box.py): under a sheet
it is on the nearest sheet above it, points from its top-left; any other
picture with ``pdf_page`` is on that PDF page, in the frame ink files use
(the pdf.js viewport at scale 1, rotation applied, origin top-left, y
down); with neither it is on no surface and shows only in the notes.

``url`` is the stored file, ``/api/uploads/<stem>.<ext>`` with a PNG, JPEG,
GIF or WebP extension. ``x``, ``y`` is the top-left of the frame before the
turn, negative where a turned frame's own corner lies off the surface, and
``w``, ``h`` its size, the picture as shown after the crop and before the
turn (an aspect other than the crop's is a stretch). ``angle``
is the turn about the frame's centre in degrees, clockwise as seen on the
screen (CSS ``rotate``, y down), in [-180, 180). ``crop`` is ``{x, y, w,
h}`` as fractions of the image as the browser shows it (its EXIF
orientation applied), so no pixel size is stored. ``flip_h`` and ``flip_v``
mirror the picture about the frame's own axes. Everything that draws one
applies the crop, then the flips, then the turn. Stored values are read
through ``normalize_picture``, never trusted; a value whose ``url`` is no
upload reference is no picture, and the block draws nothing.
frontend/src/markup/picture.js is the twin of the rules here;
tests/shared/picture.json and picturemerge.json pin both.
"""

import io
import math
import re

from .text_box import MAX_COORD, _num, _round, escape_markdown, is_text_box, plain_text  # MAX_COORD bounds a picture too

MIN_SIDE = 8  # the least side of a frame, in points
DEFAULT_SIDE = 144
CROP_MIN = 0.01  # the least side of a crop, as a fraction of the image
# An upload reference to a picture the browser draws and the PDF writers
# embed (storage.UPLOAD_REF_RE, narrowed): the stem as stored, an extension
# in any case. SVG is left out until it is wanted on a page.
URL_RE = re.compile(r"^/api/uploads/[0-9A-Za-z_-]{1,128}\.(?i:png|jpe?g|gif|webp)\Z")
_WHOLE = {"x": 0, "y": 0, "w": 1, "h": 1}


def _number(value):
    """A JSON number read as JavaScript reads it (a double), or None when
    it is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:  # an integer past a double's range, which JSON.parse reads as Infinity
        return None
    return value if math.isfinite(value) else None


def _round4(value: float):
    """Ten-thousandths, halves up, as ``text_box._round`` rounds hundredths;
    a whole number stays an integer."""
    y = value * 10000
    n = math.floor(y)
    if y - n >= 0.5:
        n += 1
    return n // 10000 if n % 10000 == 0 else n / 10000


def _angle(value):
    """Degrees in [-180, 180), hundredths; 0 when it is not a finite number.
    ``math.fmod`` is JavaScript's ``%``, so both sides wrap a value alike;
    a turn that rounds to 180 is -180."""
    a = _number(value)
    if a is None:
        return 0
    a = _round(math.fmod(math.fmod(a + 180, 360) + 360, 360) - 180)
    return -180 if a == 180 else a


def _crop(value) -> dict:
    """The crop from a stored ``crop``: the whole image when it is not an
    object, each bad key its default. Rounded to ten-thousandths first,
    then the sides clamped to CROP_MIN..1 and the corner pulled in so the
    crop stays inside the image."""
    if not isinstance(value, dict):
        return dict(_WHOLE)
    out = {}
    for key in ("w", "h"):
        n = _number(value.get(key))
        out[key] = min(1, max(CROP_MIN, 1 if n is None else _round4(n)))
    for key, side in (("x", "w"), ("y", "h")):
        n = _number(value.get(key))
        out[key] = _round4(min(1 - out[side], max(0, 0 if n is None else _round4(n))))
    return {k: out[k] for k in ("x", "y", "w", "h")}


def normalize_picture(value) -> dict | None:
    """A complete picture from a stored ``picture``, or None when it is not
    an object or its ``url`` is no picture upload: each missing or bad key
    takes its default, coordinates and sizes clamped and rounded to
    hundredths, the turn wrapped, the crop kept inside the image, only a
    real boolean a flip, unknown keys dropped. A corner may be as far off
    the surface as MAX_COORD: what is kept on it is the turned frame
    (``keep_on_surface``), whose own corner a wide picture turned near the
    edge puts past it."""
    if not isinstance(value, dict):
        return None
    url = value.get("url")
    if not isinstance(url, str) or not URL_RE.match(url):
        return None
    return {
        "url": url,
        "x": _num(value.get("x"), -MAX_COORD, MAX_COORD, 0),
        "y": _num(value.get("y"), -MAX_COORD, MAX_COORD, 0),
        "w": _num(value.get("w"), MIN_SIDE, MAX_COORD, DEFAULT_SIDE),
        "h": _num(value.get("h"), MIN_SIDE, MAX_COORD, DEFAULT_SIDE),
        "angle": _angle(value.get("angle")),
        "crop": _crop(value.get("crop")),
        "flip_h": value.get("flip_h") is True,
        "flip_v": value.get("flip_v") is True,
    }


def is_picture(props: dict | None) -> bool:
    """A picture block: ``picture`` an object, on a block that is neither
    ink (an ``ink_url`` key, whatever its value) nor a text box, the order
    the generated ``kind`` column and the client test them in. A picture
    whose value does not normalize is still one (its card says the file is
    missing); it draws nothing on the page."""
    if not isinstance(props, dict):
        return False
    return "ink_url" not in props and not is_text_box(props) and isinstance(props.get("picture"), dict)


def picture_page(props: dict | None, on_sheet: bool = False) -> int | None:
    """The PDF page a picture is on, by ``text_box.box_page``'s rule: its
    ``pdf_page`` when that is a positive integer, else None, and None too
    when ``on_sheet`` says a sheet is among the block's ancestors (the
    picture is on that sheet) or the block is no picture."""
    page = (props or {}).get("pdf_page")
    if (on_sheet or not is_picture(props) or isinstance(page, bool) or not isinstance(page, int)
            or page < 1):
        return None
    return page


# --- two writers, one picture ---------------------------------------------------------
# Every write of a picture sends it whole, so taken as one value one
# person's crop would undo another's move. A `set` whose base_props names
# the picture the change was made from is merged key by key instead, as a
# text box's is (gamma/ops.py), but for the frame: a resize or a turn
# about the centre rewrites x and y, so the frame merges as its size and
# its centre.

_FRAME = ("x", "y", "w", "h")
# A centre that moved by less has not moved: a resize about the centre
# rounds x and y to hundredths, which shifts it by half of one at most.
_MOVED = 0.0075


def _centre(pic: dict) -> tuple[float, float]:
    return pic["x"] + pic["w"] / 2, pic["y"] + pic["h"] / 2


def _moved(dx: float, dy: float) -> bool:
    return abs(dx) > _MOVED or abs(dy) > _MOVED


def merge_picture(stored, mine, base):
    """The picture a writer's change ``base`` → ``mine`` makes of the one
    stored now, as ``text_box.merge_text_box`` merges a box: the stored
    picture with the keys the writer changed taken from ``mine``, normalized
    (so a default spelled out is no change; the crop is compared by value).
    Where both changed a key, the writer's value wins. The frame merges as
    its size, ``w`` and ``h`` key by key, and its centre, as one: when the
    stored frame is the base's the writer's is taken whole; else a centre
    the writer did not move is the stored one, where both moved it and
    neither cropped (two moves) it is the writer's, and otherwise the
    writer's shift of it goes onto the stored centre, so the part a crop
    keeps stays put on a picture moved meanwhile, and a picture cropped
    meanwhile moves as far as the writer moved it. ``mine`` as sent when it
    is no picture (None deletes the key), and normalized when the stored
    value or the base is none. frontend/src/markup/picture.js mergePicture
    is the twin; tests/shared/picturemerge.json pins both."""
    ours = normalize_picture(mine)
    if ours is None:
        return mine
    now, was = normalize_picture(stored), normalize_picture(base)
    if now is None or was is None:
        return ours
    out = {**now, **{k: v for k, v in ours.items() if v != was[k]}}
    if all(now[k] == was[k] for k in _FRAME):
        return out
    (mx, my), (bx, by), (sx, sy) = _centre(ours), _centre(was), _centre(now)
    if not _moved(mx - bx, my - by):  # a resize, a turn, a flip
        cx, cy = sx, sy
    elif _moved(sx - bx, sy - by) and ours["crop"] == was["crop"] == now["crop"]:  # two moves
        cx, cy = mx, my
    else:  # a crop's shift, or a move of a picture resized or cropped meanwhile
        cx, cy = sx + (mx - bx), sy + (my - by)
    return normalize_picture({**out, "x": cx - out["w"] / 2, "y": cy - out["h"] / 2})


def resend_picture(mine, base, stored):
    """A change ``base`` → ``mine`` whose push was never answered, against
    the picture the remote stores now: the picture to send again, with the
    stored one as its base (gamma/sync_engine.py ``_unlanded``). None when
    the remote shows each change of ours as ours or changed since (landed,
    or overridden: its change may have come after ours), False when one of
    them is no picture (the plain rule). Judged by the parts merge_picture
    merges: each key but ``x`` and ``y`` on its own, then the centre as one.
    Our shift of the centre goes again, onto the stored centre as
    merge_picture puts it, when the stored centre has not moved or when our
    crop goes again (a crop shifts the centre with it); a centre both moved
    otherwise stays the stored one. So a lost resize sent again after a
    move is about the moved centre."""
    ours, was, now = normalize_picture(mine), normalize_picture(base), normalize_picture(stored)
    if ours is None or was is None or now is None:
        return False
    again = {k: v for k, v in ours.items() if k not in ("x", "y") and v != was[k] and now[k] == was[k]}
    (mx, my), (bx, by), (sx, sy) = _centre(ours), _centre(was), _centre(now)
    shift = _moved(mx - bx, my - by) and ("crop" in again or not _moved(sx - bx, sy - by))
    if not again and not shift:
        return None
    out = {**now, **again}
    cx, cy = (sx + (mx - bx), sy + (my - by)) if shift else (sx, sy)
    return normalize_picture({**out, "x": cx - out["w"] / 2, "y": cy - out["h"] / 2})


# --- the frame on the page ------------------------------------------------------------


def turned_corners(pic: dict) -> list[tuple[float, float]]:
    """The corners of the (normalized) picture's frame turned about its
    centre, in points of its surface: top-left, top-right, bottom-right,
    bottom-left of the unturned frame. A point (dx, dy) from the centre
    turns to (dx cos a - dy sin a, dx sin a + dy cos a), y down, which is
    clockwise as seen."""
    a = math.radians(pic["angle"])
    cos, sin = math.cos(a), math.sin(a)
    cx, cy = pic["x"] + pic["w"] / 2, pic["y"] + pic["h"] / 2
    hw, hh = pic["w"] / 2, pic["h"] / 2
    return [(cx + dx * cos - dy * sin, cy + dx * sin + dy * cos)
            for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]


def turned_box(pic: dict) -> tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)``, the bounding box of the turned frame."""
    corners = turned_corners(pic)
    xs, ys = [x for x, _y in corners], [y for _x, y in corners]
    return min(xs), min(ys), max(xs), max(ys)


def turned_size(pic: dict) -> tuple[float, float]:
    """The width and height of ``turned_box``, to hundredths: the /BBox of
    an appearance that draws the picture from that box's top-left."""
    x0, y0, x1, y1 = turned_box(pic)
    return round(x1 - x0, 2), round(y1 - y0, 2)


# What the PDFs draw of a picture is cut at the page's edge, so what is
# kept on the surface is the turned frame, not the frame before the turn.
# keep_on_surface is the import's rule; the handles and the menu follow the
# same one (frontend/src/markup/picture.js).


def _resized(pic: dict, w: float, h: float) -> dict:
    """The frame of ``pic`` at size ``w`` × ``h`` about the same centre."""
    return {**pic, "w": w, "h": h, "x": pic["x"] + (pic["w"] - w) / 2, "y": pic["y"] + (pic["h"] - h) / 2}


def _within(pic: dict, width: float, height: float) -> dict:
    """``pic`` moved so that its turned frame lies on a surface of
    ``width`` × ``height`` points, at its left or top edge when it is
    wider or taller; its size left alone."""
    x0, y0, x1, y1 = turned_box(pic)
    dx = max(0, min(max(0, width - (x1 - x0)), x0)) - x0
    dy = max(0, min(max(0, height - (y1 - y0)), y0)) - y0
    return {**pic, "x": pic["x"] + dx, "y": pic["y"] + dy}


def keep_on_surface(pic: dict, width: float, height: float) -> dict:
    """The (normalized) picture kept on a surface of ``width`` × ``height``
    points, as the imports place one: scaled down about its centre, its
    aspect kept, when its turned frame (``turned_box``) is wider or taller
    than the surface, then moved so that the turned frame lies on it;
    normalized."""
    x0, y0, x1, y1 = turned_box(pic)
    k = min(1, width / (x1 - x0), height / (y1 - y0))
    return normalize_picture(_within(_resized(pic, pic["w"] * k, pic["h"] * k), width, height))


# --- the picture in a PDF -------------------------------------------------------------


def _real(value: float) -> bytes:
    """A number for a content stream: five decimals at most, enough for a
    turn's sine and cosine, trailing zeros dropped."""
    text = b"%.5f" % (0 if abs(value) < 5e-6 else value)
    return text.rstrip(b"0").rstrip(b".")


def _cm(*matrix) -> bytes:
    return b" ".join(_real(v) for v in matrix) + b" cm"


def pdf_ops(pic: dict, name: str) -> bytes:
    """Content-stream operators that draw the (normalized) picture ``pic``
    with the image XObject ``name`` where the screen shows it, in a y-down
    frame of its surface (the caller flips y): from the frame's centre,
    turned by its angle, mirrored by its flips about the frame's own axes,
    clipped to the frame, and the whole image scaled so that its crop fills
    the frame. Read from the image out, that is the crop, then the flips,
    then the turn, the order the screen applies them in
    (frontend/src/markup/picture.js ``pictureLayout``). An image fills the
    unit square of its space with its first row at the top, so it is drawn
    with y negated, which the y-down frame turns upright."""
    a = math.radians(pic["angle"])
    cos, sin = math.cos(a), math.sin(a)
    w, h, crop = pic["w"], pic["h"], pic["crop"]
    iw, ih = w / crop["w"], h / crop["h"]  # the whole image at the frame's scale
    left, top = -w / 2 - crop["x"] * iw, -h / 2 - crop["y"] * ih
    ops = [b"q", _cm(cos, sin, -sin, cos, pic["x"] + w / 2, pic["y"] + h / 2)]
    if pic["flip_h"] or pic["flip_v"]:
        ops.append(_cm(-1 if pic["flip_h"] else 1, 0, 0, -1 if pic["flip_v"] else 1, 0, 0))
    ops += [b" ".join(_real(v) for v in (-w / 2, -h / 2, w, h)) + b" re W n",
            _cm(iw, 0, 0, -ih, left, top + ih), b"/%s Do" % name.encode(), b"Q"]
    return b"\n".join(ops)


# --- the picture as text --------------------------------------------------------------


def caption_text(content: str) -> str:
    """The caption on one line: every run of whitespace one space, the ends
    stripped."""
    return re.sub(r"\s+", " ", content or "").strip()


def markdown_of(content: str, pic: dict) -> str:
    """The ``![caption](url)`` line the text exports and the AI print for a
    picture block: the caption as a reader sees it (its Markdown read as
    text, ``text_box.plain_text``), on one line, without the brackets and
    pipes that would end its alt text or read as an Obsidian size, and
    escaped again for the alt text (``text_box.escape_markdown``), so a
    reader of the line sees the words the note shows. "" when ``pic`` is no
    picture."""
    pic = normalize_picture(pic)
    if pic is None:
        return ""
    caption = caption_text(re.sub(r"[\[\]|]", "", plain_text(content or "")))
    return f"![{escape_markdown(caption)}]({pic['url']})"


def image_size(data: bytes) -> tuple[int, int] | None:
    """The pixel size of a picture file as the browser shows it, the EXIF
    orientation the browser applies (``pdf_image.browser_orientation``;
    orientations 5 to 8 swap the sides), or None when it is no PNG, JPEG,
    GIF or WebP Pillow can open. Read from the header alone: the format's
    own Pillow plugin opens the file lazily and nothing is decoded, so a
    picture of any declared size answers at once (the decompression-bomb
    check that ``Image.open`` makes guards a decode, and there is none)."""
    from PIL import GifImagePlugin, JpegImagePlugin, PngImagePlugin, WebPImagePlugin

    from .ai_pictures import sniff

    opener = {"image/png": PngImagePlugin.PngImageFile, "image/jpeg": JpegImagePlugin.JpegImageFile,
              "image/gif": GifImagePlugin.GifImageFile, "image/webp": WebPImagePlugin.WebPImageFile}.get(sniff(data))
    if opener is None:
        return None
    try:
        with opener(io.BytesIO(data)) as image:
            width, height = image.size
    except Exception:  # a broken header: SyntaxError, OSError, ValueError, struct.error, by plugin
        return None
    from .pdf_image import browser_orientation

    return (height, width) if browser_orientation(data) in (5, 6, 7, 8) else (width, height)
