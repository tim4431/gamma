"""The shared upload/review/selection/import contract and its isolation
boundary. The import itself is a background job (POST /api/jobs/import)."""
import io
import os
import time
import unicodedata
import zipfile

import pytest
from fastapi import HTTPException

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
    # the review's columns answer each other: the page names the listed file it comes from
    assert paper["source_paths"] == ["files/3/unique.pdf"]
    assert "files/3/unique.pdf" in {e["path"] for e in plan["entries"]}
    job = commit(owner, plan, paper["selection_ids"])
    data = report(job)
    assert data["items"] == 1 and data["pages_created"] == 1
    assert data["pages"][0]["source_paths"] == ["files/3/unique.pdf"]  # and so does the summary's
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


def test_markdown_pages_name_the_listed_member_they_come_from(guest):
    """A page's ``source_paths`` are paths the source column lists: a note
    inside a zip inside the upload names that zip, however deep, and a
    name stored decomposed (as macOS writes it) is listed as the walk
    reads it."""
    composed = unicodedata.normalize("NFC", "notes/Café notes.md")
    decomposed = unicodedata.normalize("NFD", composed)
    part = archive({"inner.md": "# Inner reviewed note\nhello",
                    "deeper.zip": archive({"deep.md": "# Deep reviewed note\nhello"})})
    plan = review(guest, archive({"notes/Part-1.zip": part, decomposed: "# Accented reviewed note\nhello"}), "markdown-zip")
    listed = {e["path"] for e in plan["entries"]}
    assert composed in listed and decomposed not in listed
    sources = {p["title"]: p["source_paths"] for p in plan["pages"]}
    assert sources == {"Inner reviewed note": ["notes/Part-1.zip"], "Deep reviewed note": ["notes/Part-1.zip"],
                       "Accented reviewed note": [composed]}
    data = report(commit(guest, plan, [i for p in plan["pages"] for i in p["selection_ids"]]))
    assert {p["title"]: p["source_paths"] for p in data["pages"]} == sources


def test_archive_entries_names_members_as_the_walk_reads_them():
    """The source column's names are the walk's: a UTF-8 name stored
    without the UTF-8 flag (zipfile reads it as cp437) is decoded, and a
    leading slash or a backslash gives the walk's path, a backslash-ended
    directory listed as one."""
    from gamma.import_review import archive_entries
    with zipfile.ZipFile(io.BytesIO(archive({"a": "x", "b": "y", "c": "z"}))) as zf:
        flagless, slashed, folder = zf.infolist()
        flagless.filename = "Café.md".encode("utf-8").decode("cp437")
        flagless.flag_bits &= ~0x800
        slashed.filename = "/notes\\lead.md"
        folder.filename = "notes\\"
        assert [(e["path"], e["directory"]) for e in archive_entries(zf)] == [
            ("Café.md", False), ("notes/lead.md", False), ("notes/", True)]


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


def test_an_open_review_is_kept_and_a_swept_one_is_gone(accounts):
    """The dialog's keep-alive and the import's start restart a review's two
    hours, and bring back one past them that no upload has swept yet; once
    swept, both answer 404."""
    from gamma import import_staging
    owner, other = accounts["review-owner"], accounts["review-other"]
    me, ws = account_of("review-owner"), workspace_of("review-owner")

    def staged(title):
        plan = review(owner, archive({"one.md": f"# {title}\ntext"}), "markdown-zip")
        return plan, plan["pages"][0]["selection_ids"], import_staging.get(plan["review_id"], me, ws)[0]

    def age(path, seconds):
        then = time.time() - seconds
        os.utime(path, (then, then))

    def keep(plan, client=owner):
        return client.post(f"/api/import/review/{plan['review_id']}/keepalive")

    plan, ids, path = staged("Kept review")
    age(path, import_staging.TTL - 60)
    assert keep(plan).json() == {"ok": True}
    assert time.time() - path.stat().st_mtime < 60
    assert keep(plan, other).status_code == 404  # not the other account's

    age(path, import_staging.TTL + 5)  # past its TTL, not swept yet
    with pytest.raises(HTTPException) as gone:
        import_staging.get(plan["review_id"], me, ws)
    assert gone.value.status_code == 410
    assert keep(plan).status_code == 200
    assert report(commit(owner, plan, ids))["pages_created"] == 1

    plan, ids, path = staged("Review imported after its TTL")
    age(path, import_staging.TTL + 5)
    assert report(commit(owner, plan, ids))["pages_created"] == 1  # the start alone brings it back

    plan, ids, path = staged("Swept review")
    age(path, import_staging.TTL + 5)
    owner.delete(f"/api/import/review/{staged('Next upload')[0]['review_id']}")  # its upload sweeps
    assert not path.exists()
    assert keep(plan).status_code == 404 and start(owner, plan, ids).status_code == 404

    data = archive({"one.md": "# Still arriving\ntext"})
    token = owner.post("/api/import/review/parts", json={"source": "markdown-zip", "size": len(data)}).json()["token"]
    assert owner.post(f"/api/import/review/{token}/keepalive").status_code == 409
    owner.delete(f"/api/import/review/{token}")


