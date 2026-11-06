"""Zips of Markdown notes → pages: Notion's Markdown & CSV export, Gamma's own
Markdown export round-tripped, and the list-continuation parser rule both rely
on."""

import io
import os
import posixpath
import shutil
import time
import tracemalloc
import zipfile
from urllib.parse import quote

import pytest

from gamma import (import_zip, markdown_import, markdown_zip_dialects, markdown_zip_import as mzi, markdown_zip_links,
                   notion_import)
from gamma.markdown_import import md_to_blocks
from conftest import folder_names, label_names, login, make_folder, make_user

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
NOTION_HOME = "Home 0123456789abcdef0123456789abcdef"
NOTION_SUB = "Sub page fedcba9876543210fedcba9876543210"
NOTION_DB = "Tasks 11112222333344445555666677778888"
NOTION_ROW = "Write tests aaaabbbbccccddddeeeeffff00001111"


def _zip(files: dict, compression=zipfile.ZIP_STORED) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in files.items():
            zf.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    buf.seek(0)
    return buf


def _import(client, buf, folder="", name="notes.zip"):
    r = client.post("/api/import/markdown-zip",
                    files={"file": (name, buf.getvalue(), "application/zip")},
                    data={"folder": folder})
    assert r.status_code == 200, r.text
    return r.json()


def _preview(client, buf, folder=""):
    r = client.post("/api/import/markdown-zip/preview",
                    files={"file": ("notes.zip", buf.getvalue(), "application/zip")}, data={"folder": folder})
    assert r.status_code == 200, r.text
    return r.json()


def _reasons(report):
    return {w["title"]: w["reason"] for w in report["warnings"]}


def _subtree(client, page_id):
    return client.get(f"/api/blocks/{page_id}/subtree").json()["block"]


def _filed(client, page_id):
    """The paths of the folders the page is filed in."""
    paths = folder_names(client)
    return [paths[f] for f in _subtree(client, page_id)["properties"].get("folders", [])]


def _shape(node):
    return {"content": node["content"], "children": [_shape(c) for c in node["children"]]}


def _notion_zip():
    home_md = f"""# Home

Welcome to the workspace.

<aside>
💡 Remember to export with subpages.

</aside>

[Sub page]({NOTION_HOME.replace(' ', '%20')}/{NOTION_SUB.replace(' ', '%20')}.md)

![diagram.png]({NOTION_HOME.replace(' ', '%20')}/diagram.png)

[Tasks]({NOTION_HOME.replace(' ', '%20')}/{NOTION_DB.replace(' ', '%20')}.csv)

- Toggle heading

    Hidden paragraph under the toggle.
"""
    sub_md = f"""# Sub page

Back to [Home](../{NOTION_HOME.replace(' ', '%20')}.md).
"""
    row_md = """# Write tests

Status: Done

Cover the zip importer.
"""
    return _zip({
        f"{NOTION_HOME}.md": home_md,
        f"{NOTION_HOME}/{NOTION_SUB}.md": sub_md,
        f"{NOTION_HOME}/diagram.png": PNG,
        f"{NOTION_HOME}/{NOTION_DB}.csv": "Name,Status\nWrite tests,Done\n",
        f"{NOTION_HOME}/{NOTION_DB}_all.csv": "Name,Status\nWrite tests,Done\nShip it,Todo\n",
        f"{NOTION_HOME}/{NOTION_DB}/{NOTION_ROW}.md": row_md,
    })


def test_notion_export_becomes_pages_sub_pages_mentions_and_uploads(guest):
    imports = make_folder(guest, "Imports")
    report = _import(guest, _notion_zip(), folder=imports)
    assert report["folder"] == imports
    assert report["notion"] is True
    assert report["pages_created"] == 4
    assert report["assets_stored"] == 1
    assert report["warnings"] == []
    by_title = {p["title"]: p for p in report["pages"]}
    assert set(by_title) == {"Home", "Sub page", "Tasks", "Write tests"}
    # the pages inside a page (and a database's rows) are its sub-pages,
    # filed where their top is: the page directories make no folders
    assert {t: p["parent"] for t, p in by_title.items()} == {
        "Home": "", "Sub page": by_title["Home"]["id"], "Tasks": by_title["Home"]["id"],
        "Write tests": by_title["Tasks"]["id"]}
    assert all(p["folders"] == [["Imports"]] for p in report["pages"])
    assert _filed(guest, by_title["Home"]["id"]) == [["Imports"]]
    assert _subtree(guest, by_title["Write tests"]["id"])["properties"]["parent_page"] == by_title["Tasks"]["id"]
    assert "folders" not in _subtree(guest, by_title["Write tests"]["id"])["properties"]
    made = folder_names(guest)
    assert [p for p in made.values() if p[0] == "Imports"] == [["Imports"]]

    home = _subtree(guest, by_title["Home"]["id"])
    assert home["properties"]["notion_id"] == "0123456789abcdef0123456789abcdef"
    assert home["properties"]["folders"] == [imports]
    texts = [c["content"] for c in home["children"]]
    assert texts[0] == "Welcome to the workspace."
    # <aside> → callout, the H1 title line is not repeated as a block
    assert texts[1] == "> [!info] 💡 Remember to export with subpages."
    # subpage + database links → mentions of the new pages
    assert texts[2] == f"[[{by_title['Sub page']['id']}]]"
    assert texts[3].startswith("![diagram.png](/api/uploads/") and texts[3].endswith(".png)")
    assert texts[4] == f"[[{by_title['Tasks']['id']}]]"
    toggle = home["children"][5]
    assert toggle["content"] == "- Toggle heading"
    assert [c["content"] for c in toggle["children"]] == ["Hidden paragraph under the toggle."]

    upload = texts[3][len("![diagram.png]("):-1]
    assert guest.get(upload).status_code == 200

    sub = _subtree(guest, by_title["Sub page"]["id"])
    assert sub["children"][0]["content"] == f"Back to [[{by_title['Home']['id']}]]."

    # the database: one page holding the _all table (every row), rows as pages
    tasks = _subtree(guest, by_title["Tasks"]["id"])
    table = tasks["children"][0]["content"]
    assert table.startswith("| Name | Status |")
    assert "| Ship it | Todo |" in table
    row = _subtree(guest, by_title["Write tests"]["id"])
    assert [c["content"] for c in row["children"]] == ["Status: Done", "Cover the zip importer."]

    # importing the same export again adds nothing, but links still resolve
    again = _import(guest, _notion_zip(), folder=imports)
    assert again["pages_created"] == 0
    assert again["pages_skipped"] == 4
    assert {p["title"]: (p["folders"], p["parent"]) for p in again["pages"]}["Write tests"] == (
        [["Imports"]], by_title["Tasks"]["id"])
    assert folder_names(guest) == made  # nothing made twice


def test_notion_wrapper_folder_and_part_zips_are_unpacked(guest):
    inner = _zip({"Export-1b2c3d4e-0000-1111-2222-333344445555/Home 99999999999999999999999999999999.md": "# Home\n\nPart one.\n"})
    outer = _zip({"Part-1.zip": inner.getvalue()})
    report = _import(guest, outer)
    assert report["pages_created"] == 1
    assert report["pages"][0]["folders"] == []
    assert report["pages"][0]["title"] == "Home"
    assert "folders" not in _subtree(guest, report["pages"][0]["id"])["properties"]


def test_a_notion_database_keeps_every_row(guest):
    """No row cap: every row, TABLE_ROWS to a table block (each under the
    header, small enough to edit); columns past MAX_TABLE_COLS are named."""
    rows = "".join(f"Every row {i},Done\n" for i in range(1200))
    report = _import(guest, _zip({"Rows 0000aaaa1111bbbb2222cccc3333dddd.csv": "Name,Status\n" + rows}))
    assert report["warnings"] == []
    tables = [c["content"] for c in _subtree(guest, report["pages"][0]["id"])["children"]]
    assert [t.count("\n") - 1 for t in tables] == [500, 500, 200]
    assert all(t.startswith("| Name | Status |\n| --- | --- |\n") for t in tables)
    assert tables[2].endswith("| Every row 1199 | Done |")

    wide = ",".join(f"c{i}" for i in range(45)) + "\n" + ",".join("v" for _ in range(45)) + "\n"
    report = _preview(guest, _zip({"Wide 0000aaaa1111bbbb2222cccc3333eeee.csv": wide}))
    assert _reasons(report) == {"Wide 0000aaaa1111bbbb2222cccc3333eeee.csv": "5 columns past the first 40 are left out"}


def test_csv_tables_split_by_rows_and_by_characters(monkeypatch):
    data = ("Name,Note\n" + "".join(f"r{i},{'x' * 20}\n" for i in range(5))).encode()
    monkeypatch.setattr(notion_import, "TABLE_ROWS", 2)
    md, cut = notion_import._csv_to_markdown(data)
    assert cut == 0
    assert [t.count("\n") - 1 for t in md.split("\n\n")] == [2, 2, 1]
    monkeypatch.setattr(notion_import, "TABLE_ROWS", 500)
    monkeypatch.setattr(notion_import, "TABLE_CHARS", 60)   # a row is 29 characters
    md, _ = notion_import._csv_to_markdown(data)
    assert [t.count("\n") - 1 for t in md.split("\n\n")] == [2, 2, 1]


def test_a_zip_of_thousands_of_notes_is_reviewed_whole(guest):
    """No cap on notes (it was 2,000): every one is in the review."""
    report = _preview(guest, _zip({f"many/n{i}.md": f"one of many {i}\n" for i in range(2500)}))
    assert len(report["pages"]) == 2500
    assert report["warnings"] == []


