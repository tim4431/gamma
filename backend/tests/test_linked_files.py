"""PDFs read where they are on this computer (gamma/linked_files.py, the
storage seam in gamma/storage.py, the reader's routes in
routers/folder_links.py): a file opened, read through everything that reads
a stored PDF and copied nowhere; the same bytes in two places; one page to a
path; a page left out of a lookup by attachment; a file that changes or goes; a
page added to the library; what never sees such a file; what a file is never
put through; who is told a path; and a server that keeps no links, which never
makes vaults.db."""

import io
import json
import os
import sqlite3
import sys
import threading
import time
import zipfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

import pymupdf

from conftest import (account_of, doc_props, fresh_client, login, make_folder, make_label, make_page, make_user,
                      thread_on, workspace_of)
from gamma import (ai_tools, app as app_mod, backups, comments, config, db, db_maintenance, folder_links, gamma_sync,
                   integrity, jobs, linked_files, offsite, pdf_meta, seed, storage, upload_gc, workspaces, ws_backup)
from gamma.ai_context import pdf_path
from gamma.feedback import kept
from gamma.blocks_store import pages_for_docs
from gamma.db import connect_data_db, connect_pages_db, connect_users_db, connect_vaults_db
from gamma.documents import text
from gamma.routers import pages as pages_mod
from gamma.routers.clip import find_page
from gamma.search import index as text_index, indexer
from gamma.search.index import Doc
from test_disk_push import _pdf, _write
from test_folder_links import _create, _sync, disk  # noqa: F401: the disk fixture
from test_highlight_relocate import SIZE, _line_rect, _pdf as _lines, _position
from test_offsite import data  # noqa: F401: the bucket and its data directory
from test_pdf_export import _annotated_pdf

OLD_LINE = "the quick brown wombat digs a long burrow"
PDF = {"format": "pdf", "ext": ".pdf"}   # what a reader says of a PDF's document
LOOK_AGAIN = linked_files._look_again


@pytest.fixture(scope="module")
def owner(client):
    make_user("lf_owner", "pw")
    return login("lf_owner", "pw")


