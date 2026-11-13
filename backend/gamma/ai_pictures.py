"""Pictures in the AI chat (docs/dev/ai.md "Pictures"): one shape for every
picture a message carries, one size for all of them, one budget, and the
label lines that tell the model what each picture is.

A picture reaches the model as a ``(media_type, base64)`` part, the shape
the wires take. Here each one is a dict around that part::

    {"part": (media_type, b64), "label": "Pasted image, 800×600 px …",
     "group": "user" | "selection" | "ink" | "area" | "history",
     "width": W, "height": H, "page_id"?: …}

Where they come from:

- The request's ``images`` (and the ``images`` of its history's user turns):
  a stored picture ``{"kind": "pasted"|"file", "url": "/api/uploads/<hash>.jpg",
  "width", "height", "name"?}`` — what ``POST /api/ai/pictures`` stored — or
  a region of a PDF page, or of the picture a page shows, the server renders
  on demand ``{"kind": "area"|"view", "page_id", "page", "box": [x0, y0, x1, y1]
  (page fractions), "ink"?: true}``. An older client's data URL string is
  still read (``data_url_picture``), never stored.
- The context: pictures of selected passages whose text is unreliable,
  of area highlights in the notes, of attached handwriting blocks
  (gamma/ai_context.py builds them with :func:`picture`).

Every picture goes through :func:`normalize` on its way in: its longer side
at most ``RENDER_MAX_SIDE`` px (past that every provider downscales anyway),
JPEG unless it is small or translucent. That is what makes a per-picture
token cost (``Protocol.picture_tokens``) a fair bound, and what keeps a 6 MB
pasted PNG from being refused upstream.

The budget (``max_pictures`` on the request, Settings → AI → Chat →
"Pictures per message", default :data:`DEFAULT_BUDGET`) is one number for
all of a message's pictures, filled in :data:`GROUP_ORDER`: what the user
attached first, then the selection crops, then attached handwriting, then
the area highlights of the context pages. The newest pictures of earlier
turns stay in the conversation under the same budget (:func:`history_pictures`);
older ones are named in a line and not sent again.

:func:`picture_at` reads any picture back from the URL the chat shows it
by, for the chat export (gamma/chat_export.py).
"""

import base64
import io
import json
import math
import re
import warnings
from urllib.parse import parse_qs, unquote, urlsplit

from PIL import Image, ImageDraw, ImageOps

from . import colors
from .blocks_store import page_root_id
from .documents.formats import IMAGE_EXTENSIONS, IMAGE_MEDIA_TYPES
from .documents.model import is_image, page_document, pdf_file_hash
from .logbuf import log
from .pdf_image import svg_pdf, svg_png
from .pdf_text import RENDER_MAX_SIDE, image_part, page_raster, render_page
from .storage import UPLOAD_REF_RE, find_upload_file, store_file

DEFAULT_BUDGET = 12   # pictures per message (and newest earlier pictures kept)
MAX_BUDGET = 64
JPEG_QUALITY = 85
# A picture that stays PNG when it has no translucent pixel: small crisp
# things — icons, a formula, a little chart — where JPEG's blur shows.
PNG_MAX_PIXELS = 600_000
STORED_MAX_BYTES = 25_000_000  # the most a stored picture is read back for the model
GROUP_ORDER = ("user", "selection", "ink", "area")
# Only a region on a page the user is pointing at: boxes are page fractions.
REGION_KINDS = frozenset({"area", "view"})
# A region cut out of a picture (``image_region``): the crop is at least
# this many of the picture's own pixels a side (the viewer's smallest
# region is 6 screen pixels, which may be one of a figure's), and the
# picture is zoomed at most this much on its way to RENDER_MAX_SIDE.
REGION_MIN_PX = 48
IMAGE_MAX_ZOOM = 4.0
# The wash under a region's outline, as a share of its colour's alpha:
# the viewer's ``color-mix(in srgb, <color> 25%, transparent)``.
REGION_FILL_SHARE = 0.25
REGION_LINE_PX = 2
REGION_STRONG_LINE_PX = 3

_DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpeg|jpg|gif|webp));base64,([A-Za-z0-9+/=]+)$")
# The routes that draw a chat picture again from its parameters.
_DRAWN_RE = re.compile(r"^/api/ai/(page-image|ink-image|selection-crop)/([^/]+)$")


# --- one size for every picture -----------------------------------------------------

def sniff(data: bytes) -> str:
    """The media type of an image file from its first bytes, "" for none."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def chat_formats() -> tuple[str, ...]:
    """The formats a chat picture is opened as: every one Pillow reads but
    EPS, whose reader runs Ghostscript on the file, a program outside Pillow
    that no upload should reach."""
    Image.init()
    return tuple(f for f in Image.OPEN if f != "EPS")


def normalize(data: bytes, media_type: str = "") -> tuple[bytes, str, int, int] | None:
    """``(bytes, media_type, width, height)`` of ``data`` as the model gets
    it and as a chat picture is stored: the longer side at most
    ``RENDER_MAX_SIDE`` px, JPEG at :data:`JPEG_QUALITY` unless the picture
    is translucent somewhere or small (:data:`PNG_MAX_PIXELS`), then PNG.
    An animation keeps its first frame; EXIF orientation is applied. None
    for bytes that are no picture. Any format Pillow reads in its own code is
    taken (a TIFF, a PSD); PostScript is not (``chat_formats``), so an EPS
    never reaches Ghostscript."""
    media_type = "image/jpeg" if media_type == "image/jpg" else (media_type or sniff(data))
    try:
        with Image.open(io.BytesIO(data), formats=chat_formats()) as opened:
            opened.load()
            source = opened.format
            image = ImageOps.exif_transpose(opened) or opened
            width, height = image.size
            scale = min(1.0, RENDER_MAX_SIDE / max(width, height, 1))
            if scale < 1:
                image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                                     Image.LANCZOS)
            translucent = False
            if "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info):
                image = image.convert("RGBA")
                translucent = image.getchannel("A").getextrema()[0] < 255
            keep_png = translucent or (source in ("PNG", "GIF", "WEBP") and image.width * image.height <= PNG_MAX_PIXELS)
            out = io.BytesIO()
            if keep_png:
                (image if translucent else image.convert("RGB")).save(out, "PNG", optimize=True)
                return out.getvalue(), "image/png", image.width, image.height
            image.convert("RGB").save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
            return out.getvalue(), "image/jpeg", image.width, image.height
    except Exception as error:  # not a picture, or one Pillow cannot read
        log.info(f"[ai_pictures] not a readable picture: {error}")
        return None


def store_picture(ws: str, data: bytes, media_type: str = "") -> dict | None:
    """Normalize ``data`` and store it in the workspace under its content
    hash: ``{"url", "width", "height", "size", "already_existed"}``, or None
    for bytes that are no picture. What ``POST /api/ai/pictures`` and
    ``clip_region`` do; the stored file is a chat picture or a note
    picture like any other upload (gamma/upload_gc.py counts the chat's
    references)."""
    shown = normalize(data, media_type)
    if not shown:
        return None
    bytes_, media, width, height = shown
    filename, existed = store_file(ws, bytes_, IMAGE_EXTENSIONS[media])
    return {"url": f"/api/uploads/{filename}", "width": width, "height": height,
            "size": len(bytes_), "already_existed": existed}


# --- the picture dict -------------------------------------------------------------------

def picture(image, label: str, group: str, **extra) -> dict:
    """A picture as the chat carries it: ``image`` is ``render_page``'s /
    ``normalize``'s ``(bytes, media_type, width, height)``."""
    return {"part": image_part(image), "label": label, "group": group,
            "width": image[2], "height": image[3], **extra}


def parts(pictures: list) -> list:
    """The ``(media_type, base64)`` parts of ``pictures``, the wires' shape."""
    return [p["part"] for p in pictures]


