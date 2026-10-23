"""The Notability import through its endpoints and its review job: what a
note becomes (a PDF page, a composed PDF, a notebook, a plain note), its
files in storage, filing and re-import, the refusals that keep an untrusted
upload bounded, and a stop that keeps what was written
(docs/dev/import_export.md "Notability notes")."""

import io
import json
import math
import tempfile
import threading
import zipfile

import pytest
from fastapi import HTTPException

import notability_fixtures as fx
from conftest import account_of, folder_names, login, make_folder, make_user, workspace_of
from gamma import import_zip, jobs, notability as nb, notability_import as ni, storage
from gamma.blocks_store import folder_paths, pages_in_folder
from gamma.db import connect_pages_db, ws_uploads_dir

PDF = "5F0C2C77-1D3B-4E7B-9A55-2B4B0E1C0D01.pdf"
WORKED = [(10, 20), (12, 20), (14, 22), (16, 22)]


@pytest.fixture(scope="module")
def accounts(client):
    names = ("nbi_owner", "nbi_other", "nbi_filing", "nbi_quota")
    for name in names:
        make_user(name, "nbi-password")
    return {name: login(name, "nbi-password") for name in names}


def send(client, data: bytes, name: str = "notes.zip", *, folder: str = "", selected=None, preview=False):
    fields = {"folder": folder}
    if selected is not None:
        fields["selected"] = json.dumps(selected)
    return client.post("/api/import/notability/preview" if preview else "/api/import/notability",
                       files={"file": (name, data, "application/zip")}, data=fields)


def imported(client, data: bytes, name: str = "notes.zip", **kwargs) -> dict:
    r = send(client, data, name, **kwargs)
    assert r.status_code == 200, r.text
    return r.json()


def subtree(client, page_id: str) -> dict:
    return client.get(f"/api/blocks/{page_id}/subtree").json()["block"]


def rows(ws):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT * FROM unified_blocks ORDER BY id").fetchall()


def uploads(ws):
    return {p.name for p in ws_uploads_dir(ws).glob("*")}


def stored(ws, url_or_name: str) -> bytes:
    return (ws_uploads_dir(ws) / url_or_name.rsplit("/", 1)[-1]).read_bytes()


def ink_of(ws, block: dict) -> dict:
    return json.loads(stored(ws, block["properties"]["ink_url"]))


def note(name: str, *, files=None, subject="unsortedNotesKey", **session) -> bytes:
    """A .note named ``name`` (its Session.plist differs by name, so each
    is a note of its own)."""
    return fx.note_zip(name, session_bytes=fx.session(name=name, **session), meta=fx.metadata(name, subject),
                       files=files)


def reasons(report: dict) -> list[str]:
    return [w["reason"] for w in report["warnings"]] + [s["reason"] for s in report["skipped"]]


# --- preview and review ---------------------------------------------------------------------

def test_a_preview_writes_nothing_and_lists_each_note(accounts):
    owner, ws = accounts["nbi_owner"], workspace_of("nbi_owner")
    before, files = rows(ws), uploads(ws)
    upload = fx.zip_of({"Export/Biology/Cells.note": note("Preview cells", curves=[fx.curve(WORKED)]),
                        "Export/Physics/Waves.note": note("Preview waves", layout=[(PDF, 1)], pdfs={PDF: 2},
                                                          files={f"PDFs/{PDF}": fx.pdf()}),
                        "Export/readme.txt": b"hello"})
    plan = imported(owner, upload, preview=True)
    assert rows(ws) == before and uploads(ws) == files
    pages = {p["title"]: p for p in plan["pages"]}
    assert pages["Preview cells"]["kind"] == "page" and pages["Preview waves"]["kind"] == "pdf"
    assert pages["Preview cells"]["folders"] == [["Biology"]] and pages["Preview waves"]["folders"] == [["Physics"]]
    assert all(p["action"] == "create" and not p["created"] for p in plan["pages"])
    assert pages["Preview cells"]["selection_ids"] == ["Export/Biology/Cells.note"]
    assert {(e["path"], e["status"]) for e in plan["entries"]} == {
        ("Export/Biology/Cells.note", "source"), ("Export/Physics/Waves.note", "source"),
        ("Export/readme.txt", "not_imported")}
    assert plan["pages_created"] == 0 and plan["ok"] is True


