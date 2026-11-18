"""What the chat's agent draws and places on a page (docs/dev/ai_tools.md
"Drawing on pages"): shapes as a new handwriting group, a text box, a
picture and a page of paper. Each is one block a person's client would
make (handwriting.md "The stroke file", text_boxes.md, pictures.md,
notebooks.md); the registry (gamma/ai_tools.py ``draw``, ``add_text_box``,
``place_picture``, ``add_sheet``) inserts it through the op path under the
agent's stamp and records its revert.

A call names its surface: a PDF page (``pdf_page``) or a page of paper
(``sheet_id``), ``Surface``. Places are fractions of the surface, top-left
origin, as ``clip_region``'s ``box``; on a PDF page a passage may stand in
for a place, its quote found as ``highlight`` finds one
(``annotations.places.passage_at``), and only on the call's own page: one
call draws on one surface (an arrow's ``to_quote``, a rect's ``around``).
A text box or a picture placed ``near`` a
passage goes in the nearest empty space beside it
(``pdf_free_space.spot``: the page's own content, and its handwriting,
text boxes and pictures, are taken), or is refused.

Planning only: the ``plan_*`` functions (``conn``, ``ws``, the surface,
the call's arguments) read the page and answer what to insert (``Made``),
or raise ValueError in words for the model. Nothing is
stored here; ``ink_file``'s bytes are stored by the caller when the call
runs, never for an approval card.
"""

import math
import time
from pathlib import Path
from typing import NamedTuple

from . import ink as inkmod, pdf_text
from .ai_context import pdf_path
from .ai_pictures import parse_box, upload_name
from .blocks_store import block_to_dict, fetch_subtree
from .documents.model import file_hash, is_pdf, page_document
from .highlights import is_ink
from .ink_ai import CURVE_POINTS, figure, placed
from .notebook import DEFAULT_PAPER, normalize_paper, sheets_of
from .paste_math import dollar_math
from .picture import URL_RE, image_size, is_picture, normalize_picture
from .storage import find_upload_file
from .text_box import DEFAULT_COLOR, TEXT_BACKGROUNDS, TEXT_SIZES, box_page, is_text_box, keep_on_surface, measure, \
    normalize_text_box

# The pens' colours by name (frontend/src/shared/model/palette.js
# INK_COLORS), which a text box takes too.
COLORS = {"black": "#1f1f1f", "gray": "#6b7280", "blue": "#1d4ed8", "sky": "#0284c7", "teal": "#0f766e",
          "green": "#15803d", "lime": "#65a30d", "yellow": "#ca8a04", "orange": "#ea580c", "red": "#dc2626",
          "pink": "#db2777", "purple": "#7c3aed", "brown": "#92400e", "white": "#ffffff"}
DRAW_COLOR = COLORS["red"]  # a drawing's colour when the call names none: it stands out on a printed page
DRAW_WIDTH = 2.0          # pt, the toolbar pens' size
MIN_WIDTH, MAX_WIDTH = 0.5, 12.0
DASHES = ("dashed", "dotted")
SHAPES = ("line", "arrow", "rect", "ellipse", "triangle", "polygon", "curve")
MAX_SHAPES = 20           # shapes one draw call makes
MAX_SAMPLES = 50_000      # samples they may take together: a tenth of a file's budget
AROUND_PAD = 4.0          # pt between a passage and the shape drawn around it
END_PAD = 3.0             # pt between a passage and a line's end that points at it
# A text box's backgrounds by name (text_box.TEXT_BACKGROUNDS).
BACKGROUNDS = dict(zip(("none", "white", "yellow", "blue"), TEXT_BACKGROUNDS))
MAX_TEXT = 4000           # characters of a text box the agent writes
EDGE = 8.0                # pt a text box or a picture keeps from the page's edge (pdf_free_space.PAGE_MARGIN)
PICTURE_WIDTH = 0.3       # a picture's width as a fraction of the page, when the call names none
STORED_MAX = 64 * 1024 * 1024
# The paper menu's sizes (frontend/src/notebook/notebook.js PAPER_SIZES),
# portrait, and its backgrounds (PAPER_COLORS).
PAPER_SIZES = {"a4": (595.28, 841.89), "letter": (612.0, 792.0), "a5": (419.53, 595.28)}
PAPER_COLORS = {"white": "#ffffff", "cream": "#fbf7ec", "grey": "#f2f3f5", "dark": "#2b2d31"}
PATTERNS = ("blank", "ruled", "grid", "dots")


