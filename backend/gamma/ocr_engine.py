"""The text recognition engine: Tesseract 5.5 through tesserocr, whose wheels
carry libtesseract and Leptonica, with the fast models for English, French
and German (``LANGS``) in backend/tessdata (``config.tessdata_dir``; the
folder's README names their source and hashes). gamma/pdf_ocr.py runs it on
the pictures of a stored PDF's scanned pages; docs/research/ocr.md, "Stage
2", measured it against the other engines.

- ``probe()``: whether this server can recognize text, cheaply, for
  /api/server-config.
- ``check()``: the full check, the engine loaded and its models opened,
  before a run's first page.
- ``recognize(raster)``: a page picture's words, line by line, in reading
  order.
- ``render_zoom(width, height)``: the zoom a page is drawn at for it.

tesserocr is imported on first use: it loads 35 MB (Linux) to 56 MB
(Windows) of native libraries, and a server that never recognizes a page
never pays that. It has no iOS or Android build, so the mobile apps' server
has no engine (``probe`` says so). Recognition releases the GIL (measured:
the longest event-loop stall 1.8 ms over a 2.6 s page), so it runs on the
job's own thread beside every request.
"""

import importlib.util
import math
import os
import platform
import sys
import threading
from contextlib import contextmanager

from . import config
from .logbuf import log

LANGS = "eng+fra+deu"
LANG_LIST = tuple(LANGS.split("+"))

# A page is drawn at 300 dpi (what the study measured), less when that
# would pass MAX_PIXELS (an A4 page at 300 dpi; a poster drawn at 300 dpi
# would take gigabytes) or make a side longer than MAX_SIDE (Tesseract
# refuses a side past 32767; a long strip would get there).
DPI = 300
MAX_PIXELS = 2480 * 3508
MAX_SIDE = 10000
# What a page may take and give: past the timeout, or past these many words
# or characters of a word (a page of noise, a halftone read as letters), the
# page has no result.
PAGE_TIMEOUT_MS = 120_000
MAX_WORDS = 20_000
MAX_WORD_CHARS = 200

_tess = None                 # the tesserocr module once imported
_static: dict = {}           # tessdata path → probe's reason ("" none), the cheap checks
_checked: dict = {}          # tessdata path → check's reason ("" none), the full check
_check_lock = threading.Lock()


def _tesserocr():
    """The tesserocr module, imported on first use. OMP_THREAD_LIMIT is set
    first, unless the environment sets it (the Dockerfile and the desktop
    app do): the Windows wheel's OpenMP runs a page in 3.7 s with one
    thread and 4.9 s with its default team (measured), and it reads the
    variable when it loads."""
    global _tess
    if _tess is None:
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")
        import tesserocr
        _tess = tesserocr
    return _tess


def render_zoom(width: float, height: float) -> float:
    """The zoom (pixels per point) a page ``width`` x ``height`` points is
    drawn at for recognition: ``DPI``, less where ``MAX_PIXELS`` or
    ``MAX_SIDE`` would be passed. What ``pdf_text.page_raster`` takes as
    its ``zoom``."""
    w, h = max(float(width), 1.0), max(float(height), 1.0)
    return min(DPI / 72, math.sqrt(MAX_PIXELS / (w * h)), MAX_SIDE / max(w, h))


def _mobile() -> bool:
    return sys.platform in ("ios", "android") or hasattr(sys, "getandroidapilevel")


def _old_macos() -> bool:
    """macOS before 15: the tesserocr wheels are built for 15 on (their tag
    macosx_15_0), and requirements.txt's marker skips it on an older
    system, where pip would try to build it and fail."""
    if sys.platform != "darwin":
        return False
    try:
        return int(platform.mac_ver()[0].split(".")[0]) < 15
    except ValueError:
        return True


def _static_reason(path) -> str:
    """The cheap checks: no import, no Init. "" when they pass, else why
    not, in words a person can read (no paths)."""
    key = os.fspath(path)
    if key not in _static:
        if _mobile():
            reason = "Text recognition is not available in the mobile apps yet."
        elif _old_macos():
            reason = "Text recognition needs macOS 15 or later."
        elif importlib.util.find_spec("tesserocr") is None:
            reason = "The text recognition engine is not installed on this server."
        elif not all(os.path.isfile(os.path.join(key, f"{lang}.traineddata")) for lang in LANG_LIST):
            reason = "The text recognition models are missing on this server."
        else:
            reason = ""
        _static[key] = reason
    return _static[key]


