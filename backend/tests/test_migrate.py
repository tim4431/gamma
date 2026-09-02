"""gamma/migrate.py — the idempotent normalization of old data shapes: each
step turns a hand-built old-shape row into the new shape, touches nothing
else, and a second run is a no-op with zero counts."""

import json
import sqlite3

from conftest import login, make_page, make_user
from gamma.db import PAGES_SCHEMA, connect_users_db, page_now, user_db_path
from gamma.migrate import (normalize_data_db, normalize_pages_db, normalize_users_db,
                           run_all)

OLD = "2020-01-01T00:00:00.000000Z"


def _pages_db():
    conn = sqlite3.connect(":memory:")
    for stmt in PAGES_SCHEMA:
        conn.execute(stmt)
    conn.execute("INSERT INTO unified_blocks VALUES ('root', NULL, 'a0', '', '{}', ?, ?)", (OLD, OLD))
    return conn


def _insert(conn, block_id, parent, content, props):
    conn.execute(
        "INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, updated_at) "
        "VALUES (?, ?, 'a0', ?, ?, ?, ?)", (block_id, parent, content, json.dumps(props), OLD, OLD))
    conn.commit()


def _row(conn, block_id):
    content, props, updated = conn.execute(
        "SELECT content, properties, updated_at FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
    return content, json.loads(props), updated


def test_source_url_key_is_renamed():
    conn = _pages_db()
    _insert(conn, "p1", "root", "Old page", {"doc_id": "d1", "sourceUrl": "https://x/a.pdf"})
    _insert(conn, "p2", "root", "Both keys", {"sourceUrl": "https://old", "source_url": "https://new"})
    _insert(conn, "p3", "root", "Clean", {"source_url": "https://clean"})
    counts = normalize_pages_db(conn)
    assert counts["source_url_key"] == 2
    content, props, updated = _row(conn, "p1")
    assert props == {"doc_id": "d1", "source_url": "https://x/a.pdf"} and updated != OLD
    _, props, _ = _row(conn, "p2")
    assert props == {"source_url": "https://new"}  # existing source_url wins, old key gone
    _, props, updated = _row(conn, "p3")
    assert props == {"source_url": "https://clean"} and updated == OLD  # untouched
    assert normalize_pages_db(conn) == {"source_url_key": 0, "image_width": 0, "pdf_notes_title": 0}


def test_legacy_image_width_becomes_obsidian_pipe():
    conn = _pages_db()
    _insert(conn, "p", "root", "Page", {})
    _insert(conn, "b1", "p", "see ![cap](/api/uploads/ab12.png){:width 240} here", {})
    _insert(conn, "b2", "p", "already ![cap|240](/api/uploads/ab12.png)", {})
    counts = normalize_pages_db(conn)
    assert counts["image_width"] == 1
    content, _, updated = _row(conn, "b1")
    assert content == "see ![cap|240](/api/uploads/ab12.png) here" and updated != OLD
    assert _row(conn, "b2")[2] == OLD
    assert normalize_pages_db(conn)["image_width"] == 0


def test_pdf_notes_prefix_becomes_auto_title():
    conn = _pages_db()
    _insert(conn, "p1", "root", "PDF Notes - paper.pdf", {"doc_id": "d1"})
    _insert(conn, "p2", "root", "PDF Notes - ", {"doc_id": "d2"})
    _insert(conn, "p3", "root", "PDF Notes - renamed", {"doc_id": "d3", "auto_title": "other"})
    _insert(conn, "p4", "root", "pdf notes - lowercase", {"doc_id": "d4"})
    _insert(conn, "c1", "p1", "PDF Notes - not a page", {})
    counts = normalize_pages_db(conn)
    assert counts["pdf_notes_title"] == 2
    content, props, updated = _row(conn, "p1")
    assert content == "paper.pdf" and props["auto_title"] == "paper.pdf" and updated != OLD
    content, props, _ = _row(conn, "p2")
    assert content == "Untitled" and props["auto_title"] == "Untitled"
    assert _row(conn, "p3")[0] == "PDF Notes - renamed"     # already has a marker: user's title
    assert _row(conn, "p4")[0] == "pdf notes - lowercase"   # LIKE is case-insensitive, the step is not
    assert _row(conn, "c1")[0] == "PDF Notes - not a page"  # root pages only
    assert normalize_pages_db(conn)["pdf_notes_title"] == 0


def test_migrated_auto_title_lets_metadata_rename(guest):
    """The whole point of the marker: after migration the metadata worker's
    compare-and-swap rename (metadata._save_props) still applies."""
    from gamma.routers.metadata import _save_props

    page = make_page(guest, "PDF Notes - old.pdf", properties={"doc_id": "m" * 24})
    with sqlite3.connect(user_db_path("guest", "pages.db")) as conn:
        assert normalize_pages_db(conn)["pdf_notes_title"] == 1
    assert _save_props("guest", page["id"], {"meta": {"title": "Real"}}, auto_title="Real Title") is True
    r = guest.get(f"/api/blocks/{page['id']}")
    assert r.json()["content"] == "Real Title" and "auto_title" not in r.json()["properties"]


def test_data_db_drops_legacy_tables():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE annotations (id TEXT)")
    conn.execute("CREATE TABLE shares (token TEXT)")
    conn.execute("CREATE TABLE chats (block_id TEXT PRIMARY KEY, messages TEXT NOT NULL, updated_at TEXT NOT NULL)")
    assert normalize_data_db(conn) == {"dropped_tables": 2}
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert names == {"chats"}
    assert normalize_data_db(conn) == {"dropped_tables": 0}


def test_users_db_backfills_or_deletes_doc_keyed_shares(guest):
    make_user("mig_owner", "pw")
    with sqlite3.connect(user_db_path("mig_owner", "pages.db")) as conn:
        _insert(conn, "owner_page", "root", "Paper", {"doc_id": "mig_doc"})
    with connect_users_db() as conn:
        for token, doc in (("mig-ok", "mig_doc"), ("mig-gone", "vanished"), ("mig-empty", "")):
            conn.execute(
                "INSERT INTO shares (token, username, doc_id, page_id, created_at) VALUES (?, 'mig_owner', ?, NULL, ?)",
                (token, doc, page_now()))
        conn.execute(
            "INSERT INTO shares (token, username, doc_id, page_id, created_at) VALUES ('mig-keyed', 'mig_owner', '', 'owner_page', ?)",
            (page_now(),))
        conn.commit()
        assert normalize_users_db(conn) == {"shares_backfilled": 1, "shares_deleted": 2}
        rows = dict(conn.execute("SELECT token, page_id FROM shares WHERE username = 'mig_owner'"))
        assert rows == {"mig-ok": "owner_page", "mig-keyed": "owner_page"}
        assert normalize_users_db(conn) == {"shares_backfilled": 0, "shares_deleted": 0}


def test_drop_shares_doc_id_rebuilds_table_once(guest):
    """The stage-3 schema step: by hand only (never in run_all), idempotent,
    and the share endpoints keep working without the column."""
    from gamma.migrate import drop_shares_doc_id

    make_user("drop_owner", "pw")
    owner = login("drop_owner", "pw")
    page = make_page(owner, "Shared", properties={"doc_id": "drop_doc_1"})
    token = owner.post(f"/api/share/{page['id']}").json()["token"]
    with connect_users_db() as conn:
        assert "doc_id" in [r[1] for r in conn.execute("PRAGMA table_info(shares)")]
        # An unkeyed row that can't resolve goes with the column; the keyed one survives.
        conn.execute("INSERT INTO shares (token, username, doc_id, page_id, created_at) "
                     "VALUES ('drop-unkeyed', 'drop_owner', 'vanished', NULL, ?)", (page_now(),))
        conn.commit()
        assert drop_shares_doc_id(conn) is True
        cols = [r[1] for r in conn.execute("PRAGMA table_info(shares)")]
        assert "doc_id" not in cols and "page_id" in cols and "allowed_users" in cols
        assert conn.execute("SELECT page_id FROM shares WHERE token = ?", (token,)).fetchone() == (page["id"],)
        assert not conn.execute("SELECT 1 FROM shares WHERE token = 'drop-unkeyed'").fetchone()
        assert drop_shares_doc_id(conn) is False  # already gone: a no-op
        assert normalize_users_db(conn) == {"shares_backfilled": 0, "shares_deleted": 0}
    # Reads and the adaptive INSERT work without the column.
    r = owner.get(f"/api/share/{token}")
    assert r.status_code == 200 and r.json()["doc_id"] == "drop_doc_1"  # derived from the page
    other = make_page(owner, "Plain")
    r = owner.post(f"/api/share/{other['id']}")
    assert r.status_code == 200, r.text
    assert owner.get(f"/api/share/{r.json()['token']}").json()["doc_id"] == ""
    assert run_all()["shares"] == {}
    # Put the column back so the rest of the suite sees the shipped schema.
    with connect_users_db() as conn:
        conn.execute("ALTER TABLE shares ADD COLUMN doc_id TEXT NOT NULL DEFAULT ''")
        conn.commit()


def test_run_all_reports_only_what_changed(guest):
    """Steps that did nothing are left out of the summary, so a clean data
    directory reports changed == 0 (the startup log line keys on that)."""
    page = make_page(guest, "Untouched", properties={"sourceUrl": "https://x/y.pdf"})
    summary = run_all()
    assert summary["users"]["guest"] == {"source_url_key": 1}
    assert summary["changed"] >= 1
    assert guest.get(f"/api/blocks/{page['id']}").json()["properties"] == {"source_url": "https://x/y.pdf"}
    again = run_all()
    assert again["changed"] == 0 and again["users"] == {} and again["shares"] == {}