def test_a_note_past_the_block_cap_is_named_and_left_out(guest, monkeypatch):
    """A note past MAX_BLOCKS is refused like one past 5 MB, in the review
    and in the import, while the rest of the zip imports."""
    monkeypatch.setattr(markdown_import, "MAX_BLOCKS", 50)
    monkeypatch.setattr(mzi, "MAX_BLOCKS", 50)
    buf = _zip({"capped/huge.md": "".join(f"- capped item {i}\n" for i in range(60)),
                "capped/fine.md": "- a capped-test note that fits\n"})
    reason = "has more than 50 blocks: not imported"
    for report in (_preview(guest, buf), _import(guest, buf)):
        assert [p["title"] for p in report["pages"]] == ["fine"]
        assert _reasons(report) == {"huge.md": reason}
    assert report["pages_created"] == 1


def test_nested_zips_left_unopened_are_named(guest, monkeypatch):
    """Zips inside the zip open to MAX_ZIP_DEPTH levels and up to
    MAX_NESTED_ZIPS of them; each one left closed is named."""
    deepest = _zip({"deepest.md": "nested too deep\n"})
    part = _zip({"in-part.md": "nested part note\n", "deeper.zip": deepest.getvalue()})
    export = _zip({"Part-1.zip": part.getvalue(), "in-export.md": "nested export note\n"})
    outer = _zip({"Export.zip": export.getvalue(), "Other.zip": _zip({"other.md": "nested other\n"}).getvalue(),
                  "broken.zip": b"no zip at all", "top.md": "nested top note\n"})
    report = _preview(guest, outer)
    assert sorted(p["title"] for p in report["pages"]) == ["in-export", "in-part", "other", "top"]
    assert _reasons(report) == {
        "deeper.zip": "a zip nested more than 2 deep is not opened: its notes are not imported",
        "broken.zip": "not a readable zip: not opened"}

    monkeypatch.setattr(import_zip, "MAX_NESTED_ZIPS", 2)
    report = _preview(guest, outer)
    assert sorted(p["title"] for p in report["pages"]) == ["in-export", "in-part", "top"]
    assert _reasons(report)["Other.zip"] == ("more than 2 zips inside the zip: this one is not opened, "
                                             "its notes are not imported")


def test_a_zip_bomb_is_not_read(guest):
    """The guard instead of a size cap: an entry that unpacks to over
    MAX_RATIO times its packed size is left unread, and an archive that
    does so as a whole is refused. Big honest files pass (see below)."""
    bomb = _zip({"bombed/note.md": "see ![[blob.png]]\n", "bombed/blob.png": b"\0" * (2 << 20)},
                zipfile.ZIP_DEFLATED)
    report = _import(guest, bomb)
    assert report["pages_created"] == 1
    reasons = _reasons(report)
    assert reasons["bombed/blob.png"].startswith("unpacks to over 100 times its packed size")  # as in the zip
    assert reasons["note.md"] == "Missing or unselected note or attachment: blob.png; the link is kept as written"

    small_bombs = _zip({f"s{i}.md": "a" * (900 << 10) for i in range(3)}, zipfile.ZIP_DEFLATED)
    r = guest.post("/api/import/markdown-zip", files={"file": ("b.zip", small_bombs.getvalue(), "application/zip")})
    assert r.status_code == 413
    assert "like a zip bomb" in r.json()["detail"]


def test_big_files_are_streamed_and_need_free_disk(guest, monkeypatch):
    """No size cap, total or per file: a big bundled file is streamed into
    storage, one its first bytes contradict is named, and storing one
    needs the free disk a staged upload needs; a nested zip that would fill
    the disk is refused (507)."""
    buf = _zip({"sized/pic note.md": "![[big.png]] and ![[small.png]] and [[fake.m4a]]\n",
                "sized/big.png": PNG + os.urandom(1 << 20), "sized/small.png": PNG + b"sized",
                "sized/fake.m4a": b"not a recording"})
    report = _import(guest, buf)
    assert _reasons(report) == {"fake.m4a": "not a valid audio file"}
    text = _subtree(guest, report["pages"][0]["id"])["children"][0]["content"]
    assert text.startswith("![](/api/uploads/") and text.count("/api/uploads/") == 2

    real = shutil.disk_usage(mzi.config.DATA_DIR)
    monkeypatch.setattr(mzi.shutil, "disk_usage", lambda path: real._replace(free=mzi.MIN_FREE_BYTES))
    report = _import(guest, _zip({"full/disk note.md": "![[full.png]]\n", "full/full.png": PNG + b"full disk"}))
    assert _reasons(report) == {"full.png": "the server's disk has no room for this file"}
    r = guest.post("/api/import/markdown-zip",
                   files={"file": ("p.zip", _zip({"Part-1.zip": _zip({"x.md": "x\n"}).getvalue()}).getvalue(),
                                    "application/zip")})
    assert r.status_code == 507


@pytest.fixture
def own_library():
    """An account of the test's own, whose library has no folders yet: the
    shared guest may hold a "Projects" another module made, which an
    import's "projects" would be filed in (folder names match without case)."""
    make_user("mzi_plain", "pw-mzi-plain")
    return login("mzi_plain", "pw-mzi-plain")


def test_plain_zipped_folder_of_notes(own_library):
    buf = _zip({
        "vault/daily/2026-09-01.md": "- woke up\n- [[wiki style]] stays as typed\n",
        "vault/projects/gamma.md": "---\ntitle: Gamma plans\n---\n# Gamma plans\n\nSee [daily](../daily/2026-09-01.md).\n",
    })
    report = _import(own_library, buf)
    by_title = {p["title"]: p for p in report["pages"]}
    # the single common root ("vault") is dropped, the rest become folders
    assert by_title["2026-09-01"]["folders"] == [["daily"]]
    assert by_title["Gamma plans"]["folders"] == [["projects"]]
    assert _filed(own_library, by_title["Gamma plans"]["id"]) == [["projects"]]
    plans = _subtree(own_library, by_title["Gamma plans"]["id"])
    assert plans["children"][0]["content"] == f"See [[{by_title['2026-09-01']['id']}]]."


def test_names_are_kept_and_tags_become_labels(guest):
    """A directory's name is a folder's name as written ("," and "/"
    included in a front-matter name's own way: "/" separates), tags are
    labels by name, made once and reused."""
    existing = make_folder(guest, "Reading")
    buf = _zip({
        "Q&A, misc/one.md": "---\ntags: [physics, to read]\n---\nbody\n",
        "Q&A, misc/two.md": "---\ntags: physics\nfolder: Reading/Deep dive\n---\nbody\n",
        "top.md": "body\n",
    })
    report = _import(guest, buf)
    by_title = {p["title"]: p for p in report["pages"]}
    assert by_title["one"]["folders"] == [["Q&A, misc"]]
    assert by_title["two"]["folders"] == [["Reading", "Deep dive"]]
    paths = folder_names(guest)
    assert _filed(guest, by_title["two"]["id"]) == [["Reading", "Deep dive"]]
    assert [f for f, p in paths.items() if p == ["Reading"]] == [existing]  # reused, not duplicated
    labels = label_names(guest)
    assert sorted(labels.values()).count("physics") == 1 and "to read" in labels.values()
    one = _subtree(guest, by_title["one"]["id"])["properties"]
    two = _subtree(guest, by_title["two"]["id"])["properties"]
    assert [labels[i] for i in one["labels"]] == ["physics", "to read"]
    assert two["labels"] == one["labels"][:1]
    assert "category" not in one and "folder" not in one


def test_the_destination_is_a_folder_id(guest):
    before = folder_names(guest)
    buf = _zip({"note.md": "body\n"})
    r = guest.post("/api/import/markdown-zip", files={"file": ("n.zip", buf.getvalue(), "application/zip")},
                   data={"folder": "no-such-folder"})
    assert r.status_code == 400
    r = guest.post("/api/import/markdown-zip/preview", files={"file": ("n.zip", buf.getvalue(), "application/zip")},
                   data={"folder": "no-such-folder"})
    assert r.status_code == 400
    page = guest.post("/api/blocks", json={"parent_id": "root", "content": "A page"}).json()
    r = guest.post("/api/import/markdown", files={"file": ("n.md", b"body\n", "text/markdown")},
                   data={"folder": page["id"]})
    assert r.status_code == 400  # a page is no folder
    assert folder_names(guest) == before


def test_a_preview_makes_no_folder_or_label(guest):
    dest = make_folder(guest, "Inbox")
    before = (guest.get("/api/blocks/folders/subtree").json()["block"],
              guest.get("/api/blocks/labels/subtree").json()["block"])
    buf = _zip({"deep/er/note.md": "---\ntags: new-tag\n---\nbody\n", "top.md": "a top note\n"})
    r = guest.post("/api/import/markdown-zip/preview", files={"file": ("n.zip", buf.getvalue(), "application/zip")},
                   data={"folder": dest})
    assert r.status_code == 200, r.text
    assert r.json()["folder"] == dest
    assert {p["title"]: p["folders"] for p in r.json()["pages"]} == {
        "note": [["Inbox", "deep", "er"]], "top": [["Inbox"]]}
    after = (guest.get("/api/blocks/folders/subtree").json()["block"],
             guest.get("/api/blocks/labels/subtree").json()["block"])
    assert after == before


