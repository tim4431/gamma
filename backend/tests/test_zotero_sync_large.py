"""A Zotero link's engine at scale (gamma/zotero_sync.py) against the fake
Zotero with generated libraries (``zotero_fixtures.generated_library``): a
long first fill in rounds of three units resumes across many rounds, its
progress counted over the whole fill, nothing made twice; a 2,000-item
library fills in rounds that keep to the budget (``ROUND_UNITS``) in bounded
time, an item's children read by key with it (a child list only for an item
read again once its PDF came, child lists for items of hundreds of
annotations, the same children either way, each read once over rounds that
read their parents); a page of 3,000 blocks (a note of 1,500 paragraphs, 500
highlights, 1,000 notes of Gamma's) is filled and then reconciled after a
change on either side in bounded time; and the queries a round makes walk
their indexes, a page or a folder's pages read once. Set
``ZOTERO_MEASURE=1`` to have the scale tests print their timings and the
Python memory peak (tracemalloc). Accounts are ``zg_…``."""

import json
import os
import re
import time
import tracemalloc

from conftest import make_user
from zotero_engine import (LIB, _block, _folder_ops, _link, _pages, _rows, _sync, _writes, background,  # noqa: F401
                           drop_pending, fake, idle, made, me, no_relocation, owner_id)
from zotero_fixtures import _annotation, _note, _position, generated_library, pdf_bytes

from gamma import ops, zotero_sync, zotero_sync_folders, zotero_sync_gone, zotero_sync_pdfs, zotero_sync_reads, \
    zotero_sync_rows
from gamma import zotero_remote as zr
from gamma.blocks_store import FOLDERS
from gamma.db import connect_pages_db, page_now

USER, PASSWORD = "zg_owner", "pw-zg_owner-123"
MEASURE = os.environ.get("ZOTERO_MEASURE") == "1"


def _count(ws, sql, args=()) -> int:
    with connect_pages_db(ws) as conn:
        return conn.execute(sql, args).fetchone()[0]


def _rounds(ws, *, most: int, finish: bool = True) -> list[dict]:
    """Rounds as the clock runs them, each asking for the next at once while
    work remains, until one asks for none (at most ``most``; without
    ``finish``, ``most`` and no more); each round's status, the progress it
    showed and how long it took."""
    out = []
    progress: list = []
    patch = zotero_sync_rows.patch_status
    callers = (zotero_sync, zotero_sync_pdfs)  # the round's progress and the file transfers' ticks

    def watch(ws_, value, **kw):
        status = patch(ws_, value, **kw)
        if ws_ == ws and status.get("progress") and "done" in status["progress"]:
            progress.append(dict(status["progress"]))
        return status

    for module in callers:
        module.patch_status = watch
    try:
        while len(out) < most:
            drop_pending(ws)
            seen, started = len(progress), time.monotonic()
            status = zotero_sync.sync_workspace(ws)
            assert not status.get("last_error"), status
            out.append({"status": status, "progress": progress[seen:], "s": time.monotonic() - started,
                        "next": zotero_sync._clock.pending.get(ws)})
            if out[-1]["next"] is None or out[-1]["next"] > time.monotonic() + 1:
                return out  # nothing due asked for at once: the fill is done
    finally:
        for module in callers:
            module.patch_status = patch
    if not finish:
        return out
    raise AssertionError(f"still going after {most} rounds")