@pytest.fixture
def ws(owner):
    return workspace_of("lf_owner")


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No manifest walk (pdf_meta.schedule), whose background thread holds the
    file open for a moment: Windows refuses to replace or delete an open
    file, and these tests do both. No worker and no later look: a test runs
    ``follow`` itself, or sets a listener of its own. And no stat remembered:
    these tests change a file and look at once."""
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(linked_files, "_look_again", lambda ws, name: None)
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})
    monkeypatch.setattr(storage, "_offline", {})


def _open(c, path, **kw):
    return c.post("/api/folder-links/open", json={"path": str(path)}, **kw)


def _opened(c, path) -> dict:
    r = _open(c, path)
    assert r.status_code == 200, r.text
    return r.json()


def _disk_pdf(disk, name, text):  # noqa: F811: the fixture's value
    """A PDF of its own on "this computer", at rest: ``(path, bytes, doc_id)``."""
    data = _pdf(text)
    path = disk / name
    _write(path, data)
    return path, data, storage.content_digest(data)


def _index(ws, doc, format="pdf", ext=".pdf"):
    """Index the document ``doc`` read in place now, as the indexing job
    does: extracted, and stored unless it was out of reach."""
    found = indexer.index_doc(ws, Doc(doc, format, ext, "path"))
    if found is not None:
        with connect_data_db(ws) as conn:
            text_index.store_docs(conn, [found])


def _indexed(ws, doc):
    """The units the index recorded for ``doc``, None when it recorded
    nothing."""
    with connect_data_db(ws) as conn:
        info = text_index.doc_info(conn, doc)
    return None if info is None else info["units"]


def _props(c, page_id) -> dict:
    r = c.get(f"/api/blocks/{page_id}")
    assert r.status_code == 200, r.text
    return r.json()["properties"]


def _state(c, page_id) -> dict:
    r = c.get(f"/api/folder-links/page/{page_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _rows(ws, page_id) -> list:
    with connect_vaults_db() as conn:
        return conn.execute("SELECT name, path FROM linked_files WHERE ws = ? AND page_id = ?", (ws, page_id)).fetchall()


def _opened_files(ws) -> str:
    with connect_pages_db(ws) as conn:
        return linked_files._opened_files(conn)


def _inside(c, ws) -> list:
    family = c.get(f"/api/pages/{_opened_files(ws)}/family").json()
    return [p["id"] for p in family["sub_pages"]]


# --- a file opened ---------------------------------------------------------------------

def test_a_pdf_opened_is_read_where_it_is_and_copied_nowhere(owner, ws, disk):
    data = _pdf("the linked wombats of the western plains")
    f = disk / "papers" / "Wombats 2024.pdf"
    _write(f, data)
    doc, name = storage.content_digest(data), f"{storage.content_digest(data)}.pdf"
    stored_before = storage.usage(ws)

    answer = _opened(owner, f)
    assert answer["workspace"] == ws and answer["created"] is True and answer["title"] == "Wombats 2024"
    page = answer["page"]
    props = _props(owner, page)
    assert props["doc"] == {"format": "pdf", "name": "Wombats 2024.pdf", "hash": doc, "ext": ".pdf", "at": "path"}
    assert "auto_title" not in props and props["parent_page"] == _opened_files(ws)
    assert page in _inside(owner, ws)
    assert _state(owner, page) == {"vault": None, "reader": {"path": str(f.resolve()), "missing": False, "mine": True, **PDF}}

    # nothing stored...
    assert not storage.exists(ws, name) and storage.usage(ws) == stored_before and storage.readable(ws, name)
    # ...and read by all that reads a stored PDF
    r = owner.get(f"/api/uploads/{name}")
    assert r.status_code == 200 and r.content == data and r.headers["content-type"] == "application/pdf"
    assert owner.head(f"/api/uploads/{name}").status_code == 200
    part = owner.get(f"/api/uploads/{name}", headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == b"%PDF"
    info = owner.get(f"/api/pdf-info/{doc}")
    assert info.status_code == 200 and info.json()["pages"] == 1 and info.json()["bytes"] == len(data)
    assert Path(pdf_path(ws, doc)) == f.resolve()
    _index(ws, doc)
    hits = owner.get("/api/search", params={"q": "wombats western"}).json()["results"]
    assert [h["page_id"] for h in hits if h["source"] == "pdf"] == [page]

    assert f.read_bytes() == data and [p.name for p in f.parent.iterdir()] == [f.name]   # nothing beside it


def test_the_page_opened_files_is_found_by_its_property_so_a_new_title_changes_nothing(owner, ws, disk):
    _opened(owner, _disk_pdf(disk, "a.pdf", "LF first of the opened")[0])
    top = _opened_files(ws)
    r = owner.post(f"/api/pages/{top}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": top, "content": "What I read"}]})
    assert r.status_code == 200, r.text
    second = _opened(owner, _disk_pdf(disk, "b.pdf", "LF second of the opened")[0])
    assert _opened_files(ws) == top and _props(owner, second["page"])["parent_page"] == top
    assert owner.get(f"/api/blocks/{top}").json()["content"] == "What I read"


def test_opening_a_file_again_opens_its_page_and_brings_it_to_the_top(owner, ws, disk):
    first_file = _disk_pdf(disk, "first.pdf", "LF read first")[0]
    first = _opened(owner, first_file)["page"]
    second = _opened(owner, _disk_pdf(disk, "second.pdf", "LF read second")[0])["page"]
    assert _inside(owner, ws)[:2] == [second, first]

    again = _opened(owner, first_file)
    assert again["page"] == first and again["created"] is False
    assert _inside(owner, ws)[:2] == [first, second]

    # touched since, the same bytes: the same page, and it reads again
    _write(first_file, first_file.read_bytes(), age=20)
    name = f"{_props(owner, first)['doc']['hash']}.pdf"
    assert owner.get(f"/api/uploads/{name}").status_code == 404
    assert _opened(owner, first_file)["page"] == first
    assert owner.get(f"/api/uploads/{name}").status_code == 200


def test_a_file_changed_since_it_was_read_is_taken_when_it_is_opened_again(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "draft.pdf", "LF a draft")
    page = _opened(owner, f)["page"]
    _write(f, _pdf("LF the next draft"), age=20)
    again = _opened(owner, f)
    assert again["page"] == page and again["created"] is False
    assert _props(owner, page)["doc"]["hash"] == storage.content_digest(f.read_bytes())
    assert _rows(ws, page) == [(f"{_props(owner, page)['doc']['hash']}.pdf", str(f.resolve()))]


def test_the_same_bytes_in_two_places_are_two_pages_and_either_path_reads(owner, ws, disk):
    data = _pdf("LF one paper in two folders")
    a, b = disk / "a" / "paper.pdf", disk / "b" / "copy.pdf"
    _write(a, data)
    _write(b, data)
    first, second = _opened(owner, a), _opened(owner, b)
    assert first["created"] and second["created"] and first["page"] != second["page"]
    name = f"{storage.content_digest(data)}.pdf"
    assert storage.open_path(ws, name) in (a.resolve(), b.resolve())

    a.unlink()
    assert storage.open_path(ws, name) == b.resolve()
    assert _state(owner, first["page"])["reader"]["missing"] is True
    assert _state(owner, second["page"])["reader"]["missing"] is False

    _write(b, _pdf("LF the copy moved on"), age=20)
    assert storage.open_path(ws, name) is None
    linked_files.follow(ws, name)
    assert _props(owner, second["page"])["doc"]["hash"] == storage.content_digest(b.read_bytes())
    assert _props(owner, first["page"])["doc"]["hash"] == name[:-4]          # the other one stays what it was


def test_a_file_that_is_a_vault_opens_the_vault_s_page(owner, ws, disk):
    f, _data, _doc = _disk_pdf(disk, "kept.pdf", "LF a vault of one file")
    page = make_page(owner, "LF kept paper")
    link = _create(owner, kind="file", page=page["id"], path=str(f))
    try:
        answer = _opened(owner, f)
        assert answer == {"workspace": ws, "page": page["id"], "created": False, "title": "LF kept paper", "format": ""}
        assert _rows(ws, page["id"]) == []
    finally:
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_file_inside_a_directory_vault_comes_first(owner, ws, disk, monkeypatch):
    """A hook the vault engine fills in; asked before anything else."""
    f, _data, _doc = _disk_pdf(disk, "inside.pdf", "LF inside a vault")
    assert linked_files._directory_vault_page(str(f), "anyone") is None
    page = make_page(owner, "LF vault page")
    monkeypatch.setattr(linked_files, "_directory_vault_page", lambda path, actor: {"workspace": ws, "page": page})
    assert _opened(owner, f) == {"workspace": ws, "page": page["id"], "created": False, "title": "LF vault page",
                                 "format": ""}


def test_bytes_the_workspace_stores_open_their_page(owner, disk):
    f, data, doc = _disk_pdf(disk, "stored.pdf", "LF in the library already")
    up = owner.post("/api/uploads", files={"file": ("paper.pdf", data, "application/pdf")})
    assert up.json()["doc_id"] == doc
    page = make_page(owner, "LF library paper", doc_props(doc))
    answer = _opened(owner, f)
    assert answer["page"] == page["id"] and answer["created"] is False and answer["title"] == "LF library paper"
    assert _props(owner, page["id"])["doc"]["at"] == "store"


def test_a_clone_takes_no_new_opened_file(disk):
    make_user("lf_clone", "pw")
    c, ws = login("lf_clone", "pw"), workspace_of("lf_clone")
    with connect_users_db() as conn:
        conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, created_at) "
                     "VALUES (?, 'http://nas', 'r', '', ?, '2026-01-01T00:00:00Z')", (ws, account_of("lf_clone")))
    r = _open(c, _disk_pdf(disk, "clone.pdf", "LF in a clone")[0])
    assert r.status_code == 400 and "clone" in r.json()["detail"]


# --- a file of any format ----------------------------------------------------------------

def _disk_file(disk, name, data):  # noqa: F811: the fixture's value
    """A file of its own on "this computer", at rest: ``(path, doc_id)``."""
    path = disk / name
    _write(path, data.encode("utf-8") if isinstance(data, str) else data)
    return path, storage.content_digest(path.read_bytes())


def test_a_file_of_any_format_is_read_where_it_is_in_the_viewer_of_its_format(owner, ws, disk):
    from test_vault_sync import PNG
    cases = (("paper.tex", b"\\section{Wombats}\n", "text", ".tex"),
             ("README.md", b"# Read me\n", "text", ".md"),            # a Markdown file is a text document here
             ("fig.png", PNG + b"of its own", "image", ".png"),       # bytes no other test reads in place
             ("tone.wav", b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio", ".wav"))
    for name, data, fmt, ext in cases:
        f, doc = _disk_file(disk, f"formats/{name}", data)
        answer = _opened(owner, f)
        assert answer["created"] is True and answer["format"] == fmt and answer["title"] == Path(name).stem, name
        page = answer["page"]
        props = _props(owner, page)
        assert props["doc"] == {"format": fmt, "name": name, "hash": doc, "ext": ext, "at": "path", "size": len(data)}
        assert props["parent_page"] == _opened_files(ws) and page in _inside(owner, ws)
        assert _state(owner, page)["reader"] == {"path": str(f.resolve()), "missing": False, "mine": True,
                                                 "format": fmt, "ext": ext}
        assert _rows(ws, page) == [(f"{doc}{ext}", str(f.resolve()))] and not storage.exists(ws, f"{doc}{ext}")
        assert owner.get(f"/api/uploads/{doc}{ext}").content == data
        assert _opened(owner, f) == {**answer, "created": False}           # opened again: its page


def test_an_opened_tex_follows_its_file_and_stays_a_text_document(owner, ws, disk, monkeypatch):
    scheduled, stored = [], []
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: scheduled.append(doc_id))
    f, doc = _disk_file(disk, "draft.tex", "\\section{One}\n")
    page = _opened(owner, f)["page"]
    monkeypatch.setattr(kept, "keeps_bytes", lambda *a, **k: True)      # each version stored, as for an open thread
    real = folder_links.store_file_stream
    monkeypatch.setattr(folder_links, "store_file_stream", lambda w, s, ext: stored.append(ext) or real(w, s, ext))
    monkeypatch.setattr(folder_links, "store_pdf_stream", lambda *a: pytest.fail("a text file stored as a PDF"))

    two = b"\\section{Two}\n"
    _write(f, two, age=20)
    assert owner.get(f"/api/uploads/{doc}.tex").status_code == 404       # moved: served as nothing until followed
    linked_files.follow(ws, f"{doc}.tex")
    new = storage.content_digest(two)
    assert _props(owner, page)["doc"] == {"format": "text", "name": "draft.tex", "hash": new, "ext": ".tex",
                                          "at": "path", "size": len(two)}
    assert _rows(ws, page) == [(f"{new}.tex", str(f.resolve()))] and stored == [".tex"] and scheduled == []
    assert owner.get(f"/api/uploads/{new}.tex").content == two

    three = b"\\section{Three}\n"                                         # changed, then opened again
    _write(f, three, age=40)
    again = _opened(owner, f)
    assert again["page"] == page and again["created"] is False and again["format"] == "text"
    assert _props(owner, page)["doc"]["hash"] == storage.content_digest(three) and scheduled == []


def test_a_moved_markdown_file_is_located_and_a_file_of_another_format_refused(owner, ws, disk):
    from test_vault_sync import PNG
    f, doc = _disk_file(disk, "notes.md", "# Notes\n")
    page = _opened(owner, f)["page"]
    moved = disk / "elsewhere" / "notes.md"
    moved.parent.mkdir(parents=True)
    f.rename(moved)
    assert _state(owner, page)["reader"]["missing"] is True
    r = owner.post(f"/api/folder-links/opened/{page}/locate", json={"path": str(moved)})
    assert r.status_code == 200, r.text
    assert _state(owner, page)["reader"] == {"path": str(moved.resolve()), "missing": False, "mine": True,
                                             "format": "text", "ext": ".md"}
    assert _rows(ws, page) == [(f"{doc}.md", str(moved.resolve()))]
    picture = _disk_file(disk, "pic.png", PNG)[0]
    r = owner.post(f"/api/folder-links/opened/{page}/locate", json={"path": str(picture)})
    assert r.status_code == 400 and r.json()["detail"] == "Pick a text file: this page reads one."


def test_bytes_a_library_page_stores_of_any_format_open_that_page(owner, ws, disk):
    data = b"\\documentclass{article}\n"
    name, _existed = storage.store_file_stream(ws, io.BytesIO(data), ".tex")
    held = make_page(owner, "LF a stored source", doc_props(name[:-4], name="main.tex", format="text", ext=".tex",
                                                          size=len(data)))
    f, _doc = _disk_file(disk, "main-copy.tex", data)
    answer = _opened(owner, f)
    assert (answer["page"], answer["created"], answer["format"]) == (held["id"], False, "text")


def test_a_binary_a_program_and_a_file_past_the_keep_limit_are_refused(owner, disk, monkeypatch):
    blob = _disk_file(disk, "blob.dat", b"\x00\x01\x02 no text")[0]
    for path, said in ((blob, "Gamma cannot show blob.dat: open it in another app."),
                       (_disk_file(disk, "setup.exe", b"MZ\x90\x00")[0], "Gamma does not open programs (.exe)."),
                       (_disk_file(disk, "run.bat", "echo hi\n")[0], "Gamma does not open programs (.bat).")):
        r = _open(owner, path)
        assert r.status_code == 400 and r.json()["detail"] == said, r.text
    monkeypatch.setattr(config, "vault_keep_bytes", lambda: 10)
    r = _open(owner, _disk_file(disk, "big.txt", "x" * 100)[0])
    assert r.status_code == 400 and r.json()["detail"].startswith("big.txt is too large for Gamma to show here")
    f, _data, _doc = _disk_pdf(disk, "big.pdf", "LF a PDF has no keep limit")
    assert _opened(owner, f)["format"] == "pdf"


def test_only_a_pdf_has_its_metadata_looked_up(owner, disk, monkeypatch):
    scheduled = []
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: scheduled.append(doc_id))
    _opened(owner, _disk_file(disk, "meta.tex", "\\title{No lookup}\n")[0])
    assert scheduled == []
    f, _data, doc = _disk_pdf(disk, "meta.pdf", "LF looked up")
    _opened(owner, f)
    assert scheduled == [doc]


def test_an_opened_tex_joins_the_library_and_an_opened_markdown_file_does_not(owner, ws, disk):
    f, doc = _disk_file(disk, "kept.tex", "\\section{Kept}\n")
    page = _opened(owner, f)["page"]
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"keep_pushing": True})
    assert r.status_code == 400 and r.json()["detail"] == "Only a PDF keeps pushing from its file."
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={})
    assert r.status_code == 200, r.text
    assert r.json()["properties"]["doc"] == {"format": "text", "name": "kept.tex", "hash": doc, "ext": ".tex",
                                             "at": "store", "size": f.stat().st_size}
    assert storage.exists(ws, f"{doc}.tex") and _rows(ws, page) == []

    md = _opened(owner, _disk_file(disk, "README.md", "# Kept apart\n")[0])["page"]
    r = owner.post(f"/api/folder-links/opened/{md}/add", json={})
    assert r.status_code == 400 and r.json()["detail"] == (
        "A Markdown file joins the library as a note: drop it on the library, or open its folder with Gamma to edit "
        "it where it is.")
    assert _props(owner, md)["parent_page"] == _opened_files(ws)


def test_only_a_pdf_is_carried_to_another_server(owner, disk):
    page = _opened(owner, _disk_file(disk, "carried.tex", "\\section{Here}\n")[0])["page"]
    for r in (owner.get(f"/api/folder-links/opened/{page}/carried"),
              owner.post(f"/api/folder-links/opened/{page}/carried", json={"digest": "d"})):
        assert r.status_code == 400 and r.json()["detail"] == "Only a PDF is carried to another server."


# --- one page to a path ------------------------------------------------------------------

def test_opening_the_file_of_a_deleted_page_brings_that_page_back(owner, ws, disk):
    f, _data, doc = _disk_pdf(disk, "reopened.pdf", "LF deleted and opened again")
    page = _opened(owner, f)["page"]
    note = owner.post("/api/blocks", json={"parent_id": page, "content": "a thought"}).json()["id"]
    owner.delete(f"/api/blocks/{page}").raise_for_status()

    again = _opened(owner, f)
    assert again["page"] == page and again["created"] is False
    assert owner.get(f"/api/blocks/{note}").status_code == 200           # with what was on it
    assert _rows(ws, page) == [(f"{doc}.pdf", str(f.resolve()))]
    assert _state(owner, page)["reader"] == {"path": str(f.resolve()), "missing": False, "mine": True, **PDF}
    assert _inside(owner, ws)[0] == page


def test_a_file_changed_while_its_page_was_deleted_is_taken_when_it_is_opened(owner, ws, disk):
    f, _data, doc = _disk_pdf(disk, "moved-on.pdf", "LF deleted and then changed")
    page = _opened(owner, f)["page"]
    owner.delete(f"/api/blocks/{page}").raise_for_status()
    new = _pdf("LF changed while it was deleted")
    _write(f, new, age=20)

    again = _opened(owner, f)
    assert again["page"] == page and again["created"] is False
    assert _props(owner, page)["doc"]["hash"] == storage.content_digest(new)
    assert _rows(ws, page) == [(f"{storage.content_digest(new)}.pdf", str(f.resolve()))]


def test_locate_is_refused_a_file_a_page_has_not_caught_up_with(owner, ws, disk):
    f, _data, old = _disk_pdf(disk, "behind.pdf", "LF a page behind its file")
    behind = _opened(owner, f)["page"]
    seeker = _opened(owner, _disk_pdf(disk, "lost.pdf", "LF a page that lost its file")[0])["page"]
    seeker_rows = _rows(ws, seeker)
    new = _pdf("LF the file moved on")
    _write(f, new)

    r = owner.post(f"/api/folder-links/opened/{seeker}/locate", json={"path": str(f)})
    assert r.status_code == 400 and "Another opened page" in r.json()["detail"]
    linked_files.follow(ws, f"{old}.pdf")
    assert _props(owner, behind)["doc"]["hash"] == storage.content_digest(new)
    assert _rows(ws, seeker) == seeker_rows


def test_locate_is_refused_the_file_of_a_deleted_page(owner, disk):
    f = _disk_pdf(disk, "deleted-file.pdf", "LF the file of a deleted page")[0]
    deleted = _opened(owner, f)["page"]
    owner.delete(f"/api/blocks/{deleted}").raise_for_status()
    seeker = _opened(owner, _disk_pdf(disk, "seeking.pdf", "LF seeking a file")[0])["page"]
    r = owner.post(f"/api/folder-links/opened/{seeker}/locate", json={"path": str(f)})
    assert r.status_code == 400 and "Another opened page" in r.json()["detail"]


def test_a_page_given_another_pdf_keeps_it_when_its_old_file_is_opened(owner, ws, disk):
    f, data, _doc = _disk_pdf(disk, "replaced.pdf", "LF the file a replacement left")
    page = _opened(owner, f)["page"]
    mine = owner.post("/api/uploads", files={"file": ("z.pdf", _pdf("LF put there on purpose"), "application/pdf")}).json()["doc_id"]
    owner.put(f"/api/pages/{page}/attachment", json={"doc_id": mine}).raise_for_status()

    _write(f, _pdf("LF the old file changed since"), age=20)
    changed = _opened(owner, f)
    assert changed["created"] is True and changed["page"] != page
    assert _props(owner, page)["doc"]["hash"] == mine and storage.exists(ws, f"{mine}.pdf")
    assert _rows(ws, page) == []
    with connect_pages_db(ws) as conn:                                     # and no short life for it
        assert not conn.execute("SELECT 1 FROM upload_retention WHERE name = ?", (f"{mine}.pdf",)).fetchone()


def test_a_row_left_behind_opens_no_page_that_shows_something_else(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "left-behind.pdf", "LF a row left behind")
    page = _opened(owner, f)["page"]
    mine = owner.post("/api/uploads", files={"file": ("y.pdf", _pdf("LF the other PDF"), "application/pdf")}).json()["doc_id"]
    owner.put(f"/api/pages/{page}/attachment", json={"doc_id": mine}).raise_for_status()
    again = _opened(owner, f)
    assert again["created"] is True and again["page"] != page and _rows(ws, page) == []


# --- left out of a lookup by attachment --------------------------------------------------

def test_a_library_pdf_added_never_finds_a_linked_page(owner, disk):
    f, data, doc = _disk_pdf(disk, "unfound.pdf", "LF a library copy of the same bytes")
    linked = _opened(owner, f)["page"]
    assert owner.get(f"/api/blocks/by-doc/{doc}").status_code == 404
    up = owner.post("/api/uploads", files={"file": ("paper.pdf", data, "application/pdf")})
    assert up.status_code == 200, up.text
    made = owner.post(f"/api/blocks/by-doc/{doc}", json={"default_title": "paper"})
    assert made.status_code == 200 and made.json()["id"] != linked
    assert owner.get(f"/api/blocks/by-doc/{doc}").json()["id"] == made.json()["id"]
    assert _props(owner, linked)["doc"]["at"] == "path"


@pytest.mark.parametrize("attach", ["post", "put"])
def test_bytes_a_linked_page_holds_are_refused_to_no_other_page(owner, disk, attach):
    f, data, doc = _disk_pdf(disk, f"held-{attach}.pdf", f"LF bytes held by a linked page {attach}")
    _opened(owner, f)
    page = make_page(owner, f"LF attach {attach}")
    r = getattr(owner, attach)(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc})
    assert r.status_code == 200, r.text
    assert _props(owner, page["id"])["doc"]["hash"] == doc


def test_a_gamma_merge_adds_its_page_beside_a_linked_page_of_the_same_bytes(owner, ws, disk, tmp_path):
    make_user("lf_donor", "pw")
    donor = login("lf_donor", "pw")
    data = _pdf("LF a paper with notes elsewhere")
    doc = donor.post("/api/uploads", files={"file": ("p.pdf", data, "application/pdf")}).json()["doc_id"]
    page = make_page(donor, "LF the donor's paper", doc_props(doc))
    donor.post("/api/blocks", json={"parent_id": page["id"], "content": "the donor's note"}).raise_for_status()
    bundle = donor.get(f"/api/pages/{page['id']}/export", params={"mode": "gamma"}).content

    f = disk / "same-bytes.pdf"
    _write(f, data)
    linked = _opened(owner, f)["page"]

    archive = tmp_path / "page.gamma"
    archive.write_bytes(bundle)
    [reviewed] = [p for p in ws_backup.preview_zip(ws, archive)["pages"] if p["title"] == "LF the donor's paper"]
    assert reviewed["action"] == "create"

    r = owner.post("/api/import-data?mode=merge", files={"file": ("page.gamma", bundle, "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["pages_added"] == 1 and r.json()["pages_skipped"] == 0
    merged = owner.get(f"/api/blocks/by-doc/{doc}").json()
    assert merged["id"] != linked and merged["content"] == "LF the donor's paper"
    assert _props(owner, linked)["doc"]["at"] == "path"


def test_a_chip_and_a_clip_never_find_a_linked_page(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "unclipped.pdf", "LF a chip and a clip")
    page = _opened(owner, f)["page"]
    source = "https://example.org/paper.pdf"
    r = owner.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": page, "props": {"web_url": source, "meta": {"doi": "10.1/lf"}}}]})
    assert r.status_code == 200, r.text
    with connect_pages_db(ws) as conn:
        assert pages_for_docs(conn, [doc]) == {}
        assert find_page(conn, doi="10.1/lf") is None and find_page(conn, urls=(source,)) is None
    stored = owner.post("/api/uploads", files={"file": ("c.pdf", data, "application/pdf")}).json()["doc_id"]
    library = make_page(owner, "LF the library's copy", doc_props(stored))
    with connect_pages_db(ws) as conn:
        assert pages_for_docs(conn, [doc])[doc]["id"] == library["id"]


# --- a file that changes ---------------------------------------------------------------

def test_a_file_changed_on_disk_becomes_the_page_s_new_pdf(owner, ws, disk, monkeypatch):
    old_bytes, new_bytes = _lines([[(72, 100, OLD_LINE)]]), _lines([[(72, 100, "a first line added"), (72, 300, OLD_LINE)]])
    f = disk / "build.pdf"
    _write(f, old_bytes)
    page = _opened(owner, f)["page"]
    old, new = storage.content_digest(old_bytes), storage.content_digest(new_bytes)
    r = owner.post("/api/blocks", json={"parent_id": page, "content": "", "properties": {
        "pdf_position": _position(1, _line_rect(72, 100, OLD_LINE)), "quote": OLD_LINE, "color": "#ffe28fa6"}})
    assert r.status_code == 200, r.text
    highlight = r.json()["id"]

    heard = []
    monkeypatch.setattr(storage, "linked_moved", [lambda ws, name: heard.append((ws, name))])
    _write(f, new_bytes)
    assert owner.get(f"/api/uploads/{old}.pdf").status_code == 404          # the old name, meanwhile
    assert set(heard) == {(ws, f"{old}.pdf")} and _props(owner, page)["doc"]["hash"] == old   # told, and nothing done in the read

    linked_files.follow(ws, f"{old}.pdf")
    props = _props(owner, page)
    assert props["doc"]["hash"] == new and props["doc"]["at"] == "path" and props["doc"]["name"] == "build.pdf"
    assert owner.get(f"/api/uploads/{new}.pdf").content == new_bytes
    assert owner.get(f"/api/uploads/{old}.pdf").status_code == 404
    assert _rows(ws, page) == [(f"{new}.pdf", str(f.resolve()))] and not storage.exists(ws, f"{new}.pdf")
    assert f.read_bytes() == new_bytes

    # the highlight went to where its words are now, read from a file on disk
    job = jobs.latest(account_of("lf_owner"), "relocate-highlights", f"{page}:{new}")
    assert jobs.wait(job["id"])["state"] == "done"
    position = _props(owner, highlight)["pdf_position"]
    assert "lost" not in position and abs(position["rects"][0]["y2"] - (300 + 0.3 * SIZE)) < 3


def test_a_replacement_whose_highlights_could_not_be_placed_says_so(owner, ws, disk, monkeypatch):
    old_bytes = _lines([[(72, 100, OLD_LINE)]])
    f = disk / "unplaced.pdf"
    _write(f, old_bytes)
    page = _opened(owner, f)["page"]
    owner.post("/api/blocks", json={"parent_id": page, "content": "", "properties": {
        "pdf_position": _position(1, _line_rect(72, 100, OLD_LINE)), "quote": OLD_LINE, "color": "#ffe28fa6"}}
    ).raise_for_status()
    reason = "you already have 20 background tasks waiting or running"
    old, moved = storage.content_digest(old_bytes), _lines([[(72, 300, OLD_LINE)]])
    new = storage.content_digest(moved)
    with monkeypatch.context() as patch:
        patch.setattr(pages_mod, "_start_relocation", lambda *a, **kw: (None, reason))
        _write(f, moved, age=20)
        linked_files.follow(ws, f"{old}.pdf")
        assert _props(owner, page)["doc"]["hash"] == new                       # the replacement stands
        assert _state(owner, page)["reader"]["relocation_error"] == reason

        os.utime(f, (time.time() - 30, time.time() - 30))                  # only touched: still not placed
        linked_files.follow(ws, f"{new}.pdf")
        assert _state(owner, page)["reader"]["relocation_error"] == reason

    _write(f, _lines([[(72, 500, OLD_LINE)]]), age=40)
    linked_files.follow(ws, f"{new}.pdf")
    assert _state(owner, page)["reader"] == {"path": str(f.resolve()), "missing": False, "mine": True, **PDF}


def test_an_opened_pdf_with_an_open_thread_keeps_each_version_it_shows_stored(owner, ws, disk, monkeypatch):
    """A thread opened on an opened PDF stores the bytes it shows (the
    repair of the links' sweep, when the worker could not); each new file
    while it is open is stored as it comes, the old one served from the
    store; once it is resolved the next is read in place alone."""
    from fastapi import HTTPException

    monkeypatch.setattr(comments, "write_listeners", [kept.after_comment])
    monkeypatch.setattr(linked_files, "_note_moved", lambda ws, name: None)
    first, second, third = (_lines([[(72, 100 + 50 * n, OLD_LINE)]]) for n in range(3))
    f = disk / "commented.pdf"
    _write(f, first, age=30)
    page = _opened(owner, f)["page"]
    old, new, last = (storage.content_digest(data) for data in (first, second, third))
    real = storage.store_pdf_stream

    def busy(ws, stream):
        raise HTTPException(status_code=507, detail="the workspace's storage is full")
    monkeypatch.setattr(storage, "store_pdf_stream", busy)
    thread = thread_on(owner, page, page, "check the whole file")
    assert kept.wait() and not storage.exists(ws, f"{old}.pdf")
    monkeypatch.setattr(storage, "store_pdf_stream", real)
    linked_files.sweep()
    assert storage.exists(ws, f"{old}.pdf") and storage.linked_path(ws, f"{old}.pdf") == f.resolve()

    _write(f, second, age=20)
    assert storage.linked_path(ws, f"{old}.pdf") is None                  # the disk is another version now
    linked_files.follow(ws, f"{old}.pdf")
    doc = _props(owner, page)["doc"]
    assert doc["hash"] == new and doc["at"] == "path" and storage.exists(ws, f"{new}.pdf")
    assert owner.get(f"/api/uploads/{old}.pdf").content == first
    assert _rows(ws, page) == [(f"{new}.pdf", str(f.resolve()))]

    owner.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": True}).raise_for_status()
    _write(f, third, age=10)
    linked_files.follow(ws, f"{new}.pdf")
    assert _props(owner, page)["doc"]["hash"] == last and not storage.exists(ws, f"{last}.pdf")


def test_the_worker_takes_a_moved_name_off_the_queue(owner, ws, disk, monkeypatch):
    f, _data, doc = _disk_pdf(disk, "queued.pdf", "LF before the queue")
    page = _opened(owner, f)["page"]
    monkeypatch.setattr(storage, "linked_moved", [linked_files._note_moved])
    _write(f, _pdf("LF after the queue"))
    assert owner.get(f"/api/uploads/{doc}.pdf").status_code == 404
    deadline = time.time() + 15
    while _props(owner, page)["doc"]["hash"] == doc and time.time() < deadline:
        time.sleep(0.05)
    assert _props(owner, page)["doc"]["hash"] == storage.content_digest(f.read_bytes())


def test_a_file_touched_with_the_same_bytes_only_refreshes_its_stat(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "touched.pdf", "LF touched only")
    page = _opened(owner, f)["page"]
    updated = owner.get(f"/api/blocks/{page}").json()["updated_at"]
    os.utime(f, (time.time() - 30, time.time() - 30))
    assert owner.get(f"/api/uploads/{doc}.pdf").status_code == 404
    linked_files.follow(ws, f"{doc}.pdf")
    assert owner.get(f"/api/uploads/{doc}.pdf").content == data
    assert owner.get(f"/api/blocks/{page}").json()["updated_at"] == updated


def test_a_file_still_being_written_waits_for_a_later_look(owner, ws, disk, monkeypatch):
    f, data, doc = _disk_pdf(disk, "writing.pdf", "LF being written")
    page = _opened(owner, f)["page"]
    _write(f, _pdf("LF written a moment ago"), age=0)
    linked_files.follow(ws, f"{doc}.pdf")
    assert _props(owner, page)["doc"]["hash"] == doc

    _write(f, b"%PDF-1.7\n1 0 obj\n<< >>\n" + b"x" * 3000)              # at rest, cut off before its end
    linked_files.follow(ws, f"{doc}.pdf")
    assert _props(owner, page)["doc"]["hash"] == doc

    new = _pdf("LF written whole")
    _write(f, new)
    queued = []
    monkeypatch.setattr(linked_files, "_note_moved", lambda ws, name: queued.append((ws, name)))
    linked_files.sweep()                                                # no read needed: an open viewer does none
    assert (ws, f"{doc}.pdf") in queued
    linked_files.follow(ws, f"{doc}.pdf")
    assert _props(owner, page)["doc"]["hash"] == storage.content_digest(new)


def _worked(monkeypatch):
    """Reads find the real worker; the wait for it is the test's."""
    monkeypatch.setattr(storage, "linked_moved", [linked_files._note_moved])


def test_a_file_only_touched_is_served_with_no_failure_however_lately(owner, ws, disk, monkeypatch):
    f, data, doc = _disk_pdf(disk, "just-touched.pdf", "LF touched a moment ago")
    page = _opened(owner, f)["page"]
    updated = owner.get(f"/api/blocks/{page}").json()["updated_at"]
    _worked(monkeypatch)
    os.utime(f)                                                           # now: not settled, and the same bytes
    assert owner.get(f"/api/uploads/{doc}.pdf").content == data
    assert owner.get(f"/api/blocks/{page}").json()["updated_at"] == updated


def test_new_bytes_answer_404_only_when_the_page_has_them(owner, ws, disk, monkeypatch):
    f, data, doc = _disk_pdf(disk, "next-version.pdf", "LF the version before")
    page = _opened(owner, f)["page"]
    _worked(monkeypatch)
    new = _pdf("LF the version after")
    _write(f, new)
    assert owner.get(f"/api/uploads/{doc}.pdf").status_code == 404
    assert _props(owner, page)["doc"]["hash"] == storage.content_digest(new)   # the ops have gone out already


def test_a_build_is_looked_at_again_and_taken_once_it_has_settled(owner, ws, disk, monkeypatch):
    f, data, doc = _disk_pdf(disk, "building.pdf", "LF the last build")
    page = _opened(owner, f)["page"]
    _worked(monkeypatch)
    monkeypatch.setattr(linked_files, "_look_again", LOOK_AGAIN)
    monkeypatch.setattr(linked_files, "LOOK_AGAIN_S", 0.05)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0.5)
    new = _pdf("LF this build")
    _write(f, new, age=0)
    assert owner.get(f"/api/uploads/{doc}.pdf").status_code == 404
    assert _props(owner, page)["doc"]["hash"] == storage.content_digest(new)