def test_rejects_zip_without_markdown(guest):
    r = guest.post("/api/import/markdown-zip",
                   files={"file": ("x.zip", _zip({"a.txt": "hi"}).getvalue(), "application/zip")})
    assert r.status_code == 400
    r = guest.post("/api/import/markdown-zip",
                   files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert r.status_code == 400


def _put_children(client, page_id, children):
    r = client.put(f"/api/blocks/{page_id}/children", json={"blocks": children})
    assert r.status_code == 200, r.text


def test_gamma_markdown_export_round_trips(guest):
    """Export a folder as Markdown, import the zip elsewhere: same titles,
    folder tree, block nesting, multi-line blocks, images and links."""
    r = guest.post("/api/blocks", json={"parent_id": "root", "content": "Round trip A"})
    a = r.json()
    r = guest.post("/api/blocks", json={"parent_id": "root", "content": "Round trip B"})
    b = r.json()
    rt = make_folder(guest, "rt2026")
    guest.put(f"/api/blocks/{a['id']}", json={"properties": {"folders": [rt]}})
    guest.put(f"/api/blocks/{b['id']}", json={"properties": {"folders": [make_folder(guest, "rt2026/deep")]}})
    img = guest.post("/api/upload-image", files={"file": ("d.png", PNG, "image/png")}).json()["url"]
    _put_children(guest, a["id"], [
        {"id": "rt-h", "content": "# Heading", "properties": {}, "children": [
            {"id": "rt-p", "content": "first line\nsecond line", "properties": {}, "children": []},
            {"id": "rt-c", "content": "```python\nx = 1\n\ny = 2\n```", "properties": {}, "children": []},
            {"id": "rt-i", "content": f"![pic]({img})", "properties": {}, "children": []},
        ]},
        {"id": "rt-l", "content": f"see [[{b['id']}]] for more", "properties": {}, "children": [
            {"id": "rt-m", "content": "$$\na = b\n$$", "properties": {}, "children": []},
        ]},
    ])
    _put_children(guest, b["id"], [
        {"id": "rt-b1", "content": "b note", "properties": {}, "children": []},
    ])

    r = guest.get(f"/api/folders/{rt}/export?mode=readable&highlights=1&notes=1&pdf=1")
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    md_a = next(n for n in names if n.startswith("Round trip A"))
    text_a = zf.read(md_a).decode()
    assert "folder:" not in text_a                      # at the export's root
    md_b = next(n for n in names if n.startswith("Round trip B"))
    assert "folder: deep" in zf.read(md_b).decode()     # relative to the export

    restored = make_folder(guest, "restored")
    report = _import(guest, io.BytesIO(r.content), folder=restored)
    assert report["pages_created"] == 2
    assert report["warnings"] == []
    by_title = {p["title"]: p for p in report["pages"]}
    assert by_title["Round trip A"]["folders"] == [["restored"]]
    assert by_title["Round trip B"]["folders"] == [["restored", "deep"]]
    assert _filed(guest, by_title["Round trip B"]["id"]) == [["restored", "deep"]]

    new_a = _subtree(guest, by_title["Round trip A"]["id"])
    assert _shape(new_a)["children"] == [
        {"content": "# Heading", "children": [
            {"content": "first line\nsecond line", "children": []},
            {"content": "```python\nx = 1\n\ny = 2\n```", "children": []},
            {"content": f"![pic]({img})", "children": []},   # same hash → same upload
        ]},
        {"content": f"see [[{by_title['Round trip B']['id']}]] for more", "children": [
            {"content": "$$\na = b\n$$", "children": []},
        ]},
    ]
    assert new_a["properties"]["folders"] == [restored]
    assert "doc_id" not in new_a["properties"]

    # a second import of the same zip is a no-op
    again = _import(guest, io.BytesIO(r.content), folder=restored)
    assert again["pages_created"] == 0 and again["pages_skipped"] == 2


def test_a_multi_line_area_note_or_link_label_keeps_a_page_readable(guest):
    """An area highlight has no quote, so its note is its first line, and a
    link's label can run over lines too: every line stays under its bullet,
    so one such block costs the page's plain blocks no marker."""
    page = guest.post("/api/blocks", json={"parent_id": "root", "content": "Area notes"}).json()
    folder = make_folder(guest, "areas")
    guest.put(f"/api/blocks/{page['id']}", json={"properties": {"folders": [folder]}})
    area = {"pdf_position": {"page": 1, "rects": []}}
    _put_children(guest, page["id"], [
        {"id": "a-a", "content": "alpha", "properties": {}, "children": []},
        {"id": "a-h", "content": "one\ntwo", "properties": area, "children": []},
        {"id": "a-b", "content": "beta", "properties": {}, "children": [
            {"id": "a-l", "content": "see\nthere", "properties": {"link_url": "https://x.y"}, "children": []},
        ]},
    ])
    r = guest.get(f"/api/folders/{folder}/export?mode=readable&highlights=1&notes=1&pdf=1")
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    text = zf.read(next(n for n in zf.namelist() if n.startswith("Area notes"))).decode()
    assert "\n- one\n  two\n" in text and "\n  - [see\n    there](https://x.y)\n" in text

    report = _import(guest, _zip({"Area notes.md": text}))
    assert _shape(_subtree(guest, report["pages"][0]["id"]))["children"] == [
        {"content": "alpha", "children": []},
        {"content": "one\ntwo", "children": []},
        {"content": "beta", "children": [{"content": "[see\nthere](https://x.y)", "children": []}]},
    ]


def _plain_and_list_blocks():
    return [
        {"id": "o-x", "content": "x", "properties": {}, "children": [
            {"id": "o-y", "content": "- y\n  more", "properties": {}, "children": [
                {"id": "o-n", "content": "1. n", "properties": {}, "children": []},
            ]},
            {"id": "o-t", "content": "- [ ] t", "properties": {}, "children": []},
            {"id": "o-q", "content": "> q\n> r", "properties": {}, "children": []},
            {"id": "o-f", "content": "```py\n- no\n\nlist\n```", "properties": {}, "children": []},
        ]},
        {"id": "o-b", "content": "- b", "properties": {}, "children": []},
    ]


_OUTLINE = [
    {"content": "x", "children": [
        {"content": "- y\n  more", "children": [{"content": "1. n", "children": []}]},
        {"content": "- [ ] t", "children": []},
        {"content": "> q\n> r", "children": []},
        {"content": "```py\n- no\n\nlist\n```", "children": []},
    ]},
    {"content": "- b", "children": []},
]


def test_a_readable_export_gives_plain_and_list_blocks_back_as_they_were(guest):
    """``x`` is written ``- x`` and ``- y`` is written ``- - y``: the file is
    known by its shape, so each comes back as it was."""
    page = guest.post("/api/blocks", json={"parent_id": "root", "content": "Outline page"}).json()
    _put_children(guest, page["id"], _plain_and_list_blocks())
    folder = make_folder(guest, "outlined")
    guest.put(f"/api/blocks/{page['id']}", json={"properties": {"folders": [folder]}})
    r = guest.get(f"/api/folders/{folder}/export?mode=readable&highlights=1&notes=1&pdf=1")
    zipped = zipfile.ZipFile(io.BytesIO(r.content))
    text = zipped.read(next(n for n in zipped.namelist() if n.endswith(".md"))).decode()
    assert "- x\n  - - y\n" in text and "\n- - b\n" in text

    report = _import(guest, io.BytesIO(r.content), folder=make_folder(guest, "again"))
    assert _shape(_subtree(guest, report["pages"][0]["id"]))["children"] == _OUTLINE
    # one file of it, uploaded alone: its H1 stays a block, as any upload's does
    r = guest.post("/api/import/markdown", files={"file": ("other.md", text.replace("Outline", "Lone").encode(), "text/markdown")})
    assert r.status_code == 200, r.text
    assert _shape(_subtree(guest, r.json()["block_id"]))["children"] == [{"content": "# Lone page", "children": _OUTLINE}]


def test_a_stored_file_made_a_page_reads_as_an_upload_does(guest):
    """POST /pages/from-file is ``markdown_page`` too: a readable export's
    bullets are blocks, any other file's are list blocks."""
    def from_file(name, text):
        url = guest.post("/api/upload-file", files={"file": (name, text, "text/markdown")}).json()["url"]
        r = guest.post("/api/pages/from-file", json={"filename": url.rsplit("/", 1)[1], "original": name})
        assert r.status_code == 200, r.text
        return _shape(_subtree(guest, r.json()["page"]["id"]))["children"]

    assert from_file("list.md", b"- a\n- b\n") == [{"content": "- a", "children": []}, {"content": "- b", "children": []}]
    assert from_file("out.md", b"---\ntitle: Out\n---\n\n# Out\n\n- a\n") == [
        {"content": "# Out", "children": [{"content": "a", "children": []}]}]


def test_gamma_single_md_upload_honours_front_matter_folder(guest):
    src = b"---\ntitle: Filed note\nfolder: papers/misc\n---\n# Filed note\n\n- one\n"
    r = guest.post("/api/import/markdown", files={"file": ("filed.md", src, "text/markdown")},
                   data={"folder": make_folder(guest, "inbox")})
    assert r.status_code == 200, r.text
    paths = folder_names(guest)
    assert [paths[f] for f in r.json()["folders"]] == [["inbox", "papers", "misc"]]
    assert _filed(guest, r.json()["block_id"]) == [["inbox", "papers", "misc"]]


def test_a_slash_in_a_folder_name_exports_as_one_level(guest):
    """The front matter joins names with "/": a "/" inside a name is
    written "-", so the path re-imports with one folder per name."""
    top = make_folder(guest, "Net")
    sub = guest.post("/api/pages/folders/ops", json={"ops": [
        {"op": "insert", "id": "tcp-ip-dir", "parent": top, "content": "TCP/IP"}]})
    assert sub.status_code == 200, sub.text
    page = guest.post("/api/blocks", json={"parent_id": "root", "content": "Stack notes"}).json()
    guest.put(f"/api/blocks/{page['id']}", json={"properties": {"folders": ["tcp-ip-dir"]}})
    r = guest.get(f"/api/folders/{top}/export?mode=readable")
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content)) if r.content[:2] == b"PK" else None
    text = zf.read(zf.namelist()[0]).decode() if zf else r.text
    assert "folder: TCP-IP\n" in text
    report = _import(guest, _zip({"stack.md": text}), folder=make_folder(guest, "Copy"))
    assert report["pages"][0]["folders"] == [["Copy", "TCP-IP"]]


