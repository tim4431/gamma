"""Text recognized from a stored PDF's scanned pages: the store, the reading
every PDF text reader takes it through, and the run that makes it.

The ``ocr`` job (routers/ocr.py) reads the words of a file's scanned pages
from their pictures (gamma/ocr_engine.py) and keeps them in the workspace's
data.db, keyed by the stored file's hash (doc_id) like the other derived
data there: ``pdf_ocr``, a row a file, and ``pdf_ocr_pages``, a row a page
(db.DATA_SCHEMA describes the columns). A page's words are kept as lines,
each word ``[text, x0, y0, x1, y1]`` in 1/10000 of the page as shown
(``UNITS``: x of its width, y of its height, top-left origin), the frame of
highlight rects and of pdf.js viewports at scale 1, so the viewer and the
server place them alike whatever the page's rotation, crop box or
/UserUnit.

A run writes each page it recognizes *staged* (``stage_page``), seen by no
reader; one transaction then applies them all (``apply``), so the file's
text changes at once, never page by page, and a run stopped, failed or cut
short by a restart before that changes nothing anyone sees. Its staged
pages stay, and the next run goes on from them. The apply leaves the file
``pending`` until its finishing steps (the document indexed again, its
kept briefs dropped, its highlights' quotes respelled) have run
(``finished``); one of them failing, or a restart among them, leaves the
text applied and the file pending, and the next run finishes it first.

The readers (gamma/pdf_text.py) find a path's recognized text with
``view_of``: on the pages it applied to, the page text, the glyphs under a
highlight, the scan test of the translation brief and the title guess read
the recognized words in place of the file's own layer; every other page
reads the file as before.
"""

import gzip
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from collections import Counter
from contextlib import closing
from statistics import median

from . import config, db, ocr_engine, pdf_text
from .logbuf import log
from .ocr_engine import LANGS
from .pdf_text import OCR_UNITS as UNITS

# Bump when what is stored changes shape or meaning, with a reader for the
# old rows or a plan for them: rows of another version are read by nobody
# (``view_of``, ``state``), so a bare bump turns every recognized file back
# to its layer while its index and its respelled quotes still hold the
# recognized words.
VERSION = 1
# A scan page keeps its own layer when the recognition read at least this
# share of the layer's words too (docs/research/ocr.md: the good layers
# share 98-99% with a fresh recognition, the ones it helps 82-90%)...
KEEP_SHARED = 0.93
# ...and the recognition of the first SAMPLE layered scan pages decides
# whether the rest is read at all: when all of them keep their layer, the
# file's own text reads well.
SAMPLE = 3
# The most scanned pages one run recognizes (hours on a NAS past it).
MAX_PAGES = 2000
# A page with no layer holds real text when the recognition read at least
# PICTURE_WORDS words of confidence PICTURE_CONF or more; otherwise (a
# cover, a plate, a photo) it keeps the text it shows of its own (a title
# over the picture, a caption, a stamp line), or is stored without words
# when it shows none.
PICTURE_WORDS = 10
PICTURE_CONF = 60
# The most pages ``pages_lines`` reads in one call.
MAX_RANGE = 64
# The pages an OcrView loads from data.db in one read, from the one asked for on.
VIEW_WINDOW = 32
# A scan page's recognition replaces its layer only when it read at least
# this share of the layer's count of words: a recognition that read far
# fewer (a page drawn too small for it, turned sideways, mostly a figure)
# would put less text in place of what the layer has.
MIN_WORDS_READ = 0.5


def page_text(lines) -> str:
    """A page's text from its recognized ``lines``: the words of a line
    joined by a space, every line ending in "\\n", the last one too; "" for
    no lines. Exactly what the viewer's pageText gives for the same words
    (a line's last item ends it), and what the server's glyphs spell
    (pdf_text ``GlyphPages``), so a quote and a search hit line up both
    ways."""
    return "".join(" ".join(word[0] for word in line) + "\n" for line in lines)


def to_lines(recognized, raster) -> list:
    """``ocr_engine.recognize``'s lines (boxes in the raster's pixels) as
    stored lines: each word ``[text, x0, y0, x1, y1]`` in ``UNITS`` of the
    page as shown, through the zoom it was drawn at and the size of what
    was drawn (``raster.scale``, ``raster.width_pt``, ``raster.height_pt``,
    not the pixel size, which MuPDF rounds outward)."""
    w = raster.width_pt * raster.scale or 1.0
    h = raster.height_pt * raster.scale or 1.0
    return [[[word[0], round(UNITS * word[1] / w), round(UNITS * word[2] / h), round(UNITS * word[3] / w),
              round(UNITS * word[4] / h)] for word in line] for line in recognized]