class Surface(NamedTuple):
    """Where a call draws: a PDF page (``kind`` "pdf-page", ``page`` its
    number, ``pdf`` its file) or a page of paper (``canvas``, ``sheet_id``),
    ``width`` × ``height`` points in the frame ink files use; ``parent`` is
    the block a new block goes under (the page itself, at a PDF page's top
    level; the sheet)."""
    page_id: str
    title: str
    kind: str
    page: int
    sheet_id: str
    width: float
    height: float
    pdf: Path | None
    parent: str

    @property
    def words(self) -> str:
        return f"PDF page {self.page}" if self.kind == "pdf-page" else "the page of paper"

    def space(self) -> dict:
        """The ink file's ``space`` for this surface."""
        if self.kind == "pdf-page":
            return {"kind": "pdf-page", "page": self.page, "width": self.width, "height": self.height}
        return {"kind": "canvas", "width": self.width, "height": self.height}


class Made(NamedTuple):
    """What a call inserts: a block under ``surface.parent`` with
    ``content`` and ``props`` (``ink_file``, for a drawing, is the stroke
    file still to be stored: its URL goes in ``props`` then), ``what`` it
    is in words, and ``anchors``, the passages it was placed by
    (``(quote, page)``), for the approval card."""
    surface: Surface | None
    content: str
    props: dict
    what: str
    anchors: tuple = ()
    ink_file: dict | None = None


def surface(conn, ws: str, page_id: str, title: str, page_props: dict, args: dict) -> Surface:
    """The surface a call names on the page: ``sheet_id``, a page of paper
    of the page (``sheets_of``, at any depth), else ``pdf_page`` of its
    PDF, sized as pdf.js shows it (``pdf_text.page_sizes``)."""
    sheet_id = str(args.get("sheet_id") or "").strip()
    if sheet_id:
        if args.get("pdf_page") not in (None, ""):
            raise ValueError("name one surface: pdf_page for a PDF page, or sheet_id for a page of paper")
        blocks = [block_to_dict(row) for row in fetch_subtree(conn, page_id)]
        sheet = next((s for s in sheets_of(blocks, page_id) if s["id"] == sheet_id), None)
        if sheet is None:
            raise ValueError("sheet_id names no page of paper of this page: read_block labels them")
        paper = sheet["paper"]
        return Surface(page_id, title, "canvas", 0, sheet_id, paper["width"], paper["height"], None, sheet_id)
    page_no = _whole(args.get("pdf_page"), "pdf_page")
    if page_no is None:
        raise ValueError("name the surface: pdf_page (1-based) for a PDF page, or sheet_id for a page of paper")
    view = page_document(page_props)
    if not is_pdf(view):
        raise ValueError(f'"{title}" has no PDF: put it on a page of paper (sheet_id; add_sheet makes one)')
    path = pdf_path(ws, file_hash(view))
    if path is None:
        raise ValueError("the page's PDF is not stored on this server")
    sizes = pdf_text.page_sizes(path)
    if not sizes:
        raise ValueError("the page's PDF cannot be read")
    if not 1 <= page_no <= len(sizes):
        raise ValueError(f"PDF page {page_no} does not exist — the PDF has {len(sizes)} pages")
    width, height = sizes[page_no - 1]
    return Surface(page_id, title, "pdf-page", page_no, "", round(width, 2), round(height, 2), Path(path), page_id)


def _whole(value, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError(f"{name} is a whole number")
    return int(value)


def _color(value, default: str, names=COLORS) -> str:
    """A colour by name (``names``), or as ``#rrggbb``; ``default`` (a
    ``#rrggbb``) when left out."""
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in names:
        return names[text]
    if len(text) == 7 and text[0] == "#" and all(c in "0123456789abcdef" for c in text[1:]):
        return text
    raise ValueError(f"a colour is one of {', '.join(names)}, or #rrggbb")


def _point(surface: Surface, value, name: str) -> tuple[float, float]:
    """An ``[x, y]`` pair of page fractions as points on the surface."""
    if not isinstance(value, (list, tuple)) or len(value) != 2 or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1 for v in value):
        raise ValueError(f"{name} is [x, y], fractions of the page from its top-left corner (0 to 1)")
    return value[0] * surface.width, value[1] * surface.height


