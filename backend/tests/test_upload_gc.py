"""Unreferenced uploads (gamma/upload_gc.py): a file whose last reference
goes is recorded in ``upload_orphans`` and kept — still served — for 30
days, and a reference that comes back clears the record: an undo, a cut
pasted in a later batch, a block moved to another page, a re-attached PDF.
A batch hands over only the names it dropped (none while typing in a block
that keeps its image); the full pass is one scan that knows every reference
form; the purge deletes only what is still unreferenced and refuses a
database that looks wrong, and takes a backlog a cap at a time."""

import io
import json
import logging
import os
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from conftest import doc_props, login, make_folder, make_user, workspace_of
from gamma import ops, storage, upload_gc
from gamma.search import index as text_index
from gamma.db import connect_pages_db, page_now, ws_uploads_dir

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
PDF = b"%PDF-1.4 upload gc\n" + b"q" * 500 + b"\n%%EOF\n"
DAY = 86400


@pytest.fixture
def gc(monkeypatch):
    """No grace (tests upload and drop within milliseconds), and the
    module's thread kept out of the way: checks run when a test flushes."""
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 0)
    monkeypatch.setattr(upload_gc, "DEBOUNCE_S", 3600)
    monkeypatch.setattr(upload_gc, "_next_full", None)


@pytest.fixture
def ann(gc):
    make_user("gc_ann", "pw")
    return login("gc_ann", "pw"), workspace_of("gc_ann")


def _page(c, title="p"):
    return c.post("/api/pages", json={"title": title}).json()["id"]


def _ops(c, page_id, batch):
    r = c.post(f"/api/pages/{page_id}/ops", json={"client": "t", "ops": batch})
    assert r.status_code == 200, r.text
    return r.json()