def _clean(lines) -> list:
    """``lines`` as stored: each word's text a string without white space,
    its box whole numbers within the page, the edges in order; empty words
    and empty lines dropped. ValueError for a shape that is no lines of
    words."""
    out = []
    for line in lines:
        words = []
        for word in line:
            text, *box = word[:5]
            text = "".join(str(text).split())
            if not text:
                continue
            if len(box) != 4:
                raise ValueError("a word without its box")
            x0, y0, x1, y1 = (min(UNITS, max(0, int(round(float(v))))) for v in box)
            words.append([text, min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)])
        if words:
            out.append(words)
    return out


def _pack(lines) -> bytes:
    return gzip.compress(json.dumps({"lines": lines}, ensure_ascii=False, separators=(",", ":")).encode(), 6)


def _unpack(blob) -> list:
    return json.loads(gzip.decompress(blob))["lines"]


def _has_data_db(ws: str) -> bool:
    """Whether the workspace's data.db exists: a read never makes one (a
    workspace being deleted, a path that only looks like a workspace's)."""
    try:
        return os.path.isfile(db.ws_db_path(ws, "data.db"))
    except ValueError:
        return False


_LIVE = "applied = 1 AND kept = 0 AND ver = ? AND langs = ?"
_CURRENT = "ver = ? AND langs = ?"


# --- Writes (the run's) --------------------------------------------------------
# Each opens its own transaction, the write lock first (BEGIN IMMEDIATE, as
# db.set_page_snap does), and raises when it cannot write: a run that cannot
# keep its pages fails rather than claim them.

def set_scanned(ws: str, doc_id: str, pages: int, scanned: int) -> None:
    """Record the file's page count and how many of its pages are scans or
    pictures (the run's reading phase), making the file's row when it has
    none: no stamp yet, nothing applied. What ``stage_page`` needs first."""
    with db.connect_data_db(ws) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO pdf_ocr (doc_id, engine, langs, ver, pages, scanned, rev, stamp, pending, at) "
            "VALUES (?, '', ?, ?, ?, ?, 0, '', 0, ?) "
            "ON CONFLICT (doc_id) DO UPDATE SET pages = excluded.pages, scanned = excluded.scanned",
            (doc_id, LANGS, VERSION, int(pages), int(scanned), db.page_now()))
        conn.commit()


def stage_page(ws: str, doc_id: str, page: int, lines, *, shared: float | None = None,
               kept: bool = False) -> bool:
    """Keep page ``page``'s recognized ``lines`` (``to_lines``'s shape),
    staged: read by nobody until ``apply``. ``kept``: the page keeps its own
    text, a layer or a picture page's words (its lines are not kept);
    ``shared``: the share of the layer's
    words the recognition read too, None for a page without a layer. One
    transaction. A staged page of an earlier run is replaced; a page
    already applied at this VERSION and LANGS is not (False: a run never
    reads such a page again). ValueError when the file has no row
    (``set_scanned`` first) or ``lines`` are no lines of words."""
    words = _pack([] if kept else _clean(lines))
    with db.connect_data_db(ws) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM pdf_ocr WHERE doc_id = ?", (doc_id,)).fetchone() is None:
            raise ValueError(f"no recognition row for {doc_id}: set_scanned first")
        cur = conn.execute(
            "INSERT INTO pdf_ocr_pages (doc_id, page, ver, langs, applied, kept, shared, words) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?) "
            "ON CONFLICT (doc_id, page) DO UPDATE SET ver = excluded.ver, langs = excluded.langs, applied = 0, "
            "kept = excluded.kept, shared = excluded.shared, words = excluded.words "
            "WHERE NOT (pdf_ocr_pages.applied = 1 AND pdf_ocr_pages.ver = ? AND pdf_ocr_pages.langs = ?)",
            (doc_id, int(page), VERSION, LANGS, int(bool(kept)), shared, words, VERSION, LANGS))
        conn.commit()
        return cur.rowcount > 0


def apply(ws: str, doc_id: str, engine: str) -> dict | None:
    """Make the file's staged pages live, all at once, in ONE transaction:
    each staged page applied; when one of them has words (not ``kept``),
    the file's row takes a new stamp, its rev moves on, it records
    ``engine``, LANGS and VERSION and turns ``pending`` (its finishing
    steps are due: ``finished``), and the document's row in the text index
    is marked stale (ver 0), so a crash before the re-index leaves it to
    the lazy indexer. → ``{"stamp", "rev", "pages", "pending"}``, ``pages``
    the pages that went live now (none when every staged page kept its
    layer, which changes nothing anyone reads: no new stamp then); None when
    nothing was staged."""
    with db.connect_data_db(ws) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT rev, stamp, pending FROM pdf_ocr WHERE doc_id = ?", (doc_id,)).fetchone()
        staged = conn.execute(
            f"SELECT page, kept FROM pdf_ocr_pages WHERE doc_id = ? AND applied = 0 AND {_CURRENT}",
            (doc_id, VERSION, LANGS)).fetchall() if row else []
        if not staged:
            conn.rollback()
            return None
        rev, stamp, pending = row
        conn.execute(f"UPDATE pdf_ocr_pages SET applied = 1 WHERE doc_id = ? AND applied = 0 AND {_CURRENT}",
                     (doc_id, VERSION, LANGS))
        live = sorted(page for page, kept in staged if not kept)
        if live:
            rev, stamp, pending = rev + 1, secrets.token_hex(8), 1
            conn.execute("UPDATE pdf_ocr SET engine = ?, langs = ?, ver = ?, rev = ?, stamp = ?, pending = 1, "
                         "at = ? WHERE doc_id = ?", (engine, LANGS, VERSION, rev, stamp, db.page_now(), doc_id))
            # The index's tables are search/index.py's own (index.SCHEMA), made by the first search.
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'text_fts_docs'").fetchone():
                conn.execute("UPDATE text_fts_docs SET ver = 0 WHERE doc_id = ?", (doc_id,))
        conn.commit()
    return {"stamp": stamp, "rev": rev, "pages": live, "pending": bool(pending)}


