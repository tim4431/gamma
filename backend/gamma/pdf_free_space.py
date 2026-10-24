"""Where a PDF page is empty, and the nearest free spot for a box there: what
the annotated PDF's notes take when they go on the page itself
(``pdf_notes``, notes ``page``).

Empty space comes from the page's own content: MuPDF logs a bounding box
for everything the page draws (text runs, paths, images, shadings, the
annotations already written into it), which ``occupancy`` rasterizes into
a coarse grid (display space, top-left origin, /Rotate applied, one cell
``CELL`` pt: the frame the viewer stores highlight rects in). A
summed-area table over it answers "is this rectangle free?" in O(1), so
``place`` scores a few thousand candidate spots near a highlight and keeps
the cheapest. The boxes placed so far are kept in the ``Space`` too, so they
never overlap one another.

The log is taken through a bbox device of the module's own: what
``page.get_bboxlog()`` logs, except that a shading is bounded by the clip it
is painted through (``get_bboxlog`` logs the whole plane), so a
gradient-filled box counts as occupied. PyMuPDF is not thread-safe: the
read holds ``pdf_text._lock``, one short call per page. A file MuPDF cannot
open, or one that needs a password, reads as a page whose usual text block
is busy, so the boxes go in its margins.
"""

import math

from . import pdf_text
from .logbuf import log

CELL = 3.0              # the grid's resolution, pt
PAGE_MARGIN = 8.0       # never place a box closer than this to the page's edge
CLEARANCE = 2.5         # empty space to keep around a placed box
X_STEP = 9.0            # the grid of candidate spots
Y_STEP = 8.0
Y_RANGE = 320.0         # how far above and below the highlight to look


class Space:
    """Occupancy of one page in display space, plus the boxes placed so far
    (``placed``, each with its clearance)."""

    def __init__(self, width: float, height: float):
        self.w, self.h = width, height
        self.cols = max(1, int(math.ceil(width / CELL)))
        self.rows = max(1, int(math.ceil(height / CELL)))
        self.grid = bytearray(self.cols * self.rows)
        self.sat = None
        self.placed = []

    def mark(self, rect):
        x0, y0, x1, y1 = rect
        c0 = max(0, int(x0 / CELL))
        c1 = min(self.cols, int(math.ceil(x1 / CELL)))
        r0 = max(0, int(y0 / CELL))
        r1 = min(self.rows, int(math.ceil(y1 / CELL)))
        for r in range(r0, r1):
            base = r * self.cols
            for c in range(c0, c1):
                self.grid[base + c] = 1

    def freeze(self):
        """The summed-area table, so rectangle queries are O(1)."""
        cols, rows = self.cols, self.rows
        sat = [0] * ((cols + 1) * (rows + 1))
        for r in range(rows):
            row_sum = 0
            base, above, cur = r * cols, r * (cols + 1), (r + 1) * (cols + 1)
            for c in range(cols):
                row_sum += self.grid[base + c]
                sat[cur + c + 1] = sat[above + c + 1] + row_sum
        self.sat = sat

    def occupied(self, x0, y0, x1, y1) -> int:
        """How many occupied cells the rectangle covers (the page's content only)."""
        cols = self.cols
        c0 = max(0, min(cols, int(x0 / CELL)))
        c1 = max(0, min(cols, int(math.ceil(x1 / CELL))))
        r0 = max(0, min(self.rows, int(y0 / CELL)))
        r1 = max(0, min(self.rows, int(math.ceil(y1 / CELL))))
        if c1 <= c0 or r1 <= r0:
            return 0
        sat, stride = self.sat, cols + 1
        return (sat[r1 * stride + c1] - sat[r0 * stride + c1]
                - sat[r1 * stride + c0] + sat[r0 * stride + c0])

    def hits_placed(self, x0, y0, x1, y1) -> bool:
        return any(x0 < px1 and px0 < x1 and y0 < py1 and py0 < y1 for px0, py0, px1, py1 in self.placed)

    def take(self, x0, y0, x1, y1):
        """A box placed: later ones keep clear of it."""
        self.placed.append((x0 - CLEARANCE, y0 - CLEARANCE, x1 + CLEARANCE, y1 + CLEARANCE))


def _cost(box, anchor):
    bx0, by0, bx1, by1 = box
    ax0, ay0, ax1, _ay1 = anchor
    gap_x = 0.0 if bx0 < ax1 and ax0 < bx1 else min(abs(bx0 - ax1), abs(ax0 - bx1))
    return gap_x + 1.3 * abs(by0 - ay0)