def test_md_to_blocks_list_continuation_rules():
    text = """- item one
  continues item one
  - nested
- code item
  ```js
  a();

  b();
  ```
- toggle

    child paragraph after a blank line
- para item

  second paragraph of the item
"""
    tree = md_to_blocks(text)
    assert [n["content"] for n in tree] == [
        "- item one\n  continues item one",
        "- code item",
        "- toggle",
        "- para item\n\n  second paragraph of the item",
    ]
    assert [c["content"] for c in tree[0]["children"]] == ["- nested"]
    assert [c["content"] for c in tree[1]["children"]] == ["```js\na();\n\nb();\n```"]
    assert [c["content"] for c in tree[2]["children"]] == ["child paragraph after a blank line"]


def test_notion_links_with_parentheses_and_template_twins(guest):
    """Notion leaves ``(``/``)`` unencoded in its links (a view's CSV is
    ``Tasks (grid view) <id>.csv``), and two pages of one template have the
    same bytes: the link resolves, and each page is the page of its own
    id, not one of them skipped as the other."""
    view = "Tasks (grid view) 0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f"
    twin = "# Untitled\n\nStatus: Not started\n"
    report = _import(guest, _zip({
        "Board 1212121212121212121212121212abab.md": f"# Board\n\nSee [the view]({view.replace(' ', '%20')}.csv).\n",
        f"{view}.csv": "Name,Status\nA,B\n",
        "Untitled 3434343434343434343434343434cdcd.md": twin,
        "Untitled 5656565656565656565656565656efef.md": twin,
    }))
    assert report["warnings"] == [] and report["pages_created"] == 4 and report["pages_skipped"] == 0
    by_title = {}
    for p in report["pages"]:
        by_title.setdefault(p["title"], []).append(p["id"])
    board = _subtree(guest, by_title["Board"][0])
    assert board["children"][0]["content"] == f"See [[{by_title['Tasks (grid view)'][0]}]]."
    assert len(by_title["Untitled"]) == 2


# A page tree as Notion exports it since late 2025 (the shape of a real
# export): ids on files, none on directories, a "Private & Shared" section
# directory around everything; or, with "Create folders for subpages" off,
# every file in one directory.
# (the two layouts' ids differ: the session's guest imports both)
_RT_KEYS = ("top", "a", "b", "a1", "cases", "groups", "r1", "r2", "g")


def _round_trip_zip(folders: bool):
    RT = {k: f"{'f' if folders else 'e'}{i:031x}" for i, k in enumerate(_RT_KEYS)}

    def at(*parts):  # a file's path in the zip
        return "/".join(["Private & Shared", *parts] if folders else parts[-1:])

    def link(src, dst):  # a link from the file at src to the file at dst, as Notion writes it
        rel = posixpath.relpath(dst, posixpath.dirname(src)) if folders else dst
        return rel.replace(" ", "%20").replace("—", "%E2%80%94")

    top = at(f"Round trip {RT['top']}.md")
    child_a = at("Round trip", f"Child A — structure {RT['a']}.md")
    child_b = at("Round trip", f"Child B {RT['b']}.md")
    grand = at("Round trip", "Child A — structure", f"Grandchild {RT['a1']}.md")
    cases = at("Round trip", f"Cases {RT['cases']}.csv")
    cases_all = at("Round trip", f"Cases {RT['cases']}_all.csv")
    groups = at("Round trip", f"Groups {RT['groups']}_all.csv")
    row1 = at("Round trip", "Cases", f"RT-001 Alpha {RT['r1']}.md")
    row2 = at("Round trip", "Cases", f"RT-002 Beta, comma {RT['r2']}.md")
    group = at("Round trip", "Groups", f"Group A {RT['g']}.md")
    # a relation cell's path is the related file's, from the database's page
    relation = f"Group A ({link(top, group)})"
    return _zip({
        top: f"""# Round trip

> Quote fixture.
> 

---

<aside>
♻️

Roundtrip callout.

</aside>

[Groups]({link(top, groups)})

[Child A — structure]({link(top, child_a)})

[Child B]({link(top, child_b)})

[Cases]({link(top, cases)})
""",
        child_a: f"# Child A — structure\n\n- [ ]  Pending task\n\n[Grandchild]({link(child_a, grand)})\n",
        child_b: (f"# Child B\n\n[Sibling Child A]({link(child_b, child_a)})\n\n"
                  f"[Child A — structure]({link(child_b, child_a)})\n"),
        grand: "# Grandchild\n\nDepth two.\n",
        cases: f"Name,Select,Groups\nRT-001 Alpha,Ready,{relation}\n\"RT-002 Beta, comma\",,\n",
        cases_all: f"Name,Groups,Select\nRT-001 Alpha,{relation},Ready\n\"RT-002 Beta, comma\",,\n",
        groups: "Name\nGroup A\n",
        row1: f"# RT-001 Alpha\n\nGroups: Group A ({link(row1, group)})\nSelect: Ready\n\nRow body.\n",
        row2: "# RT-002 Beta, comma\n\nSelect: \n",
        group: "# Group A\n",
    })


@pytest.mark.parametrize("folders", [True, False], ids=["folders", "flat"])
def test_a_2026_notion_export_comes_in_as_a_page_tree(guest, folders):
    report = _import(guest, _round_trip_zip(folders))
    assert report["warnings"] == [] and report["pages_created"] == 9
    page = {p["title"]: p for p in report["pages"]}
    assert {t: p["parent"] and next(q for q, r in page.items() if r["id"] == p["parent"])
            for t, p in page.items()} == {
        "Round trip": "", "Child A — structure": "Round trip", "Child B": "Round trip",
        "Grandchild": "Child A — structure", "Cases": "Round trip", "Groups": "Round trip",
        "RT-001 Alpha": "Cases", "RT-002 Beta, comma": "Cases", "Group A": "Groups"}
    assert all(p["folders"] == [] for p in report["pages"])
    assert not [p for p in folder_names(guest).values() if p[0] in ("Round trip", "Private & Shared")]

    top = [c["content"] for c in _subtree(guest, page["Round trip"]["id"])["children"]]
    assert top[:3] == ["> Quote fixture.", "---", "> [!info] ♻️\n> Roundtrip callout."]
    assert top[3:] == [f"[[{page[t]['id']}]]" for t in ("Groups", "Child A — structure", "Child B", "Cases")]
    # the database keeps the view's column order; relations are mentions
    group = f"[[{page['Group A']['id']}]]"
    table = _subtree(guest, page["Cases"]["id"])["children"][0]["content"]
    assert table.split("\n")[0] == "| Name | Select | Groups |"
    assert table.split("\n")[2] == f"| RT-001 Alpha | Ready | {group} |"
    row = [c["content"] for c in _subtree(guest, page["RT-001 Alpha"]["id"])["children"]]
    assert row == [f"Groups: {group}\nSelect: Ready", "Row body."]


def test_partial_id_directories_pick_their_page(guest):
    """Two pages of one title in a directory: Notion names the second one's
    directory with the first and last four digits of its id, the other
    keeps the plain title."""
    one, two = "bbbb1111000000000000000000001111", "cccc2222000000000000000000002222"
    report = _import(guest, _zip({
        "Recipes 0000aaaa0000aaaa0000aaaa0000aaaa.md": "# Recipes\n",
        f"Recipes/Tarama {one}.md": "# Tarama\n\nFirst.\n",
        f"Recipes/Tarama {two}.md": "# Tarama\n\nSecond.\n",
        f"Recipes/Tarama/Note {'d' * 32}.md": "# Note one\n",
        f"Recipes/Tarama cccc-2222/Note {'e' * 32}.md": "# Note two\n",
    }))
    page = {p["title"]: p for p in report["pages"]}
    ids = {p["id"]: _subtree(guest, p["id"])["properties"].get("notion_id") for p in report["pages"]}
    assert ids[page["Note one"]["parent"]] == one
    assert ids[page["Note two"]["parent"]] == two


def _nids(prefix):
    """Notion ids of a test's own: the session's guest keeps every test's pages."""
    return [f"{prefix}{i:0{32 - len(prefix)}x}" for i in range(12)]


def _enc(name):
    """A file name as Notion writes it in a link."""
    return quote(name, safe="()+,&")


def _parents(report):
    """{title: the title of the page it is inside, or ""}."""
    title = {p["id"]: p["title"] for p in report["pages"]}
    return {p["title"]: title.get(p["parent"], "") for p in report["pages"]}


def test_a_flat_notion_export_with_back_links_marks_and_untitled_pages(guest):
    """Flat: a sub-page links back to its top, labels carry marks, a title
    is cut at 50 characters inside a parenthesis, an untitled sub-page has
    an empty label and an untitled row an empty first cell, and a row is
    linked from a page reached before its database."""
    n = _nids("abc1")
    long = "✅ GAOS — FULL IMPLEMENTATION PLAN (PHASE-WISE + STEPS)"
    cut = long[:50]
    assert "(" in cut and ")" not in cut
    report = _import(guest, _zip({
        f"Top {n[0]}.md": (f"# Top\n\n[Overview]({_enc(f'Overview {n[1]}.md')})\n\n"
                           f"[**Plan**]({_enc(f'Plan {n[2]}.md')})\n\n"
                           f"[✅ **{long[2:]}**]({_enc(f'{cut} {n[3]}.md')})\n\n"
                           f"[]({_enc(f'Untitled {n[4]}.md')})\n\n[Tasks]({_enc(f'Tasks {n[5]}.csv')})\n"),
        f"Overview {n[1]}.md": (f"# Overview\n\n[Write spec]({_enc(f'Write spec {n[6]}.md')})\n\n"
                                f"[Top]({_enc(f'Top {n[0]}.md')})\n"),
        f"Plan {n[2]}.md": "# Plan\n",
        f"{cut} {n[3]}.md": f"# {long}\n",
        f"Untitled {n[4]}.md": "# Untitled\n\nNo title.\n",
        f"Tasks {n[5]}.csv": "Name,Status\nWrite spec,Done\n,Todo\n",
        f"Write spec {n[6]}.md": "# Write spec\n\nStatus: Done\n",
        f"Untitled {n[7]}.md": "# Untitled\n\nStatus: Todo\n",
    }))
    assert report["warnings"] == [] and report["pages_created"] == 8
    ids = {p["title"]: p["id"] for p in report["pages"]}
    untitled = sorted(p["parent"] == ids["Tasks"] for p in report["pages"] if p["title"] == "Untitled")
    assert untitled == [False, True]  # the sub-page and the row
    parents = _parents(report)
    del parents["Untitled"]
    assert parents == {"Top": "", "Overview": "Top", "Plan": "Top", long: "Top", "Tasks": "Top", "Write spec": "Tasks"}
    assert _subtree(guest, ids["Top"])["children"][2]["content"] == f"[[{ids[long]}]]"


