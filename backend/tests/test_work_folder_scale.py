"""The plan's scale check (docs/dev/work_folders.md "The readers,
classified"): 20,000 files of one work folder beside 500 library pages
leave the library listing as fast as it was (the listing index holds what
the library lists), a directory of 10,000 files reads its first page of
rows at once, the directories' counts come from one indexed read, and the
op rule's read of a folder tree of 2,000 directories stays small. The rows
are inserted directly, as a vault's would stand.

Timed like tests/test_perf.py, and marked ``perf`` with it: ``python -m
pytest tests/test_work_folder_scale.py -m perf -q`` from backend/, alone, on
an idle machine (``-s`` prints each case's numbers). Each budget is about
twice the p95 measured when it was set (2026-11), the listing's a ratio to
itself without the files."""

import json
import os
import secrets
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_folder, make_page, make_user, mark_work_folder, workspace_of
from gamma.blocks_store import BLOCK_COLUMNS, FOLDERS, touch_page
from gamma.db import connect_pages_db, page_now
from gamma.routers import blocks as blocks_router
from gamma.work_folders.marks import Tree
from test_perf import budget, measure

pytestmark = pytest.mark.perf

USER, PASSWORD = "wfs_owner", "pw-wfs-1"
LIBRARY_PAGES = 500
FILES = 20_000
BIG = 10_000          # the files of the largest directory; the rest in DIRS of 1,000
DIRS = 10
TREE_DIRS = 2_000     # the directories of the folder tree the op rule reads


@pytest.fixture(autouse=True, scope="module")
def _serial():
    """Timed alone: beside other xdist workers the numbers mean nothing."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.skip("timing budgets run serially: python -m pytest tests/test_work_folder_scale.py -m perf")


def _folders(conn, parent, names, now) -> list[str]:
    """Folder rows below ``parent``, inserted directly."""
    ids = [secrets.token_urlsafe(9) for _ in names]
    conn.executemany(f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) VALUES (?, ?, ?, ?, '{{}}', ?, ?, ?)",
                     [(i, parent, f"a{n:06d}", name, now, now, FOLDERS) for n, (i, name) in enumerate(zip(ids, names))])
    return ids


def _files(conn, w, directory, count, now, start) -> None:
    """``count`` files of the work folder ``w`` filed in ``directory``, as
    their vault stamps them (every third a note), each with its row of the
    change log."""
    rows, seq = [], conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page_changes").fetchone()[0]
    for n in range(start, start + count):
        page_id, leaf = secrets.token_urlsafe(9), f"file-{n:05d}"
        props = {"work_folder": w, "folders": [directory]}
        if n % 3:
            props |= {"file_name": f"{leaf}.pdf", "doc": {"format": "pdf", "name": f"{leaf}.pdf",
                                                          "hash": secrets.token_hex(12), "ext": ".pdf", "at": "path",
                                                          "url": "", "size": 1000 + n}}
        else:
            props["file_name"] = f"{leaf}.md"
        rows.append((page_id, "root", f"z{n:06d}", leaf, json.dumps(props), now, now, page_id))
    conn.executemany(f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.executemany("INSERT INTO page_changes (page_id, seq, kind, at, actor) VALUES (?, ?, 'live', ?, '')",
                     [(r[0], seq + k, now) for k, r in enumerate(rows, 1)])


def _listing(c):
    r = c.get("/api/blocks/root/children")
    r.raise_for_status()
    return r


@pytest.fixture(scope="module")
def big():
    """500 library pages, the listing timed; then a work folder W of
    20,000 files: 10,000 in its directory ``big``, the rest in 10 others."""
    make_user(USER, PASSWORD)
    c, ws = login(USER, PASSWORD), workspace_of(USER)
    w = make_folder(c, "wfs-W")
    mark_work_folder(c, w)
    for n in range(LIBRARY_PAGES):
        make_page(c, f"library page {n}")
    alone = measure(lambda _: _listing(c))
    now = page_now()
    with connect_pages_db(ws) as conn:
        dirs = _folders(conn, w, ["big"] + [f"dir {n}" for n in range(DIRS)], now)
        _files(conn, w, dirs[0], BIG, now, 0)
        per = (FILES - BIG) // DIRS
        for n, directory in enumerate(dirs[1:]):
            _files(conn, w, directory, per, now, BIG + n * per)
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


def test_the_op_rule_reads_a_tree_of_2000_directories(big, request):
    with connect_pages_db(big.ws) as conn:
        _folders(conn, big.dirs[1], [f"sub {n}" for n in range(TREE_DIRS)], page_now())
        touch_page(conn, FOLDERS, account_of(USER))
        conn.commit()

    def read(_):
        with connect_pages_db(big.ws) as conn:
            return Tree.read(conn)

    tree = read(None)
    assert len(tree.parent) > TREE_DIRS and tree.work_of(tree.order[-1]) == big.w
    budget(request, measure(read), 16)