def finished(ws: str, doc_id: str, stamp: str | None = None) -> bool:
    """The file's finishing steps have run: ``pending`` off, when its stamp
    is still ``stamp`` (an apply since then has steps of its own still to
    run; None: whatever it is). Whether a row changed."""
    with db.connect_data_db(ws) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if stamp is None:
            cur = conn.execute("UPDATE pdf_ocr SET pending = 0 WHERE doc_id = ?", (doc_id,))
        else:
            cur = conn.execute("UPDATE pdf_ocr SET pending = 0 WHERE doc_id = ? AND stamp = ?", (doc_id, stamp))
        conn.commit()
        return cur.rowcount > 0


def _never_stored(_doc_id: str) -> bool:
    return False


def purge(conn, live_docs, stored=_never_stored) -> int:
    """Drop the recognized text of documents neither in ``live_docs`` (the
    documents a block carries) nor still stored (``stored(doc_id)``, asked
    only of the recognized documents ``live_docs`` leaves out, so the cost
    follows them, not the library), on the caller's data.db connection
    (search.index.purge_unused commits): the text describes the file,
    which stays a highlight's reference after a replacement until the file
    itself goes (then ``drop``). How many documents went; never raises (the
    next sweep tries again)."""
    try:
        stale = [r[0] for r in conn.execute("SELECT doc_id FROM pdf_ocr UNION SELECT doc_id FROM pdf_ocr_pages")
                 if r[0] not in live_docs]
        stale = [d for d in stale if not stored(d)]
        conn.executemany("DELETE FROM pdf_ocr_pages WHERE doc_id = ?", [(d,) for d in stale])
        conn.executemany("DELETE FROM pdf_ocr WHERE doc_id = ?", [(d,) for d in stale])
        return len(stale)
    except Exception as e:  # noqa: BLE001 — derived data
        log.warning(f"[ocr] dropping the recognized text of unused documents failed: {e}")
        return 0


def drop(ws: str, doc_ids) -> None:
    """Drop the recognized text of the files ``doc_ids``, which left the
    workspace's store with nothing referencing them (gamma/upload_gc.py
    deletes such a file, which no block carries): ``purge``'s sweep for
    those alone, without its pass over every block. data.db is opened only
    when it exists; never raises (logged: the next sweep drops them)."""
    doc_ids = sorted(set(doc_ids))
    if not doc_ids or not _has_data_db(ws):
        return
    try:
        with db.connect_data_db(ws) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany("DELETE FROM pdf_ocr_pages WHERE doc_id = ?", [(d,) for d in doc_ids])
            conn.executemany("DELETE FROM pdf_ocr WHERE doc_id = ?", [(d,) for d in doc_ids])
            conn.commit()
    except (sqlite3.Error, OSError) as e:
        log.warning(f"[ocr] dropping the recognized text of {len(doc_ids)} deleted file(s) failed: {e}")


# --- Reads -----------------------------------------------------------------------
# Each logs and answers None or nothing when data.db cannot be read: a reader
# then reads the file's own layer, as before any recognition.