def place(space: Space, anchor, box_w: float, box_h: float, width_penalty: float = 0.0):
    """The cheapest spot for a ``box_w`` × ``box_h`` box near ``anchor``
    (x0, y0, x1, y1): ``(score, x, y, free)``, or None when the box is
    larger than the page. Falls back to the least-covered spot when none is
    free, so a box is never dropped on a densely typeset page."""
    x_lo, x_hi = PAGE_MARGIN, space.w - PAGE_MARGIN - box_w
    if x_hi < x_lo or box_h > space.h - 2 * PAGE_MARGIN:
        return None
    xs = [x_lo + i * X_STEP for i in range(int((x_hi - x_lo) / X_STEP) + 1)] + [x_hi]
    ax_mid = (anchor[0] + anchor[2]) / 2
    xs.sort(key=lambda x: abs(x + box_w / 2 - ax_mid))

    best = None          # (score, x, y, free)
    steps = int(Y_RANGE / Y_STEP)
    for i in range(2 * steps + 1):
        dy = (i + 1) // 2 * Y_STEP * (1 if i % 2 else -1)
        y = min(max(anchor[1] + dy, PAGE_MARGIN), space.h - PAGE_MARGIN - box_h)
        for x in xs:
            box = (x, y, x + box_w, y + box_h)
            if space.hits_placed(*box):
                continue
            covered = space.occupied(x - CLEARANCE, y - CLEARANCE, x + box_w + CLEARANCE, y + box_h + CLEARANCE)
            score = _cost(box, anchor) + width_penalty + covered * 6.0
            if best is None or score < best[0]:
                best = (score, x, y, covered == 0)
        if best and best[3] and best[0] - width_penalty < 24:
            break        # snug against the highlight already: stop looking
    return best


# --- reading the page -------------------------------------------------------------

_BoundsLog = None


def _bounds_log_class():
    """The device class, built on first use: PyMuPDF is imported lazily."""
    global _BoundsLog
    if _BoundsLog is not None:
        return _BoundsLog
    from pymupdf import mupdf

    def rect(r):
        return (r.x0, r.y0, r.x1, r.y1)

    class BoundsLog(mupdf.FzDevice2):
        """What ``Page.get_bboxlog`` logs, with one difference: a shading is
        bounded by the clip in force when it is painted. A shading has no
        extent of its own (an axial or radial gradient fills the plane):
        ``sh`` paints the current clip, and a path filled with a shading
        pattern is clipped to the path first, so ``get_bboxlog``'s entry for
        a gradient-filled box is the whole plane and the box would count as
        free. MuPDF's device layer keeps the clip stack whichever callbacks
        are hooked, so only the drawing ones are."""

        def __init__(self, out):
            super().__init__()
            self.out = out
            for name in ("fill_path", "stroke_path", "fill_text", "stroke_text",
                         "ignore_text", "fill_shade", "fill_image", "fill_image_mask"):
                getattr(self, "use_virtual_" + name)()

        def fill_path(self, ctx, path, even_odd, ctm, *_):
            self.out.append(("fill-path", rect(mupdf.ll_fz_bound_path(path, None, ctm))))

        def stroke_path(self, ctx, path, stroke, ctm, *_):
            self.out.append(("stroke-path", rect(mupdf.ll_fz_bound_path(path, stroke, ctm))))

        def fill_text(self, ctx, text, ctm, *_):
            self.out.append(("fill-text", rect(mupdf.ll_fz_bound_text(text, None, ctm))))

        def stroke_text(self, ctx, text, stroke, ctm, *_):
            self.out.append(("stroke-text", rect(mupdf.ll_fz_bound_text(text, stroke, ctm))))

        def ignore_text(self, ctx, text, ctm, *_):
            self.out.append(("ignore-text", rect(mupdf.ll_fz_bound_text(text, None, ctm))))

        def fill_image(self, ctx, image, ctm, *_):
            self.out.append(("fill-image", rect(mupdf.ll_fz_transform_rect(mupdf.fz_unit_rect, ctm))))

        def fill_image_mask(self, ctx, image, ctm, *_):
            self.out.append(("fill-imgmask", rect(mupdf.ll_fz_transform_rect(mupdf.fz_unit_rect, ctm))))

        def fill_shade(self, ctx, shade, ctm, *_):
            bound = mupdf.ll_fz_bound_shade(shade, ctm)
            # The scissor wrapper stays in a name until the intersection is
            # taken: .internal() points into it, and a temporary is freed at
            # once (on Linux that read garbage; Windows happened to keep it).
            scissor = mupdf.fz_device_current_scissor(self)
            self.out.append(("fill-shade", rect(mupdf.ll_fz_intersect_rect(bound, scissor.internal()))))

    _BoundsLog = BoundsLog
    return BoundsLog


