"""Shared PDF text extraction.

pypdfium2 first (proper word spacing and unicode), PyPDF2 fallback — but only
when pdfium can't open the file at all: an empty pdfium result is an answer
(scanned pages have no text layer for PyPDF2 to find either), not a failure.
Used by the AI context builder, metadata lookup, /pdf-text-status, and the
search indexer, so extraction fixes land once.
"""

import base64
import re
import io
import struct
import threading
import zlib

from .logbuf import log

# Sentinel the AI context builder hands to the model when extraction raised.
# Compare against the constant, never a rewritten literal.
PDF_EXTRACT_FAILED = "(PDF text extraction failed)"

# Hard ceiling on pages read from one PDF. Deliberately far above any real
# document: pages past the cap are invisible everywhere (not in the search
# index, not reachable by read_page) and look exactly like the end of the
# file, so a cap that actually bites silently hides content. It exists only
# to stop a pathological file from parsing forever.
MAX_PAGES = 5000

# pdfium is not thread-safe. Sync endpoints run in FastAPI's threadpool and the
# search indexer parses in a background thread, so two extractions can overlap
# — and when they do, pdfium fails BOTH with "Failed to load page", even for
# different files. Every pdfium call therefore goes through one lock; the AI
# context builder would otherwise hand the model "(PDF text extraction failed)"
# and it would answer from memory. Serialized calls are all pdfium needs;
# documents may stay open in between. So the walks over every page
# (iter_page_texts, page_sizes) take it once per page (_read_page), and once
# each for the open and the close: indexing a 5,000-page book interleaves with
# every other PDF read instead of holding them all off until the book is done.
# The short walks (page_count, outline, render_page) hold it throughout.
#
# The lock alone is not enough. pypdfium2 closes a page or document it still
# owns from a weakref finalizer, and its objects sit in reference cycles, so
# a page that was merely dropped is closed by the CYCLIC GC — later, on
# whatever thread happens to allocate (a sync round parsing JSON, a request
# handler), outside this lock, while another thread is inside pdfium. That
# is a native crash of the whole server (Windows exit 0x80000003), seen when
# an offline copy pulled a library of PDFs and their manifests were walked
# in the background. Two rules follow: every page/textpage/document made
# here is closed EXPLICITLY, inside the lock, as soon as it is done with
# (a closed object's finalizer is dead, the GC never touches pdfium for it);
# and, as a net under any object that still reaches a finalizer,
# pypdfium2's finalizer template is wrapped at import to take the same lock
# (_serialize_finalizers) — a finalizer on the walking thread re-enters the
# RLock, one on any other thread waits its turn.
class _PdfiumLock:
    """The one pdfium lock, handed over in turn between a walk's pages.

    A bare RLock is not fair: the walker releases it after a page and takes
    it back microseconds later, before a waiting thread has even woken up,
    so on Linux a page count still waited out the whole book. A thread
    taking the lock fresh therefore queues at the door first and holds the
    door while it waits; the walker's next page queues behind it there, so
    every waiter is in within one page. A thread that already holds the
    lock re-enters without the door, and so do the finalizers (a GC between
    the door and the lock runs them on that very thread): whoever holds the
    lock never waits at the door, so the two locks cannot deadlock."""

    def __init__(self):
        self.rlock = threading.RLock()
        self._door = threading.Lock()

    def _is_owned(self) -> bool:
        return self.rlock._is_owned()

    def __enter__(self):
        if self.rlock._is_owned():
            self.rlock.acquire()
        else:
            with self._door:
                self.rlock.acquire()

    def __exit__(self, *exc):
        self.rlock.release()


_lock = _PdfiumLock()


def _serialize_finalizers() -> None:
    try:
        import pypdfium2.internal.bases as bases
    except Exception:  # noqa: BLE001 — pypdfium2 missing or reshaped: nothing to wrap
        return
    inner = getattr(bases, "_close_template", None)
    if inner is None or getattr(inner, "_gamma_locked", False):
        return

    def locked_close(*args, **kwargs):
        with _lock.rlock:
            return inner(*args, **kwargs)

    locked_close._gamma_locked = True
    bases._close_template = locked_close


_serialize_finalizers()