def data_url_picture(value) -> dict | None:
    """A picture from a data URL (an older client's pasted figure, never
    stored), normalized; None for anything else."""
    match = _DATA_URL_RE.match(str(value or ""))
    if not match:
        return None
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except (ValueError, TypeError):
        return None
    shown = normalize(data, match.group(1))
    return picture(shown, f"Pasted image, {shown[2]}×{shown[3]} px", "user") if shown else None


def upload_name(url: str) -> str:
    """The stored file name an ``/api/uploads/<name>`` URL names, "" for
    any other URL or a name that is no picture."""
    match = UPLOAD_REF_RE.search(str(url or ""))
    if not match:
        return ""
    name = match.group(1)
    return name if name[name.rfind("."):].lower() in IMAGE_MEDIA_TYPES else ""


def read_stored(ws: str, url: str):
    """``(bytes, media_type, width, height)`` of the workspace's stored
    picture ``url`` names, normalized; None when there is none. A model
    takes no SVG, so an SVG is a PNG of it (``pdf_image.svg_png``), None
    when it cannot be drawn."""
    name = upload_name(url)
    path = find_upload_file(name, ws) if name else None
    if not path:
        return None
    try:
        if path.stat().st_size > STORED_MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    media = IMAGE_MEDIA_TYPES.get(name[name.rfind("."):].lower(), "")
    if media == "image/svg+xml":
        try:
            data, media = svg_png(data, RENDER_MAX_SIDE)[0], "image/png"
        except ValueError as error:
            log.info(f"[ai_pictures] an SVG that does not draw: {error}")
            return None
    return normalize(data, media)


def embed_hint(url: str) -> str:
    return f"stored at {url} — to put it in a note, write ![…]({url})"


def stored_picture(ws: str, ref: dict, group: str = "user") -> dict | None:
    """A stored picture a message carries (``{"kind", "url", "name"?}``),
    read back for the model; None when the file is gone."""
    url = str(ref.get("url") or "")
    shown = read_stored(ws, url)
    if not shown:
        return None
    name = str(ref.get("name") or "").strip()
    what = ("Pasted image" if ref.get("kind") != "file" else f"Image file “{name[:80]}”" if name else "Image file")
    return picture(shown, f"{what}, {shown[2]}×{shown[3]} px, {embed_hint(url)}", group, url=url)


def parse_box(value) -> tuple | None:
    """A region's box ``[x0, y0, x1, y1]`` as page fractions, top-left
    origin, or None for anything that is not one."""
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return None
    return tuple(round(v, 4) for v in (x0, y0, x1, y1))


# --- the picture a page shows -------------------------------------------------------------

def image_region(ws: str, name: str, box=None, marks=()) -> tuple[bytes, str, int, int] | None:
    """The stored picture ``name`` (``<hash><ext>``: an upload, or a work
    folder's file read in place) as a JPEG, ``(bytes, media_type, width,
    height)`` as ``render_page`` shapes a picture, cut to ``box`` (fractions
    of the picture, top-left origin) when one is given: what a feedback
    thread's picture (gamma/feedback/pictures.py), the chat's page image of
    a picture page and ``clip_region`` draw. An SVG is drawn through its
    one-page PDF (``pdf_image.svg_pdf``); a raster is read with Pillow, its
    EXIF orientation applied, an animation's first frame, transparency on
    white. The crop is at least ``REGION_MIN_PX`` of the picture's own
    pixels a side (a vector picture has none), and the picture is scaled so
    its longer side is ``RENDER_MAX_SIDE``, zoomed at most ``IMAGE_MAX_ZOOM``.
    Each of ``marks``, ``(box, color, strong)``, is drawn as the viewer draws
    a region: an outline in the colour's line tone (``colors.line_color``),
    ``REGION_LINE_PX`` wide, ``REGION_STRONG_LINE_PX`` for a strong one, over
    a wash of the colour at ``REGION_FILL_SHARE`` of its alpha. None for a
    file not here or past ``STORED_MAX_BYTES``, one that is no picture
    Pillow or MuPDF reads (truncated, broken), a decompression bomb
    (``Image.MAX_IMAGE_PIXELS``) and a file that changed under the read."""
    path = find_upload_file(name, ws)
    if path is None:
        return None
    try:
        size = path.stat().st_size
        if size > STORED_MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if not data or len(data) != size:
        return None
    try:
        if IMAGE_MEDIA_TYPES.get(name[name.rfind("."):].lower()) == "image/svg+xml":
            drawn = _svg_region(data, box)
        else:
            drawn = _raster_region(data, box)
    except Exception as error:  # noqa: BLE001 — a picture that will not draw is none
        log.info(f"[ai_pictures] {name} does not draw: {error}")
        return None
    if drawn is None:
        return None
    image, frame = drawn
    image = _with_marks(image, marks, frame)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue(), "image/jpeg", image.width, image.height