def test_a_file_that_stopped_short_is_left_to_the_sweep(owner, ws, disk, monkeypatch):
    f, data, doc = _disk_pdf(disk, "stopped.pdf", "LF stopped short")
    _opened(owner, f)
    again = []
    monkeypatch.setattr(linked_files, "_look_again", lambda ws, name: again.append(name))
    _write(f, b"%PDF-1.7\n1 0 obj\n<< >>\n" + b"x" * 3000)
    linked_files.follow(ws, f"{doc}.pdf")
    assert again == []                                                    # no end to looking at it each second
    _write(f, _pdf("LF written just now"), age=0)
    linked_files.follow(ws, f"{doc}.pdf")
    assert again == [f"{doc}.pdf"]


def test_the_read_never_waits_and_the_route_waits_only_so_long(owner, ws, disk, monkeypatch):
    f, data, doc = _disk_pdf(disk, "slow-worker.pdf", "LF a worker that takes its time")
    _opened(owner, f)
    gate = threading.Event()
    monkeypatch.setattr(linked_files, "follow", lambda ws, name: gate.wait(20))
    monkeypatch.setattr(linked_files, "SETTLE_WAIT_S", 0.5)
    _worked(monkeypatch)
    _write(f, _pdf("LF held up in the worker"))
    try:
        began = time.monotonic()
        assert storage.open_path(ws, f"{doc}.pdf") is None
        assert time.monotonic() - began < 0.5
        began = time.monotonic()
        assert owner.get(f"/api/uploads/{doc}.pdf").status_code == 404
        assert time.monotonic() - began >= 0.5
    finally:
        gate.set()
    deadline = time.time() + 15
    while linked_files._working and time.time() < deadline:
        time.sleep(0.01)
    assert not linked_files._working


def test_a_name_nobody_works_on_is_not_waited_for(owner, monkeypatch):
    monkeypatch.setattr(linked_files, "SETTLE_WAIT_S", 60)
    assert owner.get(f"/api/uploads/{'d' * 24}.pdf").status_code == 404


def _in_a_vault(page, kind="pdf"):
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO vault_files (link_id, path, page_id, kind) VALUES ('a vault', ?, ?, ?)",
                     (f"{page}.{kind}", page, kind))


def test_a_file_a_vault_s_round_reads_goes_to_the_round_only(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "in-a-vault.pdf", "LF one replacer per file")
    page = _opened(owner, f)["page"]
    rows = _rows(ws, page)
    _in_a_vault(page)
    new = _pdf("LF the round's to take")
    _write(f, new, age=20)
    assert owner.get(f"/api/uploads/{doc}.pdf").status_code == 404        # moved, as for any reader

    linked_files.follow(ws, f"{doc}.pdf")
    assert _props(owner, page)["doc"]["hash"] == doc and _rows(ws, page) == rows
    assert _opened(owner, f)["page"] == page                              # opened again, nothing taken
    assert _props(owner, page)["doc"]["hash"] == doc and _rows(ws, page) == rows
    for action, body in (("locate", {"path": str(f)}), ("add", {})):
        r = owner.post(f"/api/folder-links/opened/{page}/{action}", json=body)
        assert r.status_code == 400 and "synced vault" in r.json()["detail"]
    assert _props(owner, page)["doc"]["hash"] == doc and _rows(ws, page) == rows


def test_a_note_in_a_vault_leaves_a_page_s_pdf_to_the_reader(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "note-only.pdf", "LF a row of another kind")
    page = _opened(owner, f)["page"]
    _in_a_vault(page, "notes")
    new = _pdf("LF the reader takes this one")
    _write(f, new, age=20)
    linked_files.follow(ws, f"{doc}.pdf")
    assert _props(owner, page)["doc"]["hash"] == storage.content_digest(new)


def test_a_work_folders_file_of_any_format_is_its_rounds_alone(owner, ws, disk):
    """A ``file`` row (gamma/vault/kinds.py) holds the page's document as a
    PDF's does: the follow worker leaves it to the round."""
    f, data, doc = _disk_pdf(disk, "a-files-row.pdf", "LF a work folder's file")
    page = _opened(owner, f)["page"]
    rows = _rows(ws, page)
    _in_a_vault(page, "file")
    assert linked_files._in_vault(page)
    _write(f, _pdf("LF the round's"), age=20)
    linked_files.follow(ws, f"{doc}.pdf")
    assert _props(owner, page)["doc"]["hash"] == doc and _rows(ws, page) == rows


def test_hash_any_hashes_a_file_of_any_format_with_its_head(disk):
    f = disk / "any.txt"
    _write(f, b"hello")
    digest, size, mtime_ns, file_id, head = linked_files.hash_any(f)
    st = os.stat(f)
    assert (digest, size, mtime_ns, file_id, head) == (storage.content_digest(b"hello"), 5, st.st_mtime_ns, str(st.st_ino), b"hello")
    with pytest.raises(ValueError, match="%PDF"):
        linked_files.hash_file(f)
    p, data, doc = _disk_pdf(disk, "any.pdf", "LF hashed either way")
    assert linked_files.hash_file(p) == linked_files.hash_any(p)[:4] and linked_files.hash_any(p)[0] == doc
    assert linked_files.hash_any(p)[4][:5] == b"%PDF-"


def test_a_row_under_another_extension_is_restated_and_its_bytes_stored_as_they_are(ws, disk):
    from test_vault_sync import PNG
    f = disk / "fig.png"
    _write(f, PNG)
    seen = linked_files.hash_any(f)[:4]
    name = f"{seen[0]}.png"
    linked_files._put_row(ws, str(f), seen, "lf-png-page", ext=".png")
    try:
        assert _rows(ws, "lf-png-page") == [(name, str(f))] and storage.open_path(ws, name) == f
        os.utime(f, (time.time() - 40, time.time() - 40))
        assert storage.open_path(ws, name) is None                     # the stat moved: served as nothing
        linked_files.restat(ws, name, str(f), linked_files.hash_any(f)[:4])
        assert storage.open_path(ws, name) == f
        assert linked_files.store_page_bytes(ws, "lf-png-page", name, seen[0]) is True
        assert storage.exists(ws, name) and storage.find_upload_file(name, ws).read_bytes() == PNG
    finally:
        linked_files._drop_row(ws, name, str(f))


def test_a_new_file_of_bytes_the_library_holds_is_still_the_page_s(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "converged.pdf", "LF before it converged")
    page = _opened(owner, f)["page"]
    same = _pdf("LF the library's bytes")
    library = owner.post("/api/uploads", files={"file": ("l.pdf", same, "application/pdf")}).json()["doc_id"]
    held = make_page(owner, "LF the library's page", doc_props(library))
    _write(f, same)
    linked_files.follow(ws, f"{doc}.pdf")
    assert _props(owner, page)["doc"]["hash"] == library
    assert owner.get(f"/api/blocks/by-doc/{library}").json()["id"] == held["id"]


def test_a_page_in_the_trash_waits_and_a_stale_row_goes(owner, ws, disk):
    f, _data, doc = _disk_pdf(disk, "trashed.pdf", "LF in the trash")
    page = _opened(owner, f)["page"]
    owner.delete(f"/api/blocks/{page}").raise_for_status()
    _write(f, _pdf("LF changed while in the trash"))
    linked_files.follow(ws, f"{doc}.pdf")
    assert _rows(ws, page) == [(f"{doc}.pdf", str(f.resolve()))]        # kept for a restore

    other, _data, other_doc = _disk_pdf(disk, "stale.pdf", "LF a stale row")
    live = _opened(owner, other)["page"]
    linked_files._put_row(ws, str(other.resolve()), ("0" * 24, 1, 1, ""), live)
    linked_files.follow(ws, "0" * 24 + ".pdf")
    assert _rows(ws, live) == [(f"{other_doc}.pdf", str(other.resolve()))]


# --- a file that goes ----------------------------------------------------------------------

def test_a_file_that_is_gone_reads_as_missing_and_is_located(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "gone.pdf", "LF gone and found")
    page = _opened(owner, f)["page"]
    f.unlink()
    assert owner.get(f"/api/uploads/{doc}.pdf").status_code == 404
    assert linked_files.missing(ws, page) is True
    assert _state(owner, page)["reader"] == {"path": str(f.resolve()), "missing": True, "mine": True, **PDF}
    assert owner.get(f"/api/blocks/{page}").status_code == 200           # the page stays

    # the same bytes, elsewhere
    moved = disk / "moved" / "found.pdf"
    _write(moved, data)
    r = owner.post(f"/api/folder-links/opened/{page}/locate", json={"path": str(moved)})
    assert r.status_code == 200 and r.json()["id"] == page and r.json()["properties"]["doc"]["hash"] == doc
    assert owner.get(f"/api/uploads/{doc}.pdf").content == data
    assert _state(owner, page)["reader"] == {"path": str(moved.resolve()), "missing": False, "mine": True, **PDF}

    # a replacement
    moved.unlink()
    newer = disk / "moved" / "newer.pdf"
    other = _pdf("LF a newer version found")
    _write(newer, other)
    r = owner.post(f"/api/folder-links/opened/{page}/locate", json={"path": str(newer)})
    assert r.status_code == 200 and r.json()["properties"]["doc"]["hash"] == storage.content_digest(other)
    assert _rows(ws, page) == [(f"{storage.content_digest(other)}.pdf", str(newer.resolve()))]
    assert owner.get(f"/api/blocks/{page}").json()["content"] == "gone"  # the title is the page's


def test_locate_is_refused_what_another_page_reads_and_a_page_that_reads_no_file(owner, disk):
    f, _data, _doc = _disk_pdf(disk, "taken.pdf", "LF read by one page")
    _opened(owner, f)
    copy, _data, _doc = _disk_pdf(disk, "seeker.pdf", "LF a page that seeks")
    seeker = _opened(owner, copy)["page"]
    r = owner.post(f"/api/folder-links/opened/{seeker}/locate", json={"path": str(f)})
    assert r.status_code == 400 and "Another opened page" in r.json()["detail"]
    plain = make_page(owner, "LF no file")
    r = owner.post(f"/api/folder-links/opened/{plain['id']}/locate", json={"path": str(f)})
    assert r.status_code == 400 and "does not read a file" in r.json()["detail"]
    assert _state(owner, plain["id"]) == {"vault": None, "reader": None}


def test_a_stored_copy_is_no_missing_file(owner, ws, disk):
    """The library took the same bytes in after the file was opened, or a
    merge did: they read from the store, whatever became of the file."""
    f, data, doc = _disk_pdf(disk, "stored-too.pdf", "LF stored as well")
    page = _opened(owner, f)["page"]
    owner.post("/api/uploads", files={"file": ("s.pdf", data, "application/pdf")}).raise_for_status()
    f.unlink()
    assert linked_files.missing(ws, page) is False
    assert _state(owner, page)["reader"]["missing"] is False

    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"keep_pushing": True})
    assert r.status_code == 400 and "gone" in r.json()["detail"]          # there is no file to go on pushing
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={})
    assert r.status_code == 200, r.text
    assert r.json()["properties"]["doc"]["at"] == "store" and _rows(ws, page) == []


