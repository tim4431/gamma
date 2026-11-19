"""A Gamma page as a GoodNotes notebook: the GoodNotes export
(docs/dev/import_export.md "GoodNotes export"). The page's PDF, or its
sheets of paper, become the notebook's pages; its handwriting, highlights,
text boxes and pictures become what GoodNotes draws and edits on them; the
writing is gamma/goodnotes_write.py's.

Only what GoodNotes was seen to open is written (the writer's docstring),
so the page is mapped onto three kinds of element:

- every stroke is a ball pen stroke through its samples, or a highlighter
  stroke, at one width (the median of its drawn widths: GoodNotes' ball pen
  has no per-point width), its colour and opacity as drawn, a dashed or
  dotted one with GoodNotes' line style; a filled shape's fill a filled
  polygon under it;
- a highlight on the PDF's text is a highlighter stroke along each of its
  lines, an underline, a strikethrough or a wavy underline a pen line where
  the viewer draws it, an area note a faintly filled, outlined rectangle;
- a text box is a text box, its Markdown the runs Notability's export makes
  of it (bold, italic, code, lists by their markers); a picture is a
  picture, its crop and flips applied to its pixels (GoodNotes keeps
  neither), its turn kept.

Handwriting, a text box or a picture that came from the PDF and is still
embedded in it is the PDF's own, as such a highlight is: the notebook
carries the stored PDF as it is, and GoodNotes draws its annotations.

Every place is the page's own in points, times GoodNotes' 11/6 canvas units:
a PDF page is drawn at its size as shown, a sheet at its paper's size, its
paper drawn into a one-page PDF that carries the paper (``PAPER_KEY``), so
Gamma's GoodNotes import brings the sheet back. What a notebook cannot hold
(the notes beside the page, recordings, a picture's caption and opacity) is
left out and counted, and the export says so.
"""

import io
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, TextStringObject

from . import colors
from . import goodnotes_write as gw
from . import ink as inkmod
from . import notability as nb
from .db import parse_stamp
from .documents.model import page_document, pdf_file_hash, stored_name
from .highlights import is_highlight
from .notability_export import box_runs
from .notebook import paper_ops, sheets_of
from .pdf_export import AREA_FILL_SHARE, MARKUP_SUBTYPES, display_size, page_frame, still_embedded
from .pdf_image import SVG_DRAW_SIDE, shown_upright, svg_png
from .pdf_typeset import num
from .picture import is_picture, normalize_picture, picture_page, picture_type, stack_order
from .text_box import PAD, box_page, normalize_text_box

C = gw.CANVAS
PAPER_KEY = "/GammaPaper"   # a sheet's paper PDF: the paper, as JSON
HIGHLIGHTER_ALPHA = 0.5     # every GoodNotes highlighter's alpha (docs/research/goodnotes-onenote-import.md)
VARIES = 0.05               # a stroke whose drawn width strays this far from its median is "of varying width"
MAX_VERTICES = 512          # of a fill's polygon
INSET = 10.0                # a GoodNotes text box's insets, canvas units
NO_SURFACE = "page has no PDF and no sheets of paper"
# a line highlight as the viewer draws it (app.css [data-hl-style]): its
# thickness, a share of the line's height, and its place, from the top
LINES = {"underline": (0.09, 1.0), "strikeout": (0.08, 0.56)}
SQUIGGLY_BAND = 0.24        # the wavy underline's band at the foot of the line, its period twice its height
MIN_LINE_PT = 0.75          # 1.5 px at the viewer's scale 2
_WHOLE = {"x": 0, "y": 0, "w": 1, "h": 1}

# What a page loses on its way into a notebook, by kind, as the export's
# warnings say it once per page; ``{n}`` is the count.
LEFT_OUT = {
    "note": ("{n} note stays behind: a GoodNotes page holds handwriting, text boxes and pictures, not notes",
             "{n} notes stay behind: a GoodNotes page holds handwriting, text boxes and pictures, not notes"),
    "recording": ("{n} recording is left out", "{n} recordings are left out"),
    "pdf sheet": ("{n} sheet of paper on a PDF page is left out", "{n} sheets of paper on a PDF page are left out"),
    "nowhere": ("{n} group of handwriting is on no page and is left out",
                "{n} groups of handwriting are on no page and are left out"),
    "picture nowhere": ("{n} picture is on no page and is left out", "{n} pictures are on no page and are left out"),
    "caption": ("{n} picture's caption stays behind: GoodNotes shows none",
                "{n} pictures' captions stay behind: GoodNotes shows none"),
    "picture opacity": ("{n} picture shows at full opacity in GoodNotes, which keeps no picture's opacity",
                        "{n} pictures show at full opacity in GoodNotes, which keeps no picture's opacity"),
    "picture svg": ("{n} SVG picture could not be drawn for the notebook and is left out",
                    "{n} SVG pictures could not be drawn for the notebook and are left out"),
    "missing": ("{n} file of the page is missing on the server and is left out",
                "{n} files of the page are missing on the server and are left out"),
    "even width": ("{n} stroke of varying width (pressure, a pencil, a calligraphy nib) is written at one width: "
                   "GoodNotes' ball pen has one",
                   "{n} strokes of varying width (pressure, a pencil, a calligraphy nib) are written at one width: "
                   "GoodNotes' ball pen has one"),
}