def _open(src):
    """Open a PDF as ``("pdfium", doc)``, or ``("pypdf2", reader)`` when
    pdfium can't open the file at all. src is a path str or PDF bytes."""
    try:
        import pypdfium2 as pdfium
        with _lock:
            return "pdfium", pdfium.PdfDocument(src)
    except Exception as e:
        log.warning(f"[pdf-text] pypdfium2 open failed ({e}), falling back to PyPDF2")
        from PyPDF2 import PdfReader
        return "pypdf2", PdfReader(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else str(src))


def _warn_truncated(total: int, max_pages: int):
    if total > max_pages:
        log.warning(f"[pdf-text] {total}-page PDF truncated to {max_pages} pages")


def _read_page(pdf, i: int, read):
    """``read(page)`` of page ``i`` (0-based), the page closed again — one
    turn of the pdfium lock."""
    with _lock:
        page = pdf[i]
        try:
            return read(page)
        finally:
            page.close()


def _text(page) -> str:
    tp = page.get_textpage()
    try:
        return tp.get_text_bounded() or ""
    finally:
        tp.close()


def iter_page_texts(src, max_pages: int = MAX_PAGES, start_page: int = 1):
    """Yield per-page text for pages ``start_page``..``max_pages`` (1-based).
    src is a path str or PDF bytes. Takes the pdfium lock per page, never
    across the walk (nor while the consumer holds a page's text)."""
    kind, pdf = _open(src)
    if kind == "pypdf2":
        _warn_truncated(len(pdf.pages), max_pages)
        for i, pg in enumerate(pdf.pages):
            if i >= max_pages:
                return
            if i + 1 < start_page:
                continue
            try:
                yield pg.extract_text() or ""
            except Exception:
                yield ""
        return
    try:
        with _lock:
            total = len(pdf)
        _warn_truncated(total, max_pages)
        for i in range(max(0, start_page - 1), min(total, max_pages)):
            yield _read_page(pdf, i, _text)
    finally:
        with _lock:
            pdf.close()


def extract_pages(src, max_pages: int = MAX_PAGES) -> list[str]:
    """All page texts as a list (the search indexer's shape)."""
    return list(iter_page_texts(src, max_pages))


def extract_text(src, char_limit: int, empty_page_cap: int = 50,
                 start_page: int = 1, label_pages: bool = False) -> str:
    """Concatenated text for AI context. Stops early once char_limit is
    gathered, or after empty_page_cap consecutive textless pages — a scanned
    book shouldn't cost a full parse just to learn it has no text.
    start_page (1-based) skips the pages before it, so a read can jump
    straight to where a search hit landed."""
    return extract_text_pages(src, char_limit, empty_page_cap, start_page, label_pages)[0]



PAGE_LABEL_RE = re.compile(r"(?m)^\[PDF page (\d+)\]\n")


def page_label(page_no, continued: bool = False) -> str:
    """The `[PDF page N]` line that heads a page's text in AI context (the
    model cites these physical numbers, never printed ones)."""
    return f"[PDF page {page_no}{'; continued' if continued else ''}]\n"

def extract_text_pages(src, char_limit: int, empty_page_cap: int = 50,
                       start_page: int = 1, label_pages: bool = False) -> tuple[str, int]:
    """extract_text plus how many PDF pages the text spans (counted from
    start_page, empty pages included) — what the chat's coverage report
    tells the user: "pages 1–9 of 22"."""
    parts, total, empties, pages = [], 0, 0, 0
    for t in iter_page_texts(src, start_page=start_page):
        pages += 1
        if t.strip():
            empties = 0
            if label_pages:
                t = page_label(start_page + pages - 1) + t
            parts.append(t)
            total += len(t)
            if total >= char_limit:
                break
        else:
            empties += 1
            if empties >= empty_page_cap:
                break
    return "\n\n".join(parts), pages