def probe() -> dict | None:
    """Whether this server can recognize text, as /api/server-config tells
    the client: ``{"engine": "tesseract", "langs": [...]}``, or None when
    it is turned off (``config.ocr_enabled``), has no engine or no models,
    runs where the engine does not (the mobile apps, macOS before 15), or
    failed its full check (``check``). Cheap: the engine is not imported
    here. The checks are cached; whether OCR is on is read every time."""
    if not config.ocr_enabled():
        return None
    path = config.tessdata_dir()
    if _static_reason(path) or _checked.get(os.fspath(path)):
        return None
    return {"engine": "tesseract", "langs": list(LANG_LIST)}


def check() -> str | None:
    """The full check: what ``probe`` checks, then the engine imported and
    one engine set up with the models and ended. None when it runs; else
    the reason, a plain sentence without paths (the log has the error).
    Cached once run (a failure turns ``probe`` off), except whether OCR is
    on. Blocking: call it on a worker thread, never in ``async def``."""
    if not config.ocr_enabled():
        return "Text recognition is turned off on this server."
    path = config.tessdata_dir()
    reason = _static_reason(path)
    if reason:
        return reason
    key = os.fspath(path)
    with _check_lock:
        if key not in _checked:
            _checked[key] = _try_engine(path)
    return _checked[key] or None


def _try_engine(path) -> str:
    try:
        _new_api(path).End()
        return ""
    except ImportError as e:
        log.warning(f"[ocr] the engine does not load: {e}")
        return "The text recognition engine does not load on this server."
    except (OSError, RuntimeError) as e:
        log.warning(f"[ocr] the engine does not start: {e}")
        return "The text recognition engine does not start on this server."


def engine() -> str:
    """The engine's name and version as Tesseract gives it ("tesseract
    5.5.4"), the first line of tesserocr's ``tesseract_version``, which
    goes on to list its image libraries. What a recognized file records."""
    return (_tesserocr().tesseract_version().splitlines() or ["tesseract"])[0].strip()


_locale_lock = threading.Lock()


@contextmanager
def _utf8_file_names(path: str):
    """The block with the C runtime reading narrow file names as UTF-8, on
    Windows for a ``path`` outside ASCII (an install under a user name with
    an accent, which French and German users have): tesserocr hands
    Tesseract the path as UTF-8, and Tesseract opens its models with
    fopen, which reads a narrow name in the ANSI code page unless the
    locale's is UTF-8 (the Universal CRT since Windows 10 1803, which
    Python and the wheel's Tesseract share). A short (8.3) name is no way
    out: it keeps the accented letters. Tesseract reads its files only
    while it is set up, so the locale is set for that alone, and put back.
    Elsewhere, and where the CRT has no UTF-8 locale, nothing changes."""
    if sys.platform != "win32" or path.isascii():
        yield
        return
    import locale

    with _locale_lock:
        before = locale.setlocale(locale.LC_CTYPE)
        try:
            locale.setlocale(locale.LC_CTYPE, ".UTF8")
        except locale.Error:
            before = None
        try:
            yield
        finally:
            if before is not None:
                locale.setlocale(locale.LC_CTYPE, before)


def _new_api(path):
    """One engine set up with the models in ``path``: page segmentation
    left to Tesseract (PSM.AUTO; the scans are papers in columns), its
    chatter on the console ("Estimating resolution", "Empty page!!") sent
    to the null device. RuntimeError when the models do not open."""
    tess = _tesserocr()
    text = os.fspath(path)
    with _utf8_file_names(text):
        return tess.PyTessBaseAPI(path=text, lang=LANGS, psm=tess.PSM.AUTO, variables={"debug_file": os.devnull})


# --- The engines -------------------------------------------------------------
# An engine holds its models (about 50 MB for the three) and is not
# thread-safe, so a few are kept loaded and each is lent to one page at a
# time: at most config.ocr_workers(), made on first use. ``reset`` ends them.
_pool = threading.Condition()
_idle: list = []     # engines ready, of _gen
_made = 0            # engines of _gen alive, idle or lent
_gen = 0             # bumped by ``reset`` and by a change of the tessdata path
_path = None         # the tessdata path the engines of _gen were set up with


def _take():
    """An engine for one page and the generation it belongs to, waiting
    while all ``config.ocr_workers()`` are lent."""
    global _made, _gen, _path
    path = os.fspath(config.tessdata_dir())
    ended = []
    with _pool:
        if path != _path:
            ended, _idle[:] = list(_idle), []
            _gen, _path, _made = _gen + 1, path, 0
            _pool.notify_all()
        while not _idle and _made >= config.ocr_workers():
            _pool.wait()
        gen = _gen
        api = _idle.pop() if _idle else None
        if api is None:
            _made += 1
    _end(ended)
    if api is None:
        try:
            api = _new_api(path)
        except BaseException:
            _give(None, gen, False)
            raise
    return api, gen