def _box(surface: Surface, value, name: str = "box") -> tuple[float, float, float, float]:
    box = parse_box(value)
    if box is None:
        raise ValueError(f"{name} is [x0, y0, x1, y1] as fractions of the page (top-left origin), x0 < x1, y0 < y1")
    return box[0] * surface.width, box[1] * surface.height, box[2] * surface.width, box[3] * surface.height


def passage(surface: Surface, quote) -> tuple[tuple[float, float, float, float], str]:
    """The box of the passage ``quote`` on the surface's PDF page, in its
    points, and the passage as the file spells it. ValueError for a quote
    not there (the closest passage named), on another page, or on a page of
    paper, which has no text to find."""
    from .annotations.places import QUOTE_MAX, passage_at

    if surface.kind != "pdf-page":
        raise ValueError("a quote anchors to a PDF page's text; on a page of paper give places as fractions")
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("a quote is the passage's text, verbatim")
    if len(quote) > QUOTE_MAX:
        raise ValueError(f"a quote holds at most {QUOTE_MAX} characters: quote less")
    position, spelled = passage_at(surface.pdf, quote.strip(), surface.page)
    if position["pageNumber"] != surface.page:
        raise ValueError(f"that quote is on PDF page {position['pageNumber']}, not on page {surface.page}: "
                         "one call draws on one page")
    sx = surface.width / (position.get("width") or surface.width)
    sy = surface.height / (position.get("height") or surface.height)
    rect = position["boundingRect"]
    return (rect["x1"] * sx, rect["y1"] * sy, rect["x2"] * sx, rect["y2"] * sy), spelled


# --- draw ----------------------------------------------------------------------------

def _end(surface: Surface, shape: dict, key: str, n: int, anchors: list):
    """A line's or an arrow's end (``from`` or ``to``) as given: ``("point",
    (x, y))``, or ``("box", box)`` for its quote (``from_quote``,
    ``to_quote``), whose spot on the box waits for the other end
    (``_toward``)."""
    quote = shape.get(f"{key}_quote")
    if isinstance(quote, str) and quote.strip():
        box, spelled = passage(surface, quote)
        anchors.append((spelled, surface.page))
        return "box", box
    return "point", _point(surface, shape.get(key), f"shape {n}'s {key} (or {key}_quote)")


def _toward(end, other) -> tuple[float, float]:
    """Where an end lands: its point, or on its passage's box (padded by
    END_PAD) where the line from the box's centre to ``other`` leaves it."""
    kind, value = end
    if kind == "point":
        return value
    x0, y0, x1, y1 = value
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = other[0] - cx, other[1] - cy
    hw, hh = (x1 - x0) / 2 + END_PAD, (y1 - y0) / 2 + END_PAD
    if abs(dx) <= hw and abs(dy) <= hh:
        return cx, cy  # the other end is on the passage itself
    t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
    return cx + t * dx, cy + t * dy


def _centre(end) -> tuple[float, float]:
    kind, value = end
    return value if kind == "point" else ((value[0] + value[2]) / 2, (value[1] + value[3]) / 2)


_SHAPE_WORDS = {"line": "a line", "arrow": "an arrow", "rect": "a rectangle", "ellipse": "an ellipse",
                "triangle": "a triangle", "polygon": "a polygon", "curve": "a curve"}


