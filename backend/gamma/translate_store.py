"""Kept translations: the paragraphs the PDF translated view translated and
the papers' briefs, kept in the workspace's data.db so that a paper read
again shows its translation without asking a model (and survives a
restart, which the in-memory cache of gamma/translate.py does not).

Only a page whose PDF is stored in the workspace has them (``doc_id`` is
the stored file's id), per target language and model: the bare model name,
as the memory cache keys it, or ``engine:<id>`` for a machine-translation
service. A paragraph is found by its source text's hash (``src_hash``), so
a page segmented again finds its paragraphs whatever their order.

Derived data, like the document text index beside it, though it comes back
only by paying a model again: the tables come from ``db.DATA_SCHEMA``,
which every data.db connection applies; the rows go with their document
(``purge``, from ``search.index.purge_unused``); and a kept translation is
never the reason a translation fails: every function logs its own errors
and carries on as if nothing were kept. routers/translate.py decides who
writes (the request's ``keep``, an account that may change the workspace);
docs/dev/translation.md describes the whole path.
"""

import hashlib

from .db import connect_data_db, page_now
from .logbuf import log

# Paragraphs kept per document, language and model. A paper is a few
# hundred to a few thousand; past this many something is translating far
# more than one paper's text under its id, and new rows are skipped.
MAX_ROWS = 20000
_full: set = set()  # (ws, doc_id) already warned about MAX_ROWS in this process

# A row made with the paper's brief is never replaced by one made without,
# unless the reader asked for the paragraph afresh (_REPLACE).
_REPLACE = ("INSERT INTO pdf_translations (doc_id, lang, model, src, text, brief, at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (doc_id, lang, model, src) DO UPDATE SET text = excluded.text, "
            "brief = excluded.brief, at = excluded.at")
_UPSERT = _REPLACE + " WHERE excluded.brief >= pdf_translations.brief"
_ONE = "doc_id = ? AND lang = ? AND model = ?"


def src_hash(text: str, stamp: str = "") -> str:
    """A source text's name in the store: over the text exactly as sent, so
    it matches whatever block indices the page has, and the stamp of the
    glossary terms it uses (gamma/translate_terms.py), so a changed term
    names the texts that use it anew. A text that uses none ("") is named
    as it was before there were glossaries."""
    return hashlib.sha256((f"{text}\x00{stamp}" if stamp else text).encode()).hexdigest()[:32]


def _marks(srcs) -> str:
    """An IN list's placeholders, one per src: at most one request's texts
    (routers/translate.py)."""
    return ",".join("?" * len(srcs))


def lookup(ws: str, doc_id: str, lang: str, model: str, srcs, need_brief: bool = False) -> dict:
    """{src_hash: translation} for the kept ones among ``srcs``; with
    ``need_brief`` only those made with the paper's brief."""
    srcs = list(dict.fromkeys(srcs))
    if not srcs:
        return {}
    try:
        with connect_data_db(ws) as conn:
            return dict(conn.execute(f"SELECT src, text FROM pdf_translations WHERE {_ONE} AND brief >= ? "
                                     f"AND src IN ({_marks(srcs)})", (doc_id, lang, model, int(need_brief), *srcs)))
    except Exception as e:  # noqa: BLE001 — derived data: translate as if none were kept
        log.warning(f"[translate_store] reading kept translations failed: {e}")
        return {}


def put(ws: str, doc_id: str, lang: str, model: str, rows, brief: bool, replace: bool = False) -> None:
    """Keep ``rows`` (``(src_hash, translation)`` pairs), made with the
    paper's brief or not. ``replace``: they were asked for afresh ("Translate
    again"), so they replace what is kept whatever it was made with. Past
    MAX_ROWS for the document, language and model only the rows already kept
    are written again."""
    rows = dict(rows)
    if not rows:
        return
    one = (doc_id, lang, model)
    try:
        with connect_data_db(ws) as conn:
            count = conn.execute(f"SELECT COUNT(*) FROM pdf_translations WHERE {_ONE}", one).fetchone()[0]
            if count + len(rows) > MAX_ROWS:
                have = {r[0] for r in conn.execute(
                    f"SELECT src FROM pdf_translations WHERE {_ONE} AND src IN ({_marks(rows)})", (*one, *rows))}
                room = MAX_ROWS - count
                fits = {}
                for src, text in rows.items():
                    if src in have:
                        fits[src] = text
                    elif room > 0:
                        fits[src] = text
                        room -= 1
                if len(fits) < len(rows) and (ws, doc_id) not in _full:
                    _full.add((ws, doc_id))
                    log.warning(f"[translate_store] {doc_id} has {MAX_ROWS} kept paragraphs in {lang} "
                                f"by {model}; not keeping more")
                rows = fits
            at = page_now()
            conn.executemany(_REPLACE if replace else _UPSERT, [(*one, src, text, int(brief), at) for src, text in rows.items()])
    except Exception as e:  # noqa: BLE001 — derived data: the translation is answered all the same
        log.warning(f"[translate_store] keeping translations failed: {e}")