def state(ws: str, doc_id: str) -> dict | None:
    """The file's recognition: its row (``engine``, ``langs``, ``ver``,
    ``pages``, ``scanned``, ``rev``, ``stamp`` "" before the first apply,
    ``pending``, ``at``) and its pages at this VERSION and LANGS:
    ``recognized`` (live, reading the recognition: a page it found no real
    text on among them, read as no text; ``live_counts`` parts them),
    ``kept`` (keeping their own text, applied or not), ``staged`` (not
    kept, not applied yet: a stopped run's), each sorted, and ``shared``
    {page: share} of the pages that had a layer. What a run resumes from
    (it skips every page of the three lists) and what GET /api/ocr answers
    from. None when the file has no row, or data.db cannot be read."""
    if not _has_data_db(ws):
        return None
    try:
        with db.connect_data_db(ws) as conn:
            conn.execute("BEGIN")  # one snapshot: the row and its pages as one apply left them
            row = conn.execute("SELECT engine, langs, ver, pages, scanned, rev, stamp, pending, at FROM pdf_ocr "
                               "WHERE doc_id = ?", (doc_id,)).fetchone()
            pages = conn.execute(f"SELECT page, applied, kept, shared FROM pdf_ocr_pages WHERE doc_id = ? AND "
                                 f"{_CURRENT} ORDER BY page", (doc_id, VERSION, LANGS)).fetchall() if row else []
            conn.commit()
    except (sqlite3.Error, OSError) as e:
        log.warning(f"[ocr] reading the recognition of {doc_id} failed: {e}")
        return None
    if row is None:
        return None
    engine, langs, ver, total, scanned, rev, stamp, pending, at = row
    return {"doc_id": doc_id, "engine": engine, "langs": langs, "ver": ver, "pages": total, "scanned": scanned,
            "rev": rev, "stamp": stamp, "pending": bool(pending), "at": at,
            "recognized": [p for p, applied, kept, _ in pages if applied and not kept],
            "kept": [p for p, _, kept, _ in pages if kept],
            "staged": [p for p, applied, kept, _ in pages if not applied and not kept],
            "shared": {p: shared for p, _, _, shared in pages if shared is not None}}


def page_lines(ws: str, doc_id: str, page: int) -> list | None:
    """The recognized lines of one live page; None when the page is not
    live (staged, kept, of another version, or never recognized)."""
    found = pages_lines(ws, doc_id, page, page)
    return found["pages"].get(page) if found else None


def pages_lines(ws: str, doc_id: str, first: int, last: int) -> dict | None:
    """The live pages among ``first``..``last`` (1-based, at most
    ``MAX_RANGE`` of them from ``first``) and the stamp they are of, read
    in one snapshot: ``{"stamp", "pages": {page: lines}}``; ``{"stamp": "",
    "pages": {}}`` for a file with nothing recognized; None when data.db
    cannot be read."""
    first = max(1, int(first))
    last = min(int(last), first + MAX_RANGE - 1)
    if not _has_data_db(ws):
        return {"stamp": "", "pages": {}}
    try:
        with db.connect_data_db(ws) as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT stamp FROM pdf_ocr WHERE doc_id = ?", (doc_id,)).fetchone()
            rows = conn.execute(f"SELECT page, words FROM pdf_ocr_pages WHERE doc_id = ? AND page BETWEEN ? AND ? "
                                f"AND {_LIVE} ORDER BY page", (doc_id, first, last, VERSION, LANGS)).fetchall()
            conn.commit()
        return {"stamp": row[0] if row else "", "pages": {page: _unpack(words) for page, words in rows}}
    except (sqlite3.Error, OSError, ValueError, OverflowError) as e:  # OverflowError: a page past SQLite's integers
        log.warning(f"[ocr] reading the recognized pages of {doc_id} failed: {e}")
        return None


def stamp(ws: str, doc_id: str) -> str:
    """The stamp of the file's recognized text as its readers read it now:
    "" for none (no row, nothing applied yet, no data.db, which is not
    made, or one that cannot be read, logged). What the search indexer
    records it read a PDF's text with (gamma/search/indexer.py
    ``index_doc``), so a text read before an apply is not stored over the
    re-index of the run that applied (search/index.py ``store_docs``)."""
    if not _has_data_db(ws):
        return ""
    try:
        with db.connect_data_db(ws) as conn:
            return current_stamp(conn, doc_id)
    except (sqlite3.Error, OSError) as e:
        log.warning(f"[ocr] reading the recognition of {doc_id} failed: {e}")
        return ""


def current_stamp(conn, doc_id: str) -> str:
    """``stamp`` read on the caller's data.db connection, inside its
    transaction: "" for no row, and on a data.db without the table (one a
    test or a tool opened without the schema)."""
    try:
        row = conn.execute("SELECT stamp FROM pdf_ocr WHERE doc_id = ?", (doc_id,)).fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return ""
        raise
    return row[0] if row else ""