def test_the_review_imports_the_selected_note(accounts):
    owner = accounts["nbi_owner"]
    upload = fx.zip_of({"A.note": note("Reviewed one", curves=[fx.curve(WORKED)]),
                        "B.note": note("Reviewed two", curves=[fx.curve(WORKED)])})
    r = owner.post("/api/import/review", data={"source": "notability"},
                   files={"file": ("pair.zip", upload, "application/zip")})
    assert r.status_code == 200, r.text
    plan = r.json()
    chosen = next(p for p in plan["pages"] if p["title"] == "Reviewed two")
    started = owner.post("/api/jobs/import", json={"review_id": plan["review_id"], "selected": chosen["selection_ids"]})
    assert started.status_code == 200, started.text
    jobs.wait(started.json()["id"])
    job = owner.get(f"/api/jobs/{started.json()['id']}").json()
    assert job["state"] == "done", job.get("error")
    result = job["result"]
    assert result["pages_created"] == 1 and [p["title"] for p in result["pages"]] == ["Reviewed two"]
    assert result["strokes_imported"] == 1


# --- what a note becomes -------------------------------------------------------------------

def test_a_note_on_its_own_pdf_keeps_the_pdf(accounts):
    owner, ws = accounts["nbi_owner"], workspace_of("nbi_owner")
    pdf = fx.pdf(((612, 792), (612, 792)))
    picture = fx.png(60, 40)
    data = note("Original PDF note", layout=[(PDF, 1), (PDF, 2)], pdfs={PDF: 2},
                curves=[fx.curve(WORKED, fractional=[1, 0.5]), fx.curve([(100, 800), (120, 820)], polyline=True)],
                media=[{"kind": "text", "origin": (40, 760), "size": (200, 30), "runs": [{"text": "Second page"}]},
                       {"kind": "picture", "origin": (300, 100), "size": (60, 40), "path": "Images/Image .png"}],
                files={f"PDFs/{PDF}": pdf, "Images/Image .png": picture})
    result = imported(owner, data, "Original PDF note.note")
    assert result["pages_created"] == 1 and result["pdfs_stored"] == 1 and result["pictures_imported"] == 1
    page = subtree(owner, result["pages"][0]["id"])
    assert page["properties"]["doc_id"] == storage.content_digest(pdf)
    assert page["properties"]["original_filename"] == "Original PDF note.pdf"
    assert "auto_title" not in page["properties"] and page["content"] == "Original PDF note"
    groups = [c for c in page["children"] if c["properties"].get("ink_url")]
    assert sorted(g["properties"]["pdf_position"]["pageNumber"] for g in groups) == [1, 2]
    for group in groups:
        f = ink_of(ws, group)
        assert f["space"]["kind"] == "pdf-page" and f["space"]["page"] == group["properties"]["pdf_position"]["pageNumber"]
        assert [s["id"] for s in f["strokes"]] == ["n0"]
    boxes = [c for c in page["children"] if c["properties"].get("text_box")]
    text = next(b for b in boxes if b["content"] == "Second page")
    assert text["properties"]["pdf_page"] == 2
    shown = next(b for b in boxes if b["content"].startswith("!["))
    assert shown["properties"]["pdf_page"] == 1 and stored(ws, shown["content"][4:-1]) == picture


def test_a_mixed_note_is_one_composed_pdf():
    from PyPDF2 import PdfReader
    from test_pdf_export import PAGE_H, PAGE_W, _pdf_with_dangling_reference

    source = _pdf_with_dangling_reference()
    owner = login("nbi_other", "nbi-password")
    ws = workspace_of("nbi_other")
    data = note("Mixed note", layout=[None, (PDF, 1), (PDF, 1)], pdfs={PDF: 2},
                paper={"identifier": "Color:#FBF7EC", "lines": "Lines:false:false:0.25"},
                curves=[fx.curve(WORKED), fx.curve([(50, 1700), (60, 1710)], polyline=True)],
                files={f"PDFs/{PDF}": source})
    result = imported(owner, data, "Mixed.note")
    page = subtree(owner, result["pages"][0]["id"])
    composed = PdfReader(io.BytesIO(stored(ws, page["properties"]["doc_id"] + ".pdf")))
    sizes = [(float(p.mediabox.width), float(p.mediabox.height)) for p in composed.pages]
    assert sizes == [(612, 803.25), (PAGE_W, PAGE_H), (PAGE_W, PAGE_H)]  # the page used twice is there twice
    paint = composed.pages[0].get_contents().get_data()
    assert b"1 0 0 -1 0 803.25 cm" in paint and b"0.98 0.97 0.93 rg" in paint and b"0.00 18.00 m" in paint
    groups = [c for c in page["children"] if c["properties"].get("ink_url")]
    assert [g["properties"]["pdf_position"]["pageNumber"] for g in groups] == [1, 3]


