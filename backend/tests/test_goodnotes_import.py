"""The GoodNotes import through the API (gamma/goodnotes_import.py,
docs/dev/import_export.md "GoodNotes notebooks"): the preview and the
reviewed import, what each kind of notebook becomes, its pictures, text
and recordings, the re-import key, folders, and what is refused."""

import io
import json

import pytest

import goodnotes_fixtures as gx
import notability_fixtures as fx
from conftest import login, make_user, workspace_of
from gamma import jobs, storage
from gamma.db import connect_pages_db, ws_uploads_dir

LINE = [(10.0, 20.0), ((15.0, 20.0), (20.0, 20.0)), ((25.0, 20.0), (30.0, 25.0))]


@pytest.fixture(scope="module")
def accounts(client):
    names = ("gni_owner", "gni_other")
    for name in names:
        make_user(name, "gni-password")
    return {name: login(name, "gni-password") for name in names}


def send(client, data: bytes, name: str = "notebooks.zip", *, folder: str = "", selected=None, preview=False):
    fields = {"folder": folder}
    if selected is not None:
        fields["selected"] = json.dumps(selected)
    return client.post("/api/import/goodnotes/preview" if preview else "/api/import/goodnotes",
                       files={"file": (name, data, "application/zip")}, data=fields)


def imported(client, data: bytes, name: str = "notebooks.zip", **kwargs) -> dict:
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


def reasons(report: dict) -> list[str]:
    return [w["reason"] for w in report["warnings"]] + [s["reason"] for s in report["skipped"]]


def paper_notebook(title: str, *, seed: int = 1, pages: int = 1) -> bytes:
    n = gx.Notebook(title, seed=seed)
    grid = n.paper("grid")
    for _ in range(pages):
        n.page(grid, [gx.ink("A", gx.flat([LINE]))])
    return n.bytes()


# --- preview and review ---------------------------------------------------------------------

def test_a_preview_writes_nothing_and_lists_each_notebook(accounts):
    owner, ws = accounts["gni_owner"], workspace_of("gni_owner")
    before, files = rows(ws), uploads(ws)
    pdf = gx.Notebook("On a PDF", seed=2)
    pdf.page(pdf.pdf(fx.pdf()))
    upload = gx.zip_of({"Export/Biology/Cells.goodnotes": paper_notebook("Preview cells"),
                        "Export/Physics/Waves.goodnotes": pdf.bytes(), "Export/readme.txt": b"hello"})
    plan = imported(owner, upload, preview=True)
    assert rows(ws) == before and uploads(ws) == files
    pages = {p["title"]: p for p in plan["pages"]}
    assert pages["Preview cells"]["kind"] == "page" and pages["On a PDF"]["kind"] == "pdf"
    assert pages["Preview cells"]["folders"] == [["Biology"]] and pages["On a PDF"]["folders"] == [["Physics"]]
    assert all(p["action"] == "create" and not p["created"] for p in plan["pages"])
    assert {(e["path"], e["status"]) for e in plan["entries"]} == {
        ("Export/Biology/Cells.goodnotes", "source"), ("Export/Physics/Waves.goodnotes", "source"),
        ("Export/readme.txt", "not_imported")}
    assert plan["pages_created"] == 0 and plan["ok"] is True


