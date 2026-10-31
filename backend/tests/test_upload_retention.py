"""Stored files kept for a review, and files kept for less than 30 days
(gamma/upload_gc.py, ``storage.upload_refs``). A highlight's
``pdf_position.doc_id`` and its ``prev.doc_id`` name the file its
rectangles were measured on: a reference, so a replaced PDF stays while a
pending highlight needs it and goes once the last one is resolved.
``retain`` gives a file a replacement let go its own shorter life, which a
reference written to the file again cancels and which never makes a
referenced file purgeable."""

import io
import json
import zipfile
from contextlib import closing

import pytest

from conftest import login, make_user, workspace_of
from gamma import jobs, storage, upload_gc
from gamma.db import connect_pages_db, ws_uploads_dir
from test_highlight_relocate import _line_rect, _pdf, _position
from test_upload_gc import DAY, PDF, _age, _ops, _orphans, _page, gc  # noqa: F401 — the gc fixture applies here too

A, B = "a" * 24, "b" * 24


@pytest.fixture
def owner(gc):  # noqa: F811 — the fixture imported above
    make_user("ret_owner", "pw")
    return login("ret_owner", "pw"), workspace_of("ret_owner")


def _stored_pdf(ws, tag: bytes) -> str:
    """A stored PDF's file name."""
    return f"{storage.store_pdf(ws, PDF + tag)[0]}.pdf"


def _retained(ws) -> dict:
    """``{name: keep_s}`` of the workspace's ``upload_retention`` rows."""
    with closing(connect_pages_db(ws)) as conn:
        return dict(conn.execute("SELECT name, keep_s FROM upload_retention").fetchall())


def _highlight(position, quote="") -> dict:
    return {"pdf_position": position, "quote": quote, "color": "#ffe28fa6"}


# --- a pending highlight holds its file ------------------------------------------------

def test_a_highlight_references_the_files_its_position_was_measured_on():
    prev = {"doc_id": B, "pageNumber": 1, "rects": [], "quote": ""}
    held = {"pdf_position": {"pageNumber": 1, "doc_id": A, "prev": prev}}
    assert storage.upload_refs("", held) == {f"{A}.pdf", f"{B}.pdf"}
    assert storage.upload_refs("", json.dumps(held)) == {f"{A}.pdf", f"{B}.pdf"}  # the stored text alike
    assert storage.upload_refs("", {"pdf_position": {"pageNumber": 1, "prev": prev}}) == {f"{B}.pdf"}
    # the stamp of a file shown from a URL alone, malformed ids, a position that names none: no file
    for position in ({"doc_id": storage.UNSTORED}, {"prev": {**prev, "doc_id": storage.UNSTORED}},
                     {"doc_id": "../../etc"}, {"doc_id": 7}, {"prev": "elsewhere"}, {}):
        assert storage.upload_refs("", {"pdf_position": {"pageNumber": 1, **position}}) == set()
    assert storage.upload_refs("", {"pdf_position": "not a position", "doc_id": A}) == {f"{A}.pdf"}


def test_the_full_pass_sees_a_file_only_a_highlight_names(owner):
    c, ws = owner
    held, before = _stored_pdf(ws, b"held"), _stored_pdf(ws, b"before")
    page = _page(c, "held files")
    _ops(c, page, [
        {"op": "insert", "id": "rtF1", "parent": page, "content": "",
         "props": _highlight({"pageNumber": 1, "doc_id": held[:-4]})},
        {"op": "insert", "id": "rtF2", "parent": page, "content": "",
         "props": _highlight({"pageNumber": 1, "prev": {"doc_id": before[:-4], "pageNumber": 1}})},
    ])
    with closing(connect_pages_db(ws)) as conn:
        # no page's doc_id on either row: only the nested "doc_id" brings them into the scan
        assert conn.execute("SELECT doc_id FROM unified_blocks WHERE id IN ('rtF1', 'rtF2')").fetchall() == [
            (None,), (None,)]
        assert {held, before} <= upload_gc.referenced(conn)
    upload_gc.reconcile(ws)
    assert not {held, before} & set(_orphans(ws))