def _entry(surface: Surface, shape: dict, n: int, anchors: list) -> tuple[dict, str]:
    """A shape of the call as the redraw's entry (``ink_ai.figure``'s input,
    in the surface's points) and its words."""
    kind = str(shape.get("kind") or "").strip().lower()
    kind = {"rectangle": "rect", "box": "rect", "circle": "ellipse", "oval": "ellipse"}.get(kind, kind)
    if kind not in SHAPES:
        raise ValueError(f"shape {n}: kind is one of {', '.join(SHAPES)}")
    words = _SHAPE_WORDS[kind]
    if kind in ("line", "arrow"):
        start = _end(surface, shape, "from", n, anchors)
        stop = _end(surface, shape, "to", n, anchors)
        a, b = _toward(start, _centre(stop)), _toward(stop, _centre(start))
        if stop[0] == "box":
            words += f" to “{_short(anchors[-1][0])}”"
        return {"kind": kind, "from": list(a), "to": list(b)}, words
    if kind in ("rect", "ellipse"):
        if isinstance(shape.get("around"), str):
            (x0, y0, x1, y1), spelled = passage(surface, shape["around"])
            anchors.append((spelled, surface.page))
            x0, y0, x1, y1 = x0 - AROUND_PAD, y0 - AROUND_PAD, x1 + AROUND_PAD, y1 + AROUND_PAD
            words += f" around “{_short(spelled)}”"
            # an ellipse through the corners of the padded box, so the passage is inside it
            grow = math.sqrt(2) if kind == "ellipse" else 1
        else:
            x0, y0, x1, y1 = _box(surface, shape.get("box"), f"shape {n}'s box")
            grow = 1
        cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
        if kind == "rect":
            return {"kind": "rect", "cx": cx, "cy": cy, "w": w, "h": h, "angle": 0}, words
        return {"kind": "ellipse", "cx": cx, "cy": cy, "rx": w / 2 * grow, "ry": h / 2 * grow, "angle": 0}, words
    points = shape.get("points")
    if not isinstance(points, list):
        raise ValueError(f"shape {n}: points is a list of [x, y] page fractions")
    if len(points) > CURVE_POINTS:
        raise ValueError(f"shape {n}: a {kind} takes at most {CURVE_POINTS} points")
    return {"kind": kind, "points": [list(_point(surface, p, f"shape {n}'s point")) for p in points],
            "closed": shape.get("closed") is True, "smooth": shape.get("smooth") is True}, words


def _short(text: str) -> str:
    words = " ".join(text.split())
    return words[:60] + ("…" if len(words) > 60 else "")


def plan_draw(conn, ws: str, surface: Surface, args: dict) -> Made:
    """The call's shapes (``args["shapes"]``) as one new handwriting group
    on the surface: each a constant-width pen stroke with its shape record
    (``ink_ai.figure``'s params, sampled by ``ink.shape_samples``), in the
    call's ``color``, ``width`` and ``dash`` or a shape's own, a closed one
    filled when it asks. The file is checked as an upload would be
    (``ink.parse_ink``)."""
    shapes = args.get("shapes")
    if not isinstance(shapes, list) or not shapes:
        raise ValueError("shapes is a list of shapes to draw")
    if len(shapes) > MAX_SHAPES:
        raise ValueError(f"one call draws at most {MAX_SHAPES} shapes")
    color = _color(args.get("color"), DRAW_COLOR)
    width = _width(args.get("width"), DRAW_WIDTH)
    dash = _dash(args.get("dash"))
    strokes, words, anchors, left = [], [], [], MAX_SAMPLES
    for n, shape in enumerate(shapes, 1):
        if not isinstance(shape, dict):
            raise ValueError(f"shape {n} is an object with its kind")
        entry, said = _entry(surface, shape, n, anchors)
        made = figure(entry)
        if made is None:
            raise ValueError(f"shape {n} ({said}) is too small or not a shape this tool can draw")
        kind, params, _points = made
        params = placed(params, 0, 0)
        samples = inkmod.shape_samples(kind, params, left)
        if samples is None:
            raise ValueError("these shapes are too long to draw in one call: draw fewer, or simpler ones")
        if not samples:
            raise ValueError(f"shape {n} ({said}) draws nothing")
        left -= len(samples)
        stroke = {"pen": False, "color": _color(shape.get("color"), color),
                  "size": _width(shape.get("width"), width), "shape": {"kind": kind, "params": params}, "ch": "xy",
                  "pts": inkmod.encode_points([{"x": x, "y": y} for x, y in samples], "xy")}
        if (shape_dash := _dash(shape.get("dash"))) or dash:
            stroke["dash"] = shape_dash or dash
        if shape.get("fill") is True and kind in inkmod.CLOSED_SHAPES:
            # its own colour at the file's fill opacity (0.25, written only when another)
            stroke["fill"] = {"color": stroke["color"]}
        strokes.append(stroke)
        words.append(said)
    files = list(inkmod.group_files(strokes, surface.space()))
    if len(files) != 1:
        raise ValueError("these shapes are too large to draw in one call: draw fewer")
    ink = inkmod.parse_ink(files[0])  # what an upload is held to
    props = {"ink_strokes": len(ink.strokes)}
    if surface.kind == "pdf-page":
        props["pdf_position"] = inkmod.pdf_position(ink)
    n = len(words)
    what = (words[0] if n == 1 else f"{n} shapes: " + ", ".join(words))
    return Made(surface, "", props, what, tuple(anchors), files[0])


