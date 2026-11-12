"""A work folder's file and the library (routers/pages.py; docs/dev/
work_folders.md "Add to library, Save a copy, Remove"): *Add to library…*
stores the file's PDF from the path this server reads it at and makes the
page a library document as well, filed in a library folder when asked;
*Remove from library* takes it out again, its library folders with it, its
title back to the file's; *Save a copy to library…* makes a library page of
the PDF's bytes as they are now, or answers the one that owns them."""

import secrets
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_folder, make_page, make_user, mark_work_folder, work_props, workspace_of
from gamma import linked_files, pdf_meta, storage
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page
from gamma.db import connect_pages_db
from gamma.documents.model import document, stored_name

USER, PASSWORD = "wlr_owner", "pw-wlr-1"


@pytest.fixture(scope="module")
def lib():
    """A work folder W that does not pull, its directory D, library folders
    L and L2."""
    make_user(USER, PASSWORD)
    owner = login(USER, PASSWORD)
    ids = {name: make_folder(owner, path) for name, path in (
        ("W", "wlr-W"), ("D", "wlr-W/dir"), ("L", "wlr-L"), ("L2", "wlr-L/sub"))}
    mark_work_folder(owner, ids["W"], pull=False)
    return SimpleNamespace(owner=owner, ws=workspace_of(USER), account=account_of(USER), **ids)