class OcrView:
    """What the PDF readers read of one file's recognized text
    (``view_of``): its ``stamp`` (the readers' caches key on it), the live
    recognized ``pages`` (a frozenset of 1-based numbers), the file's page
    count as the run read it (``total``), and a live page's ``lines`` and
    ``text``. A page's words are read from data.db when first asked for,
    with the next pages after it (``VIEW_WINDOW``), so a walk over a book
    reads it in a few queries and a single quote one page. An applied
    page's words never change at this VERSION, so a view stays true for
    the pages it lists. One reader at a time."""

    __slots__ = ("ws", "doc_id", "stamp", "pages", "total", "_window")

    def __init__(self, ws: str, doc_id: str, stamp: str, pages, total: int):
        self.ws, self.doc_id, self.stamp = ws, doc_id, stamp
        self.pages = frozenset(pages)
        self.total = total
        self._window: dict = {}  # page → its words' blob, the pages last read

    def lines(self, page: int) -> list | None:
        """Page ``page``'s recognized lines; None when it is not one of
        ``pages``, or its words cannot be read (logged): the reader then
        reads the file's own layer."""
        if page not in self.pages:
            return None
        if page not in self._window:
            try:
                with db.connect_data_db(self.ws) as conn:
                    self._window = dict(conn.execute(
                        f"SELECT page, words FROM pdf_ocr_pages WHERE doc_id = ? AND page BETWEEN ? AND ? "
                        f"AND {_LIVE}", (self.doc_id, page, page + VIEW_WINDOW - 1, VERSION, LANGS)).fetchall())
            except (sqlite3.Error, OSError) as e:
                log.warning(f"[ocr] reading the recognized words of {self.doc_id} failed: {e}")
                self._window = {}
                return None
        blob = self._window.get(page)
        if blob is None:
            return None  # purged since the view was made
        try:
            return _unpack(blob)
        except (OSError, ValueError, KeyError, EOFError) as e:
            log.warning(f"[ocr] the recognized words of {self.doc_id} page {page} do not read: {e}")
            return None

    def text(self, page: int) -> str | None:
        """Page ``page``'s recognized text (``page_text``); None as for ``lines``."""
        lines = self.lines(page)
        return None if lines is None else page_text(lines)


def view_of(src) -> OcrView | None:
    """The recognized text of the PDF at ``src`` (a path) as the readers
    read it, or None: bytes, a path that is no workspace's stored PDF nor
    one linked file's, a file with no live recognized page, or data.db
    that cannot be read (logged). Cheap (path arithmetic, then one indexed
    read of data.db, which is opened only when it exists); never call it
    under pdf_text's lock."""
    if not isinstance(src, (str, os.PathLike)):
        return None
    try:
        found = _resolve(src)
    except Exception as e:  # noqa: BLE001 — a path we cannot place reads as the file's own layer
        log.warning(f"[ocr] placing a PDF's path failed: {e}")
        return None
    if found is None:
        return None
    ws, doc_id = found
    if not _has_data_db(ws):
        return None
    try:
        with db.connect_data_db(ws) as conn:
            conn.execute("BEGIN")
            row = conn.execute("SELECT stamp, pages FROM pdf_ocr WHERE doc_id = ?", (doc_id,)).fetchone()
            pages = [r[0] for r in conn.execute(f"SELECT page FROM pdf_ocr_pages WHERE doc_id = ? AND {_LIVE}",
                                                (doc_id, VERSION, LANGS))] if row and row[0] else []
            conn.commit()
    except (sqlite3.Error, OSError) as e:
        log.warning(f"[ocr] reading the recognition of {doc_id} failed: {e}")
        return None
    if not pages or not row[1]:
        return None
    return OcrView(ws, doc_id, row[0], pages, int(row[1]))


def _resolve(src) -> tuple[str, str] | None:
    """``(ws, doc_id)`` of the stored PDF at the path ``src``: a file
    ``<workspace>/uploads/<doc_id>.pdf`` of the data directory (read now:
    tests move it), placed by its path alone; else a linked file's
    (``_linked``); None for any other path."""
    path = os.path.abspath(os.fspath(src))
    folder, name = os.path.split(path)
    if name.endswith(".pdf") and os.path.basename(folder) == "uploads":
        ws, doc_id = os.path.basename(os.path.dirname(folder)), name[:-4]
        try:
            db.safe_ws_id(ws)
            db.safe_doc_id(doc_id)
        except ValueError:
            pass
        else:
            stored = os.path.abspath(db.ws_uploads_dir(ws) / name)
            if os.path.normcase(path) == os.path.normcase(stored):
                return ws, doc_id
    return _linked(src, path)


def _linked(src, path: str) -> tuple[str, str] | None:
    """``(ws, doc_id)`` of the linked PDF at ``path`` (gamma/linked_files.py),
    on the server that keeps links: the one workspace and name vaults.db
    records for the path (several: None, since the path alone cannot tell
    which file a reader means), and only while the file's size and time
    are as recorded, so what is read is what was hashed. vaults.db is
    opened only when it exists."""
    if not config.folder_links_enabled() or not os.path.isfile(db.VAULTS_DB):
        return None
    spellings = sorted({os.fspath(src), path, str(os.path.normpath(path))})
    marks = ",".join("?" * len(spellings))
    with db.connect_vaults_db() as conn:
        rows = conn.execute(f"SELECT ws, name, size, mtime_ns FROM linked_files WHERE path IN ({marks})",
                            spellings).fetchall()
    if len({(ws, name) for ws, name, *_ in rows}) != 1:
        return None
    ws, name, size, mtime_ns = rows[0]
    if not name.endswith(".pdf"):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    if (st.st_size, st.st_mtime_ns) != (size, mtime_ns):
        return None
    return ws, name[:-4]


