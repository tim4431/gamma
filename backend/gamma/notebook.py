"""Sheets of paper to write on (docs/dev/notebooks.md).

A sheet is a block carrying ``sheet: <paper>``, anywhere among a page's
blocks; nothing else marks a page as a notebook. A page's sheets are those
blocks in document order, at any depth: adding a page is inserting a
block, so sheets two devices add while apart both survive a merge, and
nothing counts pages. A sheet's handwriting is the ink groups under it that
no nearer sheet holds — blocks with an ``ink_url`` whose file
(gamma/ink.py) is drawn on a ``canvas`` space the sheet's size, points from
its top-left corner. Other blocks under a sheet are notes about that page.

A paper is ``{width, height, color, pattern, spacing, line}``: the size in
points, the background colour, ``blank`` / ``ruled`` / ``grid`` / ``dots``
drawn every ``spacing`` points in ``line``. Stored values are read through
``normalize_paper``, never trusted: a missing or bad key takes the
fallback's value. frontend/src/notebook/notebook.js is the twin of the rules
here; tests/shared/paper.json pins both.
"""

import io
import math
import re

DEFAULT_PAPER = {"width": 595.28, "height": 841.89, "color": "#ffffff", "pattern": "blank",
                 "spacing": 24, "line": "#c8d1dc"}
PATTERNS = ("blank", "ruled", "grid", "dots")
MIN_SIDE, MAX_SIDE = 144, 2000
MIN_SPACING, MAX_SPACING = 12, 96
LINE_WIDTH = 0.5
DOT_RADIUS = 0.9
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _clamp(value, low, high):
    v = round(min(high, max(low, value)), 2)
    return int(v) if v == int(v) else v


def normalize_paper(raw, fallback=None) -> dict:
    """A complete paper from a stored one: each missing or bad key from
    ``fallback`` (itself normalized; the built-in default when None),
    sizes and spacing clamped and rounded to hundredths, colours as
    lowercase ``#rrggbb``."""
    out = dict(normalize_paper(fallback) if fallback is not None else DEFAULT_PAPER)
    if not isinstance(raw, dict):
        return out
    for key in ("width", "height"):
        v = _number(raw.get(key))
        if v is not None:
            out[key] = _clamp(v, MIN_SIDE, MAX_SIDE)
    v = _number(raw.get("spacing"))
    if v is not None:
        out["spacing"] = _clamp(v, MIN_SPACING, MAX_SPACING)
    for key in ("color", "line"):
        v = raw.get(key)
        if isinstance(v, str) and _HEX.match(v):
            out[key] = v.lower()
    if raw.get("pattern") in PATTERNS:
        out["pattern"] = raw["pattern"]
    return out


def _steps(extent: float, spacing: float) -> list:
    """Offsets ``k * spacing`` for k = 1, 2, … strictly inside ``extent``."""
    out, k = [], 1
    while k * spacing < extent - 1e-9:
        v = round(k * spacing, 2)
        out.append(int(v) if v == int(v) else v)
        k += 1
    return out


def paper_lines(paper: dict) -> dict:
    """The pattern of a (normalized) paper as geometry: ``{lines: [[x1, y1,
    x2, y2]], dots: [[x, y]]}`` in points from the top-left corner. Ruled
    paper has a line every ``spacing``, grid paper the verticals then the
    horizontals, dot paper a dot at every crossing, row by row."""
    w, h, s, kind = paper["width"], paper["height"], paper["spacing"], paper["pattern"]
    lines, dots = [], []
    if kind in ("ruled", "grid"):
        if kind == "grid":
            lines += [[x, 0, x, h] for x in _steps(w, s)]
        lines += [[0, y, w, y] for y in _steps(h, s)]
    elif kind == "dots":
        xs = _steps(w, s)
        dots = [[x, y] for y in _steps(h, s) for x in xs]
    return {"lines": lines, "dots": dots}


def is_sheet(props: dict | None) -> bool:
    return isinstance((props or {}).get("sheet"), dict)


# --- the sheets as a PDF --------------------------------------------------------------

def _rgb(hex_color: str) -> bytes:
    from .pdf_typeset import num
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return b"%s %s %s" % (num(r), num(g), num(b))


def paper_ops(paper: dict) -> bytes:
    """Content-stream operators painting a sheet's paper in a top-left
    frame (the caller flips y): the background, then the pattern."""
    from .pdf_typeset import num
    w, h = paper["width"], paper["height"]
    ops = [b"q", _rgb(paper["color"]) + b" rg", b"0 0 %s %s re f" % (num(w), num(h))]
    geo = paper_lines(paper)
    if geo["lines"]:
        ops.append(_rgb(paper["line"]) + b" RG %s w" % num(LINE_WIDTH))
        ops += [b"%s %s m %s %s l S" % (num(x1), num(y1), num(x2), num(y2)) for x1, y1, x2, y2 in geo["lines"]]
    if geo["dots"]:
        # a zero-length round-capped segment is a dot
        ops.append(_rgb(paper["line"]) + b" RG 1 J %s w" % num(2 * DOT_RADIUS))
        ops += [b"%s %s m %s %s l S" % (num(x), num(y), num(x), num(y)) for x, y in geo["dots"]]
    ops.append(b"Q")
    return b"\n".join(ops)


def notebook_pdf(sheets: list[tuple[dict, list]]) -> bytes:
    """A PDF of a page's sheets: one PDF page per ``(paper, [InkFile])`` in
    order (at least one), the paper painted and the handwriting drawn on it
    as vectors (the page is the drawing, so every viewer and printer shows
    it as written)."""
    from PyPDF2 import PdfWriter
    from PyPDF2.generic import DecodedStreamObject, NameObject

    from . import ink as inkmod
    from .pdf_typeset import num

    writer = PdfWriter()
    for paper, inks in sheets:
        w, h = paper["width"], paper["height"]
        writer.add_blank_page(w, h)
        page = writer.pages[-1]  # add_blank_page's return value is not the page the writer keeps
        body = [b"q 1 0 0 -1 0 %s cm" % num(h), paper_ops(paper)]
        body += [inkmod.pdf_path_ops(ink, lambda x, y: (x, y)) for ink in inks]
        body.append(b"Q")
        stream = DecodedStreamObject()
        stream.set_data(b"\n".join(body))
        page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def sheets_of(blocks: list[dict], page_id: str) -> list[dict]:
    """The page's sheets in document order, at any depth, from its flat
    block dicts (``block_to_dict``): ``[{id, paper, blocks: [the blocks
    under it that no nearer sheet holds]}]``."""
    kids: dict[str, list[dict]] = {}
    for b in blocks:
        kids.setdefault(b.get("parent_id"), []).append(b)
    for rows in kids.values():
        rows.sort(key=lambda b: (b.get("position") or "", b["id"]))
    out, of = [], {}
    stack = [(b, None) for b in kids.get(page_id, [])]
    while stack:
        b, sheet = stack.pop(0)
        props = b.get("properties") or {}
        if is_sheet(props):
            of[b["id"]] = {"id": b["id"], "paper": normalize_paper(props["sheet"]), "blocks": []}
            out.append(of[b["id"]])
            sheet = b["id"]
        elif sheet:
            of[sheet]["blocks"].append(b)
        stack[:0] = [(c, sheet) for c in kids.get(b["id"], [])]
    return out
