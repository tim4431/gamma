"""The Import dialog's one card for both note apps (source ``note-apps``,
gamma/note_app_import.py ``import_note_apps``): Notability notes and
GoodNotes notebooks found in one walk of one upload and read by their own
app's import, in one review; each app's re-import key; the apps the
import job's params name for the setup guide; and what is refused
(docs/dev/import_export.md "The Import dialog")."""

import io
import json
import zipfile

import pytest

import goodnotes_fixtures as gx
import notability_fixtures as fx
from conftest import login, make_user, workspace_of
from gamma import jobs, note_app_import as app, notability as nb
from gamma.db import connect_pages_db

LINE = [(10.0, 20.0), ((15.0, 20.0), (20.0, 20.0)), ((25.0, 20.0), (30.0, 25.0))]


@pytest.fixture(scope="module")
def accounts(client):
    names = ("nai_owner", "nai_guide")
    for name in names:
        make_user(name, "nai-password")
    return {name: login(name, "nai-password") for name in names}


def send(client, data: bytes, name: str = "notes.zip", *, selected=None, preview=False):
    fields = {"folder": ""}
    if selected is not None:
        fields["selected"] = json.dumps(selected)
    return client.post("/api/import/note-apps/preview" if preview else "/api/import/note-apps",
                       files={"file": (name, data, "application/zip")}, data=fields)


def imported(client, data: bytes, name: str = "notes.zip", **kwargs) -> dict:
    r = send(client, data, name, **kwargs)
    assert r.status_code == 200, r.text
    return r.json()


def notebook(title: str, seed: int) -> bytes:
    n = gx.Notebook(title, seed=seed)
    n.page(n.paper("grid"), [gx.ink("A", gx.flat([LINE]))])
    return n.bytes()


def mixed(tag: str) -> bytes:
    """A folder's export holding a Notability note, a GoodNotes notebook
    and a file of neither."""
    return fx.zip_of({"Export/Notes/Lecture.note": fx.sample_note(),
                      "Export/Books/Cells.goodnotes": notebook(f"Cells {tag}", seed=len(tag)),
                      "Export/readme.txt": b"hello"})


def page_rows(ws, key: str) -> list:
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root' "
                            f"AND json_extract(properties, '$.{key}') IS NOT NULL").fetchall()


def test_one_review_lists_both_apps_notes(accounts):
    owner = accounts["nai_owner"]
    plan = imported(owner, mixed("preview"), preview=True)
    assert {(e["path"], e["status"]) for e in plan["entries"]} == {
        ("Export/Notes/Lecture.note", "source"), ("Export/Books/Cells.goodnotes", "source"),
        ("Export/readme.txt", "not_imported")}
    pages = {p["source_path"]: p for p in plan["pages"]}
    assert pages["Export/Notes/Lecture.note"]["title"] == "E2E Notability note"
    assert pages["Export/Books/Cells.goodnotes"]["title"] == "Cells preview"
    assert pages["Export/Notes/Lecture.note"]["folders"] == [["Notes"]]
    assert pages["Export/Books/Cells.goodnotes"]["folders"] == [["Books"]]
    assert "stage" not in plan  # a plain preview keeps no review


def test_the_import_writes_each_with_its_own_key_and_skips_them_again(accounts):
    owner, ws = accounts["nai_owner"], workspace_of("nai_owner")
    upload = mixed("import")
    result = imported(owner, upload)
    assert result["pages_created"] == 2 and result["strokes_imported"] > 0
    assert len(page_rows(ws, "notability_import")) == 1 and len(page_rows(ws, "goodnotes_import")) == 1
    again = imported(owner, upload)
    assert again["pages_created"] == 0 and again["pages_skipped"] == 2
    assert {p["action"] for p in again["pages"]} == {"skip"}


def test_the_review_selects_across_apps_and_names_them_for_the_guide(accounts):
    guide = accounts["nai_guide"]
    r = guide.post("/api/import/review", data={"source": "note-apps"},
                   files={"file": ("notes.zip", mixed("guide"), "application/zip")})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert "stage" not in plan
    book = next(p for p in plan["pages"] if p["source_path"].endswith(".goodnotes"))
    started = guide.post("/api/jobs/import", json={"review_id": plan["review_id"], "selected": book["selection_ids"]})
    assert started.status_code == 200, started.text
    assert started.json()["params"]["apps"] == ["goodnotes"]
    jobs.wait(started.json()["id"])
    job = guide.get(f"/api/jobs/{started.json()['id']}").json()
    assert job["state"] == "done", job.get("error")
    assert job["result"]["pages_created"] == 1
    assert [p["title"] for p in job["result"]["pages"]] == ["Cells guide"]


def test_a_single_note_of_either_app_imports_alone(accounts):
    owner = accounts["nai_owner"]
    note = imported(owner, fx.sample_note(), "Lecture.note", preview=True)
    assert [p["title"] for p in note["pages"]] == ["E2E Notability note"]
    book = imported(owner, notebook("Alone", seed=9), "Alone.goodnotes", preview=True)
    assert [p["title"] for p in book["pages"]] == ["Alone"]


def test_a_note_of_one_app_inside_the_other_s_unzipped_note_is_its_file(accounts):
    owner = accounts["nai_owner"]
    # a .goodnotes among a Notability note's files, the note the upload itself
    note = fx.note_zip("Lecture", session_bytes=fx.session(), files={"Attachments/Book.goodnotes": notebook("In", 5)})
    plan = imported(owner, note, "Lecture.note", preview=True)
    assert [p["source_path"] for p in plan["pages"]] == ["Lecture.note"]
    # a .note among an unzipped GoodNotes notebook's files
    with zipfile.ZipFile(io.BytesIO(notebook("Unzipped", 6))) as zf:
        files = {f"Book/{name}": zf.read(name) for name in zf.namelist() if not name.endswith("/")}
    plan = imported(owner, fx.zip_of({**files, "Book/extra.note": fx.sample_note()}), preview=True)
    assert [p["source_path"] for p in plan["pages"]] == ["Book"]


def test_an_upload_of_neither_app_and_a_supernote_are_refused(accounts):
    owner = accounts["nai_owner"]
    r = send(owner, fx.zip_of({"readme.txt": b"hello"}))
    assert r.status_code == 400 and r.json()["detail"] == app.NO_NOTES_REASON
    r = send(owner, nb.SUPERNOTE_MAGIC + b"20230015", "Board.note")
    assert r.status_code == 400 and r.json()["detail"] == nb.SUPERNOTE_REASON
    r = send(owner, mixed("unknown"), selected=["Export/readme.txt"])
    assert r.status_code == 400
