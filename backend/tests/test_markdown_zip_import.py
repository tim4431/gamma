"""Zips of Markdown notes → pages: Notion's Markdown & CSV export, Gamma's own
Markdown export round-tripped, and the list-continuation parser rule both rely
on."""

import io
import os
import shutil
import zipfile

import pytest

from gamma import import_zip, markdown_import, markdown_zip_import as mzi
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


def test_notion_export_becomes_pages_folders_mentions_and_uploads(guest):
    imports = make_folder(guest, "Imports")
    report = _import(guest, _notion_zip(), folder=imports)
    assert report["folder"] == imports
    assert report["notion"] is True
    assert report["pages_created"] == 4
    assert report["assets_stored"] == 1
    assert report["warnings"] == []
    by_title = {p["title"]: p for p in report["pages"]}
    assert set(by_title) == {"Home", "Sub page", "Tasks", "Write tests"}
    assert by_title["Home"]["folders"] == [["Imports"]]
    assert by_title["Sub page"]["folders"] == [["Imports", "Home"]]
    assert by_title["Tasks"]["folders"] == [["Imports", "Home"]]
    assert by_title["Write tests"]["folders"] == [["Imports", "Home", "Tasks"]]
    # the folders are blocks below the destination, the pages filed by id
    assert _filed(guest, by_title["Write tests"]["id"]) == [["Imports", "Home", "Tasks"]]
    made = folder_names(guest)
    assert all([p for p in made.values()].count(path) == 1
               for path in (["Imports"], ["Imports", "Home"], ["Imports", "Home", "Tasks"]))

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
    assert toggle["content"] == "Toggle heading"
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
    assert {p["title"]: p["folders"] for p in again["pages"]}["Write tests"] == [["Imports", "Home", "Tasks"]]
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
    monkeypatch.setattr(mzi, "TABLE_ROWS", 2)
    md, cut = mzi._csv_to_markdown(data)
    assert cut == 0
    assert [t.count("\n") - 1 for t in md.split("\n\n")] == [2, 2, 1]
    monkeypatch.setattr(mzi, "TABLE_ROWS", 500)
    monkeypatch.setattr(mzi, "TABLE_CHARS", 60)   # a row is 29 characters
    md, _ = mzi._csv_to_markdown(data)
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
        "item one\ncontinues item one",
        "code item\n```js\na();\n\nb();\n```",
        "toggle",
        "para item\n\nsecond paragraph of the item",
    ]
    assert [c["content"] for c in tree[0]["children"]] == ["nested"]
    assert [c["content"] for c in tree[2]["children"]] == ["child paragraph after a blank line"]