class ExportError(ValueError):
    """The page cannot be a notebook at all: the reason."""


@dataclass
class _Surface:
    """A page of the notebook on its way: its size in points, and the
    page's blocks on it."""
    width: float
    height: float
    page: gw.Page
    inks: list = field(default_factory=list)      # InkFile
    pictures: list = field(default_factory=list)  # blocks
    boxes: list = field(default_factory=list)     # blocks
    marks: list = field(default_factory=list)     # gw elements of the highlights

    def k(self, width: float, height: float) -> tuple[float, float]:
        """Canvas units per unit of a frame ``width`` × ``height`` laid over the page."""
        return self.width / width * C if width > 0 else C, self.height / height * C if height > 0 else C


class _Page:
    """One Gamma page on its way into a notebook: its blocks, its surfaces,
    and what is left out."""

    def __init__(self, blocks: list[dict], page_id: str, stored, resolve_ref=None):
        self.blocks, self.page_id, self.stored = blocks, page_id, stored  # name -> its file (``storage.files``)
        self.resolve_ref = resolve_ref  # [[ref]] → the block it names (markdown_export.block_ref_resolver)
        self.kids: dict[str, list[dict]] = {}
        for b in blocks:
            self.kids.setdefault(b.get("parent_id"), []).append(b)
        for rows in self.kids.values():
            rows.sort(key=lambda b: (b.get("position") or "", b["id"]))
        self.root = next(b for b in blocks if b["id"] == page_id)
        self.left_out = Counter()
        self.surfaces: list[_Surface] = []
        self.images: dict = {}  # (url, crop, flips) → (attachment, kind), or None / False when it failed
        self.draft = gw.Draft(title=nb.note_title("", self.root.get("content") or "", None, ""),
                              created=parse_stamp(self.root.get("created_at")))

    def ordered(self, parent: str):
        """The blocks below ``parent`` in document order."""
        stack = list(reversed(self.kids.get(parent, [])))
        while stack:
            b = stack.pop()
            yield b
            stack += reversed(self.kids.get(b["id"], []))

    def _hide(self, b: dict, hidden: set, notes: bool = False) -> None:
        """Leave out what lies below ``b``; with ``notes``, counted as notes."""
        hidden.update(c["id"] for c in self.kids.get(b["id"], []))
        if notes:
            self.left_out["note"] += sum(1 for c in self.ordered(b["id"]) if (c.get("content") or "").strip())

    # --- the whole page ---------------------------------------------------------------------

    def build(self) -> gw.Draft:
        doc_id = pdf_file_hash(page_document(self.root.get("properties") or {}))
        sheets = [] if doc_id else sheets_of(self.blocks, self.page_id)
        on_sheet = {b["id"]: n for n, sheet in enumerate(sheets) for b in sheet["blocks"]}
        if doc_id:
            self._pdf(doc_id)
        elif sheets:
            self._sheets(sheets)
        else:
            raise ExportError(NO_SURFACE)
        hidden: set[str] = set()
        for b in self.ordered(self.page_id):
            if b["id"] in hidden:
                hidden.update(c["id"] for c in self.kids.get(b["id"], []))
                continue
            kind, p = b.get("kind"), b.get("properties") or {}
            if kind == "sheet":
                if doc_id:
                    self.left_out["pdf sheet"] += 1
                    self._hide(b, hidden)
                continue
            if kind == "highlight" or (kind == "link" and isinstance(p.get("pdf_position"), dict)):
                if kind == "highlight" and doc_id and is_highlight(p) and not still_embedded(p):
                    self._mark(p)
                self._hide(b, hidden, notes=True)
                continue
            if kind == "recording":
                self.left_out["recording"] += 1
                self._hide(b, hidden, notes=True)
                continue
            if doc_id and still_embedded(p):  # the PDF's own annotation, which GoodNotes draws with the PDF
                continue
            if kind == "ink":
                ink = inkmod.read_upload(self.stored, p.get("ink_url") or "")
                surface = self._ink_surface(b, ink, on_sheet, doc_id)
                if ink is None:
                    self.left_out["missing"] += 1
                elif surface is None:
                    self.left_out["nowhere"] += 1
                else:
                    surface.inks.append(ink)
                continue
            if is_picture(p):  # also while the kind column is older than pictures
                surface = self._placed(b, picture_page(p) if doc_id else None, on_sheet, doc_id)
                if surface is None:
                    self.left_out["picture nowhere"] += 1
                else:
                    surface.pictures.append(b)
                continue
            if kind == "text_box":
                surface = self._placed(b, box_page(p) if doc_id else None, on_sheet, doc_id)
                if surface is not None:
                    surface.boxes.append(b)
                    continue
            if (b.get("content") or "").strip():
                self.left_out["note"] += 1
        for surface in self.surfaces:
            self._draw(surface)
        return self.draft

    def _placed(self, b: dict, page: int | None, on_sheet: dict, doc_id) -> _Surface | None:
        """The surface a picture or a text box lies on: its sheet in a
        notebook, on a PDF the page it names; None for none."""
        n = on_sheet.get(b["id"]) if not doc_id else (page - 1 if page and page <= len(self.surfaces) else None)
        return self.surfaces[n] if n is not None else None

    def _ink_surface(self, b: dict, ink, on_sheet: dict, doc_id) -> _Surface | None:
        """The surface an ink group draws on: its sheet in a notebook, on a
        PDF the page its file's space names; None for none."""
        if not doc_id:
            n = on_sheet.get(b["id"])
            return self.surfaces[n] if n is not None else None
        if ink is None or b["id"] in on_sheet or ink.space.kind != "pdf-page":
            return None
        page = ink.space.page or 0
        return self.surfaces[page - 1] if 1 <= page <= len(self.surfaces) else None

    # --- surfaces ---------------------------------------------------------------------------

    def _surface(self, width: float, height: float, attachment: str, page: int = 1) -> None:
        template = gw.Template(attachment, page, (width * C, height * C))
        self.surfaces.append(_Surface(width, height, gw.Page(template)))
        self.draft.pages.append(self.surfaces[-1].page)

    def _pdf(self, doc_id: str) -> None:
        """A PDF page's notebook: a page per page of the PDF, each drawn on
        that page of the one attachment, at its size as shown."""
        path = self.stored(stored_name(doc_id, ".pdf"))
        if path is None:
            raise ExportError("PDF not stored on the server")
        try:
            reader = PdfReader(str(path), strict=False)
            if reader.is_encrypted:
                reader.decrypt("")
            sizes = [display_size(*page_frame(page)) for page in reader.pages]
        except Exception as exc:  # noqa: BLE001 — a PDF pypdf cannot read has no pages to place
            raise ExportError(f"the PDF could not be read ({exc.__class__.__name__})") from None
        if not sizes:
            raise ExportError("the PDF has no pages")
        attachment = self.draft.attach(path, pages=len(sizes))
        for n, (width, height) in enumerate(sizes, 1):
            self._surface(float(width), float(height), attachment, n)

    def _sheets(self, sheets: list) -> None:
        """A notebook's: a page per sheet, drawn on its paper's PDF (one per
        paper, shared by the sheets that have it)."""
        papers: dict[str, str] = {}
        for sheet in sheets:
            paper = sheet["paper"]
            key = json.dumps(paper, sort_keys=True)
            if key not in papers:
                papers[key] = self.draft.attach(paper_pdf(paper), pages=1)
            self._surface(paper["width"], paper["height"], papers[key])

    # --- a surface's elements ---------------------------------------------------------------

    def _draw(self, s: _Surface) -> None:
        """A surface's elements in the order the screen draws them: the
        pictures as they stack, the text boxes, the highlights, then the
        handwriting (band 0 of every group, then band 1, each in file
        order)."""
        for b in stack_order(s.pictures, lambda b: b["properties"]["picture"]):
            self._picture(b, s)
        for b in s.boxes:
            self._box(b, s)
        s.page.elements += s.marks
        for band in (0, 1):
            for ink in s.inks:
                for stroke in ink.strokes:
                    if inkmod.band_of(stroke) == band:
                        self._stroke(stroke, ink.space, s)

    def _stroke(self, stroke: inkmod.Stroke, space, s: _Surface) -> None:
        """A stroke as a ball pen stroke (a highlighter's for the
        highlighter) through its samples at the median of its drawn widths,
        a filled shape's fill a polygon under it."""
        kx, ky = s.k(space.width, space.height)
        samples = inkmod.decode_stroke(stroke)
        points = [(p["x"] * kx, p["y"] * ky) for p in samples]
        widths = [inkmod.stroke_width(stroke, p) for p in samples]
        width = statistics.median(widths)
        if (max(widths) - min(widths) > VARIES * width or stroke.brush == "pencil"
                or (stroke.brush == "calligraphy" and stroke.shape is None)):
            self.left_out["even width"] += 1
        r, g, b, a = inkmod.parse_color(stroke.color)
        highlighter = stroke.tool == "highlighter"
        if highlighter:
            alpha = HIGHLIGHTER_ALPHA
        else:
            alpha = inkmod.drawn_alpha(stroke, a)
            if "o" in stroke.ch:  # a pencil's baked shades, as one
                alpha *= statistics.fmean(p["o"] for p in samples)
        fill = inkmod.fill_of(stroke)
        if fill is not None and len(points) > 2:
            fr, fg, fb, _fa = inkmod.parse_color(fill.color)
            s.page.elements.append(_polygon(points, (fr, fg, fb, fill.opacity)))
        s.page.elements.append(gw.Stroke(points, 2 * width * kx / C, (r, g, b, max(0.01, min(1.0, alpha))),
                                         highlighter, inkmod.DASH.get(stroke.dash) if stroke.dash else None))

    def _mark(self, props: dict) -> None:
        """A highlight on its page: a highlighter stroke along each line of
        the wash, as wide as the line is high; a line style a pen line in
        the colour's line tone, where the viewer draws it; an area note a
        rectangle under a faint wash of its colour, outlined 2 pt wide."""
        pos = props["pdf_position"]
        page = pos.get("pageNumber")
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= len(self.surfaces):
            return
        s = self.surfaces[page - 1]
        try:
            kx, ky = s.k(float(pos.get("width") or 0), float(pos.get("height") or 0))
            rects = [(float(r["x1"]) * kx, float(r["y1"]) * ky, float(r["x2"]) * kx, float(r["y2"]) * ky)
                     for r in pos.get("rects") or [] if isinstance(r, dict) and r.get("x1") is not None]
        except (TypeError, ValueError, KeyError):
            return
        rects = [(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)) for x1, y1, x2, y2 in rects
                 if all(map(math.isfinite, (x1, y1, x2, y2)))]
        if not rects:
            return
        stored = props.get("color")
        r, g, b, a = colors.parse_color(stored) or colors.parse_color(colors.DEFAULT_HIGHLIGHT)
        if pos.get("area"):
            x0, y0 = min(q[0] for q in rects), min(q[1] for q in rects)
            x1, y1 = max(q[2] for q in rects), max(q[3] for q in rects)
            s.marks.append(gw.Box(x0, y0, max(x1 - x0, 1.0), max(y1 - y0, 1.0), fill=(r, g, b, a * AREA_FILL_SHARE),
                                  outline=(2 * C, (r, g, b, a))))
            return
        style = props.get("style") if props.get("style") in MARKUP_SUBTYPES else "highlight"
        if style == "highlight":
            for x0, y0, x1, y1 in rects:
                s.marks.append(_line(x0, x1, (y0 + y1) / 2, y1 - y0, (r, g, b, HIGHLIGHTER_ALPHA), True))
            return
        line = colors.parse_color(colors.line_color(stored) or colors.line_color(colors.DEFAULT_HIGHLIGHT))
        tone = (*line[:3], 1.0)
        for x0, y0, x1, y1 in rects:
            h = y1 - y0
            if style in LINES:
                share, at = LINES[style]
                t = max(MIN_LINE_PT * C, share * h)
                y = min(y0 + at * h, y1 - t / 2)
                s.marks.append(_line(x0, x1, y, t, tone))
            else:  # squiggly: a wave in the band at the line's foot
                band = max(2 * MIN_LINE_PT * C, SQUIGGLY_BAND * h)
                t = band / 3
                top, foot = y1 - band + t / 2, y1 - t / 2
                steps = max(2, math.ceil((x1 - x0) / band))
                pts = [(x0 + (x1 - x0) * i / steps, foot if i % 2 else top) for i in range(steps + 1)]
                s.marks.append(gw.Stroke(pts, 2 * t / C, tone))

    def _box(self, b: dict, s: _Surface) -> None:
        """A text box: its frame such that GoodNotes' text starts where
        Gamma's does (GoodNotes insets its text by INSET, Gamma by PAD),
        its runs of Markdown (``runs``), its fill."""
        box = normalize_text_box((b.get("properties") or {}).get("text_box"))
        if box is None:
            return
        rgba = inkmod.parse_color(box["color"])
        made = runs(b.get("content") or "", box["size"] * C, rgba, self.resolve_ref)
        x, y = (box["x"] + PAD) * C - INSET, (box["y"] + PAD) * C - INSET
        w = max(0.0, box["w"] - 2 * PAD) * C + 2 * INSET
        h = max(0.0, box["h"] - 2 * PAD) * C + 2 * INSET
        fill = inkmod.parse_color(box["bg"]) if box["bg"] else None
        s.page.elements.append(gw.Box(x, y, w, h, runs=made, fill=fill))

    # --- pictures ---------------------------------------------------------------------------

    def _picture(self, b: dict, s: _Surface) -> None:
        """A picture at its frame and turn, its file with its crop and
        flips applied (``_image``); one whose file is missing or does not
        read is left out (counted)."""
        pic = normalize_picture((b.get("properties") or {}).get("picture"))
        if pic is None:
            return
        made = self._image(pic)
        if not made:
            if made is None:
                self.left_out["missing"] += 1
            return
        if (b.get("content") or "").strip():
            self.left_out["caption"] += 1
        if "opacity" in pic:
            self.left_out["picture opacity"] += 1
        attachment, kind = made
        s.page.elements.append(gw.Picture(attachment, (pic["x"] + pic["w"] / 2) * C, (pic["y"] + pic["h"] / 2) * C,
                                          pic["w"] * C, pic["h"] * C, math.radians(pic["angle"]), kind))

    def _image(self, pic: dict) -> tuple | bool | None:
        """``(attachment, kind)`` of a picture's file as it is shown: a PNG
        or JPEG shown whole as it is, else its pixels cut to the crop and
        flipped (``pixels``), an SVG drawn as a PNG first; one attachment
        per file and look. None when the file is missing or does not read;
        False, counted ("picture svg"), for an SVG that does not draw."""
        key = (pic["url"], tuple(pic["crop"].values()), pic["flip_h"], pic["flip_v"])
        if key in self.images:
            return self.images[key]
        path = self.stored(pic["url"].rsplit("/", 1)[-1])
        made = None
        try:
            data = path.read_bytes() if path is not None else None
        except OSError:
            data = None
        if data is not None:
            kind = picture_type(data)
            if pic["crop"] == _WHOLE and not (pic["flip_h"] or pic["flip_v"]) and kind in ("image/png", "image/jpeg"):
                made = (self.draft.attach(path), gw.JPEG if kind == "image/jpeg" else gw.PNG)
            elif kind == "image/svg+xml":
                try:
                    png, _w, _h = svg_png(data, SVG_DRAW_SIDE)
                except ValueError:
                    self.left_out["picture svg"] += 1
                    made = False
                else:
                    made = self._pixels(png, "image/png", pic)
            elif kind:
                made = self._pixels(data, kind, pic)
        self.images[key] = made
        return made

    def _pixels(self, data: bytes, kind: str, pic: dict) -> tuple | None:
        out = pixels(data, kind, pic)
        return (self.draft.attach(out[0]), out[1]) if out is not None else None


