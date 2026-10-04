"""The workspace change log (``page_changes``, blocks_store.touch_page):
every writer of a page moves the page's row to the next seq of the
workspace — ``live``, or ``deleted`` when the page goes — inside its own
transaction, so the seqs are handed out one per write in commit order
however many writers race, and a reader of the change feed misses nothing
and is told nothing twice (docs/dev/collab.md "The change feed")."""

import io
import zipfile

import pytest

from conftest import account_of, at_once, login, make_user
from gamma import ops, ws_backup
from gamma.blocks_store import touch_page
from gamma.db import connect_pages_db
from gamma.routers import imports
from gamma.routers.sync import changes
from test_import_transactions import _zotero_zip
from test_writer_races import EDN, MD, _annotated_pdf, _pdf

USER = "pc_writer"


@pytest.fixture(scope="module")
def writer(client):
    ws = make_user(USER, "pc-password-1")
    return login(USER, "pc-password-1"), ws


def _row(ws, page_id):
    """The page's row of the change log: ``(seq, kind, actor)``, or None."""
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT seq, kind, actor FROM page_changes WHERE page_id = ?", (page_id,)).fetchone()


def _newest(ws):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page_changes").fetchone()[0]


def _listed(ws, since):
    """The feed after the seq ``since`` walked to the end, two entries an
    answer: ``{page id: "live" | "deleted"}``."""
    out, cursor = {}, str(since)
    with connect_pages_db(ws) as conn:
        while True:
            feed = changes(conn, cursor, 2)
            out.update({p["id"]: "live" for p in feed["pages"]})
            out.update({d["id"]: "deleted" for d in feed["deleted"]})
            cursor = feed["cursor"]
            if not feed["more"]:
                return out


def _moves(ws, write, kind="live"):
    """Run ``write`` (→ the page it wrote) and check that the page's row
    took a seq above every row before it, as ``kind``, and that the feed
    since then lists it so. Returns the page id."""
    before = _newest(ws)
    page_id = write()
    row = _row(ws, page_id)
    assert row is not None and row[0] > before and row[1] == kind, (page_id, row, before)
    assert _listed(ws, before).get(page_id) == kind
    return page_id


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_every_writer_of_a_page_moves_it_in_the_change_log(writer, monkeypatch):
    c, ws = writer
    me = account_of(USER)

    # creating a page (POST /pages, POST /blocks under root), an op batch
    page = _moves(ws, lambda: _ok(c.post("/api/pages", json={"title": "Logged"}))["id"])
    assert _row(ws, page)[2] == me
    other = _moves(ws, lambda: _ok(c.post("/api/blocks", json={"parent_id": "root", "content": "Other"}))["id"])
    _moves(ws, lambda: _ok(c.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "pcN1", "parent": page, "content": "n"}]})) and page)

    # a cross-page move: record_ops on the page it leaves, log_reload on the one it joins
    before = _newest(ws)
    _ok(c.post("/api/blocks/pcN1/reorder", json={"parent_id": other}))
    assert _listed(ws, before) == {page: "live", other: "live"}

    # a subtree replace (log_reload)
    def replace():
        _ok(c.put(f"/api/blocks/{other}/children", json={"blocks": [{"content": "fresh"}]}))
        return other
    _moves(ws, replace)

    # to Recently deleted and back, then for good from there: the row keeps its trashing
    def trash():
        _ok(c.delete(f"/api/blocks/{page}"))
        return page
    _moves(ws, trash, "deleted")
    assert _row(ws, page)[2] == me
    _moves(ws, lambda: _ok(c.post(f"/api/trash/{page}/restore"))["id"])
    trash()
    trashed = _row(ws, page)
    _ok(c.delete(f"/api/trash/{page}"))
    assert _row(ws, page) == trashed and page not in _listed(ws, trashed[0])

    # deleted for good at once (a share host), then made again under that id
    def hard_delete():
        with connect_pages_db(ws) as conn:
            ops.delete_page(ws, conn, other, actor=me)
        return other
    _moves(ws, hard_delete, "deleted")
    _moves(ws, lambda: _ok(c.post("/api/pages", json={"id": other, "title": "Again"}))["id"])

    # the imports' own writers
    _moves(ws, lambda: _ok(c.post("/api/import/markdown", files={
        "file": ("n.md", "# Imported\n\n- a\n", "text/markdown")}))["block_id"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("vault/Zipped.md", "# Zipped\n\nbody\n")
    _moves(ws, lambda: _ok(c.post("/api/import/markdown-zip", files={
        "file": ("notes.zip", buf.getvalue(), "application/zip")}))["pages"][0]["id"])
    key = "https://example.org/pc-zotero"
    zotero = _moves(ws, lambda: _ok(c.post("/api/import/zotero", files={"file": (
        "lib.zip", _zotero_zip([(key, "Zotero paper", None, [("n1", "a note")], [])]), "application/zip")}))
        ["pages"][0]["id"])
    assert _moves(ws, lambda: _ok(c.post("/api/import/zotero", files={"file": (
        "lib.zip", _zotero_zip([(key, "Zotero paper", None, [("n1", "a note"), ("n2", "more")], [])]),
        "application/zip")}))["pages"][0]["id"]) == zotero
    _moves(ws, lambda: _ok(c.post("/api/import/logseq", files={
        "pdf": ("pc.pdf", _pdf("page changes"), "application/pdf"),
        "edn": ("hls.edn", EDN.encode(), "application/octet-stream"),
        "md": ("hls.md", MD.encode(), "text/markdown")}))["block_id"])
    path = _annotated_pdf(ws, "page-changes-annots.pdf", monkeypatch)
    _moves(ws, lambda: imports.import_embedded_annotations(ws, zotero, path, False, actor=me) and zotero)

    # a backup restore: a replace moves every restored page — and the folder
    # and label trees, which it rewrites too — and deletes the ones it removes
    snap = ws_backup.create(ws, label="pc")
    made = _ok(c.post("/api/pages", json={"title": "Made after the backup"}))["id"]
    before = _newest(ws)
    ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snap["name"]), "replace", by=me)
    with connect_pages_db(ws) as conn:
        library = {r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root'")}
    assert _listed(ws, before) == {**dict.fromkeys([*library, "folders", "labels"], "live"), made: "deleted"}

    # a merge brings a page deleted for good back live
    def merge():
        with connect_pages_db(ws) as conn:
            ops.delete_page(ws, conn, zotero, actor=me)
        assert _row(ws, zotero)[1] == "deleted"
        assert ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snap["name"]), "merge", by=me)["pages_added"] == 1
        return zotero
    _moves(ws, merge)