def test_a_paper_note_becomes_a_notebook():
    owner = login("nbi_other", "nbi-password")
    ws = workspace_of("nbi_other")
    data = note("Paper note", paper={"identifier": "Color:#FFFFFF", "lines": "Lines:false:false:0.25"},
                curves=[fx.curve(WORKED), fx.curve([(30, 900), (40, 910)], polyline=True)],
                media=[{"kind": "text", "origin": (60, 950), "size": (200, 30), "runs": [{"text": "On page two"}]},
                       {"kind": "sticky", "origin": (300, 100), "size": (80, 80)}],
                flow=[{"text": "Typed on the page"}])
    result = imported(owner, data, "Paper.note")
    page = subtree(owner, result["pages"][0]["id"])
    assert "doc_id" not in page["properties"]
    flow, *sheets = page["children"]
    assert flow["content"] == "Typed on the page" and len(sheets) == 2
    for sheet in sheets:
        props = sheet["properties"]
        assert props["collapsed"] is True and props["sheet"]["pattern"] == "ruled" and props["sheet"]["spacing"] == 18
    one, two = sheets
    first = ink_of(ws, one["children"][0])
    assert first["space"] == {"kind": "canvas", "width": 612, "height": 803.25}
    assert first["strokes"][0]["band"] == 0 and first["strokes"][0]["shape"]["kind"] == "rect"  # the sticky first
    assert "pdf_position" not in one["children"][0]["properties"]
    box = next(c for c in two["children"] if c["properties"].get("text_box"))
    assert box["content"] == "On page two" and "pdf_page" not in box["properties"]
    assert result["strokes_imported"] == 3 and result["text_boxes_imported"] == 1

    typed = imported(owner, note("Only typed", flow=[{"text": "Just words\n"}, {"text": "More words"}]), "T.note")
    children = subtree(owner, typed["pages"][0]["id"])["children"]
    assert [c["content"] for c in children] == ["Just words", "More words"]


def test_the_sample_note_has_what_the_browser_suite_checks(accounts):
    owner, ws = accounts["nbi_quota"], workspace_of("nbi_quota")
    result = imported(owner, fx.sample_note(), "E2E Notability note.note")
    assert result["pages_created"] == 1 and result["recordings_imported"] == 1, result
    page = subtree(owner, result["pages"][0]["id"])
    assert page["properties"].get("doc_id") and page["content"] == "E2E Notability note"
    groups = [c for c in page["children"] if c["properties"].get("ink_url")]
    files = [ink_of(ws, g) for g in groups]
    assert len(groups) == 2 and all(f["version"] == 2 and f["space"]["kind"] == "pdf-page" for f in files)
    first = next(f for f in files if f["space"]["page"] == 1)
    assert any(s.get("tool") == "highlighter" for s in first["strokes"])
    assert any("w" in s["ch"] for s in first["strokes"])
    boxes = [c for c in page["children"] if c["properties"].get("text_box")]
    assert {b["content"] for b in boxes if b["properties"]["pdf_page"] == 1} >= {"Typed **box**"}
    assert any(b["content"].startswith("![](/api/uploads/") for b in boxes)
    recording = next(c for c in page["children"] if c["properties"].get("recording"))
    segment = recording["properties"]["recording"]["segments"][0]
    assert segment["mime"] == "audio/mp4" and segment["dur"] == 3500 and recording["content"] == "Recording 1"
    assert stored(ws, segment["url"])[4:8] == b"ftyp"


def test_recordings_and_their_warnings(accounts):
    owner, ws = accounts["nbi_owner"], workspace_of("nbi_owner")
    files = {"Recordings/library.plist": fx.library([
        {"identifier": 1, "filepath": "Lecture.m4a", "userFacingName": "Lecture"},
        {"identifier": 2, "filepath": "Gone.m4a", "name": "Gone"},
        {"identifier": 3, "filepath": "Clip.mp3", "name": "Clip"}]),
        "Recordings/Lecture.m4a": fx.m4a(4200), "Recordings/Clip.mp3": b"ID3 not really"}
    result = imported(owner, note("Recorded note", files=files, curves=[fx.curve(WORKED)]), "R.note")
    page = subtree(owner, result["pages"][0]["id"])
    (block,) = [c for c in page["children"] if c["properties"].get("recording")]
    rec = block["properties"]["recording"]
    assert block["content"] == "Lecture" and rec["state"] == "done" and rec["dur"] == 4200
    assert rec["segments"][0]["bytes"] == len(fx.m4a(4200)) and rec["segments"][0]["started"] == rec["started"]
    assert stored(ws, rec["segments"][0]["url"]) == fx.m4a(4200)
    assert page["children"][-1]["id"] == block["id"]  # after everything else
    found = reasons(result)
    assert "Missing recording Recordings/Gone.m4a: left out" in found
    assert "Recording Clip is not an .m4a file: left out" in found
    assert {w["title"] for w in result["warnings"]} == {"Recorded note"}
    assert result["pages"][0]["missing"] is True and result["recordings_imported"] == 1


