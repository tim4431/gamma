"""Pages copied into another workspace of this server, and a whole
workspace duplicated into a new one (gamma/workspace_copy.py, the
``workspace-copy`` job of routers/ws_backups.py): what comes, what a
Zotero link's marks become, what stays behind, what is refused."""

import json
import os

import pytest

from conftest import (account_of, doc_props, drop_user, login, make_folder, make_label, make_page, make_user,
                      thread_on, workspace_of)
from zotero_engine import link_row
from zotero_fixtures import pdf_bytes

from gamma import jobs, ops, storage, workspaces, ws_backup
from gamma.blocks_store import folder_path
from gamma.db import connect_pages_db
from gamma.documents.model import page_document

MARK = {"key": "ABCD2345", "state": "remote", "filename": "in-zotero.pdf"}


@pytest.fixture(scope="module")
def me(client):
    ws = make_user("wc_ann", "wc-ann-pw")
    c = login("wc_ann", "wc-ann-pw")
    c.headers["X-Gamma-Workspace"] = ws
    return c


@pytest.fixture(scope="module")
def boss(client):
    make_user("wc_boss", "wc-boss-pw", is_admin=1)
    yield login("wc_boss", "wc-boss-pw")
    drop_user("wc_boss")


def _new_workspace(c, name) -> str:
    r = c.post("/api/workspaces", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _job(c, body) -> dict:
    r = c.post("/api/jobs/workspace-copy", json=body)
    assert r.status_code == 200, r.text
    return jobs.wait(r.json()["id"])


def _props(ws, block_id) -> dict | None:
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _paths(ws, folder_ids) -> list[list[str]]:
    with connect_pages_db(ws) as conn:
        return [folder_path(conn, f) for f in folder_ids]


def _pdf_page(c, ws, title, text, **props) -> dict:
    doc_id, _ = storage.store_pdf(ws, pdf_bytes(text))
    return make_page(c, title, {**doc_props(doc_id), **props})


def test_a_copy_brings_the_pages_whole_and_leaves_them_here(me):
    source, target = workspace_of("wc_ann"), _new_workspace(me, "WC target")
    folder, label = make_folder(me, "Reading/ML"), make_label(me, "key")
    paper = _pdf_page(me, source, "WC paper", "a copied paper", folders=[folder], labels=[label])
    inside = make_page(me, "WC inside", {"parent_page": paper["id"]})
    stays = make_page(me, "WC stays")
    ops.commit_ops(source, paper["id"], [{"op": "insert", "id": "wcNote01", "parent": paper["id"], "content": "a note"}],
                   actor=account_of("wc_ann"), client="t")
    thread_on(me, paper["id"], "wcNote01", "a comment")

    assert me.post("/api/workspace-copy/preview", json={"pages": [paper["id"]]}).json() == {"pages": 2, "zotero_pdfs": 0}
    job = _job(me, {"pages": [paper["id"]], "to": target})
    assert job["state"] == "done", job["error"]
    assert job["workspace"] == target and job["params"]["to_name"] == "WC target" and job["params"]["pages"] == 2
    assert job["result"]["pages_added"] == 2 and job["result"]["workspace"] == target
    assert job["result"]["comments_added"] == 1 and job["result"]["zotero_pdfs"] == 0

    copied = _props(target, paper["id"])
    assert _paths(target, copied["folders"]) == [["Reading", "ML"]]
    assert storage.exists(target, page_document(copied)["file"])
    assert _props(target, inside["id"])["parent_page"] == paper["id"]
    assert _props(target, "wcNote01") is not None and _props(target, stays["id"]) is None
    with connect_pages_db(target) as conn:
        assert [r[0] for r in conn.execute("SELECT content FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?)) "
                                           "AND parent_id = 'labels'", (json.dumps(copied["labels"]),))] == ["key"]
    assert _props(source, paper["id"]) is not None  # a copy: the page stays where it was

    again = _job(me, {"pages": [paper["id"]], "to": target})  # nothing comes twice
    assert again["result"]["pages_added"] == 0 and again["result"]["pages_skipped"] == 2


def test_a_page_inside_another_comes_filed_where_its_top_is(me):
    source, target = workspace_of("wc_ann"), _new_workspace(me, "WC sub-pages")
    folder = make_folder(me, "Projects/Rydberg")
    top = make_page(me, "WC top", {"folders": [folder]})
    inside = make_page(me, "WC alone", {"parent_page": top["id"]})
    job = _job(me, {"pages": [inside["id"]], "to": target})
    assert job["state"] == "done" and job["result"]["pages_added"] == 1, job["error"]
    copied = _props(target, inside["id"])
    assert "parent_page" not in copied and _paths(target, copied["folders"]) == [["Projects", "Rydberg"]]
    assert _props(source, inside["id"])["parent_page"] == top["id"]


def test_a_copy_out_of_a_zotero_linked_workspace_carries_none_of_the_links_marks(me):
    source = _new_workspace(me, "WC linked")
    target = _new_workspace(me, "WC linked elsewhere")
    link_row(source, account_of("wc_ann"))
    link_row(target, account_of("wc_ann"), library="groups/77")
    c = login("wc_ann", "wc-ann-pw")
    c.headers["X-Gamma-Workspace"] = source
    waiting = make_page(c, "WC in Zotero", {"zotero_pdf": dict(MARK), "zotero_key": "ITEM2345"})
    ops.commit_ops(source, waiting["id"], [{"op": "insert", "id": "wcTheirs1", "parent": waiting["id"], "content": "theirs",
                                            "props": {"zotero_readonly": "locked", "zotero_author": "Maya"}}],
                   actor=account_of("wc_ann"), client="t")

    assert c.post("/api/workspace-copy/preview", json={"pages": [waiting["id"]]}).json()["zotero_pdfs"] == 1
    job = _job(c, {"pages": [waiting["id"]], "to": target})
    assert job["state"] == "done" and job["result"]["zotero_pdfs"] == 1, job["error"]
    copied = _props(target, waiting["id"])
    assert "zotero_pdf" not in copied and copied["zotero_key"] == "ITEM2345"  # its key pairs it again, if ever
    assert not {"zotero_readonly", "zotero_author"} & set(_props(target, "wcTheirs1"))
    assert _props(source, waiting["id"])["zotero_pdf"] == MARK  # the original keeps what its link says
    # the target's own link will never bring that PDF: the copy takes one of its own
    c.headers["X-Gamma-Workspace"] = target
    r = c.post(f"/api/pages/{waiting['id']}/attachment", json={"source_url": "https://example.org/own.pdf"})
    assert r.status_code == 200, r.text


def test_duplicate_copies_every_page_into_a_new_personal_workspace(me):
    source = _new_workspace(me, "WC original")
    c = login("wc_ann", "wc-ann-pw")
    c.headers["X-Gamma-Workspace"] = source
    paper = _pdf_page(c, source, "WC dup paper", "a duplicated paper")
    inside = make_page(c, "WC dup inside", {"parent_page": paper["id"]})
    trashed = make_page(c, "WC dup trashed")
    assert c.delete(f"/api/blocks/{trashed['id']}").status_code == 200
    opened = make_page(c, "Opened files", {"opened_files": True})
    read_here = make_page(c, "WC opened", {"parent_page": opened["id"]})

    preview = c.post("/api/workspace-copy/preview", json={}).json()
    assert preview["zotero_pdfs"] == 0
    job = _job(c, {"name": "  WC   duplicate  "})
    assert job["state"] == "done", job["error"]
    made = job["result"]["workspace"]
    assert job["params"]["new"] and job["params"]["all"] and job["result"]["pages_added"] == preview["pages"]
    info = workspaces.get(made)
    assert info["name"] == "WC duplicate" and info["kind"] == "personal"
    assert workspaces.role_of(made, account_of("wc_ann")) == "owner"
    assert _props(made, paper["id"]) is not None and _props(made, inside["id"]) is not None
    for left in (trashed, opened, read_here):  # Recently deleted, and Opened files with what is inside it
        assert _props(made, left["id"]) is None
    assert made in {w["id"] for w in me.get("/api/workspaces/mine").json()["workspaces"]}


def test_a_duplicate_that_fails_takes_its_new_workspace_back_out_without_a_final_copy(me, monkeypatch):
    make_page(me, "WC before a failure")
    before = {w["id"] for w in me.get("/api/workspaces/mine").json()["workspaces"]}
    kept = set(os.listdir(ws_backup.deleted_dir())) if ws_backup.deleted_dir().exists() else set()

    def broken(*args, **kwargs):
        raise ws_backup.BackupError("the merge broke")

    monkeypatch.setattr(ws_backup, "restore_zip", broken)
    job = _job(me, {"name": "WC never"})
    assert job["state"] == "failed" and "the merge broke" in job["error"]
    assert {w["id"] for w in me.get("/api/workspaces/mine").json()["workspaces"]} == before
    assert (set(os.listdir(ws_backup.deleted_dir())) if ws_backup.deleted_dir().exists() else set()) == kept


def test_a_copy_past_the_targets_quota_writes_nothing(me, boss):
    import pymupdf

    lab = boss.post("/api/workspaces", json={"name": "WC small lab", "kind": "shared", "owner": "wc_ann",
                                             "quota_mb": 1}).json()["id"]
    doc = pymupdf.open()
    doc.new_page()
    doc.embfile_add("blob", os.urandom(2 << 20))  # incompressible: the file takes 2 MB
    doc_id, _ = storage.store_pdf(workspace_of("wc_ann"), doc.tobytes())
    big = make_page(me, "WC big", doc_props(doc_id))
    job = _job(me, {"pages": [big["id"]], "to": lab})
    assert job["state"] == "failed" and "storage quota exceeded" in job["error"]
    assert _props(lab, big["id"]) is None


def test_what_a_copy_refuses(me, boss, guest):
    source = workspace_of("wc_ann")
    page = make_page(me, "WC refused")
    other = _new_workspace(me, "WC other")
    viewing = boss.post("/api/workspaces", json={"name": "WC read only", "kind": "shared"}).json()["id"]
    assert boss.put(f"/api/workspaces/{viewing}/members/wc_ann", json={"role": "viewer"}).status_code == 200
    stranger = make_user("wc_cid", "wc-cid-pw")

    def start(body):
        return me.post("/api/jobs/workspace-copy", json=body).status_code

    assert start({"pages": [page["id"]], "to": source}) == 400            # where it is already
    assert start({"pages": [page["id"]], "to": other, "name": "x"}) == 400  # one target
    assert start({"pages": [page["id"]]}) == 400                          # none
    assert start({"pages": [], "to": other}) == 400
    assert start({"pages": ["wcNoSuchPage"], "to": other}) == 404
    assert start({"pages": [page["id"]], "to": viewing}) == 403          # a viewer merges nothing
    assert start({"ws": stranger, "pages": [page["id"]], "to": other}) == 404  # nobody's pages but your own
    assert me.post("/api/workspace-copy/preview", json={"ws": stranger}).status_code == 404
    assert guest.post("/api/jobs/workspace-copy", json={"name": "WC guest copy"}).status_code == 403