def _give(api, gen: int, keep: bool) -> None:
    """An engine back from a page: kept for the next when ``keep`` and its
    generation is current, else ended."""
    global _made
    with _pool:
        current = gen == _gen
        if current and keep and api is not None:
            _idle.append(api)
            _pool.notify()
            return
        if current:
            _made -= 1
            _pool.notify()
    _end([api] if api is not None else [])


def _end(apis) -> None:
    for api in apis:
        try:
            api.End()
        except Exception:  # noqa: BLE001 — an engine that will not end is let go
            pass


def reset() -> None:
    """End every idle engine and forget the checks (tests). An engine lent
    to a page now is ended when it comes back."""
    global _gen, _made
    with _pool:
        ended, _idle[:] = list(_idle), []
        _gen, _made = _gen + 1, 0
        _pool.notify_all()
    _end(ended)
    with _check_lock:
        _static.clear()
        _checked.clear()


def recognize(raster):
    """The words Tesseract reads on ``raster`` (pdf_text's ``Raster``, grey
    or RGB, drawn at ``raster.scale`` pixels per point): its lines in
    Tesseract's reading order, each a list of words ``(text, x0, y0, x1,
    y1, confidence)``, the box in the raster's pixels (top-left origin), the
    confidence 0 to 100; a line holds at least one word. [] for a page with
    no words; None when the page has no result: recognition failed, timed
    out (``PAGE_TIMEOUT_MS``) or read past ``MAX_WORDS`` words or a word
    past ``MAX_WORD_CHARS`` characters (logged). Blocking for seconds; the
    GIL is free meanwhile. ``check`` first: an engine that does not load
    answers None here."""
    try:
        api, gen = _take()
    except Exception as e:  # noqa: BLE001 — no engine: the page has no result
        log.warning(f"[ocr] no engine for the page: {e}")
        return None
    keep = False
    try:
        if raster.channels not in (1, 3, 4):
            raise ValueError(f"a raster of {raster.channels} channels")
        api.SetImageBytes(raster.data, raster.width, raster.height, raster.channels, raster.stride)
        api.SetSourceResolution(max(1, round(72 * raster.scale)))
        if not api.Recognize(timeout=PAGE_TIMEOUT_MS):
            keep = True
            log.warning(f"[ocr] a page was not recognized (timed out or failed) at {raster.width}x{raster.height}")
            return None
        lines = _lines(api.GetIterator(), _tesserocr())
        keep = True
        return lines
    except _TooMuch as e:
        keep = True
        log.warning(f"[ocr] a page read {e}: left without a result")
        return None
    except Exception as e:  # noqa: BLE001 — that page has no result; the run goes on
        log.warning(f"[ocr] recognition of a page failed: {e}")
        return None
    finally:
        if keep:
            try:
                api.Clear()  # the page's results and picture; the models stay
            except Exception:  # noqa: BLE001
                keep = False
        _give(api, gen, keep)


class _TooMuch(Exception):
    """A page read past MAX_WORDS words or a word past MAX_WORD_CHARS."""


def _lines(iterator, tess) -> list:
    """``recognize``'s walk of the result ``iterator`` (None: no result
    to walk, an empty page), word by word in reading order, a new line at
    each word that begins one. A word's text loses its white space (a word
    is what lies between two spaces of the page's text, for the server's
    glyphs and the viewer's tiles alike); a word left empty, or without a
    box, is dropped, and a line left without words. A blank page gives an
    iterator whose one element has no text, which tesserocr raises for
    ("No text returned"): that is an empty word."""
    if iterator is None:
        return []
    ril = tess.RIL
    lines, line, count = [], None, 0
    for word in tess.iterate_level(iterator, ril.WORD):
        if line is None or word.IsAtBeginningOf(ril.TEXTLINE):
            line = []
            lines.append(line)
        try:
            raw = word.GetUTF8Text(ril.WORD)
        except RuntimeError:
            raw = ""
        text = "".join((raw or "").split())
        if not text:
            continue
        box = word.BoundingBox(ril.WORD)
        if not box:
            continue
        if len(text) > MAX_WORD_CHARS:
            raise _TooMuch(f"a word of {len(text)} characters")
        count += 1
        if count > MAX_WORDS:
            raise _TooMuch(f"more than {MAX_WORDS} words")
        x0, y0, x1, y1 = (int(v) for v in box)
        line.append((text, x0, y0, x1, y1, round(float(word.Confidence(ril.WORD)), 1)))
    return [line for line in lines if line]
