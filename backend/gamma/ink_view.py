"""Handwriting as a picture for a vision model (docs/dev/ai_tools.md
"view_ink"): a group's strokes drawn where they were written — on their PDF
page or on their sheet of paper — rasterized by MuPDF like view_pdf_page's
page, whole or cropped to the handwriting.

Nothing is drawn here. A sheet goes through the notebook export's one-page
PDF (``notebook.notebook_pdf``: the paper painted, its pictures drawn and
its text boxes typeset, the strokes as vectors); ink on a PDF page is that
page alone with its pictures and text boxes drawn into its content as the
annotated export places them, and the strokes over them as the same
vectors (``pdf_export.page_with_ink``: baked widths, tapers, dashes, the
pencil's shades and grain and the calligraphy nib as drawn, highlighters
multiplied onto the text under them). So a picture of a page is the page
as the user sees it. ``pdf_text.render_page`` rasterizes either.
"""

from . import ink as inkmod
from . import storage
from .ai_pictures import page_pdf_path
from .blocks_store import block_to_dict, fetch_subtree
from .documents.model import file_hash, is_pdf, page_document
from .highlights import is_ink
from .logbuf import log
from .notebook import is_sheet, normalize_paper, notebook_pdf, sheet_pictures, sheet_text_boxes, sheets_of
from .pdf_text import RENDER_MAX_SIDE, render_page

# A cropped picture's margin around the handwriting (points, or a tenth of
# its larger side) and the least it shows of each side of the page, so one
# scribbled word is still seen with what it was written next to.
CROP_PAD = 18.0
CROP_MIN_SIDE = 144.0


def crop_box(inks, width: float, height: float):
    """``(x0, y0, x1, y1)`` as fractions of a ``width`` × ``height`` page
    around every stroke of ``inks`` — padded, at least ``CROP_MIN_SIDE`` a
    side, inside the page; None when nothing is drawn."""
    boxes = [box for ink in inks if (box := inkmod.bounding_box(ink))]
    if not boxes:
        return None
    return grow_box(min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                    max(b[3] for b in boxes), width, height)


def grow_box(x0: float, y0: float, x1: float, y1: float, width: float, height: float):
    """``crop_box``'s rule for the box ``(x0, y0, x1, y1)`` (in a ``width``
    × ``height`` frame, top-left origin): padded, at least
    ``CROP_MIN_SIDE`` a side, inside the page, as fractions; None for no
    frame. What a feedback thread's ink anchor is cropped by
    (gamma/feedback/anchors.py), so its picture is view_ink's."""
    if width <= 0 or height <= 0:
        return None
    pad = max(CROP_PAD, 0.1 * max(x1 - x0, y1 - y0))

    def side(lo, hi, extent):
        lo, hi = lo - pad, hi + pad
        short = min(CROP_MIN_SIDE, extent) - (hi - lo)
        if short > 0:
            lo, hi = lo - short / 2, hi + short / 2
        if lo < 0:
            lo, hi = 0.0, min(extent, hi - lo)
        if hi > extent:
            lo, hi = max(0.0, lo - (hi - extent)), extent
        return lo / extent, hi / extent

    (fx0, fx1), (fy0, fy1) = side(x0, x1, width), side(y0, y1, height)
    return (fx0, fy0, fx1, fy1)


def _sheet_pdf(paper, sheet, inks, stored) -> bytes:
    """One sheet as ``notebook_pdf`` draws it: its paper, pictures, text
    boxes and ``inks``; blank paper with the strokes alone when ``sheet``
    is None."""
    blocks = sheet["blocks"] if sheet else []
    return notebook_pdf([(paper, sheet_text_boxes(blocks), inks)], pictures=[sheet_pictures(blocks)],
                        stored=stored)


def _placed(conn, blocks: list[dict], page_id: str, stored) -> dict:
    """What ``pdf_export.page_with_ink`` draws under the handwriting of the
    page ``page_id``'s PDF: its pictures and text boxes, with the originals
    they replace, picked as the annotated export picks them
    (routers/export.py ``_collect_pictures``, ``_collect_text_boxes``)."""
    from .markdown_export import block_ref_resolver
    from .routers.export import _collect_pictures, _collect_text_boxes

    boxes, replaced = _collect_text_boxes(blocks, page_id)
    pictures, more = _collect_pictures(blocks, page_id, stored)
    return {"pictures": pictures, "text_boxes": boxes, "replaced": replaced | more, "stored": stored,
            "resolve_ref": block_ref_resolver(conn)}