def stored_path(ws: str, doc_id: str) -> str | None:
    """The path a run reads the workspace's stored PDF ``doc_id`` from:
    its uploads copy, else the linked file the desktop app reads it from
    (storage ``find_upload_file``); None when there is neither, or when the
    readers could not tell from that path that it is this workspace's file
    (``view_of``: a path two workspaces link), so nothing recognized there
    would ever be read. What POST /api/jobs/ocr asks before it starts a
    run."""
    from .documents.model import stored_name
    from .storage import find_upload_file  # local: storage sits above the readers

    try:
        path = find_upload_file(stored_name(db.safe_doc_id(doc_id), ".pdf"), ws)
        return str(path) if path is not None and _resolve(path) == (ws, doc_id) else None
    except Exception as e:  # noqa: BLE001 — a file we cannot place is no file to recognize
        log.warning(f"[ocr] placing the stored PDF {doc_id} failed: {e}")
        return None


def reads_well(found: dict) -> bool:
    """Whether a file's recognition (``state``) found that its own text
    reads well: the first ``SAMPLE`` scan pages with a layer kept it, each
    sharing ``KEEP_SHARED`` of its words with the recognition (all of
    them, on a file with fewer: every scanned page is done), and no page's
    layer was replaced. A picture page that kept its own text has no layer
    to share (no ``shared``) and counts neither way. A run then reads no
    more of its layered pages, and the Document menu need not offer one."""
    kept, shares = set(found["kept"]), found["shared"]
    layered = kept & set(shares)
    if not layered or not set(shares) <= kept or min(shares.values()) < KEEP_SHARED:
        return False
    scanned = found["scanned"]
    return len(layered) >= SAMPLE or (scanned is not None and scanned <= len(found["recognized"]) + len(kept))


# --- The run ---------------------------------------------------------------------
# The ``ocr`` job's body (routers/ocr.py starts it, one at a time for a file:
# its key is the workspace and the file).

# A run's apply and its finishing steps take the file's turn: a run finishes
# what an interrupted one left due (``pending``) too, and the two must not
# interleave. Striped by file, as gamma/translate.py's turns are.
_TURNS = [threading.Lock() for _ in range(16)]


def _turn(ws: str, doc_id: str) -> threading.Lock:
    return _TURNS[hash((ws, doc_id)) % len(_TURNS)]


# How the study compared a layer with a fresh recognition
# (tools/ocr-study/stage2/score.py ``words``), which KEEP_SHARED was measured
# by: a text's words split at white space, compatibility forms, curly quotes
# and dashes folded, the punctuation at either end of a word dropped, case
# kept, each word counted as often as it comes.
_FOLD = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'", "´": "'",
                       "“": '"', "”": '"', "„": '"', "″": '"', "«": '"', "»": '"',
                       "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
                       "­": None, "​": None, "﻿": None})
_WORD_EDGES_RE = re.compile(r"^[\W_]+|[\W_]+$")


def _words(text: str) -> Counter:
    words = Counter()
    for raw in unicodedata.normalize("NFKC", text or "").translate(_FOLD).split():
        word = _WORD_EDGES_RE.sub("", raw)
        if word:
            words[word] += 1
    return words


def shared_words(layer: str, recognized: str) -> float | None:
    """The share of a page's layer's words (``layer``, its own text) that
    its recognition (``recognized``) read too, a word counted as often as
    both read it (``_words``); None for a layer without a word."""
    have = _words(layer)
    total = sum(have.values())
    return sum((have & _words(recognized)).values()) / total if total else None


def _page_rule(kind: str, found, raster, layer: str) -> tuple[str, list, float | None]:
    """What a page's recognition makes of it: ``(outcome, lines, shared)``.
    ``found`` is ``ocr_engine.recognize``'s (None: no result), ``kind``
    ``pdf_text.page_kinds``' and ``layer`` the page's own text (a picture
    page's: what it shows besides its picture). A scan page's recognition
    replaces its layer ("recognized", its ``lines``) when they share under
    ``KEEP_SHARED`` of the layer's words and the recognition read at least
    ``MIN_WORDS_READ`` of their count; otherwise the page keeps its layer
    ("kept"), ``shared`` saying how much they share. A picture page, or a
    scan page whose layer holds no word, takes its recognition when it
    read real text (``PICTURE_WORDS`` words of confidence ``PICTURE_CONF``
    or more); otherwise a picture page showing words of its own keeps them
    ("kept", no ``shared``: a cover's title, a plate's caption, a stamp
    line), and one showing none is stored without words ("empty": a
    plate, a photo). "failed": no result, nothing kept, the next run reads
    the page again."""
    if found is None:
        return "failed", [], None
    lines = to_lines(found, raster)
    own = _words(layer)
    if kind != "scan" or not own:
        real = sum(1 for line in found for word in line if word[5] >= PICTURE_CONF)
        if real >= PICTURE_WORDS:
            return "recognized", lines, None
        return ("kept", [], None) if own else ("empty", [], None)
    got = _words(page_text(lines))
    total = sum(own.values())
    shared = sum((own & got).values()) / total
    if shared >= KEEP_SHARED or sum(got.values()) < MIN_WORDS_READ * total:
        return "kept", [], shared
    return "recognized", lines, shared