def test_a_flat_export_gives_each_database_its_own_rows(guest):
    """Two databases of one template name rows alike: each takes as many
    notes of a title as it has rows of it, the one whose properties match
    (a row page may show a property its CSV leaves out)."""
    n = _nids("abc2")
    report = _import(guest, _zip({
        f"Top {n[0]}.md": f"# Top\n\n[Meetings]({_enc(f'Meetings {n[1]}.csv')})\n\n[Meetings]({_enc(f'Meetings {n[2]}.csv')})\n",
        f"Meetings {n[1]}.csv": "Name,Year\nWeekly sync,2025\n",
        f"Meetings {n[2]}.csv": "Name,Year\nWeekly sync,2026\n",
        f"Weekly sync {n[3]}.md": "# Weekly sync\n\nYear: 2026\nEmail: team@example.org\n",
        f"Weekly sync {n[4]}.md": "# Weekly sync\n\nYear: 2025\n",
    }))
    nid = {p["id"]: _subtree(guest, p["id"])["properties"]["notion_id"] for p in report["pages"]}
    rows = {nid[p["parent"]]: _subtree(guest, p["id"])["children"][0]["content"].split("\n")[0]
            for p in report["pages"] if p["title"] == "Weekly sync"}
    assert rows == {n[1]: "Year: 2025", n[2]: "Year: 2026"}


def test_a_folder_notion_export_with_look_alike_directories(guest):
    """2026 folders: a title ending like a partial id, a page and a
    database of one title with the rows in the plain directory, a
    database's stub page of its own id, and a page whose attachment
    directory marks the export as one with folders."""
    n = _nids("abc3")
    report = _import(guest, _zip({
        f"Home {n[0]}.md": "# Home\n\n![pic](Home/pic.png)\n\n" + f"[Leaf]({_enc(f'Leaf {n[8]}.md')})\n",
        "Home/pic.png": PNG,
        f"Home/Budget 2024-2025 {n[1]}.md": "# Budget 2024-2025\n",
        f"Home/Budget 2024-2025/Q1 {n[2]}.md": "# Q1\n",
        f"Home/Tasks {n[3]}.md": "# Tasks\n\nNotes about tasks.\n",
        f"Home/Tasks {n[4]}_all.csv": "Name,Status\nWrite tests,Done\n",
        f"Home/Tasks/Write tests {n[5]}.md": "# Write tests\n\nStatus: Done\n",
        f"Home/People {n[6]}.md": f"# People\n\n[People]({_enc(f'People {n[6]}_all.csv')})\n\nfilters: \nRole\n",
        f"Home/People {n[6]}_all.csv": "Name,Role\nAda,Lead\n",
        f"Home/People/Ada {n[7]}.md": "# Ada\n\nRole: Lead\n",
        f"Leaf {n[8]}.md": "# Leaf\n",
    }))
    assert report["warnings"] == [] and report["pages_created"] == 9
    nid = {p["id"]: _subtree(guest, p["id"])["properties"]["notion_id"] for p in report["pages"]}
    parent = {nid[p["id"]]: nid.get(p["parent"], "") for p in report["pages"]}
    assert parent == {n[0]: "", n[8]: "", n[1]: n[0], n[2]: n[1], n[3]: n[0], n[4]: n[0], n[5]: n[4],
                      n[6]: n[0], n[7]: n[6]}
    people = next(p["id"] for p in report["pages"] if nid[p["id"]] == n[6])
    assert [c["content"].split("\n")[0] for c in _subtree(guest, people)["children"]] == ["filters:", "| Name | Role |"]


def test_notion_quote_tails_and_callouts():
    tidy = notion_import._drop_quote_tails
    assert tidy("- Toggle\n    \n    > Quote.\n    > \n- Next") == "- Toggle\n    \n    > Quote.\n- Next"
    assert tidy("> a\n>\n> b\n> ") == "> a\n>\n> b"
    assert tidy(notion_import._convert_asides("<aside>\n💡\n\n> Quoted.\n> \n\n</aside>")) == "> [!info] 💡\n> > Quoted."
    # after a first line of text, the blank line still parts paragraphs
    assert notion_import._convert_asides("<aside>\n💡 **Tip:** one.\n\nTwo.\n</aside>") == "> [!info] 💡 **Tip:** one.\n>\n> Two."


def test_notion_relations_become_mentions():
    pages = {"1" * 32: "P1", "2" * 32: "P2", "3" * 32: "P3", "4" * 32: "P4"}
    page_of = lambda nid, path: pages.get(nid)  # noqa: E731
    cell = (f"| x | v1.2 release (Home/v1%202%20release%20{'1' * 32}.md), Beta, comma (Beta,%20comma%20{'2' * 32}.md), "
            f"1) Intro (1)%20Intro%20{'3' * 32}.md) |")
    assert notion_import._link_relations(cell, page_of) == ("| x | [[P1]], [[P2]], [[P3]] |", 3)
    line = f"Blocked by: System Design: Chat App (System%20Design%20Chat%20App%20{'4' * 32}.md)"
    assert notion_import._link_relations(line, page_of) == ("Blocked by: [[P4]]", 1)
    kept = f"Other: Elsewhere (Elsewhere%20{'5' * 32}.md)"
    assert notion_import._link_relations(kept, page_of) == (kept, 0)


def test_a_notion_page_uploaded_alone_is_not_imported_again(guest):
    """A page made from its file alone has the file's bytes but no Notion
    id; its export zip finds it by the bytes."""
    n = _nids("abc4")
    name, text = f"Solo {n[0]}.md", "# Solo\n\nUploaded first.\n"
    r = guest.post("/api/import/markdown", files={"file": (name, text.encode(), "text/markdown")})
    assert r.status_code == 200, r.text
    report = _import(guest, _zip({name: text}))
    assert report["pages_created"] == 0 and report["pages_skipped"] == 1
    assert report["pages"][0]["id"] == r.json()["block_id"]


@pytest.mark.parametrize("folders", [True, False], ids=["folders", "flat"])
def test_a_database_stub_export_imported_again_takes_a_new_row(guest, folders):
    """A database's stub note is folded into its page; importing the export
    again with a row added puts the new row under that page."""
    n = _nids("abc5" if folders else "abc6")
    sub = "Home/" if folders else ""

    def export(rows):
        files = {f"Home {n[0]}.md": f"# Home\n\n[People]({_enc(f'{sub}People {n[1]}.md')})\n",
                 f"{sub}People {n[1]}.md": f"# People\n\n[People]({_enc(f'People {n[1]}_all.csv')})\n",
                 f"{sub}People {n[1]}_all.csv": "Name,Role\n" + "".join(f"{name},{role}\n" for name, role, _ in rows)}
        for name, role, i in rows:
            files[f"{sub}{'People/' if folders else ''}{name} {n[i]}.md"] = f"# {name}\n\nRole: {role}\n"
        return _zip(files)

    first = _import(guest, export([("Ada", "Lead", 2)]))
    assert first["pages_created"] == 3
    people = next(p["id"] for p in first["pages"] if p["title"] == "People")
    assert _preview(guest, export([("Ada", "Lead", 2), ("Bob", "Member", 3)]))["pages_created"] == 1
    again = _import(guest, export([("Ada", "Lead", 2), ("Bob", "Member", 3)]))
    assert again["pages_created"] == 1 and again["pages_skipped"] == 3
    assert next(p["parent"] for p in again["pages"] if p["title"] == "Bob") == people


def test_template_twins_after_one_was_uploaded_alone(guest):
    """The page made from one twin's file alone is that twin's; the other
    twin, of the same bytes, is still imported."""
    n = _nids("abc7")
    twin = "# Untitled\n\nStatus: Not started (abc7)\n"
    r = guest.post("/api/import/markdown", files={"file": (f"Untitled {n[0]}.md", twin.encode(), "text/markdown")})
    assert r.status_code == 200, r.text
    report = _import(guest, _zip({f"Untitled {n[0]}.md": twin, f"Untitled {n[1]}.md": twin}))
    assert report["pages_created"] == 1 and report["pages_skipped"] == 1
    assert len({p["id"] for p in report["pages"]}) == 2
    assert r.json()["block_id"] in {p["id"] for p in report["pages"]}


