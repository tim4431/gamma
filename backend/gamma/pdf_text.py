"""Shared PDF reading: page text, page sizes, the outline, a page as a
picture, and the glyph boxes under a highlight — through PyMuPDF (import
name ``pymupdf``), with PyPDF2 as the fallback for text and sizes, but only
when PyMuPDF is not installed or can't open the file at all: an empty
result is an answer (scanned pages have no text layer for PyPDF2 to find
either), not a failure. Used by the AI context builder, metadata lookup,
/pdf-text-status, the search indexer, the manifest walk and the highlight
import, so extraction fixes land once.

MuPDF carries the base-14 fonts and Droid Sans Fallback inside the library,
so a page whose fonts are not embedded (CJK text above all) renders and
reads the same on every server, with no system font to find, and its text
extraction knows each glyph's font and box; and it has an iOS wheel. Those
are the reasons it reads here rather than pdfium, which read here before
(docs/research/pypdfium2-vs-pymupdf.md).
"""

import base64
import os
import re
import io
import struct
import threading
import zlib
from collections import OrderedDict
from contextlib import contextmanager

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

# PyMuPDF's documentation says it is not thread-safe: running it on several
# threads "may cause incorrect behaviour or even crash Python itself". Sync
# endpoints run in FastAPI's threadpool and the search indexer parses in a
# background thread, so two reads can overlap; every PyMuPDF call therefore
# goes through one lock. Serialized calls are all it needs; documents may
# stay open in between. So the walks over every page (iter_page_texts,
# page_sizes, GlyphPages) take it once per page (_read_page), and once each
# for the open and the close: indexing a 5,000-page book interleaves with
# every other PDF read instead of holding them all off until the book is
# done. The short walks (page_count, outline, render_page) hold it
# throughout.
#
# Two more facts about PyMuPDF shape the calls under it. It holds the GIL
# inside every call, so a long call stalls the event loop — every request
# and every WebSocket — for its duration: a call here is one page's text,
# size, glyphs or picture, never a pass over a whole document inside the
# library. And it frees its objects by reference count, under the GIL, so a
# document is closed and a page dropped explicitly, inside the lock, as soon
# as it is done with, and that is the end of them: nothing is finalized
# later, on another thread. (pypdfium2, which read here before, closed its
# objects from weakref finalizers that the cyclic GC ran on whatever thread
# allocated next — a native crash of the whole server when that landed
# inside another thread's call — and needed its finalizer wrapped in this
# lock as a net. There is nothing to net now.)
class _MuPdfLock:
    """The one PyMuPDF lock, handed over in turn between a walk's pages.

    A bare RLock is not fair: the walker releases it after a page and takes
    it back microseconds later, before a waiting thread has even woken up,
    so on Linux a page count still waited out the whole book. A thread
    taking the lock fresh therefore queues at the door first and holds the
    door while it waits; the walker's next page queues behind it there, so
    every waiter is in within one page. A thread that already holds the
    lock re-enters without the door: whoever holds the lock never waits at
    the door, so the two locks cannot deadlock."""

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


_lock = _MuPdfLock()

# The pymupdf module once _pymupdf has imported it, and the flags set with
# it. _TEXT_FLAGS is page.get_text("text")'s: MuPDF's defaults without
# TEXT_PRESERVE_LIGATURES, so a ligature comes out as its letters ("fi",
# not "ﬁ"; search folds them anyway, and the AI and the metadata regexes
# read plain letters better). TEXT_DEHYPHENATE stays off: an end-of-line
# hyphen stays as written, where pdfium joined some of them — search
# re-joins the words (gamma/textnorm.py) and the AI reads the page as it
# is. Lines end in "\n" (pdfium's ended in "\r\n"). _RAWDICT_FLAGS is the
# glyph walk's: without TEXT_PRESERVE_IMAGES, which would copy every
# image's bytes into the dictionary (a second on an image-heavy page) for
# boxes nobody reads.
_mupdf = None
_TEXT_FLAGS = 0
_RAWDICT_FLAGS = 0


