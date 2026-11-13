"""The commented version kept (gamma/feedback/kept.py): a work folder's
build read in place has its bytes stored once a thread is opened on it,
each rebuild while the thread is open stores the incoming build, so the
relocation run reads the outgoing one from the store and places an area by
its text again and again; the thread's version is held until its resolve,
which gives it the replacement's day, and a reopen claims it back; a work
folder's picture is kept the same way, a text file's never; the repair stores what the quota
refused; the place's source line is recorded on the anchor at the thread's
open; the owner's copy of a page (a Gamma export, a backup merge) carries
the versions its open threads hold."""

import io
import json
import logging
import sys
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from conftest import (account_of, doc_props, login, make_folder, make_page, make_user, mark_work_folder, thread_on,
                      work_props, workspace_of)
from gamma import comments, folder_links, gamma_sync, jobs, linked_files, pdf_meta, storage, upload_gc, ws_backup
from gamma.vault import disk as vault_disk, kinds, store as vault_store
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page, new_block_id
from gamma.db import connect_pages_db, connect_users_db
from gamma.documents.model import document, page_document
from gamma.feedback import anchors, kept
from gamma.vault.local_remote import LocalRemote
from test_highlight_relocate import H, W, _pdf as _lines, _position
from test_vault_sync import Vault, insert

OWNER = "fkept_owner"
ROWS = [("Alpha one", "Beta one"), ("Alpha two", "Beta two"), ("Alpha three", "Beta three")]
AREA = {"x1": 66, "y1": 88, "x2": 260, "y2": 134}   # over the table of the first build
PAPER = Path(__file__).parent / "samples" / "synctex" / "paper"   # a LaTeX paper built twice, with its SyncTeX


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """test_work_folder_rounds' quick rounds; the comment writes heard by
    the kept version alone; the upload checks run when a test flushes, with
    no grace."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})
    monkeypatch.setattr(comments, "write_listeners", [kept.after_comment])
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 0)
    monkeypatch.setattr(upload_gc, "DEBOUNCE_S", 3600)
    monkeypatch.setattr(upload_gc, "_next_full", None)


class KeptVault(Vault):
    """A work folder's vault of this module's account that never pulls."""

    def __init__(self, owner, root):
        self.owner, self.root = owner, root
        self.ws, self.user = workspace_of(OWNER), account_of(OWNER)
        self.folder = make_folder(owner, f"FK {uuid.uuid4().hex[:8]}")
        root.mkdir(parents=True, exist_ok=True)
        marker = vault_disk.make_marker(root)
        self.link = {"id": uuid.uuid4().hex, "workspace_id": self.ws, "folder_id": self.folder, "path": str(root),
                     "pull": 0, "push": folder_links.PUSH_ALL, "created_by": self.user,
                     "state": {"vault": marker["id"]}, "kind": "folder", "remote_url": "", "library": 0}
        self.remote = LocalRemote(self.ws, self.folder, self.user)
        mark_work_folder(owner, self.folder, pull=False)

    def props(self, block_id, page_id=None) -> dict:
        return self.blocks(page_id or block_id)[block_id]["props"]


@pytest.fixture
def work(owner, tmp_path):
    made = []

    def make(name="w"):
        v = KeptVault(owner, tmp_path / name)
        made.append(v)
        return v
    yield make
    for v in made:
        vault_store.forget_link(v.link["id"])


def _build(words: str, dy: float = 0) -> bytes:
    """A build of the paper: a line of ``words`` and a small table ``dy``
    below where the first build has it."""
    return _lines([[(72, 60, f"the {words} build of the paper")]
                   + [(x, 100 + dy + 14 * r, text) for r, row in enumerate(ROWS) for x, text in zip((72, 200), row)]])


def _rebuilt(v, data: bytes, age: int) -> str:
    """``main.pdf`` rewritten as ``data`` and the round run; its hash, once
    the relocation run of the page's highlights is done."""
    v.write("main.pdf", data, age=age)
    v.sync()
    page, doc = v.page_of("main.pdf"), storage.content_digest(data)
    assert v.props(page)["doc"]["hash"] == doc and v.props(page)["doc"]["at"] == "path"
    job = jobs.latest(v.user, "relocate-highlights", f"{page}:{doc}")
    assert job is not None and jobs.wait(job["id"])["state"] == "done"
    return doc