def live_counts(ws: str, doc_id: str, recognized: list) -> tuple[int, int]:
    """``(with words, without)`` of the file's live recognized pages:
    ``recognized`` (``state``'s) parted by their words, read in one pass
    (logged on a read error, every page then counted with words). What a
    run's result and /api/pdf-text-status count pages reading recognized
    text by."""
    try:
        with db.connect_data_db(ws) as conn:
            blobs = [r[0] for r in conn.execute(f"SELECT words FROM pdf_ocr_pages WHERE doc_id = ? AND {_LIVE}",
                                                (doc_id, VERSION, LANGS))]
        empty = sum(1 for blob in blobs if not _unpack(blob))
    except (sqlite3.Error, OSError, ValueError, KeyError, EOFError) as e:
        log.warning(f"[ocr] counting the recognized pages of {doc_id} failed: {e}")
        return len(recognized), 0
    return len(recognized) - empty, empty


def _no_progress(**_) -> None:
    pass


def run(ws: str, doc_id: str, src: str, *, actor: str, progress=None) -> dict:
    """Recognize the text of the scanned pages of the workspace's stored
    PDF ``doc_id``, read from ``src`` (``stored_path``), the ``ocr`` job's
    work; ``actor`` the account its highlight writes go under, ``progress``
    its report (a stop lands at each call, jobs.py).

    1. A file left ``pending`` by an interrupted run is finished first
       (``_finish``), with no report.
    2. Phase ``reading`` (unit pages): every page classified
       (``pdf_text.page_kinds``, one page a turn of the PyMuPDF lock), and
       how many are scans or pictures recorded (``set_scanned``). More than
       ``MAX_PAGES`` of them: ValueError. None: the run ends here.
    3. Phase ``recognizing`` (unit pages, counting from the pages already
       done, staged or live, at this VERSION and LANGS; ``since``, when the
       phase began in ms, and ``from``, the count then, for the time left):
       first the sample, the first ``SAMPLE`` scan pages with a layer, then
       every other scan and picture page not done, each drawn grey without
       its annotations (``ocr_engine.render_zoom``), recognized outside the
       lock and staged by the page rules (``_page_rule``): a failed page
       is not kept and is read again next time. When every page of the
       sample keeps its layer, the file's own text reads well and its other
       layered pages are not read (``reads_well``); its picture pages are.
       A stop lands between two pages and leaves what was staged for the
       next run, unseen.
    4. ``updating``, no longer stoppable: what is staged applied in one
       transaction (``apply``, the file's text changes at once), then the
       finishing steps (``_finish``), under the file's turn. A failure in
       those is logged and the run ends as done: its text is live, and the
       file stays ``pending`` for the next run's step 1.

    → ``{scanned, recognized, kept, empty, failed, good_layer, shared,
    highlights_respelled, highlights_kept, highlights_changed}``: the
    file's scan and picture pages, its pages reading recognized words,
    keeping their own text and recognized without words (live, every
    run's), this run's pages without a result, whether its own text reads
    well and the median share of a layer's words a recognition read too
    (the sample's when it reads well, else every layered page's; None for
    no layer), and what the respell did (``respell_quotes``). ValueError
    for a file that does not open."""
    progress = progress or _no_progress
    respelled = Counter()
    found = state(ws, doc_id)
    if found is not None and found["pending"]:
        with _turn(ws, doc_id):
            respelled.update(_finish(ws, doc_id, actor))

    total = pdf_text.page_count(src)
    if not total:
        raise ValueError("the PDF cannot be read")
    kinds = {}
    progress(done=0, total=total, unit="pages", phase="reading")
    with closing(pdf_text.page_kinds(src)) as walk:
        for page, kind in walk:
            if kind:
                kinds[page] = kind
            progress(done=page, total=total, unit="pages", phase="reading")
    scanned = sorted(kinds)
    set_scanned(ws, doc_id, total, len(scanned))
    if len(scanned) > MAX_PAGES:
        raise ValueError(f"This PDF has {len(scanned)} scanned pages; text recognition reads at most "
                         f"{MAX_PAGES} pages of a file.")

    tally, sample, good_layer = Counter(), [], False
    if scanned:
        found = state(ws, doc_id)
        if found is None:
            raise ValueError("the recognized text cannot be kept now")
        done = set(found["recognized"]) | set(found["kept"]) | set(found["staged"])
        layered = [page for page in scanned if kinds[page] == "scan"]
        sample = layered[:SAMPLE]
        outcome = {page: "kept" if page in found["kept"] else "recognized" for page in sample if page in done}
        shares = dict(found["shared"])
        count = {"done": len(done & set(scanned)), "total": len(scanned)}
        since, start = int(time.time() * 1000), count["done"]

        def report():
            progress(done=count["done"], total=count["total"], unit="pages", phase="recognizing", since=since,
                     **{"from": start})

        def read(pages):
            """Recognize and stage each of ``pages`` in turn, a report after
            each (a stop lands there), each by its own text, read in step
            (``pdf_text.layer_texts``: the file opened once for them, those
            pages alone read, one held at a time); a page that did not draw
            is failed."""
            seen = set()
            rasters = pdf_text.page_rasters(src, pages, ocr_engine.render_zoom, annots=False, grey=True)
            layers = pdf_text.layer_texts(src, pages)  # both follow ``pages``: the rasters skip a page, never reorder
            with closing(rasters), closing(layers):
                for page, raster in rasters:
                    seen.add(page)
                    layer = next((text for at, text in layers if at == page), "")
                    result, lines, shared = _page_rule(kinds[page], ocr_engine.recognize(raster), raster, layer)
                    if result != "failed":
                        stage_page(ws, doc_id, page, lines, shared=shared, kept=result == "kept")
                    if shared is not None:
                        shares[page] = shared
                    outcome[page] = result
                    tally[result] += 1
                    count["done"] += 1
                    report()
            for page in pages:
                if page not in seen:
                    outcome[page] = "failed"
                    tally["failed"] += 1

        report()
        read([page for page in sample if page not in outcome])
        # Kept for sharing its words, not for a recognition that read too few of them.
        good_layer = bool(sample) and all(outcome.get(page) == "kept" and shares.get(page, 0) >= KEEP_SHARED
                                          for page in sample)
        rest = [page for page in scanned if page not in done and page not in outcome
                and (kinds[page] == "picture" or not good_layer)]
        if good_layer:
            count["total"] = count["done"] + len(rest)  # the layered pages left are not read
            report()
        read(rest)

    progress(phase="updating", stoppable=False)
    with _turn(ws, doc_id):
        if scanned:
            apply(ws, doc_id, ocr_engine.engine())
        found = state(ws, doc_id)
        if found is not None and found["pending"]:
            try:
                respelled.update(_finish(ws, doc_id, actor))
            except Exception:  # noqa: BLE001 — the text is live: the run did its work, the next one finishes it
                log.exception(f"[ocr] the finishing steps of {doc_id} failed; it stays pending for the next run")
    found = found or {"recognized": [], "kept": [], "shared": {}}
    words, empty = live_counts(ws, doc_id, found["recognized"])
    shares = found["shared"]
    sampled = [shares[page] for page in sample if page in shares]
    shared = median(sampled) if good_layer and sampled else median(shares.values()) if shares else None
    return {"scanned": len(scanned), "recognized": words, "kept": len(found["kept"]), "empty": empty,
            "failed": tally["failed"], "good_layer": good_layer, "shared": None if shared is None else round(shared, 3),
            "highlights_respelled": respelled["respelled"], "highlights_kept": respelled["kept"],
            "highlights_changed": respelled["changed"]}