def _zoom(width: float, height: float) -> float:
    """Pixels per pixel (or per point) that bring the longer side to
    ``RENDER_MAX_SIDE``, zooming at most ``IMAGE_MAX_ZOOM``."""
    return min(RENDER_MAX_SIDE / max(width, height, 1), IMAGE_MAX_ZOOM)


def _span(lo: float, hi: float, side: int) -> tuple[int, int]:
    """``(lo, hi)`` in whole pixels, grown around its centre to at least
    ``REGION_MIN_PX`` and kept inside ``[0, side]``."""
    need = min(REGION_MIN_PX, side) - (hi - lo)
    if need > 0:
        lo, hi = lo - need / 2, hi + need / 2
    if lo < 0:
        lo, hi = 0.0, hi - lo
    if hi > side:
        lo, hi = lo - (hi - side), float(side)
    lo, hi = max(0, math.floor(lo)), min(side, math.ceil(hi))
    return (lo, hi) if hi > lo else (max(0, side - 1), side)


def _raster_region(data: bytes, box):
    """A raster's region as a Pillow RGB image with the frame the marks
    are placed in; None past ``Image.MAX_IMAGE_PIXELS``."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data), formats=chat_formats()) as opened:
            if Image.MAX_IMAGE_PIXELS is not None and opened.width * opened.height > Image.MAX_IMAGE_PIXELS:
                return None
            opened.load()
            image = _on_white(ImageOps.exif_transpose(opened) or opened)
    width, height = image.size
    if box:
        left, right = _span(box[0] * width, box[2] * width, width)
        top, bottom = _span(box[1] * height, box[3] * height, height)
    else:
        left, top, right, bottom = 0, 0, width, height
    image = image.crop((left, top, right, bottom))
    scale = _zoom(image.width, image.height)
    if scale != 1:
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
    return image, (width, height, left, top, scale)


def _svg_region(data: bytes, box):
    """An SVG's region drawn by MuPDF (``pdf_image.svg_pdf``) as a Pillow
    RGB image, with the frame the marks are placed in: the page's points
    are the picture's pixels."""
    seen = []

    def zoom(width, height):
        seen.append((width, height))
        return _zoom(width, height)

    raster, _pages = page_raster(svg_pdf(data), 1, zoom, box)
    if raster is None or not seen:
        return None
    (width, height), scale = seen[0], _zoom(*seen[0])
    if box:
        width, height = width / max(box[2] - box[0], 1e-9), height / max(box[3] - box[1], 1e-9)
    left, top = (box[0] * width, box[1] * height) if box else (0.0, 0.0)
    image = Image.frombuffer(raster.mode, (raster.width, raster.height), raster.data, "raw", raster.mode,
                             raster.stride, 1).convert("RGB")
    return image, (width, height, left, top, scale)


def _on_white(image):
    """The image as RGB, its transparent parts on white."""
    if "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info):
        image = image.convert("RGBA")
        white = Image.new("RGB", image.size, "white")
        white.paste(image, mask=image.getchannel("A"))
        return white
    return image.convert("RGB")