def _width(value, default: float) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"width is a line width in points, {MIN_WIDTH:g} to {MAX_WIDTH:g}")
    return round(min(MAX_WIDTH, max(MIN_WIDTH, float(value))), 2)


def _dash(value) -> str:
    if value in (None, "", "solid"):
        return ""
    if value not in DASHES:
        raise ValueError("dash is solid, dashed or dotted")
    return value


# --- placing a text box or a picture -------------------------------------------------

def taken(conn, surface: Surface) -> list[tuple[float, float, float, float]]:
    """The boxes already placed on a PDF page, in its points: each
    handwriting group's, text box's and picture's on that page (not on a
    sheet: a sheet's blocks are on the sheet). What ``near`` keeps clear of."""
    blocks = [block_to_dict(row) for row in fetch_subtree(conn, surface.page_id)]
    on_sheets = {b["id"] for s in sheets_of(blocks, surface.page_id) for b in s["blocks"]}
    out = []
    for b in blocks:
        props = b.get("properties") or {}
        if b["id"] in on_sheets:
            continue
        if is_ink(props):
            pos = props.get("pdf_position") or {}
            rect, w, h = pos.get("boundingRect"), pos.get("width"), pos.get("height")
            if pos.get("pageNumber") == surface.page and isinstance(rect, dict) and w and h:
                sx, sy = surface.width / w, surface.height / h
                out.append((rect["x1"] * sx, rect["y1"] * sy, rect["x2"] * sx, rect["y2"] * sy))
        elif is_text_box(props) and box_page(props) == surface.page:
            box = normalize_text_box(props["text_box"])
            out.append((box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]))
        elif is_picture(props) and props.get("pdf_page") == surface.page:
            pic = normalize_picture(props["picture"])
            if pic:
                out.append((pic["x"], pic["y"], pic["x"] + pic["w"], pic["y"] + pic["h"]))
    return out


def _corner(conn, surface: Surface, args: dict, w: float, h: float, anchors: list) -> tuple[float, float]:
    """Where a ``w`` × ``h`` box goes: ``near`` a passage, in the nearest
    empty space beside it; else ``at`` (its top-left corner, fractions)."""
    from .pdf_free_space import spot

    if isinstance(args.get("near"), str):
        box, spelled = passage(surface, args["near"])
        anchors.append((spelled, surface.page))
        found = spot(surface.pdf.read_bytes(), surface.page, box, w, h, taken(conn, surface))
        if found is None:
            raise ValueError("there is no empty space that size beside that passage: give `at` (fractions of the "
                             "page) to place it, or make it smaller")
        return found
    if args.get("at") is not None:
        return _point(surface, args.get("at"), "at")
    raise ValueError("place it: `near` (a quote of the PDF page, beside which it goes) or `at` ([x, y] of its "
                     "top-left corner, fractions of the page)")


def plan_text_box(conn, ws: str, surface: Surface, args: dict) -> Made:
    """A text box of ``text`` (Markdown, math in dollars) on the surface:
    ``size``, ``color``, ``background``, ``width`` (a fraction of the page;
    without it the box takes its text's width), sized as the PDF writers lay
    it out (``text_box.measure``) and kept on the page."""
    text = dollar_math(str(args.get("text") or "")).strip()
    if not text:
        raise ValueError("text is what the box says")
    if len(text) > MAX_TEXT:
        raise ValueError(f"a text box holds at most {MAX_TEXT} characters: put a longer text in a note")
    size = args.get("size") if args.get("size") not in (None, "") else 12
    if size not in TEXT_SIZES:
        raise ValueError(f"size is one of {', '.join(map(str, TEXT_SIZES))}")
    width = args.get("width")
    auto = width in (None, "")
    if not auto and (isinstance(width, bool) or not isinstance(width, (int, float)) or not 0 < width <= 1):
        raise ValueError("width is the box's width as a fraction of the page (0 to 1)")
    bg = str(args.get("background") or "none").strip().lower()
    if bg not in BACKGROUNDS:
        raise ValueError(f"background is one of {', '.join(BACKGROUNDS)}")
    box = normalize_text_box({"x": 0, "y": 0, "w": surface.width * (width or 0.5), "auto": auto, "size": size,
                              "color": _color(args.get("color"), DEFAULT_COLOR),
                              "bg": BACKGROUNDS[bg]})
    w, h = measure(text, box, surface.width)
    box = normalize_text_box({**box, "w": w if auto else box["w"], "h": h})
    if box["h"] > surface.height - 2 * EDGE or box["w"] > surface.width:
        raise ValueError("the text does not fit the page at this size: make it shorter, smaller or wider, or split "
                         "it into several boxes")
    anchors = []
    x, y = _corner(conn, surface, args, box["w"], box["h"], anchors)
    box = keep_on_surface({**box, "x": x, "y": y}, surface.width, surface.height)
    props = {"text_box": box, **({"pdf_page": surface.page} if surface.kind == "pdf-page" else {})}
    return Made(surface, text, props, "a text box", tuple(anchors))


