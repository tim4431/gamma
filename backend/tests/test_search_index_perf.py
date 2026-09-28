"""The search indexes at scale (gamma/block_index.py, gamma/pdf_index.py): a
page's or a paper's rows are found through the ``*_rows`` side tables and
deleted by rowid (a filter on the UNINDEXED page_id / doc_id column reads the
whole FTS table — O(n²) over a rebuild), an index an older Gamma wrote is
taken over without duplicates, each page is rebuilt in its own short
transaction, a search rebuilds only for a moment and leaves the rest to the
background refresher, an op batch gets its page re-indexed without any
search, and pruning reads the bookkeeping only. Search results stay what a
full rebuild gives."""

import re
import sqlite3
import time
from contextlib import closing

import pytest

from conftest import login, make_page, make_user, workspace_of
from gamma import block_index, pdf_index
from gamma.blocks_store import fetch_subtree
from gamma.db import connect_pages_db, ws_db_path
from gamma.textnorm import INDEX_VERSION, normalize_text

USER, PASSWORD = "sip_owner", "pw-sip-1"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


def _block(c, parent, content, props=None):
    r = c.post("/api/blocks", json={"parent_id": parent, "content": content, "properties": props or {}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _search(c, q, **params):
    r = c.get("/api/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _delete_page(c, page_id):
    """Delete a page for good: to Recently deleted, then out of it."""
    assert c.delete(f"/api/blocks/{page_id}").status_code == 200
    assert c.delete(f"/api/trash/{page_id}").status_code == 200


def _data(ws):
    return closing(sqlite3.connect(ws_db_path(ws, "data.db")))


def _page_rows(ws, page_id):
    """(block_id, content) of the page's index rows, found by the slow scan on
    purpose: whatever the side table says, these are the rows really there."""
    with _data(ws) as conn:
        return sorted(conn.execute("SELECT block_id, content FROM block_fts WHERE page_id = ?", (page_id,)))


def _expected_rows(ws, page_id):
    with closing(connect_pages_db(ws)) as conn:
        rows = fetch_subtree(conn, page_id)
    return sorted((r[0], normalize_text(r[3])[:block_index.MAX_BLOCK_CHARS])
                  for r in rows if r[0] != page_id and normalize_text(r[3] or ""))


@pytest.fixture
def traced(monkeypatch):
    """Every statement run on the data.db connections the indexes open."""
    seen: list[str] = []
    real = block_index.connect_data_db

    def connect(ws):
        conn = real(ws)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(block_index, "connect_data_db", connect)
    return seen


def _ours(statements, pattern):
    return [s for s in statements if re.search(pattern, s)]


def test_a_rebuilt_page_replaces_its_rows_by_rowid(owner, traced):
    ws = workspace_of(USER)
    page = make_page(owner, "Rowid page")
    other = make_page(owner, "Bystander page")
    blocks = [_block(owner, page["id"], f"wombat fact {i}") for i in range(3)]
    _block(owner, other["id"], "wombat bystander")
    _search(owner, "wombat")
    assert _page_rows(ws, page["id"]) == _expected_rows(ws, page["id"])

    traced.clear()
    assert owner.put(f"/api/blocks/{blocks[0]}", json={"content": "echidna fact"}).status_code == 200
    assert owner.delete(f"/api/blocks/{blocks[1]}").status_code == 200
    hits = _search(owner, "fact")["results"]
    assert {h["block_id"] for h in hits} == {blocks[0], blocks[2]}
    # The rebuild found the page's old rows through the side table and
    # deleted them one rowid at a time; nothing filtered block_fts by page_id.
    assert _ours(traced, r"DELETE FROM block_fts WHERE rowid")
    assert not _ours(traced, r"FROM block_fts\b(?!_)[^;]*\bpage_id\s*=")
    # No duplicates, no leftovers, the bystander untouched.
    assert _page_rows(ws, page["id"]) == _expected_rows(ws, page["id"])
    assert _page_rows(ws, other["id"]) == _expected_rows(ws, other["id"])
    with _data(ws) as conn:
        tracked = conn.execute("SELECT count(*) FROM block_fts_rows WHERE page_id = ?", (page["id"],)).fetchone()[0]
    assert tracked == len(_expected_rows(ws, page["id"]))


def test_each_page_is_its_own_short_transaction(owner, traced, monkeypatch):
    pages = [make_page(owner, f"Txn page {i}")["id"] for i in range(3)]
    for p in pages:
        _block(owner, p, "platypus note")
    traced.clear()
    assert len(_search(owner, "platypus")["results"]) == 3
    # One BEGIN IMMEDIATE ... COMMIT per rebuilt page, never one for the batch.
    assert len(_ours(traced, r"^BEGIN IMMEDIATE")) >= 3
    assert len(_ours(traced, r"^COMMIT")) >= 3


def test_search_rebuilds_for_a_moment_and_the_background_finishes(owner, monkeypatch):
    monkeypatch.setattr(block_index, "QUIET_S", 60)      # no help from the commit listener
    monkeypatch.setattr(block_index, "REFRESH_BUDGET_S", 0)  # nothing inline
    # The refresher is one thread for the whole process: in a full run it
    # still has the earlier modules' workspaces queued, each served for a
    # slice in turn. Give it a queue of this test's own (theirs comes back
    # when the test ends), so the wait below is for this workspace alone.
    monkeypatch.setattr(block_index, "_due", {})
    ws = workspace_of(USER)
    page = make_page(owner, "Background page")
    wanted = _block(owner, page["id"], "quokka sighting")
    body = _search(owner, "quokka")
    assert body["indexing"] >= 1 and body["results"] == []   # not rebuilt in the request
    deadline = time.monotonic() + 60  # a slice it may still be busy with, on a loaded machine
    while _search(owner, "quokka")["indexing"]:
        assert time.monotonic() < deadline, ("the background refresher did not finish",
                                             block_index._due, block_index._worker)
        time.sleep(0.05)
    assert [h["block_id"] for h in _search(owner, "quokka")["results"]] == [wanted]
    assert _page_rows(ws, page["id"]) == _expected_rows(ws, page["id"])


def test_the_refresher_serves_workspaces_in_turn(monkeypatch):
    """A workspace with a page due later (an edit waiting for QUIET_S) goes
    to the back of the queue once served like any other, so a long backlog
    of one never starves the rest."""
    past = time.monotonic() - 1
    queue = {"ws-a": {None: past, "page-later": past + 3600}, "ws-b": {None: past}}
    monkeypatch.setattr(block_index, "_due", queue)
    with block_index._wake:  # the running refresher waits outside while we take turns
        assert block_index._take_due() == ("ws-a", None)
        queue["ws-a"][None] = past  # its slice left work: re-queued
        assert block_index._take_due() == ("ws-b", None)
        assert block_index._take_due() == ("ws-a", None)
    assert queue == {"ws-a": {"page-later": past + 3600}}


def test_a_written_page_is_indexed_without_a_search(owner, monkeypatch):
    monkeypatch.setattr(block_index, "QUIET_S", 0.05)
    ws = workspace_of(USER)
    page = make_page(owner, "Listener page")
    _block(owner, page["id"], "numbat burrow")
    deadline = time.monotonic() + 15
    while True:
        with _data(ws) as conn:
            row = conn.execute("SELECT ver FROM block_fts_meta WHERE page_id = ?", (page["id"],)).fetchone()
        if row and row[0] == INDEX_VERSION and _page_rows(ws, page["id"]) == _expected_rows(ws, page["id"]):
            break
        assert time.monotonic() < deadline, "the commit listener never had the page indexed"
        time.sleep(0.05)


def test_pruning_reads_the_bookkeeping_only(owner, traced):
    ws = workspace_of(USER)
    page = make_page(owner, "Doomed index page")
    _block(owner, page["id"], "bilby burrow")
    _search(owner, "bilby")
    traced.clear()
    _delete_page(owner, page["id"])
    assert _page_rows(ws, page["id"]) == []
    with _data(ws) as conn:
        assert not conn.execute("SELECT 1 FROM block_fts_meta WHERE page_id = ?", (page["id"],)).fetchone()
        assert not conn.execute("SELECT 1 FROM block_fts_rows WHERE page_id = ?", (page["id"],)).fetchone()
    assert not _ours(traced, r"DISTINCT page_id FROM block_fts\b")
    assert _search(owner, "bilby")["results"] == []


def test_an_index_written_before_the_side_table_is_taken_over(monkeypatch):
    """The layout an older Gamma wrote: block_fts + block_fts_meta, no
    block_fts_rows, and a page whose bookkeeping row its mark_page_dirty had
    deleted. Taken over on first use: every row tracked, nothing doubled
    when the pages are rebuilt, the orphan's rows pruned with its page."""
    monkeypatch.setattr(block_index, "QUIET_S", 60)
    make_user("sip_legacy", "pw-sip-2")
    c = login("sip_legacy", "pw-sip-2")
    ws = workspace_of("sip_legacy")
    kept = make_page(c, "Kept page")
    orphan = make_page(c, "Orphan page")
    _block(c, kept["id"], "dingo den")
    _block(c, orphan["id"], "dingo tracks")
    with closing(connect_pages_db(ws)) as conn:
        stamps = dict(conn.execute("SELECT id, updated_at FROM unified_blocks WHERE parent_id = 'root'"))
    with _data(ws) as conn:
        for table in ("block_fts_rows", "block_fts_meta", "block_fts"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("CREATE VIRTUAL TABLE block_fts USING fts5(block_id UNINDEXED, page_id UNINDEXED, content)")
        conn.execute("CREATE TABLE block_fts_meta (page_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, "
                     "ver INTEGER NOT NULL DEFAULT 0)")
        for page in (kept, orphan):
            for block_id, text in _expected_rows(ws, page["id"]):
                conn.execute("INSERT INTO block_fts (block_id, page_id, content) VALUES (?, ?, ?)",
                             (block_id, page["id"], text))
        conn.execute("INSERT INTO block_fts_meta VALUES (?, ?, ?)", (kept["id"], stamps[kept["id"]], INDEX_VERSION))
        conn.commit()

    hits = _search(c, "dingo")["results"]  # takes over, then rebuilds the orphan (no meta row = stale)
    assert sorted(h["page_id"] for h in hits) == sorted([kept["id"], orphan["id"]])
    for page in (kept, orphan):
        assert _page_rows(ws, page["id"]) == _expected_rows(ws, page["id"])
    with _data(ws) as conn:
        assert conn.execute("SELECT count(*) FROM block_fts_rows").fetchone()[0] == \
            conn.execute("SELECT count(*) FROM block_fts").fetchone()[0]
    # A second edit rebuilds by rowid again — still one row per block.
    _block(c, orphan["id"], "dingo pups")
    _search(c, "dingo")
    assert _page_rows(ws, orphan["id"]) == _expected_rows(ws, orphan["id"])
    _delete_page(c, orphan["id"])
    assert _page_rows(ws, orphan["id"]) == []


def test_results_are_what_a_full_rebuild_gives(owner):
    """The notes MATCH runs over exactly the rows the old rebuild wrote: same
    blocks, same normalized text, so the same hits and snippets."""
    ws = workspace_of(USER)
    texts = ["the wallaby considered 3,000-qubit systems", "a coherent wallaby",
             "wallaby ﬁne print", "wallaby-level error correction", "nothing to see"]
    page = make_page(owner, "Fixture page")
    parent = _block(owner, page["id"], texts[0])
    for t in texts[1:]:
        _block(owner, parent, t)
    for q in ("wallaby", "3000 qubit", "fine", "error correction"):
        hits = {(h["block_id"], h["snippet"]) for h in _search(owner, q)["results"] if h["page_id"] == page["id"]}
        # The reference: the same rows in a fresh FTS table, built the old way.
        ref = sqlite3.connect(":memory:")
        ref.execute("CREATE VIRTUAL TABLE block_fts USING fts5(block_id UNINDEXED, page_id UNINDEXED, content)")
        ref.executemany("INSERT INTO block_fts (block_id, page_id, content) VALUES (?, ?, ?)",
                        [(b, page["id"], t) for b, t in _expected_rows(ws, page["id"])])
        expected = {(b, snip) for b, _, snip in block_index.search_blocks(
            ref, block_index.fts_query(q), 20, [page["id"]])}
        assert hits == expected and hits, q


# --- the PDF index ------------------------------------------------------------------

def _pdf_rows(ws, doc_id):
    with _data(ws) as conn:
        return sorted(conn.execute("SELECT page, content FROM pdf_fts WHERE doc_id = ?", (doc_id,)))


def test_a_paper_is_replaced_and_dropped_by_rowid(owner, monkeypatch):
    ws = workspace_of(USER)
    monkeypatch.setattr(pdf_index, "STORE_CHUNK", 2)  # several transactions for one paper
    seen: list[str] = []
    with closing(sqlite3.connect(ws_db_path(ws, "data.db"))) as conn:
        conn.set_trace_callback(seen.append)
        pdf_index.store_doc(conn, "sipdoc01", [(1, "old one"), (2, "old two"), (3, "old three")])
        pdf_index.store_doc(conn, "sipdoc02", [(1, "neighbour")])
        pdf_index.store_doc(conn, "sipdoc01", [(1, "new one"), (3, "new three"), (4, "new four"),
                                               (5, "new five"), (7, "new seven")])
        assert pdf_index.doc_pages(conn, "sipdoc01", 5) == [(1, "new o"), (3, "new t"), (4, "new f"),
                                                            (5, "new f"), (7, "new s")]
        assert pdf_index.pdf_missing(conn, ["sipdoc01", "sipdoc02"]) == []
        assert pdf_index.doc_chars(conn)["sipdoc01"] == sum(len(t) for t in
                                                             ("new one", "new three", "new four", "new five", "new seven"))
        pdf_index.drop_docs(conn, ["sipdoc01"])
        conn.commit()
        assert pdf_index.pdf_missing(conn, ["sipdoc01"]) == ["sipdoc01"]
    assert _pdf_rows(ws, "sipdoc01") == []
    assert _pdf_rows(ws, "sipdoc02") == [(1, "neighbour")]
    assert _ours(seen, r"DELETE FROM pdf_fts WHERE rowid")
    assert not _ours(seen, r"FROM pdf_fts\b(?!_)[^;]*\bdoc_id\s*=")
    assert len(_ours(seen, r"^BEGIN IMMEDIATE")) >= 3 + 2  # the 5-page paper alone: delete, 3 chunks, stamp


def test_purge_keeps_papers_a_block_still_carries(owner):
    """The PDF rows of a deleted page go; a paper some other block still
    carries (only the full pass over properties can tell) stays."""
    ws = workspace_of(USER)
    gone = make_page(owner, "Paper to delete", properties={"doc_id": "sipdoc10"})
    holder = make_page(owner, "Holder page")
    _block(owner, holder["id"], "a nested carrier", {"doc_id": "sipdoc11"})
    with closing(sqlite3.connect(ws_db_path(ws, "data.db"))) as conn:
        pdf_index.store_doc(conn, "sipdoc10", [(1, "doomed text")])
        pdf_index.store_doc(conn, "sipdoc11", [(1, "kept text")])
    _delete_page(owner, gone["id"])
    assert _pdf_rows(ws, "sipdoc10") == []
    assert _pdf_rows(ws, "sipdoc11") == [(1, "kept text")]