def test_a_long_first_fill_in_rounds_of_three_resumes_and_counts_its_progress(me, fake, monkeypatch):
    made = generated_library(fake, 30, pdfs=10, annotations=2, notes=10, collections=6)
    monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 3)
    ws = _link(me)
    rounds = _rounds(ws, most=60)
    assert len(rounds) > 10
    shown = [p for r in rounds for p in r["progress"]]
    for r in rounds:  # each round keeps to its three units, and asks for the next at once while work remains
        assert len(r["progress"]) <= 3
    for r in rounds[:-1]:  # the fill's count goes on to the next round
        assert r["status"]["backlog"] > 0 and r["status"]["fill"]["units"] == r["progress"][-1]["done"]
    done = [p["done"] for p in shown]
    assert done == list(range(done[0], done[0] + len(done)))  # one more each unit, across every round
    assert all(p["phase"] == "first" and p["done"] <= p["total"] for p in shown)
    assert shown[-1]["done"] == shown[-1]["total"] and all(p["bytes"]["done"] <= p["bytes"]["total"] for p in shown)
    sizes = sum(len(pdf_bytes(f"Paper {n}")) for n in range(10))
    assert shown[-1]["bytes"]["done"] == sizes
    last = rounds[-1]["status"]
    assert last["backlog"] == 0 and "fill" not in last and "progress" not in last
    assert _writes(fake) == []
    # nothing twice: a page per item, a block per annotation, a region per note, a folder per collection
    assert _count(ws, "SELECT COUNT(*) FROM unified_blocks WHERE parent_id = 'root'") == 30
    assert len(_rows(ws, "item")) == 30 and len(_rows(ws, "attachment")) == 10
    annotations = _rows(ws, "annotation")
    assert set(annotations) == set(made["annotations"]) and all(r["state"] == "live" for r in annotations.values())
    assert len({r["block_id"] for r in annotations.values()}) == 20
    assert set(_rows(ws, "note")) == set(made["notes"])
    assert _count(ws, "SELECT COUNT(*) FROM unified_blocks WHERE page_id = 'folders' AND id != 'folders'") == 6
    with connect_pages_db(ws) as conn:
        assert conn.execute("SELECT COUNT(*) FROM zotero_state WHERE first != ''").fetchone()[0] == 0


def test_a_fill_cut_off_by_zotero_going_away_goes_on_where_it_stopped(me, fake, monkeypatch):
    from gamma.zotero_web import MAX_RETRIES
    made = generated_library(fake, 20, pdfs=5, annotations=1, notes=5, collections=3)
    monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 5)
    ws = _link(me)
    first = _rounds(ws, most=1, finish=False)
    fake.fail_next(502, "itemKey=", times=MAX_RETRIES + 1, method="GET")  # Zotero unreachable for a while
    drop_pending(ws)
    status = zotero_sync.sync_workspace(ws)
    assert status["error_kind"] == "offline" and status["fill"]["units"] >= first[-1]["progress"][-1]["done"]
    rest = _rounds(ws, most=60)
    done = [p["done"] for r in rest for p in r["progress"]]
    assert done[0] > status["fill"]["units"] and done == sorted(done) and len(set(done)) == len(done)
    assert rest[-1]["status"]["backlog"] == 0 and not rest[-1]["status"]["last_error"]
    assert _count(ws, "SELECT COUNT(*) FROM unified_blocks WHERE parent_id = 'root'") == 20
    assert set(_rows(ws, "annotation")) == set(made["annotations"]) and set(_rows(ws, "note")) == set(made["notes"])
    assert _writes(fake) == []


# --- what a fill reads ----------------------------------------------------------------------------

def _fill_requests(me, fake):
    import collections
    import re
    from zotero_fixtures import generated_library
    generated_library(fake, 60, pdfs=20, annotations=3, notes=10, collections=4)
    ws = _link(me)
    calls = len(fake.calls)
    for _ in range(3):
        _sync(me, ws)
    shapes = collections.Counter()
    for m, path, _ in fake.calls[calls:]:
        if m == "GET" and "itemKey=" in path:
            shapes["by key"] += 1
        elif m == "GET" and re.search(r"/items/[23456789A-Z]{8}/children", path):
            shapes["children"] += 1
        elif path.endswith("/file") or "/__storage/" in path:
            shapes["file"] += 1
    return ws, shapes


def test_a_first_fill_reads_items_by_key_in_batches_and_each_child_with_its_item(me, fake):
    ws, shapes = _fill_requests(me, fake)
    rows = _rows(ws)
    assert sum(1 for row in rows.values() if row["kind"] == "item") >= 60
    assert sum(1 for row in rows.values() if row["kind"] == "annotation" and row["state"] == "live") >= 60
    # 60 items, their 20 PDFs and 90 children: by key, the items and their children fifty a read, each item read
    # again after its PDF came (one by one and children first, 150 reads by key)
    assert shapes["by key"] <= 25, shapes
    # a child list only for an item read again once its PDF came (its own and its PDF's), none for an item's
    # first unit (each item's and each PDF's: 120)
    assert shapes["children"] <= 2 * 20, shapes