def test_a_copy_of_the_file_elsewhere_does_not_hide_that_this_page_lost_its_own(owner, ws, disk):
    data = _pdf("LF one copy left")
    a, b = disk / "c" / "kept.pdf", disk / "d" / "lost.pdf"
    _write(a, data)
    _write(b, data)
    kept, lost = _opened(owner, a)["page"], _opened(owner, b)["page"]
    b.unlink()
    assert linked_files.missing(ws, lost) is True and linked_files.missing(ws, kept) is False


def test_a_linked_file_out_of_reach_starts_no_indexing_and_is_not_waited_for(disk, monkeypatch):
    make_user("lf_indexing", "pw")                                         # a workspace with this one paper
    c = login("lf_indexing", "pw")
    f, data, doc = _disk_pdf(disk, "unindexed.pdf", "LF the quokka passage")
    _opened(c, f)
    away = f.with_suffix(".away")
    f.rename(away)
    started = []
    monkeypatch.setattr(indexer, "index_missing_async", lambda ws, docs: started.append(sorted(docs)))

    def pending():
        return [c.get("/api/search", params={"q": "quokka"}).json()["indexing"],
                c.post("/api/document-search", json={"q": "quokka"}).json()["indexing"]]

    assert pending() == [0, 0] and started == []
    away.rename(f)                                                         # within reach again: its turn comes
    assert pending() == [1, 1] and started == [[doc], [doc]]


def test_a_text_file_read_in_place_is_indexed_and_left_for_later_out_of_reach(owner, ws, disk):
    """A ``.tex`` read where it is goes into the index like a PDF: its run
    found by a word of its line; out of reach, nothing is recorded and a
    later search tries again."""
    f = disk / "inplace" / "method.tex"
    _write(f, b"\\section{Method}\nWe fit the wombat model by LF stochastic descent.\n")
    seen = linked_files.hash_any(f)
    doc = seen[0]
    page = make_page(owner, "method.tex", properties=doc_props(doc, name="method.tex", at="path", format="text",
                                                                ext=".tex"))["id"]
    linked_files._put_row(ws, str(f), seen, page, ext=".tex")
    away = f.with_suffix(".away")
    f.rename(away)
    _index(ws, doc, "text", ".tex")
    assert _indexed(ws, doc) is None                                       # out of reach: nothing recorded
    away.rename(f)
    _index(ws, doc, "text", ".tex")
    assert _indexed(ws, doc) == 1
    hits = owner.post("/api/document-search", json={"q": "stochastic descent", "pages": [page]}).json()["results"]
    assert [(h["source"], h["page_id"], h["line"]) for h in hits] == [("file", page, 2)]


@pytest.mark.skipif(sys.platform != "win32", reason="a byte-range lock that refuses other readers is Windows'")
def test_a_text_file_its_writer_has_locked_is_left_for_later(owner, ws, disk):
    """A ``.tex`` a build holds locked is out of reach like a PDF: nothing
    recorded, read once the lock goes."""
    import msvcrt

    f = disk / "locked" / "main.tex"
    data = b"\\section{Locked}\nLF the locked wombat burrow.\n"
    _write(f, data)
    seen = linked_files.hash_any(f)
    doc = seen[0]
    page = make_page(owner, "main.tex", properties=doc_props(doc, name="main.tex", at="path", format="text",
                                                              ext=".tex"))["id"]
    linked_files._put_row(ws, str(f), seen, page, ext=".tex")
    with open(f, "r+b") as held:
        msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, len(data))
        try:
            _index(ws, doc, "text", ".tex")
            assert _indexed(ws, doc) is None
        finally:
            msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, len(data))
    _index(ws, doc, "text", ".tex")
    assert _indexed(ws, doc) == 1


def test_pending_probes_a_bounded_number_of_linked_files(ws, disk, monkeypatch):
    """A work folder's unindexed in-place files are ``linked_files`` rows:
    ``pending`` finds which are linked in one query and tests at most
    PROBE_LIMIT of them for reach, so a search over thousands asks no more;
    the rest count as pending until the job has been through them."""
    names = {f"{n:064x}": Doc(f"{n:064x}", "text", ".tex", "path") for n in range(1, 301)}
    with connect_vaults_db() as conn:
        conn.executemany(
            "INSERT INTO linked_files (ws, name, path, size, mtime_ns, file_id, page_id, actor) "
            "VALUES (?, ?, ?, 1, 1, '', '', '')",
            [(ws, doc.name, f"Z:/nowhere/{h}.tex") for h, doc in names.items()])
    probed = []
    real = storage.readable
    monkeypatch.setattr(storage, "readable", lambda w, name: probed.append(name) or real(w, name))
    try:
        with connect_data_db(ws) as conn:
            found = text_index.pending(ws, conn, names)
        assert len(probed) == text_index.PROBE_LIMIT == 200
        assert found == sorted(names)[200:]                                # the probed ones are out of reach
    finally:
        with connect_vaults_db() as conn:
            conn.execute("DELETE FROM linked_files WHERE ws = ? AND path LIKE 'Z:/nowhere/%'", (ws,))


def test_a_linked_file_that_reads_but_does_not_parse_is_recorded_once(owner, ws, disk, monkeypatch):
    f, data, doc = _disk_pdf(disk, "broken.pdf", "LF readable and broken")
    _opened(owner, f)

    def broken(path, format, *, name=""):
        raise ValueError("cannot parse")

    monkeypatch.setattr(text, "extract", broken)
    _index(ws, doc)
    assert _indexed(ws, doc) == 0


def _clocked(monkeypatch):
    """``linked_stat`` as it runs, on a clock the test turns: the list's one
    number."""
    now = [100.0]
    monkeypatch.setattr(storage, "_clock", lambda: now[0])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 1)
    return now


def _looks_at(monkeypatch, f) -> list:
    """Filled with a 1 for each ``os.stat`` of ``f``."""
    looks, real = [], os.stat

    def counted(path, *args, **kw):
        if str(path) == str(f):
            looks.append(1)
        return real(path, *args, **kw)

    monkeypatch.setattr(storage.os, "stat", counted)
    return looks


def test_a_stat_is_remembered_for_a_second_a_path(disk, monkeypatch):
    now = _clocked(monkeypatch)
    f = disk / "looked-at.pdf"
    _write(f, b"%PDF one")
    looks = _looks_at(monkeypatch, f)
    first = storage.linked_stat(str(f))
    assert first.st_size == 8 and storage.linked_stat(str(f)) is first and len(looks) == 1
    now[0] += 0.9
    f.unlink()
    assert storage.linked_stat(str(f)) is first and len(looks) == 1
    now[0] += 0.2
    assert storage.linked_stat(str(f)) is None and len(looks) == 2        # gone, on a disk that answers
    assert storage.linked_stat(str(f)) is None and len(looks) == 2 and storage._offline == {}
    _write(f, b"%PDF two!")
    storage.forget_linked_stat(str(f))                                     # a row changed: a real look
    assert storage.linked_stat(str(f)).st_size == 9 and len(looks) == 3


def test_a_directory_at_a_linked_path_is_no_file(disk):
    disk.mkdir(parents=True, exist_ok=True)
    assert storage.linked_stat(str(disk)) is None


def test_a_disk_that_does_not_answer_is_asked_once_in_thirty_seconds(disk, monkeypatch):
    now = _clocked(monkeypatch)
    mount, away = disk / "mount", disk / "unplugged"
    f = mount / "paper.pdf"
    _write(f, b"%PDF on a disk")
    monkeypatch.setattr(storage, "_root", lambda path: str(mount))
    looks = _looks_at(monkeypatch, f)
    other = str(mount / "another.pdf")

    assert storage.linked_stat(str(f)) is not None and len(looks) == 1
    mount.rename(away)
    now[0] += 2
    assert storage.linked_stat(str(f)) is None and len(looks) == 2
    assert storage.linked_stat(str(f)) is None and storage.linked_stat(other) is None and len(looks) == 2

    away.rename(mount)                                                     # back, and not looked at yet
    now[0] += 29
    assert storage.linked_stat(str(f)) is None and len(looks) == 2
    now[0] += 2
    assert storage.linked_stat(str(f)) is not None and len(looks) == 3

    mount.rename(away)
    now[0] += 2
    assert storage.linked_stat(str(f)) is None
    away.rename(mount)
    storage.forget_linked_stat(str(f))                                     # just read: the disk is back
    assert storage.linked_stat(str(f)) is not None


def test_an_opened_file_on_a_disk_that_is_gone_reads_as_missing(owner, ws, disk, monkeypatch):
    now = _clocked(monkeypatch)
    mount, away = disk / "stick", disk / "stick-away"
    f = mount / "on-a-stick.pdf"
    data = _pdf("LF on a stick")
    _write(f, data)
    page = _opened(owner, f)["page"]
    doc = storage.content_digest(data)
    monkeypatch.setattr(storage, "_root", lambda path: str(mount.resolve()))
    heard = []
    monkeypatch.setattr(storage, "linked_moved", [lambda ws, name: heard.append(name)])
    mount.rename(away)
    now[0] += 2
    try:
        assert owner.get(f"/api/uploads/{doc}.pdf").status_code == 404 and heard == []   # nothing moved
        assert _state(owner, page)["reader"] == {"path": str(f.resolve()), "missing": True, "mine": True, **PDF}
        assert str(mount.resolve()) in storage._offline
    finally:
        away.rename(mount)
    now[0] += 31
    assert owner.get(f"/api/uploads/{doc}.pdf").content == data


@pytest.mark.parametrize("path, root", [("/mnt/nas/papers/a.pdf", "/mnt/nas"), ("/Volumes/Disk/a.pdf", "/Volumes/Disk"),
                                        ("/tmp/a.pdf", "/tmp"), ("/a.pdf", "/")])
def test_a_disk_begins_at_a_mount_point_with_no_drive_to_go_by(path, root):
    assert storage._root(str(Path(path))) == str(Path(root))


@pytest.mark.skipif(sys.platform != "win32", reason="drives and shares are Windows'")
@pytest.mark.parametrize("path, root", [(r"E:\papers\deep\a.pdf", "E:\\"), (r"\\nas\lab\deep\a.pdf", "\\\\nas\\lab\\")])
def test_a_disk_is_a_drive_or_a_share(path, root):
    assert storage._root(path) == root


def test_a_linked_file_is_never_cached_under_its_hash_name(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "uncached.pdf", "LF changes under one name")
    _opened(owner, f)
    r = owner.get(f"/api/uploads/{doc}.pdf")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers["etag"]

    stored = owner.post("/api/uploads", files={"file": ("u.pdf", data, "application/pdf")})
    assert stored.status_code == 200
    assert "immutable" in owner.get(f"/api/uploads/{doc}.pdf").headers["cache-control"]


def test_a_file_out_of_reach_is_not_stamped_indexed_and_has_no_file_for_now(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "indexed.pdf", "LF the phascolomys passage")
    page = _opened(owner, f)["page"]

    def stamped():
        return _indexed(ws, doc)

    def has_file():
        return storage.readable(ws, f"{doc}.pdf")

    # read where it is outside a work folder: no library document, so no paper with a record to look up
    assert page not in {p["id"] for p in owner.get("/api/metadata/status").json()["papers"]}
    assert has_file() is True
    away = f.with_suffix(".away")
    f.rename(away)
    _index(ws, doc)
    assert stamped() is None and has_file() is False                     # a stored file gone would be stamped units=0
    assert owner.get(f"/api/pdf-info/{doc}").status_code == 404
    away.rename(f)
    _index(ws, doc)
    assert stamped() == 1 and has_file() is True


# --- into the library --------------------------------------------------------------------

def test_add_to_library_stores_the_bytes_and_the_page_leaves_opened_files(owner, ws, disk):
    folder = make_folder(owner, "LF shelf")
    f, data, doc = _disk_pdf(disk, "keeper.pdf", "LF worth keeping")
    page = _opened(owner, f)["page"]
    updated = owner.post(f"/api/folder-links/opened/{page}/add", json={"folder": folder})
    assert updated.status_code == 200, updated.text
    props = updated.json()["properties"]
    assert updated.json()["id"] == page and props["doc"]["hash"] == doc and props["folders"] == [folder]
    assert props["doc"]["at"] == "store" and "parent_page" not in props
    assert storage.exists(ws, f"{doc}.pdf") and _rows(ws, page) == []
    assert _state(owner, page) == {"vault": None, "reader": None} and page not in _inside(owner, ws)
    assert owner.get(f"/api/blocks/by-doc/{doc}").json()["id"] == page
    f.unlink()
    assert owner.get(f"/api/uploads/{doc}.pdf").content == data         # stored now: the file may go

    again = owner.post(f"/api/folder-links/opened/{page}/add", json={})
    assert again.status_code == 400 and "does not read a file" in again.json()["detail"]


def test_add_to_library_keeping_on_pushing_makes_a_vault_of_one_file(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "manuscript.pdf", "LF a manuscript v1")
    page = _opened(owner, f)["page"]
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"keep_pushing": True})
    assert r.status_code == 200, r.text
    assert "folders" not in r.json()["properties"] and "parent_page" not in r.json()["properties"]
    links = [l for l in owner.get("/api/folder-links").json()["links"] if l["page_id"] == page]
    assert len(links) == 1 and links[0]["kind"] == "file" and links[0]["path"] == str(f.resolve())
    assert links[0]["pull"] is False and links[0]["push"] == "pdfs"
    assert _sync(owner, links[0]["id"])["counts"]["sent"] == 0          # the page has these bytes

    v2 = _pdf("LF a manuscript v2")
    _write(f, v2, age=20)
    assert _sync(owner, links[0]["id"])["counts"]["sent"] == 1
    assert _props(owner, page)["doc"]["hash"] == storage.content_digest(v2)
    assert storage.exists(ws, f"{storage.content_digest(v2)}.pdf")      # a file vault's push stores
    assert _opened(owner, f)["page"] == page                             # and the reader opens the vault's page
    owner.delete(f"/api/folder-links/{links[0]['id']}").raise_for_status()


def test_add_to_library_is_refused_a_missing_file_and_an_unknown_folder(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "refused.pdf", "LF refused")
    page = _opened(owner, f)["page"]
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"folder": "nosuchfolder"})
    assert r.status_code == 400 and "folder" in r.json()["detail"]
    f.unlink()
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={})
    assert r.status_code == 400 and "gone" in r.json()["detail"]
    assert not storage.exists(ws, f"{doc}.pdf") and _props(owner, page)["doc"]["at"] == "path"


