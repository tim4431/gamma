"""The notes index (``block_fts`` in pages.db, db.BLOCK_FTS_SCHEMA): triggers
on the block table keep it inside every write's own transaction, so a search
inside the transaction already finds what it wrote and one right after any
writer does too — no refresher, nothing pending — while a rolled-back write
leaves nothing. What is indexed: every block inside a page, highlights
included, not a page's own row; a page in Recently deleted keeps its rows
and no search reaches them. An op batch pays for the rows it touched only;
a connection without the textnorm function cannot write a block; the
rebuild is the recovery path — after every restore, and through
/api/search-reindex. (The suite's autouse check, conftest.py
``notes_index_drift``, holds every workspace a test wrote to it.)"""

import json
import sqlite3
import zipfile
from contextlib import closing

import pytest

from conftest import login, make_page, make_user, notes_index_drift, workspace_of
from gamma import block_index, ops, ws_backup
from gamma.blocks_store import STORED_COLUMNS, write_lock
from gamma.db import NOTES_INDEX_CHARS, connect_pages_db, register_functions, ws_db_path
from gamma.textnorm import normalize_text

USER, PW = "ni_owner", "pw-ni-1"
OLD = "2024-01-01T00:00:00.000000Z"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PW)
    return login(USER, PW)


def _ops(c, page, batch):
    r = c.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": batch})
    assert r.status_code == 200, r.text
    return r.json()


