"""Zips of Markdown notes → pages: Notion's Markdown & CSV export, Gamma's own
Markdown export round-tripped, and the list-continuation parser rule both rely
on."""

import io
import zipfile

from gamma.markdown_import import md_to_blocks
from conftest import folder_names, label_names, make_folder

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
NOTION_HOME = "Home 0123456789abcdef0123456789abcdef"
NOTION_SUB = "Sub page fedcba9876543210fedcba9876543210"
NOTION_DB = "Tasks 11112222333344445555666677778888"
NOTION_ROW = "Write tests aaaabbbbccccddddeeeeffff00001111"


def _zip(files: dict) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
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


def test_plain_zipped_folder_of_notes(guest):
    buf = _zip({
        "vault/daily/2026-09-01.md": "- woke up\n- [[wiki style]] stays as typed\n",
        "vault/projects/gamma.md": "---\ntitle: Gamma plans\n---\n# Gamma plans\n\nSee [daily](../daily/2026-09-01.md).\n",
    })
    report = _import(guest, buf)
    by_title = {p["title"]: p for p in report["pages"]}
    # the single common root ("vault") is dropped, the rest become folders
    assert by_title["2026-09-01"]["folders"] == [["daily"]]
    assert by_title["Gamma plans"]["folders"] == [["projects"]]
    assert _filed(guest, by_title["Gamma plans"]["id"]) == [["projects"]]
    plans = _subtree(guest, by_title["Gamma plans"]["id"])
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