def test_flat_titles_with_hashes_and_marks(guest):
    """A title ending in "#" (``# C#`` is "C#", not "C" with a closing
    sequence) and titles whose own characters look like marks still nest."""
    n = _nids("abc8")
    report = _import(guest, _zip({
        f"Top {n[0]}.md": (f"# Top\n\n[Langs]({_enc(f'Langs {n[1]}.csv')})\n\n[**_Inbox**]({_enc(f'_Inbox {n[2]}.md')})\n\n"
                           f"[`init` notes]({_enc(f'init notes {n[3]}.md')})\n"),
        f"Langs {n[1]}.csv": "Name,Kind\nC#,Compiled\nPython,Interpreted\n",
        f"C {n[4]}.md": "# C#\n\nKind: Compiled\n",
        f"Python {n[5]}.md": "# Python\n\nKind: Interpreted\n",
        f"_Inbox {n[2]}.md": "# _Inbox\n",
        f"init notes {n[3]}.md": "# `init` notes\n",
    }))
    assert _parents(report) == {"Top": "", "Langs": "Top", "C#": "Langs", "Python": "Langs",
                                "_Inbox": "Top", "`init` notes": "Top"}


def test_a_relation_names_the_copy_its_path_names(guest):
    """Two copies of one page (one id) in a zip: each relation is the copy
    its path names, not the last one read."""
    n = _nids("abc9")
    files = {}
    for side in ("A", "B"):
        files[f"{side} {n[0 if side == 'A' else 1]}.md"] = f"# {side}\n"
        files[f"{side}/Target {n[2]}.md"] = f"# Target\n\nCopy {side}.\n"
        files[f"{side}/Row {n[3 if side == 'A' else 4]}.md"] = f"# Row\n\nRel: Target (Target%20{n[2]}.md)\n"
    report = _import(guest, _zip(files))
    target_of = {}
    for p in report["pages"]:
        tree = _subtree(guest, p["id"])
        if p["title"] == "Target":
            target_of[tree["children"][0]["content"]] = p["id"]
    for p in report["pages"]:
        if p["title"] == "Row":
            side = next(q["title"] for q in report["pages"] if q["id"] == p["parent"])
            assert _subtree(guest, p["id"])["children"][0]["content"] == f"Rel: [[{target_of[f'Copy {side}.']}]]"


def test_big_relation_cells_and_untitled_rows_stay_fast():
    """Thousands of relations in one cell, and of rows sharing one name,
    are linear work."""
    import time
    ids = [f"{i:032x}" for i in range(3000)]
    cell = "| x | " + ", ".join(f"T{i} (T{i}%20{nid}.md)" for i, nid in enumerate(ids)) + " |"
    t = time.perf_counter()
    text, linked = notion_import._link_relations(cell, lambda nid, href: "P" + nid[-4:])
    assert linked == 3000 and time.perf_counter() - t < 2

    class Plan:
        def __init__(self, path, title, rows=(), fields=None):
            self.entry = type("E", (), {"path": path})()
            self.page_id, self.title, self.rows, self.links = path, title, list(rows), []
            self.fields = fields or {}
            self.existing = False
    db = Plan(f"Tasks {'f' * 32}.csv", "Tasks", [["Name", "Due"]] + [["", f"day {i}"] for i in range(2000)])
    notes = [Plan(f"Untitled {i:032x}.md", "Untitled", fields={"Due": f"day {i}"}) for i in range(2000)]
    t = time.perf_counter()
    tree = notion_import._flat_tree([db] + notes, lambda plan, href: None)
    assert time.perf_counter() - t < 5
    assert len(tree) == 2000 and set(tree.values()) == {db.page_id}


def test_rows_differing_only_by_punctuation_keep_their_databases(guest):
    """``C`` and ``C++`` have the same letters: exact titles go first, over
    every database, also when another row's own page is missing."""
    n = _nids("abca")
    report = _import(guest, _zip({
        f"Top {n[0]}.md": (f"# Top\n\n[Languages]({_enc(f'Languages {n[1]}.csv')})\n\n"
                           f"[Grades]({_enc(f'Grades {n[2]}.csv')})\n\n[Marks]({_enc(f'Marks {n[5]}.csv')})\n"),
        f"Languages {n[1]}.csv": "Name\nC++\n",
        f"Grades {n[2]}.csv": "Name\nC\n",
        f"Marks {n[5]}.csv": "Name\nC+\n",
        f"C {n[4]}.md": "# C\n\nA passing grade.\n",
        f"C++ {n[3]}.md": "# C++\n\nA language.\n",
    }))
    assert _parents(report) == {"Top": "", "Languages": "Top", "Grades": "Top", "Marks": "Top",
                                "C++": "Languages", "C": "Grades"}


def test_titles_ending_in_hashes():
    take = markdown_zip_dialects._take_title
    assert take("# C#\n\nbody", None) == ("C#", "body")
    assert take("# Plan ##\n\nbody", None) == ("Plan", "body")
    # what Gamma's export writes for the titles "Issue #" and "Plan ##"
    assert take("# Issue #\n\nbody", "Issue #") == ("Issue #", "body")
    assert take("# Plan ##\n\nbody", "Plan ##", vault_stem="Plan ##") == ("Plan ##", "body")


def test_a_vault_link_to_a_heading_ending_in_a_hash(guest):
    """Obsidian leaves the "#" out of a link to the heading ``Learn C#``."""
    report = _import(guest, _zip({
        "HashVault/.obsidian/app.json": "{}",
        "HashVault/Hash Lang.md": "Intro hash vault.\n\n## Learn C#\n\nBody.\n",
        "HashVault/Hash Home.md": "See [[Hash Lang#Learn C]].\n",
    }))
    ids = {p["title"]: p["id"] for p in report["pages"]}
    heading = next(c for c in _subtree(guest, ids["Hash Lang"])["children"] if c["content"] == "## Learn C#")
    assert _subtree(guest, ids["Hash Home"])["children"][0]["content"] == f"See [[{heading['id']}]]."


def test_a_flat_export_with_a_file_of_no_id(guest):
    """A hand-added file beside a flat export is no page of it: a whole-line
    link to it is a link, not a crash."""
    n = _nids("abcb")
    files = {f"Top {n[0]}.md": "# Top\n\n[READMEB](READMEB.md)\n", f"Sub {n[1]}.md": "# Sub\n", "READMEB.md": "# READMEB\n"}
    assert _preview(guest, _zip(files))["pages_created"] == 3
    report = _import(guest, _zip(files))
    assert _parents(report) == {"Top": "", "Sub": "", "READMEB": ""}


def test_a_bare_cr_csv_does_not_fail_the_import(guest):
    n = _nids("abcc")
    report = _import(guest, _zip({
        f"Note {n[0]}.md": "# Note\n\nBeside a classic Mac file.\n",
        f"Mac {n[1]}.csv": "Name,Notes\rA,B\r",
    }))
    assert report["pages_created"] == 2
    table = _subtree(guest, next(p["id"] for p in report["pages"] if p["title"] == "Mac"))["children"][0]["content"]
    assert table.split("\n") == ["| Name | Notes |", "| --- | --- |", "| A | B |"]


def test_flat_rows_with_look_alike_properties_and_blank_rows(guest):
    """Notion leaves a row's empty properties out, so a first body line with
    a colon reads as a property; a wholly blank row is an untitled page."""
    n = _nids("abcd")
    report = _import(guest, _zip({
        f"Top {n[0]}.md": f"# Top\n\n[Log]({_enc(f'Log {n[1]}.csv')})\n\n[Tasks]({_enc(f'Tasks {n[2]}.csv')})\n",
        f"Log {n[1]}.csv": "Name\nKickoff\n",
        f"Kickoff {n[3]}.md": "# Kickoff\n\nAttendees: Ada, Bob\n",
        f"Tasks {n[2]}.csv": "Name,Status \nWrite,Done\n,\n",
        f"Write {n[4]}.md": "# Write\n\nStatus: Done\n",
        f"Untitled {n[5]}.md": "# Untitled\n\nA row of no values (abcd).\n",
    }))
    assert _parents(report) == {"Top": "", "Log": "Top", "Tasks": "Top", "Kickoff": "Log",
                                "Write": "Tasks", "Untitled": "Tasks"}


@pytest.mark.parametrize("flip", [False, True], ids=["one-first", "two-first"])
def test_rows_go_where_their_values_fit_whichever_database_is_read_first(guest, flip):
    n = _nids("abce" if flip else "abcf")
    one, two = (f"One {n[1]}.csv", "Name,Year\nSync,\n"), (f"Two {n[2]}.csv", "Name,Year\nSync,2026\n")
    first, second = (two, one) if flip else (one, two)
    report = _import(guest, _zip({
        f"Top {n[0]}.md": f"# Top\n\n[One]({_enc(one[0])})\n\n[Two]({_enc(two[0])})\n",
        first[0]: first[1],
        second[0]: second[1],
        f"Sync {n[3]}.md": "# Sync\n\nYear: 2026\n",
        f"Sync {n[4]}.md": "# Sync\n\nNo year.\n",
    }))
    title = {p["id"]: p["title"] for p in report["pages"]}
    rows = {_subtree(guest, p["id"])["children"][0]["content"]: title[p["parent"]]
            for p in report["pages"] if p["title"] == "Sync"}
    assert rows == {"Year: 2026": "Two", "No year.": "One"}


def test_a_row_without_a_page_leaves_a_linked_sub_page_alone(guest):
    n = _nids("abd0")
    report = _import(guest, _zip({
        f"Top {n[0]}.md": f"# Top\n\n[C++]({_enc(f'C++ {n[1]}.md')})\n\n[Grades]({_enc(f'Grades {n[2]}.csv')})\n",
        f"C++ {n[1]}.md": "# C++\n\nA language.\n",
        f"Grades {n[2]}.csv": "Name,Points\nC,70\n",
    }))
    assert _parents(report) == {"Top": "", "C++": "Top", "Grades": "Top"}