def test_a_fill_of_items_holding_hundreds_of_annotations_reads_their_child_lists_not_each_by_key(me, fake):
    import urllib.parse
    from zotero_fixtures import generated_library
    made = generated_library(fake, 6, pdfs=3, annotations=120)  # (363 children of 6 items: lists cost fewer reads)
    ws = _link(me)
    _sync(me, ws)
    read = set()
    for m, path, _ in fake.calls:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        if m == "GET" and "itemKey" in query:
            read.update(query["itemKey"][0].split(","))
    assert read and not read & set(made["annotations"])
    rows = _rows(ws)
    assert {k for k, row in rows.items() if row["kind"] == "annotation" and row["state"] == "live"} == \
        set(made["annotations"])


def test_an_items_children_as_a_fill_reads_them_by_key_are_those_its_child_lists_give(me, made, fake, monkeypatch):
    keys = made["keys"]
    from zotero_fixtures import _annotation, _position
    fake.add_item(LIB, _annotation(keys["snapshot"], "highlight", "#ffd400", "00000|000001|00000",
                                   _position(0, rects=[[1, 1, 9, 9]]), annotationText="on the web page"))
    second = fake.add_item(LIB, {"itemType": "attachment", "parentItem": keys["attention"], "linkMode": "imported_file",
                                 "title": "Supplement", "contentType": "application/pdf", "filename": "supp.pdf"})
    fake.add_file(LIB, second, pdf_bytes("Supplementary material"))
    fake.add_item(LIB, _annotation(second, "highlight", "#5fb236", "00000|000002|00000",
                                   _position(0, rects=[[2, 2, 8, 8]]), annotationText="Supplementary"))
    gone = fake.add_item(LIB, {"itemType": "note", "parentItem": keys["attention"], "note": "<p>Second thoughts</p>"})
    fake.trash(LIB, gone)
    fake.add_item(LIB, {"itemType": "note", "parentItem": keys["attention"], "note": "<p>In the same second</p>"})
    seen = []
    listed = zotero_sync_reads._listed_children

    def both(r, item, entry):
        found = listed(r, item, entry)
        if found is not None:
            seen.append((item["key"], [c["key"] for c in found], [c["key"] for c in zr.descendants(r.client, r.library,
                                                                                                    item)]))
        return found

    monkeypatch.setattr(zotero_sync_reads, "_listed_children", both)
    ws = _link(me)
    _sync(me, ws)
    assert {keys["attention"], keys["standalone_pdf"], keys["standalone_note"]} <= {key for key, _, _ in seen}
    for key, by_key, lists in seen:
        assert by_key == lists, key
    assert next(len(by_key) for key, by_key, _ in seen if key == keys["attention"]) >= 15


def test_a_fill_that_reads_its_childrens_parents_over_rounds_brings_each_once(me, fake, monkeypatch):
    from zotero_fixtures import generated_library
    made = generated_library(fake, 30, pdfs=10, annotations=3, notes=10, collections=2)
    monkeypatch.setattr(zotero_sync_reads, "CHILD_READS", 20)  # (50 children: three rounds to read their parents)
    monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 4)
    ws = _link(me)
    for _ in range(40):
        if not _sync(me, ws)["backlog"]:
            break
    rows = _rows(ws)
    assert {k for k, row in rows.items() if row["kind"] == "item"} == set(made["items"])
    assert {k for k, row in rows.items() if row["kind"] == "annotation" and row["state"] == "live"} == \
        set(made["annotations"])
    assert {k for k, row in rows.items() if row["kind"] == "note"} == set(made["notes"])
    assert len(_pages(ws)) == 30 and len({row["page_id"] for row in rows.values() if row["kind"] == "note"}) == 10
    with connect_pages_db(ws) as conn:
        assert conn.execute("SELECT COUNT(*) FROM zotero_queue").fetchone()[0] == 0


def _measured(fn):
    """``fn()`` timed, with tracemalloc's peak when measuring: (value,
    seconds, peak MiB or None)."""
    if MEASURE:
        tracemalloc.start()
    started = time.monotonic()
    try:
        value = fn()
    finally:
        took = time.monotonic() - started
        peak = tracemalloc.get_traced_memory()[1] / 2 ** 20 if MEASURE else None
        if MEASURE:
            tracemalloc.stop()
    return value, took, peak