def _with_marks(image, marks, frame):
    """``image`` with ``marks`` (``image_region``) drawn on it. ``frame`` is
    ``(width, height, left, top, scale)``: the picture's size in its own
    pixels, where the crop begins there, and the pixels of the image per
    pixel of the picture."""
    if not marks:
        return image
    width, height, left, top, scale = frame
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for mark, color, strong in marks:
        rgba = colors.parse_color(color) or colors.parse_color(colors.DEFAULT_HIGHLIGHT)
        line = colors.parse_color(colors.line_color(color) or colors.line_color(colors.DEFAULT_HIGHLIGHT))
        x0, x1 = sorted(round((v * width - left) * scale) for v in (mark[0], mark[2]))
        y0, y1 = sorted(round((v * height - top) * scale) for v in (mark[1], mark[3]))
        fill = tuple(round(v * 255) for v in rgba[:3]) + (round(rgba[3] * REGION_FILL_SHARE * 255),)
        draw.rectangle((x0, y0, x1, y1), fill=fill)
        draw.rectangle((x0, y0, x1, y1), outline=tuple(round(v * 255) for v in line[:3]) + (255,),
                       width=REGION_STRONG_LINE_PX if strong else REGION_LINE_PX)
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _page_view(conn, page_id: str) -> dict | None:
    """The document view of the page ``page_id`` (``page_document``), None
    for a page without one or no such page."""
    row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                       (page_id,)).fetchone()
    try:
        return page_document(json.loads(row[0] or "{}")) if row else None
    except ValueError:
        return None


def page_doc_id(conn, page_id: str) -> str:
    """The hash of the PDF the page ``page_id`` shows (``file_hash``: a
    duplicated page's is the original's), "" for a page without one (or no
    such page)."""
    return pdf_file_hash(_page_view(conn, page_id))


def page_pdf_path(ws: str, conn, page_id: str):
    """The path of the PDF file attached to the page ``page_id`` on this
    server (``ai_context.pdf_path``), None without one."""
    from .ai_context import pdf_path

    doc_id = page_doc_id(conn, page_id)
    return pdf_path(ws, doc_id) if doc_id else None


def _is_picture_page(view) -> bool:
    return is_image(view) and bool(view["file"])


def render_region(ws: str, conn, page_id: str, page_no: int, box=None, ink: bool = False):
    """A PDF page of the page ``page_id`` (or its region ``box``) as a
    picture, with the user's handwriting written on it when ``ink``, over
    the pictures and text boxes placed on it (``ink_view.page_with_handwriting``):
    ``(image, pages)`` as ``render_page`` gives them — ``(None, 0)`` for a
    page without a PDF file here, ``(None, pages)`` for a page number past
    the end. A page whose document is a picture is that picture (or its
    region, ``image_region``), one page long, with no handwriting."""
    view = _page_view(conn, page_id)
    if _is_picture_page(view):
        if page_no != 1:
            return None, 1
        image = image_region(ws, view["file"], box)
        return (image, 1) if image else (None, 0)
    path = page_pdf_path(ws, conn, page_id)
    if not path:
        return None, 0
    if ink:
        from .ink_view import page_with_handwriting
        data, pages = page_with_handwriting(ws, conn, page_id, page_no, path=path)
        if data:
            image, _ = render_page(data, 1, RENDER_MAX_SIDE, box)
            return image, pages
        if data == b"" and pages:
            return None, pages
    return render_page(str(path), page_no, RENDER_MAX_SIDE, box)


def region_picture(ws: str, conn, ref: dict, group: str = "user") -> dict | None:
    """A picture of a region the user pointed at (``{"kind": "area"|"view",
    "page_id", "page", "box", "ink"?}``), rendered now; None when it cannot
    be drawn (no PDF or picture, a page past the end, a malformed box)."""
    page_id = str(ref.get("page_id") or "").strip()[:64]
    try:
        page_no = int(ref.get("page") or 0)
    except (TypeError, ValueError):
        page_no = 0
    box = parse_box(ref.get("box")) if ref.get("box") is not None else None
    if not page_id or page_no < 1 or (ref.get("box") is not None and box is None):
        return None
    ink = bool(ref.get("ink"))
    image, _ = render_region(ws, conn, page_id, page_no, box, ink=ink)
    if image is None:
        return None
    title = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    title = (title[0] if title else "") or "Untitled"
    picture_page = _is_picture_page(_page_view(conn, page_id))
    where = "the picture" if picture_page else f"PDF page {page_no} of"
    if ref.get("kind") == "view":
        what = f"What the user sees: {where} “{title[:80]}”" + (", the visible part" if box else "")
    else:
        what = f"A region the user marked on {where} “{title[:80]}”"
    if ink and not picture_page:
        what += ", with their handwriting on it"
    return picture(image, f"{what}, {image[2]}×{image[3]} px", group, page_id=page_id, page=page_no)