def page_sizes(src) -> list[tuple[float, float]]:
    """``(width, height)`` in PDF points of every page, rotation applied — the
    same box pdf.js measures its scale-1 viewport from, so a layout built
    from these is exact. Empty when the file is unreadable. No text
    extraction; takes the pdfium lock per page, like iter_page_texts."""
    try:
        kind, pdf = _open(src)
        if kind == "pypdf2":
            out = []
            for pg in pdf.pages:
                box = pg.mediabox
                w, h = float(box.width), float(box.height)
                if (int(pg.get("/Rotate") or 0) // 90) % 2:
                    w, h = h, w
                out.append((w, h))
            return out
        try:
            with _lock:
                total = len(pdf)
            return [_read_page(pdf, i, lambda page: tuple(page.get_size())) for i in range(total)]
        finally:
            with _lock:
                pdf.close()
    except Exception as e:
        log.warning(f"[pdf-text] page sizes failed: {e}")
        return []


def page_count(src) -> int:
    """How many pages a PDF has (0 = unreadable). No text extraction."""
    with _lock:
        try:
            kind, pdf = _open(src)
            if kind == "pypdf2":
                return len(pdf.pages)
            try:
                return len(pdf)
            finally:
                pdf.close()
        except Exception as e:
            log.warning(f"[pdf-text] page count failed: {e}")
            return 0


# Longest side, in pixels, of a page picture handed to a vision model (past
# ~1.6k px providers downscale anyway; below it small print gets unreadable).
RENDER_MAX_SIDE = 1568


def _png(width: int, height: int, channels: int, rows) -> bytes:
    """A plain PNG (8-bit RGB / RGBA, filter 0) — no Pillow needed."""
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def _encode_bitmap(bitmap) -> tuple[bytes, str]:
    """``(bytes, media type)`` of a rendered pdfium bitmap: JPEG through
    Pillow when it is installed (a scan is a photo — several times smaller),
    else a PNG written here."""
    try:
        from PIL import Image  # noqa: F401 — optional
    except ImportError:
        width, height, stride = bitmap.width, bitmap.height, bitmap.stride
        channels = bitmap.n_channels
        data = bytes(bitmap.buffer)
        rows = (data[y * stride:y * stride + width * channels] for y in range(height))
        return _png(width, height, channels, rows), "image/png"
    buf = io.BytesIO()
    bitmap.to_pil().convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


def outline(src) -> list[tuple[int, str, int]]:
    """The PDF's own table of contents (its bookmarks) as ``(level, title,
    1-based page)`` in document order; entries without a page destination
    are skipped. [] when the file has none or can't be read."""
    with _lock:
        try:
            kind, pdf = _open(src)
            if kind == "pypdf2":
                return []
            try:
                entries = []
                for item in pdf.get_toc():
                    dest = item.get_dest()
                    index = dest.get_index() if dest else None
                    title = (item.get_title() or "").strip()
                    if index is not None and title:
                        entries.append((item.level, title, index + 1))
                return entries
            finally:
                pdf.close()
        except Exception as e:
            log.warning(f"[pdf-text] outline read failed: {e}")
            return []


# A cropped region is rendered sharper than a whole page (a formula is small),
# but never past this zoom — 4× = 288 dpi.
_CROP_MAX_SCALE = 4.0


def render_page(src, page_no: int, max_side: int = RENDER_MAX_SIDE, box=None):
    """Rasterize one page (1-based) for a vision model: ``(image, pages)``
    where image is ``(bytes, media_type, width, height)`` — the page scaled
    so its longer side is ``max_side`` px — or None when the page number is
    out of range; ``(None, 0)`` when the file can't be rendered (unreadable,
    or only PyPDF2 could open it). ``box`` = ``(x0, y0, x1, y1)`` as
    fractions of the page, top-left origin, renders just that region (its
    longer side at ``max_side`` px, zoom capped). Holds the pdfium lock
    throughout, like the other short walks."""
    with _lock:
        try:
            kind, pdf = _open(src)
            if kind == "pypdf2":
                return None, 0
            try:
                total = len(pdf)
                if page_no < 1 or page_no > total:
                    return None, total
                page = pdf[page_no - 1]
                try:
                    w, h = page.get_size()
                    crop = (0, 0, 0, 0)
                    scale = max_side / max(w, h, 1)
                    if box:
                        x0, y0, x1, y1 = box
                        # pdfium crops by the amount cut off each side
                        # (left, bottom, right, top) in PDF units.
                        crop = (x0 * w, (1 - y1) * h, (1 - x1) * w, y0 * h)
                        scale = min(max_side / max((x1 - x0) * w, (y1 - y0) * h, 1),
                                    _CROP_MAX_SCALE)
                    bitmap = page.render(scale=scale, crop=crop, rev_byteorder=True)
                    try:
                        data, media_type = _encode_bitmap(bitmap)
                        return (data, media_type, bitmap.width, bitmap.height), total
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            finally:
                pdf.close()
        except Exception as e:
            log.warning(f"[pdf-text] page render failed: {e}")
            return None, 0


def image_part(image) -> tuple[str, str]:
    """A picture ``render_page`` made, as the ``(media type, base64)`` pair
    a chat message or a tool result carries."""
    return image[1], base64.standard_b64encode(image[0]).decode("ascii")


# --- Glyphs under a highlight ------------------------------------------------

class GlyphPages:
    """A PDF's glyphs page by page, each with its box, for the text under a
    highlight's quads (``quote_under``). Opens the file with pdfium on the
    first page asked for and reads each page once; a file pdfium can't
    open, or a page without a text layer, has no glyphs, so its highlights
    import without a quote. A context manager; close it when done. Each
    page's read takes the pdfium lock once, like iter_page_texts.

    A glyph is ``(char, (left, bottom, right, top))`` in PDF user space,
    the space /QuadPoints are in. The box is pdfium's loose char box, the
    glyph's advance by the font's ascent and descent, so a thin "i" or "."
    has a centre as sound as any letter's. The separators pdfium generates
    between runs (spaces, line breaks) have an empty box and keep their
    place in the order; a glyph of zero advance (a combining mark) does
    too. ``src`` is a path str or PDF bytes."""

    def __init__(self, src):
        self._src = src
        self._pdf = None
        self._failed = False
        self._pages = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        pdf, self._pdf = self._pdf, None
        if pdf is not None:
            with _lock:
                pdf.close()

    def page(self, page_no: int) -> list:
        """The glyphs of page ``page_no`` (1-based), in pdfium's text order."""
        if page_no not in self._pages:
            self._pages[page_no] = self._read(page_no)
        return self._pages[page_no]

    def _read(self, page_no: int) -> list:
        if self._pdf is None and not self._failed:
            try:
                import pypdfium2 as pdfium
                with _lock:
                    self._pdf = pdfium.PdfDocument(self._src)
            except Exception as e:
                log.warning(f"[pdf-text] pypdfium2 open failed ({e}); highlights import without quotes")
                self._failed = True
        if self._pdf is None:
            return []
        try:
            with _lock:
                if not 1 <= page_no <= len(self._pdf):
                    return []
            return _read_page(self._pdf, page_no - 1, _glyphs)
        except Exception as e:
            log.warning(f"[pdf-text] glyphs of page {page_no} failed: {e}")
            return []


def _glyphs(page) -> list:
    """``GlyphPages``'s read of one page. A character beyond the BMP reaches
    pdfium as a surrogate pair where wchar_t is 16-bit; the pair is one glyph."""
    import pypdfium2.raw as pdfium_c
    tp = page.get_textpage()
    try:
        out, high = [], None
        for i in range(tp.count_chars()):
            code = pdfium_c.FPDFText_GetUnicode(tp, i)
            if not code:
                continue
            if 0xD800 <= code <= 0xDBFF:
                high = (code, tp.get_charbox(i, loose=True))
                continue
            if 0xDC00 <= code <= 0xDFFF:
                if high is not None:
                    (hi, box), high = high, None
                    out.append((chr(0x10000 + ((hi - 0xD800) << 10) + (code - 0xDC00)), box))
                continue
            high = None
            out.append((chr(code), tp.get_charbox(i, loose=True)))
        return out
    finally:
        tp.close()


def quote_under(glyphs, quads, limit: int = 1000) -> str:
    """The text under a highlight: the glyphs whose centre lies in one of
    ``quads`` (``(x1, y1, x2, y2)`` boxes in PDF user space, bottom-left
    origin), in page order, on one line. A glyph counts when more than half
    of it is covered, so a quad's edge never drags in the neighbour it
    touches, and a highlight that starts mid-line starts there. Whatever
    the quads skip between two counted glyphs (a line's end and the next
    line's start, an unhighlighted word, pdfium's own separators) reads as
    one space. Clipped to ``limit`` characters."""
    parts, gap = [], False
    for ch, (x1, y1, x2, y2) in glyphs:
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            # No box of its own: a generated separator, or a mark on the glyph before it.
            if ch.isspace():
                gap = gap or bool(parts)
            elif parts and not gap:
                parts.append(ch)
            continue
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if any(qx1 <= cx <= qx2 and qy1 <= cy <= qy2 for qx1, qy1, qx2, qy2 in quads):
            if gap:
                parts.append(" ")
            parts.append(ch)
            gap = False
        elif parts:
            gap = True
    return re.sub(r"\s+", " ", "".join(parts)).strip()[:limit]
