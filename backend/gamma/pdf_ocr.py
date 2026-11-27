"""Text recognized from a stored PDF's scanned pages: the store, and the
view every PDF text reader takes it through. The runs that make it are
gamma/ocr_run.py's.

The text is kept in the workspace's data.db, keyed by the stored file's
hash (doc_id) like the other derived data there: ``pdf_ocr``, a row a
file, and ``pdf_ocr_pages``, a row a page (db.DATA_SCHEMA describes the
columns). A page's words are kept as lines, each word ``[text, x0, y0, x1,
y1]`` in 1/10000 of the page as shown (``UNITS``: x of its width, y of its
height, top-left origin), the frame of highlight rects and of pdf.js
viewports at scale 1, so the viewer and the server place them alike
whatever the page's rotation, crop box or /UserUnit.

A run writes each page it recognizes *staged* (``stage_page``), seen by no
reader; one transaction then applies them all (``apply``), so the file's
text changes at once, never page by page, and a run stopped, failed or cut
short by a restart before that changes nothing anyone sees. Its staged
pages stay, and the next run goes on from them. The apply leaves the file
``pending`` until its finishing steps have run (``finished``); a forget
deletes its pages again (``unapply``).

The readers (gamma/pdf_text.py) find a path's recognized text with
``view_of``: on the pages it applied to, the page text, the glyphs under a
highlight, the scan test of the translation brief and the title guess read
the recognized words in place of the file's own layer; every other page
reads the file as before.
"""

import gzip
import json
import os
import secrets
import sqlite3

from . import config, db
from .logbuf import log
from .ocr_engine import LANGS
from .pdf_text import OCR_UNITS as UNITS

# Bump when what is stored changes shape or meaning, with a reader for the
# old rows or a plan for them: rows of another version are read by nobody
# (``view_of``, ``state``), so a bare bump turns every recognized file back
# to its layer while its index and its respelled quotes still hold the
# recognized words.
VERSION = 1
# The most pages ``pages_lines`` reads in one call.
MAX_RANGE = 64
# The pages an OcrView loads from data.db in one read, from the one asked for on.
VIEW_WINDOW = 32


def page_text(lines) -> str:
    """A page's text from its recognized ``lines``: the words of a line
    joined by a space, every line ending in "\\n", the last one too; "" for
    no lines. Exactly what the viewer's pageText gives for the same words
    (a line's last item ends it), and what the server's glyphs spell
    (pdf_text ``GlyphPages``), so a quote and a search hit line up both
    ways."""
    return "".join(" ".join(word[0] for word in line) + "\n" for line in lines)


def clean_lines(lines, conf: bool = False) -> list:
    """``lines`` as stored: each word's text a string without white space,
    its box whole numbers within the page, the edges in order; empty words
    and empty lines dropped. ``conf``: each word keeps its confidence too,
    a sixth element from 0 to 100 (0 for a word without one), as the page
    rules read it. ValueError for a shape that is no lines of words."""
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
            if conf:
                words[-1].append(min(100.0, max(0.0, float(word[5]))) if len(word) > 5 else 0.0)
        if words:
            out.append(words)
    return out


def _pack(lines) -> bytes:
    return gzip.compress(json.dumps({"lines": lines}, ensure_ascii=False, separators=(",", ":")).encode(), 6)


def _unpack(blob) -> list:
    return json.loads(gzip.decompress(blob))["lines"]


def has_data_db(ws: str) -> bool:
    """Whether the workspace's data.db exists: a read never makes one (a
    workspace being deleted, a path that only looks like a workspace's)."""
    try:
        return os.path.isfile(db.ws_db_path(ws, "data.db"))
    except ValueError:
        return False


_LIVE = "applied = 1 AND kept = 0 AND ver = ? AND langs = ?"
_CURRENT = "ver = ? AND langs = ?"


# --- Writes (a run's) ----------------------------------------------------------
# Each opens its own transaction, the write lock first (BEGIN IMMEDIATE, as
# db.set_page_snap does), and raises when it cannot write: a run that cannot
# keep its pages fails rather than claim them.

def set_scanned(ws: str, doc_id: str, pages: int, scanned: int, kinds: dict | None = None) -> None:
    """Record the file's page count and how many of its pages are scans or
    pictures (the run's reading phase) and, given, which (``kinds``,
    ``classify``'s {page: "scan" | "picture"}: what ``plan_of`` plans a
    run in a window from), making the file's row when it has none: no
    stamp yet, nothing applied. What ``stage_page`` needs first."""
    stored = None if kinds is None else json.dumps({str(page): kind for page, kind in sorted(kinds.items())},
                                                   separators=(",", ":"))
    with db.connect_data_db(ws) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO pdf_ocr (doc_id, engine, langs, ver, pages, scanned, kinds, rev, stamp, pending, at) "
            "VALUES (?, '', ?, ?, ?, ?, ?, 0, '', 0, ?) "
            "ON CONFLICT (doc_id) DO UPDATE SET pages = excluded.pages, scanned = excluded.scanned, "
            "kinds = excluded.kinds",
            (doc_id, LANGS, VERSION, int(pages), int(scanned), stored, db.page_now()))
        conn.commit()