def test_a_turned_and_cropped_picture_is_stored_turned(accounts, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(ni, "MAX_TURNED_SIDE", 100)
    owner, ws = accounts["nbi_owner"], workspace_of("nbi_owner")
    data = note("Turned picture", files={"Images/Image .jpg": fx.jpeg(400, 300)},
                media=[{"kind": "picture", "origin": (100, 100), "size": (145, 100), "rotation": 0.5,
                        "crop": (10, 10, 290, 200), "path": "Images/Image .jpg", "caption": "A |300 [x]"}])
    result = imported(owner, data, "Turned.note")
    sheet = subtree(owner, result["pages"][0]["id"])["children"][0]
    (box,) = sheet["children"]
    assert box["content"].startswith("![A 300 x](/api/uploads/") and box["content"].endswith(".png)")
    picture = Image.open(io.BytesIO(stored(ws, box["content"].split("(")[1][:-1])))
    assert picture.format == "PNG" and picture.mode == "RGBA" and max(picture.size) <= 100
    assert picture.getpixel((0, 0))[3] == 0  # a turned picture's corner is transparent
    w, h = 145, 100
    c, s = math.cos(0.5), math.sin(0.5)
    assert picture.size[0] / picture.size[1] == pytest.approx((w * c + h * s) / (w * s + h * c), rel=0.05)


def test_a_turned_picture_stays_within_its_longest_side(monkeypatch):
    """Pillow's expanded size rounds the turned corners outward, up to 2 px
    past their extent, and the fit leaves room for it: a photo of
    Notability-notes-converter's example note came out 2,049 px long."""
    from PIL import Image
    monkeypatch.setattr(ni, "MAX_TURNED_SIDE", 100)
    photo = fx.jpeg(300, 400)
    for angle in (k / 20 for k in range(1, 60)):  # 0.1, 0.35, 1.15 and more came out 101 px
        media = nb.Media(kind="picture", x=0, y=0, w=30, h=40, rotation=angle, picture="a.jpg")
        assert max(Image.open(io.BytesIO(ni.picture_bytes(photo, media)[0])).size) <= 100, angle


# --- many notes, re-import, filing ------------------------------------------------------------

def test_a_packed_note_is_opened_only_while_it_is_read(monkeypatch):
    make_user("nbi_many", "nbi-password")
    ws = workspace_of("nbi_many")
    monkeypatch.setattr(import_zip, "MAX_NESTED_ZIPS", 1)
    spools = []
    real = tempfile.TemporaryFile

    def tracked(*args, **kwargs):
        assert all(f.closed for f in spools), "a note's temporary file is still open"
        spools.append(real(*args, **kwargs))
        return spools[-1]

    monkeypatch.setattr(import_zip.tempfile, "TemporaryFile", tracked)
    upload = fx.zip_of({f"N{i}.note": note(f"Many {i}", curves=[fx.curve(WORKED)]) for i in range(3)})
    with connect_pages_db(ws) as conn:
        report = ni.import_notes(ws, io.BytesIO(upload), conn, name="many.zip", actor="nbi")
    assert report["pages_created"] == 3 and len(spools) == 3 and all(f.closed for f in spools)


def test_reimport_duplicates_and_the_same_files_in_two_workspaces(accounts):
    owner, other = accounts["nbi_owner"], accounts["nbi_other"]
    data = note("Imported twice", curves=[fx.curve(WORKED), fx.curve([(10, 900)])])
    first = imported(owner, data, "Twice.note")
    again = imported(owner, data, "Twice.note")
    assert first["pages_created"] == 1 and again["pages_created"] == 0 and again["pages_skipped"] == 1
    assert again["pages"][0]["action"] == "skip" and again["pages"][0]["id"] == first["pages"][0]["id"]
    copies = imported(owner, fx.zip_of({"a/One.note": note("Duplicated"), "b/Copy.note": note("Duplicated")}))
    assert copies["pages_created"] == 1 and copies["pages_skipped"] == 1
    assert "The same note as a/One.note" in reasons(copies)

    there = imported(other, data, "Twice.note")
    urls = lambda client, r: sorted(c["properties"]["ink_url"] for sheet in subtree(client, r["pages"][0]["id"])["children"]
                                    for c in sheet.get("children", []) if c["properties"].get("ink_url"))
    assert urls(owner, first) == urls(other, there) and len(urls(owner, first)) == 2


def test_folders_from_the_zip_and_from_the_subject(accounts):
    owner = accounts["nbi_filing"]
    under = make_folder(owner, "Imports")
    upload = fx.zip_of({"Export/Biology/Cells.note": note("Filed cells", curves=[fx.curve(WORKED)]),
                        "Export/Physics/Waves.note": note("Filed waves", curves=[fx.curve(WORKED)])})
    result = imported(owner, upload, folder=under)
    names = folder_names(owner)
    filed = {p["title"]: [names[f] for f in subtree(owner, p["id"])["properties"]["folders"]] for p in result["pages"]}
    assert filed == {"Filed cells": [["Imports", "Biology"]], "Filed waves": [["Imports", "Physics"]]}
    assert {p["title"]: p["folders"] for p in result["pages"]} == {"Filed cells": [["Imports", "Biology"]],
                                                                  "Filed waves": [["Imports", "Physics"]]}
    alone = imported(owner, note("By subject", subject="Chemistry", curves=[fx.curve(WORKED)]), "S.note")
    props = subtree(owner, alone["pages"][0]["id"])["properties"]
    assert [folder_names(owner)[f] for f in props["folders"]] == [["Chemistry"]]


def imported_at_once(monkeypatch, ws: str, uploads: dict) -> dict:
    """``{name: report}`` of the uploads ``{name: .note bytes}`` imported
    into ``ws`` at once, a thread each: every import reads the library and
    stores its files, then waits for the others before it writes
    (``_Import._write``), so none has written when another looks."""
    real = ni._Import._write
    together = threading.Barrier(len(uploads), timeout=30)

    def gated(self, *args, **kwargs):
        together.wait()
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ni._Import, "_write", gated)
    reports, errors = {}, []

    def run(name, data):
        try:
            with connect_pages_db(ws) as conn:
                reports[name] = ni.import_notes(ws, io.BytesIO(data), conn, name=f"{name}.note", actor="nbi")
        except BaseException as exc:  # noqa: BLE001 — failed below, with the others let go
            errors.append(exc)
            together.abort()

    threads = [threading.Thread(target=run, args=upload) for upload in uploads.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not errors, errors
    return reports


def test_two_imports_of_one_note_at_once_leave_one_page_and_no_empty_folder(monkeypatch):
    """The same note in two uploads, each filing it by a subject of its own,
    imported at once: both have looked for it in the library before either
    writes. The one that writes first makes the page and its folder; the
    other finds the page under the write lock and writes nothing, no folder
    either, and names the page where the first filed it."""
    make_user("nbi_race", "nbi-password")
    ws = workspace_of("nbi_race")
    reports = imported_at_once(monkeypatch, ws, {
        side: note("Raced note", subject=f"Raced {side}", curves=[fx.curve(WORKED)]) for side in "AB"})
    won, lost = sorted(reports, key=lambda side: -reports[side]["pages_created"])
    assert (reports[won]["pages_created"], reports[lost]["pages_created"], reports[lost]["pages_skipped"]) == (1, 0, 1)
    page, skipped = reports[won]["pages"][0], reports[lost]["pages"][0]
    with connect_pages_db(ws) as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root'")] == [page["id"]]
        paths = folder_paths(conn)
        assert {folder: pages_in_folder(conn, folder) for folder in paths} == {
            folder: [page["id"]] for folder in paths}, "no folder is empty"
        assert list(paths.values()) == [[f"Raced {won}"]], "the other import made no folder"
    assert skipped["action"] == "skip" and skipped["id"] == page["id"]
    assert skipped["folders"] == page["folders"] == [[f"Raced {won}"]]


def test_two_notes_filed_into_one_new_folder_at_once_make_it_once(monkeypatch):
    """Two notes filed by one new subject, imported at once: the folder is
    looked for under the write lock, so the one that writes second files
    its page in the folder the first made, not in a sibling of its name."""
    make_user("nbi_shared", "nbi-password")
    ws = workspace_of("nbi_shared")
    reports = imported_at_once(monkeypatch, ws, {
        name: note(name, subject="Shared subject", curves=[fx.curve(WORKED)]) for name in ("Shared 1", "Shared 2")})
    assert [r["pages_created"] for r in reports.values()] == [1, 1]
    with connect_pages_db(ws) as conn:
        paths = folder_paths(conn)
        assert list(paths.values()) == [["Shared subject"]], "one folder, not two of one name"
        assert len(pages_in_folder(conn, next(iter(paths)))) == 2


def test_a_note_whose_page_fails_to_write_leaves_no_folder(monkeypatch):
    """A note whose page cannot be written is skipped with the reason, and
    the folder made for it goes with the transaction; the next note is
    written."""
    make_user("nbi_failed", "nbi-password")
    ws = workspace_of("nbi_failed")
    real = ni.insert_page_tree

    def failing(conn, page_id, title, props, tree, actor):
        written = real(conn, page_id, title, props, tree, actor)
        if title == "Failed note":
            raise RuntimeError("the write failed")
        return written

    monkeypatch.setattr(ni, "insert_page_tree", failing)
    upload = fx.zip_of({"Export/Failed/Failed.note": note("Failed note", curves=[fx.curve(WORKED)]),
                        "Export/Kept/Kept.note": note("Kept note", curves=[fx.curve(WORKED)])})
    with connect_pages_db(ws) as conn:
        report = ni.import_notes(ws, io.BytesIO(upload), conn, name="Failed.zip", actor="nbi")
    assert report["pages_created"] == 1
    assert report["skipped"] == [{"title": "Failed note", "reason": "the write failed"}]
    with connect_pages_db(ws) as conn:
        assert [r[0] for r in conn.execute("SELECT content FROM unified_blocks WHERE parent_id = 'root'")] == [
            "Kept note"]
        assert list(folder_paths(conn).values()) == [["Kept"]], "the failed note's folder went with it"


# --- refusals -------------------------------------------------------------------------------------

def test_what_is_refused(accounts):
    owner = accounts["nbi_owner"]
    r = send(owner, fx.ntb(), "Cloud.ntb")
    assert r.status_code == 400 and r.json()["detail"] == nb.NTB_REASON
    r = send(owner, b"noteSN_FILE_VER_20230015" + bytes(64), "supernote.note")
    assert r.status_code == 400 and r.json()["detail"] == nb.SUPERNOTE_REASON
    r = send(owner, b"%PDF-1.7 not a zip", "file.note")
    assert r.status_code == 400 and r.json()["detail"] == nb.NO_NOTES_REASON
    r = send(owner, fx.zip_of({"readme.txt": b"x"}), "other.zip")
    assert r.status_code == 400 and r.json()["detail"] == nb.NO_NOTES_REASON
    beside = imported(owner, fx.zip_of({"Old.ntb": fx.ntb(), "Renamed.note": fx.ntb(),
                                        "New.note": note("Beside an ntb")}))
    assert beside["pages_created"] == 1 and nb.NTB_REASON in reasons(beside)
    assert f"Not imported: {nb.NTB_REASON}" in reasons(beside)  # an .ntb named .note


def test_a_central_directory_past_the_cap(accounts, monkeypatch):
    owner = accounts["nbi_owner"]
    monkeypatch.setattr(ni, "MAX_DIRECTORY_BYTES", 400)
    many = {f"Images/picture number {i:03d}.png": fx.png(2, 2) for i in range(20)}
    upload = fx.zip_of({"Big.note": note("Big directory", files=many), "Small.note": fx.note_zip(
        "N", session_bytes=fx.session(name="Small directory"), meta=fx.metadata("Small directory"))})
    result = imported(owner, upload)
    assert [p["title"] for p in result["pages"]] == ["Small directory"]
    assert any(w["selection_id"] == "Big.note" and "lists more files than this import reads" in w["reason"]
               for w in result["warnings"])
    big = fx.zip_of({f"x{i}/Session.plist": b"" for i in range(10)})
    r = send(owner, big)
    assert r.status_code == 413 and "lists more files" in r.json()["detail"]


def test_zip_bombs(accounts):
    owner = accounts["nbi_owner"]
    upload = fx.zip_of({"Bombed/Session.plist": fx.session(name="Beside a bomb"),
                        "Bombed/metadata.plist": fx.metadata("Beside a bomb"),
                        "Bombed/blob.bin": bytes(2 << 20)}, zipfile.ZIP_DEFLATED)
    result = imported(owner, upload)
    assert result["pages_created"] == 1
    assert any("like a zip bomb" in w["reason"] and w["title"] == "Bombed/blob.bin" for w in result["warnings"])
    bomb = fx.zip_of({f"s{i}.bin": bytes(900 << 10) for i in range(3)} | {"x.note": note("Never read")},
                     zipfile.ZIP_DEFLATED)
    r = send(owner, bomb)
    assert r.status_code == 413 and "like a zip bomb" in r.json()["detail"]


def test_a_pdf_over_the_file_cap_skips_its_note(accounts, monkeypatch):
    owner = accounts["nbi_owner"]
    monkeypatch.setattr(ni, "workspace_quota", lambda ws: {"max_upload_mb": 1})
    heavy = fx.pdf() + bytes(1 << 20)
    upload = fx.zip_of({"Heavy.note": note("Heavy PDF", layout=[(PDF, 1)], pdfs={PDF: 2},
                                           files={f"PDFs/{PDF}": heavy}),
                        "Light.note": note("Light note", curves=[fx.curve(WORKED)])})
    plan = imported(owner, upload, preview=True)
    heavy_entry = next(p for p in plan["pages"] if p["title"] == "Heavy PDF")
    assert [w["reason"] for w in heavy_entry["warnings"]] == [
        f"Not imported: its PDF {PDF} is over the 1 MB per-file limit"]
    result = imported(owner, upload)
    assert [p["title"] for p in result["pages"]] == ["Light note"]
    assert result["skipped"] == [{"title": "Heavy PDF", "reason": f"its PDF {PDF} is over the 1 MB per-file limit"}]


def test_a_full_quota_stops_the_import(accounts, monkeypatch):
    owner = accounts["nbi_quota"]
    small, large = fx.pdf(), fx.pdf(((612, 792),) * 40)
    real = storage.check_upload_allowed

    def quota(ws, nbytes):
        if nbytes >= len(large):
            raise HTTPException(status_code=507, detail="storage quota exceeded")
        return real(ws, nbytes)

    monkeypatch.setattr(storage, "check_upload_allowed", quota)
    upload = fx.zip_of({f"{i}.note": note(f"Quota {i}", layout=[(PDF, n) for n in range(1, 41 if i == 2 else 2)],
                                          pdfs={PDF: 2}, files={f"PDFs/{PDF}": large if i == 2 else small})
                        for i in (1, 2, 3)})
    result = imported(owner, upload)
    assert [p["title"] for p in result["pages"]] == ["Quota 1"]
    assert result["skipped"] == [{"title": "Quota 2", "reason": "storage quota exceeded"},
                                 {"title": "3.note", "reason": "storage quota exceeded"}]


def test_a_stopped_import_keeps_the_notes_it_finished():
    make_user("nbi_stop", "nbi-password")
    ws = workspace_of("nbi_stop")
    upload = fx.zip_of({f"S{i}.note": note(f"Stopped {i}", curves=[fx.curve(WORKED)]) for i in range(3)})

    def progress(done=None, **_):
        if done == 1:
            raise jobs.Cancelled()

    with connect_pages_db(ws) as conn:
        with pytest.raises(jobs.Cancelled):
            ni.import_notes(ws, io.BytesIO(upload), conn, name="stop.zip", actor="nbi", progress=progress)
    with connect_pages_db(ws) as conn:
        titles = [r[0] for r in conn.execute("SELECT content FROM unified_blocks WHERE parent_id = 'root'")]
    assert "Stopped 0" in titles and "Stopped 1" not in titles


def test_a_viewer_cannot_import(accounts):
    from gamma import workspaces
    owner = account_of("nbi_owner")
    ws = workspaces.create("Notability shared", owner, kind="shared")["id"]
    workspaces.set_member(ws, account_of("nbi_other"), "viewer", by=owner)
    for path in ("/api/import/notability", "/api/import/notability/preview"):
        r = accounts["nbi_other"].post(path, headers={"X-Gamma-Workspace": ws},
                                       files={"file": ("x.note", note("Viewer note"), "application/zip")})
        assert r.status_code == 403, r.text


# --- what a hostile note may cost ---------------------------------------------------------------

def test_one_pdf_under_several_names_is_read_once(accounts, monkeypatch):
    owner = accounts["nbi_owner"]
    opened, real = [], ni.ExportPdfReader
    monkeypatch.setattr(ni, "ExportPdfReader", lambda *a, **k: opened.append(1) or real(*a, **k))
    names = [PDF, PDF.lower(), f"PDFs/{PDF}"]  # each resolves to the one file
    result = imported(owner, note("One PDF, three names", layout=[(n, 1) for n in names], pdfs={n: 2 for n in names},
                                  files={f"PDFs/{PDF}": fx.pdf()}), "Names.note")
    assert result["pages_created"] == 1 and result["pdfs_stored"] == 1 and len(opened) == 1


def test_a_picture_file_shown_over_and_over_is_read_a_few_times(accounts, monkeypatch):
    owner = accounts["nbi_owner"]
    real, made = fx.media_object, []

    def one_object(arch, item):  # every entry of mediaObjects names the first one
        if not made:
            made.append(real(arch, item))
        return made[0]

    monkeypatch.setattr(fx, "media_object", one_object)
    picture = {"kind": "picture", "origin": (10, 10), "size": (40, 40), "path": "Images/a.png", "crop": (0, 0, 20, 20)}
    decoded, bytes_of = [], ni.picture_bytes
    monkeypatch.setattr(ni, "picture_bytes", lambda data, media: decoded.append(1) or bytes_of(data, media))
    result = imported(owner, note("One picture named often", media=[picture] * 9,
                                  files={"Images/a.png": fx.png(40, 40)}), "Often.note")
    assert result["pictures_imported"] == ni.MAX_PICTURE_USES == len(decoded)
    assert reasons(result) == [f"Picture a.png is shown more than {ni.MAX_PICTURE_USES} times: the rest are left out"]


def test_a_recording_listed_twice_is_stored_once(accounts, monkeypatch):
    owner = accounts["nbi_owner"]
    streamed, real = [], ni.store_file_stream
    monkeypatch.setattr(ni, "store_file_stream", lambda *a: streamed.append(1) or real(*a))
    library = fx.library([{"identifier": i, "filepath": "a.m4a", "name": f"Take {i}"} for i in range(1, 6)])
    result = imported(owner, note("One recording listed five times", files={
        "Recordings/library.plist": library, "Recordings/a.m4a": fx.m4a(2000)}), "Listed.note")
    assert result["recordings_imported"] == 1 == len(streamed)


def test_a_stretch_too_long_and_thin_is_refused_before_pillow_sizes_it(monkeypatch):
    from PIL import Image

    def never(*_args, **_kwargs):
        raise AssertionError("resized")

    monkeypatch.setattr(Image.Image, "resize", never)
    thin = nb.Media(kind="picture", x=0, y=0, w=1e6, h=1e-5, picture="a.png")  # 2,000,000,000 x 1 px asked
    with pytest.raises(ValueError, match="too long and thin"):
        ni.picture_bytes(fx.png(10, 10), thin)


def test_the_note_s_sample_budget_bounds_the_work(accounts, monkeypatch):
    owner = accounts["nbi_owner"]
    monkeypatch.setattr(ni, "MAX_NOTE_SAMPLES", 25)
    made, real = [], ni.nmap.curve_stroke
    monkeypatch.setattr(ni.nmap, "curve_stroke", lambda curve, at: made.append(1) or real(curve, at))
    result = imported(owner, note("Too much ink", curves=[fx.curve(WORKED)] * 6), "Ink.note")
    assert result["strokes_imported"] == 2 and len(made) == 3  # the third went past: it and the rest unmade
    assert "The note's handwriting is too long: 4 strokes are left out" in reasons(result)


def test_damage_in_one_group_leaves_one_stroke_out_not_the_note(accounts):
    owner = accounts["nbi_owner"]
    inner = {"transform": [1e-200, 0, 0, 1e-200, 1e200, -1e200], "index": 0,
             "curves": [fx.curve([(1, 1), (2, 2)], polyline=True)]}
    outer = {"transform": [1e200, 0, 1e200, 1e200, 0, 0], "index": 0, "groups": [inner]}  # composes to NaN
    long_one = fx.shape(points=[(0, 100), (300_000, 100), (0, 200)])  # 300,052 samples: a stroke takes 50,000
    result = imported(owner, note("Damaged group", curves=[fx.curve(WORKED)], groups=[outer],
                                  shapes=[("triangle", 0, long_one)]), "Damaged.note")
    assert result["pages_created"] == 1 and result["strokes_imported"] == 1
    assert "2 strokes could not be read and were left out" in reasons(result)