def _pymupdf():
    """The pymupdf module, imported on first use (70 ms and 30 MB: a server
    that never reads a PDF never pays it, and one without PyMuPDF installed
    starts all the same and falls back to PyPDF2), with MuPDF's own chatter
    on the console turned off; the warnings it would have printed are
    gathered instead, for the log line of a failed open (``_mupdf_warnings``).
    ImportError when it is not installed."""
    global _mupdf, _TEXT_FLAGS, _RAWDICT_FLAGS
    if _mupdf is None:
        import pymupdf
        pymupdf.TOOLS.mupdf_display_errors(False)
        _TEXT_FLAGS = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES
        _RAWDICT_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        _mupdf = pymupdf
    return _mupdf


def _mupdf_warnings() -> str:
    """What MuPDF has warned since ``_mupdf_open`` last emptied its store —
    read under the lock, right after an open failed, that is the open's own
    messages — as a suffix for a log line ("; MuPDF: ..."); "" when nothing."""
    if _mupdf is None:
        return ""
    try:
        text = (_mupdf.TOOLS.mupdf_warnings(reset=True) or "").strip()
    except Exception:  # noqa: BLE001 — a log suffix is never worth an error
        return ""
    return f"; MuPDF: {' | '.join(text.splitlines())}" if text else ""


def _mupdf_open(src):
    """A PyMuPDF document of ``src`` (a path or PDF bytes); called under the
    lock. ``filetype="pdf"`` so an image or XPS file is never opened as a
    document of its own kind. A file whose password the empty-password try
    did not satisfy counts as unreadable, as pdfium's refusal did: it is
    closed again and the caller falls back."""
    mupdf = _pymupdf()
    # PyMuPDF keeps every warning MuPDF raises, in any call, in one list for
    # the process (turning the display off only stops the printing), and
    # nothing of its own ever empties it. Emptied before each open, it holds
    # one open's messages: a failed open's log line carries its own, not the
    # repairs of every damaged file read since the last failure, and a
    # server that reads such files for weeks does not grow a list of them.
    mupdf.TOOLS.reset_mupdf_warnings()
    if isinstance(src, (bytes, bytearray)):
        doc = mupdf.open(stream=bytes(src), filetype="pdf")
    else:
        doc = mupdf.open(os.fspath(src), filetype="pdf")
    if doc.needs_pass:
        doc.close()
        raise ValueError("the file needs a password")
    return doc


def _open(src):
    """Open a PDF as ``("mupdf", doc)``, or ``("pypdf2", reader)`` when
    PyMuPDF is not installed or can't open the file at all. src is a path
    str or PDF bytes."""
    with _lock:
        try:
            return "mupdf", _mupdf_open(src)
        except Exception as e:
            reason = f"{e}{_mupdf_warnings()}"
    log.warning(f"[pdf-text] PyMuPDF open failed ({reason}), falling back to PyPDF2")
    from PyPDF2 import PdfReader
    return "pypdf2", PdfReader(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else str(src))


def _warn_truncated(total: int, max_pages: int):
    if total > max_pages:
        log.warning(f"[pdf-text] {total}-page PDF truncated to {max_pages} pages")


def _read_page(pdf, i: int, read):
    """``read(page)`` of page ``i`` (0-based) — one turn of the lock. The
    page is dropped before the turn ends: PyMuPDF frees by reference count,
    so the drop, inside the lock, is its close (a page has none of its own)."""
    with _lock:
        page = pdf[i]
        try:
            return read(page)
        finally:
            del page


@contextmanager
def unrotated(page):
    """``page`` with its /Rotate set to 0 for the block and put back after —
    in memory, on a document of the caller's own that is never saved.

    MuPDF extracts text and logs drawings from the page unrotated (get_text
    sets the rotation to 0 for the read, and back), in a space whose origin
    is the page box's top-left corner (the crop box, or the media box), y
    down. page.transformation_matrix is that space's transform from PDF
    user space only while the rotation is 0: on a rotated page PyMuPDF
    makes it a bare flip that forgets the page box's offset, and a
    highlight on a rotated page with cropped margins quoted nothing. Read
    the page inside this block, and the matrix is the true one. Under the
    lock, like every call on a page."""
    rotation = page.rotation
    if rotation:
        page.set_rotation(0)
    try:
        yield page
    finally:
        if rotation:
            page.set_rotation(rotation)