def _finish(ws: str, doc_id: str, actor: str) -> dict:
    """The steps due after an apply (the file ``pending``), each harmless
    run again, in the order a reader needs them (under the file's turn):
    the document indexed again for search in this thread (the workspace's
    indexing job drops a start while it runs, and the document would read
    as pending until a search kicked it); its kept briefs dropped and its
    paid paragraph translations kept (gamma/translate.py ``forget_brief``);
    the quotes of the highlights on its recognized pages respelled
    (highlight_relocate ``respell_quotes``, under ``actor``); then
    ``pending`` off, unless an apply since has steps of its own due. A
    failed re-index is left to the lazy indexer (the apply marked the
    document's index row stale); any other failure raises with the file
    still pending, for the next run to finish. → the respell's counts."""
    from . import highlight_relocate, translate  # local: they read PDFs through this module
    from .search import index, indexer  # local: search.index imports this module

    found = state(ws, doc_id)
    if found is None or not found["pending"]:
        return {}
    try:
        indexed = indexer.index_doc(ws, index.Doc(doc_id, "pdf", ".pdf", "store"))
        if indexed is not None:
            with db.connect_data_db(ws) as conn:
                index.store_docs(conn, [indexed])
    except Exception as e:  # noqa: BLE001 — the row is stale: the next search indexes it
        log.warning(f"[ocr] indexing {doc_id} again failed, left to the next search: {e}")
    translate.forget_brief(ws, doc_id)
    counts = highlight_relocate.respell_quotes(ws, doc_id, found["recognized"], actor=actor)
    finished(ws, doc_id, found["stamp"])
    return counts
