"""Unreferenced uploads (gamma/upload_gc.py): a file whose last reference
goes is recorded in ``upload_orphans`` and kept — still served — for 30
days, and a reference that comes back clears the record: an undo, a cut
pasted in a later batch, a block moved to another page, a re-attached PDF.
A batch hands over only the names it dropped (none while typing in a block
that keeps its image); the full pass is one scan that knows every reference
form; the purge deletes only what is still unreferenced and refuses a
database that looks wrong."""

import io
import os
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from conftest import login, make_folder, make_user, workspace_of
from gamma import ops, pdf_index, storage, upload_gc
from gamma.db import connect_pages_db, ws_uploads_dir

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
    real = pdf_index.purge_unused
    monkeypatch.setattr(pdf_index, "purge_unused", lambda w, conn: (calls.append(w), real(w, conn)))
    page = _page(c)
    _ops(c, page, [{"op": "insert", "id": "gcL1", "parent": page, "content": "plain"},
                   {"op": "insert", "id": "gcL2", "parent": page, "content": "", "props": {"doc_id": "d" * 24}}])
    _ops(c, page, [{"op": "delete", "id": "gcL1"}])
    assert calls == []
    _ops(c, page, [{"op": "delete", "id": "gcL2"}])
    assert calls == [ws]


# --- the full pass --------------------------------------------------------------------

def test_the_upload_reference_grammar():
    refs = storage.upload_refs(
        "![a](/api/uploads/0123abcd.png). [w](/api/uploads/beef01.safetensors) "
        "[p](/api/uploads/cafe02.pdf#page=2) /api/uploads/dead03.jpg?ws=x",
        '{"ink_url": "/api/uploads/f00d04.ink", "doc_id": "facade05", "source_url": "/api/uploads/b0b06.pdf"}')
    assert refs == {"0123abcd.png", "beef01.safetensors", "cafe02.pdf", "dead03.jpg",
                    "f00d04.ink", "facade05.pdf", "b0b06.pdf"}
    assert storage.upload_refs("plain text", {"color": "red"}) == set()
    assert storage.upload_refs("", {"doc_id": "../../etc"}) == set()


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
        {"op": "insert", "id": "gcF4", "parent": page, "content": "", "props": {"source_url": f"/api/uploads/{names['src']}"}},
        {"op": "insert", "id": "gcF5", "parent": page, "content": "", "props": {"doc_id": names["doc"][:-4]}},
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


def test_purge_refuses_too_many_files_at_once(ann, monkeypatch):
    c, ws = ann
    page = _page(c, "many")
    _ops(c, page, [{"op": "insert", "id": "gcN", "parent": page, "content": "x"}])
    monkeypatch.setattr(upload_gc, "PURGE_MAX", 3)
    names = [_image(c, f"many-{i}")[1] for i in range(4)]
    for name in names:
        _age(ws, name, 31)
    assert "more than one purge may delete" in upload_gc.reconcile(ws)["blocked"]
    assert all((ws_uploads_dir(ws) / n).is_file() for n in names)
    monkeypatch.setattr(upload_gc, "PURGE_MAX", 100)
    upload_gc.reconcile(ws)
    assert not any((ws_uploads_dir(ws) / n).exists() for n in names)


def test_purge_blocker_rules():
    class Conn:
        """A pages.db answering the blocker's questions as told."""
        def __init__(self, root=True, pages=True, check="ok"):
            self.answers = {"id = 'root'": (1,) if root else None, "LIMIT 1": (1,) if pages else None,
                            "quick_check": (check,)}

        def execute(self, sql):
            answer = next(v for k, v in self.answers.items() if k in sql)
            return type("Cursor", (), {"fetchone": lambda self: answer})()

    assert upload_gc.purge_blocker(Conn(), 1, 1) == ""
    assert upload_gc.purge_blocker(Conn(check="row 7 missing from index"), 1, 50).startswith("its pages.db fails quick_check")
    assert upload_gc.purge_blocker(Conn(), 10, 10) == ""             # up to PURGE_FLOOR at once, whatever the share
    assert upload_gc.purge_blocker(Conn(), 11, 50) != ""             # over the floor and over 20 %
    assert upload_gc.purge_blocker(Conn(), 11, 100) == ""
    assert upload_gc.purge_blocker(Conn(), 101, 10_000) != ""        # never more than PURGE_MAX


def test_a_restored_database_starts_its_clocks_over(ann):
    c, ws = ann
    url, name = _image(c, "restored")
    _age(ws, name, 45)
    with closing(connect_pages_db(ws)) as conn:
        upload_gc.restart_clocks(conn)
    upload_gc.reconcile(ws)
    assert (ws_uploads_dir(ws) / name).is_file() and name in _orphans(ws)
