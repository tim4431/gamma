"""The picture of a feedback thread's region (docs/dev/feedback.md "The
pictures"): the passage, area or handwriting a thread hangs on, cut out of
its PDF page as the page shows it; the region of a picture a thread hangs
on, cut out of the picture; the whole picture for a thread on a picture's
page.

``now`` is the region as the person sees it now: the page's current file
with its pictures, text boxes and handwriting stamped in
(``ink_view.page_with_handwriting``) and the page's highlights whose
rectangles were measured on that file drawn over them as annotations
(``pdf_export.page_with_ink``'s ``highlights``: a dashed one included, a
lost one or one still placed in another file not; a link region, a Gamma
navigation aid, never); on a picture, the current version cut at the
region's box with the page's regions drawn (``ai_pictures.image_region``),
whatever version the region was drawn on, since a region stays where it
was on a new version. ``made`` is the region as it was commented on: the
version the thread was opened on (held while it is open,
gamma/feedback/kept.py), its page with the pictures, text boxes and
handwriting as they are (page coordinates do not move) and the thread's
own rectangles alone, in its colour; on a picture, that version cut at the
box with the thread's own region drawn. A note on its own has no picture,
nor the whole file but a picture's.

A region is ``(file, page, box, drawn)``: ``file`` the stored file it is
cut from (``<hash><ext>``: a PDF, or the picture itself), ``page`` its
1-based PDF page (0 for a picture), ``box`` the crop (fractions of the page
or the picture as shown, top-left origin, the anchor's:
gamma/feedback/anchors.py) and ``drawn`` what is drawn on it: each
highlight by its id, colour, kind, style and rectangles, each ink group by
its content-addressed file, each picture and text box by its place and
content, the embedded originals they replace; on a picture, each region by
its id, colour, rectangle and whether it was drawn on this version. So
``key`` is the picture's content key: the same inputs give the same key, a
mark moved on that page another, a mark on another page none. The picture
is ``pdf_text.render_page``'s JPEG (the longer side ``RENDER_MAX_SIDE``, a
zoom of at most 4, quality 85), never re-encoded and never an upload:
``cached`` keeps it in data.db ``feedback_pictures`` by key, at most
``CACHE_BYTES`` in all, the oldest going first; in a work folder its file
``.gamma/feedback/img/<key>.jpg`` is the cache too (gamma/feedback/files.py).
One page's PDF is built once per ``(file, page, drawn)`` and cut for each
thread on it.
"""

import hashlib
import json
import threading
from collections import OrderedDict
from typing import NamedTuple

from .. import ai_pictures, highlights, storage
from ..blocks_store import block_to_dict, fetch_subtree, load_props
from ..db import connect_data_db, page_now
from ..documents.model import file_hash, is_image, page_document, pdf_file_hash
from ..highlights import COORDS, is_ink
from ..pdf_export import still_embedded
from ..pdf_text import RENDER_MAX_SIDE, render_page
from . import anchors

PICTURE_V = 1                      # what a picture draws: bumped when that changes, which moves every key
KINDS = ("passage", "area", "ink", "region")  # the anchors with a region (a note under one takes its kind)
CACHE_BYTES = 48 * 1024 * 1024     # data.db's pictures at most, the oldest going first
PAGE_PDFS = 4                      # the one-page PDFs kept for the next threads of their page
WHOLE = (0.0, 0.0, 1.0, 1.0)       # the box of a whole picture


class Region(NamedTuple):
    file: str        # the stored file it is cut from, <hash><ext>: a PDF, or the picture itself
    page: int        # its 1-based PDF page; 0 for a picture
    box: tuple       # the crop, fractions of the page or the picture as shown, four decimals
    drawn: tuple     # what is drawn on it (the module docstring)


_pages: OrderedDict = OrderedDict()   # (ws, page id, file, page, drawn) → one-page PDF bytes, the newest last
_pages_lock = threading.Lock()