def test_a_replaced_pdf_stays_while_a_highlight_names_it_and_goes_once_it_is_resolved(owner):
    c, ws = owner
    line = "Ultracold atoms in an optical lattice show a sharp transition"
    old = c.post("/api/uploads", files={"file": ("v1.pdf", io.BytesIO(_pdf([[(72, 100, line)]])),
                                                 "application/pdf")}).json()["doc_id"]
    new = c.post("/api/uploads", files={"file": ("v2.pdf", io.BytesIO(_pdf([[(72, 100, "Nothing of it is here")]])),
                                                 "application/pdf")}).json()["doc_id"]
    page = _page(c, "replaced")
    assert c.post(f"/api/pages/{page}/attachment", json={"doc_id": old}).status_code == 200
    _ops(c, page, [{"op": "insert", "id": "rtR", "parent": page, "content": "",
                    "props": _highlight(_position(1, _line_rect(72, 100, line)), line)}])
    r = c.put(f"/api/pages/{page}/attachment", json={"doc_id": new})
    assert r.status_code == 200, r.text
    assert jobs.wait(r.json()["job"]["id"])["state"] == "done"
    position = c.get("/api/blocks/rtR").json()["properties"]["pdf_position"]
    assert position["lost"] is True and position["doc_id"] == old  # its text is nowhere in the new file
    # the page let go of the old file, and the highlight still names it: no row, by the check or the full pass
    assert f"{old}.pdf" not in _orphans(ws)
    upload_gc.reconcile(ws)
    assert f"{old}.pdf" not in _orphans(ws)
    # moved by hand: a position on the page's file names none, and the check records the old file
    _ops(c, page, [{"op": "set", "id": "rtR", "props": {"pdf_position": _position(1, _line_rect(72, 300, line))}}])
    assert f"{old}.pdf" in _orphans(ws)
    assert (ws_uploads_dir(ws) / f"{old}.pdf").is_file()


