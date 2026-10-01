"""Burn Gamma highlight blocks into a PDF as standard annotations — text
highlights as /Highlight, area notes (Ctrl+drag rectangles, position carries
``area: true``) as /Square — so the exported file shows them (with notes as
annotation popups) in Acrobat, SumatraPDF, Preview, browsers, etc.

Coordinate round-trip: the viewer stores rects in top-left-origin page-render
pixels together with the render size (``width``/``height``), i.e. effectively
normalized coordinates in pdf.js viewport space. pdf.js viewports are based on
the crop box and apply /Rotate, so the inverse mapping here must too. This is
the exact reverse of what routers/imports.py does when reading embedded
annotations (which come straight from PDF user space).

/Highlight carries no appearance stream (/AP) — every mainstream viewer
synthesizes the marker look from /QuadPoints + /C. /Square does: synthesized
from /Rect + /C + /BS it would be a bare outline, and the viewer's area note
also has a faint fill.

Zotero compatibility: its reader imports /Highlight (→ highlight) and /Square
(→ image/area annotation) — but pdf-worker's ``readRawAnnotation`` DROPS a
/Square that carries no annotation id (``/Zotero:Key``, or ``/NM`` shaped
``Zotero-<key>``); highlights import fine without one. So every /Square gets a
deterministic ``/NM`` key derived from the highlight block id (stable across
re-exports, so Zotero can dedupe), spelled in Zotero's own 8-char key
alphabet. Highlights stay id-less on purpose.

Handwriting becomes /Ink and text boxes /FreeText (gamma/text_box.py), each
with a private key holding Gamma's own record for a lossless re-import.
"""

import hashlib
import io
import json
import re

from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NullObject,
    NumberObject,
    TextStringObject,
)

_RGBA_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)")
_HEX_RE = re.compile(r"#([0-9a-fA-F]{6})$")
DEFAULT_COLOR = (1.0, 226 / 255, 143 / 255, 0.65)  # the viewer's yellow


class ExportPdfReader(PdfReader):
    """Keep dangling optional references from breaking PyPDF2's clone path."""

    def get_object(self, indirect_reference):
        obj = super().get_object(indirect_reference)
        if obj is None:
            # Missing indirect objects have PDF null semantics. PyPDF2 reads
            # them as Python None, which its writer cannot clone. Retain the
            # reference so the null is registered/deduplicated in the writer.
            obj = NullObject()
            obj.indirect_reference = indirect_reference
            self.cache_indirect_object(
                indirect_reference.generation, indirect_reference.idnum, obj
            )
        return obj


def parse_css_color(value):
    """CSS color string (as stored on highlight blocks) → (r, g, b, alpha) in 0..1."""
    m = _RGBA_RE.match((value or "").strip())
    if m:
        r, g, b = (min(int(v), 255) / 255 for v in m.groups()[:3])
        a = min(float(m.group(4)), 1.0) if m.group(4) else 1.0
        return (r, g, b, a)
    m = _HEX_RE.match((value or "").strip())
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (1.0,)
    return DEFAULT_COLOR


def viewer_point_to_pdf(u, v, rotation, crop):
    """A display-space point, normalized (``u`` right, ``v`` down from the
    top of the page as shown) → PDF user space (bottom-left origin).
    ``crop`` is (cx0, cy0, cx1, cy1); ``rotation`` a multiple of 90."""
    cx0, cy0, cx1, cy1 = crop
    cw, ch = cx1 - cx0, cy1 - cy0
    if rotation == 90:
        return cx0 + v * cw, cy0 + u * ch
    if rotation == 180:
        return cx1 - u * cw, cy0 + v * ch
    if rotation == 270:
        return cx1 - v * cw, cy1 - u * ch
    return cx0 + u * cw, cy1 - v * ch


def pdf_point_to_viewer(px, py, rotation, crop):
    """The inverse of ``viewer_point_to_pdf``: a PDF user-space point →
    normalized display space (``u`` right, ``v`` down)."""
    cx0, cy0, cx1, cy1 = crop
    cw, ch = cx1 - cx0, cy1 - cy0
    if rotation == 90:
        return (py - cy0) / ch, (px - cx0) / cw
    if rotation == 180:
        return (cx1 - px) / cw, (py - cy0) / ch
    if rotation == 270:
        return (cy1 - py) / ch, (cx1 - px) / cw
    return (px - cx0) / cw, (cy1 - py) / ch