def user_unit(page) -> float:
    """The page's /UserUnit: how many points one unit of MuPDF's page space
    is. MuPDF (1.26 on) folds it into page.rect, the bbox log, the text
    boxes and the pixmap; pdf.js (4.4.168, the viewer's), pdfium and PyPDF2
    leave it out, so a measure that leaves here in points divides by it. 1
    when the key is absent, not a number or unreadable (MuPDF then ignores
    it too); the magnitude of a negative one, which MuPDF applies as a
    mirror of the same size. Called under the lock, like every read of a
    page."""
    doc = page.parent
    try:
        kind, value = doc.xref_get_key(page.xref, "UserUnit")
        if kind == "xref":
            value, kind = doc.xref_object(int(value.split()[0])), "float"
        if kind not in ("int", "float"):
            return 1.0
        return abs(float(value)) or 1.0
    except Exception:  # noqa: BLE001 — a key that cannot be read is no key
        return 1.0


def _text(page) -> str:
    return page.get_text("text", flags=_TEXT_FLAGS) or ""


# --- The page-text cache -----------------------------------------------------
# The AI chat reads a paper's text on every turn — the head excerpt, a
# selection's placement over the whole document, each read_page window —
# and each read was a walk under the lock, seconds on a long book and in
# the way of every other PDF read meanwhile. A stored PDF never changes
# under its name (a content hash), so the text of a page, once extracted,
# is kept here: per file, the pages read so far (a head read fills the head
# only; the next window goes on from there), bounded by characters across
# files and dropped least recently used. The key carries the file's size
# and mtime, so a file rewritten in place is read again. Bytes sources (an
# uploaded PDF in a chat message) are never cached.
TEXT_CACHE_CHARS = 32_000_000   # about 32–128 MB of Python strings
TEXT_CACHE_FILES = 64


class _TextEntry:
    __slots__ = ("total", "pages", "chars", "cached")

    def __init__(self, total: int):
        self.total = total
        self.pages: list = [None] * total  # None = not read yet
        self.chars = 0
        self.cached = True


_text_cache: "OrderedDict[tuple, _TextEntry]" = OrderedDict()
_text_cache_chars = 0
_text_cache_lock = threading.Lock()


def _text_key(src):
    """The cache key of a path source: ``(absolute path, size, mtime)``;
    None for bytes or a path that cannot be read."""
    if not isinstance(src, (str, os.PathLike)):
        return None
    try:
        st = os.stat(src)
    except OSError:
        return None
    return (os.path.abspath(os.fspath(src)), st.st_size, st.st_mtime_ns)


def _text_entry(key):
    """The cached entry of ``key``, made most recently used; None when there is none."""
    if key is None:
        return None
    with _text_cache_lock:
        entry = _text_cache.get(key)
        if entry is not None:
            _text_cache.move_to_end(key)
        return entry


def _text_remember(key, total: int) -> _TextEntry:
    """A new entry for ``key`` (or the one another thread made meanwhile)."""
    entry = _TextEntry(total)
    if key is None:
        entry.cached = False
        return entry
    with _text_cache_lock:
        existing = _text_cache.get(key)
        if existing is not None:
            _text_cache.move_to_end(key)
            return existing
        _text_cache[key] = entry
        _text_evict_locked()
        return entry


def _text_store(entry: _TextEntry, i: int, text: str) -> None:
    global _text_cache_chars
    with _text_cache_lock:
        if entry.pages[i] is not None:
            return
        entry.pages[i] = text
        entry.chars += len(text)
        if entry.cached:
            _text_cache_chars += len(text)
            _text_evict_locked()


def _text_evict_locked() -> None:
    """Drop the least recently used entries past the bounds (the newest
    one stays: it is the walk in progress). Under _text_cache_lock."""
    global _text_cache_chars
    while len(_text_cache) > 1 and (len(_text_cache) > TEXT_CACHE_FILES or _text_cache_chars > TEXT_CACHE_CHARS):
        _, victim = _text_cache.popitem(last=False)
        victim.cached = False
        _text_cache_chars -= victim.chars