def stage_page(ws: str, doc_id: str, page: int, lines, *, shared: float | None = None,
               kept: bool = False, engine: str = "") -> bool:
    """Keep page ``page``'s recognized ``lines`` (``to_lines``'s shape),
    staged: read by nobody until ``apply``. ``kept``: the page keeps its own
    text, a layer or a picture page's words (its lines are not kept);
    ``shared``: the share of the layer's
    words the recognition read too, None for a page without a layer;
    ``engine``: what read it (ocr_engine ``engine()``, or a window's
    ``BROWSER_ENGINE``). One
    transaction. A staged page of an earlier run is replaced; a page
    already applied at this VERSION and LANGS is not (False: a run never
    reads such a page again). ValueError when the file has no row
    (``set_scanned`` first) or ``lines`` are no lines of words."""
    words = _pack([] if kept else clean_lines(lines))
    with db.connect_data_db(ws) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM pdf_ocr WHERE doc_id = ?", (doc_id,)).fetchone() is None:
            raise ValueError(f"no recognition row for {doc_id}: set_scanned first")
        cur = conn.execute(
            "INSERT INTO pdf_ocr_pages (doc_id, page, ver, langs, applied, kept, shared, words, engine) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?) "
            "ON CONFLICT (doc_id, page) DO UPDATE SET ver = excluded.ver, langs = excluded.langs, applied = 0, "
            "kept = excluded.kept, shared = excluded.shared, words = excluded.words, engine = excluded.engine "
            "WHERE NOT (pdf_ocr_pages.applied = 1 AND pdf_ocr_pages.ver = ? AND pdf_ocr_pages.langs = ?)",
            (doc_id, int(page), VERSION, LANGS, int(bool(kept)), shared, words, engine, VERSION, LANGS))
        conn.commit()
        return cur.rowcount > 0


def apply(ws: str, doc_id: str, engine: str = "") -> dict | None:
    """Make the file's staged pages live, all at once, in ONE transaction:
    each staged page applied (one that records no engine taking
    ``engine``); when one of them has words (not ``kept``), the file's row
    takes a new stamp, its rev moves on, it records the engines its live
    pages were read with (" + " between two: a run stopped on the server
    and gone on in a window), LANGS and VERSION and turns ``pending`` (its
    finishing steps are due: ``finished``), and the document's row in the
    text index is marked stale (ver 0), so a crash before the re-index
    leaves it to the lazy indexer. → ``{"stamp", "rev", "pages",
    "pending"}``, ``pages`` the pages that went live now (none when every
    staged page kept its layer, which changes nothing anyone reads: no new
    stamp then); None when nothing was staged."""
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
        conn.execute(f"UPDATE pdf_ocr_pages SET applied = 1, engine = CASE WHEN engine = '' THEN ? ELSE engine END "
                     f"WHERE doc_id = ? AND applied = 0 AND {_CURRENT}", (engine, doc_id, VERSION, LANGS))
        live = sorted(page for page, kept in staged if not kept)
        if live:
            rev, stamp, pending = rev + 1, secrets.token_hex(8), 1
            engines = " + ".join(r[0] for r in conn.execute(
                f"SELECT DISTINCT engine FROM pdf_ocr_pages WHERE doc_id = ? AND {_LIVE} AND engine != '' "
                f"ORDER BY engine", (doc_id, VERSION, LANGS)))
            conn.execute("UPDATE pdf_ocr SET engine = ?, langs = ?, ver = ?, rev = ?, stamp = ?, pending = 1, "
                         "at = ? WHERE doc_id = ?", (engines, LANGS, VERSION, rev, stamp, db.page_now(), doc_id))
            _index_stale(conn, doc_id)
        conn.commit()
    return {"stamp": stamp, "rev": rev, "pages": live, "pending": bool(pending)}