def picture(ws: str, conn, block_id: str, page_id: str, whole: bool = False, ink=None) -> dict:
    """What view_ink shows of ``block_id`` — a handwriting group or a sheet
    of paper — on the page ``page_id``: ``{image, strokes, whole, pdf_page,
    bare}`` (``image`` is render_page's ``(bytes, media type, width,
    height)``; ``bare``: the strokes are drawn on blank paper because the PDF
    page could not be copied), or ``{error}`` in words for the model.
    ``whole`` shows the whole PDF page or sheet with all its handwriting
    instead of this group, cropped. ``ink``, an InkFile, is drawn in place
    of the block's stored file — the strokes a user selected for the chat
    (ai_context.request_ink_selections), saved or not, so the block need
    not hold a file nor be saved yet: cropped to them, on PDF page
    ``ink.space.page`` of the page's PDF, or on the sheet holding the block,
    else on blank paper of the space's size, never ``whole``."""
    blocks = [block_to_dict(row) for row in fetch_subtree(conn, page_id)]
    by_id = {b["id"]: b for b in blocks}
    block = by_id.get(block_id)
    inline = ink is not None
    if page_id not in by_id or (block is None and not inline):
        return {"error": "error: no such block on that page"}
    props = block["properties"] if block else {}
    stored = storage.files(ws)

    def load(b):
        url = b["properties"].get("ink_url")
        return inkmod.read_upload(stored, url) if url else None

    sheets = sheets_of(blocks, page_id)
    if inline:
        whole = False
    elif is_sheet(props):
        sheet = next((s for s in sheets if s["id"] == block_id), None)
        if sheet is None:
            return {"error": "error: that page of paper could not be read"}
        inks = [ink for b in sheet["blocks"] if (ink := load(b)) is not None]
        return _done(_render(_sheet_pdf(sheet["paper"], sheet, inks, stored), None), inks, whole=True)
    elif not is_ink(props):
        return {"error": ('error: that block holds no handwriting — pass the id of a handwriting '
                          'block (read_block labels it "handwriting on …") or of a page of paper')}
    else:
        ink = load(block)
        if ink is None:
            return {"error": "error: the handwriting's stroke file is missing or unreadable"}

    if ink.space.kind == "canvas":
        # On a sheet: the nearest one holding the group (sheets_of), else a
        # blank sheet the size the strokes were drawn on.
        sheet = next((s for s in sheets if any(b["id"] == block_id for b in s["blocks"])), None)
        paper = sheet["paper"] if sheet else normalize_paper(
            {"width": ink.space.width, "height": ink.space.height})
        inks = ([i for b in sheet["blocks"] if (i := load(b)) is not None]
                if whole and sheet else [ink])
        box = None if whole else crop_box([ink], paper["width"], paper["height"])
        return _done(_render(_sheet_pdf(paper, sheet, inks, stored), box), inks, whole=whole)

    from .ai_context import pdf_path
    from .pdf_export import page_with_ink, still_embedded

    page_no = ink.space.page
    view = page_document(by_id[page_id]["properties"])
    path = pdf_path(ws, file_hash(view)) if is_pdf(view) else None
    # What is added to the page: ink still embedded in the PDF is drawn by
    # the file itself (a selection's strokes are drawn all the same).
    if whole:
        inks = [i for b in blocks
                if is_ink(b["properties"]) and not still_embedded(b["properties"])
                and (i := load(b)) is not None and i.space.kind == "pdf-page" and i.space.page == page_no]
    else:
        inks = [] if still_embedded(props) and not inline else [ink]
    drawn = inks or [ink]
    box = None if whole else crop_box([ink], ink.space.width, ink.space.height)
    data = b""
    if path:
        try:
            data, pages = page_with_ink(path, page_no, inks, **_placed(conn, blocks, page_id, stored))
            if not data:
                return {"error": f"error: the handwriting is on PDF page {page_no}, but the PDF has {pages} pages"}
        except Exception as e:  # an encrypted or broken file: the strokes alone still read
            log.warning(f"[ink-view] could not copy PDF page {page_no}: {e}")
    if data:
        return _done(_render(data, box), drawn, pdf_page=page_no, whole=whole)
    # Blank paper the PDF page's own size, which the crop was taken on: a
    # sheet's 2000 pt cap would leave a poster's lower half off the paper.
    paper = {**normalize_paper(None), "width": ink.space.width, "height": ink.space.height}
    return _done(_render(notebook_pdf([(paper, [], drawn)]), box), drawn, pdf_page=page_no, whole=whole, bare=True)


def _render(pdf: bytes, box):
    image, _ = render_page(pdf, 1, RENDER_MAX_SIDE, box)
    return image


def page_with_handwriting(ws: str, conn, page_id: str, page_no: int, path=None, marks=None):
    """PDF page ``page_no`` of the page ``page_id`` with its pictures and
    text boxes drawn on it and every handwriting group over them, as
    one-page PDF bytes (``pdf_export.page_with_ink``) — what a picture of
    "the page as the user sees it" is rendered from — or ``(b"", pages)``
    when the page has no such PDF page; ``(None, 0)`` for a page without a
    PDF file on this server (``path`` is that file when the caller has it
    already: another version of the page's PDF draws the same things, page
    coordinates being the same). Ink the PDF still embeds is drawn by the
    file itself and never added twice; a box or picture it embeds is drawn
    as Gamma has it, in place of the original, as the annotated export
    writes it. ``marks``, highlight marks as ``annotate_pdf`` takes them,
    are drawn over it all (a feedback thread's region picture,
    gamma/feedback/pictures.py, picks them); none by default."""
    from .pdf_export import page_with_ink, still_embedded

    path = path or page_pdf_path(ws, conn, page_id)
    if not path:
        return None, 0
    blocks = [block_to_dict(row) for row in fetch_subtree(conn, page_id)]
    stored = storage.files(ws)
    inks = []
    for b in blocks:
        url = b["properties"].get("ink_url")
        if not url or still_embedded(b["properties"]):
            continue
        ink = inkmod.read_upload(stored, url)
        if ink is not None and ink.space.kind == "pdf-page" and ink.space.page == page_no:
            inks.append(ink)
    try:
        return page_with_ink(path, page_no, inks, highlights=marks or (), **_placed(conn, blocks, page_id, stored))
    except Exception as e:  # an encrypted or broken file: no handwriting, the page itself still renders
        log.warning(f"[ink-view] could not copy PDF page {page_no} of {page_id}: {e}")
        return b"", 0


def _done(image, inks, *, whole=False, pdf_page=0, bare=False) -> dict:
    if image is None:
        return {"error": "error: the handwriting could not be drawn"}
    return {"image": image, "strokes": sum(len(i.strokes) for i in inks), "whole": whole,
            "pdf_page": pdf_page, "bare": bare}