def _hits(q, ws=None):
    """``[(block_id, page_id)]`` the notes index finds for ``q`` in the
    library's pages, as a search reads it."""
    with connect_pages_db(ws or workspace_of(USER)) as conn:
        pages = [r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root'")]
        return sorted((b, p) for b, p, _ in block_index.search_blocks(conn, block_index.fts_query(q), 50, pages))


def _indexed(q, ws=None):
    """``[block_id]`` of every row the index holds for ``q``, whatever page
    it is in (a page in Recently deleted too)."""
    with connect_pages_db(ws or workspace_of(USER)) as conn:
        return sorted(r[0] for r in conn.execute(
            "SELECT block_id FROM block_fts WHERE block_fts MATCH ?", (block_index.fts_query(q),)))


def _search(c, q):
    r = c.get("/api/search", params={"q": q})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_write_is_found_inside_its_own_transaction(owner):
    ws = workspace_of(USER)
    page = make_page(owner, "Transaction page")["id"]
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        conn.execute(f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES (?, ?, 'a0', ?, '{{}}', ?, ?, ?)",
                     ("niTx", page, "a quokka mid-write", OLD, OLD, page))
        found = block_index.search_blocks(conn, block_index.fts_query("quokka"), 5, [page])
        assert [(b, p) for b, p, _ in found] == [("niTx", page)]
        conn.rollback()
        assert block_index.search_blocks(conn, block_index.fts_query("quokka"), 5, [page]) == []
        assert not notes_index_drift(conn)


def test_every_writer_is_searchable_at_once(owner):
    """Op inserts, edits and deletes, a move within the page and to another
    page, the subtree replace, an import, trashing and restoring a page,
    deleting it for good: a search right after each reads what it left."""
    ws = workspace_of(USER)
    page = make_page(owner, "Wombat page")["id"]
    other = make_page(owner, "Other page")["id"]
    assert _hits("wombat") == []  # a page's own title is not indexed

    _ops(owner, page, [{"op": "insert", "id": "niA", "parent": page, "content": "a wombat at dusk"},
                       {"op": "insert", "id": "niB", "parent": "niA", "content": "a nested wombat"},
                       {"op": "insert", "id": "niH", "parent": page, "content": "wombat highlight",
                        "props": {"pdf_position": {"pageNumber": 1}}}])
    assert _hits("wombat") == [("niA", page), ("niB", page), ("niH", page)]
    assert _search(owner, "wombat")["indexing"] == 0

    _ops(owner, page, [{"op": "set", "id": "niA", "content": "an echidna at dusk"}])
    assert _hits("wombat") == [("niB", page), ("niH", page)] and _hits("echidna") == [("niA", page)]
    _ops(owner, page, [{"op": "set", "id": "niA", "props": {"collapsed": True}}])  # properties only
    assert _hits("echidna") == [("niA", page)]
    _ops(owner, page, [{"op": "move", "id": "niB", "parent": page}])
    assert _hits("nested") == [("niB", page)]
    _ops(owner, page, [{"op": "delete", "id": "niH"}])
    assert _hits("highlight") == []

    # to another page: the subtree's rows read the new page
    assert owner.post("/api/blocks/niA/reorder", json={"parent_id": other}).status_code == 200
    assert _hits("echidna") == [("niA", other)]

    # the subtree replace under a nested block
    r = owner.put("/api/blocks/niB/children", json={"blocks": [{"content": "a numbat below",
                                                                "children": [{"content": "a deeper numbat"}]}]})
    assert r.status_code == 200, r.text
    assert len(_hits("numbat")) == 2 and {p for _, p in _hits("numbat")} == {page}

    # an import: a new page with its blocks
    r = owner.post("/api/import/markdown", files={"file": ("bilby.md", b"- a bilby burrow\n  - a bilby kit\n",
                                                           "text/markdown")})
    assert r.status_code == 200, r.text
    imported = r.json()["block_id"]
    assert [p for _, p in _hits("bilby")] == [imported, imported]

    # Recently deleted: the rows stay, no search reaches them; restored, found again
    assert owner.delete(f"/api/blocks/{page}").status_code == 200
    assert _hits("numbat") == [] and len(_indexed("numbat")) == 2
    assert _search(owner, "numbat")["results"] == []
    assert owner.post(f"/api/trash/{page}/restore").status_code == 200
    assert len(_hits("numbat")) == 2

    # deleted for good: the rows go with the blocks
    assert owner.delete(f"/api/blocks/{page}").status_code == 200
    assert owner.delete(f"/api/trash/{page}").status_code == 200
    assert _indexed("numbat") == [] and _indexed("nested") == []
    with connect_pages_db(ws) as conn:
        assert not notes_index_drift(conn)


def test_a_batch_pays_for_the_rows_it_touched(owner):
    """The index's per-row bookkeeping (FTS5's docsize table, one write per
    row in and out) moves only for the rows an op changes the indexed text
    or place of — not for the rest of the page, not for a properties-only
    edit."""
    ws = workspace_of(USER)
    page = make_page(owner, "Big page")["id"]
    _ops(owner, page, [{"op": "insert", "id": f"niBig{i}", "parent": page, "content": f"row {i}"}
                       for i in range(200)])

    def rows_written(batch):
        seen = []
        with connect_pages_db(ws) as conn:
            conn.set_trace_callback(seen.append)
            ops.apply_ops(conn, page, batch, actor="")
        return len([s for s in seen if "block_fts_docsize" in s])

    assert rows_written([{"op": "set", "id": "niBig7", "content": "row seven"}]) == 2  # out, in
    assert rows_written([{"op": "set", "id": "niBig7", "props": {"color": "red"}}]) == 0
    assert rows_written([{"op": "move", "id": "niBig8", "parent": "niBig7"}]) == 2
    assert rows_written([{"op": "delete", "id": "niBig9"}]) == 1


def test_a_block_write_needs_the_textnorm_function(owner):
    ws = workspace_of(USER)
    page = make_page(owner, "Guarded page")["id"]
    with closing(sqlite3.connect(ws_db_path(ws, "pages.db"), timeout=10)) as conn:
        with pytest.raises(sqlite3.OperationalError, match="textnorm"):
            conn.execute(f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES ('niBare', ?, 'a0', 'bare', "
                         "'{}', ?, ?, ?)", (page, OLD, OLD, page))
        conn.rollback()
    assert owner.get("/api/blocks/niBare").status_code == 404


def _drift_the_index(path, block_id, text):
    """Rewrite a block's text behind the index's back — what an index built
    under other textnorm rules amounts to: the index holds what the rows no
    longer make."""
    with closing(sqlite3.connect(str(path), timeout=10)) as conn:
        register_functions(conn)
        conn.execute("DROP TRIGGER block_fts_update")
        conn.execute("UPDATE unified_blocks SET content = ? WHERE id = ?", (text, block_id))
        conn.commit()
        assert notes_index_drift(conn)


def test_search_reindex_rebuilds_the_notes_index(owner):
    ws = workspace_of(USER)
    page = make_page(owner, "Drifted page")["id"]
    _ops(owner, page, [{"op": "insert", "id": "niDr", "parent": page, "content": "a dingo den"}])
    _drift_the_index(ws_db_path(ws, "pages.db"), "niDr", "a dingo pup")
    r = owner.post("/api/search-reindex")
    assert r.status_code == 200, r.text
    with connect_pages_db(ws) as conn:  # the trigger is back (rebuild puts the schema in place) and the index matches
        assert not notes_index_drift(conn)
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'block_fts_update'").fetchone()
    assert [h["block_id"] for h in _search(owner, "pup")["results"]] == ["niDr"]
    assert _search(owner, "den")["results"] == []


def _rewritten(zpath, out, change):
    """A copy of the backup zip ``zpath`` at ``out`` whose pages.db went
    through ``change(path)``."""
    with zipfile.ZipFile(zpath) as z:
        names = {n: z.read(n) for n in z.namelist()}
    db = out.with_suffix(".pages.db")
    db.write_bytes(names["pages.db"])
    change(db)
    names["pages.db"] = db.read_bytes()
    with zipfile.ZipFile(out, "w") as z:
        for n, data in names.items():
            z.writestr(n, data)
    return out


def test_a_restore_builds_the_backups_index_again(tmp_path):
    """A backup whose index does not match its rows (built under other
    textnorm rules) is indexed afresh on the way in: replaced, merged."""
    make_user("ni_restore", PW)
    c = login("ni_restore", PW)
    ws = workspace_of("ni_restore")
    page = make_page(c, "Restored page")["id"]
    _ops(c, page, [{"op": "insert", "id": "niRs", "parent": page, "content": "a koala asleep"}])
    snap = ws_backup.create(ws, label="ni", uploads=False)
    zpath = _rewritten(ws_backup.backup_path(ws, snap["name"]), tmp_path / "drifted.zip",
                       lambda db: _drift_the_index(db, "niRs", "a koala awake"))

    ws_backup.restore_zip(ws, zpath, "replace")
    assert _hits("awake", ws) == [("niRs", page)] and _hits("asleep", ws) == []
    with connect_pages_db(ws) as conn:
        assert not notes_index_drift(conn)

    make_user("ni_merge", PW)
    ws2 = workspace_of("ni_merge")
    assert ws_backup.restore_zip(ws2, zpath, "merge")["pages_added"] == 1
    assert _hits("awake", ws2) == [("niRs", page)] and _hits("asleep", ws2) == []


def test_results_match_a_plain_fts_table_of_the_normalized_rows(owner):
    """The view hands FTS5 every non-root block, its text normalized and cut
    at NOTES_INDEX_CHARS, so the hits and snippets are those of a plain FTS5
    table holding exactly those texts."""
    texts = ["the wallaby considered 3,000-qubit systems", "a coherent wallaby",
             "wallaby ﬁne print", "wallaby-level error correction", "nothing to see",
             "wallaby " + "x" * NOTES_INDEX_CHARS + " beyond the cut"]
    page = make_page(owner, "Fixture page")["id"]
    _ops(owner, page, [{"op": "insert", "id": "niW0", "parent": page, "content": texts[0]}])
    _ops(owner, page, [{"op": "insert", "id": f"niW{i}", "parent": "niW0", "content": t}
                       for i, t in enumerate(texts[1:], start=1)])
    ref = sqlite3.connect(":memory:")
    ref.execute("CREATE VIRTUAL TABLE ref USING fts5(block_id UNINDEXED, page_id UNINDEXED, content)")
    ref.executemany("INSERT INTO ref VALUES (?, ?, ?)",
                    [(f"niW{i}", page, normalize_text(t)[:NOTES_INDEX_CHARS]) for i, t in enumerate(texts)])
    for q in ("wallaby", "3000 qubit", "fine", "error correction", "beyond"):
        hits = {(h["block_id"], h["snippet"]) for h in _search(owner, q)["results"] if h["page_id"] == page}
        expected = set(ref.execute(
            f"SELECT block_id, snippet(ref, 2, '', '', '…', {block_index.SNIPPET_TOKENS}) FROM ref "
            "WHERE ref MATCH ?", (block_index.fts_query(q),)).fetchall())
        assert hits == expected, q
    assert _hits("beyond") == []  # past the cut


def test_an_older_backup_brings_its_chats_into_pages_db(tmp_path):
    """A backup from before migration step 28 holds its chats in data.db,
    and a notes index of its own there: restored, replaced or merged (all of
    it, or what a review selected), the chats are in pages.db, the old
    index is gone and the new one finds the notes."""
    pages_db, data_db = tmp_path / "pages.db", tmp_path / "data.db"
    with closing(sqlite3.connect(str(pages_db))) as conn:
        conn.execute("CREATE TABLE unified_blocks (id TEXT PRIMARY KEY, parent_id TEXT, position TEXT NOT NULL, "
                     "content TEXT NOT NULL DEFAULT '', properties TEXT NOT NULL DEFAULT '{}', "
                     "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.executemany("INSERT INTO unified_blocks VALUES (?, ?, 'a0', ?, '{}', ?, ?)", [
            ("root", None, "", OLD, OLD), ("niOldPage", "root", "Old page", OLD, OLD),
            ("niOldNote", "niOldPage", "an old platypus", OLD, OLD)])
        conn.commit()
    with closing(sqlite3.connect(str(data_db))) as conn:
        conn.execute("CREATE TABLE chats (block_id TEXT PRIMARY KEY, messages TEXT NOT NULL, "
                     "updated_at TEXT NOT NULL)")  # the oldest shape: no title
        conn.execute("CREATE TABLE chat_history (id TEXT PRIMARY KEY, bucket TEXT NOT NULL, title TEXT NOT NULL "
                     "DEFAULT '', messages TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.executemany("INSERT INTO chats VALUES (?, ?, ?)", [
            ("niOldPage", json.dumps([{"role": "user", "text": "about the page"}]), OLD),
            ("home", json.dumps([{"role": "user", "text": "about the library"}]), OLD)])
        conn.execute("INSERT INTO chat_history VALUES ('niOldEntry', 'niOldPage', 'Before', ?, ?, ?)",
                     (json.dumps([{"role": "user", "text": "earlier"}]), OLD, OLD))
        conn.execute("CREATE VIRTUAL TABLE block_fts USING fts5(block_id UNINDEXED, page_id UNINDEXED, content)")
        conn.execute("INSERT INTO block_fts VALUES ('niOldNote', 'niOldPage', 'a stale text')")
        conn.commit()
    zpath = tmp_path / "old.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(pages_db, "pages.db")
        z.write(data_db, "data.db")

    def chats_of(c):
        return (c.get("/api/chats/niOldPage").json()["messages"], c.get("/api/chats/home").json()["messages"],
                [s["id"] for s in c.get("/api/chat-history", params={"bucket": "niOldPage"}).json()["sessions"]])

    everything = ([{"role": "user", "text": "about the page"}], [{"role": "user", "text": "about the library"}],
                  ["niOldEntry"])
    for name, mode in (("ni_old_replace", "replace"), ("ni_old_merge", "merge")):
        make_user(name, PW)
        c, ws = login(name, PW), workspace_of(name)
        ws_backup.restore_zip(ws, zpath, mode)
        assert chats_of(c) == everything, mode
        assert [h["block_id"] for h in _search(c, "platypus")["results"]] == ["niOldNote"]
        assert _search(c, "stale")["results"] == []
        with closing(sqlite3.connect(ws_db_path(ws, "data.db"))) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not tables & {"chats", "chat_history", "block_fts"}, mode

    # the review lists the page (its chats with it) and the library chat on its own
    make_user("ni_old_review", PW)
    c, ws = login("ni_old_review", PW), workspace_of("ni_old_review")
    planned = {p["selection_ids"][0]: p for p in ws_backup.preview_zip(ws, zpath)["pages"]}
    assert set(planned) == {"page:niOldPage", "chat:home"}
    assert planned["chat:home"]["action"] == "create" and planned["chat:home"]["source_paths"] == ["pages.db"]
    result = ws_backup.restore_zip(ws, zpath, "merge", selected={"page:niOldPage"})
    assert result["chats_added"] == 2  # the page's conversation and its earlier one; not the library's
    messages, library, history = chats_of(c)
    assert (messages, library, history) == (everything[0], [], ["niOldEntry"])
    assert ws_backup.preview_zip(ws, zpath)["pages"][0]["action"] == "skip"  # nothing new left but the library chat