def _index_stale(conn, doc_id: str) -> None:
    """The document's row in the text index marked stale (ver 0), in the
    caller's transaction, so a crash before the re-index leaves it to the
    lazy indexer. The index's tables are search/index.py's own
    (index.SCHEMA), made by the first search."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'text_fts_docs'").fetchone():
        conn.execute("UPDATE text_fts_docs SET ver = 0 WHERE doc_id = ?", (doc_id,))


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


def unapply(ws: str, doc_id: str) -> dict | None:
    """Forget what the file's recognition kept, in ONE transaction: every
    page row of it deleted (live, kept or staged, of any VERSION), so
    every reader reads its own layer again and the next run reads every
    scanned page anew; its row kept (what its pages are: ``pages``,
    ``scanned``, ``kinds``) with no engine, a new stamp (the readers'
    caches and the index's ``ocr_stamp`` never take the old text for the
    new), its rev moved on and ``pending`` on (its finishing steps are due:
    the re-index, the briefs dropped), and the document's index row marked
    stale, as ``apply`` does. → ``{"stamp", "rev", "pages"}``, ``pages``
    the pages that read recognized text before; None when the file has no
    row."""
    with db.connect_data_db(ws) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT rev FROM pdf_ocr WHERE doc_id = ?", (doc_id,)).fetchone()
        if row is None:
            conn.rollback()
            return None
        pages = [r[0] for r in conn.execute(f"SELECT page FROM pdf_ocr_pages WHERE doc_id = ? AND {_LIVE} "
                                            f"ORDER BY page", (doc_id, VERSION, LANGS))]
        conn.execute("DELETE FROM pdf_ocr_pages WHERE doc_id = ?", (doc_id,))
        rev, stamp = row[0] + 1, secrets.token_hex(8)
        conn.execute("UPDATE pdf_ocr SET engine = '', rev = ?, stamp = ?, pending = 1, at = ? WHERE doc_id = ?",
                     (rev, stamp, db.page_now(), doc_id))
        _index_stale(conn, doc_id)
        conn.commit()
    return {"stamp": stamp, "rev": rev, "pages": pages}


def _never_stored(_doc_id: str) -> bool:
    return False


# What a file's recognition keeps, its pages first: ``purge`` and ``drop``
# delete a file's rows of each (the automatic recognition's mark too).
_TABLES = ("pdf_ocr_pages", "pdf_ocr", "pdf_ocr_auto")


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
        stale = [r[0] for r in conn.execute("SELECT doc_id FROM pdf_ocr UNION SELECT doc_id FROM pdf_ocr_pages "
                                            "UNION SELECT doc_id FROM pdf_ocr_auto")
                 if r[0] not in live_docs]
        stale = [d for d in stale if not stored(d)]
        for table in _TABLES:
            conn.executemany(f"DELETE FROM {table} WHERE doc_id = ?", [(d,) for d in stale])
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
    if not doc_ids or not has_data_db(ws):
        return
    try:
        with db.connect_data_db(ws) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table in _TABLES:
                conn.executemany(f"DELETE FROM {table} WHERE doc_id = ?", [(d,) for d in doc_ids])
            conn.commit()
    except (sqlite3.Error, OSError) as e:
        log.warning(f"[ocr] dropping the recognized text of {len(doc_ids)} deleted file(s) failed: {e}")


# --- Reads -----------------------------------------------------------------------
# Each logs and answers None or nothing when data.db cannot be read: a reader
# then reads the file's own layer, as before any recognition.

def state(ws: str, doc_id: str) -> dict | None:
    """The file's recognition: its row (``engine``, ``langs``, ``ver``,
    ``pages``, ``scanned``, ``kinds`` {page: "scan" | "picture"} or None
    before they were recorded, ``rev``, ``stamp`` "" before the first
    apply, ``pending``, ``at``) and its pages at this VERSION and LANGS:
    ``recognized`` (live, reading the recognition: a page it found no real
    text on among them, read as no text; ``live_counts`` parts them),
    ``kept`` (keeping their own text, applied or not), ``staged`` (not
    kept, not applied yet: a stopped run's), ``applied`` (live, kept or
    not), each sorted, and ``shared`` {page: share} of the pages that had a
    layer. What a run resumes from (it skips every page of the first three
    lists) and what GET /api/ocr answers from. None when the file has no
    row, or data.db cannot be read."""
    if not has_data_db(ws):
        return None
    try:
        with db.connect_data_db(ws) as conn:
            conn.execute("BEGIN")  # one snapshot: the row and its pages as one apply left them
            row = conn.execute("SELECT engine, langs, ver, pages, scanned, kinds, rev, stamp, pending, at "
                               "FROM pdf_ocr WHERE doc_id = ?", (doc_id,)).fetchone()
            pages = conn.execute(f"SELECT page, applied, kept, shared FROM pdf_ocr_pages WHERE doc_id = ? AND "
                                 f"{_CURRENT} ORDER BY page", (doc_id, VERSION, LANGS)).fetchall() if row else []
            conn.commit()
        kinds = None if row is None or row[5] is None else {int(p): k for p, k in json.loads(row[5]).items()}
    except (sqlite3.Error, OSError, ValueError, AttributeError) as e:  # the last two: kinds that do not read
        log.warning(f"[ocr] reading the recognition of {doc_id} failed: {e}")
        return None
    if row is None:
        return None
    engine, langs, ver, total, scanned, _kinds, rev, stamp, pending, at = row
    return {"doc_id": doc_id, "engine": engine, "langs": langs, "ver": ver, "pages": total, "scanned": scanned,
            "kinds": kinds, "rev": rev, "stamp": stamp, "pending": bool(pending), "at": at,
            "recognized": [p for p, applied, kept, _ in pages if applied and not kept],
            "kept": [p for p, _, kept, _ in pages if kept],
            "staged": [p for p, applied, kept, _ in pages if not applied and not kept],
            "applied": [p for p, applied, _, _ in pages if applied],
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
    if not has_data_db(ws):
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
    if not has_data_db(ws):
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
    if not has_data_db(ws):
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