def picture_url_of_page(page_id: str, page_no: int, box=None, ink: bool = False) -> str:
    """The ``GET /api/ai/page-image`` URL that shows a PDF page (or its
    region ``box``, with handwriting when ``ink``): what a view chip
    expands to and what the model embeds to show the user the page."""
    url = f"/api/ai/page-image/{page_id}?page={page_no}"
    if box:
        url += "&box=" + ",".join(f"{v:g}" for v in box)
    if ink:
        url += "&ink=1"
    return url


def ink_picture_url(block_id: str, whole: bool = False) -> str:
    """The ``GET /api/ai/ink-image`` URL that shows a handwriting block's
    picture (with ``whole`` its page or sheet with all the handwriting):
    what an ink chip expands to and what the model embeds."""
    return f"/api/ai/ink-image/{block_id}" + ("?area=page" if whole else "")


def picture_at(ws: str, conn, url: str) -> tuple[bytes, str] | None:
    """``(bytes, media_type)`` of the picture the chat shows from ``url``,
    read or drawn now in the workspace ``ws``: a stored upload
    (``/api/uploads/…``, as stored), an older chat's data URL, or a picture
    the routes draw from their parameters (``/api/ai/page-image``,
    ``/api/ai/ink-image``, ``/api/ai/selection-crop``, as they draw it).
    None for any other URL and for a picture that can no longer be drawn.
    What the chat export packs (gamma/chat_export.py)."""
    url = str(url or "").strip()
    match = _DATA_URL_RE.match(url)
    if match:
        try:
            data = base64.b64decode(match.group(2), validate=True)
        except (ValueError, TypeError):
            return None
        media = sniff(data)
        return (data, media) if media else None
    split = urlsplit(url)
    if split.scheme or split.netloc:
        return None
    path, query = unquote(split.path), parse_qs(split.query)

    def arg(name):
        return (query.get(name) or [""])[0]

    stored = UPLOAD_REF_RE.fullmatch(path)
    if stored:
        name = stored.group(1)
        media = IMAGE_MEDIA_TYPES.get(name[name.rfind("."):].lower())
        found = find_upload_file(name, ws) if media else None
        try:
            if not found or found.stat().st_size > STORED_MAX_BYTES:
                return None
            return found.read_bytes(), media
        except OSError:
            return None
    drawn = _DRAWN_RE.match(path)
    if not drawn:
        return None
    route, target = drawn.groups()
    if route == "ink-image":
        from .ink_view import picture as ink_picture

        page_id = page_root_id(conn, target)
        shown = ink_picture(ws, conn, target, page_id, whole=arg("area") == "page") if page_id else {}
        return tuple(shown["image"][:2]) if shown.get("image") else None
    try:
        page_no = int(arg("page"))
    except ValueError:
        return None
    box = parse_box(arg("box").split(",")) if arg("box") else None
    if page_no < 1 or (arg("box") and box is None):
        return None
    if route == "page-image":
        image, _ = render_region(ws, conn, target, page_no, box, ink=arg("ink") == "1")
    else:
        from . import ai_context

        pdf = ai_context.pdf_path(ws, target) if box and re.fullmatch(r"[0-9a-f]+", target) else None
        image = ai_context.render_selection_crop(pdf, page_no, box) if pdf else None
    return tuple(image[:2]) if image else None