def test_a_database_title_cut_at_50_characters(guest):
    """A CSV has no H1: the full title comes from the line linking to it,
    or the database's stub note."""
    n = _nids("abd1")
    long = "Vertically integrated proof markets (demand + supply side)"
    cut = long[:50]
    assert (cut + f" {n[1]}.csv").count(")") == 0
    report = _import(guest, _zip({
        f"Top {n[0]}.md": f"# Top\n\n[{long}]({_enc(f'{cut} {n[1]}.csv')})\n",
        f"{cut} {n[1]}.csv": "Name\nRow one\n",
        f"Row one {n[2]}.md": "# Row one\n",
    }))
    assert _parents(report) == {"Top": "", long: "Top", "Row one": long}
    stub = _import(guest, _zip({
        f"{cut} {n[3]}.md": f"# {long} stubbed\n\n[{long} stubbed]({_enc(f'{cut} {n[3]}_all.csv')})\n",
        f"{cut} {n[3]}_all.csv": "Name\nRow two\n",
    }))
    assert [p["title"] for p in stub["pages"]] == [f"{long} stubbed"]


def test_a_link_to_an_absent_csv_finds_the_page_of_its_id(guest):
    """A page's view CSV can be left out of the export: others' links reach
    the page of that id, its own link to it stays a warning."""
    n = _nids("abd2")
    report = _import(guest, _zip({
        f"Top {n[0]}.md": f"# Top\n\n[Library]({_enc(f'Library {n[1]}.csv')})\n",
        f"Library {n[1]}.md": f"# Library\n\n[Library]({_enc(f'Library {n[1]}.csv')})\n",
    }))
    ids = {p["title"]: p["id"] for p in report["pages"]}
    assert _parents(report) == {"Top": "", "Library": "Top"}
    assert _subtree(guest, ids["Top"])["children"][0]["content"] == f"[[{ids['Library']}]]"
    assert _subtree(guest, ids["Library"])["children"][0]["content"].startswith("[Library](")
    assert len(report["warnings"]) == 1


def test_asides_in_code_stay_and_in_toggles_stay_whole():
    convert = notion_import._convert_asides
    fenced = "```html\n<aside>\nnote\n</aside>\n```"
    assert convert(fenced) == fenced
    assert convert("Use `<aside>` and `</aside>` always.") == "Use `<aside>` and `</aside>` always."
    assert convert("- Toggle\n\n    <aside>\n    💡\n\n    Inside.\n\n    </aside>\n- Next") == (
        "- Toggle\n\n    > [!info] 💡\n    > Inside.\n- Next")
    assert convert("<aside>\nnever closed") == "<aside>\nnever closed"


def test_a_callout_or_quote_in_a_toggle_is_one_child():
    def shape(text):
        return [(b["content"], [c["content"] for c in b["children"]]) for b in md_to_blocks(text)]

    notion = "- Toggle\n    \n    <aside>\n    💡\n    \n    Callout text.\n    \n    Second para.\n    \n    </aside>\n    \n- Next\n"
    assert shape(notion_import._convert_asides(notion)) == [
        ("- Toggle", ["> [!info] 💡\n> Callout text.\n>\n> Second para."]), ("- Next", [])]
    assert shape("- Toggle\n\n    First para.\n\n    > Quote one\n    > quote two\n\n    Last para.\n") == [
        ("- Toggle", ["First para.", "> Quote one\n> quote two", "Last para."])]
    # a fence opened right below a child paragraph is a child too, after it
    assert shape("- Item\n\n    Para.\n    ```py\n    x = 1\n    ```\n") == [
        ("- Item", ["Para.", "```py\nx = 1\n```"])]


def test_only_notion_notes_have_their_asides_converted(guest):
    report = _import(guest, _zip({"Plain abd3.md": "# Plain abd3\n\n<aside>\nA real HTML element.\n</aside>\n"}))
    content = _subtree(guest, report["pages"][0]["id"])["children"][0]["content"]
    assert content == "<aside>\nA real HTML element.\n</aside>"


def test_vault_heading_links_match_as_obsidian_does(guest):
    report = _import(guest, _zip({
        "PunctVault/.obsidian/app.json": "{}",
        "PunctVault/Punct.md": "Punct intro.\n\n## 100% done: yes\n\nOne.\n\n## Learn C#\n\nTwo.\n",
        "PunctVault/Punct Home.md": "[[Punct#100% done yes]] and [[Punct#Learn C#]].\n",
    }))
    ids = {p["title"]: p["id"] for p in report["pages"]}
    heads = {c["content"]: c["id"] for c in _subtree(guest, ids["Punct"])["children"]}
    assert _subtree(guest, ids["Punct Home"])["children"][0]["content"] == (
        f"[[{heads['## 100% done: yes']}]] and [[{heads['## Learn C#']}]].")


def test_relations_to_emoji_titles():
    pages = {"1" * 32: "P1", "2" * 32: "P2"}
    text = f"| x | 🎉 (%F0%9F%8E%89%20{'1' * 32}.md), ✅ (%E2%9C%85%20{'2' * 32}.md) |"
    assert notion_import._link_relations(text, lambda nid, path: pages.get(nid)) == ("| x | [[P1]], [[P2]] |", 2)


def test_rows_of_one_name_sharing_every_value_stay_fast():
    import time

    class Plan:
        def __init__(self, path, title, rows=(), fields=None):
            self.entry = type("E", (), {"path": path})()
            self.page_id, self.title, self.rows, self.links = path, title, list(rows), []
            self.fields = fields or {}
            self.existing = False
    same = ["Standup", "Open", "Team", "Weekly", "Room"]
    db = Plan(f"Log {'f' * 32}.csv", "Log", [["Name", "A", "B", "C", "D"]] + [same] * 4000)
    fields = dict(zip("ABCD", same[1:]))
    notes = [Plan(f"Standup {i:032x}.md", "Standup", fields=dict(fields)) for i in range(4000)]
    t = time.perf_counter()
    tree = notion_import._flat_tree([db] + notes, lambda plan, href: None)
    assert time.perf_counter() - t < 5
    assert len(tree) == 4000


# --- a small zip stays small ---------------------------------------------------

def _peak(work) -> int:
    """The bytes ``work`` holds at its most."""
    tracemalloc.start()
    try:
        work()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _quickly(work, *args, seconds=2):
    start = time.perf_counter()
    result = work(*args)
    assert time.perf_counter() - start < seconds
    return result


def test_a_view_s_order_widens_no_row():
    """A wide header over thousands of one-cell rows, in the view's order:
    an 18 KB zip of this once held 518 MB."""
    data = (",".join(f"c{i}" for i in range(8000)) + "\n" + "x\n" * 8000).encode()
    held = []
    assert _peak(lambda: held.append(notion_import._csv_to_markdown(data, ["c7", "c3"]))) < 64_000_000
    table, cut = held[0]
    assert cut == 7960
    assert table.split("\n")[0].startswith("| c7 | c3 | c0 | c1 |")
    assert table.split("\n")[2].startswith("|   |   | x |   |") and table.split("\n")[2].count("|") == 41


def test_a_view_s_order_leaves_a_long_row_s_cells_after_the_header_s():
    table, cut = notion_import._csv_to_markdown(b"a,b\n1,2,3\n4\n", ["b", "a"])
    assert cut == 0 and table.split("\n") == ["| b | a |   |", "| --- | --- | --- |", "| 2 | 1 | 3 |", "|   | 4 |   |"]