def get_brief(ws: str, doc_id: str, lang: str, model: str) -> str | None:
    """The paper's kept brief (rendered, as the system prompt carries it)."""
    try:
        with connect_data_db(ws) as conn:
            row = conn.execute(f"SELECT brief FROM pdf_translation_briefs WHERE {_ONE}", (doc_id, lang, model)).fetchone()
    except Exception as e:  # noqa: BLE001 — derived data: as if none were kept
        log.warning(f"[translate_store] reading a kept brief failed: {e}")
        return None
    return row[0] if row else None


def put_brief(ws: str, doc_id: str, lang: str, model: str, brief: str) -> None:
    try:
        with connect_data_db(ws) as conn:
            conn.execute("INSERT OR REPLACE INTO pdf_translation_briefs (doc_id, lang, model, brief, at) "
                         "VALUES (?, ?, ?, ?, ?)", (doc_id, lang, model, brief, page_now()))
    except Exception as e:  # noqa: BLE001 — derived data
        log.warning(f"[translate_store] keeping a brief failed: {e}")


def _documents(conn) -> set:
    """The ids of every document with kept translations or a kept brief. The
    paragraphs' table is walked one document at a time down its primary
    key, never row by row: its rows carry the text."""
    docs = {r[0] for r in conn.execute("SELECT DISTINCT doc_id FROM pdf_translation_briefs")}
    doc = ""
    while row := conn.execute("SELECT doc_id FROM pdf_translations WHERE doc_id > ? "
                              "ORDER BY doc_id LIMIT 1", (doc,)).fetchone():
        doc = row[0]
        docs.add(doc)
    return docs


def stats(ws: str) -> dict:
    """What the workspace keeps: {documents, paragraphs, bytes}, the bytes
    being the kept text's UTF-8 size, translations and briefs."""
    try:
        with connect_data_db(ws) as conn:
            paragraphs, size = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(text AS BLOB))), 0) FROM pdf_translations").fetchone()
            size += conn.execute(
                "SELECT COALESCE(SUM(LENGTH(CAST(brief AS BLOB))), 0) FROM pdf_translation_briefs").fetchone()[0]
            documents = len(_documents(conn))
    except Exception as e:  # noqa: BLE001 — derived data: reads as nothing kept
        log.warning(f"[translate_store] counting kept translations failed: {e}")
        return {"documents": 0, "paragraphs": 0, "bytes": 0}
    return {"documents": documents, "paragraphs": paragraphs, "bytes": size}


def _delete(conn, doc_id: str) -> int:
    conn.execute("DELETE FROM pdf_translation_briefs WHERE doc_id = ?", (doc_id,))
    return conn.execute("DELETE FROM pdf_translations WHERE doc_id = ?", (doc_id,)).rowcount


def clear(ws: str) -> int:
    """Drop the workspace's kept translations and briefs; how many
    paragraphs went."""
    try:
        with connect_data_db(ws) as conn:
            conn.execute("DELETE FROM pdf_translation_briefs")
            return conn.execute("DELETE FROM pdf_translations").rowcount
    except Exception as e:  # noqa: BLE001 — derived data
        log.warning(f"[translate_store] clearing kept translations failed: {e}")
        return 0


def forget(ws: str, doc_id: str) -> int:
    """Drop one document's kept translations and briefs, in every language
    and model; how many paragraphs went."""
    try:
        with connect_data_db(ws) as conn:
            return _delete(conn, doc_id)
    except Exception as e:  # noqa: BLE001 — derived data
        log.warning(f"[translate_store] forgetting {doc_id}'s translations failed: {e}")
        return 0


def purge(conn, live_docs) -> int:
    """Drop the kept translations and briefs of documents not in
    ``live_docs``, on the caller's data.db connection (search.index.purge_unused
    commits). How many documents went."""
    try:
        stale = [d for d in _documents(conn) if d not in live_docs]
        for doc_id in stale:
            _delete(conn, doc_id)
        return len(stale)
    except Exception as e:  # noqa: BLE001 — derived data: the next sweep tries again
        log.warning(f"[translate_store] dropping translations of unused papers failed: {e}")
        return 0