def plan_picture(conn, ws: str, surface: Surface, args: dict) -> Made:
    """A stored picture (``url``) placed on the surface: fitted inside
    ``box`` with its aspect kept, or ``width`` wide (a fraction of the page)
    ``near`` a passage or ``at`` a corner; ``caption`` its text. Its size
    is the file's as a browser shows it (``picture.image_size``: EXIF
    turns, an SVG's own size)."""
    url = str(args.get("url") or "").strip()
    name = upload_name(url) if URL_RE.match(url) else ""
    path = find_upload_file(name, ws) if name else None
    if path is None:
        raise ValueError("url names no picture this workspace holds: use one clip_region stored, or one a note "
                         "embeds (read_block shows it)")
    size = image_size(path.read_bytes()) if path.stat().st_size <= STORED_MAX else None
    if not size or not all(size):
        raise ValueError("that file is not a picture this tool can place")
    aspect = size[1] / size[0]
    anchors = []
    if args.get("box") is not None:
        x0, y0, x1, y1 = _box(surface, args.get("box"))
        w = min(x1 - x0, (y1 - y0) / aspect)
        h = w * aspect
        x, y = x0 + (x1 - x0 - w) / 2, y0 + (y1 - y0 - h) / 2
    else:
        fraction = args.get("width") if args.get("width") not in (None, "") else PICTURE_WIDTH
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 < fraction <= 1:
            raise ValueError("width is the picture's width as a fraction of the page (0 to 1)")
        w = surface.width * fraction
        h = w * aspect
        if h > surface.height - 2 * EDGE:
            h = surface.height - 2 * EDGE
            w = h / aspect
        x, y = _corner(conn, surface, args, w, h, anchors)
        x, y = min(max(x, 0), surface.width - w), min(max(y, 0), surface.height - h)
    pic = normalize_picture({"url": url, "x": x, "y": y, "w": w, "h": h})
    props = {"picture": pic, "t0": int(time.time() * 1000),
             **({"pdf_page": surface.page} if surface.kind == "pdf-page" else {})}
    caption = dollar_math(str(args.get("caption") or "")).strip()
    return Made(surface, caption, props, "a picture", tuple(anchors))


# --- a page of paper -----------------------------------------------------------------

def plan_sheet(args: dict) -> Made:
    """A new page of paper: ``size`` (a4, letter, a5; ``landscape`` turns
    it), ``pattern``, ``color``, ``title`` its text. Collapsed in the notes,
    as the notebook's New page makes one."""
    size = str(args.get("size") or "a4").strip().lower()
    if size not in PAPER_SIZES:
        raise ValueError(f"size is one of {', '.join(PAPER_SIZES)}")
    pattern = str(args.get("pattern") or "blank").strip().lower()
    if pattern not in PATTERNS:
        raise ValueError(f"pattern is one of {', '.join(PATTERNS)}")
    width, height = PAPER_SIZES[size]
    if args.get("landscape") is True:
        width, height = height, width
    color = _color(args.get("color"), PAPER_COLORS["white"], PAPER_COLORS)
    paper = normalize_paper({**DEFAULT_PAPER, "width": width, "height": height, "pattern": pattern, "color": color})
    title = " ".join(str(args.get("title") or "").split())[:300]
    return Made(None, title, {"sheet": paper, "collapsed": True}, f"a {pattern} page of paper ({size})")