def test_the_review_imports_the_selected_notebook(accounts):
    owner = accounts["gni_owner"]
    upload = gx.zip_of({"A.goodnotes": paper_notebook("Reviewed one", seed=3),
                        "B.goodnotes": paper_notebook("Reviewed two", seed=4)})
    r = owner.post("/api/import/review", data={"source": "goodnotes"},
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


# --- what a notebook becomes ----------------------------------------------------------------

def test_a_notebook_of_papers_becomes_sheets(accounts):
    owner, ws = accounts["gni_owner"], workspace_of("gni_owner")
    n = gx.Notebook("Paper notebook", seed=5)
    picture = n.attach(fx.png(60, 30))
    cover = n.paper("cover", fill=(0.31764707, 0.84313726, 0.9607843), colour="Blue")
    n.page(cover)
    n.page(n.paper("ruled"), [gx.ink("A", gx.flat([LINE])), gx.rtf_text("T", (100, 100, 220, 60), gx.COCOA),
                              gx.image("I", picture, (300, 300, 120, 60))])
    result = imported(owner, n.bytes(), "Paper notebook.goodnotes")
    assert [result[k] for k in ("pages_created", "strokes_imported", "text_boxes_imported", "pictures_imported")] == [
        1, 1, 1, 1]
    page = subtree(owner, result["pages"][0]["id"])
    assert "doc_id" not in page["properties"] and page["content"] == "Paper notebook"
    cover_sheet, ruled = page["children"]
    assert cover_sheet["properties"]["sheet"]["color"] == "#51d7f5" and cover_sheet["children"] == []
    assert ruled["properties"]["sheet"]["pattern"] == "ruled" and ruled["properties"]["sheet"]["spacing"] == 25
    group, box, shown = ruled["children"]
    assert ink_of(ws, group)["space"] == {"kind": "canvas", "width": 455.04, "height": 588.45}
    assert box["content"] == "Plain and **bold**" and "pdf_page" not in box["properties"]
    assert shown["content"] == "" and list(shown["properties"]) == ["picture"]
    assert stored(ws, shown["properties"]["picture"]["url"]) == fx.png(60, 30)


def test_a_notebook_on_one_pdf_keeps_it(accounts):
    owner, ws = accounts["gni_owner"], workspace_of("gni_owner")
    pdf = fx.pdf(((612, 792), (612, 792)))
    n = gx.Notebook("PDF notebook", seed=6)
    first = n.pdf(pdf, 1)
    attachment = next(iter(n.attachments))
    n.page(first, [gx.ink("A", gx.flat([LINE]))])
    n.page(n.pdf(pdf, 2, attachment=attachment), [gx.ink("B", gx.flat([LINE]))])
    result = imported(owner, n.bytes(), "PDF notebook.goodnotes")
    assert result["pages_created"] == 1 and result["pdfs_stored"] == 1
    page = subtree(owner, result["pages"][0]["id"])
    assert page["properties"]["doc_id"] == storage.content_digest(pdf)
    groups = [c for c in page["children"] if c["properties"].get("ink_url")]
    assert [ink_of(ws, g)["space"] for g in groups] == [{"kind": "pdf-page", "page": k, "width": 612, "height": 792}
                                                         for k in (1, 2)]


def test_a_mixed_notebook_is_one_composed_pdf(accounts):
    from PyPDF2 import PdfReader

    owner, ws = accounts["gni_other"], workspace_of("gni_other")
    n = gx.Notebook("Mixed notebook", seed=7)
    n.page(n.paper("grid"), [gx.ink("A", gx.flat([LINE]))])
    n.page(n.pdf(fx.pdf(((300, 400),)), size=(300, 400)), [gx.ink("B", gx.flat([LINE]))])
    n.page(None)
    result = imported(owner, n.bytes(), "Mixed.goodnotes")
    assert "A page names no paper: it is blank paper" in reasons(result)
    page = subtree(owner, result["pages"][0]["id"])
    composed = PdfReader(io.BytesIO(stored(ws, page["properties"]["doc_id"] + ".pdf")))
    sizes = [(round(float(p.mediabox.width), 2), round(float(p.mediabox.height), 2)) for p in composed.pages]
    assert sizes == [(455.04, 588.45), (300, 400), (455.04, 588.45)]
    assert b"0.8156863 0.8235294 0.8274510 scn" in composed.pages[0]["/Resources"]["/XObject"]["/xo0"].get_data()
    groups = [c for c in page["children"] if c["properties"].get("ink_url")]
    assert [g["properties"]["pdf_position"]["pageNumber"] for g in groups] == [1, 2]


def test_recordings_come_after_the_pages(accounts):
    owner = accounts["gni_other"]
    n = gx.Notebook("Heard notebook", seed=8)
    n.page(n.paper())
    n.recording(fx.m4a(2500), when_ms=1790000100000.0)
    result = imported(owner, n.bytes(), "Heard.goodnotes")
    assert result["recordings_imported"] == 1
    *_sheets, block = subtree(owner, result["pages"][0]["id"])["children"]
    recording = block["properties"]["recording"]
    assert (block["content"], recording["dur"], recording["started"]) == ("Recording", 2500, 1790000100000)


def test_a_sticker_is_a_picture_of_its_art(accounts):
    pytest.importorskip("pymupdf")
    from PIL import Image

    owner, ws = accounts["gni_other"], workspace_of("gni_other")
    n = gx.Notebook("Sticker notebook", seed=9)
    sticker = n.attach(gx.sticker_pdf((100, 60)))
    n.page(n.paper(), [gx.image("S", sticker, (50, 50, 183.33, 110), kind=3)])
    result = imported(owner, n.bytes(), "Sticker.goodnotes")
    assert result["pictures_imported"] == 1, reasons(result)
    [sheet] = subtree(owner, result["pages"][0]["id"])["children"]
    [shown] = sheet["children"]
    placed = shown["properties"]["picture"]
    assert (placed["x"], placed["y"], placed["w"], placed["h"]) == pytest.approx((27.27, 27.27, 100, 60), abs=0.01)
    picture = Image.open(io.BytesIO(stored(ws, placed["url"])))
    assert placed["url"].endswith(".png") and (picture.format, picture.mode, picture.size) == ("PNG", "RGBA", (200, 120))


def test_a_sticker_shown_twice_is_rendered_once_for_its_largest_frame(accounts):
    """A sticker shown small, then large, is one PNG for both, twice the
    large frame's 400 pt: not the small one's, drawn blurred at the large."""
    pytest.importorskip("pymupdf")
    from PIL import Image

    owner, ws = accounts["gni_other"], workspace_of("gni_other")
    n = gx.Notebook("Resized sticker", seed=21)
    sticker = n.attach(gx.sticker_pdf((100, 60)))
    n.page(n.paper(), [gx.image("S", sticker, (50, 50, 36.67, 22), kind=3),
                       gx.image("T", sticker, (50, 300, 733.33, 440), kind=3)])
    result = imported(owner, n.bytes(), "Resized.goodnotes")
    assert result["pictures_imported"] == 2, reasons(result)
    [sheet] = subtree(owner, result["pages"][0]["id"])["children"]
    small, large = (b["properties"]["picture"] for b in sheet["children"])
    assert (small["w"], large["w"]) == pytest.approx((20, 400), abs=0.01) and small["url"] == large["url"]
    assert Image.open(io.BytesIO(stored(ws, large["url"]))).size == (800, 480)


def test_a_picture_is_a_picture_block_of_its_file_as_it_came(accounts):
    """A picture comes in as a picture block, its file never decoded: its
    frame (#2) through the page's, its turn (#3.#3, radians) in degrees,
    the file stored once however many pictures show it. One GoodNotes
    shows cropped comes in whole (counted)."""
    owner, ws = accounts["gni_other"], workspace_of("gni_other")
    photo = fx.jpeg(80, 40)
    n = gx.Notebook("Turned pictures", seed=20)
    picture = n.attach(photo)
    n.page(n.paper(), [gx.image("A", picture, (110, 220, 160, 80), turn=0.5),
                       gx.image("B", picture, (110, 440, 160, 80), shown=(190, 480, 80, 80))])
    result = imported(owner, n.bytes(), "Turned.goodnotes")
    assert result["pictures_imported"] == 2 and reasons(result) == ["1 cropped picture is shown whole"]
    [sheet] = subtree(owner, result["pages"][0]["id"])["children"]
    turned, cropped = (b["properties"]["picture"] for b in sheet["children"])
    assert turned["url"] == cropped["url"] and turned["url"].endswith(".jpg") and stored(ws, turned["url"]) == photo
    k = 6 / 11
    assert (turned["x"], turned["y"], turned["w"], turned["h"]) == pytest.approx((110 * k, 220 * k, 160 * k, 80 * k),
                                                                                abs=0.01)
    assert (turned["angle"], turned["flip_h"], turned["flip_v"]) == (28.65, False, False)
    assert (cropped["angle"], cropped["crop"], cropped["y"]) == (0, {"x": 0, "y": 0, "w": 1, "h": 1},
                                                                 pytest.approx(440 * k, abs=0.01))


# --- the key, the folders, what is refused ---------------------------------------------------

def test_a_notebook_imported_again_is_skipped_and_an_edited_one_is_new(accounts):
    owner = accounts["gni_owner"]
    data = paper_notebook("Imported twice", seed=10)
    first = imported(owner, data, "Twice.goodnotes")
    again = imported(owner, data, "Twice.goodnotes")
    assert (again["pages_created"], again["pages_skipped"]) == (0, 1)
    assert again["pages"][0]["id"] == first["pages"][0]["id"] and again["pages"][0]["action"] == "skip"
    edited = imported(owner, paper_notebook("Imported twice", seed=10, pages=2), "Twice.goodnotes")
    assert edited["pages_created"] == 1 and edited["pages"][0]["id"] != first["pages"][0]["id"]
    twins = gx.zip_of({"a/One.goodnotes": paper_notebook("Twin", seed=11), "b/One.goodnotes": paper_notebook(
        "Twin", seed=11)})
    both = imported(owner, twins)
    assert (both["pages_created"], both["pages_skipped"]) == (1, 1)
    assert "The same note as a/One.goodnotes" in reasons(both)


def test_folders_follow_the_export_s_tree(accounts):
    owner = login("gni_other", "gni-password")
    upload = gx.zip_of({"Library/Courses/Chemistry/Lab.goodnotes": paper_notebook("Lab book", seed=12),
                        "Library/Inbox.goodnotes": paper_notebook("Inbox book", seed=13)})
    result = imported(owner, upload)
    folders = {p["title"]: p["folders"] for p in result["pages"]}
    assert folders == {"Lab book": [["Courses", "Chemistry"]], "Inbox book": []}


def test_an_upload_without_a_notebook_is_refused(accounts):
    owner = accounts["gni_owner"]
    for data in (b"not a zip at all", gx.zip_of({"readme.txt": b"hello"})):
        r = send(owner, data, "notes.zip")
        assert r.status_code == 400 and "no GoodNotes notebooks found" in r.json()["detail"]


def test_a_damaged_notebook_is_named_and_the_next_imported(accounts):
    owner = accounts["gni_owner"]
    upload = gx.zip_of({"A.goodnotes": b"PK\x03\x04 not a zip", "B.goodnotes": paper_notebook("Kept", seed=14),
                        "C.goodnotes": gx.zip_of({"readme.txt": b"x"})})
    result = imported(owner, upload)
    assert [p["title"] for p in result["pages"]] == ["Kept"] and result["pages_created"] == 1
    assert {w["title"]: w["reason"] for w in result["warnings"]} == {
        "A.goodnotes": "Not imported: not a readable zip: not opened",
        "C.goodnotes": "Not imported: not a GoodNotes notebook"}


def test_a_missing_paper_pdf_is_blank_paper(accounts):
    owner = accounts["gni_owner"]
    n = gx.Notebook("Lost paper notebook", seed=15)
    grid = n.paper("grid")
    n.page(grid, [gx.ink("A", gx.flat([LINE]))])
    paper = next(iter(n.attachments.values()))[0]
    result = imported(owner, n.bytes(drop=(paper,)), "Lost.goodnotes")
    assert result["pages_created"] == 1 and result["strokes_imported"] == 1
    assert f"Missing PDF {paper}: its pages are blank paper" in reasons(result)


def test_an_emoji_in_a_text_box_imports(accounts):
    owner = accounts["gni_owner"]
    n = gx.Notebook("Emoji notebook", seed=16)
    rtf = rb"{\rtf1\ansi\ansicpg1252 {\fonttbl\f0 HelveticaNeue;}\f0\fs48 Done \uc0\u55357 \u56832 }"
    n.page(n.paper(), [gx.rtf_text("T", (100, 100, 220, 60), rtf)])
    result = imported(owner, n.bytes(), "Emoji.goodnotes")
    assert result["pages_created"] == 1 and result["text_boxes_imported"] == 1, reasons(result)
    [sheet] = subtree(owner, result["pages"][0]["id"])["children"]
    assert sheet["children"][0]["content"] == "Done " + chr(0x1F600)


def test_pages_are_read_one_at_a_time_as_their_ink_is_stored(accounts, monkeypatch):
    from gamma import goodnotes as gn

    owner = accounts["gni_owner"]
    calls, real = [], gn.read_page
    monkeypatch.setattr(gn, "read_page", lambda notebook, page, *rest: calls.append(page.id) or real(notebook, page,
                                                                                                    *rest))
    data = paper_notebook("Read page by page", seed=17, pages=3)
    preview = imported(owner, data, "Pages.goodnotes", preview=True)
    assert preview["pages"][0]["action"] == "create" and calls == []
    result = imported(owner, data, "Pages.goodnotes")
    assert result["strokes_imported"] == 3 and len(calls) == 3 == len(set(calls))


def test_a_notebook_that_fails_to_read_is_named_and_the_next_imported(accounts, monkeypatch):
    from gamma import goodnotes as gn

    owner = accounts["gni_owner"]
    real = gn.read_notebook

    def failing(members, *, path, **kwargs):
        if path == "A.goodnotes":
            raise OSError(22, "Invalid argument")  # what Windows' fromtimestamp once raised
        return real(members, path=path, **kwargs)

    monkeypatch.setattr(gn, "read_notebook", failing)
    upload = gx.zip_of({"A.goodnotes": paper_notebook("Fails", seed=18), "B.goodnotes": paper_notebook("Reads",
                                                                                                    seed=19)})
    result = imported(owner, upload)
    assert [p["title"] for p in result["pages"]] == ["Reads"]
    assert {w["title"]: w["reason"] for w in result["warnings"]} == {"A.goodnotes": "Not imported: it could not be read"}


def test_an_upload_is_read_before_its_destination(accounts):
    owner = accounts["gni_owner"]
    r = send(owner, b"not a zip at all", "notes.zip", folder="no-such-folder")
    assert r.status_code == 400 and "no GoodNotes notebooks found" in r.json()["detail"]