def test_add_to_library_of_another_workspace_carries_the_page_with_everything_on_it(owner, ws, disk, lab):
    """An opened PDF taken to another workspace of this server: the Gamma
    export and the import's merge, in-process, its notes, highlights, chat
    and the pages inside it with it, the bytes stored there, filed in a
    folder of that workspace. The opened page and the pages inside it went
    from here for good; they go to Recently deleted with all they held, and
    without their rows: the file opened again is a new page, and the page
    restored comes back whole, the file no longer its own."""
    lab_ws, lab_owner, _bob, _vera = lab
    folder = make_folder(lab_owner, "LF lab shelf")
    f, data, doc = _disk_pdf(disk, "carried over.pdf", "LF carried over to the lab")
    page = _opened(owner, f)["page"]
    hl = owner.post("/api/blocks", json={"parent_id": page, "content": "my note", "properties": {
        "pdf_position": _position(1, _line_rect(72, 100, OLD_LINE)), "quote": OLD_LINE}}).json()["id"]
    owner.post("/api/blocks", json={"parent_id": hl, "content": "a thought under it"}).raise_for_status()
    owner.put(f"/api/chats/{page}", json={"messages": [{"role": "user", "text": "LF what is this paper"}]}).raise_for_status()
    inner = owner.post("/api/pages", json={"title": "LF a page inside it", "parent_page": page}).json()["id"]
    owner.post("/api/blocks", json={"parent_id": inner, "content": "LF the inner note"}).raise_for_status()

    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws, "folder": folder})
    assert r.status_code == 200, r.text
    props = r.json()["properties"]
    assert r.json()["id"] == page and props["doc"]["hash"] == doc and props["folders"] == [folder]
    assert props["doc"]["at"] == "store" and "parent_page" not in props
    assert storage.exists(lab_ws, f"{doc}.pdf") and lab_owner.get(f"/api/uploads/{doc}.pdf").content == data
    (mark,) = lab_owner.get(f"/api/blocks/{page}/children").json()["children"]
    assert mark["id"] == hl and mark["content"] == "my note" and mark["properties"]["quote"] == OLD_LINE
    assert _notes(lab_owner, hl) == ["a thought under it"] and _notes(lab_owner, inner) == ["LF the inner note"]
    assert lab_owner.get(f"/api/chats/{page}").json()["messages"][0]["text"] == "LF what is this paper"
    # gone from where it was opened, to Recently deleted with the page inside it, and its file is no page's here
    assert owner.get(f"/api/blocks/{page}").status_code == 404 and owner.get(f"/api/blocks/{inner}").status_code == 404
    trashed = {p["id"]: p for p in owner.get("/api/trash").json()["pages"]}
    assert trashed[page]["sub_pages"] == 1 and inner not in trashed
    assert _rows(ws, page) == [] and page not in _inside(owner, ws) and f.read_bytes() == data
    again = _opened(owner, f)
    assert again["created"] is True and again["page"] != page             # opened again: a new page of its own
    # restored, it is back whole, inside Opened files, and the file stays the new page's
    restored = owner.post(f"/api/trash/{page}/restore")
    assert restored.status_code == 200, restored.text
    assert _notes(owner, page) == ["my note"] and _notes(owner, hl) == ["a thought under it"]
    assert _notes(owner, inner) == ["LF the inner note"] and page in _inside(owner, ws)
    assert owner.get(f"/api/chats/{page}").json()["messages"][0]["text"] == "LF what is this paper"
    assert _rows(ws, page) == [] and _opened(owner, f)["page"] == again["page"]
    assert _notes(lab_owner, page) == ["my note"]                         # and the lab's copy is as it came


def test_add_to_library_of_another_workspace_keeping_on_pushing_makes_the_vault_there(owner, ws, disk, lab):
    lab_ws, lab_owner, _bob, _vera = lab
    f, data, doc = _disk_pdf(disk, "lab manuscript.pdf", "LF the lab's manuscript v1")
    page = _opened(owner, f)["page"]
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws, "keep_pushing": True})
    assert r.status_code == 200, r.text
    links = [l for l in lab_owner.get("/api/folder-links").json()["links"] if l["page_id"] == page]
    assert len(links) == 1 and links[0]["workspace_id"] == lab_ws and links[0]["kind"] == "file"
    _write(f, _pdf("LF the lab's manuscript v2"), age=20)
    assert _sync(lab_owner, links[0]["id"])["counts"]["sent"] == 1
    assert lab_owner.get(f"/api/blocks/{page}").json()["properties"]["doc"]["hash"] != doc
    lab_owner.delete(f"/api/folder-links/{links[0]['id']}").raise_for_status()


def test_add_to_library_of_another_workspace_is_refused_what_that_workspace_cannot_take(owner, ws, disk, lab):
    lab_ws, lab_owner, _bob, vera = lab
    f, data, doc = _disk_pdf(disk, "kept here.pdf", "LF kept where it was opened")
    page = _opened(owner, f)["page"]
    viewing = workspaces.create("LF only viewing", account_of("lf_vera"), kind="shared")
    workspaces.set_member(viewing["id"], account_of("lf_owner"), "viewer", by=account_of("lf_vera"))
    for target, said in ((viewing["id"], "cannot add pages"), ("nosuchworkspace", "cannot add pages")):
        r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": target})
        assert r.status_code == 400 and said in r.json()["detail"], r.text
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws, "folder": "nosuchfolder"})
    assert r.status_code == 400 and "folder" in r.json()["detail"]
    storage.put_upload(lab_ws, f"{doc}.pdf", data)
    lab_owner.post("/api/blocks", json={"parent_id": "root", "content": "LF the lab's own copy",
                                        "properties": doc_props(doc)}).raise_for_status()
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws})
    assert r.status_code == 400 and "already" in r.json()["detail"]
    assert _props(owner, page)["doc"]["at"] == "path" and _rows(ws, page)          # nothing done


def test_add_to_library_of_another_workspace_takes_nothing_when_a_page_inside_it_is_there(owner, ws, disk, lab,
                                                                                          monkeypatch):
    """A page inside the opened page whose PDF the other workspace has
    already: the merge would skip it, so nothing goes across and nothing here
    is deleted, before the export or, should the page arrive there meanwhile,
    after the merge."""
    lab_ws, lab_owner, _bob, _vera = lab
    f, data, doc = _disk_pdf(disk, "with a page inside.pdf", "LF a page inside it")
    page = _opened(owner, f)["page"]
    inner_data = _pdf("LF the inner paper")
    inner_doc = storage.content_digest(inner_data)
    storage.put_upload(ws, f"{inner_doc}.pdf", inner_data)
    inner = owner.post("/api/pages", json={"title": "LF inner", "parent_page": page}).json()["id"]
    owner.put(f"/api/blocks/{inner}", json={"properties": doc_props(inner_doc)}).raise_for_status()
    storage.put_upload(lab_ws, f"{inner_doc}.pdf", inner_data)
    lab_owner.post("/api/blocks", json={"parent_id": "root", "content": "LF the lab's copy of the inner paper",
                                        "properties": doc_props(inner_doc)}).raise_for_status()

    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws})
    assert r.status_code == 400 and "already" in r.json()["detail"], r.text
    assert _props(owner, page)["doc"]["at"] == "path" and _rows(ws, page)
    assert owner.get(f"/api/blocks/{inner}").status_code == 200
    assert lab_owner.get(f"/api/blocks/{page}").status_code == 404

    # the check before the export passes, and the inner page is there by the time of the merge
    held, asked = linked_files.page_for_file, []

    def blind(conn, doc_id, ext, *a, **kw):
        asked.append(doc_id)
        return None if doc_id == inner_doc else held(conn, doc_id, ext, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(linked_files, "page_for_file", blind)
        r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws})
    assert r.status_code == 400 and "already" in r.json()["detail"], r.text
    assert inner_doc in asked
    assert lab_owner.get(f"/api/blocks/{page}").status_code == 404                  # what arrived went back out
    assert _props(owner, page)["doc"]["at"] == "path" and owner.get(f"/api/blocks/{inner}").status_code == 200


def test_a_carry_whose_merge_finds_the_page_there_takes_back_only_what_it_brought(owner, ws, disk, lab, monkeypatch):
    """The page came to be there between the check and the merge (another
    import of it): the merge skipped it, and the take-back deleted for good
    every carried page it found there, the other arrival too. Only what this
    merge added goes back out, and the page here is as it was."""
    lab_ws, lab_owner, _bob, _vera = lab
    f, data, doc = _disk_pdf(disk, "imported meanwhile.pdf", "LF imported meanwhile")
    page = _opened(owner, f)["page"]
    owner.post("/api/blocks", json={"parent_id": page, "content": "LF a note on it"}).raise_for_status()
    real = ws_backup.restore_zip

    def imported_first(target, zpath, mode, **kw):
        real(target, zpath, mode, **kw)                     # the same export, merged in by another import just before
        return real(target, zpath, mode, **kw)

    monkeypatch.setattr(ws_backup, "restore_zip", imported_first)
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws})
    assert r.status_code == 400 and "already" in r.json()["detail"], r.text
    assert [c["content"] for c in lab_owner.get(f"/api/blocks/{page}/children").json()["children"]] == ["LF a note on it"]
    assert _props(owner, page)["doc"]["at"] == "path" and _rows(ws, page)


def _notes(c, page_id) -> list:
    r = c.get(f"/api/blocks/{page_id}/children")
    assert r.status_code == 200, r.text
    return [b["content"] for b in r.json()["children"]]


def test_a_carry_taken_back_leaves_a_page_inside_it_that_came_another_way(owner, ws, disk, lab, monkeypatch):
    """The page inside the opened one came to be there between the check and
    the merge, by another import of it alone, and was written on there: the
    merge skipped it and added the opened page, and the take-back's delete
    for good of that page took the pages inside it there, this one with its
    note. Only the pages the merge added go back out."""
    lab_ws, lab_owner, bob, _vera = lab
    f, data, doc = _disk_pdf(disk, "inner came first.pdf", "LF the inner page came first")
    page = _opened(owner, f)["page"]
    inner = owner.post("/api/pages", json={"title": "LF inner", "parent_page": page}).json()["id"]
    owner.post("/api/blocks", json={"parent_id": inner, "content": "LF the inner note"}).raise_for_status()
    real = ws_backup.restore_zip

    def inner_came_first(target, zpath, mode, **kw):
        assert real(target, zpath, "merge", selected={f"page:{inner}"}, by=account_of("lf_bob"))["added"] == [inner]
        bob.post("/api/blocks", json={"parent_id": inner, "content": "LF bob's note there"}).raise_for_status()
        return real(target, zpath, mode, **kw)

    monkeypatch.setattr(ws_backup, "restore_zip", inner_came_first)
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws})
    assert r.status_code == 400 and "already" in r.json()["detail"], r.text
    assert lab_owner.get(f"/api/blocks/{page}").status_code == 404
    assert _notes(lab_owner, inner) == ["LF the inner note", "LF bob's note there"]
    assert _props(owner, page)["doc"]["at"] == "path" and _notes(owner, inner) == ["LF the inner note"]


def test_a_page_written_on_while_it_is_carried_stays_here(owner, ws, disk, lab, monkeypatch):
    """A note written on the opened page, and a page made inside it with a
    note, after the export was made and before the page went from here: it
    was deleted here for good with them, and the other workspace had it as
    exported, so they were in neither. A page that changed since its export
    stays here with all of it, nothing of it stays there, its file vault
    neither, and pressed again it goes with everything."""
    lab_ws, lab_owner, _bob, _vera = lab
    f, data, doc = _disk_pdf(disk, "written on meanwhile.pdf", "LF written on while it was carried")
    page = _opened(owner, f)["page"]
    owner.post("/api/blocks", json={"parent_id": page, "content": "LF before"}).raise_for_status()
    real, made = ws_backup.restore_zip, {}

    def written_meanwhile(target, zpath, mode, **kw):
        owner.post("/api/blocks", json={"parent_id": page, "content": "LF meanwhile"}).raise_for_status()
        made["sub"] = owner.post("/api/pages", json={"title": "LF made meanwhile", "parent_page": page}).json()["id"]
        owner.post("/api/blocks", json={"parent_id": made["sub"], "content": "LF its note"}).raise_for_status()
        return real(target, zpath, mode, **kw)

    with monkeypatch.context() as m:
        m.setattr(ws_backup, "restore_zip", written_meanwhile)
        r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws, "keep_pushing": True})
    assert r.status_code == 400 and "while" in r.json()["detail"], r.text
    assert _notes(owner, page) == ["LF before", "LF meanwhile"] and _notes(owner, made["sub"]) == ["LF its note"]
    assert _props(owner, page)["doc"]["at"] == "path" and _rows(ws, page)
    assert lab_owner.get(f"/api/blocks/{page}").status_code == 404
    assert lab_owner.get(f"/api/blocks/{made['sub']}").status_code == 404
    assert not [l for l in lab_owner.get("/api/folder-links").json()["links"] if l["page_id"] == page]

    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws})
    assert r.status_code == 200, r.text
    assert _notes(lab_owner, page) == ["LF before", "LF meanwhile"] and _notes(lab_owner, made["sub"]) == ["LF its note"]
    assert owner.get(f"/api/blocks/{page}").status_code == 404 and owner.get(f"/api/blocks/{made['sub']}").status_code == 404
    assert {p["id"]: p for p in owner.get("/api/trash").json()["pages"]}[page]["sub_pages"] == 1


def test_a_carry_taken_back_keeps_what_someone_wrote_there_meanwhile(owner, ws, disk, lab, monkeypatch):
    """The page here was written on during the carry, and a member of the
    other workspace wrote on its copy there before the take-back: the copy,
    deleted there for good, took that member's note with it. A copy written
    on there stays there whole, the page here stays as it is, and the person
    is told it is in both workspaces."""
    lab_ws, lab_owner, bob, _vera = lab
    f, data, doc = _disk_pdf(disk, "written on in both.pdf", "LF written on in both workspaces")
    page = _opened(owner, f)["page"]
    owner.post("/api/blocks", json={"parent_id": page, "content": "LF before"}).raise_for_status()
    real = folder_links.create_file_link

    def written_on_both_sides(target, page_id, path, actor):
        bob.post("/api/blocks", json={"parent_id": page_id, "content": "LF bob's note there"}).raise_for_status()
        owner.post("/api/blocks", json={"parent_id": page_id, "content": "LF meanwhile here"}).raise_for_status()
        return real(target, page_id, path, actor)

    monkeypatch.setattr(folder_links, "create_file_link", written_on_both_sides)
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws, "keep_pushing": True})
    assert r.status_code == 400 and "both workspaces" in r.json()["detail"], r.text
    assert _notes(lab_owner, page) == ["LF before", "LF bob's note there"]
    assert _notes(owner, page) == ["LF before", "LF meanwhile here"]
    assert _props(owner, page)["doc"]["at"] == "path" and _rows(ws, page)
    assert not [l for l in lab_owner.get("/api/folder-links").json()["links"] if l["page_id"] == page]


def test_two_carries_of_one_page_at_once_carry_it_once(owner, ws, disk, lab, monkeypatch):
    """A double press, or the dialog open in two windows: both passed the
    check, the second's merge skipped the page the first had brought, and its
    take-back deleted it there while the first deleted it here, so it was in
    neither workspace. One carry runs at a time, and the second finds the
    page gone from here and changes nothing."""
    lab_ws, lab_owner, _bob, _vera = lab
    f, data, doc = _disk_pdf(disk, "pressed twice.pdf", "LF pressed twice")
    page = _opened(owner, f)["page"]
    actor, real = account_of("lf_owner"), ws_backup.restore_zip
    merging, go, merges, answers = threading.Event(), threading.Event(), [], {}

    def held(target, zpath, mode, **kw):
        merges.append(target)
        merging.set()
        go.wait(10)
        return real(target, zpath, mode, **kw)

    def carry(n):
        try:
            answers[n] = linked_files.add_to_library(ws, page, "", False, actor, workspace=lab_ws)["id"]
        except folder_links.LinkError as e:
            answers[n] = str(e)

    monkeypatch.setattr(ws_backup, "restore_zip", held)
    first, second = threading.Thread(target=carry, args=(1,)), threading.Thread(target=carry, args=(2,))
    first.start()
    assert merging.wait(10)
    second.start()
    second.join(1)                                          # the second press, while the first merges
    go.set()
    first.join(10)
    second.join(10)
    assert merges == [lab_ws] and answers[1] == page and "does not read a file" in answers[2], answers
    assert lab_owner.get(f"/api/blocks/{page}").status_code == 200 and owner.get(f"/api/blocks/{page}").status_code == 404