@pytest.fixture(autouse=True)
def _links(monkeypatch):
    """This server keeps folders on disk, as the desktop's does; the PDFs'
    manifests are recorded, not computed."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    scheduled = []
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: scheduled.append(doc_id))
    return scheduled


def _pdf_on_disk(tmp_path, name="main.pdf") -> tuple:
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4\n" + secrets.token_bytes(32))
    return path, linked_files.hash_file(path)


def _file(lib, title, props) -> str:
    with connect_pages_db(lib.ws) as conn:
        return create_page(conn, title, props, actor=lib.account, client=DISK_CLIENT)["id"]


def _linked_pdf(lib, tmp_path, *, name="main.pdf", directory="") -> tuple[str, str]:
    """A PDF of W read where it is, as its vault makes one: the page and the
    ``linked_files`` row of its path; its bytes are not stored. → (page id,
    hash)."""
    path, seen = _pdf_on_disk(tmp_path, name)
    doc = document(seen[0], name=name, at="path", size=seen[1])
    page_id = _file(lib, name.rsplit(".", 1)[0], work_props(lib.W, name, doc=doc, directory=directory or lib.D))
    linked_files._put_row(lib.ws, str(path), seen, page_id, lib.account)
    return page_id, seen[0]


def _page(lib, page_id) -> dict:
    r = lib.owner.get(f"/api/blocks/{page_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _listed(lib) -> set:
    return {p["id"] for p in lib.owner.get("/api/blocks/root/children").json()["children"]}


def test_add_stores_the_bytes_from_the_linked_path_and_lists_the_file(lib, tmp_path, _links):
    page_id, digest = _linked_pdf(lib, tmp_path)
    assert not storage.exists(lib.ws, stored_name(digest, ".pdf")) and page_id not in _listed(lib)
    r = lib.owner.post(f"/api/pages/{page_id}/library", json={})
    assert r.status_code == 200, r.text
    assert storage.exists(lib.ws, stored_name(digest, ".pdf"))
    summary = r.json()["page"]
    assert summary["id"] == page_id and summary["properties"]["library"] is True
    props = _page(lib, page_id)["properties"]
    assert props["library"] is True and props["folders"] == [lib.D]
    assert props["auto_title"] == "main"  # its record may title it, as an upload's
    assert props["doc"]["at"] == "path" and props["work_folder"] == lib.W  # the same page, at its path
    assert digest in _links and page_id in _listed(lib)
    assert lib.owner.post("/api/pages/by-docs", json={"doc_ids": [digest]}).json()["pages"][digest]["id"] == page_id


def test_add_files_it_in_a_library_folder_too(lib, tmp_path):
    page_id, _ = _linked_pdf(lib, tmp_path)
    assert lib.owner.post(f"/api/pages/{page_id}/library", json={"folder": lib.D}).status_code == 400
    assert lib.owner.post(f"/api/pages/{page_id}/library", json={"folder": "no-such-folder"}).status_code == 400
    r = lib.owner.post(f"/api/pages/{page_id}/library", json={"folder": lib.L2})
    assert r.status_code == 200, r.text
    assert _page(lib, page_id)["properties"]["folders"] == [lib.D, lib.L2]
    again = lib.owner.post(f"/api/pages/{page_id}/library", json={})
    assert again.status_code == 400  # in the library already


def test_add_is_for_a_work_folders_file(lib):
    page = make_page(lib.owner, "A library page")
    assert lib.owner.post(f"/api/pages/{page['id']}/library", json={}).status_code == 400
    assert lib.owner.post("/api/pages/no-such-page/library", json={}).status_code == 404


def test_add_of_bytes_a_library_page_owns_answers_that_page(lib, tmp_path):
    page_id, digest = _linked_pdf(lib, tmp_path)
    linked_files._store(lib.ws, digest, str(tmp_path / "main.pdf"))
    owner_page = make_page(lib.owner, "The paper", properties={"doc": document(digest, name="paper.pdf")})
    r = lib.owner.post(f"/api/pages/{page_id}/library", json={})
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": "Already in the library as “The paper”.", "page_id": owner_page["id"]}
    assert "library" not in _page(lib, page_id)["properties"]


def test_add_of_bytes_this_server_cannot_have(lib, tmp_path):
    """No path here holds them (a vault of another computer), or the file
    there is other bytes by now."""
    elsewhere = _file(lib, "elsewhere", work_props(lib.W, "elsewhere.pdf", directory=lib.D, doc=document(
        secrets.token_hex(32), name="elsewhere.pdf", at="path")))
    r = lib.owner.post(f"/api/pages/{elsewhere}/library", json={})
    assert r.status_code == 409 and r.json()["detail"] == "Its bytes are on another computer."
    changed, _ = _linked_pdf(lib, tmp_path, name="changed.pdf")
    (tmp_path / "changed.pdf").write_bytes(b"%PDF-1.4\nother bytes now")
    storage.forget_linked_stat(str(tmp_path / "changed.pdf"))
    r = lib.owner.post(f"/api/pages/{changed}/library", json={})
    assert r.status_code == 409 and r.json()["detail"] == "The file changed: try again after the next sync."
    for page_id in (elsewhere, changed):
        assert "library" not in _page(lib, page_id)["properties"]


def test_a_note_is_added_with_no_bytes_and_keeps_its_title(lib, _links):
    note = _file(lib, "plan", work_props(lib.W, "plan.md"))
    r = lib.owner.post(f"/api/pages/{note}/library", json={"folder": lib.L})
    assert r.status_code == 200, r.text
    page = _page(lib, note)
    assert page["properties"]["library"] is True and page["properties"]["folders"] == [lib.W, lib.L]
    assert "auto_title" not in page["properties"] and page["content"] == "plan" and _links == []


def test_remove_drops_the_library_folders_and_gives_back_the_files_name(lib, tmp_path):
    page_id, _ = _linked_pdf(lib, tmp_path)
    assert lib.owner.post(f"/api/pages/{page_id}/library", json={"folder": lib.L}).status_code == 200
    assert lib.owner.put(f"/api/blocks/{page_id}", json={"content": "A paper's own title"}).status_code == 200
    r = lib.owner.delete(f"/api/pages/{page_id}/library")
    assert r.status_code == 200, r.text
    assert r.json()["page"]["content"] == "main"
    page = _page(lib, page_id)
    assert page["content"] == "main" and page["properties"]["folders"] == [lib.D]
    assert not {"library", "auto_title"} & page["properties"].keys()
    assert page_id not in _listed(lib)
    assert lib.owner.delete(f"/api/pages/{page_id}/library").status_code == 400  # not in the library


def test_save_a_copy_makes_a_library_page_and_answers_it_again(lib, tmp_path, _links):
    page_id, digest = _linked_pdf(lib, tmp_path, name="draft.pdf")
    before = _page(lib, page_id)
    r = lib.owner.post(f"/api/pages/{page_id}/library-copy", json={"folder": lib.L})
    assert r.status_code == 200, r.text
    copy, existed = r.json()["page"], r.json()["existed"]
    assert existed is False and copy["id"] != page_id
    props = _page(lib, copy["id"])["properties"]
    assert props["doc"]["hash"] == digest and props["doc"]["at"] == "store" and props["folders"] == [lib.L]
    assert not {"work_folder", "library", "file_name"} & props.keys()
    assert storage.exists(lib.ws, stored_name(digest, ".pdf")) and _links.count(digest) >= 1
    assert _page(lib, page_id) == before  # the file's page is untouched
    assert copy["id"] in _listed(lib)
    _links.clear()
    again = lib.owner.post(f"/api/pages/{page_id}/library-copy", json={})
    assert again.status_code == 200 and again.json()["existed"] is True
    assert again.json()["page"]["id"] == copy["id"] and _links == []


def test_save_a_copy_is_for_a_files_pdf(lib):
    note = _file(lib, "notes", work_props(lib.W, "notes.md"))
    assert lib.owner.post(f"/api/pages/{note}/library-copy", json={}).status_code == 400
    page = make_page(lib.owner, "Not a file")
    assert lib.owner.post(f"/api/pages/{page['id']}/library-copy", json={}).status_code == 400


def test_save_a_copy_into_a_work_folder_is_refused(lib, tmp_path):
    page_id, _ = _linked_pdf(lib, tmp_path, name="fig.pdf")
    assert lib.owner.post(f"/api/pages/{page_id}/library-copy", json={"folder": lib.W}).status_code == 400