def text_cache_clear() -> None:
    """Forget every cached page text (tests)."""
    global _text_cache_chars
    with _text_cache_lock:
        for entry in _text_cache.values():
            entry.cached = False
        _text_cache.clear()
        _text_cache_chars = 0


def _pypdf2_texts(pdf, max_pages: int, start_page: int):
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


def iter_page_texts(src, max_pages: int = MAX_PAGES, start_page: int = 1, cache: bool = True):
    """Yield per-page text for pages ``start_page``..``max_pages`` (1-based).
    src is a path str or PDF bytes. Takes the lock per page, never across
    the walk (nor while the consumer holds a page's text). A page read
    before comes from the page-text cache without opening the file; the
    file is opened at the first page that is not there. ``cache=False``
    reads past the cache and leaves nothing in it (the search indexer's
    walk over a library would otherwise push out the papers being read)."""
    key = _text_key(src) if cache else None
    entry = _text_entry(key)
    pdf = None  # the PyMuPDF document, once opened (a PyPDF2 reader is never held here)
    try:
        if entry is None:
            kind, opened = _open(src)
            if kind == "pypdf2":
                yield from _pypdf2_texts(opened, max_pages, start_page)
                return
            pdf = opened
            with _lock:
                total = len(pdf)
            entry = _text_remember(key, total)
        _warn_truncated(entry.total, max_pages)
        for i in range(max(0, start_page - 1), min(entry.total, max_pages)):
            text = entry.pages[i]
            if text is None:
                if pdf is None:
                    kind, opened = _open(src)
                    if kind == "pypdf2":  # the file is not what it was: no cache for it
                        yield from _pypdf2_texts(opened, max_pages, i + 1)
                        return
                    pdf = opened
                text = _read_page(pdf, i, _text)
                _text_store(entry, i, text)
            yield text
    finally:
        if pdf is not None:
            with _lock:
                pdf.close()


def extract_pages(src, max_pages: int = MAX_PAGES, cache: bool = True) -> list[str]:
    """All page texts as a list (the search indexer's shape)."""
    return list(iter_page_texts(src, max_pages, cache=cache))


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
    """``(width, height)`` in PDF points of every page, rotation applied
    (``page.rect``) and divided by the page's /UserUnit, which MuPDF folds
    into page.rect and pdf.js's scale-1 viewport does not (``user_unit``) —
    the same box pdf.js measures that viewport from, so a layout built from
    these is exact. Empty when the file is unreadable. No text extraction;
    takes the lock per page, like iter_page_texts."""
    def _size(page):
        unit = user_unit(page)
        return (page.rect.width / unit, page.rect.height / unit)

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
            return [_read_page(pdf, i, _size) for i in range(total)]
        finally:
            with _lock:
                pdf.close()
    except Exception as e:
        log.warning(f"[pdf-text] page sizes failed: {e}")
        return []


def _span_text(spans) -> str:
    """A line's spans as its text, with a space where two spans stand a
    space apart: many PDFs set each word as a span of its own and draw no
    space character between them."""
    out, prev = "", None
    for span in spans:
        text = span["text"]
        if prev is not None and not out.endswith(" ") and not text.startswith(" "):
            gap = span["bbox"][0] - prev["bbox"][2]
            if gap > 0.15 * min(float(prev.get("size") or 0), float(span.get("size") or 0)):
                out += " "
        out += text
        prev = span
    return " ".join(out.split())