@pytest.mark.parametrize("segment", ["a/", "a 0000-0000/"], ids=["plain", "partial id"])
@pytest.mark.parametrize("note", ["Note.md", f"{NOTION_HOME}.md"], ids=["plain", "notion"])
def test_one_long_entry_name_costs_its_length(guest, segment, note):
    """Directories 32,000 deep, each kept as a prefix, once took a gigabyte
    of a 125 KB zip."""
    name = "d0/" + segment * (64_000 // len(segment)) + "x.png"
    buf = _zip({note: "# Note\n", name: PNG})
    assert _peak(lambda: _quickly(_preview, guest, buf)) < 128_000_000


def test_a_directory_holding_only_directories_still_takes_its_page(guest):
    one, two = "bbbb3333000000000000000000003333", "cccc4444000000000000000000004444"
    report = _import(guest, _zip({
        "Recipes 0000bbbb0000bbbb0000bbbb0000bbbb.md": "# Recipes\n",
        f"Recipes/Mezze {one}.md": "# Mezze\n\nFirst.\n",
        f"Recipes/Mezze {two}.md": "# Mezze\n\nSecond.\n",
        f"Recipes/Mezze/Note {'a' * 32}.md": "# Note of the first\n",
        "Recipes/Mezze cccc-4444/deeper/still/x.png": PNG,
    }))
    page = {p["title"]: p for p in report["pages"]}
    parent = _subtree(guest, page["Note of the first"]["parent"])
    assert parent["properties"].get("notion_id") == one


@pytest.mark.parametrize("top", ["", "top/"], ids=["at the top", "in a directory"])
@pytest.mark.parametrize("beside, flat", [(None, True), ("d/e/x.png", False)], ids=["flat", "a directory of directories"])
def test_a_directory_of_directories_beside_the_pages_is_no_flat_export(guest, top, beside, flat):
    a, b = "cccc5555000000000000000000005555", "dddd6666000000000000000000006666"
    files = {f"{top}Flat {a}.md": f"# Flat\n\n[Under it](Under%20it%20{b}.md)\n", f"{top}Under it {b}.md": "# Under it\n"}
    if top:
        files["other.md"] = "# Other\n"
    if beside:
        files[f"{top}{beside}"] = PNG
    page = {p["title"]: p for p in _preview(guest, _zip(files))["pages"]}
    assert bool(page["Under it"]["parent"]) is flat


def _damaged(files: dict, name: str) -> io.BytesIO:
    """A zip whose member ``name`` fails its checksum, read to its end."""
    data = _zip(files).getvalue()
    good = files[name].encode("utf-8")
    assert data.count(good) == 1
    return io.BytesIO(data.replace(good, good[:-1] + b"#"))


@pytest.mark.parametrize("extra", [{}, {f"{NOTION_HOME}.md": "# Home\n"}], ids=["plain", "notion"])
def test_an_unreadable_html_nothing_links_fails_nothing(guest, extra):
    files = {"Readable note.md": "# Readable note\n\ntext\n", "broken.html": "<html><body>nothing to see</body></html>",
             **extra}
    report = _preview(guest, _damaged(files, "broken.html"))
    assert "Readable note" in [p["title"] for p in report["pages"]]
    assert _reasons(report)["broken.html"] == "this file could not be read"
    assert "Readable note" in [p["title"] for p in _import(guest, _damaged(files, "broken.html"))["pages"]]


@pytest.mark.parametrize("name", ["broken.md", f"{NOTION_DB}.csv"], ids=["note", "database"])
def test_an_unreadable_note_or_table_fails_nothing(guest, name):
    files = {"Readable note.md": "# Readable note\n\ntext\n", name: "Name,Status\nWrite,Done\n"}
    for report in (_preview(guest, _damaged(files, name)), _import(guest, _damaged(files, name))):
        assert [p["title"] for p in report["pages"]] == ["Readable note"]
        assert _reasons(report)[name] == "this file could not be read"


@pytest.mark.parametrize("name, data", [("broken.html", "<html><body>nothing to see</body></html>"),
                                        ("broken.pdf", "%PDF-1.4 not much of one")], ids=["html", "pdf"])
def test_an_unreadable_file_a_note_links_keeps_the_link_as_written(guest, name, data):
    files = {"Linking note.md": f"# Linking note\n\n[it]({name})\n", name: data}
    report = _import(guest, _damaged(files, name))
    assert _reasons(report)[name] == "this file could not be read" and report["assets_stored"] == 0
    note = _subtree(guest, report["pages"][0]["id"])
    assert [node["content"] for node in note["children"]] == [f"[it]({name})"]


def test_an_unreadable_view_leaves_the_table_its_rows(guest):
    view = f"{NOTION_DB}.csv"
    files = {f"{NOTION_DB}_all.csv": "Name,Status\nWrite,Done\n", view: "Status,Name\nDone,Write\n"}
    report = _import(guest, _damaged(files, view))
    assert _reasons(report)[view] == "this file could not be read"
    table = _subtree(guest, report["pages"][0]["id"])["children"][0]["content"]
    assert table.split("\n")[0] == "| Name | Status |" and "Write" in table


def test_a_note_opening_with_blank_lines_keeps_them_unless_a_bibtex_block_follows():
    assert mzi._take_bibtex("\n\ntext\n") == (None, "\n\ntext\n")
    assert mzi._take_bibtex("\n\n```bibtex\n@a{b}\n```\n\ntext\n") == ("@a{b}", "text\n")


_OUTLINE_SHAPE = "title: What is X?\n---\n# What is X?\n\n- Intro\n  - a\n  - b\n    - c\n"


def _bullets(node):
    return [(c["content"], _bullets(c)) for c in node["children"]]


@pytest.mark.parametrize("front, back", [
    ("---\ngamma_id: abc\n", [("- Intro", [("- a", []), ("- b", [("- c", [])])])]),
    ("---\n", [("Intro", [("a", []), ("b", [("c", [])])])]),
], ids=["folder sync's file", "the readable export's"])
def test_a_file_of_folder_sync_keeps_its_bullets(guest, front, back):
    note = front + _OUTLINE_SHAPE
    page = _import(guest, _zip({"What is X.md": note}))["pages"][0]
    assert _bullets(_subtree(guest, page["id"])) == back
    r = guest.post("/api/import/markdown", files={"file": ("What is X.md", note + " ", "text/markdown")})
    assert r.status_code == 200, r.text
    assert _bullets(_subtree(guest, r.json()["block_id"])) == [("# What is X?", back)]


# --- a small note stays quick ----------------------------------------------------

@pytest.mark.parametrize("unit", ["](", "[a](", "[a](b", "[", "[a", "[[x]", "[a](<", "![a](", "[a\n", '[a](b "'])
@pytest.mark.parametrize("tail", ["", " )"], ids=["no close", "a close at the end"])
def test_a_body_of_unclosed_links_is_linear_work(unit, tail):
    body = unit * (300_000 // len(unit)) + tail
    links = markdown_zip_links
    for work in (notion_import._escape_tags, notion_import._link_lines,
                 lambda text: links.MD_LINKS_RE.sub("", text), lambda text: links.WRITTEN_LINKS_RE.sub("", text)):
        _quickly(work, body)


def test_a_run_of_hashes_in_a_wikilink_is_quick():
    assert not _quickly(markdown_zip_links._WIKILINK_RE.search, "[[" + "#" * 64 + "x")
    assert markdown_zip_links._WIKILINK_RE.search("[[Note#A#B|x]]").groups() == ("", "Note", "#A#B", "x")


def test_spaces_in_a_name_or_a_line_are_linear_work():
    spaces = " " * 300_000
    assert _quickly(markdown_zip_links._notion_id, spaces) is None
    assert _quickly(notion_import._PARTIAL_ID_RE.search, spaces) is None
    assert _quickly(markdown_zip_dialects._ANCHOR_END_RE.search, f"a{spaces}b") is None
    assert _quickly(markdown_zip_dialects._H1_RE.match, f"# a{spaces}b").group(1) == f"a{spaces}b"
    assert _quickly(mzi._HEADING_TEXT_RE.match, f"## a{spaces}b").group(1) == f"a{spaces}b"
    assert markdown_zip_dialects._H1_RE.match("# Title  ##  ").group(1) == "Title"


@pytest.mark.parametrize("line", ["#" + " " * 300_000, "# " + "\t" * 300_000, "#\t" + " " * 300_000],
                         ids=["spaces", "tabs", "both"])
def test_a_heading_of_only_spaces_is_no_heading_and_is_quick(line):
    assert _quickly(markdown_zip_dialects._H1_RE.match, line) is None
    assert _quickly(mzi._HEADING_TEXT_RE.match, line) is None


@pytest.mark.parametrize("name", ["Note.md", f"{NOTION_HOME}.md"], ids=["plain", "notion"])
def test_a_note_of_a_heading_of_only_spaces_is_quick(guest, name):
    report = _quickly(_preview, guest, _zip({name: "#" + " " * 300_000}), seconds=5)
    assert len(report["pages"]) == 1


@pytest.mark.parametrize("line, text", [
    ("#   Title", "Title"), ("#\tTitle\t", "Title"), ("# C#", "C#"), ("# Title  ##  ", "Title"),
    ("# a  b", "a  b"), ("# # x", "# x"),
])
def test_a_heading_s_text_ends_where_it_did(line, text):
    assert markdown_zip_dialects._H1_RE.match(line).group(1) == text
    assert mzi._HEADING_TEXT_RE.match(line).group(1) == text


def test_a_cell_of_spaces_is_linear_work():
    table, _ = _quickly(notion_import._csv_to_markdown, b'a\n"y' + b" " * 300_000 + b'z"\n')
    assert table.split("\n")[2] == f"| y{' ' * 300_000}z |"


def test_a_cell_s_lines_join_as_one_break_each():
    table, _ = notion_import._csv_to_markdown('a\n" x \n \n\t y\n z  w "\n'.encode())
    assert table.split("\n")[2] == "| x<br>y<br>z  w |"


def test_an_aside_s_first_line_of_open_tags_is_linear_work():
    tags = "<img " * 80_000
    assert _quickly(notion_import._convert_asides, f"<aside>\n{tags}\n</aside>") == f"> [!info] {tags.rstrip()}"


@pytest.mark.parametrize("first, after", [
    ("<img src=a>", ["> text"]),
    ("<img src=a><img src=b>", ["> text"]),
    ("<img src=a> word", [">", "> text"]),
    ("<img src=a", [">", "> text"]),
    ("<img src=a> <img src=b", [">", "> text"]),
])
def test_a_blank_after_a_first_line_of_only_an_icon_goes(first, after):
    converted = notion_import._convert_asides(f"<aside>\n{first}\n\ntext\n</aside>")
    assert converted.split("\n") == [f"> [!info] {first}", *after]


def test_relations_behind_a_run_of_spaces_are_linear_work():
    relation = f"a (a%20{'0' * 32}.md)"
    text = " " * 200_000 + ", ".join([relation] * 8000)
    assert _quickly(notion_import._link_relations, text, lambda nid, path: "page")[1] == 8000


def test_a_line_s_table_is_read_for_each_line():
    relation = f"a (a%20{'0' * 32}.md)"
    text = f"| {relation}, {relation} | x |\nName: {relation}, {relation}\n"
    assert notion_import._link_relations(text, lambda nid, path: "page") == (
        "| [[page]], [[page]] | x |\nName: [[page]], [[page]]\n", 4)


def test_backtick_runs_nothing_closes_are_quick():
    _quickly(notion_import._escape_tags, "".join("`" * n + " " for n in range(800, 0, -1)))


def test_unclosed_asides_are_linear_work():
    body = "<aside>\n" * 100_000
    assert _quickly(notion_import._convert_asides, body) == body


@pytest.mark.parametrize("line, escaped", [
    ("](a <b>", "](a \\<b>"),
    ("[x](a<b>c) <d>", "[x](a<b>c) \\<d>"),
    ("[x](a) ](b <c>", "[x](a) ](b \\<c>"),
    ("`a ) <b>` <c>", "`a ) <b>` \\<c>"),
    ("\\<a> <u>x</u> <img src=y>", "\\<a> <u>x</u> <img src=y>"),
])
def test_a_tag_escapes_past_a_link_s_target_whether_it_closes_or_not(line, escaped):
    assert notion_import._escape_tags(line) == escaped