def test_a_carry_takes_nothing_while_a_page_inside_it_has_not_its_file_at_hand(owner, ws, disk, lab):
    """A page inside the opened one is an opened PDF too, and its file moved,
    or holds new bytes the worker has not read yet: the export read it as
    nothing, so that page went across without its PDF and with no path to
    locate one by, and went to Recently deleted here without its rows. The
    carry is refused as for the opened page's own file, nothing of it goes
    there, and once the file is at hand again every PDF goes with its page."""
    lab_ws, lab_owner, _bob, _vera = lab
    f, data, doc = _disk_pdf(disk, "the outer paper.pdf", "LF the outer paper")
    g, inner_data, inner_doc = _disk_pdf(disk, "the inner paper.pdf", "LF the inner paper")
    page, inner = _opened(owner, f)["page"], _opened(owner, g)["page"]
    owner.post("/api/blocks", json={"parent_id": inner, "content": "LF the inner note"}).raise_for_status()
    owner.put(f"/api/blocks/{inner}", json={"properties": {"parent_page": page}}).raise_for_status()
    moved = disk / "the inner paper, moved.pdf"
    g.rename(moved)

    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws})
    assert r.status_code == 400 and "gone" in r.json()["detail"] and "inside" in r.json()["detail"], r.text
    assert lab_owner.get(f"/api/blocks/{page}").status_code == 404 and lab_owner.get(f"/api/blocks/{inner}").status_code == 404
    assert not storage.exists(lab_ws, f"{doc}.pdf") and not storage.exists(lab_ws, f"{inner_doc}.pdf")
    assert _props(owner, page)["doc"]["at"] == "path" and _rows(ws, page) and _rows(ws, inner)
    assert _notes(owner, inner) == ["LF the inner note"]

    owner.post(f"/api/folder-links/opened/{inner}/locate", json={"path": str(moved)}).raise_for_status()
    newer = _pdf("LF the inner paper, newer")
    _write(moved, newer)                                                 # not read yet: the old name reads nothing
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws})
    assert r.status_code == 400 and "gone" in r.json()["detail"], r.text
    assert lab_owner.get(f"/api/blocks/{page}").status_code == 404 and _rows(ws, inner)

    linked_files.follow(ws, f"{inner_doc}.pdf")                          # the worker reads it
    newer_doc = storage.content_digest(newer)
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws})
    assert r.status_code == 200, r.text
    assert lab_owner.get(f"/api/uploads/{doc}.pdf").content == data
    assert _props(lab_owner, inner)["doc"]["hash"] == newer_doc and lab_owner.get(f"/api/uploads/{newer_doc}.pdf").content == newer
    assert _notes(lab_owner, inner) == ["LF the inner note"] and _props(lab_owner, inner)["doc"]["at"] == "store"
    assert _rows(ws, page) == [] and _rows(ws, inner) == []


def test_a_touch_followed_during_a_carry_gives_the_page_no_row_back(owner, ws, disk, lab, monkeypatch):
    """A build touches the file while its page is carried (the same bytes, a
    new time), and the worker reads the page as it stood, hashes the file,
    and wrote the row back once the carry had dropped it: the file opened
    again brought the page back from Recently deleted, in both workspaces
    now. The worker refreshes only a row that still stands."""
    lab_ws, lab_owner, _bob, _vera = lab
    f, data, doc = _disk_pdf(disk, "touched while carried.pdf", "LF touched while carried")
    page = _opened(owner, f)["page"]
    owner.post("/api/blocks", json={"parent_id": page, "content": "LF a note on it"}).raise_for_status()
    real_hash, real_merge = linked_files.hash_file, ws_backup.restore_zip
    hashing, go, worker = threading.Event(), threading.Event(), {}

    def held_hash(path):
        if threading.current_thread() is worker.get("thread"):
            hashing.set()
            go.wait(10)
        return real_hash(path)

    def touched_meanwhile(target, zpath, mode, **kw):
        st = os.stat(f)
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        worker["thread"] = threading.Thread(target=linked_files.follow, args=(ws, f"{doc}.pdf"))
        worker["thread"].start()
        assert hashing.wait(10)
        return real_merge(target, zpath, mode, **kw)

    monkeypatch.setattr(linked_files, "hash_file", held_hash)
    monkeypatch.setattr(ws_backup, "restore_zip", touched_meanwhile)
    assert linked_files.add_to_library(ws, page, "", False, account_of("lf_owner"), workspace=lab_ws)["id"] == page
    go.set()
    worker["thread"].join(10)
    assert not worker["thread"].is_alive() and _rows(ws, page) == []
    again = _opened(owner, f)
    assert again["created"] is True and again["page"] != page
    assert owner.get(f"/api/blocks/{page}").status_code == 404 and page in {p["id"] for p in owner.get("/api/trash").json()["pages"]}
    assert _notes(lab_owner, page) == ["LF a note on it"]


def _tree_names(ws, tree) -> set:
    with connect_pages_db(ws) as conn:
        return {r[0] for r in conn.execute("SELECT content FROM unified_blocks WHERE page_id = ?", (tree,))}


def test_a_carry_taken_back_takes_back_the_folders_and_labels_its_merge_made(owner, ws, disk, lab, monkeypatch):
    """A page inside the opened one is filed in folders and a label of this
    workspace: the merge made what the other workspace lacked, and the
    take-back, which deleted only the pages, left them there, empty, for
    its members to see. They go back out with the pages; a folder that
    workspace had already stays, and so does one a member there filed a
    page in meanwhile."""
    lab_ws, lab_owner, bob, _vera = lab
    make_folder(lab_owner, "LF shared shelf")
    shelf, private = make_folder(owner, "LF shared shelf"), make_folder(owner, "LF private shelf/LF deeper")
    label = make_label(owner, "LF private label")
    f, data, doc = _disk_pdf(disk, "filed inside.pdf", "LF a page inside it is filed")
    page = _opened(owner, f)["page"]
    inner = owner.post("/api/pages", json={"title": "LF inner", "parent_page": page}).json()["id"]
    owner.put(f"/api/blocks/{inner}", json={"properties": {"folders": [shelf, private], "labels": [label]}}).raise_for_status()
    folders_before, labels_before = _tree_names(lab_ws, "folders"), _tree_names(lab_ws, "labels")
    real = ws_backup.restore_zip

    def written_meanwhile(target, zpath, mode, **kw):
        owner.post("/api/blocks", json={"parent_id": page, "content": "LF meanwhile"}).raise_for_status()
        return real(target, zpath, mode, **kw)

    with monkeypatch.context() as m:
        m.setattr(ws_backup, "restore_zip", written_meanwhile)
        r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws})
    assert r.status_code == 400 and "while" in r.json()["detail"], r.text
    assert lab_owner.get(f"/api/blocks/{page}").status_code == 404 and lab_owner.get(f"/api/blocks/{inner}").status_code == 404
    assert _tree_names(lab_ws, "folders") == folders_before and _tree_names(lab_ws, "labels") == labels_before
    assert "LF shared shelf" in folders_before

    # a member files a page of that workspace in the folder the merge made, before the take-back
    real_link, kept = folder_links.create_file_link, lab_owner.post("/api/pages", json={"title": "LF bob's page"}).json()["id"]

    def filed_meanwhile(target, page_id, path, actor):
        with connect_pages_db(lab_ws) as conn:
            made = conn.execute("SELECT id FROM unified_blocks WHERE page_id = 'folders' AND content = 'LF deeper'").fetchone()[0]
        bob.put(f"/api/blocks/{kept}", json={"properties": {"folders": [made]}}).raise_for_status()
        owner.post("/api/blocks", json={"parent_id": page, "content": "LF meanwhile again"}).raise_for_status()
        return real_link(target, page_id, path, actor)

    monkeypatch.setattr(folder_links, "create_file_link", filed_meanwhile)
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws, "keep_pushing": True})
    assert r.status_code == 400 and "while" in r.json()["detail"], r.text
    assert lab_owner.get(f"/api/blocks/{page}").status_code == 404 and lab_owner.get(f"/api/blocks/{inner}").status_code == 404
    assert {"LF private shelf", "LF deeper"} <= _tree_names(lab_ws, "folders")           # in use there: they stay
    assert _tree_names(lab_ws, "labels") == labels_before                                 # and only they
    assert _props(lab_owner, kept)["folders"] and _props(owner, page)["doc"]["at"] == "path"


def test_a_carry_brings_no_folder_of_the_filing_it_replaces(owner, ws, disk, lab):
    """The opened page is filed in a folder here, and goes to the folder
    chosen there: the export still carried the folder it left, which the
    merge made there, empty, under this workspace's name for it."""
    lab_ws, lab_owner, _bob, _vera = lab
    shelf, mine = make_folder(lab_owner, "LF lab shelf"), make_folder(owner, "LF only here")
    f, data, doc = _disk_pdf(disk, "filed here.pdf", "LF filed in a folder here")
    page = _opened(owner, f)["page"]
    owner.put(f"/api/blocks/{page}", json={"properties": {"folders": [mine]}}).raise_for_status()
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={"workspace": lab_ws, "folder": shelf})
    assert r.status_code == 200, r.text
    assert r.json()["properties"]["folders"] == [shelf] and "LF only here" not in _tree_names(lab_ws, "folders")


# --- carried to another server -------------------------------------------------------------

def _digest(c, page_id) -> str:
    r = c.get(f"/api/folder-links/opened/{page_id}/carried")
    assert r.status_code == 200, r.text
    return r.json()["digest"]


def _let_go(c, page_id, digest):
    return c.post(f"/api/folder-links/opened/{page_id}/carried", json={"digest": digest})


def test_a_page_carried_to_another_server_goes_to_recently_deleted_with_the_pages_inside_it(owner, ws, disk):
    """The desktop app carried an opened PDF to another server, its export
    merged in there, and deleted the page here for good with the pages
    inside it, whatever was written on them meanwhile. Let go by the digest
    read before the export, the page goes as a carry to another workspace
    here sends it: to Recently deleted with the pages inside it, an opened
    PDF among them, all without their rows, so either file opened again is a
    new page, and the page restored comes back whole. The digest's answer
    names those pages, the opened one first: the desktop app looks for each
    on the other server before it sends anything, as a page there, live or
    in Recently deleted, would not go or would be replaced there for good."""
    f, data, doc = _disk_pdf(disk, "carried to another server.pdf", "LF carried to another server")
    g = _disk_pdf(disk, "inside the carried one.pdf", "LF an opened paper inside it")[0]
    page, paper = _opened(owner, f)["page"], _opened(owner, g)["page"]
    owner.post("/api/blocks", json={"parent_id": page, "content": "LF my note"}).raise_for_status()
    owner.put(f"/api/chats/{page}", json={"messages": [{"role": "user", "text": "LF what is this paper"}]}).raise_for_status()
    inner = owner.post("/api/pages", json={"title": "LF a page inside it", "parent_page": page}).json()["id"]
    owner.post("/api/blocks", json={"parent_id": inner, "content": "LF the inner note"}).raise_for_status()
    owner.put(f"/api/blocks/{paper}", json={"properties": {"parent_page": page}}).raise_for_status()
    library = make_page(owner, "LF a library page")["id"]
    r = owner.get(f"/api/folder-links/opened/{library}/carried")
    assert r.status_code == 400 and "does not read a file" in r.json()["detail"], r.text

    digest = _digest(owner, page)
    assert _digest(owner, page) == digest                                 # read again, nothing changed: the same
    pages = owner.get(f"/api/folder-links/opened/{page}/carried").json()["pages"]
    assert pages[0] == page and sorted(pages[1:]) == sorted([inner, paper])
    r = _let_go(owner, page, digest)
    assert r.status_code == 200 and r.json() == {"ok": True}, r.text
    for gone in (page, inner, paper):
        assert owner.get(f"/api/blocks/{gone}").status_code == 404
    trashed = {p["id"]: p for p in owner.get("/api/trash").json()["pages"]}
    assert trashed[page]["sub_pages"] == 2 and inner not in trashed and paper not in trashed
    assert _rows(ws, page) == [] and _rows(ws, paper) == [] and page not in _inside(owner, ws)
    assert f.read_bytes() == data
    r = _let_go(owner, page, digest)
    assert r.status_code == 400 and "does not read a file" in r.json()["detail"]   # pressed again: nothing to let go
    again, paper_again = _opened(owner, f), _opened(owner, g)
    assert again["created"] is True and again["page"] != page
    assert paper_again["created"] is True and paper_again["page"] != paper

    restored = owner.post(f"/api/trash/{page}/restore")
    assert restored.status_code == 200, restored.text
    assert _notes(owner, page) == ["LF my note"] and _notes(owner, inner) == ["LF the inner note"]
    assert owner.get(f"/api/blocks/{paper}").status_code == 200 and page in _inside(owner, ws)
    assert owner.get(f"/api/chats/{page}").json()["messages"][0]["text"] == "LF what is this paper"
    assert _rows(ws, page) == [] and _opened(owner, f)["page"] == again["page"]   # the file stays the new page's
    assert _opened(owner, g)["page"] == paper_again["page"]


def test_a_page_written_on_since_its_digest_is_not_let_go(owner, ws, disk):
    """A note written on the opened page, a page made inside it, or a
    comment, after the desktop app read the digest and before it let the
    page go: the copy on the other server has none of them. Refused, 409,
    and nothing here touched; let go by its digest as it stands now, the
    page goes with everything."""
    f, data, doc = _disk_pdf(disk, "written on since.pdf", "LF written on since its digest")
    page = _opened(owner, f)["page"]
    note = owner.post("/api/blocks", json={"parent_id": page, "content": "LF before"}).json()["id"]
    made = {}
    changes = (
        lambda: owner.post("/api/blocks", json={"parent_id": page, "content": "LF meanwhile"}).raise_for_status(),
        lambda: made.setdefault("sub", owner.post("/api/pages", json={
            "title": "LF made meanwhile", "parent_page": page}).json()["id"]),
        lambda: owner.post(f"/api/pages/{page}/comments", json={"block_id": note, "body": "LF a comment"}
                           ).raise_for_status(),
    )
    for change in changes:
        digest = _digest(owner, page)
        change()
        r = _let_go(owner, page, digest)
        assert r.status_code == 409 and r.json()["detail"] == linked_files.CHANGED_SINCE, r.text
        assert _props(owner, page)["doc"]["at"] == "path" and _rows(ws, page) and page in _inside(owner, ws)
        assert page not in {p["id"] for p in owner.get("/api/trash").json()["pages"]}
    assert _notes(owner, page) == ["LF before", "LF meanwhile"] and owner.get(f"/api/blocks/{made['sub']}").status_code == 200
    assert _let_go(owner, page, "not the digest").status_code == 409

    assert _let_go(owner, page, _digest(owner, page)).status_code == 200
    assert {p["id"]: p for p in owner.get("/api/trash").json()["pages"]}[page]["sub_pages"] == 1
    assert _rows(ws, page) == []


def test_no_digest_is_given_while_a_page_inside_has_not_its_file_at_hand(owner, ws, disk):
    """An opened PDF inside the opened page whose file moved: the export
    would carry that page to the other server without its PDF, and here it
    would go to Recently deleted without its rows. The digest is refused, as
    a carry to another workspace here is, until the file is located."""
    f = _disk_pdf(disk, "outer for a server.pdf", "LF the outer paper")[0]
    g = _disk_pdf(disk, "inner for a server.pdf", "LF the inner paper")[0]
    page, paper = _opened(owner, f)["page"], _opened(owner, g)["page"]
    owner.put(f"/api/blocks/{paper}", json={"properties": {"parent_page": page}}).raise_for_status()
    moved = g.with_name("inner moved away.pdf")
    g.rename(moved)
    r = owner.get(f"/api/folder-links/opened/{page}/carried")
    assert r.status_code == 400 and "is gone or has just changed" in r.json()["detail"], r.text
    assert owner.post(f"/api/folder-links/opened/{paper}/locate", json={"path": str(moved)}).status_code == 200
    assert _digest(owner, page)                                           # located: the carry may go


def test_a_file_changed_just_as_it_is_added_stores_nothing_as_the_page_s(owner, ws, disk, monkeypatch):
    f, data, doc = _disk_pdf(disk, "racing.pdf", "LF racing the store")
    page = _opened(owner, f)["page"]
    monkeypatch.setattr(storage, "store_pdf_stream", lambda ws, stream: ("f" * 24, False, 1))
    r = owner.post(f"/api/folder-links/opened/{page}/add", json={})
    assert r.status_code == 400 and "changed" in r.json()["detail"]
    assert _props(owner, page)["doc"]["at"] == "path" and _rows(ws, page)


# --- what never sees such a file ---------------------------------------------------------

def test_the_quota_the_orphan_purge_and_backups_never_see_it(owner, ws, disk, tmp_path):
    f, data, doc = _disk_pdf(disk, "unseen.pdf", "LF unseen by the store")
    name = f"{doc}.pdf"
    before = storage.usage(ws)
    page = _opened(owner, f)["page"]
    assert storage.usage(ws) == before
    assert name not in {n for n, _, _ in storage.list(ws)}
    assert storage.size(ws, name) is None and storage.stat(ws, name) is None and not storage.touch(ws, name)

    swept = upload_gc.reconcile(ws)
    assert name not in swept["recorded"] and name not in swept["purged"] and f.read_bytes() == data

    backup = tmp_path / "backup.zip"
    ws_backup.write_zip(ws, backup)
    with zipfile.ZipFile(backup) as z:
        assert f"uploads/{name}" not in z.namelist()
        with z.open(next(n for n in z.namelist() if n.endswith("pages.db"))) as pages:
            assert str(f.parent).encode() not in pages.read()            # no path in a workspace's own files
    assert _rows(ws, page)