def _largest_type_lines(page, max_chars: int) -> str:
    """The lines of ``page`` set in its largest type, in reading order: the
    title on most papers. Only horizontal lines count (arXiv stamps its id
    up the left margin in bigger type), and lines with fewer than two
    letters (a drop cap, a page number) carry no size; a line's size is
    its largest letters', so a title line with a smaller italic word keeps
    the title's. A size whose lines hold fewer than three words (a
    journal's banner, an "ARTICLE" label) is passed over for the next size
    down, three sizes at most. Lines within 3% of the chosen size count as
    it; a journal header a half point smaller does not."""
    lines = []  # (size, y, x, text)
    for block in page.get_text("dict", flags=_TEXT_FLAGS).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            cos, sin = line.get("dir") or (1, 0)
            if cos < 0.99 or abs(sin) > 0.01:
                continue
            spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            text = _span_text(spans)
            if sum(c.isalpha() for c in text) < 2:
                continue
            sizes = [float(s.get("size") or 0) for s in spans if any(c.isalpha() for c in s["text"])]
            if not sizes:
                continue
            x, y = line.get("bbox", (0, 0, 0, 0))[:2]
            lines.append((max(sizes), y, x, text))
    sizes = sorted({round(size, 1) for size, _, _, _ in lines}, reverse=True)
    tried, above = 0, float("inf")  # the sizes passed over are above `above`
    while sizes and tried < 3:
        size = sizes[0]
        group = sorted((l for l in lines if size * 0.97 <= l[0] < above), key=lambda l: (round(l[1]), l[2]))
        above = size * 0.97
        sizes = [s for s in sizes if s < above]
        tried += 1
        if sum(len(re.findall(r"[^\W\d_]{2,}", l[3])) for l in group) < 3:
            continue
        out = ""
        for _, _, _, text in group:
            if out and len(out) + 1 + len(text) > max_chars:
                break
            out = f"{out} {text}".strip() if out else text[:max_chars]
        return out
    return ""


def title_guess(src, max_chars: int = 300) -> str:
    """The first page's largest-type lines (``_largest_type_lines``): what
    a metadata lookup queries Crossref with before the text head, whose
    reading order starts with a running header, a journal banner or a
    "Downloaded from" line as often as with the title. "" when the file
    cannot be read, has no pages or no text, or opened through PyPDF2,
    which knows no sizes. Takes the lock like every other read."""
    try:
        kind, pdf = _open(src)
    except Exception as e:  # noqa: BLE001 — a guess is never worth an error
        log.warning(f"[pdf-text] title guess failed: {e}")
        return ""
    if kind == "pypdf2":
        return ""
    try:
        with _lock:
            empty = len(pdf) == 0
        if empty:
            return ""
        return _read_page(pdf, 0, lambda page: _largest_type_lines(page, max_chars))
    except Exception as e:  # noqa: BLE001
        log.warning(f"[pdf-text] title guess failed: {e}")
        return ""
    finally:
        with _lock:
            pdf.close()


def page_count(src) -> int:
    """How many pages a PDF has (0 = unreadable). No text extraction; a
    file in the page-text cache answers without being opened."""
    entry = _text_entry(_text_key(src))
    if entry is not None:
        return entry.total
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


class _Raster:
    """A rendered page's pixels, copied out of MuPDF's pixmap — what
    ``render_page`` takes outside the lock to encode (the encoding is pure
    Python without Pillow, far longer than the render itself)."""
    __slots__ = ("data", "width", "height", "stride", "channels", "mode")

    def __init__(self, pixmap):
        self.data = pixmap.samples  # bytes, a copy: the pixmap may go
        self.width, self.height, self.stride = pixmap.width, pixmap.height, pixmap.stride
        self.channels, self.mode = pixmap.n, "RGBA" if pixmap.alpha else "RGB"