def _rows(ws, table, name):
    with connect_pages_db(ws) as conn:
        return conn.execute(f"SELECT * FROM {table} WHERE name = ?", (name,)).fetchall()


def test_an_open_thread_keeps_each_build_stored_and_the_relocation_reads_them(work, owner):
    v = work()
    first = _build("first")
    v.write("main.pdf", first, age=30)
    v.sync()
    page, ws = v.page_of("main.pdf"), v.ws
    d1 = storage.content_digest(first)
    area = new_block_id()
    v.edit(page, insert(page, "", {"pdf_position": _position(1, AREA, area=True), "color": "#ffe28fa6"}, area))
    assert not storage.exists(ws, f"{d1}.pdf")                            # a highlight alone stores nothing
    thread = thread_on(owner, page, area, "move the legend inside the plot")
    assert kept.wait() and storage.exists(ws, f"{d1}.pdf")
    with connect_pages_db(ws) as conn:
        anchor = anchors.parse(conn.execute("SELECT anchor FROM comments WHERE id = ?", (thread["id"],)).fetchone()[0])
    assert anchor["kind"] == "area" and anchor["doc_id"] == d1

    # a rebuild: the incoming stored, the outgoing served from the store, the area placed by its text
    d2 = _rebuilt(v, _build("second", 50), age=20)
    assert storage.exists(ws, f"{d2}.pdf") and owner.get(f"/api/uploads/{d1}.pdf").content == first
    position = v.props(area, page)["pdf_position"]
    assert position["boundingRect"] == pytest.approx({"x1": 66, "y1": 138, "x2": 260, "y2": 184}, abs=0.05)
    assert not {"approx", "doc_id", "lost"} & set(position)

    # a second one still finds it: its reference, the build before, was stored as it came
    d3 = _rebuilt(v, _build("third", 120), age=10)
    position = v.props(area, page)["pdf_position"]
    assert position["boundingRect"] == pytest.approx({"x1": 66, "y1": 208, "x2": 260, "y2": 254}, abs=0.05)
    assert not {"approx", "doc_id", "lost"} & set(position) and storage.exists(ws, f"{d3}.pdf")
    with connect_pages_db(ws) as conn:
        assert f"{d1}.pdf" in upload_gc.referenced(conn)                  # held by the open thread alone
    upload_gc.flush(ws)
    assert not _rows(ws, "upload_orphans", f"{d1}.pdf")

    # resolved: let go, with the day its replacement gave it; reopened: claimed again
    r = owner.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": True})
    assert r.status_code == 200, r.text
    upload_gc.flush(ws)
    assert _rows(ws, "upload_orphans", f"{d1}.pdf")
    assert [row[1] for row in _rows(ws, "upload_retention", f"{d1}.pdf")] == [gamma_sync.KEEP_OLD_DAYS * 86400]
    assert owner.get(f"/api/uploads/{d1}.pdf").content == first          # still served for its day
    r = owner.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": False})
    assert r.status_code == 200, r.text
    assert not _rows(ws, "upload_orphans", f"{d1}.pdf") and not _rows(ws, "upload_retention", f"{d1}.pdf")

    # no thread open on it any more: the next build is read in place alone
    owner.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": True}).raise_for_status()
    d4 = _rebuilt(v, _build("fourth", 160), age=5)
    assert not storage.exists(ws, f"{d4}.pdf")