def now(conn, page_id: str, props: dict, anchor: dict | None, *, memo: dict | None = None) -> Region | None:
    """The region of the thread anchored ``anchor`` (a stored one, or one
    ``anchors.anchor_of`` reads now for a root from before step 54) as the
    page ``page_id`` (``props`` its properties, in the pages.db ``conn``)
    shows it now: on its current file, at the anchor's place while the
    anchor was measured on that file, else at the block's place now when
    that is measured on it. On a picture, the current version at the
    region's box, or the whole picture for a thread on the page. None for a
    note alone or a PDF's file, a block lost or still placed in another
    file, handwriting on another build (ink is never placed again) and a
    page without a PDF or a picture. ``memo``, a dict shared by the calls of
    one pass, reads each page's marks once."""
    view = page_document(props)
    if not anchor or not view or not file_hash(view):
        return None
    if is_image(view):
        return _picture_now(conn, page_id, view, anchor, memo)
    current = pdf_file_hash(view)
    if not current or anchor.get("kind") not in KINDS or anchor["kind"] == "region":
        return None
    if anchor.get("doc_id") == current:
        page, box = anchor.get("page"), anchor.get("box")
    elif anchor["kind"] == "ink":
        return None
    else:
        place = _place_now(conn, page_id, anchor.get("block") or "", current)
        if place is None:
            return None
        page, box = place
    if not _is_page(page) or not _is_box(box):
        return None
    return Region(view["file"], page, _round(box), _drawn(conn, page_id, current, page, memo))


def _picture_now(conn, page_id: str, view: dict, anchor: dict, memo: dict | None) -> Region | None:
    """``now`` on a picture: a region's thread at the anchor's box, the
    page's at the whole picture; None for a note alone."""
    if anchor.get("kind") == "region":
        box = anchor.get("box")
    elif anchor.get("kind") == "file":
        box = WHOLE
    else:
        return None
    if not _is_box(box):
        return None
    return Region(view["file"], 0, _round(box), _regions(conn, page_id, view["hash"], memo))


def made(anchor: dict | None, color: str = "") -> Region | None:
    """The region of the thread anchored ``anchor`` as it was commented
    on: on the version it was opened on (``anchors.held_file``), drawing
    its rectangles alone in ``color`` (its block's, the viewer's yellow
    without one); a handwriting group is drawn as the page's ink is; on a
    picture (the anchor's kind says: a ``region``, or a ``file`` whose
    version is no PDF, whatever extension the picture is stored under), the
    region's rectangle, or the whole picture for a thread on the page. None
    for a note alone, a PDF's file and an anchor with no version or no box.
    Whether that version is still stored is the caller's to ask
    (``storage.exists``)."""
    file = anchors.held_file(anchor) if anchor else ""
    if not file:
        return None
    kind, box = anchor["kind"], anchor.get("box")
    rects = tuple(_round(r) for r in anchor.get("rects") or () if _is_box(r))
    if kind == "region" or kind == "file" and not file.endswith(".pdf"):
        if kind == "file":
            box = WHOLE
        return Region(file, 0, _round(box), (("anchor", kind, color or "", rects),)) if _is_box(box) else None
    page = anchor.get("page")
    if kind not in KINDS or kind == "file" or not _is_page(page) or not _is_box(box):
        return None
    return Region(file, page, _round(box), (("anchor", kind, color or "", rects),))