def test_the_file_a_pending_highlight_is_matched_from_stays_through_later_replacements(owner):
    c, ws = owner
    cited = "Similar schemes were demonstrated with ions [{n}] and with superconducting circuits"

    def version(n=None, lower=False):
        lines = [(72, 100, "Trapped atoms in optical tweezers form arrays of qubits")]
        lines += [(72, 114, "A paragraph added in this revision of the paper")] if lower else []
        return _pdf([lines + ([(72, 128 if lower else 114, cited.format(n=n))] if n else [])])

    def upload(pdf, name):
        return c.post("/api/uploads", files={"file": (name, io.BytesIO(pdf), "application/pdf")}).json()["doc_id"]

    def replace(doc, **body):
        r = c.put(f"/api/pages/{page}/attachment", json={"doc_id": doc, **body})
        assert r.status_code == 200, r.text
        assert jobs.wait(r.json()["job"]["id"])["state"] == "done"
        return c.get("/api/blocks/rtV").json()["properties"]["pdf_position"]

    v1 = upload(version(3), "v1.pdf")
    page = _page(c, "versions")
    assert c.post(f"/api/pages/{page}/attachment", json={"doc_id": v1}).status_code == 200
    _ops(c, page, [{"op": "insert", "id": "rtV", "parent": page, "content": "",
                    "props": _highlight(_position(1, _line_rect(72, 114, cited.format(n=3))), cited.format(n=3))}])
    # v2, a build a sync sent: renumbered, pending, matched from v1 at the next replacement, so v1 stays, and as
    # the highlight's reference it loses the day the sync asked for
    on_v2 = replace(upload(version(4), "v2.pdf"), keep_old_days=1)
    assert on_v2["approx"] is True and on_v2["prev"]["doc_id"] == v1
    assert f"{v1}.pdf" not in _orphans(ws) and f"{v1}.pdf" not in _retained(ws)
    # v3 before anyone looked: still dashed, still v1's prev; v2 held only the rectangles the run moved off it,
    # so it goes after the sync's day
    v2 = c.get(f"/api/blocks/{page}").json()["properties"]["doc_id"]
    on_v3 = replace(upload(version(5, lower=True), "v3.pdf"), keep_old_days=1)
    assert on_v3["approx"] is True and on_v3["prev"]["doc_id"] == v1 and "doc_id" not in on_v3
    orphans = _orphans(ws)
    assert f"{v2}.pdf" in orphans and f"{v1}.pdf" not in orphans and _retained(ws)[f"{v2}.pdf"] == DAY
    _age(ws, f"{v1}.pdf", 31)  # an old file, and a row a writer past the op path left
    out = upload_gc.reconcile(ws)
    assert f"{v1}.pdf" in out["cleared"] and f"{v1}.pdf" not in out["purged"]
    assert (ws_uploads_dir(ws) / f"{v1}.pdf").is_file()
    # accepted on v3: nothing names v1 any more; the undo names it again
    accepted = {k: v for k, v in on_v3.items() if k not in ("approx", "match", "prev", "doc_id")}
    _ops(c, page, [{"op": "set", "id": "rtV", "props": {"pdf_position": accepted}}])
    assert f"{v1}.pdf" in _orphans(ws)
    _ops(c, page, [{"op": "set", "id": "rtV", "props": {"pdf_position": on_v3}}])
    assert f"{v1}.pdf" not in _orphans(ws)
    # v4 without the passage: hollow at v1's place, naming v1; v3, whose rectangles it no longer has, goes
    v3 = c.get(f"/api/blocks/{page}").json()["properties"]["doc_id"]
    on_v4 = replace(upload(version(), "v4.pdf"))
    assert on_v4["lost"] is True and on_v4["doc_id"] == v1 and "prev" not in on_v4
    orphans = _orphans(ws)
    assert f"{v3}.pdf" in orphans and f"{v1}.pdf" not in orphans
    # discarded: v1 is no one's, kept the usual 30 days for an undo
    _ops(c, page, [{"op": "delete", "id": "rtV"}])
    assert f"{v1}.pdf" in _orphans(ws) and (ws_uploads_dir(ws) / f"{v1}.pdf").is_file()
    assert f"{v1}.pdf" not in _retained(ws)


# --- a shorter life ------------------------------------------------------------------

def test_a_retained_file_is_recorded_at_once_and_goes_after_its_own_keep(owner, monkeypatch):
    c, ws = owner
    _page(c, "keeper")  # the purge refuses a library without pages
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 15 * 60)
    short, usual = _stored_pdf(ws, b"short"), _stored_pdf(ws, b"usual")
    upload_gc.retain(ws, short, DAY)
    # stored a moment ago, yet recorded: the grace is for an upload not attached yet
    assert short in _orphans(ws) and _retained(ws)[short] == DAY
    upload_gc.retain(ws, "f" * 24 + ".pdf", DAY)  # a file not stored: nothing to keep
    assert "f" * 24 + ".pdf" not in _retained(ws)
    _age(ws, short, 2)
    _age(ws, usual, 2)
    out = upload_gc.reconcile(ws)
    assert short in out["purged"] and not (ws_uploads_dir(ws) / short).exists()
    assert short not in _retained(ws)  # the row goes with the file
    assert (ws_uploads_dir(ws) / usual).is_file() and usual in _orphans(ws)  # 2 of its 30 days


def test_a_replacement_that_asks_for_it_keeps_the_old_file_a_day(owner, monkeypatch):
    c, ws = owner
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 15 * 60)
    old, new = (c.post("/api/uploads", files={"file": (f"{tag}.pdf", io.BytesIO(PDF + tag.encode()),
                                                        "application/pdf")}).json()["doc_id"] for tag in ("b1", "b2"))
    page = _page(c, "build")
    assert c.post(f"/api/pages/{page}/attachment", json={"doc_id": old}).status_code == 200
    r = c.put(f"/api/pages/{page}/attachment", json={"doc_id": new, "keep_old_days": 1})
    assert r.status_code == 200, r.text
    assert _retained(ws)[f"{old}.pdf"] == DAY and f"{old}.pdf" in _orphans(ws)
    _age(ws, f"{old}.pdf", 2)
    assert f"{old}.pdf" in upload_gc.reconcile(ws)["purged"]
    assert c.get(f"/api/blocks/{page}").json()["properties"]["doc_id"] == new