def display_size(crop, rotation):
    """The page as shown, in points: the pdf.js viewport at scale 1, the
    crop box with its sides swapped on a quarter turn."""
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    return (ch, cw) if rotation in (90, 270) else (cw, ch)


def _viewer_rect_to_pdf(rect, rotation, crop):
    """One stored viewer rect → (x1, y1, x2, y2) in PDF user space."""
    w = float(rect.get("width") or 0) or 1.0
    h = float(rect.get("height") or 0) or 1.0
    (ax, ay), (bx, by) = (viewer_point_to_pdf(float(vx) / w, float(vy) / h, rotation, crop)
                          for vx, vy in ((rect["x1"], rect["y1"]), (rect["x2"], rect["y2"])))
    return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))


def _page_box(page, key):
    """A page's /MediaBox or /CropBox as (x0, y0, x1, y1), corners in
    order; None when it is missing or has no area."""
    try:
        v = [float(n) for n in _resolve(page.get(key))]
    except (TypeError, ValueError):
        return None
    if len(v) != 4:
        return None
    box = (min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3]))
    return box if box[2] > box[0] and box[3] > box[1] else None


def page_frame(page):
    """(view box, rotation) of a PyPDF2 page — the frame both mappers need.
    The view box is the part of the page a viewer shows, and so the frame the
    client stores positions in: the crop box clipped to the media box, as
    pdf.js, pdfium and MuPDF clip it; the media box when there is no crop box
    or the two do not meet (pdf.js's rule); a US Letter page with no media box."""
    media = _page_box(page, "/MediaBox") or (0.0, 0.0, 612.0, 792.0)
    crop = _page_box(page, "/CropBox") or media
    view = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
    if view[2] <= view[0] or view[3] <= view[1]:
        view = media
    try:
        rotation = int(page.rotation) % 360
    except Exception:
        rotation = 0
    return view, rotation


def _resolve(obj):
    """PyPDF2 dict access can hand back unresolved IndirectObject references."""
    return obj.get_object() if hasattr(obj, "get_object") else obj


# --- embedded annotations as the importer takes them --------------------------------
# routers/imports.py turns a PDF's own annotations into blocks; the export
# replaces the embedded original of a text box it writes again. Both read the
# annotations with these rules.

# The /F bits that keep an annotation off the screen (ISO 32000-1, 12.5.3):
# Hidden and NoView. pdf.js does not draw such an annotation, so neither is
# it imported.
_UNSEEN = 2 | 32
# The embedded annotations a Gamma text box stands for: typed text and
# sticky notes (the importer makes boxes of them, the export replaces them).
TEXT_BOX_TYPES = frozenset({"/FreeText", "/Text"})


def annotation_shown(annot) -> bool:
    """Whether an annotation is something the page shows as its own: not
    Hidden or NoView, and not a review-state stamp (a /Text with /State or
    /StateModel: "Accepted by …", kept in its parent's thread)."""
    try:
        flags = int(_resolve(annot.get("/F")) or 0)
    except (TypeError, ValueError):
        flags = 0
    return not flags & _UNSEEN and "/State" not in annot and "/StateModel" not in annot


def reply_parent(annot):
    """The annotation a reply answers (its /IRT), or None for one that is
    no reply. A /RT /Group member is an annotation of its own, grouped with
    its parent rather than a comment on it."""
    if str(_resolve(annot.get("/RT")) or "") == "/Group":
        return None
    parent = _resolve(annot.get("/IRT"))
    return parent if hasattr(parent, "get") else None


def first_rect(annot):
    """(x0, y0, x1, y1) of an annotation's first quad (/QuadPoints) or its
    /Rect, in user space; None without either."""
    qp = _resolve(annot.get("/QuadPoints"))
    if qp:
        nums = [float(_resolve(v)) for v in qp]
        if len(nums) >= 8:
            xs, ys = nums[0:8:2], nums[1:8:2]
            return min(xs), min(ys), max(xs), max(ys)
    r = [float(_resolve(v)) for v in (_resolve(annot.get("/Rect")) or ())]
    if len(r) != 4:
        return None
    return min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])


def annotation_key(pnum: int, subtype: str, rect) -> str:
    """The ``imported_annot`` key of an embedded annotation other than ink:
    its page, subtype and the rounded corners of ``first_rect``. The importer
    has keyed them so from the start, so a PDF imported before adds nothing
    twice."""
    x0, y0, x1, _y1 = rect
    return f"{pnum}:{subtype}:{round(x0)}:{round(y0)}:{round(x1)}"