def key(region: Region) -> str:
    """The picture's content key: 16 hex digits of the SHA-256 of what
    makes it (``PICTURE_V``, the file, the page, the box, the size, what is
    drawn). A PDF goes in by its stem, so the keys of step 2 stand."""
    file = region.file[:-4] if region.file.endswith(".pdf") else region.file
    made_of = [PICTURE_V, file, region.page, list(_round(region.box)), RENDER_MAX_SIDE, region.drawn]
    text = json.dumps(made_of, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def render(ws: str, conn, page_id: str, region: Region) -> bytes | None:
    """The picture of ``region`` of the page ``page_id`` as JPEG bytes;
    None when it cannot be drawn now: its file is not readable here (a
    build being written, a version no longer stored), the page is past the
    file's end, or the file does not open (encrypted, broken, no picture).
    The next pass tries again."""
    if region.page == 0:
        image = ai_pictures.image_region(ws, region.file, region.box, _image_marks(region))
        return image[0] if image else None
    data = _page_pdf(ws, conn, page_id, region)
    if not data:
        return None
    image, _ = render_page(data, 1, RENDER_MAX_SIDE, region.box)
    return image[0] if image else None


def cached(ws: str, conn, page_id: str, region: Region) -> bytes | None:
    """``render``'s picture through data.db ``feedback_pictures``: read by
    its key, else drawn and kept (``_put``)."""
    name = key(region)
    with connect_data_db(ws) as db:
        row = db.execute("SELECT data FROM feedback_pictures WHERE key = ?", (name,)).fetchone()
    if row:
        return bytes(row[0])
    data = render(ws, conn, page_id, region)
    if data:
        _put(ws, name, data)
    return data


def _put(ws: str, name: str, data: bytes) -> None:
    """Keep the picture ``data`` under ``name``, the oldest pictures going
    first until all of them fit in ``CACHE_BYTES``; one bigger than that is
    not kept."""
    if len(data) > CACHE_BYTES:
        return
    with connect_data_db(ws) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            rows = db.execute("SELECT key, bytes FROM feedback_pictures WHERE key != ? ORDER BY at, key",
                              (name,)).fetchall()
            over = sum(size for _, size in rows) + len(data) - CACHE_BYTES
            gone = []
            for old, size in rows:
                if over <= 0:
                    break
                gone.append(old)
                over -= size
            db.executemany("DELETE FROM feedback_pictures WHERE key = ?", [(old,) for old in gone])
            db.execute("INSERT OR REPLACE INTO feedback_pictures (key, data, bytes, at) VALUES (?, ?, ?, ?)",
                       (name, data, len(data), page_now()))
            db.commit()
        except BaseException:
            db.rollback()
            raise


def _is_page(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_box(value) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))


def _round(values) -> tuple:
    return tuple(round(min(1.0, max(0.0, float(v))), 4) for v in values)


def _place_now(conn, page_id: str, block_id: str, current: str):
    """``(page, box)`` of the block ``block_id`` where it is now, when its
    place there was measured on the file ``current`` and it is not lost;
    None otherwise."""
    row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ? AND page_id = ?",
                       (block_id, page_id)).fetchone()
    pos = load_props(row[0]).get("pdf_position") if row else None
    if not isinstance(pos, dict) or pos.get("lost"):
        return None
    live = anchors.anchor_of(conn, page_id, block_id)
    if live["kind"] not in KINDS or live["doc_id"] != current:
        return None
    return live["page"], live["box"]


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fractions(pos: dict) -> tuple:
    """A position's rectangles as fractions of its page, four decimals."""
    size = highlights._size(pos)
    return tuple(_round(r[k] / size[0 if k[0] == "x" else 1] for k in COORDS)
                 for r in pos.get("rects") or () if highlights.placed(r))


def _marked(props: dict, pos, file_hash: str, page: int) -> bool:
    """Whether a highlight's mark is drawn on page ``page`` of the file
    ``file_hash``: placed there, measured on that file (a ``doc_id`` naming
    none or that one), neither lost nor drawn by the file itself."""
    return (isinstance(pos, dict) and pos.get("pageNumber") == page and not pos.get("lost")
            and pos.get("doc_id") in (None, "", file_hash) and highlights._size(pos) is not None
            and not still_embedded(props))


def _blocks(conn, page_id: str, memo: dict) -> list:
    """The page's blocks as dicts, read once per page with a ``memo``."""
    blocks = memo.get(("blocks", page_id))
    if blocks is None:
        blocks = memo[("blocks", page_id)] = [block_to_dict(row) for row in fetch_subtree(conn, page_id)]
    return blocks


def _regions(conn, page_id: str, current: str, memo: dict | None) -> tuple:
    """What is drawn on the picture of the page ``page_id`` whose current
    version is ``current``: each region (``highlights.region_box``) by its
    id, colour, rectangle and whether it was drawn on this version, sorted;
    read once per page with a ``memo``."""
    memo = {} if memo is None else memo
    found = memo.get(("regions", page_id, current))
    if found is not None:
        return found
    items = []
    for b in _blocks(conn, page_id, memo):
        props = b["properties"]
        box = highlights.region_box(props)
        if box:
            items.append(("region", b["id"], props.get("color") or "", (box,),
                          props["image_position"].get("hash") == current))
    drawn = memo[("regions", page_id, current)] = tuple(sorted(items))
    return drawn


