"""The notes index's query half: ``block_fts`` in the workspace's pages.db,
kept current by triggers inside every write's own transaction
(``db.BLOCK_FTS_SCHEMA`` says what is indexed and the two rules that keep
it exact).

Every block inside a page is indexed under its page, highlights included
(their note text is a note like any other; the quoted PDF passage is in the
PDF index, ``pdf_fts`` in data.db, gamma/pdf_index.py) — a page's own row
is not. Text is indexed in its search form (gamma.textnorm, the PDF index's
rules too) so one query matches both. A page in Recently deleted keeps its
rows; a search reads only the pages it reaches. ``rebuild`` is the whole
recovery path: migration step 28 and every restore run it
(normalize.block_fts), and so does Settings' rebuild (POST
/api/search-reindex).

No positions are stored: the frontend re-finds the match in the block text.
"""

import json
import sqlite3

from .textnorm import normalize_text

SNIPPET_TOKENS = 14


def fts_query(q: str) -> str:
    """User text → safe FTS5 MATCH: AND of quoted terms, prefix on the last.
    Normalized first so "3,000" and "3000" build the same query the indexes
    store. Shared by the PDF and the block index (same tokenizer, same rules)."""
    terms = [t for t in normalize_text(q).split(" ") if t]
    if not terms:
        return ""
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    quoted[-1] += "*"
    return " ".join(quoted)


def search_blocks(conn: sqlite3.Connection, match: str, limit: int,
                  page_ids) -> list[tuple[str, str, str]]:
    """bm25-ranked ``(block_id, page_id, snippet)`` hits for an FTS MATCH on
    a pages.db connection, in the pages ``page_ids`` only — the pages the
    search reaches (a scope may be a folder; a page in Recently deleted is
    none of them). A malformed MATCH is no results, not an error."""
    if not match or limit <= 0:
        return []
    try:
        return conn.execute(
            f"SELECT block_id, page_id, snippet(block_fts, 2, '', '', '…', {SNIPPET_TOKENS}) FROM block_fts "
            "WHERE block_fts MATCH ? AND page_id IN (SELECT value FROM json_each(?)) ORDER BY rank LIMIT ?",
            (match, json.dumps(list(page_ids)), limit)).fetchall()
    except sqlite3.OperationalError:
        return []


def rebuild(conn: sqlite3.Connection) -> None:
    """Build the notes index again from the block rows (FTS5's ``rebuild``),
    inside the caller's transaction, which the caller commits."""
    conn.execute("INSERT INTO block_fts (block_fts) VALUES ('rebuild')")