def _image(c, tag: str):
    r = c.post("/api/upload-image", files={"file": (f"{tag}.png", PNG + tag.encode(), "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    return url, url.rsplit("/", 1)[1]


def _orphans(ws) -> dict:
    """``{name: since}`` once the scheduled checks have run."""
    upload_gc.flush(ws)
    with upload_gc.guard(ws), closing(connect_pages_db(ws)) as conn:
        return dict(conn.execute("SELECT name, since FROM upload_orphans").fetchall())


def _age(ws, name, days):
    """Pretend the file became unreferenced ``days`` ago and was written then."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with closing(connect_pages_db(ws)) as conn:
        conn.execute("INSERT OR REPLACE INTO upload_orphans (name, since) VALUES (?, ?)", (name, since))
        conn.commit()
    t = time.time() - days * DAY
    os.utime(ws_uploads_dir(ws) / name, (t, t))


# --- a dropped reference is recorded, not deleted -------------------------------------

def test_undo_of_a_delete_finds_the_file(ann):
    c, ws = ann
    url, name = _image(c, "undo")
    page = _page(c)
    md = f"figure: ![fig]({url})"
    _ops(c, page, [{"op": "insert", "id": "gcA", "parent": page, "content": md}])
    _ops(c, page, [{"op": "delete", "id": "gcA"}])
    assert name in _orphans(ws)
    assert c.get(url).status_code == 200  # still served while recorded
    _ops(c, page, [{"op": "insert", "id": "gcA", "parent": page, "content": md}])  # Ctrl+Z
    assert name not in _orphans(ws)
    assert c.get(url).status_code == 200


def test_cut_and_paste_in_a_later_batch(ann):
    c, ws = ann
    url, name = _image(c, "cut")
    page = _page(c)
    _ops(c, page, [{"op": "insert", "id": "gcX", "parent": page, "content": f"x ![i]({url})"},
                   {"op": "insert", "id": "gcY", "parent": page, "content": "y"}])
    _ops(c, page, [{"op": "set", "id": "gcX", "content": "x "}])               # Ctrl+X
    assert name in _orphans(ws)
    _ops(c, page, [{"op": "set", "id": "gcY", "content": f"y ![i]({url})"}])  # Ctrl+V, a batch later
    assert name not in _orphans(ws) and c.get(url).status_code == 200


def test_block_moved_to_another_page_keeps_its_file(ann):
    c, ws = ann
    url, name = _image(c, "move")
    p1, p2 = _page(c, "one"), _page(c, "two")
    _ops(c, p1, [{"op": "insert", "id": "gcM", "parent": p1, "content": f"![c]({url})"}])
    _ops(c, p1, [{"op": "delete", "id": "gcM"}])
    _ops(c, p2, [{"op": "insert", "id": "gcM2", "parent": p2, "content": f"![c]({url})"}])
    assert name not in _orphans(ws) and c.get(url).status_code == 200


def test_a_batch_hands_over_only_what_it_dropped(ann, monkeypatch):
    c, ws = ann
    reading = make_folder(c, "Reading")
    seen = []
    monkeypatch.setattr(upload_gc, "schedule", lambda w, names: seen.append(sorted(names)))
    url, name = _image(c, "typing")
    page = _page(c)
    _ops(c, page, [{"op": "insert", "id": "gcT", "parent": page, "content": f"a ![i]({url})"}])
    # typing in a block that keeps its image, a props patch on it: nothing to check
    _ops(c, page, [{"op": "set", "id": "gcT", "content": f"ab ![i]({url})", "props": {"color": "red"}}])
    # a cut and paste within one batch changes nothing either
    _ops(c, page, [{"op": "delete", "id": "gcT"},
                   {"op": "insert", "id": "gcT2", "parent": page, "content": f"![i]({url})"}])
    # filing a PDF page in a folder drops nothing
    doc = c.post("/api/uploads", files={"file": ("a.pdf", io.BytesIO(PDF + b"typing"), "application/pdf")}).json()
    assert c.post(f"/api/pages/{page}/attachment", json={"doc_id": doc["doc_id"]}).status_code == 200
    assert c.put(f"/api/blocks/{page}", json={"properties": {"folders": [reading]}}).status_code == 200
    assert seen == [[], [], [], [], []]
    # only a change that really drops a name hands it over
    _ops(c, page, [{"op": "set", "id": "gcT2", "content": "gone"}])
    assert seen[-1] == [name]


def test_a_deleted_page_hands_over_its_files(ann):
    c, ws = ann
    url, name = _image(c, "pagedel")
    doc = c.post("/api/uploads", files={"file": ("b.pdf", io.BytesIO(PDF + b"pagedel"), "application/pdf")}).json()
    page = _page(c, "doomed")
    c.post(f"/api/pages/{page}/attachment", json={"doc_id": doc["doc_id"]})
    _ops(c, page, [{"op": "insert", "id": "gcD", "parent": page, "content": f"![i]({url})"}])
    with closing(connect_pages_db(ws)) as conn:  # the page deleted for good
        assert ops.delete_page(ws, conn, page, actor="gc_ann")["dropped_uploads"] == sorted(
            [name, f"{doc['doc_id']}.pdf"])
    orphans = _orphans(ws)
    assert name in orphans and f"{doc['doc_id']}.pdf" in orphans
    assert (ws_uploads_dir(ws) / name).is_file()


def test_only_a_deleted_pdf_block_costs_the_library_wide_cleanup(ann, monkeypatch):
    c, ws = ann
    calls = []
    real = text_index.purge_unused
    monkeypatch.setattr(text_index, "purge_unused", lambda w, conn: (calls.append(w), real(w, conn)))
    page = _page(c)
    _ops(c, page, [{"op": "insert", "id": "gcL1", "parent": page, "content": "plain"},
                   {"op": "insert", "id": "gcL2", "parent": page, "content": "", "props": doc_props("d" * 24)}])
    _ops(c, page, [{"op": "delete", "id": "gcL1"}])
    assert calls == []
    _ops(c, page, [{"op": "delete", "id": "gcL2"}])
    assert calls == [ws]


# --- the full pass --------------------------------------------------------------------

def test_the_upload_reference_grammar():
    refs = storage.upload_refs(
        "![a](/api/uploads/0123abcd.png). [w](/api/uploads/beef01.safetensors) "
        "[p](/api/uploads/cafe02.pdf#page=2) /api/uploads/dead03.jpg?ws=x",
        json.dumps({"ink_url": "/api/uploads/f00d04.ink", **doc_props("facade05", url="/api/uploads/b0b06.pdf")}))
    assert refs == {"0123abcd.png", "beef01.safetensors", "cafe02.pdf", "dead03.jpg",
                    "f00d04.ink", "facade05.pdf", "b0b06.pdf"}
    assert storage.upload_refs("plain text", {"color": "red"}) == set()
    # a block's doc names its file whatever reads it: stored, read in place, a
    # copy (no address stored), cached under an address's hash, of any format
    assert storage.upload_refs("", doc_props("s1")) == {"s1.pdf"}
    assert storage.upload_refs("", doc_props("p1", at="path")) == {"p1.pdf"}
    assert storage.upload_refs("", doc_props(url="/api/uploads/c1.pdf")) == {"c1.pdf"}
    assert storage.upload_refs("", doc_props("u1", url="https://x.test/p.pdf")) == {"u1.pdf"}
    assert storage.upload_refs("", doc_props(url="https://x.test/p.pdf")) == set()
    text = {"doc": {"format": "text", "name": "main.tex", "hash": "t1", "ext": ".tex", "at": "store"}}
    assert storage.upload_refs("", text) == {"t1.tex"} == storage.upload_refs("", json.dumps(text), held=False)
    # a stem the grammar refuses, a doc that does not read, a flat doc_id: none
    assert storage.upload_refs("", doc_props("a.b")) == set()
    assert storage.upload_refs("", {"doc": {"hash": "../../etc"}}) == set()
    assert storage.upload_refs("", {"doc_id": "facade05"}) == set()
    # a highlight's position holds its files, shown or not
    held = {"pdf_position": {"pageNumber": 1, "doc_id": "h1", "prev": {"doc_id": "h0"}}}
    assert storage.upload_refs("", held) == {"h1.pdf", "h0.pdf"} and storage.upload_refs("", held, held=False) == set()
    assert storage.upload_refs("", {"pdf_position": {"doc_id": storage.UNSTORED}}) == set()


def test_reconcile_knows_every_reference_form(ann):
    c, ws = ann
    uploads = ws_uploads_dir(ws)
    page = _page(c, "forms")
    names = {}
    for tag, ext in (("chip", ".bin"), ("ink", ".ink"), ("long", ".safetensors"), ("src", ".pdf"),
                     ("doc", ".pdf"), ("dot", ".png"), ("stray", ".png")):
        filename, _ = storage.store_file(ws, PDF + tag.encode() if ext == ".pdf" else tag.encode() * 9, ext)
        names[tag] = filename
    _ops(c, page, [
        {"op": "insert", "id": "gcF1", "parent": page, "content": f"[data](/api/uploads/{names['chip']})"},
        {"op": "insert", "id": "gcF2", "parent": page, "content": "", "props": {"ink_url": f"/api/uploads/{names['ink']}"}},
        {"op": "insert", "id": "gcF3", "parent": page, "content": f"[w](/api/uploads/{names['long']})"},
        # a copy's file (its address is not stored) and a nested document's
        {"op": "insert", "id": "gcF4", "parent": page, "content": "", "props": doc_props(url=f"/api/uploads/{names['src']}")},
        {"op": "insert", "id": "gcF5", "parent": page, "content": "", "props": doc_props(names["doc"][:-4])},
        {"op": "insert", "id": "gcF6", "parent": page, "content": f"see /api/uploads/{names['dot']}."},
    ])
    # a write that never finished: not a stored file, and swept once old
    partial = uploads / ".partial"
    partial.mkdir(exist_ok=True)
    (partial / "dead-write").write_bytes(b"half")
    old = time.time() - 2 * DAY
    os.utime(partial / "dead-write", (old, old))
    upload_gc.reconcile(ws)
    orphans = _orphans(ws)
    assert names["stray"] in orphans
    assert not {n for t, n in names.items() if t != "stray"} & set(orphans)
    assert not (partial / "dead-write").exists()


def test_only_an_open_zotero_file_conflict_holds_the_pdf_it_names(client):
    ws = make_user("gc_zotero_rows", "pw-gc_zotero_rows")
    rows = [("zotero_file", 0, json.dumps({"doc_id": "keptOpen", "md5": "a"})),
            ("zotero_file", 1, json.dumps({"doc_id": "keptResolved"})),
            ("zotero_field", 0, json.dumps({"doc_id": "notAFile"})),
            ("zotero_file", 0, "{not json"),
            ("zotero_file", 0, json.dumps({"doc_id": "../outside"})),
            ("zotero_file", 0, json.dumps({"doc_id": ""}))]
    with closing(connect_pages_db(ws)) as conn:
        conn.executemany("INSERT INTO sync_conflicts (page_id, block_id, kind, base, at, resolved) "
                         "VALUES ('p', 'p', ?, ?, ?, ?)", [(k, b, page_now(), r) for k, r, b in rows])
        conn.commit()
        refs = upload_gc.referenced(conn)
    assert "keptopen.pdf" in refs
    assert not {"keptresolved.pdf", "notafile.pdf", "../outside.pdf", ".pdf"} & refs


def _anchor(doc_id, kind="area"):
    return json.dumps({"v": 1, "kind": kind, "block": "b", "doc_id": doc_id, "page": 1, "rects": [], "quote": "",
                       "box": None})


def _threads(ws, rows):
    """Comment rows written directly: ``(id, thread_id, anchor, resolved,
    orphaned, deleted)``."""
    stamp = page_now()
    with closing(connect_pages_db(ws)) as conn:
        conn.executemany(
            "INSERT INTO comments (id, thread_id, block_id, page_id, author, body, created_at, updated_at, "
            "resolved_at, deleted_at, orphaned_at, anchor) VALUES (?, ?, 'b', 'p', 'a', 'x', ?, ?, ?, ?, ?, ?)",
            [(i, t, stamp, stamp, stamp if resolved else None, stamp if deleted else None,
              stamp if orphaned else None, anchor) for i, t, anchor, resolved, orphaned, deleted in rows])
        conn.commit()


def test_an_open_thread_holds_the_version_it_was_opened_on_and_shows_none(client):
    ws = make_user("gc_thread_rows", "pw-gc_thread_rows")
    _threads(ws, [("tOpen", "tOpen", _anchor("heldOpen"), False, False, False),
                  ("tTomb", "tTomb", _anchor("heldTomb"), False, False, True),       # a tombstone with replies
                  ("tTombR", "tTomb", None, False, False, False),
                  ("tDone", "tDone", _anchor("heldResolved"), True, False, False),
                  ("tGone", "tGone", _anchor("heldOrphaned"), False, True, False),
                  ("tReply", "tOpen", _anchor("heldByAReply"), False, False, False),  # only a root's anchor
                  ("tNote", "tNote", _anchor("", kind="note"), False, False, False),
                  ("tBad", "tBad", "{not json", False, False, False),
                  ("tUnstored", "tUnstored", _anchor(storage.UNSTORED), False, False, False),
                  ("tOld", "tOld", None, False, False, False)])                       # a root from before step 54
    with closing(connect_pages_db(ws)) as conn:
        refs, shown = upload_gc._references(conn)
    assert {"heldopen.pdf", "heldtomb.pdf"} <= refs
    assert not {"heldresolved.pdf", "heldorphaned.pdf", "heldbyareply.pdf", ".pdf", "unstored.pdf"} & refs
    assert not {"heldopen.pdf", "heldtomb.pdf"} & shown


def test_the_purge_never_takes_a_version_an_open_thread_holds(ann):
    c, ws = ann
    _page(c, "held")                                                  # the purge needs a library that reads right
    name, _ = storage.store_file(ws, PDF + b"thread", ".pdf")
    held = name.removesuffix(".pdf")
    _age(ws, name, 31)
    _threads(ws, [("gcThread", "gcThread", _anchor(held), False, False, False)])
    upload_gc.reconcile(ws)
    assert storage.exists(ws, name) and name not in _orphans(ws)      # held: its row cleared, the file kept
    with closing(connect_pages_db(ws)) as conn:
        conn.execute("UPDATE comments SET resolved_at = ? WHERE id = 'gcThread'", (page_now(),))
        conn.commit()
    upload_gc.reconcile(ws)
    assert name in _orphans(ws) and storage.exists(ws, name)          # let go: kept its 30 days from now
    _age(ws, name, 31)
    upload_gc.reconcile(ws)
    assert not storage.exists(ws, name)


# --- the purge ------------------------------------------------------------------------

def test_purge_deletes_only_what_is_still_unreferenced_after_30_days(ann):
    c, ws = ann
    uploads = ws_uploads_dir(ws)
    page = _page(c, "purge")
    _ops(c, page, [{"op": "insert", "id": "gcP", "parent": page, "content": "keeper"}])
    gone_url, gone = _image(c, "purge-gone")
    young_url, young = _image(c, "purge-young")
    back_url, back = _image(c, "purge-back")
    again_url, again = _image(c, "purge-again")
    _age(ws, gone, 31)
    _age(ws, young, 29)
    _age(ws, back, 31)
    _age(ws, again, 31)
    # a reference came back (a writer that bypasses the op path left the row)
    _ops(c, page, [{"op": "insert", "id": "gcP2", "parent": page, "content": f"![b]({back_url})"}])
    _age(ws, back, 31)
    # the same bytes uploaded again: re-dated, their 30 days start over
    assert c.post("/api/upload-image", files={"file": ("a.png", PNG + b"purge-again", "image/png")}).json()["already_existed"]
    upload_gc.reconcile(ws)
    assert not (uploads / gone).exists() and c.get(gone_url).status_code == 404
    for name in (young, back, again):
        assert (uploads / name).is_file()
    orphans = _orphans(ws)
    assert gone not in orphans and back not in orphans
    assert young in orphans and again in orphans


def test_purge_refuses_a_database_without_pages(gc):
    make_user("gc_empty", "pw")
    c, ws = login("gc_empty", "pw"), workspace_of("gc_empty")
    url, name = _image(c, "empty-db")
    _age(ws, name, 40)
    # the library reads empty — an interrupted restore, a placeholder a sync
    # client left: every page gone at once
    with closing(connect_pages_db(ws)) as conn:
        conn.execute("DELETE FROM unified_blocks WHERE id != 'root'")
        conn.commit()
    assert upload_gc.reconcile(ws)["blocked"] == "its pages.db has no pages"
    # a file sqlite3 recreated empty: not even the root row
    with closing(connect_pages_db(ws)) as conn:
        conn.execute("DELETE FROM unified_blocks")
        conn.commit()
    assert upload_gc.reconcile(ws)["blocked"] == "its pages.db has no root row"
    assert (ws_uploads_dir(ws) / name).is_file()


def test_a_backlog_past_the_cap_goes_a_cap_at_a_time_the_longest_due_first(ann, monkeypatch, caplog):
    """A big Empty makes hundreds of files due on one day. One purge takes
    the cap's worth, the longest due first, and the next full pass the next
    ones, until none are left — never refusing the lot pass after pass."""
    c, ws = ann
    uploads = ws_uploads_dir(ws)
    page = _page(c, "many")
    _ops(c, page, [{"op": "insert", "id": "gcN", "parent": page, "content": "x"}])
    monkeypatch.setattr(upload_gc, "PURGE_MAX", 3)
    names = [_image(c, f"many-{i}")[1] for i in range(7)]
    for days, name in enumerate(names, start=31):  # the last the longest due
        _age(ws, name, days)
    oldest = names[::-1]
    with caplog.at_level(logging.WARNING, logger="gamma"):
        out = upload_gc.reconcile(ws)
    assert out["purged"] == oldest[:3] and out["blocked"] == "" and out["left"] == 4
    assert not any((uploads / n).exists() for n in oldest[:3])
    assert all((uploads / n).is_file() for n in oldest[3:]) and set(oldest[3:]) <= set(_orphans(ws))
    assert any("4 more due, left for the next pass (one purge deletes at most 3 of its" in r.message
               for r in caplog.records)
    out = upload_gc.reconcile(ws)  # the next pass
    assert out["purged"] == oldest[3:6] and out["left"] == 1
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="gamma"):
        out = upload_gc.reconcile(ws)
    assert out["purged"] == oldest[6:] and out["left"] == 0 and out["blocked"] == ""
    assert not any(r.name == "gamma" and "[uploads]" in r.message for r in caplog.records)  # caught up: no warning
    assert not any((uploads / n).exists() for n in names) and not set(names) & set(_orphans(ws))
    assert upload_gc.reconcile(ws)["purged"] == []


def test_a_chunk_skips_a_file_redated_meanwhile_and_takes_the_next(ann, monkeypatch):
    """The cap counts the files the purge deletes, not the ones it asks
    about: a due file an upload re-dated since the listing is kept, and the
    next due one fills its place."""
    c, ws = ann
    uploads = ws_uploads_dir(ws)
    page = _page(c, "redated")
    _ops(c, page, [{"op": "insert", "id": "gcR", "parent": page, "content": "x"}])
    monkeypatch.setattr(upload_gc, "PURGE_MAX", 2)
    names = [_image(c, f"redated-{i}")[1] for i in range(4)]
    for days, name in zip((40, 39, 38, 37), names):
        _age(ws, name, days)
    real_stat = storage.stat

    def stat(w, n):  # the same bytes uploaded again between the listing and the purge
        st = real_stat(w, n)
        return (st[0], time.time()) if n == names[0] else st

    monkeypatch.setattr(storage, "stat", stat)
    out = upload_gc.reconcile(ws)
    assert out["purged"] == names[1:3] and out["left"] == 1
    assert (uploads / names[0]).is_file() and (uploads / names[3]).is_file()


def test_a_chunk_still_needs_a_database_that_reads_right(gc, monkeypatch):
    """Past the cap or not, a pages.db that fails the checks loses nothing."""
    make_user("gc_chunk_db", "pw")
    c, ws = login("gc_chunk_db", "pw"), workspace_of("gc_chunk_db")
    monkeypatch.setattr(upload_gc, "PURGE_MAX", 2)
    names = [_image(c, f"chunk-db-{i}")[1] for i in range(4)]
    for name in names:
        _age(ws, name, 40)
    with closing(connect_pages_db(ws)) as conn:  # the library reads empty
        conn.execute("DELETE FROM unified_blocks WHERE id != 'root'")
        conn.commit()
    out = upload_gc.reconcile(ws)
    assert out["purged"] == [] and out["blocked"] == "its pages.db has no pages" and out["left"] == 4
    assert all((ws_uploads_dir(ws) / n).is_file() for n in names)


def test_the_purge_dates_each_due_file_and_lists_nothing_under_the_write_lock(gc, monkeypatch):
    """Under the write lock the purge dates each due file on its own
    (``storage.stat``), never by listing the workspace while writers wait,
    and asks no more once it has the cap's worth."""
    make_user("gc_dated", "pw")
    c, ws = login("gc_dated", "pw"), workspace_of("gc_dated")
    page = _page(c, "dated")
    _ops(c, page, [{"op": "insert", "id": "gcD", "parent": page, "content": "x"}])
    names = sorted(_image(c, f"dated-{i}")[1] for i in range(5))
    for name in names:
        _age(ws, name, 31)
    lists, stats = [], []
    real_list, real_stat, real_purge = storage.list, storage.stat, upload_gc._purge
    monkeypatch.setattr(storage, "list", lambda w: lists.append(w) or real_list(w))
    monkeypatch.setattr(storage, "stat", lambda w, n: stats.append(n) or real_stat(w, n))

    def purge(*args):  # everything _purge does, it does under the write lock
        lists.clear()
        stats.clear()
        return real_purge(*args)

    monkeypatch.setattr(upload_gc, "_purge", purge)
    monkeypatch.setattr(upload_gc, "PURGE_MAX", 2)
    out = upload_gc.reconcile(ws)
    assert out["purged"] == names[:2] and out["left"] == 3  # aged in name order: the first due first
    assert lists == [] and stats == names[:2]  # the cap's worth found: the last three never asked
    monkeypatch.setattr(upload_gc, "PURGE_MAX", 100)
    assert upload_gc.reconcile(ws)["purged"] == names[2:]
    assert lists == [] and sorted(stats) == names[2:]
    assert not any((ws_uploads_dir(ws) / n).exists() for n in names)


def test_purge_blocker_rules():
    class Conn:
        """A pages.db answering the blocker's questions as told."""
        def __init__(self, root=True, pages=True, check="ok"):
            self.answers = {"LIMIT 1": (1,) if pages else None,  # before "id = 'root'", which parent_id's holds
                            "id = 'root'": (1,) if root else None, "quick_check": (check,)}

        def execute(self, sql):
            answer = next(v for k, v in self.answers.items() if k in sql)
            return type("Cursor", (), {"fetchone": lambda self: answer})()

    assert upload_gc.purge_blocker(Conn()) == ""
    assert upload_gc.purge_blocker(Conn(root=False)) == "its pages.db has no root row"
    assert upload_gc.purge_blocker(Conn(pages=False)) == "its pages.db has no pages"
    assert upload_gc.purge_blocker(Conn(check="row 7 missing from index")).startswith("its pages.db fails quick_check")


def test_purge_cap_rules():
    assert upload_gc.purge_cap(1) == 10          # up to PURGE_FLOOR at once, whatever the share
    assert upload_gc.purge_cap(50) == 10         # a fifth of 50 is under the floor
    assert upload_gc.purge_cap(55) == 11         # over the floor: a fifth
    assert upload_gc.purge_cap(100) == 20
    assert upload_gc.purge_cap(10_000) == 100    # never more than PURGE_MAX
    for stored in range(0, 1000):  # a fifth rounded down, however the float rounds
        assert upload_gc.purge_cap(stored) == min(100, max(10, stored // 5))


def test_a_restored_database_starts_its_clocks_over(ann):
    c, ws = ann
    url, name = _image(c, "restored")
    _age(ws, name, 45)
    with closing(connect_pages_db(ws)) as conn:
        upload_gc.restart_clocks(conn)
    upload_gc.reconcile(ws)
    assert (ws_uploads_dir(ws) / name).is_file() and name in _orphans(ws)