def _image_marks(region: Region) -> list[tuple]:
    """The marks ``region.drawn`` names on a picture, as
    ``ai_pictures.image_region`` draws them: ``(box, color, strong)``, the
    thread's own rectangle (``made``) the strong one."""
    marks = []
    for item in region.drawn:
        if item[0] == "region":
            marks += [(rect, item[2], False) for rect in item[3]]
        elif item[0] == "anchor":
            marks += [(rect, item[2], True) for rect in item[3]]
    return marks


def _drawn(conn, page_id: str, file_hash: str, page: int, memo: dict | None) -> tuple:
    """What is drawn on page ``page`` of the file ``file_hash`` of the page
    ``page_id`` (the module docstring), sorted; read once per file and page
    with a ``memo``."""
    from ..routers.export import _collect_pictures, _collect_text_boxes  # local: ink_view's pickers

    memo = {} if memo is None else memo
    found = memo.get(("drawn", page_id, file_hash, page))
    if found is not None:
        return found
    blocks = _blocks(conn, page_id, memo)
    items = []
    for b in blocks:
        props = b["properties"]
        pos = props.get("pdf_position")
        if b["kind"] == "highlight" and _marked(props, pos, file_hash, page):
            rects = _fractions(pos)
            if rects:
                items.append(("hl", b["id"], props.get("color") or "", bool(pos.get("area")),
                              props.get("style") or "highlight", rects))
        elif (is_ink(props) and props.get("ink_url") and not still_embedded(props)
              and isinstance(pos, dict) and pos.get("pageNumber") == page):
            items.append(("ink", props["ink_url"]))
    boxes, replaced = _collect_text_boxes(blocks, page_id)
    pictures, more = _collect_pictures(blocks, page_id, lambda name: True)
    items += [("pic", p["picture"]["url"], _canonical(p["picture"])) for p in pictures if p["page"] == page]
    items += [("box", hashlib.sha1(tb["content"].encode("utf-8")).hexdigest(), _canonical(tb["box"]))
              for tb in boxes if tb["page"] == page]
    keys = tuple(sorted(k for k in replaced | more if k.startswith(f"{page}:")))
    if keys:
        items.append(("replaced", keys))
    drawn = memo[("drawn", page_id, file_hash, page)] = tuple(sorted(items))
    return drawn


def _marks_of(region: Region) -> list[dict]:
    """The highlight marks ``region.drawn`` names, as ``annotate_pdf``'s
    marks in a frame of the page's fractions: what is drawn is what the key
    says."""
    marks = []
    for item in region.drawn:
        if item[0] == "hl":
            _, block_id, color, area, style, rects = item
        elif item[0] == "anchor" and item[1] != "ink":
            _, kind, color, rects = item
            block_id, area, style = "", kind == "area", "highlight"
        else:
            continue
        marks.append({"position": {"pageNumber": region.page, "width": 1, "height": 1, "area": area,
                                   "rects": [dict(zip(COORDS, r)) for r in rects]},
                      "color": color or None, "style": style, "id": block_id})
    return marks


def _page_pdf(ws: str, conn, page_id: str, region: Region) -> bytes | None:
    """The region's page as one-page PDF bytes with everything drawn on it
    (``ink_view.page_with_handwriting``, the marks of ``_marks_of``), the
    last few kept for the next threads of their page; None when it cannot
    be built."""
    from ..ink_view import page_with_handwriting  # local: ink_view reaches the page readers, which import comments

    cache_key = (ws, page_id, region.file, region.page, region.drawn)
    with _pages_lock:
        if cache_key in _pages:
            _pages.move_to_end(cache_key)
            return _pages[cache_key]
    path = storage.find_upload_file(region.file, ws)
    if path is None:
        return None
    data, _pages_in_file = page_with_handwriting(ws, conn, page_id, region.page, path=path, marks=_marks_of(region))
    if not data:
        return None
    with _pages_lock:
        _pages[cache_key] = data
        while len(_pages) > PAGE_PDFS:
            _pages.popitem(last=False)
    return data