def test_a_library_of_2000_items_fills_in_rounds_that_keep_to_the_budget(me, fake, monkeypatch):
    built = time.monotonic()
    made = generated_library(fake, 2000, pdfs=60, annotations=2, notes=150, collections=40)
    built = time.monotonic() - built
    monkeypatch.setattr(zotero_sync, "ROUND_UNITS", 50)
    monkeypatch.setattr(zotero_sync, "ROUND_BUDGET_S", 60)
    ws = _link(me)
    rounds, took, peak = _measured(lambda: _rounds(ws, most=200))
    units = [len(r["progress"]) for r in rounds]
    slowest = max(r["s"] for r in rounds)
    lists = sum(1 for m, path, _ in fake.calls if m == "GET" and re.search(r"/items/[^/?]+/children", path))
    if MEASURE:
        print(f"\n2,000 items: library built in {built:.1f} s; {len(rounds)} rounds, {sum(units)} units in "
              f"{took:.1f} s ({1000 * took / max(1, sum(units)):.0f} ms a unit), slowest round {slowest:.1f} s, "
              f"Python peak {peak:.0f} MiB; {len(fake.calls)} requests to Zotero, {lists} of them child lists")
    assert max(units) <= 50 and len(rounds) <= 2 * (2000 + 60) // 50 + 5
    # an item's children come by key with it, fifty a read: a child list only for each of the 60 items read again
    # once its PDF came, its own and its PDF's (each item's and each PDF's too: 2,180 in all, 2,453 requests)
    assert lists <= 2 * 60 and len(fake.calls) < 600
    assert slowest < 60 and took < 280  # a round keeps to its budget, the fill to reasonable time
    assert _count(ws, "SELECT COUNT(*) FROM unified_blocks WHERE parent_id = 'root'") == 2000
    assert len(_rows(ws, "annotation")) == 120 and len(_rows(ws, "note")) == 150
    assert set(_rows(ws, "item")) == set(made["items"]) and _writes(fake) == []


BIG_NOTE_PARAGRAPHS = 1500


BIG_HIGHLIGHTS = 500


BIG_OWN_NOTES = 1000


def test_a_page_of_3000_blocks_reconciles_in_bounded_time(me, fake, owner_id):
    lib = fake.library
    item = fake.add_item(lib, {"itemType": "journalArticle", "title": "A long read", "date": "2020"})
    att = fake.add_item(lib, {"itemType": "attachment", "parentItem": item, "linkMode": "imported_file",
                              "title": "Full Text PDF", "contentType": "application/pdf", "filename": "long.pdf"})
    fake.add_file(lib, att, pdf_bytes())
    note = fake.add_item(lib, {"itemType": "note", "parentItem": item, "note": _note(
        "<h1>Reading notes</h1>" + "".join(f"<p>Paragraph {n} of the reading notes.</p>"
                                           for n in range(BIG_NOTE_PARAGRAPHS)))})
    highlights = []
    for n in range(BIG_HIGHLIGHTS):
        page, top = n % 2, 700.0 - (n % 40) * 15
        highlights.append(fake.add_item(lib, _annotation(
            att, "highlight", "#ffd400", f"{page:05d}|{n:06d}|{int(792 - top - 12):05d}",
            _position(page, rects=[[72.0, top, 72.0 + 3 * (n % 50) + 40, top + 12]]), comment=f"highlight {n}",
            annotationText=f"passage {n}")))
    ws = _link(me)
    _, fill, peak = _measured(lambda: _rounds(ws, most=10))
    page = _rows(ws, "item")[item]["page_id"]
    blocks = _count(ws, "SELECT COUNT(*) FROM unified_blocks WHERE page_id = ? AND id != ?", (page, page))
    assert blocks == BIG_NOTE_PARAGRAPHS + 1 + BIG_HIGHLIGHTS
    for start in range(0, BIG_OWN_NOTES, 500):  # (a batch holds at most 500 ops)
        ops.commit_ops(ws, page, [{"op": "insert", "id": f"zgOwn{n}", "parent": page, "content": f"my note {n}"}
                                  for n in range(start, min(start + 500, BIG_OWN_NOTES))], actor=owner_id, client="t")
    rows = _rows(ws)
    root = rows[note]["block_id"]
    with connect_pages_db(ws) as conn:
        first = conn.execute("SELECT id FROM unified_blocks WHERE parent_id = ? ORDER BY position LIMIT 1",
                             (root,)).fetchone()[0]
    # Zotero edits one paragraph and one highlight's colour; Gamma one paragraph and one highlight's comment
    html = fake.data(lib, note)["note"].replace("Paragraph 700 of", "Paragraph 700, edited in Zotero, of")
    fake.edit(lib, note, note=html)
    fake.edit(lib, highlights[10], annotationColor="#ff6666")
    ops.commit_ops(ws, page, [{"op": "set", "id": rows[highlights[20]]["block_id"], "content": "edited here"}],
                   actor=owner_id, client="t")
    calls = len(fake.calls)
    _, pulled, _ = _measured(lambda: _rounds(ws, most=10))
    with connect_pages_db(ws) as conn:
        assert "Paragraph 700, edited in Zotero, of the reading notes." in {
            r[0] for r in conn.execute("SELECT content FROM unified_blocks WHERE page_id = ?", (page,))}
        color = json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?",
                                        (rows[highlights[10]]["block_id"],)).fetchone()[0])["color"]
    assert color.startswith("#ff6666")
    assert fake.data(lib, highlights[20])["annotationComment"] == "edited here"
    sent = _writes(fake, calls)
    assert len(sent) == 1  # the one annotation PATCH; the page's own notes wait for the page to be still
    ops.commit_ops(ws, page, [{"op": "set", "id": first, "content": "Paragraph 0, edited here."}],
                   actor=owner_id, client="t")
    _, pushed, _ = _measured(lambda: _rounds(ws, most=10))
    assert "Paragraph 0, edited here." in fake.data(lib, note)["note"]
    if MEASURE:
        print(f"\n3,000 blocks: filled in {fill:.1f} s (Python peak {peak:.0f} MiB), a change in Zotero "
              f"reconciled in {pulled:.1f} s, one here in {pushed:.1f} s")
    assert fill < 120 and pulled < 30 and pushed < 30


