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
  a region of a PDF page the server renders on demand ``{"kind": "area"|"view",
  "page_id", "page", "box": [x0, y0, x1, y1] (page fractions), "ink"?: true}``.
  An older client's data URL string is still read (``data_url_picture``),
  never stored.
- The context: pictures of selected passages whose text is unreliable,
  of area highlights in the notes, of attached handwriting blocks
  (gamma/ai_context.py builds them with :func:`picture`).

Every picture goes through :func:`normalize` on its way in: its longer side
at most ``RENDER_MAX_SIDE`` px (past that every provider downscales anyway),
JPEG unless it is small or translucent. That is what makes a per-picture
token cost (``Protocol.picture_tokens``) a fair bound, and what keeps a 6 MB
pasted PNG from being refused upstream. Without Pillow the bytes pass as
they are, under ``RAW_MAX_BYTES``.

The budget (``max_pictures`` on the request, Settings → AI → Chat →
"Pictures per message", default :data:`DEFAULT_BUDGET`) is one number for
all of a message's pictures, filled in :data:`GROUP_ORDER`: what the user
attached first, then the selection crops, then attached handwriting, then
the area highlights of the context pages. The newest pictures of earlier
turns stay in the conversation under the same budget (:func:`history_pictures`);
older ones are named in a line and not sent again.
"""

import base64
import io
import json
import re
import struct

from .blocks_store import page_attachment
from .logbuf import log
from .pdf_text import RENDER_MAX_SIDE, image_part, render_page
from .storage import IMAGE_EXTENSIONS, IMAGE_MEDIA_TYPES, UPLOAD_REF_RE, find_upload_file, store_file

DEFAULT_BUDGET = 12   # pictures per message (and newest earlier pictures kept)
MAX_BUDGET = 64
JPEG_QUALITY = 85
# A picture that stays PNG when it has no translucent pixel: small crisp
# things — icons, a formula, a little chart — where JPEG's blur shows.
PNG_MAX_PIXELS = 600_000
RAW_MAX_BYTES = 3_500_000   # without Pillow nothing is re-encoded: the providers' per-image limit
STORED_MAX_BYTES = 25_000_000  # the most a stored picture is read back for the model
GROUP_ORDER = ("user", "selection", "ink", "area")
# Only a region on a page the user is pointing at: boxes are page fractions.
REGION_KINDS = frozenset({"area", "view"})

_DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpeg|jpg|gif|webp));base64,([A-Za-z0-9+/=]+)$")


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


def dimensions(data: bytes) -> tuple[int, int]:
    """``(width, height)`` read from a PNG, JPEG or GIF header, (0, 0) when
    the file is none of those or too short to say."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 4 <= len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    if i + 9 > len(data):
                        break
                    height, width = struct.unpack(">HH", data[i + 5:i + 9])
                    return width, height
                i += 2 + length
    except struct.error:
        pass
    return 0, 0


def normalize(data: bytes, media_type: str = "") -> tuple[bytes, str, int, int] | None:
    """``(bytes, media_type, width, height)`` of ``data`` as the model gets
    it and as a chat picture is stored: the longer side at most
    ``RENDER_MAX_SIDE`` px, JPEG at :data:`JPEG_QUALITY` unless the picture
    is translucent somewhere or small (:data:`PNG_MAX_PIXELS`), then PNG.
    An animation keeps its first frame; EXIF orientation is applied. None
    for bytes that are no picture. Without Pillow the bytes pass through
    under :data:`RAW_MAX_BYTES` with the size their header says."""
    media_type = "image/jpeg" if media_type == "image/jpg" else (media_type or sniff(data))
    try:
        from PIL import Image, ImageOps
    except ImportError:
        if not media_type or len(data) > RAW_MAX_BYTES:
            return None
        width, height = dimensions(data)
        return data, media_type, width, height
    try:
        with Image.open(io.BytesIO(data)) as opened:
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
    ext = name[name.rfind("."):].lower()
    return name if ext in IMAGE_MEDIA_TYPES and ext != ".svg" else ""


def read_stored(ws: str, url: str):
    """``(bytes, media_type, width, height)`` of the workspace's stored
    picture ``url`` names, normalized; None when there is none."""
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
    return normalize(data, IMAGE_MEDIA_TYPES.get(name[name.rfind("."):].lower(), ""))


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


def page_doc_id(conn, page_id: str) -> str:
    """The id of the PDF attached to the page ``page_id``, "" for a page
    without one (or no such page)."""
    row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                       (page_id,)).fetchone()
    try:
        attachment = page_attachment(json.loads(row[0] or "{}")) if row else None
    except ValueError:
        attachment = None
    return attachment["id"] if attachment else ""


def page_pdf_path(ws: str, conn, page_id: str):
    """The path of the PDF file attached to the page ``page_id`` on this
    server (``ai_context.pdf_path``), None without one."""
    from .ai_context import pdf_path

    doc_id = page_doc_id(conn, page_id)
    return pdf_path(ws, doc_id) if doc_id else None


def render_region(ws: str, conn, page_id: str, page_no: int, box=None, ink: bool = False):
    """A PDF page of the page ``page_id`` (or its region ``box``) as a
    picture, with the user's handwriting written on it when ``ink``:
    ``(image, pages)`` as ``render_page`` gives them — ``(None, 0)`` for a
    page without a PDF file here, ``(None, pages)`` for a page number past
    the end."""
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
    be drawn (no PDF, a page past the end, a malformed box)."""
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
    if ref.get("kind") == "view":
        what = f"What the user sees: PDF page {page_no} of “{title[:80]}”" + (", the visible part" if box else "")
    else:
        what = f"A region the user marked on PDF page {page_no} of “{title[:80]}”"
    if ink:
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


def request_pictures(ws: str, conn, images: list, budget: int) -> list:
    """The pictures the request's ``images`` carry, in order, at most
    ``budget`` of them (the rest are counted as left out by :func:`fit`)."""
    out = []
    for value in (images or [])[:MAX_BUDGET]:
        try:
            shown = resolve(ws, conn, value)
        except Exception as error:  # one picture that fails never fails the message
            log.warning(f"[ai_pictures] picture skipped: {error}")
            shown = None
        if shown:
            out.append(shown)
    return out


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