def test_the_off_site_copy_never_sees_it_nor_vaults_db(data, monkeypatch, tmp_path_factory):  # noqa: F811: the fixture
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    f = tmp_path_factory.mktemp("disk") / "paper.pdf"
    _write(f, _pdf("LF never off site"))
    seen = linked_files.hash_file(f)
    linked_files._put_row("ws-a", str(f), seen, "p1")
    assert storage.readable("ws-a", f"{seen[0]}.pdf")
    round_ = offsite.tick()
    assert sorted(round_["copied"]) == ["users.db", "ws-a/data.db", "ws-a/pages.db", "ws-b/data.db", "ws-b/pages.db"]
    assert not [key for key in data.keys() if seen[0] in key or "vaults" in key]


def test_a_gamma_export_carries_the_bytes(owner, disk, tmp_path):
    """How a page crosses to another workspace."""
    f, data, doc = _disk_pdf(disk, "carried.pdf", "LF carried across")
    page = _opened(owner, f)["page"]
    r = owner.get(f"/api/pages/{page}/export", params={"mode": "gamma"})
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert z.read(f"uploads/{doc}.pdf") == data
        pages = z.read("pages.db")
    copy = tmp_path / "pages.db"
    copy.write_bytes(pages)
    with closing(sqlite3.connect(copy)) as conn:
        props = json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page,)).fetchone()[0])
    assert props["doc"]["hash"] == doc and props["doc"]["at"] == "store"           # stored, where it lands


def test_embedded_annotations_are_read_and_the_file_is_never_written(owner, ws, disk):
    data = _annotated_pdf(pages=5)
    f = disk / "annotated.pdf"
    _write(f, data)
    doc, stat = storage.content_digest(data), f.stat()
    page = _opened(owner, f)["page"]
    r = owner.post("/api/import/pdf-annotations", json={"block_id": page, "doc_id": doc, "strip": True})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 2 and r.json()["stripped"] == 0
    assert f.read_bytes() == data and f.stat().st_mtime_ns == stat.st_mtime_ns
    assert not storage.exists(ws, f"{doc}.pdf")
    kids = owner.get(f"/api/blocks/{page}/children").json()["children"]
    marks = [b for b in kids if b["properties"].get("imported_annot")]
    assert len(marks) == 2 and not any(b["properties"].get("annot_stripped") for b in marks)


# --- what is refused ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def lab(owner):
    """A shared workspace of lf_owner's, with an editor and a viewer:
    ``(workspace, its owner, editor and viewer, clients asking in it)``."""
    info = workspaces.create("LF lab", account_of("lf_owner"), kind="shared")

    def member(name, role):
        make_user(name, "pw")
        workspaces.set_member(info["id"], account_of(name), role, by=account_of("lf_owner"))
        return login(name, "pw")

    clients = [login("lf_owner", "pw"), member("lf_bob", "editor"), member("lf_vera", "viewer")]
    for c in clients:
        c.params = {"ws": info["id"]}
    return info["id"], *clients


def test_a_member_who_only_views_is_not_told_the_path(lab, disk):
    _ws, alice, bob, vera = lab
    f = _disk_pdf(disk, "private.pdf", "LF a person's own reading")[0]
    page = _opened(alice, f)["page"]
    assert vera.get(f"/api/folder-links/page/{page}").json()["reader"] == {"path": "", "missing": False, "mine": False, **PDF}
    assert bob.get(f"/api/folder-links/page/{page}").json()["reader"]["path"] == str(f.resolve())


def test_the_page_says_whether_the_asking_account_opened_the_file(lab, disk):
    """``reader.mine``: the desktop app takes an opened PDF to another
    server only for the account that opened it, as the server keeps it
    pushing or takes it to another workspace only for that account."""
    _ws, alice, bob, vera = lab
    f = _disk_pdf(disk, "whose.pdf", "LF whose file is this")[0]
    page = _opened(alice, f)["page"]
    assert alice.get(f"/api/folder-links/page/{page}").json()["reader"]["mine"] is True
    assert bob.get(f"/api/folder-links/page/{page}").json()["reader"]["mine"] is False
    assert vera.get(f"/api/folder-links/page/{page}").json()["reader"]["mine"] is False
    assert _opened(bob, f)["page"] == page                                # opened again by bob: still alice's
    assert bob.get(f"/api/folder-links/page/{page}").json()["reader"]["mine"] is False


def test_only_the_account_that_opened_a_file_keeps_it_pushing_or_takes_it_to_another_workspace(lab, disk):
    """Another editor of the workspace added a member's opened PDF as a vault
    of one file, in their own workspace or in this one as its maker: the file
    on the opener's computer went on reaching a workspace the opener has no
    role in, and the opener could not see the vault or stop it. What goes on
    reading a path, or takes the page elsewhere, is the opener's alone."""
    lab_ws, alice, bob, _vera = lab
    f = _disk_pdf(disk, "alice private.pdf", "LF v1 of alice's draft")[0]
    page = _opened(alice, f)["page"]
    for body in ({"workspace": workspace_of("lf_bob"), "keep_pushing": True}, {"workspace": workspace_of("lf_bob")},
                 {"keep_pushing": True}):
        r = bob.post(f"/api/folder-links/opened/{page}/add", json=body)
        assert r.status_code == 400 and "opened" in r.json()["detail"], (body, r.text)
    assert _props(alice, page)["doc"]["at"] == "path" and _rows(lab_ws, page)
    assert not [l for l in folder_links.all_links() if l["page_id"] == page]
    r = bob.post(f"/api/folder-links/opened/{page}/add", json={})      # stored in the library here: no path follows
    assert r.status_code == 200 and _rows(lab_ws, page) == []


@pytest.mark.parametrize("how", ["opened again", "located"])
def test_opening_or_locating_another_accounts_file_leaves_it_the_openers(lab, disk, how):
    """Another editor opened the same path again, or located it, and their
    row replaced the opener's: they could then make it a vault of theirs, or
    take the opener's page and notes to their own workspace, and the opener
    could no longer. The rows go on naming the opener."""
    lab_ws, alice, bob, _vera = lab
    f = _disk_pdf(disk, f"alice {how}.pdf", f"LF alice's draft {how}")[0]
    page = _opened(alice, f)["page"]
    if how == "opened again":
        assert _opened(bob, f)["page"] == page
    else:
        assert bob.post(f"/api/folder-links/opened/{page}/locate", json={"path": str(f)}).status_code == 200
    with connect_vaults_db() as conn:
        assert {r[0] for r in conn.execute("SELECT actor FROM linked_files WHERE page_id = ?", (page,))} == {
            account_of("lf_owner")}
    for body in ({"workspace": workspace_of("lf_bob")}, {"keep_pushing": True}):
        r = bob.post(f"/api/folder-links/opened/{page}/add", json=body)
        assert r.status_code == 400 and "opened" in r.json()["detail"], (body, r.text)
    r = alice.post(f"/api/folder-links/opened/{page}/add", json={"keep_pushing": True})
    assert r.status_code == 200, r.text
    link = next(l for l in folder_links.all_links() if l["page_id"] == page)
    assert alice.delete(f"/api/folder-links/{link['id']}").status_code == 200


def test_only_the_account_that_opened_a_file_lets_it_go_after_a_carry_to_another_server(lab, disk):
    """Another editor of the workspace, or a viewer, read the digest or let
    the page go: what takes an opened page to another server is its
    opener's alone, as taking it to another workspace here is."""
    lab_ws, alice, bob, vera = lab
    f = _disk_pdf(disk, "alice carried.pdf", "LF alice's paper, carried away")[0]
    page = _opened(alice, f)["page"]
    digest = _digest(alice, page)
    for r in (bob.get(f"/api/folder-links/opened/{page}/carried"), _let_go(bob, page, digest)):
        assert r.status_code == 400 and "opened" in r.json()["detail"], r.text
    assert vera.get(f"/api/folder-links/opened/{page}/carried").status_code == 403
    assert _let_go(vera, page, digest).status_code == 403
    assert _props(alice, page)["doc"]["at"] == "path" and _rows(lab_ws, page)
    assert _let_go(alice, page, digest).status_code == 200
    assert alice.get(f"/api/blocks/{page}").status_code == 404 and _rows(lab_ws, page) == []


def _highlighted(c, path, data):
    """A file opened, with one highlight on its line: ``(page, stored name)``."""
    _write(path, data)
    page = _opened(c, path)["page"]
    c.post("/api/blocks", json={"parent_id": page, "content": "", "properties": {
        "pdf_position": _position(1, _line_rect(72, 100, OLD_LINE)), "quote": OLD_LINE, "color": "#ffe28fa6"}}
    ).raise_for_status()
    return page, f"{storage.content_digest(data)}.pdf"


def _log_actors(ws, page) -> list:
    with connect_pages_db(ws) as conn:
        return [r[0] for r in conn.execute("SELECT actor FROM page_ops WHERE page_id = ? ORDER BY seq", (page,))]


def test_a_change_made_by_a_request_is_recorded_under_who_asked(lab, disk):
    ws, alice, bob, _vera = lab
    new_bytes = _lines([[(72, 300, OLD_LINE)]])

    # locate
    page, _name = _highlighted(alice, disk / "bob-a.pdf", _lines([[(72, 100, OLD_LINE)]]))
    found = disk / "bob-found.pdf"
    _write(found, new_bytes)
    (disk / "bob-a.pdf").unlink()
    r = bob.post(f"/api/folder-links/opened/{page}/locate", json={"path": str(found)})
    assert r.status_code == 200, r.text
    new = storage.content_digest(new_bytes)
    assert _log_actors(ws, page)[-1] == account_of("lf_bob")
    assert jobs.latest(account_of("lf_bob"), "relocate-highlights", f"{page}:{new}") is not None
    assert jobs.latest(account_of("lf_owner"), "relocate-highlights", f"{page}:{new}") is None

    # opened again, changed since
    f = disk / "bob-b.pdf"
    moved = _lines([[(72, 100, OLD_LINE), (72, 500, "a line of its own")]])
    other, _name = _highlighted(alice, f, _lines([[(72, 100, OLD_LINE), (72, 400, "another line")]]))
    _write(f, moved, age=20)
    assert _opened(bob, f)["page"] == other
    assert account_of("lf_bob") in _log_actors(ws, other)
    assert jobs.latest(account_of("lf_bob"), "relocate-highlights", f"{other}:{storage.content_digest(moved)}") is not None


def test_a_change_on_disk_alone_is_made_as_who_opened_the_file(lab, disk):
    ws, _alice, bob, _vera = lab
    data = _lines([[(72, 100, OLD_LINE), (72, 200, "and a line for follow")]])
    f = disk / "alone.pdf"
    page, name = _highlighted(bob, f, data)
    with connect_vaults_db() as conn:
        assert conn.execute("SELECT actor FROM linked_files WHERE page_id = ?", (page,)).fetchone() == (account_of("lf_bob"),)
    changed = _lines([[(72, 100, OLD_LINE), (72, 250, "and a line for follow")]])
    _write(f, changed, age=20)
    linked_files.follow(ws, name)
    assert _log_actors(ws, page)[-1] == account_of("lf_bob")
    new = storage.content_digest(changed)
    assert jobs.latest(account_of("lf_bob"), "relocate-highlights", f"{page}:{new}") is not None


def test_a_change_on_disk_falls_to_the_owner_once_its_opener_may_not_edit(lab, disk):
    ws, _alice, bob, _vera = lab
    f = disk / "left.pdf"
    page, name = _highlighted(bob, f, _lines([[(72, 100, OLD_LINE), (72, 220, "a line for a leaver")]]))
    with connect_vaults_db() as conn:
        conn.execute("UPDATE linked_files SET actor = 'an account long gone' WHERE page_id = ?", (page,))
    _write(f, _lines([[(72, 100, OLD_LINE), (72, 260, "a line for a leaver")]]), age=20)
    linked_files.follow(ws, name)
    assert _log_actors(ws, page)[-1] == account_of("lf_owner")
    with connect_vaults_db() as conn:          # the row still names who opened it: the owner only wrote the change
        assert conn.execute("SELECT actor FROM linked_files WHERE page_id = ?", (page,)).fetchone() == ("an account long gone",)


def test_a_change_on_disk_nobody_may_write_waits_for_the_next_look(owner, ws, disk, monkeypatch):
    """Where nobody may edit the workspace any more (one with no member
    row), the read of the page before the change, which asks first
    (``LocalSink.current``), raised past ``follow``'s try: an exception for
    the worker and for the names after it. The change waits, as a file that
    cannot be read now does, and is taken at a later look."""
    f, data, doc = _disk_pdf(disk, "nobody may write.pdf", "LF nobody may write this")
    page = _opened(owner, f)["page"]
    changed = _pdf("LF nobody may write this, v2")
    _write(f, changed, age=20)
    with monkeypatch.context() as m:
        m.setattr(folder_links, "may_edit", lambda ws, user_id: False)
        linked_files.follow(ws, f"{doc}.pdf")
    assert _props(owner, page)["doc"]["hash"] == doc and [name for name, _path in _rows(ws, page)] == [f"{doc}.pdf"]
    linked_files.follow(ws, f"{doc}.pdf")
    assert _props(owner, page)["doc"]["hash"] == storage.content_digest(changed)


def test_a_change_the_owner_wrote_for_an_opener_leaves_the_file_the_openers(lab, disk):
    """The owner's write of a change on disk, while the opener could not edit,
    put the owner's name in the row, which says who opened the file: the
    owner could then make a vault of a path on the opener's computer, and the
    opener could not, back as an editor."""
    ws, alice, bob, _vera = lab
    f = disk / "bobs-own.pdf"
    page, name = _highlighted(bob, f, _lines([[(72, 100, OLD_LINE), (72, 220, "a line of bob's")]]))
    workspaces.set_member(ws, account_of("lf_bob"), "viewer", by=account_of("lf_owner"))
    try:
        _write(f, _lines([[(72, 100, OLD_LINE), (72, 260, "a line of bob's")]]), age=20)
        linked_files.follow(ws, name)
        assert _log_actors(ws, page)[-1] == account_of("lf_owner")
    finally:
        workspaces.set_member(ws, account_of("lf_bob"), "editor", by=account_of("lf_owner"))
    r = alice.post(f"/api/folder-links/opened/{page}/add", json={"keep_pushing": True})
    assert r.status_code == 400 and "opened" in r.json()["detail"]
    r = bob.post(f"/api/folder-links/opened/{page}/add", json={"keep_pushing": True})
    assert r.status_code == 200, r.text
    assert [l["created_by"] for l in folder_links.all_links() if l["page_id"] == page] == [account_of("lf_bob")]


@pytest.mark.skipif(sys.platform != "win32", reason="a handle that shares nothing is Windows'")
def test_a_file_held_with_no_sharing_names_no_path_in_an_answer(owner, ws, disk):
    import ctypes
    from ctypes import wintypes

    f, _data, doc = _disk_pdf(disk, "held-no-sharing.pdf", "LF held with no sharing")
    page = _opened(owner, f)["page"]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.HANDLE]
    handle = kernel.CreateFileW(str(f), 0x80000000, 0, None, 3, 0x80, None)     # GENERIC_READ, no sharing, OPEN_EXISTING
    try:
        r = owner.post("/api/import/pdf-annotations", json={"block_id": page, "doc_id": doc})
    finally:
        kernel.CloseHandle(handle)
    assert r.status_code == 400 and "held-no-sharing" not in r.text and "Permission denied" in r.json()["detail"]


def test_an_error_read_out_names_no_path_of_this_computer():
    path = "/home/ann/private/paper.pdf"
    denied = PermissionError(13, "Permission denied", path)
    assert jobs.error_text(denied) == "Permission denied"
    assert jobs.error_text(OSError("No space left on device")) == "No space left on device"
    assert jobs.error_text(ValueError("a bad backup")) == "a bad backup"
    with pytest.raises(Exception) as mupdf:
        pymupdf.open(path)
    assert "paper.pdf" in str(mupdf.value) and "paper.pdf" not in jobs.error_text(mupdf.value)

    job = SimpleNamespace(kind="export", id="abcdef")
    assert "paper.pdf" not in jobs._message(job, denied)
    assert "paper.pdf" not in jobs._message(job, mupdf.value)


