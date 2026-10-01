"""Handwriting as a picture for a vision model (docs/dev/ai_tools.md
"view_ink"): a group's strokes drawn where they were written — on their PDF
page or on their sheet of paper — rasterized by pdfium like view_pdf_page's
page, whole or cropped to the handwriting.

Nothing is drawn here. A sheet goes through the notebook export's one-page
PDF (``notebook.notebook_pdf``: the paper painted, its text boxes typeset,
the strokes as vectors);
ink on a PDF page is that page alone with the strokes as the annotated
export's ``/Ink`` annotations (``pdf_export.page_with_ink``), which pdfium
draws when it renders. ``pdf_text.render_page`` rasterizes either.
"""

from . import ink as inkmod
from .blocks_store import block_to_dict, fetch_subtree, page_attachment
from .db import ws_uploads_dir
from .logbuf import log
from .notebook import is_sheet, normalize_paper, notebook_pdf, sheet_text_boxes, sheets_of
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
    if not boxes or width <= 0 or height <= 0:
        return None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
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


def picture(ws: str, conn, block_id: str, page_id: str, whole: bool = False) -> dict:
    """What view_ink shows of ``block_id`` — a handwriting group or a sheet
    of paper — on the page ``page_id``: ``{image, strokes, whole, pdf_page,
    bare}`` (``image`` is render_page's ``(bytes, media type, width,
    height)``; ``bare``: the strokes are drawn on blank paper because the PDF
    page could not be copied), or ``{error}`` in words for the model.
    ``whole`` shows the whole PDF page or sheet with all its handwriting
    instead of this group, cropped."""
    blocks = [block_to_dict(row) for row in fetch_subtree(conn, page_id)]
    by_id = {b["id"]: b for b in blocks}
    block = by_id.get(block_id)
    if block is None:
        return {"error": "error: no such block on that page"}
    props = block["properties"]
    uploads = ws_uploads_dir(ws)

    def load(b):
        url = b["properties"].get("ink_url")
        return inkmod.read_upload(uploads, url) if url else None

    sheets = sheets_of(blocks, page_id)
    if is_sheet(props):
        sheet = next((s for s in sheets if s["id"] == block_id), None)
        if sheet is None:
            return {"error": "error: that page of paper could not be read"}
        inks = [ink for b in sheet["blocks"] if (ink := load(b)) is not None]
        return _done(_render(notebook_pdf([(sheet["paper"], sheet_text_boxes(sheet["blocks"]), inks)]), None),
                     inks, whole=True)
    if not props.get("ink_url"):
        return {"error": ('error: that block holds no handwriting — pass the id of a handwriting '
                          'block (read_block labels it "handwriting on …") or of a page of paper')}
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
        boxes = sheet_text_boxes(sheet["blocks"]) if sheet else []
        return _done(_render(notebook_pdf([(paper, boxes, inks)]), box), inks, whole=whole)

    from .ai_context import pdf_path
    from .pdf_export import page_with_ink, still_embedded

    page_no = ink.space.page
    attachment = page_attachment(by_id[page_id]["properties"])
    path = pdf_path(ws, attachment["id"]) if attachment else None
    # What is added to the page: ink still embedded in the PDF is drawn by
    # the file itself.
    if whole:
        inks = [i for b in blocks
                if b["properties"].get("ink_url") and not still_embedded(b["properties"])
                and (i := load(b)) is not None and i.space.kind == "pdf-page" and i.space.page == page_no]
    else:
        inks = [] if still_embedded(props) else [ink]
    drawn = inks or [ink]
    box = None if whole else crop_box([ink], ink.space.width, ink.space.height)
    data = b""
    if path:
        try:
            data, pages = page_with_ink(path, page_no, inks)
            if not data:
                return {"error": f"error: the handwriting is on PDF page {page_no}, but the PDF has {pages} pages"}
        except Exception as e:  # an encrypted or broken file: the strokes alone still read
            log.warning(f"[ink-view] could not copy PDF page {page_no}: {e}")
    if data:
        return _done(_render(data, box), drawn, pdf_page=page_no, whole=whole)
    paper = normalize_paper({"width": ink.space.width, "height": ink.space.height})
    return _done(_render(notebook_pdf([(paper, [], drawn)]), box), drawn, pdf_page=page_no, whole=whole, bare=True)


def _render(pdf: bytes, box):
    image, _ = render_page(pdf, 1, RENDER_MAX_SIDE, box)
    return image


def _done(image, inks, *, whole=False, pdf_page=0, bare=False) -> dict:
    if image is None:
        return {"error": "error: the handwriting could not be drawn"}
    return {"image": image, "strokes": sum(len(i.strokes) for i in inks), "whole": whole,
            "pdf_page": pdf_page, "bare": bare}