def test_a_pictures_version_is_kept_while_its_thread_is_open(work, owner):
    """A work folder's PNG is a page of the file class (step 3): a thread on
    it stores the version it shows, under its own extension, and holds it
    (``anchor.ext``); a rebuild while the thread is open stores the incoming
    version too; the resolve lets the commented one go once the page no
    longer shows it."""
    def png(color) -> bytes:
        out = io.BytesIO()
        Image.new("RGB", (1200, 800), color).save(out, "PNG")
        return out.getvalue()

    v = work("picture")
    data = png("white")
    v.write("figures/fig.png", data, age=30)
    v.sync()
    page, ws, name = v.page_of("figures/fig.png"), v.ws, f"{storage.content_digest(data)}.png"
    doc = v.props(page)["doc"]
    assert (doc["format"], doc["hash"], doc["ext"], doc["at"]) == ("image", name[:-4], ".png", "path")
    assert not storage.exists(ws, name)                                    # a page alone stores nothing
    thread = thread_on(owner, page, page, "the colours are too light")
    assert kept.wait() and storage.exists(ws, name)
    with connect_pages_db(ws) as conn:
        anchor = anchors.parse(conn.execute("SELECT anchor FROM comments WHERE id = ?", (thread["id"],)).fetchone()[0])
        assert (anchor["kind"], anchor["doc_id"], anchor["ext"]) == ("file", name[:-4], ".png")
        assert name in kept.held_files(conn) and name in upload_gc.referenced(conn)
    upload_gc.flush(ws)
    assert not _rows(ws, "upload_orphans", name)

    other = png("black")
    v.write("figures/fig.png", other, age=20)
    v.sync()
    new = f"{storage.content_digest(other)}.png"
    assert v.props(page)["doc"]["hash"] == new[:-4] and storage.exists(ws, new) and storage.exists(ws, name)
    assert owner.get(f"/api/uploads/{name}").content == data            # the commented version, still held
    owner.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": True}).raise_for_status()
    upload_gc.flush(ws)
    assert _rows(ws, "upload_orphans", name) and not _rows(ws, "upload_orphans", new)


def test_a_text_files_thread_keeps_no_version(owner):
    """``KEPT_FORMATS``: a range of a text file carries its own context, so a
    thread on a ``.tex`` read in place stores nothing at its open nor at a
    replacement (``keeps_bytes``, ``_unstored``), while a picture's does and
    a work folder's file in the library keeps its bytes whatever it is."""
    ws, account = workspace_of(OWNER), account_of(OWNER)
    folder = make_folder(owner, f"FK formats {uuid.uuid4().hex[:8]}")
    mark_work_folder(owner, folder, pull=False)

    def page(name, hash, format, ext, **more):
        doc = document(hash, name=name, at="path", format=format, ext=ext)
        with connect_pages_db(ws) as conn:
            made = create_page(conn, name, work_props(folder, name, doc=doc, **more), actor=account, client=DISK_CLIENT)
        thread_on(owner, made["id"], made["id"], f"on {name}")
        return made["id"], made["properties"]

    (tex, tex_props), (png, png_props), (lib, lib_props) = (
        page("main.tex", "1" * 24, "text", ".tex"), page("fig.png", "2" * 24, "image", ".png"),
        page("kept.tex", "3" * 24, "text", ".tex", library=True))
    assert kept.wait()
    assert not kept.keeps_bytes(ws, tex) and not kept.keeps_bytes(ws, tex, tex_props)
    assert kept.keeps_bytes(ws, png) and kept.keeps_bytes(ws, png, png_props)
    assert kept.keeps_bytes(ws, lib) and kept.keeps_bytes(ws, lib, lib_props)
    assert not kept._unstored(ws, page_document(tex_props)) and kept._unstored(ws, page_document(png_props))
    assert not storage.exists(ws, f"{'1' * 24}.tex")
    with connect_pages_db(ws) as conn:
        assert f"{'2' * 24}.png" in kept.held_files(conn, [png])