def bbox_log(doc, index: int) -> list:
    """The drawing log for page ``index``, ``[(kind, (x0, y0, x1, y1))]`` in
    the unrotated page's space, in points: the page run through the module's
    own device (``_bounds_log_class``) the way ``get_bboxlog`` runs it,
    ``pdf_text.unrotated`` for the run (a run under PyMuPDF's derotation
    matrix instead would be off: that matrix is built from the raw crop
    box's size, wrong on a crop box larger than the media box, which MuPDF
    clips, and on a /UserUnit page). MuPDF scales the log by the page's
    /UserUnit as it does page.rect, and the view box ``page_frame`` finds,
    which the step back to user space is spelled out against, is in points
    (``pdf_text.user_unit``), so the run's matrix takes the unit out. ``[]``
    without a document or when the page will not load. The page object lives
    only inside this call: PyMuPDF frees it with the reference, so there is
    nothing to close."""
    if doc is None:
        return []
    try:
        from pymupdf import mupdf
        page = doc[index]
        scale = 1 / pdf_text.user_unit(page)
        out = []
        dev = _bounds_log_class()(out)
        with pdf_text.unrotated(page):
            mupdf.fz_run_page(page.this, dev, mupdf.FzMatrix(scale, 0, 0, scale, 0, 0), mupdf.FzCookie())
            mupdf.fz_close_device(dev)
        return out
    except Exception:
        return []


def page_space(bounds, view, to_display, disp_w: float, disp_h: float) -> Space:
    """Everything the page already draws, as a display-space occupancy grid.

    ``bounds`` is the page's bbox log: one entry per drawing operation, form
    XObjects flattened into the page's space, and the annotations'
    appearances among them. So what ``annotate_pdf`` wrote before the notes
    counts as occupied too: the text boxes with either export switch, the
    highlights and ink when the highlight layer is on. Invisible text (an
    OCR layer) draws nothing visible and is skipped. A shading's entry is the
    clip it was painted through (``bbox_log``), so a gradient-filled box
    counts as occupied.

    The log's space is the unrotated page's: origin at the view box's
    top-left corner, y down. The step back to user space is spelled out
    against the view box ``page_frame`` found rather than taken from
    ``page.transformation_matrix``, which on a rotated page PyMuPDF replaces
    with a bare flip that loses the crop box's offset. From user space
    ``to_display`` takes over, as it does for the highlight rects."""
    space = Space(disp_w, disp_h)
    area = disp_w * disp_h
    boxes = []
    cx0, _cy0, _cx1, cy1 = view
    for kind, (x0, y0, x1, y1) in bounds:
        if kind == "ignore-text":
            continue
        ax, ay = to_display(x0 + cx0, cy1 - y0)
        bx, by = to_display(x1 + cx0, cy1 - y1)
        box = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
        if (box[2] - box[0]) * (box[3] - box[1]) > 0.6 * area:
            continue        # a page-sized background fill, not content
        boxes.append(box)
    if not boxes:
        # No drawing read (MuPDF failed, or a scanned page): the usual text
        # block counts as busy, so the boxes still land in the margins.
        boxes = [(disp_w * 0.07, disp_h * 0.05, disp_w * 0.93, disp_h * 0.95)]
    for box in boxes:
        space.mark(box)
    space.freeze()
    return space


def occupancy(pdf_bytes: bytes, pages) -> dict:
    """``{index: Space}`` for ``pages``, ``{index: (view, to_display,
    disp_w, disp_h)}``: each page's frame as ``pdf_notes`` maps it. One
    open of the document under ``pdf_text._lock``, each page one short call;
    the grids are built after the lock is let go."""
    logs = {}
    with pdf_text._lock:
        try:
            doc = pdf_text._mupdf_open(pdf_bytes)
        except Exception as e:
            log.warning(f"[pdf-notes] PyMuPDF open failed ({e}{pdf_text._mupdf_warnings()}); "
                        "placing notes in the margins")
            doc = None
        try:
            logs = {index: bbox_log(doc, index) for index in pages}
        finally:
            if doc is not None:
                doc.close()
    return {index: page_space(logs[index], *frame) for index, frame in pages.items()}