def test_update_adds_to_a_staged_reviews_metadata(accounts):
    """``import_staging.update`` merges into the review's metadata, under
    its claim: the account's own review only, and not while its import
    holds it."""
    from gamma import import_staging
    owner = accounts["review-owner"]
    me, ws = account_of("review-owner"), workspace_of("review-owner")
    plan = review(owner, archive({"one.md": "# Updated review\ntext"}), "markdown-zip")
    token = plan["review_id"]
    _, before = import_staging.get(token, me, ws)
    import_staging.update(token, me, ws, learned={"one.md": [1, 2, "digest"]})
    import_staging.update(token, me, ws, also=True)
    _, after = import_staging.get(token, me, ws)
    assert after == {**before, "learned": {"one.md": [1, 2, "digest"]}, "also": True}
    with pytest.raises(HTTPException) as other:
        import_staging.update(token, account_of("review-other"), ws, learned={})
    assert other.value.status_code == 404
    with import_staging.claim(token, me, ws):
        with pytest.raises(HTTPException) as running:
            import_staging.update(token, me, ws, learned={})
    assert running.value.status_code == 409
    owner.delete(f"/api/import/review/{token}")


def test_a_previews_stage_reaches_its_commit_and_no_answer(accounts, monkeypatch):
    """What a preview hands on in ``stage`` stays with the staged review,
    for its commit's metadata, and leaves the review's answer; a plain
    preview endpoint, with no review to keep it for, drops it."""
    from gamma import import_staging
    from gamma.routers import imports

    owner = accounts["review-owner"]
    me, ws = account_of("review-owner"), workspace_of("review-owner")
    stage = {"learned": {"one.md": [12, 34, "digest"]}}
    preview, commit_markdown, committed = imports._preview_markdown_zip, imports._commit_markdown_zip, []

    def committing(ws, actor, data, meta, *rest):
        committed.append(meta)
        return commit_markdown(ws, actor, data, meta, *rest)

    monkeypatch.setattr(imports, "_preview_markdown_zip", lambda *args: {**preview(*args), "stage": stage})
    monkeypatch.setattr(imports, "_commit_markdown_zip", committing)
    plan = review(owner, archive({"one.md": "# Staged preview\ntext"}), "markdown-zip")
    assert "stage" not in plan
    _, metadata = import_staging.get(plan["review_id"], me, ws)
    assert metadata["learned"] == stage["learned"] and metadata["source"] == "markdown-zip"
    assert report(commit(owner, plan, plan["pages"][0]["selection_ids"]))["pages_created"] == 1
    assert [meta["learned"] for meta in committed] == [stage["learned"]]

    monkeypatch.setattr(imports, "_preview_zotero", lambda ws, data, meta: {"ok": True, "pages": [], "stage": stage})
    plain = owner.post("/api/import/zotero/preview", files={"file": ("lib.zip", archive({"a.rdf": ""}))})
    assert plain.status_code == 200 and plain.json() == {"ok": True, "pages": []}