def test_a_retained_file_a_highlight_names_is_kept_and_its_keep_applies_once_the_name_goes(owner):
    c, ws = owner
    held = _stored_pdf(ws, b"held build")
    page = _page(c, "pending")
    _ops(c, page, [{"op": "insert", "id": "rtK", "parent": page, "content": "",
                    "props": _highlight({"pageNumber": 1, "doc_id": held[:-4]})}])
    upload_gc.retain(ws, held, 60)
    assert held not in _orphans(ws) and _retained(ws)[held] == 60
    _age(ws, held, 2)  # an old file, and a row a writer that bypasses the op path left
    out = upload_gc.reconcile(ws)
    assert held in out["cleared"] and held not in out["purged"]
    assert (ws_uploads_dir(ws) / held).is_file() and _retained(ws)[held] == 60
    # resolved: the name goes, the file is recorded, and after its minute it is purged
    _ops(c, page, [{"op": "set", "id": "rtK", "props": {"pdf_position": {"pageNumber": 1}}}])
    assert held in _orphans(ws)
    _age(ws, held, 0.01)
    assert held in upload_gc.reconcile(ws)["purged"]


def test_a_reference_written_again_cancels_the_shorter_life(owner):
    c, ws = owner
    again, direct = _stored_pdf(ws, b"again"), _stored_pdf(ws, b"direct")
    upload_gc.retain(ws, again, 60)
    upload_gc.retain(ws, direct, 60)
    page = _page(c, "again")
    _ops(c, page, [{"op": "insert", "id": "rtC", "parent": page, "content": f"[v1](/api/uploads/{again})"}])
    assert again not in _retained(ws) and again not in _orphans(ws)
    with closing(connect_pages_db(ws)) as conn:  # a writer's claim, by a name in another case
        upload_gc.claim(conn, [direct.upper()])
        conn.commit()
    assert direct not in _retained(ws) and direct not in _orphans(ws)


def test_the_full_pass_drops_the_shorter_life_of_a_file_no_longer_stored(owner):
    c, ws = owner
    _page(c, "keeper")
    name = _stored_pdf(ws, b"vanished")
    upload_gc.retain(ws, name, 60)
    storage.delete(ws, name)
    upload_gc.reconcile(ws)
    assert name not in _retained(ws) and name not in _orphans(ws)


def test_a_restored_database_forgets_the_shorter_lives(owner):
    c, ws = owner
    name = _stored_pdf(ws, b"restored")
    upload_gc.retain(ws, name, 60)
    _age(ws, name, 2)
    with closing(connect_pages_db(ws)) as conn:
        upload_gc.restart_clocks(conn)
    upload_gc.reconcile(ws)
    assert name not in _retained(ws)
    assert (ws_uploads_dir(ws) / name).is_file() and name in _orphans(ws)


