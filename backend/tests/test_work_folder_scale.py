"""The plan's scale check (docs/dev/work_folders.md "The readers,
classified"): 20,000 files of one work folder beside 500 library pages
leave the library listing as fast as it was (the listing index holds what
the library lists), a directory of 10,000 files reads its first page of
rows at once, the directories' counts come from one indexed read, the op
rule's read of a folder tree of 2,000 directories stays small, the
feedback document of 50 open threads among the files reads at once
(docs/dev/feedback.md), quick open's search of the files by name or path
(GET /api/files) answers within a keystroke, a path resolves to its page
at once, the summaries of a tab bar's pages read at once, and migration
step 55's rebuild of the kind column over the files is timed once. The
rows are inserted directly, as a vault's would stand
(tests/seed_work_folder.py). The one real round is a work folder's first over 2,000 small
files of mixed formats, every file a page (docs/dev/work_folders.md "The
round in a work folder"), timed once and reported.

Timed like tests/test_perf.py, and marked ``perf`` with it: ``python -m
pytest tests/test_work_folder_scale.py -m perf -q`` from backend/, alone, on
an idle machine (``-s`` prints each case's numbers). Each budget is about
twice the p95 measured when it was set (2026-11), the listing's a ratio to
itself without the files."""

import json
import os
import secrets
import sqlite3
import time
import uuid
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_folder, make_page, make_user, mark_work_folder, workspace_of
from gamma import comments, folder_links, migrations, pdf_meta, storage
from gamma.blocks_store import BLOCK_COLUMNS, FOLDERS, touch_page
from gamma.db import BLOCK_HOT_INDEXES, PAGES_SCHEMA, connect_pages_db, connect_vaults_db, page_now
from gamma.feedback import entries
from gamma.routers import blocks as blocks_router
from gamma.vault import disk as vault_disk, store as vault_store, sync as vault_sync
from gamma.vault.local_remote import LocalRemote
from gamma.work_folders.marks import Tree
from seed_work_folder import add_folders, seed
from test_migrations import KIND_55, kind_before_55
from test_perf import budget, measure
from test_vault_files import PNG
from test_vault_sync import _pdf

pytestmark = pytest.mark.perf

USER, PASSWORD = "wfs_owner", "pw-wfs-1"
VAULT_USER = "wfs_vault"     # the account whose work folder is really synced: its pages apart from USER's 20,000
LIBRARY_PAGES = 500
FILES = 20_000
BIG = 10_000          # the files of the largest directory; the rest in DIRS of 1,000
DIRS = 10
TREE_DIRS = 2_000     # the directories of the folder tree the op rule reads
MIXED = 2_000         # the files of the first round timed, in 20 directories
MIXED_DIRS = 20