def test_an_ai_tool_that_fails_on_a_file_sends_no_path_to_the_model(owner, ws, monkeypatch):
    def reads(conn, ws, scope, args):
        raise PermissionError(13, "Permission denied", "/home/ann/private/paper.pdf")

    name, tool = next((n, t) for n, t in ai_tools._BY_NAME.items() if not t["mutating"])
    monkeypatch.setitem(ai_tools._BY_NAME, name, {**tool, "run": reads})
    monkeypatch.setattr(ai_tools, "agent_tools", lambda *a, **kw: [{"name": name}])
    result, _action = ai_tools.run_agent_tool(ws, {"type": sorted(tool["scopes"])[0]}, name, {})
    assert result == "error: Permission denied"


def test_what_cannot_be_opened_is_refused(owner, disk):
    disk.mkdir(parents=True, exist_ok=True)
    fake = disk / "fake.pdf"
    fake.write_text("no pdf either", encoding="utf-8")
    for path, why in (("relative/paper.pdf", "full path"), (disk / "there-is-none.pdf", "no file"), (fake, "%PDF")):
        r = _open(owner, path)
        assert r.status_code == 400 and why in r.json()["detail"], r.text

    inside = config.DATA_DIR / "inside.pdf"
    inside.write_bytes(_pdf("LF in the data directory"))
    try:
        r = _open(owner, inside)
        assert r.status_code == 400 and "data directory" in r.json()["detail"]
    finally:
        inside.unlink()
    assert fresh_client().post("/api/folder-links/open", json={"path": str(fake)}).status_code == 401


@pytest.mark.parametrize("sent, status", [(None, 403), ("wrong", 403), ("s3cret", 200), ("s3crét", 403)])
def test_a_route_that_takes_a_path_wants_the_shell_s_secret(owner, disk, monkeypatch, sent, status):
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "s3cret")
    f = _disk_pdf(disk, "secret.pdf", f"LF a shell's file {sent}")[0]
    headers = {"X-Gamma-Shell": sent.encode("latin-1")} if sent else {}
    r = _open(owner, f, headers=headers)
    assert r.status_code == status, r.text
    if status == 403:
        assert owner.post("/api/folder-links", json={"kind": "file", "page": "p", "path": str(f)},
                          headers=headers).status_code == 403
        assert owner.post("/api/folder-links/opened/p/locate", json={"path": str(f)}, headers=headers).status_code == 403
    else:
        page = r.json()["page"]
        monkeypatch.setenv("GAMMA_SHELL_SECRET", "")                     # none: no check at all
        assert _opened(owner, f)["page"] == page
        monkeypatch.setenv("GAMMA_SHELL_SECRET", "s3cret")
        assert owner.get(f"/api/folder-links/page/{page}").status_code == 200   # it takes no path: no secret
        assert owner.post(f"/api/folder-links/opened/{page}/add", json={}).status_code == 200


def test_a_file_another_handle_holds_open_is_read_all_the_same(owner, ws, disk):
    f, data, doc = _disk_pdf(disk, "held.pdf", "LF held open by a viewer")
    with open(f, "r+b"):
        answer = _opened(owner, f)
        assert owner.get(f"/api/uploads/{doc}.pdf").content == data
    assert answer["created"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="a byte-range lock that refuses other readers is Windows'")
def test_a_file_its_writer_has_locked_is_refused_and_left_for_later(owner, ws, disk):
    import msvcrt

    f, data, doc = _disk_pdf(disk, "locked.pdf", "LF locked by its writer")
    page = _opened(owner, f)["page"]
    other, other_data, other_doc = _disk_pdf(disk, "locked-new.pdf", "LF locked before it was opened")
    with open(f, "r+b") as held, open(other, "r+b") as also:
        msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, len(data))
        msvcrt.locking(also.fileno(), msvcrt.LK_NBLCK, len(other_data))
        try:
            r = _open(owner, other)
            assert r.status_code == 400 and "Cannot read" in r.json()["detail"]

            _index(ws, doc)                                               # out of reach: nothing recorded
            assert owner.get(f"/api/pdf-info/{doc}").status_code == 404   # and no ``pages = 0`` kept
            assert _indexed(ws, doc) is None

            os.utime(f, (time.time() - 30, time.time() - 30))             # the stat moved: a look finds it locked
            linked_files.follow(ws, f"{doc}.pdf")
            assert _props(owner, page)["doc"]["hash"] == doc
        finally:
            msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, len(data))
            msvcrt.locking(also.fileno(), msvcrt.LK_UNLCK, len(other_data))
    linked_files.follow(ws, f"{doc}.pdf")
    assert owner.get(f"/api/uploads/{doc}.pdf").content == data


# --- the sweep ---------------------------------------------------------------------------

def _age_page(ws, page_id, days=91):
    then = db.format_stamp(db.datetime.now(db.timezone.utc) - db.timedelta(days=days))
    with connect_pages_db(ws) as conn:
        conn.execute("UPDATE unified_blocks SET updated_at = ? WHERE id = ?", (then, page_id))


def test_the_sweep_forgets_an_opened_file_nobody_kept(owner, ws, disk, monkeypatch):
    monkeypatch.setattr(linked_files, "_note_moved", lambda ws, name: None)
    idle, _d, _i = _disk_pdf(disk, "idle.pdf", "LF read and left")
    marked = _disk_pdf(disk, "marked.pdf", "LF read and marked")[0]
    chatted = _disk_pdf(disk, "chatted.pdf", "LF read and asked about")[0]
    recent = _disk_pdf(disk, "recent.pdf", "LF read just now")[0]
    pages = {p.stem: _opened(owner, p)["page"] for p in (idle, marked, chatted, recent)}
    owner.post("/api/blocks", json={"parent_id": pages["marked"], "content": "a thought"}).raise_for_status()
    owner.put(f"/api/chats/{pages['chatted']}", json={"messages": [{"role": "user", "content": "hi"}]}).raise_for_status()
    note = make_page(owner, "LF moved in", {"parent_page": _opened_files(ws)})   # no opened file: no business of the sweep
    for page in (pages["idle"], pages["marked"], pages["chatted"], note["id"]):
        _age_page(ws, page)

    linked_files.sweep()
    assert owner.get(f"/api/blocks/{pages['idle']}").status_code == 404 and _rows(ws, pages["idle"]) == []
    assert idle.is_file()
    for stem in ("marked", "chatted", "recent"):
        assert owner.get(f"/api/blocks/{pages[stem]}").status_code == 200 and _rows(ws, pages[stem])
    assert owner.get(f"/api/blocks/{note['id']}").status_code == 200


def test_a_row_goes_with_its_page_and_with_its_workspace(owner, ws, disk, monkeypatch):
    monkeypatch.setattr(linked_files, "_note_moved", lambda ws, name: None)
    f, data, doc = _disk_pdf(disk, "row.pdf", "LF the row follows the page")
    page = _opened(owner, f)["page"]
    owner.delete(f"/api/blocks/{page}").raise_for_status()
    linked_files.sweep()
    assert _rows(ws, page)                                                # in Recently deleted: a restore finds its file
    owner.delete(f"/api/trash/{page}").raise_for_status()
    linked_files.sweep()
    assert _rows(ws, page) == []

    linked_files._put_row("no-such-workspace", str(f), linked_files.hash_file(f), "p1")
    linked_files._put_row(ws, str(f), linked_files.hash_file(f), "")      # a vault's picture: no page, and kept
    linked_files.sweep()
    assert _rows("no-such-workspace", "p1") == [] and _rows(ws, "") == [(f"{doc}.pdf", str(f))]
    linked_files._drop_row(ws, f"{doc}.pdf", str(f))


def test_the_tick_runs_the_sweep_only_where_links_are_kept(monkeypatch):
    swept = []
    monkeypatch.setattr(linked_files, "sweep", lambda: swept.append(1))
    monkeypatch.delenv("GAMMA_FOLDER_LINKS", raising=False)
    folder_links.tick()
    assert swept == []
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(folder_links, "all_links", lambda: [])
    folder_links.tick()
    assert swept == [1]


def test_the_sweep_looks_again_under_the_write_lock_before_it_deletes(owner, ws, disk, monkeypatch):
    """A mark made after the sweep listed the page is not deleted with it."""
    monkeypatch.setattr(linked_files, "_note_moved", lambda ws, name: None)
    page = _opened(owner, _disk_pdf(disk, "late.pdf", "LF marked at the last moment")[0])["page"]
    _age_page(ws, page)
    locked = linked_files.write_lock

    def marked_first(conn):
        owner.post("/api/blocks", json={"parent_id": page, "content": "a late thought"}).raise_for_status()
        _age_page(ws, page)
        locked(conn)

    monkeypatch.setattr(linked_files, "write_lock", marked_first)
    linked_files.sweep()
    assert owner.get(f"/api/blocks/{page}").status_code == 200 and _rows(ws, page)


def test_the_sweep_leaves_a_page_someone_has_open(owner, ws, disk, monkeypatch):
    monkeypatch.setattr(linked_files, "_note_moved", lambda ws, name: None)
    page = _opened(owner, _disk_pdf(disk, "viewed.pdf", "LF reopened from the list")[0])["page"]
    _age_page(ws, page)
    monkeypatch.setattr(linked_files.collab, "room_for",
                        lambda w, p: SimpleNamespace(peers={"a": 1}) if p == page else None)
    linked_files.sweep()
    assert owner.get(f"/api/blocks/{page}").status_code == 200

    monkeypatch.setattr(linked_files.collab, "room_for", lambda w, p: None)
    linked_files.sweep()
    assert owner.get(f"/api/blocks/{page}").status_code == 404


def test_a_workspace_that_cannot_be_opened_stops_no_sweep(owner, ws, disk, monkeypatch):
    f, _data, doc = _disk_pdf(disk, "bystander.pdf", "LF beside a broken workspace")
    _opened(owner, f)
    make_user("lf_second", "pw")
    second = login("lf_second", "pw")
    _opened(second, _disk_pdf(disk, "second.pdf", "LF in a second workspace")[0])
    _write(f, _pdf("LF changed under an open viewer"))

    tried, queued = [], []

    def stuck(ws, page_ids):
        tried.append(ws)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(linked_files, "_forget", stuck)
    monkeypatch.setattr(linked_files, "_note_moved", lambda ws, name: queued.append((ws, name)))
    linked_files.sweep()
    assert {ws, workspace_of("lf_second")} <= set(tried) and (ws, f"{doc}.pdf") in queued


# --- vaults.db -----------------------------------------------------------------------------

def test_a_server_that_keeps_no_links_never_makes_vaults_db(data_dir, monkeypatch):
    monkeypatch.delenv("GAMMA_FOLDER_LINKS", raising=False)
    monkeypatch.setattr(db, "connect_vaults_db", lambda: pytest.fail("vaults.db opened"))
    monkeypatch.setattr(storage, "connect_vaults_db", lambda: pytest.fail("vaults.db opened"))
    app_mod._startup_maintenance()
    seed.create_workspace_files("lfNoLinks")
    assert storage.open_path("lfNoLinks", "a" * 24 + ".pdf") is None
    assert not storage.readable("lfNoLinks", "a" * 24 + ".pdf") and not storage.is_linked("lfNoLinks", "a" * 24 + ".pdf")
    folder_links.tick()
    db_maintenance.tick()
    assert integrity.db_files()[0] == config.USERS_DB
    assert "vaults.db" not in backups.create("nolinks")["files"]
    assert not (data_dir / "vaults.db").exists()


def test_the_routes_of_a_server_that_keeps_no_links_say_where_they_are(owner, monkeypatch):
    monkeypatch.delenv("GAMMA_FOLDER_LINKS", raising=False)
    for r in (owner.post("/api/folder-links/open", json={"path": "/x.pdf"}), owner.get("/api/folder-links/page/p"),
              owner.post("/api/folder-links/opened/p/add", json={}), owner.post("/api/folder-links/opened/p/locate", json={"path": "/x"}),
              owner.get("/api/folder-links")):
        assert r.status_code == 404 and "Synced vaults are kept by the Gamma desktop app" in r.json()["detail"]


def test_the_desktop_server_keeps_vaults_db_with_the_other_databases(data_dir, monkeypatch):
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    app_mod._startup_maintenance()
    vaults = data_dir / "vaults.db"
    assert vaults.is_file() and config.VAULTS_DB == vaults
    with closing(connect_vaults_db()) as conn:
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} >= {
            "linked_files", "vault_files", "vault_dirs", "vault_history"}
        columns = {table: [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                   for table in ("linked_files", "vault_files", "vault_dirs", "vault_history")}
    assert columns == {
        "linked_files": ["ws", "name", "path", "size", "mtime_ns", "file_id", "page_id", "actor", "relocation_error"],
        "vault_files": ["link_id", "path", "page_id", "kind", "hash", "size", "mtime_ns", "file_id", "read_at", "text", "nodes",
                        "gone", "title", "folder", "seq", "ours", "was", "missing_since", "problem", "pending"],
        "vault_dirs": ["link_id", "path", "folder_id", "file_id"],
        "vault_history": ["id", "link_id", "path", "at", "data"]}
    assert vaults in integrity.db_files()
    assert "vaults.db" in {f["file"] for f in integrity.check_all()["files"]}
    assert "vaults.db" in backups.create("links")["files"]
    assert "vaults.db" in backups.create("links-global", workspaces=False)["files"]

    # "" is no longer users.db alone
    assert (str(vaults), "") in db_maintenance._databases()
    assert db_maintenance._label(str(vaults), "") == "vaults.db" and db_maintenance._label(str(config.USERS_DB), "") == "users.db"
    with connect_vaults_db():
        assert (str(vaults), "") in db.cached_files()
    assert db_maintenance._optimize(str(vaults), "") is True
    assert not [p for p in data_dir.rglob("*") if p.name == "linked_files"]


def test_a_large_file_id_is_kept(ws, disk):
    """A file id can pass what SQLite's integers hold."""
    linked_files._put_row(ws, "/big-id.pdf", ("e" * 24, 1, 1, str(2 ** 64 - 1)), "big")
    with connect_vaults_db() as conn:
        row = conn.execute("SELECT file_id FROM linked_files WHERE page_id = 'big'").fetchone()
        conn.execute("DELETE FROM linked_files WHERE page_id = 'big'")
    assert row == (str(2 ** 64 - 1),)


# --- a link's direction ---------------------------------------------------------------------

def test_a_link_says_its_direction_in_words_and_keeps_numbers(owner, disk):
    folder = make_folder(owner, "LF direction")
    link = _create(owner, folder=folder, path=str(disk / "direction"), push="pdfs")
    assert link["pull"] is True and link["push"] == "pdfs"
    assert folder_links.get_link(link["id"])["push"] == folder_links.PUSH_PDFS

    def patched(value):
        r = owner.patch(f"/api/folder-links/{link['id']}", json={"push": value})
        assert r.status_code == 200, r.text
        return r.json()["push"]

    assert patched("off") == "off" and patched("pdfs") == "pdfs"
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"push": "sideways"})
    assert r.status_code == 400 and "off, pdfs or everything" in r.json()["detail"]
    r = owner.post("/api/folder-links", json={"folder": folder, "path": str(disk / "else"), "push": "sideways"})
    assert r.status_code == 400 and "off, pdfs or everything" in r.json()["detail"]
    # a vault that would push into the pages this one pushes PDFs into is refused (gamma/folder_links.py
    # check_vault); this one may push everything
    r = owner.post("/api/folder-links", json={"folder": folder, "path": str(disk / "else"), "push": "everything"})
    assert r.status_code == 400 and "pushes into too" in r.json()["detail"]
    assert owner.get(f"/api/folder-links/{link['id']}").json()["push"] == "pdfs"
    assert patched("everything") == "everything"
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_no_older_shell_is_spoken_to(owner, disk):
    """The routes are the desktop's own server, shipped with its shell."""
    folder = make_folder(owner, "LF old shell")
    link = _create(owner, folder=folder, path=str(disk / "old"), push="pdfs")
    url = f"/api/folder-links/{link['id']}"
    assert owner.post("/api/folder-links", json={"folder": folder, "path": str(disk / "bool"), "push": True}).status_code == 422
    assert owner.patch(url, json={"push": True}).status_code == 422
    assert owner.post(f"{url}/sync", params={"wait": 1, "force": 1}).status_code == 422
    assert owner.delete(url, params={"files": "yes"}).status_code == 422

    assert _sync(owner, link["id"], force="pull")["last_error"] == ""
    assert owner.delete(url, params={"files": "keep"}).status_code == 200