def drop_annotations(page, doomed) -> int:
    """Take the annotations whose objects are in ``doomed`` (``id()`` of the
    resolved dictionaries) off ``page``'s /Annots, with their threads:
    the replies and review states that answer them (/IRT), at any depth, and
    their /Popup windows. Returns how many annotations went."""
    from PyPDF2.generic import ArrayObject

    items = [(ref, _resolve(ref)) for ref in (_resolve(page.get("/Annots")) or ())]
    gone = set(doomed)
    while True:
        more = {id(obj) for _ref, obj in items if id(obj) not in gone and (
            id(_resolve(obj.get("/IRT"))) in gone
            or (str(obj.get("/Subtype", "")) == "/Popup" and id(_resolve(obj.get("/Parent"))) in gone))}
        if not more:
            break
        gone |= more
    kept = ArrayObject(ref for ref, obj in items if id(obj) not in gone)
    removed = len(items) - len(kept)
    if removed:
        page[NameObject("/Annots")] = kept
    return removed


def _drop_replaced(writer, keys) -> None:
    """Take out of the copy the embedded text boxes a Gamma version
    replaces: each shown /FreeText or /Text, no reply, whose key is among
    ``keys`` (the ``imported_annot`` of the boxes the import made of them),
    with its thread."""
    for pnum, page in enumerate(writer.pages, start=1):
        doomed = set()
        for ref in _resolve(page.get("/Annots")) or ():
            obj = _resolve(ref)
            subtype = str(obj.get("/Subtype", ""))
            if (subtype in TEXT_BOX_TYPES and annotation_shown(obj) and reply_parent(obj) is None
                    and (rect := first_rect(obj)) and annotation_key(pnum, subtype, rect) in keys):
                doomed.add(id(obj))
        if doomed:
            drop_annotations(page, doomed)


def _finish_annotation(annot, color, note, author):
    r, g, b, alpha = color
    annot[NameObject("/C")] = ArrayObject((FloatObject(r), FloatObject(g), FloatObject(b)))
    annot[NameObject("/CA")] = FloatObject(round(alpha, 3))
    annot[NameObject("/F")] = NumberObject(4)  # print
    if note:
        annot[NameObject("/Contents")] = TextStringObject(note)
    if author:
        annot[NameObject("/T")] = TextStringObject(author)
    return annot


def _highlight_annotation(rects, color, note, author):
    quads, xs, ys = [], [], []
    for x1, y1, x2, y2 in rects:
        # Quad order: upper-left, upper-right, lower-left, lower-right.
        quads.extend((x1, y2, x2, y2, x1, y1, x2, y1))
        xs.extend((x1, x2))
        ys.extend((y1, y2))
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Highlight"),
        NameObject("/Rect"): ArrayObject(
            FloatObject(v) for v in (min(xs), min(ys), max(xs), max(ys))
        ),
        NameObject("/QuadPoints"): ArrayObject(FloatObject(v) for v in quads),
    })
    return _finish_annotation(annot, color, note, author)


# Zotero's item-key alphabet (32 chars — 5 bits per char).
_ZOTERO_KEY_CHARS = "23456789ABCDEFGHIJKLMNPQRSTUVWXZ"


def zotero_annot_key(highlight_id: str) -> str:
    """Deterministic 8-char Zotero-style key for a highlight block id."""
    digest = hashlib.sha1((highlight_id or "").encode("utf-8")).digest()
    return "".join(_ZOTERO_KEY_CHARS[b & 31] for b in digest[:8])


# The area note's wash, as a share of the colour's alpha — the viewer's
# ``color-mix(in srgb, <color> 25%, transparent)``.
AREA_FILL_SHARE = 0.25