# --- the queries and reads a round makes ----------------------------------------------------------

def _plans(conn, run) -> list[tuple[str, str]]:
    """(sql, its query plan) of each statement ``run(conn)`` executes."""
    seen = []
    conn.set_trace_callback(seen.append)
    try:
        run(conn)
    finally:
        conn.set_trace_callback(None)
    out = []
    for sql in seen:
        if sql.lstrip().upper().startswith("SELECT"):
            plan = " | ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
            out.append((sql, plan))
    return out


def test_a_rounds_own_looks_at_its_rows_and_queue_walk_indexes(client):
    ws = make_user("zg_plans", "pw-zg_plans")
    with connect_pages_db(ws) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_zotero_objects_kind", "idx_zotero_queue_order"} <= names
        for sql, plan in _plans(conn, zotero_sync._waiting):  # what waits: every idle round
            assert "idx_zotero_objects_kind" in plan, (sql, plan)
        for sql, plan in _plans(conn, lambda c: zr.take(c, now=page_now(), limit=50, kinds=zotero_sync.UNIT_KINDS,
                                                         exclude={"page:x"})):
            assert "idx_zotero_queue_order" in plan and "TEMP B-TREE" not in plan, (sql, plan)


def test_a_rounds_progress_finds_each_queued_files_row_by_its_key(client, monkeypatch):
    ws = make_user("zg_progress", "pw-zg_progress")
    conn = connect_pages_db(ws)
    seen = []
    monkeypatch.setattr(zotero_sync, "connect_pages_db", lambda ws_: conn)
    monkeypatch.setattr(zotero_sync, "patch_status", lambda *a, **k: {})
    r = zotero_sync_rows.Round(ws, {"status": {}}, time.monotonic(), library=LIB)
    conn.set_trace_callback(seen.append)
    try:
        zotero_sync._progress(r, None)
    finally:
        conn.set_trace_callback(None)
    (join,) = [sql for sql in seen if "JOIN zotero_objects" in sql]
    plan = " | ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + join))
    assert "SEARCH o USING INDEX sqlite_autoindex_zotero_objects_1" in plan, plan