def test_a_file_another_page_shows_gets_no_shorter_life(owner, monkeypatch):
    c, ws = owner
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 15 * 60)

    def upload(tag):
        return c.post("/api/uploads", files={"file": (f"{tag}.pdf", io.BytesIO(PDF + tag.encode()),
                                                      "application/pdf")}).json()["doc_id"]

    def attach(page_id, **body):
        assert c.post(f"/api/pages/{page_id}/attachment", json=body).status_code == 200

    def replace(page_id, **body):
        r = c.put(f"/api/pages/{page_id}/attachment", json=body)
        assert r.status_code == 200, r.text

    old, new, mine = upload("s1"), upload("s2"), upload("s3")
    page, copy = _page(c, "the build"), _page(c, "a copy of it")
    attach(page, doc_id=old)
    attach(copy, source_url=f"/api/uploads/{old}.pdf")   # a duplicated page shows the file by its address alone
    # the build moves on: the copy still shows the file it let go of, which keeps its 30 days
    replace(page, doc_id=new, keep_old_days=1)
    assert f"{old}.pdf" not in _retained(ws) and f"{old}.pdf" not in _orphans(ws)
    # a hand replacement of the copy drops the last reference: the usual 30 days, not one
    replace(copy, doc_id=mine)
    assert f"{old}.pdf" not in _retained(ws)
    _age(ws, f"{old}.pdf", 2)
    assert f"{old}.pdf" not in upload_gc.reconcile(ws)["purged"] and (ws_uploads_dir(ws) / f"{old}.pdf").is_file()

    # a copy that follows a build lets go of the file the first page still carries: no shorter life either
    first, follower = upload("s4"), upload("s5")
    page2, copy2 = _page(c, "the second build"), _page(c, "its follower")
    attach(page2, doc_id=first)
    attach(copy2, source_url=f"/api/uploads/{first}.pdf")
    replace(copy2, doc_id=follower, keep_old_days=1)
    assert f"{first}.pdf" not in _retained(ws)
    assert c.get(f"/api/blocks/{page2}").json()["properties"]["doc_id"] == first


def test_the_full_pass_drops_the_shorter_life_of_a_file_a_block_shows_again(owner):
    c, ws = owner
    name = _stored_pdf(ws, b"shown again")
    upload_gc.retain(ws, name, 60)
    assert _retained(ws)[name] == 60
    with closing(connect_pages_db(ws)) as conn:  # a writer past the op path (an import, a mirror) shows it again
        conn.execute("UPDATE unified_blocks SET content = ? WHERE id = ?",
                     (f"[it](/api/uploads/{name})", _page(c, "shows it")))
        conn.commit()
    upload_gc.reconcile(ws)
    assert name not in _retained(ws) and name not in _orphans(ws)
    upload_gc.retain(ws, name, 60)  # shown: no shorter life at all
    assert name not in _retained(ws)


# --- what travels to another copy -------------------------------------------------------

def test_a_share_s_export_and_a_mirror_carry_only_the_files_the_pages_show(owner):
    from fastapi.testclient import TestClient

    from gamma import sync_tree
    from gamma.app import app

    c, ws = owner
    shown, held = _stored_pdf(ws, b"the build shown"), _stored_pdf(ws, b"the build before")
    page = _page(c, "shared build")
    assert c.post(f"/api/pages/{page}/attachment", json={"doc_id": shown[:-4]}).status_code == 200
    position = {"pageNumber": 1, "doc_id": shown[:-4], "prev": {"doc_id": held[:-4], "pageNumber": 1}}
    _ops(c, page, [{"op": "insert", "id": "rtS", "parent": page, "content": "", "props": _highlight(position)}])
    token = c.post(f"/api/share/{page}").json()["token"]
    anon = TestClient(app)

    def uploads(response):
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            return sorted(n.split("/", 1)[1] for n in z.namelist() if n.startswith("uploads/"))

    carried = uploads(anon.get(f"/api/pages/{page}/export", params={"mode": "gamma", "share": token}))
    assert carried == [shown]
    for name in carried:  # every file the share's export carries, the share serves
        assert anon.get(f"/api/uploads/{name}", params={"share": token}).status_code == 200
    assert anon.get(f"/api/uploads/{held}", params={"share": token}).status_code == 403
    # the owner's own export is a copy of the library: the held file comes along for the review
    assert held in uploads(c.get(f"/api/pages/{page}/export", params={"mode": "gamma"}))
    # a mirror (and a page published to the share host) moves what the blocks show
    blocks = [{"content": "", "props": {"doc_id": shown[:-4]}}, {"content": "", "props": _highlight(position)}]
    assert sync_tree.upload_refs(blocks, held=False) == {shown}
    assert sync_tree.upload_refs(blocks) == {shown, held}