@pytest.fixture(autouse=True, scope="module")
def _serial():
    """Timed alone: beside other xdist workers the numbers mean nothing."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.skip("timing budgets run serially: python -m pytest tests/test_work_folder_scale.py -m perf")


def _listing(c):
    r = c.get("/api/blocks/root/children")
    r.raise_for_status()
    return r


@pytest.fixture(scope="module")
def big():
    """500 library pages, the listing timed; then a work folder W of
    20,000 files: 10,000 in its directory ``big``, the rest in 10 others
    (``dir 0`` … ``dir 9``, 1,000 each, numbered on from 10,000)."""
    make_user(USER, PASSWORD)
    c, ws = login(USER, PASSWORD), workspace_of(USER)
    w = make_folder(c, "wfs-W")
    mark_work_folder(c, w)
    for n in range(LIBRARY_PAGES):
        make_page(c, f"library page {n}")
    alone = measure(lambda _: _listing(c))
    now = page_now()
    with connect_pages_db(ws) as conn:
        dirs = seed(conn, w, files=FILES, big=BIG, dirs=DIRS, now=now)
        touch_page(conn, FOLDERS, account_of(USER), now=now)
        conn.commit()
    return SimpleNamespace(c=c, ws=ws, w=w, dirs=dirs, alone=alone)


def test_the_listing_takes_no_longer_for_the_files(big, request):
    listed = _listing(big.c).json()
    assert len(listed["children"]) >= LIBRARY_PAGES and listed["work_counts"][big.dirs[0]] == BIG
    assert not any("work_folder" in p["properties"] for p in listed["children"])
    beside = measure(lambda _: _listing(big.c))
    print(f"\n{request.node.name}: p50 {big.alone['p50']:.2f} ms alone, {beside['p50']:.2f} ms beside the files")
    assert beside["p50"] < 1.5 * big.alone["p50"], (big.alone, beside)


def test_a_directorys_first_page_reads_at_once(big, request):
    answer = big.c.get(f"/api/folders/{big.dirs[0]}/pages").json()
    assert answer["total"] == BIG and len(answer["pages"]) == 200 and answer["next"] == 200
    assert answer["pages"][0]["properties"]["file_name"] == "file-00000.md"
    budget(request, measure(lambda _: big.c.get(f"/api/folders/{big.dirs[0]}/pages").raise_for_status()), 60)


def test_a_directory_sorted_by_size_reads_at_once(big, request):
    rows = big.c.get(f"/api/folders/{big.dirs[0]}/pages", params={"sort": "size", "order": "desc"}).json()["pages"]
    assert rows[0]["properties"]["doc"]["size"] == max(1000 + n for n in range(BIG) if n % 3)
    budget(request, measure(lambda _: big.c.get(f"/api/folders/{big.dirs[0]}/pages",
                                                params={"sort": "size"}).raise_for_status()), 90)


def test_the_work_counts_are_one_indexed_read(big, request):
    def counts(_):
        with connect_pages_db(big.ws) as conn:
            return blocks_router._work_counts(conn, None)

    assert sum(counts(None).values()) == FILES
    budget(request, measure(counts), 10)


def _json(r):
    r.raise_for_status()
    return r.json()


def _files(big, q):
    return _json(big.c.get("/api/files", params={"q": q}))


def test_quick_open_finds_a_file_among_20000_by_name(big, request):
    """Every name holds ``file``, so the runs ``file`` AND ``012`` let
    through every name holding ``012``, which Python ranks: the hundred
    ``file-012NN`` match."""
    got = _files(big, "file-012")
    assert got["total"] == 100 and got["files"][0]["properties"]["file_name"] == "file-01200.md"
    budget(request, measure(lambda _: _files(big, "file-012")), 25)


def test_quick_open_finds_a_file_by_a_path_fragment(big, request):
    """One path term: the leaf's runs are the LIKEs, the segment ``dir 3``
    is scored on what they let through."""
    got = _files(big, "dir 3/file-1301")
    assert got["files"] and all(f["path"].startswith("dir 3/file-1301") for f in got["files"])
    _files(big, "dir 3/file-012")
    budget(request, measure(lambda _: _files(big, "dir 3/file-012")), 22)


def test_quick_open_answers_a_name_no_file_has(big, request):
    assert _files(big, "nothing-here") == {"files": [], "total": 0}
    budget(request, measure(lambda _: _files(big, "nothing-here")), 16)


def test_quick_open_ranks_every_file_for_a_query_with_no_latin_run(big, request):
    """``图`` gives the prefilter no run: the whole covering read, and every
    one of the 20,000 names folded and scored, the worst case."""
    assert _files(big, "图") == {"files": [], "total": 0}
    budget(request, measure(lambda _: _files(big, "图")), 95)


def test_a_path_resolves_to_its_page_at_once(big, request):
    """A file of the directory of 10,000 by its path: one seek of
    idx_ub_file_name, never a read of the directory."""
    def at(_):
        return _json(big.c.get(f"/api/folders/{big.w}/at", params={"path": "big/file-01234.bin"}))

    pages = at(None)["pages"]
    assert [p["path"] for p in pages] == ["big/file-01234.bin"]
    budget(request, measure(at), 11)


def test_the_summaries_of_40_files_read_at_once(big, request):
    """``POST /pages/summaries``'s lookup of 40 files among the 20,000
    (``blocks.summaries``, ``+parent_id``: without it the planner walks
    idx_ub_parent over every root row, about ten times as long). Timed on
    the connection: the request around it costs a few milliseconds of its
    own, and the previews one seek a page."""
    with connect_pages_db(big.ws) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE work_folder = ? AND parent_id = 'root' "
                                          "ORDER BY id LIMIT 40", (big.w,))]
    assert [p["id"] for p in _json(big.c.post("/api/pages/summaries", json={"ids": ids}))["pages"]] == ids

    def read(_):
        with connect_pages_db(big.ws) as conn:
            return blocks_router.summaries(conn, ids)

    assert len(read(None)) == 40
    budget(request, measure(read), 2)


def test_the_op_rule_reads_a_tree_of_2000_directories(big, request):
    with connect_pages_db(big.ws) as conn:
        add_folders(conn, big.dirs[1], [f"sub {n}" for n in range(TREE_DIRS)], page_now())
        touch_page(conn, FOLDERS, account_of(USER))
        conn.commit()

    def read(_):
        with connect_pages_db(big.ws) as conn:
            return Tree.read(conn)

    tree = read(None)
    assert len(tree.parent) > TREE_DIRS and tree.work_of(tree.order[-1]) == big.w
    budget(request, measure(read), 16)


def test_the_feedback_document_of_50_open_threads_reads_at_once(big, request):
    """50 open threads on files of W among its 20,000: ``entries.document``
    for W, the whole work folder, without the pictures drawn and without
    the SyncTeX map (no vault of this computer keeps W)."""
    with connect_pages_db(big.ws) as conn:
        pages = [r[0] for r in conn.execute(
            "SELECT id FROM unified_blocks WHERE work_folder = ? AND parent_id = 'root' "
            "AND json_extract(properties, '$.file_name') LIKE '%.bin' ORDER BY content LIMIT 50", (big.w,))]
        assert len(pages) == 50
        for page_id in pages:
            comments.create(conn, page_id, block_id=page_id, body="a thought on this file", author=account_of(USER),
                            author_name=USER)

    def read(_):
        with connect_pages_db(big.ws) as conn:
            return entries.document(big.ws, conn, entries.resolve_scope(conn, big.w), owner=USER, sources=False)

    doc = read(None)
    assert doc["count"] == 50 and {e["kind"] for e in doc["entries"]} == {"file"}
    budget(request, measure(read), 300)


STEP_55_MS = 10_000


def test_step_55_rebuilds_the_kind_column_over_the_files(big, request, tmp_path):
    """Migration step 55 on a pages.db of release 54's shape holding W's
    rows: ``kind`` and the five generated columns after it dropped and
    added again with their eight indexes (normalize.block_columns), timed
    once, since a rerun does nothing. The copy is built from the fixture's
    rows under the old statement."""
    with connect_pages_db(big.ws) as conn:
        live = conn.execute("PRAGMA database_list").fetchone()[2]
    table = next(s for s in PAGES_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS unified_blocks"))
    old = sqlite3.connect(str(tmp_path / "pages-54.db"))
    old.execute(kind_before_55(table))
    for stmt in BLOCK_HOT_INDEXES:
        old.execute(stmt)
    old.execute("ATTACH DATABASE ? AS live", (live,))
    old.execute(f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) SELECT {BLOCK_COLUMNS} FROM live.unified_blocks")
    old.commit()
    old.execute("DETACH DATABASE live")
    rows = old.execute("SELECT COUNT(*) FROM unified_blocks").fetchone()[0]
    assert rows > FILES
    t0 = time.perf_counter()
    migrations._v55_positions("ws", old, None)
    ms = (time.perf_counter() - t0) * 1000
    assert KIND_55 in old.execute("SELECT sql FROM sqlite_master WHERE name = 'unified_blocks'").fetchone()[0]
    assert old.execute("SELECT COUNT(*) FROM unified_blocks WHERE kind = 'page'").fetchone()[0] >= FILES
    print(f"\n{request.node.name}: step 55 over {rows} rows in {ms:.0f} ms, budget {STEP_55_MS} ms")
    assert ms < STEP_55_MS, ms


FIRST_ROUND_MS = 60_000


def _mixed(n: int) -> tuple[str, bytes]:
    """The ``n``th file of the mixed work folder: eight formats in turn, each
    file's bytes its own (a note, a PDF, LaTeX, JSON, a PNG, CSV, Python, a
    blob)."""
    match n % 8:
        case 0:
            return f"note-{n:04d}.md", f"# Note {n}\n\nsome text of note {n}\n\n- a point\n- another\n".encode()
        case 1:
            return f"paper-{n:04d}.pdf", _pdf(f"paper {n}")
        case 2:
            return f"section-{n:04d}.tex", f"\\section{{Part {n}}}\nSome text of part {n}.\n".encode()
        case 3:
            return f"data-{n:04d}.json", json.dumps({"n": n, "values": list(range(n % 7))}).encode()
        case 4:
            return f"fig-{n:04d}.png", PNG + n.to_bytes(2, "big")
        case 5:
            return f"table-{n:04d}.csv", f"x,y\n{n},{n * 2}\n{n + 1},{n * 2 + 2}\n".encode()
        case 6:
            return f"run-{n:04d}.py", f"print({n})\n".encode()
        case _:
            return f"blob-{n:04d}.bin", b"\x00\x01" + n.to_bytes(4, "big") + secrets.token_bytes(64)


def test_a_first_round_over_2000_mixed_files(request, tmp_path, monkeypatch):
    """A work folder's first round over 2,000 small files of eight formats in
    20 directories, a ``.gammaignore`` tracking every file: every file a page
    whose document is read where it is, the PDFs and the other files linked
    (one create per file, each its own transaction), then the quiet round
    after it. Timed once, since a second first round would be the quiet one;
    reported, the bound generous."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "_stats", {})
    make_user(VAULT_USER, PASSWORD)
    c, ws, user = login(VAULT_USER, PASSWORD), workspace_of(VAULT_USER), account_of(VAULT_USER)
    folder = make_folder(c, "wfs-mixed")
    mark_work_folder(c, folder)
    root = tmp_path / "mixed"
    root.mkdir()
    (root / ".gammaignore").write_text("# every file\n", encoding="utf-8")
    then = time.time() - 10
    for n in range(MIXED):
        leaf, data = _mixed(n)
        path = root / f"d{n % MIXED_DIRS}" / leaf
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (then, then))
    marker = vault_disk.make_marker(root)
    link = {"id": uuid.uuid4().hex, "workspace_id": ws, "folder_id": folder, "path": str(root), "pull": 1,
            "push": folder_links.PUSH_ALL, "created_by": user, "state": {"vault": marker["id"]}, "kind": "folder",
            "remote_url": "", "library": 0}
    remote = LocalRemote(ws, folder, user)
    try:
        t0 = time.perf_counter()
        result = vault_sync.sync(link, remote=remote)
        first = (time.perf_counter() - t0) * 1000
        assert result.status["last_error"] == "" and result.status["counts"]["sent"] == MIXED, result.status
        link["state"] = result.state
        t0 = time.perf_counter()
        result = vault_sync.sync(link, remote=remote)
        quiet = (time.perf_counter() - t0) * 1000
        assert result.status["last_error"] == "" and result.status["counts"]["sent"] == 0, result.status
        pages = remote.layout().pages
        assert len(pages) == MIXED and sum(1 for p in pages.values() if p.format == "pdf") == MIXED // 8
        print(f"\n{request.node.name}: first round over {MIXED} files in {first:.0f} ms, the quiet one {quiet:.0f} ms,"
              f" budget {FIRST_ROUND_MS} ms")
        assert first < FIRST_ROUND_MS, first
    finally:
        vault_store.forget_link(link["id"])
        with connect_vaults_db() as conn:
            conn.execute("DELETE FROM linked_files WHERE ws = ? AND substr(path, 1, ?) = ?",
                         (ws, len(str(root)), str(root)))