def resolve(ws: str, conn, value, group: str = "user") -> dict | None:
    """One ``images`` entry of a request or a saved message as a picture:
    a stored picture, a region, or an older client's data URL."""
    if isinstance(value, str):
        return data_url_picture(value)
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "")
    if kind in REGION_KINDS:
        return region_picture(ws, conn, value, group)
    if value.get("url"):
        return stored_picture(ws, value, group)
    return None


def request_pictures(ws: str, conn, images: list, budget: int) -> tuple[list, int]:
    """``(pictures, left_out)``: the request's ``images`` resolved in order
    until ``budget`` of them are, and how many after that were not looked
    at (:func:`fit` adds them to the user group's count)."""
    out, left_out = [], 0
    for value in images or []:
        if len(out) >= budget:
            left_out += 1
            continue
        try:
            shown = resolve(ws, conn, value)
        except Exception as error:  # one picture that fails never fails the message
            log.warning(f"[ai_pictures] picture skipped: {error}")
            shown = None
        if shown:
            out.append(shown)
    return out, left_out


# --- the budget -------------------------------------------------------------------------

def fit(groups: list, budget: int) -> tuple[list, dict]:
    """``(kept, left_out)``: the pictures of ``groups`` (``[(name,
    pictures)]`` in priority order) up to ``budget``, and how many of each
    group did not fit."""
    kept, left_out = [], {}
    for name, pictures in groups:
        for shown in pictures:
            if len(kept) < budget:
                kept.append(shown)
            else:
                left_out[name] = left_out.get(name, 0) + 1
    return kept, left_out


_LEFT_OUT_WORDS = {"user": "attached picture", "selection": "picture of a selected passage",
                   "ink": "picture of attached handwriting", "area": "picture of an area highlight",
                   "history": "earlier picture"}


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"1 {word}"
    head, _, tail = word.partition(" of ")
    return f"{n} {head}s" + (f" of {tail}" if tail else "")


def label_lines(pictures: list, left_out: dict | None = None, budget: int = DEFAULT_BUDGET) -> str:
    """The lines that name a turn's pictures for the model, numbered in the
    order they are attached, and what was left out."""
    lines = []
    if pictures:
        lines.append("Pictures attached to this message, in order:")
        lines += [f"{i}. {p['label']}." for i, p in enumerate(pictures, start=1)]
    dropped = [(_plural(n, _LEFT_OUT_WORDS.get(name, "picture"))) for name, n in (left_out or {}).items() if n]
    if dropped:
        lines.append(f"Left out, over the budget of {budget} pictures per message: " + ", ".join(dropped) + ".")
    return "\n".join(lines)


def history_pictures(ws: str, conn, history: list, budget: int) -> int:
    """Keep the newest ``budget`` pictures of the conversation's earlier
    user turns in the model's view: each history item (a dict the chat
    sent) whose ``images`` carry stored or region pictures gets
    ``pictures_sent`` — ``{"images": [(media_type, b64)], "lines": text}``,
    the pictures read back now and the label lines naming them (and the
    older ones no longer shown). Written onto the items in place, which is
    where ``ai_context.build_messages`` reads them. Returns how many
    pictures were kept."""
    kept = 0
    for item in reversed(history or []):
        if not isinstance(item, dict) or item.get("role") != "user" or not isinstance(item.get("images"), list):
            continue
        refs = [v for v in item["images"] if isinstance(v, dict)]  # data URLs are not re-sent
        if not refs:
            continue
        shown, left = [], 0
        for ref in refs:
            if kept + len(shown) >= budget:
                left += 1
                continue
            try:
                one = resolve(ws, conn, ref, "history")
            except Exception as error:
                log.warning(f"[ai_pictures] earlier picture skipped: {error}")
                one = None
            if one:
                shown.append(one)
            else:
                left += 1
        kept += len(shown)
        lines = label_lines(shown)
        if left:
            lines += ("\n" if lines else "") + (
                f"({_plural(left, 'picture')} attached to this message "
                + ("are" if left > 1 else "is") + " not shown again.)")
        item["pictures_sent"] = {"images": parts(shown), "lines": lines}
    return kept