# --- what the elements are made of ------------------------------------------------------------

def _line(x0: float, x1: float, y: float, thickness: float, rgba: tuple, highlighter: bool = False) -> gw.Stroke:
    """A round-capped line from x0 to x1 at y, ``thickness`` canvas units
    wide, its caps within the two ends (a dot where it is no longer than it
    is wide)."""
    half = thickness / 2
    a, b = x0 + half, x1 - half
    pts = [(a, y), (b, y)] if b > a else [((x0 + x1) / 2, y)]
    return gw.Stroke(pts, 2 * thickness / C, rgba, highlighter)


def _polygon(points: list, rgba: tuple) -> gw.Box:
    """A filled polygon through ``points`` (canvas units): a shape box at
    their bounding box, its vertices in the box's unit square."""
    if len(points) > MAX_VERTICES:
        step = math.ceil(len(points) / MAX_VERTICES)
        points = points[::step]
    xs, ys = [x for x, _y in points], [y for _x, y in points]
    x0, y0 = min(xs), min(ys)
    w, h = max(max(xs) - x0, 1e-3), max(max(ys) - y0, 1e-3)
    return gw.Box(x0, y0, w, h, fill=rgba, vertices=[((x - x0) / w, (y - y0) / h) for x, y in points])


def runs(md: str, size: float, rgba: tuple, resolve_ref=None) -> list[gw.Run]:
    """A text box's Markdown as GoodNotes runs: Notability's export's runs
    (``notability_export.box_runs``: a paragraph per line, inline marks as
    the font's face, a ``[[ref]]`` as what it names), bold and italic as
    the run's weight and slant, code in Menlo, each list item led by its
    marker as text (GoodNotes' list fields are not known) indented by its
    level; the strikes, underlines and marks GoodNotes' fields are not
    known for are plain text. ``size`` in canvas units."""
    out: list[gw.Run] = []
    start = True
    for r in box_runs(md, size, rgba, resolve_ref):
        if start and r.list_style:
            marker = {1: "• ", 2: f"{r.number}. ", 3: "☑ " if r.checked else "☐ "}[r.list_style]
            out.append(gw.Run("    " * r.level + marker, r.size, r.rgba))
        face = r.font.split("-", 1)[1] if "-" in r.font else ""
        out.append(gw.Run(r.text, r.size, r.rgba, bold="Bold" in face, italic="Italic" in face,
                          font="Menlo" if r.font.startswith("Menlo") else None))
        start = r.text.endswith("\n")
    return out or [gw.Run("", size, rgba)]