def _encode_raster(raster: _Raster) -> tuple[bytes, str]:
    """``(bytes, media type)`` of a rendered page: JPEG through Pillow when
    it is installed (a scan is a photo — several times smaller), else a PNG
    written here."""
    try:
        from PIL import Image
    except ImportError:
        width, height, stride, channels = raster.width, raster.height, raster.stride, raster.channels
        data = raster.data
        rows = (data[y * stride:y * stride + width * channels] for y in range(height))
        return _png(width, height, channels, rows), "image/png"
    buf = io.BytesIO()
    image = Image.frombuffer(raster.mode, (raster.width, raster.height), raster.data, "raw",
                             raster.mode, raster.stride, 1)
    image.convert("RGB").save(buf, "JPEG", quality=85)
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
                for level, title, page_no in pdf.get_toc(simple=True):
                    title = (title or "").strip()
                    if page_no >= 1 and title:  # -1 or 0: no page destination
                        entries.append((level, title, page_no))
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
    longer side at ``max_side`` px, zoom capped). The page is drawn as a
    viewer shows it, annotations included: an /Ink or /Highlight without an
    appearance stream (what pdf_export writes) gets one synthesized, which
    view_ink relies on. Holds the lock for the render, like the other short
    walks; the image is encoded outside it (the pure-Python PNG takes
    longer than MuPDF does)."""
    try:
        with _lock:
            kind, pdf = _open(src)
            if kind == "pypdf2":
                return None, 0
            try:
                total = len(pdf)
                if page_no < 1 or page_no > total:
                    return None, total
                mupdf = _pymupdf()
                page = pdf[page_no - 1]
                try:
                    # page.rect is the page as displayed (rotation applied),
                    # top-left origin, in points: the space the box's
                    # fractions are in, and the space the clip is given in.
                    w, h = page.rect.width, page.rect.height
                    scale, clip = max_side / max(w, h, 1), None
                    if box:
                        x0, y0, x1, y1 = box
                        clip = mupdf.Rect(x0 * w, y0 * h, x1 * w, y1 * h)
                        scale = min(max_side / max((x1 - x0) * w, (y1 - y0) * h, 1), _CROP_MAX_SCALE)
                    pixmap = page.get_pixmap(matrix=mupdf.Matrix(scale, scale), clip=clip, alpha=False)
                    raster = _Raster(pixmap)
                    del pixmap
                finally:
                    del page
            finally:
                pdf.close()
        data, media_type = _encode_raster(raster)
        return (data, media_type, raster.width, raster.height), total
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
    highlight's quads (``quote_under``). Opens the file with PyMuPDF on the
    first page asked for and reads each page once; a file it can't open, or
    a page without a text layer, has no glyphs, so its highlights import
    without a quote. A context manager; close it when done. Each page's
    read takes the lock once, like iter_page_texts.

    A glyph is ``(char, (left, bottom, right, top))`` in PDF user space,
    the space /QuadPoints are in (MuPDF's boxes come in the unrotated
    page's space, top-left origin; ``_glyphs`` maps them back). The box is the
    glyph's advance by the font's ascender and descender, so a thin "i" or
    "." has a centre as sound as any letter's. After each of MuPDF's lines
    comes a line break with an empty box, which keeps its place in the
    order; a glyph of zero advance (a combining mark) has one too. ``src``
    is a path str or PDF bytes."""

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
        """The glyphs of page ``page_no`` (1-based), in MuPDF's text order."""
        if page_no not in self._pages:
            self._pages[page_no] = self._read(page_no)
        return self._pages[page_no]

    def _read(self, page_no: int) -> list:
        if self._pdf is None and not self._failed:
            with _lock:
                try:
                    self._pdf = _mupdf_open(self._src)
                except Exception as e:
                    reason = f"{e}{_mupdf_warnings()}"
                    self._failed = True
            if self._failed:
                log.warning(f"[pdf-text] PyMuPDF open failed ({reason}); highlights import without quotes")
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
    """``GlyphPages``'s read of one page: the chars of MuPDF's raw text
    dictionary (blocks of text, their lines, their spans, their chars; the
    image blocks skipped), each box taken back to PDF user space, and a
    line break after every line. A character beyond the BMP is one char
    here. The page is read ``unrotated``: that is the space MuPDF's boxes
    come in, and the only one in which page.transformation_matrix takes
    them back to user space whole, page-box offset included."""
    mupdf = _pymupdf()
    with unrotated(page):
        to_user = ~page.transformation_matrix
        blocks = page.get_text("rawdict", flags=_RAWDICT_FLAGS)["blocks"]
    out = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    box = mupdf.Rect(char["bbox"]) * to_user  # normalized: bottom < top
                    out.append((char["c"], (box.x0, box.y0, box.x1, box.y1)))
            out.append(("\n", (0.0, 0.0, 0.0, 0.0)))
    return out


def quote_under(glyphs, quads, limit: int = 1000) -> str:
    """The text under a highlight: the glyphs whose centre lies in one of
    ``quads`` (``(x1, y1, x2, y2)`` boxes in PDF user space, bottom-left
    origin), in page order, on one line. A glyph counts when more than half
    of it is covered, so a quad's edge never drags in the neighbour it
    touches, and a highlight that starts mid-line starts there. Whatever
    the quads skip between two counted glyphs (a line's end and the next
    line's start, an unhighlighted word, the line breaks between MuPDF's
    lines) reads as one space. Clipped to ``limit`` characters."""
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