def test_the_repair_stores_what_the_quota_refused(work, owner, monkeypatch, caplog):
    v = work("quota")
    data = _build("refused")
    v.write("main.pdf", data, age=30)
    v.sync()
    page, ws, name = v.page_of("main.pdf"), v.ws, f"{storage.content_digest(data)}.pdf"
    real, refused = storage.store_pdf_stream, []

    def full(ws, stream):
        refused.append(ws)
        raise HTTPException(status_code=507, detail="the workspace's storage is full")
    monkeypatch.setattr(storage, "store_pdf_stream", full)
    with caplog.at_level(logging.INFO, logger="gamma"):
        thread_on(owner, page, page, "the whole file")
        assert kept.wait() and refused and not storage.exists(ws, name)
        assert kept.repair(ws, [page]) == 0 and len(refused) == 2
        monkeypatch.setattr(storage, "store_pdf_stream", real)
        assert kept.repair(ws, [page, "no-such-page"]) == 1 and storage.exists(ws, name)
        assert kept.repair(ws, [page]) == 0
    said = [r.getMessage() for r in caplog.records if page in r.getMessage()]
    assert len([s for s in said if "is not stored" in s]) == 1 and any("is stored now" in s for s in said)


def test_a_server_that_keeps_no_links_stores_nothing_from_a_path(monkeypatch):
    """A thread on a NAS, a clone or a hosted server never makes a vaults.db:
    the store asks ``folder_links_enabled`` first, as ``storage.linked_path``
    does."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "0")
    monkeypatch.setattr(linked_files, "connect_vaults_db", lambda: pytest.fail("vaults.db was opened"))
    assert linked_files.store_page_bytes("ws", "page", f"{'a' * 64}.pdf", "a" * 64) is False


def test_the_source_of_the_place_is_recorded_on_the_anchor_at_the_thread_s_open(work, owner, monkeypatch):
    asked = []

    def source_for(ws, conn, page_id, props, place):
        asked.append((page_id, place))
        return {"file": "main.tex", "line": 18}
    fake = SimpleNamespace(source_for=source_for)
    monkeypatch.setitem(sys.modules, "gamma.feedback.sources", fake)
    monkeypatch.setattr("gamma.feedback.sources", fake, raising=False)
    v = work("source")
    v.write("main.pdf", _build("sourced"), age=30)
    v.sync()
    page = v.page_of("main.pdf")
    area = new_block_id()
    v.edit(page, insert(page, "", {"pdf_position": _position(1, AREA, area=True)}, area))
    thread = thread_on(owner, page, area, "where is this drawn")
    whole = thread_on(owner, page, page, "and the file as a whole")
    assert kept.wait()
    with connect_pages_db(v.ws) as conn:
        stored = dict(conn.execute("SELECT id, anchor FROM comments WHERE id IN (?, ?)", (thread["id"], whole["id"])))
    anchor = anchors.parse(stored[thread["id"]])
    assert anchor["source"] == {"file": "main.tex", "line": 18}
    assert asked == [(page, {"kind": "area", "page": 1, "rects": anchor["rects"]})]   # the file itself has no place
    assert "source" not in anchors.parse(stored[whole["id"]])


def test_a_passage_of_the_paper_records_its_tex_lines_from_the_build_s_synctex(work, owner):
    """The paper's first build in a work folder of this computer, its
    ``.synctex.gz`` and sources beside it (untracked: the vault tracks PDFs
    and notes): a thread on the method's passage records the lines of
    ``sections/method.tex`` that set it, from the build the thread was
    opened on."""
    v = work("paper")
    for rel in ("main.pdf", "main.synctex.gz", "main.tex", "sections/method.tex"):
        v.write(rel, (PAPER / "v1" / rel).read_bytes(), age=30)
    v.sync()
    page = v.page_of("main.pdf")
    place = json.loads((PAPER / "places.json").read_text("utf-8"))["v1"]["passage"]
    rects = [{"x1": x0 * W, "y1": y0 * H, "x2": x1 * W, "y2": y1 * H} for x0, y0, x1, y1 in place["rects"]]
    passage = new_block_id()
    v.edit(page, insert(page, "", {"pdf_position": _position(1, rects), "quote": place["quote"]}, passage))
    with connect_users_db() as conn:   # the link as its row, so the page's vault is found (vault_of_page)
        conn.execute("INSERT INTO folder_links (id, workspace_id, folder_id, path, notes, created_by, created_at, "
                     "cursor, status, remote_url, token, token_id, kind, page_id, push, state, pull) VALUES "
                     "(?, ?, ?, ?, 1, ?, '2026-01-01', '', '{}', '', '', '', 'folder', '', ?, ?, 0)",
                     (v.link["id"], v.ws, v.folder, str(v.root), v.user, folder_links.PUSH_ALL,
                      json.dumps(v.link["state"])))
    try:
        thread = thread_on(owner, page, passage, "lambda is never introduced")
        assert kept.wait()
    finally:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM folder_links WHERE id = ?", (v.link["id"],))
    with connect_pages_db(v.ws) as conn:
        anchor = anchors.parse(conn.execute("SELECT anchor FROM comments WHERE id = ?", (thread["id"],)).fetchone()[0])
    assert anchor["kind"] == "passage" and anchor["doc_id"] == storage.content_digest((PAPER / "v1" / "main.pdf").read_bytes())
    assert anchor["source"] == {"file": place["file"], "line": place["line"], "end": place["end"]}
    assert storage.exists(v.ws, f"{anchor['doc_id']}.pdf")


def test_the_owner_s_copy_of_a_page_carries_the_versions_its_open_threads_hold(owner, tmp_path):
    def upload(data):
        r = owner.post("/api/uploads", files={"file": ("p.pdf", io.BytesIO(data), "application/pdf")})
        assert r.status_code == 200, r.text
        return r.json()["doc_id"]

    def area_on(version):
        """An area measured on ``version`` with a thread opened on it there,
        then placed on the page's file: the thread alone holds ``version``."""
        block = new_block_id()
        position = _position(1, AREA, area=True)
        ops = [{"op": "insert", "id": block, "parent": page, "content": "", "props": {
            "pdf_position": {**position, "doc_id": version}}}]
        owner.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": ops}).raise_for_status()
        thread = thread_on(owner, page, block, "as it was")
        owner.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
            {"op": "set", "id": block, "props": {"pdf_position": position}}]}).raise_for_status()
        return thread

    held, gone, shown = upload(_build("held")), upload(_build("let go")), upload(_build("shown", 40))
    page = make_page(owner, "FK copied", doc_props(shown))["id"]
    area_on(held)
    done = area_on(gone)
    owner.post(f"/api/comments/{done['id']}/resolve", json={"resolved": True}).raise_for_status()
    r = owner.get(f"/api/pages/{page}/export", params={"mode": "gamma"})
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = set(z.namelist())
    assert {f"uploads/{held}.pdf", f"uploads/{shown}.pdf"} <= names and f"uploads/{gone}.pdf" not in names

    make_user("fkept_other", "pw")
    other = login("fkept_other", "pw")
    archive = tmp_path / "page.gamma"
    archive.write_bytes(r.content)
    [reviewed] = [p for p in ws_backup.preview_zip(workspace_of("fkept_other"), archive)["pages"]
                  if p["title"] == "FK copied"]
    assert f"uploads/{held}.pdf" in reviewed["source_paths"] and not reviewed["missing"]
    r = other.post("/api/import-data?mode=merge", files={"file": ("page.gamma", r.content, "application/zip")})
    assert r.status_code == 200, r.text
    assert storage.exists(workspace_of("fkept_other"), f"{held}.pdf")