def pixels(data: bytes, kind: str, pic: dict) -> tuple[bytes, int] | None:
    """A picture's file as it is shown, ``(bytes, kind)``: turned upright
    by the EXIF orientation the browser applies, cut to the crop, flipped,
    a JPEG again for a JPEG and a PNG for anything else; None when it does
    not read."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGBA") if source.mode not in ("RGB", "RGBA", "L") else source.copy()
    except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
        return None
    image = shown_upright(image, data)
    w, h = image.size
    c = pic["crop"]
    left, top = min(w - 1, round(c["x"] * w)), min(h - 1, round(c["y"] * h))
    box = (left, top, max(left + 1, round((c["x"] + c["w"]) * w)), max(top + 1, round((c["y"] + c["h"]) * h)))
    if box != (0, 0, w, h):
        image = image.crop(box)
    if pic["flip_h"]:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if pic["flip_v"]:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    out = io.BytesIO()
    if kind == "image/jpeg":
        image.convert("RGB").save(out, "JPEG", quality=92)
        return out.getvalue(), gw.JPEG
    image.save(out, "PNG")
    return out.getvalue(), gw.PNG


def paper_pdf(paper: dict) -> bytes:
    """A sheet's paper as a one-page PDF of its size (``notebook.paper_ops``:
    its colour, then its pattern), the paper itself under PAPER_KEY in the
    page's dictionary, which every reader passes by and Gamma's GoodNotes
    import reads back."""
    writer = PdfWriter()
    writer.add_blank_page(paper["width"], paper["height"])
    page = writer.pages[-1]
    content = DecodedStreamObject()
    content.set_data(b"q 1 0 0 -1 0 %s cm\n%s\nQ" % (num(paper["height"]), paper_ops(paper)))
    page[NameObject("/Contents")] = writer._add_object(content.flate_encode())
    page[NameObject(PAPER_KEY)] = TextStringObject(json.dumps(paper, sort_keys=True, separators=(",", ":")))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# --- the entry point ---------------------------------------------------------------------------

def page_draft(blocks: list[dict], page_id: str, stored, resolve_ref=None) -> tuple[gw.Draft, list[str]]:
    """The notebook of a page (its blocks as ``block_to_dict`` gives them,
    the root among them) and the warnings for what it leaves out;
    ``resolve_ref`` reads a ``[[ref]]`` as the block it names.
    ExportError when the page cannot be a notebook."""
    page = _Page(blocks, page_id, stored, resolve_ref)
    draft = page.build()
    return draft, nb.left_out_reasons(page.left_out, table=LEFT_OUT)


def write_page(blocks: list[dict], page_id: str, stored, out, resolve_ref=None) -> list[str]:
    """``page_draft`` written as a .goodnotes into ``out`` (a path or a
    binary file); the warnings."""
    draft, warnings = page_draft(blocks, page_id, stored, resolve_ref)
    gw.write_notebook(draft, out)
    return warnings