def test_a_parents_rows_and_the_newest_top_level_rows_walk_the_parent_version_index(client, monkeypatch):
    from types import SimpleNamespace

    from gamma import zotero_sync_pages, zotero_sync_pdfs, zotero_sync_reads
    ws = make_user("zg_parent_plans", "pw-zg_parent_plans")
    with connect_pages_db(ws) as conn:  # (held open: the units' own ``with`` blocks reuse it)
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_zotero_objects_parent_version", "idx_zotero_queue_parent"} <= names
        assert "idx_zotero_objects_parent" not in names  # (the parent-version index's first column serves it)
        for module in (zotero_sync_pdfs, zotero_sync_pages, zotero_sync_reads):
            monkeypatch.setattr(module, "connect_pages_db", lambda ws_: conn)
        r = zotero_sync_rows.Round(ws, {"status": {}, "download": "open"}, time.monotonic(), library=LIB)
        # "When opened": a new item's count of the newer items stops at the newest, not at every top-level row
        unit = SimpleNamespace(r=r, version=10 ** 9, key="NEWITEM2")
        (plan,) = [p for sql, p in _plans(conn, lambda c: zotero_sync_pdfs.UnitPdf._wants_download(unit))
                   if "version >" in sql]
        assert "idx_zotero_objects_parent_version (parent=? AND version>?)" in plan, plan
        # a page with no item's or PDF's row: its standalone note's row by the page's rows, not every top-level
        # row's
        entry = zr.Entry("page:zbNoRows", "page", 0, zr.PAGE_PRIORITY, 0, "", "")
        (plan,) = [p for sql, p in _plans(conn, lambda c: zotero_sync_pages.unit_page(r, entry)) if "'note'" in sql]
        assert "idx_zotero_objects_page" in plan, plan
        # a fill's queued children of its items, and those whose parent is not read yet
        for run in (lambda c: zr.children_of(c, ["ITEM2222"]), lambda c: zr.unread_children(c, limit=5)):
            (plan,) = [p for _, p in _plans(conn, run)]
            assert "idx_zotero_queue_parent" in plan and "TEMP B-TREE" not in plan, plan


def test_a_refiled_collections_members_rows_are_found_by_their_keys(client):
    from types import SimpleNamespace
    ws = make_user("zg_refile_plans", "pw-zg_refile_plans")
    plan = SimpleNamespace(refile={"COLLKEY1"}, refiled=set(), regained=set(), rekeyed=set(), rows={})
    with connect_pages_db(ws) as conn:
        (found,) = [p for sql, p in _plans(conn, lambda c: zotero_sync_folders._Folders.follow_ups(plan, c))
                    if "json_each" in sql]
        conn.rollback()
    assert "SEARCH zotero_objects USING INDEX sqlite_autoindex_zotero_objects_1 (key=?)" in found, found
    assert "MULTI-INDEX OR" not in found, found


def test_an_item_removed_for_good_reads_its_page_once(me, made, fake, monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    page = _rows(ws)[keys["attention"]]["page_id"]
    real_side, real_gone = zotero_sync_gone.page_side, zotero_sync.items_gone
    reads, inside = [], []

    def side(conn, ws_, page_id, **kw):
        if inside and page_id == page:
            reads.append(page_id)
        return real_side(conn, ws_, page_id, **kw)

    def gone(r, keys_):
        inside.append(1)
        try:
            return real_gone(r, keys_)
        finally:
            inside.clear()

    monkeypatch.setattr(zotero_sync_gone, "page_side", side)
    monkeypatch.setattr(zotero_sync, "items_gone", gone)
    fake.delete(LIB, keys["attention"])  # with every child
    _sync(me, ws)
    assert _block(ws, page)["parent"] != "root"
    assert len(reads) == 1, reads


def test_the_folders_a_pass_confirms_read_the_pages_once(me, made, fake, owner_id, monkeypatch):
    ws = _link(me)
    _sync(me, ws)
    _folder_ops(ws, owner_id, [{"op": "insert", "id": f"zbF{n}", "parent": FOLDERS, "content": f"New {n}"}
                               for n in range(5)])
    reads = []
    real = zotero_sync_folders._filed_pages
    monkeypatch.setattr(zotero_sync_folders, "_filed_pages",
                        lambda conn, fids: (reads.append(fids), real(conn, fids))[1])
    _sync(me, ws)
    assert len(reads) <= 2 and {f"New {n}" for n in range(5)} <= {c["name"] for c in fake.collections(LIB)}
