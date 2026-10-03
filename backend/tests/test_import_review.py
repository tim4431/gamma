"""The shared upload/review/selection/import contract and its isolation
boundary. The import itself is a background job (POST /api/jobs/import)."""
import io
import zipfile

import pytest

from conftest import (account_of, folder_names, label_names, login, make_folder, make_label, make_page, make_user,
                      workspace_of, guest_name)
from test_zotero_import import RDF, _annotated_pdf


def archive(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def review(client, data, source, name="review.zip", **options):
    result = client.post("/api/import/review", data={"source": source, **options},
                        files={"file": (name, data, "application/zip")})
    assert result.status_code == 200, result.text
    return result.json()


def start(client, plan, ids, **kwargs):
    return client.post("/api/jobs/import", json={"review_id": plan["review_id"], "selected": ids}, **kwargs)


def commit(client, plan, ids):
    """Start the import job and wait for it: the finished job, its report in ``result``."""
    from gamma import jobs
    started = start(client, plan, ids)
    assert started.status_code == 200, started.text
    job = started.json()
    assert job["kind"] == "import" and job["params"]["selected"] == len(set(ids))
    jobs.wait(job["id"])
    done = client.get(f"/api/jobs/{job['id']}")
    assert done.status_code == 200, done.text
    return done.json()


def report(job):
    assert job["state"] == "done", job["error"]
    return job["result"]


def rows(ws):
    from gamma.db import connect_pages_db
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT * FROM unified_blocks ORDER BY id").fetchall()


def uploads(ws):
    from gamma.db import ws_uploads_dir
    return {p.name for p in ws_uploads_dir(ws).glob("*")}


def test_zotero_selection_upload_once_and_commit_retry(accounts):
    # Its own account: the session-wide guest also runs test_zotero_import,
    # whose RDF this is, and a page of it imported here would merge there.
    owner = accounts["review-owner"]
    ws = workspace_of("review-owner")
    before, before_files = rows(ws), uploads(ws)
    rdf = RDF.replace("s41586-000-00000-0", "selected-zotero")
    plan = review(owner, archive({"library.rdf": rdf, "files/3/unique.pdf": _annotated_pdf(b"Selected Zotero paper")}), "zotero")
    assert rows(ws) == before and uploads(ws) == before_files
    paper = next(p for p in plan["pages"] if p["kind"] == "pdf")
    assert any(p["missing"] for p in plan["pages"])
    job = commit(owner, plan, paper["selection_ids"])
    data = report(job)
    assert data["items"] == 1 and data["pages_created"] == 1
    assert all(w["title"] != "Proximal Policy Optimization" for w in data["warnings"])
    again = start(owner, plan, paper["selection_ids"])  # a retried request answers the same job
    assert again.status_code == 200 and again.json()["id"] == job["id"]
    assert start(owner, plan, []).status_code == 409


def test_empty_selection_and_bad_ids_do_not_import(guest):
    ws = workspace_of(guest_name())
    before, before_files = rows(ws), uploads(ws)
    plan = review(guest, archive({"one.md": "# Empty selection\nhello"}), "markdown-zip")
    refused = commit(guest, plan, ["not-a-page"])
    assert refused["state"] == "failed" and "selection" in refused["error"]
    plan = review(guest, archive({"one.md": "# Empty selection\nhello"}), "markdown-zip")
    assert report(commit(guest, plan, []))["pages_created"] == 0
    assert rows(ws) == before and uploads(ws) == before_files


def test_markdown_selection_preserves_links_and_only_stores_selected_assets(guest):
    ws = workspace_of(guest_name())
    selected = make_folder(guest, "Selected notes")
    before, before_files = rows(ws), uploads(ws)
    data = archive({"vault/.obsidian/app.json": "{}", "vault/One.md": "# One\n[[Two]]\n![image](one.png)\n![missing](gone.png)",
                    "vault/Two.md": "# Two\n![image](two.png)", "vault/one.png": b"one-picture", "vault/two.png": b"two-picture"})
    plan = review(guest, data, "markdown-zip", folder=selected)
    assert rows(ws) == before and uploads(ws) == before_files
    page = next(p for p in plan["pages"] if p["title"] == "One")
    assert page["missing"] and page["folders"] == [["Selected notes"]]
    data = report(commit(guest, plan, page["selection_ids"]))
    assert data["pages_created"] == 1 and data["assets_stored"] == 1
    assert len(uploads(ws) - before_files) == 1
    assert any("Two" in w["reason"] for w in data["warnings"])
    children = guest.get(f"/api/blocks/{data['pages'][0]['id']}/children").json()["children"]
    assert any("[[Two]]" in child["content"] for child in children)


def test_single_markdown_uses_same_review_contract(guest):
    parent = make_folder(guest, "Parent")
    plan = review(guest, b"---\ntitle: Single reviewed note\nfolder: child\n---\nContent", "markdown-file", name="note.md",
                  folder=parent)
    assert len(plan["pages"]) == 1 and plan["folder"] == parent
    page = plan["pages"][0]
    assert page["title"] == "Single reviewed note" and page["folders"] == [["Parent", "child"]]
    assert "child" not in [p[-1] for p in folder_names(guest).values()]  # the review made nothing
    created = report(commit(guest, plan, page["selection_ids"]))
    assert created["pages_created"] == 1
    filed = guest.get(f"/api/blocks/{created['pages'][0]['id']}").json()["properties"]["folders"]
    assert [folder_names(guest)[f] for f in filed] == [["Parent", "child"]]


def test_a_review_refuses_a_destination_that_is_no_folder(guest):
    result = guest.post("/api/import/review", data={"source": "markdown-zip", "folder": "no-such-folder"},
                        files={"file": ("n.zip", archive({"n.md": "# n"}), "application/zip")})
    assert result.status_code == 400


def test_markdown_repeat_review_shows_existing_destination(accounts):
    owner = accounts["review-owner"]
    content = b"# Repeat review note\nKeep the existing destination."
    first = review(owner, content, "markdown-file", name="repeat.md", folder=make_folder(owner, "Original folder"))
    created = report(commit(owner, first, first["pages"][0]["selection_ids"]))["pages"][0]
    result = owner.put(f"/api/blocks/{created['id']}", json={"content": "Renamed in library"})
    assert result.status_code == 200
    repeated = review(owner, content, "markdown-file", name="repeat.md", folder=make_folder(owner, "Different folder"))
    page = repeated["pages"][0]
    assert page["title"] == "Renamed in library" and page["folders"] == [["Original folder"]]
    assert page["action"] == "skip" and repeated["pages_created"] == 0
    imported = report(commit(owner, repeated, page["selection_ids"]))
    assert imported["pages_created"] == 0 and imported["pages_skipped"] == 1
    assert imported["pages"][0]["id"] == created["id"]


@pytest.fixture(scope="module")
def accounts(client):
    for name in ("review-owner", "review-other", "review-donor"):
        make_user(name, "test-password")
    return {name: login(name, "test-password") for name in ("review-owner", "review-other", "review-donor")}


def test_staged_upload_is_bound_to_user_and_workspace_and_cancelled(accounts):
    owner, other = accounts["review-owner"], accounts["review-other"]
    plan = review(owner, archive({"one.md": "# Private staged note"}), "markdown-zip")
    ids = plan["pages"][0]["selection_ids"]
    assert start(other, plan, ids).status_code == 404
    assert other.delete(f"/api/import/review/{plan['review_id']}").status_code == 404
    other_ws = owner.post("/api/workspaces", json={"name": "Another review destination"}).json()["id"]
    assert start(owner, plan, ids, headers={"X-Gamma-Workspace": other_ws}).status_code == 404
    from gamma import import_staging
    path, _ = import_staging.get(plan["review_id"], account_of("review-owner"), workspace_of("review-owner"))
    assert path.exists()
    with import_staging.claim(plan["review_id"], account_of("review-owner"), workspace_of("review-owner")):
        # a delete while the import holds the upload waits for it
        assert owner.delete(f"/api/import/review/{plan['review_id']}").status_code == 409
    assert owner.delete(f"/api/import/review/{plan['review_id']}").status_code == 200
    assert not path.exists()
    assert start(owner, plan, ids).status_code == 404


def test_gamma_selection_keeps_page_dependencies_and_excludes_other_pages(accounts):
    donor, receiver = accounts["review-donor"], accounts["review-owner"]
    a_pdf = donor.post("/api/uploads", files={"file": ("a.pdf", _annotated_pdf(b"Selected Gamma PDF"), "application/pdf")}).json()
    b_pdf = donor.post("/api/uploads", files={"file": ("b.pdf", _annotated_pdf(b"Unselected Gamma PDF"), "application/pdf")}).json()
    a = make_page(donor, "Chosen Gamma page", {"doc_id": a_pdf["doc_id"], "source_url": a_pdf["source_url"],
                                               "folders": [make_folder(donor, "Research/Chosen")],
                                               "labels": [make_label(donor, "chosen-tag")]})
    b = make_page(donor, "Excluded Gamma page", {"doc_id": b_pdf["doc_id"], "source_url": b_pdf["source_url"],
                                                 "folders": [make_folder(donor, "Research/Other")],
                                                 "labels": [make_label(donor, "other-tag")]})
    make_folder(donor, "Research/Empty")
    child = donor.post("/api/blocks", json={"parent_id": a["id"], "content": f"Chosen child note [[{b['id']}]]"}).json()
    donor.put(f"/api/chats/{a['id']}", json={"messages": [{"role": "user", "content": "chosen chat"}]})
    donor.put(f"/api/chats/{b['id']}", json={"messages": [{"role": "user", "content": "excluded chat"}]})
    ws = workspace_of("review-owner")
    before, before_files = rows(ws), uploads(ws)
    plan = review(receiver, donor.get("/api/export").content, "gamma")
    assert rows(ws) == before and uploads(ws) == before_files
    chosen = next(p for p in plan["pages"] if p["title"] == "Chosen Gamma page")
    assert chosen["folders"] == [["Research", "Chosen"]]
    data = report(commit(receiver, plan, chosen["selection_ids"]))
    # only the folders and labels the chosen page needs come along
    research = sorted(p for p in folder_names(receiver).values() if p[0] == "Research")
    assert research == [["Research"], ["Research", "Chosen"]]
    assert "chosen-tag" in label_names(receiver).values() and "other-tag" not in label_names(receiver).values()
    filed = receiver.get(f"/api/blocks/{a['id']}").json()["properties"]["folders"]
    assert [folder_names(receiver)[f] for f in filed] == [["Research", "Chosen"]]
    assert data["pages_added"] == 1 and data["chats_added"] == 1
    assert any("unselected" in w["reason"] for w in data["warnings"])
    assert receiver.get(f"/api/blocks/{child['id']}").status_code == 200
    assert receiver.get(f"/api/blocks/{b['id']}").status_code == 404
    assert f"{a_pdf['doc_id']}.pdf" in uploads(ws) and f"{b_pdf['doc_id']}.pdf" not in uploads(ws)
    assert receiver.put(f"/api/blocks/{a['id']}", json={"content": "Renamed Gamma page"}).status_code == 200
    repeated = review(receiver, donor.get("/api/export").content, "gamma")
    existing = next(p for p in repeated["pages"] if p["selection_ids"] == chosen["selection_ids"])
    assert existing["title"] == "Renamed Gamma page" and existing["action"] == "skip"


def test_guest_cannot_use_review_to_bypass_backup_restriction(guest, accounts):
    data = accounts["review-donor"].get("/api/export").content
    result = guest.post("/api/import/review", data={"source": "gamma"}, files={"file": ("backup.zip", data)})
    assert result.status_code == 403


def test_import_job_reports_progress_and_can_be_stopped(accounts, monkeypatch):
    """A job stops at its next item and keeps what it imported; the staged
    upload goes once the job is over."""
    import threading
    from gamma import import_staging, jobs
    from gamma.routers import imports

    owner = accounts["review-owner"]
    plan = review(owner, archive({f"note{i}.md": f"# Stoppable note {i}\ntext" for i in range(3)}), "markdown-zip")
    ids = [i for p in plan["pages"] for i in p["selection_ids"]]
    reached, release = threading.Event(), threading.Event()
    real = imports.import_markdown_zip

    def gated(*args, progress=None, **kwargs):
        def report(**fields):
            if fields.get("done") == 1:  # hold the job after the first note
                reached.set()
                release.wait(10)
            progress(**fields)
        return real(*args, progress=report, **kwargs)

    monkeypatch.setattr(imports, "import_markdown_zip", gated)
    job = start(owner, plan, ids).json()
    assert reached.wait(10)
    live = owner.get(f"/api/jobs/{job['id']}").json()
    assert live["state"] == "running" and live["progress"]["unit"] == "items" and live["progress"]["total"] == 3
    assert owner.post(f"/api/jobs/{job['id']}/cancel").json()["stopping"] is True
    release.set()
    done = jobs.wait(job["id"])
    assert done["state"] == "cancelled" and done["result"] is None
    titles = {b["content"] for b in owner.get("/api/blocks/root/children").json()["children"]}
    assert "Stoppable note 0" in titles and "Stoppable note 2" not in titles
    with pytest.raises(Exception):
        import_staging.get(plan["review_id"], account_of("review-owner"), workspace_of("review-owner"))