def _square_appearance(writer, box, color):
    """The /Square's normal appearance, drawn like the viewer's area note: a
    faint multiply wash of the colour under a 2pt border. /IC is not set — a
    viewer that regenerates the look from it would fill at the full /CA and
    hide the figure; without an /AP it falls back to the plain outline."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    r, g, b, alpha = color
    rgb = f"{r:.4f} {g:.4f} {b:.4f}"
    ops = (f"/Fill gs {rgb} rg 0 0 {w:.2f} {h:.2f} re f\n"
           f"/Stroke gs {rgb} RG 2 w 1 1 {max(w - 2, 0):.2f} {max(h - 2, 0):.2f} re S\n")

    def gstate(key, value):
        return DictionaryObject({
            NameObject("/Type"): NameObject("/ExtGState"),
            NameObject(key): FloatObject(round(value, 3)),
            NameObject("/BM"): NameObject("/Multiply"),
        })

    stream = DecodedStreamObject()
    stream.set_data(ops.encode("ascii"))
    stream.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject(FloatObject(v) for v in (0, 0, w, h)),
        NameObject("/Resources"): DictionaryObject({
            NameObject("/ExtGState"): DictionaryObject({
                NameObject("/Fill"): gstate("/ca", alpha * AREA_FILL_SHARE),
                NameObject("/Stroke"): gstate("/CA", alpha),
            }),
        }),
    })
    return writer._add_object(stream)


def _square_annotation(writer, rects, color, note, author, highlight_id=""):
    """Area note → /Square over the bounding box of the rects, with an
    appearance stream for the viewer's look (``_square_appearance``). The
    /NM id is what makes Zotero import it (see module docstring)."""
    xs = [v for x1, _, x2, _ in rects for v in (x1, x2)]
    ys = [v for _, y1, _, y2 in rects for v in (y1, y2)]
    box = (min(xs), min(ys), max(xs), max(ys))
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Square"),
        NameObject("/Rect"): ArrayObject(FloatObject(v) for v in box),
        NameObject("/BS"): DictionaryObject({
            NameObject("/W"): NumberObject(2),
            NameObject("/S"): NameObject("/S"),
        }),
        NameObject("/AP"): DictionaryObject({
            NameObject("/N"): _square_appearance(writer, box, color),
        }),
    })
    if highlight_id:
        annot[NameObject("/NM")] = TextStringObject(f"Zotero-{zotero_annot_key(highlight_id)}")
    return _finish_annotation(annot, color, note, author)


def _ink_annotations(ink, rotation, crop, note, author, block_id=""):
    """One handwriting group → ``/Ink`` annotations, one per look bucket
    (``ink.ink_buckets``): ``/InkList`` polylines in user space, ``/BS /W``
    the bucket's mean drawn width, ``/C`` + ``/CA`` its colour. Every
    annotation also carries its strokes as ``/GammaInk`` (the gamma-ink JSON)
    so a Gamma re-import keeps pressure and time; other readers ignore the
    private key. The note rides on the first bucket only."""
    from . import ink as inkmod
    sw, sh = float(ink.space.width), float(ink.space.height)
    # Display points are pdf.js scale-1 points, so the crop box has the same
    # size unless the page was replaced; widths scale by the ratio.
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    k = ((cw if rotation in (0, 180) else ch) / sw) if sw else 1.0
    out = []
    for n, bucket in enumerate(inkmod.ink_buckets(ink)):
        first = bucket[0]
        r, g, b, a = inkmod.parse_color(first.color)
        paths, xs, ys, widths = [], [], [], []
        for stroke in bucket:
            poly = inkmod.stroke_polyline(stroke)
            flat = []
            for x, y, w in poly:
                px, py = viewer_point_to_pdf(x / sw, y / sh, rotation, crop)
                flat.extend((px, py))
                xs.append(px)
                ys.append(py)
                widths.append(w)
            if len(poly) == 1:
                flat.extend(flat[:2])
            paths.append(ArrayObject(FloatObject(round(v, 2)) for v in flat))
        if not paths:
            continue
        width = (sum(widths) / len(widths)) * k
        pad = width / 2 + 1
        annot = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Ink"),
            NameObject("/Rect"): ArrayObject(
                FloatObject(v) for v in (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)),
            NameObject("/InkList"): ArrayObject(paths),
            NameObject("/BS"): DictionaryObject({
                NameObject("/W"): FloatObject(round(width, 2)),
                NameObject("/S"): NameObject("/S"),
            }),
            NameObject("/GammaInk"): TextStringObject(inkmod.dumps({
                "format": inkmod.FORMAT, "version": inkmod.VERSION,
                "space": ink.space.model_dump(exclude_none=True),
                "strokes": [s.model_dump(exclude_none=True) for s in bucket],
            }).decode("utf-8")),
        })
        if block_id:
            annot[NameObject("/NM")] = TextStringObject(f"Zotero-{zotero_annot_key(f'{block_id}:{n}')}")
        out.append(_finish_annotation(annot, (r, g, b, min(a, first.opacity)),
                                      note if n == 0 else "", author))
    return out


def _upright(rotation, w, h):
    """The appearance form's /Matrix on a page shown with ``rotation``: it
    turns the form's w × h BBox back against the page's turn, so the text
    reads upright on screen, and lands the result at the origin."""
    return {90: (0, 1, -1, 0, h, 0), 180: (-1, 0, 0, -1, w, h),
            270: (0, -1, 1, 0, 0, w)}.get(rotation, (1, 0, 0, 1, 0, 0))


def _pdf_date(stamp: str) -> str:
    """A ``page_now`` stamp (UTC ISO) → a PDF date string."""
    return f"D:{re.sub(r'[^0-9]', '', stamp[:19])}Z"


def _text_box_annotation(writer, glyphs, tb, rotation, crop, author, resolve_ref=None):
    """One text box → a ``/FreeText`` with an appearance (``/AP /N``) that
    typesets the text as Gamma shows it (``text_box.pdf_ops``), upright on a
    turned page and grown when the text needs more room than the box; the
    ``/Rect`` is the drawn box in user space. ``/DA`` carries the font, size
    and colour and ``/C`` the fill (the key Acrobat, MuPDF and pypdf read as
    a free-text background), for a viewer that rebuilds the look after an
    edit, and ``/Rotate`` (Adobe's key, read by pdf.js and MuPDF) keeps that
    rebuilt text upright too. ``/BS /W 0``: no border. ``/Contents`` is the
    plain text, references read through ``resolve_ref``; the private
    ``/GammaTextBox`` holds the Markdown, the box and that text, for a
    lossless re-import that can tell another viewer's edit of /Contents
    from a reference label."""
    from . import text_box
    from .pdf_typeset import num

    box, md = tb["box"], tb["content"]
    disp_w, disp_h = display_size(crop, rotation)
    fonts = set()
    ops, w, h = text_box.pdf_ops(md, box, disp_w, glyphs, fonts, resolve_ref)
    text = text_box.plain_text(md, resolve_ref)
    w, h = round(w, 2), round(h, 2)
    (ax, ay), (bx, by) = (viewer_point_to_pdf(x / disp_w, y / disp_h, rotation, crop)
                          for x, y in ((box["x"], box["y"]), (box["x"] + w, box["y"] + h)))
    stream = DecodedStreamObject()
    stream.set_data(b"q 1 0 0 -1 0 %s cm\n%s\nQ" % (num(h), ops))
    form = stream.flate_encode()
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject(FloatObject(v) for v in (0, 0, w, h)),
        NameObject("/Matrix"): ArrayObject(FloatObject(v) for v in _upright(rotation, w, h)),
        NameObject("/Resources"): text_box.pdf_resources(fonts, glyphs),
    })
    rgb = " ".join(f"{v:.4f}" for v in parse_css_color(box["color"])[:3])
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/FreeText"),
        NameObject("/IT"): NameObject("/FreeText"),
        NameObject("/Rect"): ArrayObject(
            FloatObject(round(v, 3)) for v in (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))),
        NameObject("/Contents"): TextStringObject(text),
        NameObject("/DA"): TextStringObject(f"/Helv {box['size']:g} Tf {rgb} rg"),
        NameObject("/BS"): DictionaryObject({NameObject("/W"): NumberObject(0)}),
        NameObject("/F"): NumberObject(4),  # print
        NameObject("/AP"): DictionaryObject({NameObject("/N"): writer._add_object(form)}),
        NameObject("/NM"): TextStringObject(f"Zotero-{zotero_annot_key(tb['id'])}"),
        NameObject("/GammaTextBox"): TextStringObject(json.dumps(
            {"v": 1, "md": md, "box": box, "text": text}, ensure_ascii=False, separators=(",", ":"))),
    })
    if box["bg"]:
        annot[NameObject("/C")] = ArrayObject(FloatObject(round(v, 4)) for v in parse_css_color(box["bg"])[:3])
    if rotation:
        annot[NameObject("/Rotate")] = NumberObject(rotation)
    if author:
        annot[NameObject("/T")] = TextStringObject(author)
    if tb.get("modified"):
        annot[NameObject("/M")] = TextStringObject(_pdf_date(tb["modified"]))
    return annot


def highlight_note_text(block, children_by_id):
    """The annotation popup text: the highlight's own comment plus its nested
    notes as an indented bullet list. A text box on a PDF page among them is
    left out with the notes under it: it is written as its own /FreeText,
    whose text speaks for itself on the page. A box under a sheet among them
    is on that sheet (the nearest sheet wins), so it stays a note line."""
    from .notebook import is_sheet
    from .text_box import box_page

    def walk(bid, depth, on_sheet):
        lines = []
        for child in children_by_id.get(bid, []):
            props = child.get("properties")
            if box_page(props, on_sheet):
                continue
            text = (child.get("content") or "").strip()
            if text:
                lines.append("  " * depth + "- " + text)
            lines.extend(walk(child["id"], depth + 1, on_sheet or is_sheet(props)))
        return lines

    parts = []
    own = (block.get("content") or "").strip()
    if own:
        parts.append(own)
    parts.extend(walk(block["id"], 0, False))
    return "\n".join(parts)


def annotate_pdf(pdf_bytes: bytes, highlights, author: str = "", ink=(),
                 text_boxes=(), replaced=(), resolve_ref=None) -> tuple[bytes, int]:
    """Return (annotated pdf bytes, number of annotations written).

    ``highlights``: [{position: <pdf_position dict>, color: <css string>,
    note: <str>, id: <highlight block id, optional>}]. Positions with no
    usable rects or an out-of-range page are skipped rather than failing the
    whole export. ``ink``: [{ink: <gamma.ink.InkFile>, note, id}], the
    handwriting groups, written as ``/Ink`` (``_ink_annotations``).
    ``text_boxes``: [{box: <normalized text_box>, content, page, id,
    modified}], written as ``/FreeText`` (``_text_box_annotation``) under
    the rest, as the page draws them under the ink, their references read
    through ``resolve_ref`` (``text_box._paragraphs``). ``replaced``: the
    ``imported_annot`` keys of text boxes imported from this PDF and still
    embedded in it, whose originals leave the copy (``_drop_replaced``)
    before anything is written, so the Gamma version stands alone.
    """
    from .pdf_glyphs import GlyphFonts

    reader = ExportPdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    writer.append(reader)
    glyphs = GlyphFonts(writer)
    if replaced:
        _drop_replaced(writer, set(replaced))

    written = 0
    for tb in text_boxes:
        if not 1 <= tb["page"] <= len(writer.pages):
            continue
        crop, rotation = page_frame(writer.pages[tb["page"] - 1])
        annot = _text_box_annotation(writer, glyphs, tb, rotation, crop, author, resolve_ref)
        writer.add_annotation(page_number=tb["page"] - 1, annotation=annot)
        written += 1
    for group in ink:
        ink_file = group.get("ink")
        page_num = ink_file.space.page if ink_file and ink_file.space.kind == "pdf-page" else None
        if not page_num or page_num < 1 or page_num > len(writer.pages):
            continue
        crop, rotation = page_frame(writer.pages[page_num - 1])
        for annot in _ink_annotations(ink_file, rotation, crop, group.get("note") or "",
                                      author, block_id=group.get("id") or ""):
            writer.add_annotation(page_number=page_num - 1, annotation=annot)
            written += 1
    for h in highlights:
        pos = h.get("position") or {}
        page_num = pos.get("pageNumber") or (pos.get("boundingRect") or {}).get("pageNumber")
        if not page_num or page_num < 1 or page_num > len(writer.pages):
            continue
        viewer_rects = pos.get("rects") or ([pos["boundingRect"]] if pos.get("boundingRect") else [])
        viewer_rects = [r for r in viewer_rects if r and r.get("x1") is not None]
        if not viewer_rects:
            continue
        crop, rotation = page_frame(writer.pages[page_num - 1])
        pdf_rects = [_viewer_rect_to_pdf(r, rotation, crop) for r in viewer_rects]
        color = parse_css_color(h.get("color"))
        if pos.get("area"):
            annot = _square_annotation(writer, pdf_rects, color, h.get("note") or "",
                                       author, highlight_id=h.get("id") or "")
        else:
            annot = _highlight_annotation(pdf_rects, color, h.get("note") or "", author)
        writer.add_annotation(page_number=page_num - 1, annotation=annot)
        written += 1

    glyphs.finalize()
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), written