def test_racing_writers_get_one_seq_each_and_a_reader_misses_none(writer):
    """Writers on four pages at once while a reader walks the feed, three
    entries an answer: every write takes the next seq (none twice, none
    skipped) and the reader, caught up at the end, has seen every page as
    its last write left it."""
    c, ws = writer
    pages = [_ok(c.post("/api/pages", json={"title": f"Race {i}"}))["id"] for i in range(4)]
    start, writes, finished = _newest(ws), 12, []

    def write(page):
        def run():
            for i in range(writes):
                ops.commit_ops(ws, page, [{"op": "insert", "id": f"{page}-{i}", "parent": page, "content": str(i)}],
                               actor=USER)
            finished.append(page)
        return run

    def read():
        seen, cursor = {}, str(start)
        while True:
            last = len(finished) == len(pages)  # read before the walk: it then sees every write
            with connect_pages_db(ws) as conn:
                feed = changes(conn, cursor, 3)
            seen.update({p["id"]: p["seq"] for p in feed["pages"]})
            cursor = feed["cursor"]
            if last and not feed["more"]:
                return seen, cursor

    *done, (seen, cursor) = at_once([write(p) for p in pages] + [read])
    assert not [e for e in done if isinstance(e, Exception)], done
    with connect_pages_db(ws) as conn:
        assert seen == {p: ops.latest_seq(conn, p) for p in pages} == dict.fromkeys(pages, writes)
        seqs = [r[0] for r in conn.execute("SELECT seq FROM page_changes WHERE seq > ? ORDER BY seq", (start,))]
    assert seqs[-1] == start + len(pages) * writes == int(cursor)  # one seq per write
    assert len(seqs) == len(pages)  # one row per page, at its last write


def test_touch_page_takes_the_next_seq_whatever_the_kind(writer):
    c, ws = writer
    page = _ok(c.post("/api/pages", json={"title": "Touched"}))["id"]
    with connect_pages_db(ws) as conn:
        first = conn.execute("SELECT MAX(seq) FROM page_changes").fetchone()[0]
        touch_page(conn, page, "pc_a", "deleted")
        touch_page(conn, page, "pc_b")
        conn.commit()
        row = conn.execute("SELECT seq, kind, actor, at FROM page_changes WHERE page_id = ?", (page,)).fetchone()
        stamp = conn.execute("SELECT updated_at FROM unified_blocks WHERE id = ?", (page,)).fetchone()[0]
    assert row[:3] == (first + 2, "live", "pc_b") and row[3] == stamp  # a live touch stamps the page too