def test_a_zotero_reviews_commit_hashes_no_pdf_its_preview_hashed(accounts, monkeypatch):
    """A Zotero review's preview hashes each PDF once and keeps the digests
    with the staged review (its ``stage``, ``zotero_digests``); the commit
    files the PDF under its digest without reading it for one again."""
    from gamma import import_staging, zotero_import
    from gamma.storage import content_digest

    owner = accounts["review-owner"]
    me, ws = account_of("review-owner"), workspace_of("review-owner")
    hashed, real = [], zotero_import._pdf_digest
    monkeypatch.setattr(zotero_import, "_pdf_digest", lambda zf, name: hashed.append(name) or real(zf, name))
    pdf, entry = _annotated_pdf(b"Hashed once for its review"), "files/3/Vaswani - 2017 - Attention.pdf"
    rdf = RDF.replace("s41586-000-00000-0", "hashed-once").replace("Attention is all you need", "Hashed once")
    plan = review(owner, archive({"library.rdf": rdf, entry: pdf}), "zotero")
    assert hashed == [entry] and "stage" not in plan
    _, metadata = import_staging.get(plan["review_id"], me, ws)
    assert metadata["zotero_digests"][entry][2] == content_digest(pdf)
    paper = next(p for p in plan["pages"] if p["kind"] == "pdf")
    data = report(commit(owner, plan, paper["selection_ids"]))
    assert data["pages_created"] == 1 and hashed == [entry]
    assert owner.get(f"/api/blocks/{data['pages'][0]['id']}").json()["properties"]["doc_id"] == content_digest(pdf)


def test_upload_in_parts_reviews_and_imports_like_one_request(accounts):
    """Past what a proxy lets one request carry, the upload comes in parts;
    finishing answers the same preview, and the job imports from it."""
    owner, other = accounts["review-owner"], accounts["review-other"]
    data = archive({f"part{i}.md": f"# Note sent in parts {i}\n" + "text " * 400 for i in range(3)})
    whole = review(owner, data, "markdown-zip")
    owner.delete(f"/api/import/review/{whole['review_id']}")

    opened = owner.post("/api/import/review/parts", json={"source": "markdown-zip", "size": len(data), "name": "notes.zip"})
    assert opened.status_code == 200, opened.text
    token = opened.json()["token"]

    def send(offset, chunk, client=owner):
        return client.post(f"/api/import/review/parts/{token}", data={"offset": str(offset)},
                           files={"part": ("part", chunk, "application/octet-stream")})

    third = len(data) // 3
    assert send(0, data[:third]).json() == {"received": third}
    # a part whose reply was lost, sent again: told where the upload stands
    resent = send(0, data[:third])
    assert resent.status_code == 409 and resent.json()["received"] == third
    assert send(third, data[third:]).json() == {"received": len(data)}
    assert send(len(data), b"x").status_code == 413  # past the announced size
    assert send(third, data[third:], client=other).status_code == 404

    plan = owner.post(f"/api/import/review/parts/{token}/finish").json()
    assert plan["review_id"] == token
    assert [p["title"] for p in plan["pages"]] == [p["title"] for p in whole["pages"]]
    ids = [i for p in plan["pages"] for i in p["selection_ids"]]
    assert report(commit(owner, plan, ids))["pages_created"] == 3


def test_upload_in_parts_is_imported_only_once_whole(accounts):
    owner = accounts["review-owner"]
    data = archive({"one.md": "# Half sent\ntext"})
    token = owner.post("/api/import/review/parts", json={"source": "markdown-zip", "size": len(data)}).json()["token"]
    owner.post(f"/api/import/review/parts/{token}", data={"offset": "0"}, files={"part": ("part", data[:10])})
    assert owner.post("/api/jobs/import", json={"review_id": token, "selected": []}).status_code == 409
    missing = owner.post(f"/api/import/review/parts/{token}/finish")
    assert missing.status_code == 400 and "10 of" in missing.json()["detail"]
    assert owner.delete(f"/api/import/review/{token}").status_code == 200
    assert owner.post(f"/api/import/review/parts/{token}/finish").status_code == 404
    assert owner.post("/api/import/review/parts", json={"source": "nope", "size": 10}).status_code == 400
    assert owner.post("/api/import/review/parts", json={"source": "zotero", "size": 0}).status_code == 400


def test_staging_refuses_an_upload_the_disk_has_no_room_for(accounts, monkeypatch):
    """No size cap: only the disk's free space bounds a staged upload."""
    from gamma import import_staging
    owner = accounts["review-owner"]
    data = archive({"one.md": "# Room\ntext"})
    monkeypatch.setattr(import_staging, "MIN_FREE_BYTES", 1 << 62)
    full = owner.post("/api/import/review", data={"source": "markdown-zip"}, files={"file": ("a.zip", data)})
    assert full.status_code == 507
    assert owner.post("/api/import/review/parts", json={"source": "markdown-zip", "size": len(data)}).status_code == 507
    monkeypatch.setattr(import_staging, "MIN_FREE_BYTES", 0)
    owner.delete(f"/api/import/review/{review(owner, data, 'markdown-zip')['review_id']}")


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
