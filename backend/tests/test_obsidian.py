"""Obsidian: a zipped vault → pages (wikilinks, embeds, anchors, properties),
and pages → a vault (the ``obsidian`` export mode), including the round trip."""

import io
import itertools
import zipfile

import pytest

from conftest import folder_names, label_names, make_folder, make_label, make_page

from gamma.markdown_import import md_to_blocks, parse_frontmatter
from gamma.obsidian_export import VaultContext, render_vault_page, vault_name

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _blank_pdf_bytes():
    from PyPDF2 import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _zip(files: dict) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    buf.seek(0)
    return buf


def _import(client, buf, folder=""):
    r = client.post("/api/import/markdown-zip",
                    files={"file": ("vault.zip", buf.getvalue(), "application/zip")},
                    data={"folder": folder})
    assert r.status_code == 200, r.text
    return r.json()


def _subtree(client, page_id):
    return client.get(f"/api/blocks/{page_id}/subtree").json()["block"]


def _flat(node, out=None):
    out = [] if out is None else out
    for c in node["children"]:
        out.append(c)
        _flat(c, out)
    return out


def _put_children(client, page_id, children):
    r = client.put(f"/api/blocks/{page_id}/children", json={"blocks": children})
    assert r.status_code == 200, r.text


# --- import ------------------------------------------------------------------

def _vault():
    home = """---
tags:
  - project/alpha
  - reading
aliases: [Start, "Home page"]
---
# Home

Intro paragraph with a comment %%hidden%% inside.

%%
A whole block comment
%%

See [[Deep Note]] and [[deep note|the alias]] and [[Notes/Deep Note#Method]].
Block link: [[Deep Note#^key-result]] and embed ![[Deep Note#^key-result]].
Whole-note embed ![[Deep Note]] and section ![[Deep Note#Method]].
Same note: [[#Setup]].
Picture ![[diagram.png|300]] and sized ![[diagram.png|200x100]] and titled ![[diagram.png|A diagram]].
Paper [[paper.pdf#page=3]] and file [[notes.txt]].
Markdown link [Deep](Notes/Deep%20Note.md) and shortest [Deep](Deep%20Note.md#Method).
Missing [[Nowhere]] stays.

> [!faq]- Folded question
> the answer

## Setup

A paragraph. ^para-anchor

```python
x = "[[not a link]] %%not a comment%%"
```
"""
    deep = """# Different heading

The key result. ^key-result

## Method

Steps here.

- first bullet ^bullet-id
- second
  - nested

^after-list
"""
    return _zip({
        "MyVault/.obsidian/app.json": "{}",
        "MyVault/.obsidian/plugins/x/README.md": "# not a note",
        "MyVault/.trash/Old.md": "gone",
        "MyVault/Home.md": home,
        "MyVault/Notes/Deep Note.md": deep,
        "MyVault/attachments/diagram.png": PNG,
        "MyVault/attachments/paper.pdf": PDF,
        "MyVault/attachments/notes.txt": "hello",
        "MyVault/Board.canvas": "{}",
    })


def test_vault_import_pages_links_embeds_and_properties(guest):
    report = _import(guest, _vault(), folder=make_folder(guest, "obs"))
    assert report["obsidian"] is True
    assert report["pages_created"] == 2
    assert any("canvas" in w["reason"] for w in report["warnings"])
    titles = {p["title"]: p for p in report["pages"]}
    assert set(titles) == {"Home", "Deep Note"}          # filename is the title
    assert titles["Deep Note"]["folders"] == [["obs", "Notes"]]

    home = _subtree(guest, titles["Home"]["id"])
    deep = _subtree(guest, titles["Deep Note"]["id"])
    assert [folder_names(guest)[f] for f in deep["properties"]["folders"]] == [["obs", "Notes"]]
    # tags are labels by name ("/" is part of a name)
    labels = label_names(guest)
    assert [labels[i] for i in home["properties"]["labels"]] == ["project/alpha", "reading"]
    assert home["properties"]["aliases"] == ["Start", "Home page"]

    deep_blocks = _flat(deep)
    by_content = {b["content"].split("\n")[0]: b for b in deep_blocks}
    # The H1 differs from the filename, so it stays in the body.
    assert "# Different heading" in by_content
    key = by_content["The key result."]                    # anchor stripped
    method = by_content["## Method"]
    first = by_content["- first bullet"]
    second = by_content["- second"]
    assert "^" not in key["content"] and "^" not in first["content"]
    assert not any(b["content"].startswith("^") for b in deep_blocks)

    home_blocks = _flat(home)
    text = "\n".join(b["content"] for b in home_blocks)
    did = deep["id"]
    assert "hidden" not in text and "whole block comment" not in text.lower()
    assert "Intro paragraph with a comment  inside." in text
    assert f"See [[{did}]] and [[{did}]] and [[{method['id']}]]." in text
    assert f"Block link: [[{key['id']}]] and embed ![[{key['id']}]]." in text
    assert f"Whole-note embed [[{did}]] and section [[{method['id']}]]." in text
    setup = next(b for b in home_blocks if b["content"] == "## Setup")
    assert f"Same note: [[{setup['id']}]]." in text
    assert "![|300](/api/uploads/" in text and "![|200x100](/api/uploads/" in text
    assert "![A diagram](/api/uploads/" in text
    assert "[paper.pdf](/api/uploads/" in text and ".pdf)" in text
    assert "[notes.txt](/api/uploads/" in text
    assert f"Markdown link [[{did}]] and shortest [[{method['id']}]]." in text
    assert "[[Nowhere]] stays" in text
    assert "> [!faq]- Folded question" in text               # the fold flag renders
    para = next(b for b in home_blocks if b["content"] == "A paragraph.")
    assert para["parent_id"] == setup["id"]
    fence = next(b for b in home_blocks if b["content"].startswith("```python"))
    assert "[[not a link]] %%not a comment%%" in fence["content"]
    # Lists nest under the nearest heading; the anchor line after the list
    # belongs to the block before it (and was dropped as a block).
    assert second["parent_id"] == method["id"] and first["parent_id"] == method["id"]
    assert report["assets_stored"] == 3
    assert "# Home" not in text                              # H1 repeats the title


def test_vault_import_is_idempotent(guest):
    """The same vault (same bytes) imported again adds nothing — the pages
    were already created by the test above, this import is the repeat."""
    again = _import(guest, _vault(), folder=make_folder(guest, "obs2"))
    assert again["pages_created"] == 0 and again["pages_skipped"] == 2


def test_frontmatter_lists_and_fold_markers():
    fm, body = parse_frontmatter("---\ntags:\n  - a\n  - b\nx: [1, 2]\ns: 'q'\n---\nrest")
    assert fm == {"tags": ["a", "b"], "x": ["1", "2"], "s": "q"} and body == "rest"
    # The editor folds a callout by its flag, so the flag stays.
    assert md_to_blocks("> [!tip]+ T\n> b")[0]["content"] == "> [!tip]+ T\n> b"


# --- export ------------------------------------------------------------------

def _highlight(hid, quote, note="", page=1):
    return {
        "id": hid, "content": note, "children": [],
        "properties": {
            "quote": quote, "color": "rgba(255, 226, 143, 0.65)", "pdf_position": {"pageNumber": page},
        },
    }


def test_vault_name_strips_obsidian_forbidden_characters():
    assert vault_name('A: "b" [c] #d ^e | f/g') == "A b c d e  f g".replace("  ", " ")
    assert vault_name(".hidden.") == "hidden"
    assert vault_name("") == "Untitled"


def test_obsidian_export_writes_a_vault(guest):
    img = guest.post("/api/upload-image", files={"file": ("d.png", PNG, "image/png")}).json()["url"]
    target = make_page(guest, "Target: note", properties={
        "folders": [make_folder(guest, "ov/sub")], "labels": [make_label(guest, "read"), make_label(guest, "later")]})
    _put_children(guest, target["id"], [
        {"id": "ot-h", "content": "## Findings", "properties": {}, "children": [
            {"id": "ot-p", "content": "a shared finding\nsecond line", "properties": {}, "children": [
                {"id": "ot-c", "content": "child bullet", "properties": {}, "children": []},
            ]},
        ]},
        {"id": "ot-f", "content": "```py\nx = 1\n```", "properties": {}, "children": []},
    ])
    ov = make_folder(guest, "ov")
    src = make_page(guest, "Source", properties={"folders": [ov], "aliases": ["S"]})
    _put_children(guest, src["id"], [
        {"id": "os-1", "content": f"see [[ot-p]] and page [[{target['id']}]] and code [[ot-f]]",
         "properties": {}, "children": []},
        {"id": "os-2", "content": "synced: ![[ot-p]]", "properties": {}, "children": []},
        {"id": "os-3", "content": f"pic ![cap|300]({img})", "properties": {}, "children": []},
        {"id": "os-4", "content": "> [!note] kept\n> body", "properties": {}, "children": []},
    ])

    r = guest.get(f"/api/folders/{ov}/export", params={"mode": "obsidian"})
    assert r.status_code == 200, r.text
    assert "ov-obsidian.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert "Source.md" in names and "sub/Target note.md" in names
    assert f"attachments/{img.rsplit('/', 1)[1]}" in names
    assert ".obsidian/app.json" in names

    t = z.read("sub/Target note.md").decode()
    assert t.startswith("---\ntitle: \"Target: note\"\ntags:\n  - read\n  - later\n---\n")
    assert "# Target" not in t                              # no H1: the filename is the title
    # A block with children is a list item (the anchor on its bullet line),
    # a leaf block a paragraph, a fence's anchor sits on its own line.
    assert "## Findings\n\n- a shared finding ^ot-p\n  second line\n  - child bullet\n" in t
    assert "```py\nx = 1\n```\n\n^ot-f\n" in t

    s = z.read("Source.md").decode()
    assert "aliases:\n  - S\n" in s
    assert "see [[Target note#^ot-p]] and page [[Target note]] and code [[Target note#^ot-f]]" in s
    assert "synced: ![[Target note#^ot-p]]" in s
    assert f"pic ![cap|300](attachments/{img.rsplit('/', 1)[1]})" in s
    assert "> [!note] kept\n> body" in s


def test_obsidian_export_bundles_pdf_and_links_highlight_pages(guest):
    up = guest.post("/api/uploads", files={"file": ("paper.pdf", _blank_pdf_bytes(), "application/pdf")})
    assert up.status_code == 200, up.text
    props = {"doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"],
             "folders": [make_folder(guest, "ovp/inner")]}
    page_id = make_page(guest, "Paper", properties=props)["id"]
    _put_children(guest, page_id, [
        _highlight("oh-1", "quoted text\nsecond", note="my note", page=3) | {"children": [
            {"id": "oh-1c", "content": "under the note", "properties": {}, "children": []}]},
        {"id": "op-1", "content": "top paragraph", "properties": {}, "children": [
            {"id": "op-1a", "content": _highlight("oh-2", "nested quote", page=5)["content"],
             "properties": _highlight("oh-2", "nested quote", page=5)["properties"], "children": []},
        ]},
    ])
    r = guest.get(f"/api/pages/{page_id}/export", params={"mode": "obsidian"})
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "attachments/Paper.pdf" in z.namelist()
    # A single page keeps its folder's whole path as the directory.
    md = z.read("ovp/inner/Paper.md").decode()
    assert 'source: "[[Paper.pdf]]"' in md
    assert "> [!quote] [[Paper.pdf#page=3|p. 3]]\n> quoted text\n> second\n\nmy note\n\n- under the note\n" in md
    assert "- top paragraph\n  - > nested quote\n    [[Paper.pdf#page=5|p. 5]]\n" in md

    # Without the bundle: page markers only, and the source stays a URL.
    r = guest.get(f"/api/pages/{page_id}/export", params={"mode": "obsidian", "pdf": 0})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    md = z.read("ovp/inner/Paper.md").decode()
    assert "attachments/Paper.pdf" not in z.namelist()
    assert "> [!quote] p. 3\n> quoted text" in md and "[[Paper.pdf" not in md
    assert f"source: {props['source_url']}" in md

    # Dropping highlights keeps the note as a plain block (with its child).
    r = guest.get(f"/api/pages/{page_id}/export", params={"mode": "obsidian", "highlights": 0})
    md = zipfile.ZipFile(io.BytesIO(r.content)).read("ovp/inner/Paper.md").decode()
    assert "[!quote]" not in md and "\n- my note\n  - under the note\n" in md


def test_obsidian_export_writes_text_boxes_as_notes_with_their_page(guest):
    """A text box is a note, never a [!quote]: on a PDF page it is followed
    by the page link (its own paragraph at the top level, a line under the
    bullet in a list); on a sheet it is just the note."""
    up = guest.post("/api/uploads", files={"file": ("boxes.pdf", _blank_pdf_bytes(), "application/pdf")})
    assert up.status_code == 200, up.text
    props = {"doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"],
             "folders": [make_folder(guest, "ovb")]}
    page_id = make_page(guest, "Boxed", properties=props)["id"]
    box = {"x": 40, "y": 60, "w": 180}
    _put_children(guest, page_id, [
        {"id": "ob-1", "content": "- typed list\n- on the paper", "children": [],
         "properties": {"text_box": box, "pdf_page": 2}},
        {"id": "ob-2", "content": "a thread", "properties": {}, "children": [
            {"id": "ob-2a", "content": "moved under it", "children": [],
             "properties": {"text_box": box, "pdf_page": 4}},
        ]},
        {"id": "ob-s", "content": "", "properties": {"sheet": {}}, "children": [
            {"id": "ob-3", "content": "typed on the sheet", "children": [],
             "properties": {"text_box": box}},
            # a stale pdf_page under a sheet: the sheet holds it
            {"id": "ob-4", "content": "moved onto the sheet", "children": [],
             "properties": {"text_box": box, "pdf_page": 5}},
        ]},
    ])
    r = guest.get(f"/api/pages/{page_id}/export", params={"mode": "obsidian"})
    assert r.status_code == 200, r.text
    md = zipfile.ZipFile(io.BytesIO(r.content)).read("ovb/Boxed.md").decode()
    assert "- typed list\n- on the paper\n\n[[Boxed.pdf#page=2|p. 2]]\n" in md
    assert "- a thread\n  - moved under it\n    [[Boxed.pdf#page=4|p. 4]]\n" in md
    assert "typed on the sheet\n" in md and "#page=" not in md.split("typed on the sheet")[1]
    assert "moved onto the sheet" in md and "page=5" not in md
    assert "[!quote]" not in md

    r = guest.get(f"/api/pages/{page_id}/export", params={"mode": "obsidian", "pdf": 0, "highlights": 0})
    md = zipfile.ZipFile(io.BytesIO(r.content)).read("ovb/Boxed.md").decode()
    assert "- on the paper\n\np. 2\n" in md and "    p. 4\n" in md


def test_obsidian_export_disambiguates_same_titles(guest):
    ovt = make_folder(guest, "ovt")
    a = make_page(guest, "Twin", properties={"folders": [make_folder(guest, "ovt/one")]})
    b = make_page(guest, "Twin", properties={"folders": [make_folder(guest, "ovt/two")]})
    c = make_page(guest, "Twin", properties={"folders": [make_folder(guest, "ovt/two")]})
    src = make_page(guest, "Links", properties={"folders": [ovt]})
    _put_children(guest, src["id"], [
        {"id": "ol-1", "content": f"[[{a['id']}]] [[{b['id']}]] [[{c['id']}]]",
         "properties": {}, "children": []},
    ])
    r = guest.get(f"/api/folders/{ovt}/export", params={"mode": "obsidian"})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"one/Twin.md", "two/Twin.md", "two/Twin 2.md", "Links.md"} <= set(z.namelist())
    assert "[[one/Twin]] [[two/Twin]] [[Twin 2]]" in z.read("Links.md").decode()


def test_obsidian_round_trip(guest):
    """Export a folder as a vault, import the zip into another folder: the
    same titles, tree, synced block and labels come back."""
    img = guest.post("/api/upload-image", files={"file": ("d.png", PNG, "image/png")}).json()["url"]
    ovr = make_folder(guest, "ovr")
    a = make_page(guest, "Round A", properties={"folders": [ovr], "labels": [make_label(guest, "t1")]})
    b = make_page(guest, "Round B", properties={"folders": [make_folder(guest, "ovr/deep")]})
    _put_children(guest, a["id"], [
        {"id": "ra-h", "content": "# Heading", "properties": {}, "children": [
            {"id": "ra-p", "content": "first line\nsecond line", "properties": {}, "children": []},
            {"id": "ra-c", "content": "```python\nx = 1\n\ny = 2\n```", "properties": {}, "children": []},
            {"id": "ra-i", "content": f"![pic|200]({img})", "properties": {}, "children": []},
        ]},
        {"id": "ra-l", "content": f"see [[{b['id']}]] and ![[rb-1]]", "properties": {}, "children": [
            {"id": "ra-m", "content": "$$\na = b\n$$", "properties": {}, "children": []},
        ]},
    ])
    _put_children(guest, b["id"], [
        {"id": "rb-1", "content": "b note", "properties": {}, "children": []},
    ])
    r = guest.get(f"/api/folders/{ovr}/export", params={"mode": "obsidian"})
    assert r.status_code == 200, r.text
    restored = make_folder(guest, "restored")
    report = _import(guest, io.BytesIO(r.content), folder=restored)
    assert report["pages_created"] == 2 and report["warnings"] == []
    by_title = {p["title"]: p for p in report["pages"]}
    assert by_title["Round B"]["folders"] == [["restored", "deep"]]
    new_a = _subtree(guest, by_title["Round A"]["id"])
    new_b = _subtree(guest, by_title["Round B"]["id"])
    assert new_a["properties"]["folders"] == [restored]
    assert new_a["properties"]["labels"] == [make_label(guest, "t1")]  # the same label, by name
    b_note = new_b["children"][0]
    # Document style is lossy in two ways: what follows a heading belongs to
    # it on re-import, and a plain block with children, written as a list
    # item, comes back a bullet. Everything else — nesting, multi-line blocks,
    # the mention, the synced block, the sized image — comes back as it was.
    heading = new_a["children"][0]
    assert [c["content"] for c in new_a["children"]] == ["# Heading"]
    assert [(c["content"], [g["content"] for g in c["children"]]) for c in heading["children"]] == [
        ("first line\nsecond line", []),
        ("```python\nx = 1\n\ny = 2\n```", []),
        (f"![pic|200]({img})", []),
        (f"- see [[{new_b['id']}]] and ![[{b_note['id']}]]", ["$$\na = b\n$$"]),
    ]


# --- a vault read back with its markers kept ----------------------------------

def _block(content, *children):
    return {"content": content, "children": list(children)}


def _vault_text(tree) -> str:
    """The note the vault export writes for a page of the blocks ``tree``."""
    ids = itertools.count()

    def with_ids(nodes):
        return [{**node, "id": f"b{next(ids)}", "children": with_ids(node["children"])} for node in nodes]

    return render_vault_page({"id": "page", "content": "Cycle", "children": with_ids(tree)}, VaultContext(None))


@pytest.mark.parametrize("child", [
    _block("x\ny"),
    _block("- x\n  y"),
    _block("1. x\n   y"),
    _block("- [ ] x\n  y"),
    _block("- a\n- b"),
    _block("```py\nx\n\ny\n```"),
    _block("- a\n  ```py\n  x\n  ```"),
    _block("a\n```py\nx\n```"),
    _block("> a\n> b"),
    _block("> [!tip] t\n> b"),
    _block("$$\nx\n$$"),
    _block("$$x$$"),
    _block("text\n$$\nx\n$$"),
    _block("- a", _block("- b", _block("1. c")), _block("- [ ] d")),
    _block("1. a", _block("- b")),
    _block("10. a", _block("b")),
    _block("a", _block("b", _block("c\nd"))),
    _block("- a\nb"),
    _block("- ```x``` y", _block("- z")),
    _block("- a", _block("- b\n- c")),
    _block("- a\n```py\ndef f():\n    return 1\n- b\n```"),
    _block("- a\n$$\n- x\n$$"),
    _block("````\n```\n- x\n```\n````"),
    _block("- a\n~~~~\n~~~\n- x\n~~~\n~~~~"),
    _block("- "),
    _block("1. ", _block("- ")),
    _block("---"),
], ids=lambda child: child["content"][:12])
def test_a_vault_written_block_stops_changing_after_one_cycle(child):
    """Blocks are read back with their markers kept and written again: what
    the first cycle gives, the next give too, so a block gains no indent or
    marker however often a vault goes round."""
    tree, texts = [_block("p", child)], []
    for _ in range(3):
        texts.append(_vault_text(tree))
        tree = md_to_blocks(texts[-1])
    assert texts[1:] == [texts[1]] * 2
    assert md_to_blocks(texts[2]) == tree


def test_a_wrapped_block_comes_back_exactly():
    tree = [_block("- p", _block("- x\n  y"), _block("- [ ] x\n  y"), _block("1. x\n   y"), _block("$$\nx\n$$"))]
    assert md_to_blocks(_vault_text(tree)) == tree


@pytest.mark.parametrize("content,under", [("- x", "  "), ("1. x", "   "), ("10) x", "    "), ("- [ ] x", "  ")])
def test_a_nested_list_block_writes_one_marker_and_its_children_at_its_text(content, under):
    text = _vault_text([_block("p", _block(content, _block("kid")))])
    assert f"\n  {content}\n  {under}- kid\n" in text


def test_a_block_with_no_marker_of_its_own_is_written_as_before():
    text = _vault_text([_block("p", _block("x\ny", _block("> q")), _block("2 x"), _block("-x"), _block("12345. x"))])
    assert "- p\n  - x\n    y\n    - > q\n  - 2 x\n  - -x\n  - 12345. x\n" in text


def test_a_list_block_s_next_lines_stay_in_it():
    tree = [_block("p", _block("- a\n  b"), _block("1. a\n   b"), _block("- a\n- b"), _block("- a\nb"))]
    assert "- p\n  - a\n    b\n  1. a\n     b\n  - a\n  - b\n  - a\n    b\n" in _vault_text(tree)


@pytest.mark.parametrize("code", [
    "```py\ndef f():\n    return 1\n- b\n\n1. c\n```",
    "~~~\n    x\n- b\n~~~",
    "$$\n- x + y &= 3 \\\\\n1. & z\n$$",
    "````\n```\n- b\n```\n- c\n````",
], ids=["fence", "tilde", "math", "long fence"])
def test_a_list_block_s_code_moves_under_its_marker_whole(code):
    """Indented or list-shaped lines of a fence at a list block's left edge
    go with the fence: they stay in the item, and the same code comes back."""
    text = _vault_text([_block("p", _block("- a\n" + code))])
    assert "\n  - a\n" + "\n".join(f"    {line}" for line in code.split("\n")) + "\n" in text
    tree = md_to_blocks(text)
    assert tree == [_block("- p", _block("- a", _block(code)))]
    assert md_to_blocks(_vault_text(tree)) == tree


def test_a_top_level_list_block_moves_its_fence_too():
    text = _vault_text([_block("- a\n```py\n    x\n- y\n```", _block("kid"))])
    assert "- a\n  ```py\n      x\n  - y\n  ```\n  - kid\n" in text


def test_inline_code_opens_no_fence_in_a_list_block():
    assert "- p\n  - a\n    ```x``` y\n  - b\n" in _vault_text([_block("p", _block("- a\n```x``` y\n- b"))])


def test_the_lines_after_a_list_block_s_fence_are_laid_out_again():
    assert "- p\n  - a\n    ```\n    - x\n    ```\n  - b\n" in _vault_text([_block("p", _block("- a\n```\n- x\n```\n- b"))])


def test_a_text_box_s_page_line_stays_under_its_marker():
    box = {**_block("1. x"), "properties": {"text_box": {"x": 1, "y": 1}, "pdf_page": 3}}
    assert "- p\n  1. x\n     p. 3\n" in _vault_text([_block("p", box)])


# --- round trips through a zip ------------------------------------------------

_CYCLES = itertools.count()


def _new_folder(client):
    return make_folder(client, f"cycle {next(_CYCLES)}")


def _exported(client, folder, mode):
    r = client.get(f"/api/folders/{folder}/export", params={"mode": mode})
    assert r.status_code == 200, r.text
    return r.content


def _notes(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return sorted(zf.read(name) for name in zf.namelist() if name.endswith(".md"))


def _cycles(client, folder, mode):
    """The folder of one page through an export and an import into a new
    folder, over and over: the page each cycle brings back, up to the one
    that writes the file it read, which any further cycle would too (and
    which an import skips). Three is as many as a page may take."""
    pages = []
    data = _exported(client, folder, mode)
    for _ in range(3):
        folder = _new_folder(client)
        pages += _import(client, io.BytesIO(data), folder=folder)["pages"]
        data, read = _exported(client, folder, mode), data
        if _notes(data) == _notes(read):
            return pages
    pytest.fail("the page is still changing")


def _page_of(client, title, blocks):
    """A folder holding a page of ``blocks``, as ``_put_children`` takes them
    with ids made up: strings, or ``(content, children)``."""
    ids = itertools.count()

    def nodes(items):
        return [{"id": f"c{next(_CYCLES)}-{next(ids)}", "content": item if isinstance(item, str) else item[0],
                 "properties": {}, "children": [] if isinstance(item, str) else nodes(item[1])} for item in items]
    folder = _new_folder(client)
    page = make_page(client, title, properties={"folders": [folder]})
    _put_children(client, page["id"], nodes(blocks))
    return folder


@pytest.mark.parametrize("blocks,back", [
    (["---", "important text", "---", "tail"], ["***", "important text", "---", "tail"]),
    (["---", "title: Hijacked", "---", "tail"], ["***", "title: Hijacked", "---", "tail"]),
    (["", "---", "folder: x", "---", "tail"], ["***", "folder: x", "---", "tail"]),
    (["---\ntitle: Hijacked\n---\ntail"], ["***\ntitle: Hijacked\n---\ntail"]),
    (["---  \ntitle: Hijacked\n---\nend"], ["***\ntitle: Hijacked\n---\nend"]),
], ids=["text", "hijack", "blank first block", "one block", "spaces after it"])
def test_a_vault_page_that_opens_with_a_divider_keeps_what_follows_it(guest, blocks, back):
    for page in _cycles(guest, _page_of(guest, "Opens divided", blocks), "obsidian"):
        assert page["title"] == "Opens divided"
        assert [c["content"] for c in _subtree(guest, page["id"])["children"]] == back


def test_front_matter_leaves_a_page_s_opening_divider_as_it_is():
    children = [{**_block(text), "id": text} for text in ("---", "x")]
    page = {"id": "page", "content": "Tagged", "children": children}
    assert render_vault_page(page, VaultContext(None), tags=["t"]) == "---\ntags:\n  - t\n---\n\n---\n\nx\n"
    assert _vault_text([_block("---"), _block("x")]) == "***\n\nx\n"
    assert _vault_text([_block("x"), _block("---")]) == "x\n\n---\n"


@pytest.mark.parametrize("mode", ["obsidian", "readable"])
@pytest.mark.parametrize("title", ['Say "hi" [now]', "back\\slash: x", "'Tis", 'Say "hi"'])
def test_a_title_with_quotes_stays_as_it_is_every_cycle(guest, title, mode):
    for page in _cycles(guest, _page_of(guest, title, ["x"]), mode):
        assert page["title"] == title


def test_a_vault_title_in_quotes_keeps_them_every_cycle(guest):
    for page in _cycles(guest, _page_of(guest, '"double"', ["x"]), "obsidian"):
        assert page["title"] == '"double"'


def test_a_readable_title_in_quotes_loses_them_once(guest):
    """The readable export writes a title bare, so ``title: "double"`` is a
    quoted value: what it loses it loses once, and no backslash comes."""
    for page in _cycles(guest, _page_of(guest, '"double"', ["x"]), "readable"):
        assert page["title"] == "double"


@pytest.mark.parametrize("mode", ["obsidian", "readable", "notion"])
@pytest.mark.parametrize("code,inner", [
    ("````md\n```\ninner\n```\n````", "```\ninner\n```"),
    ("~~~~\n~~~\ninner\n~~~\n~~~~", "~~~\ninner\n~~~"),
], ids=["ticks", "tildes"])
def test_a_long_fence_comes_back_whole_from_a_zip(guest, mode, code, inner):
    folder = _page_of(guest, "Long fence", [code, "after", ("host", [code, "- next"])])
    for page in _cycles(guest, folder, mode):
        blocks = [node["content"] for node in _flat(_subtree(guest, page["id"]))]
        assert sum(inner in content for content in blocks) == 2 and "inner" not in blocks
        assert "after" in blocks and "- next" in blocks


@pytest.mark.parametrize("mode", ["obsidian", "readable"])
def test_an_empty_list_item_comes_back_empty(guest, mode):
    folder = _page_of(guest, "Empty items", ["- ", "1. ", "x", ("host", ["- "]), "-", "- "])
    for page in _cycles(guest, folder, mode):
        blocks = [node["content"] for node in _flat(_subtree(guest, page["id"]))]
        assert blocks.count("- ") == 3 and blocks.count("1. ") == 1 and blocks.count("-") == 1


@pytest.mark.parametrize("mode", ["obsidian", "readable"])
@pytest.mark.parametrize("todo", ["- [ ] ", "- [x] "])
def test_an_empty_todo_stays_a_todo(guest, mode, todo):
    folder = _page_of(guest, "Empty todos", [todo, "x", ("host", [todo]), todo])
    for page in _cycles(guest, folder, mode):
        blocks = [node["content"] for node in _flat(_subtree(guest, page["id"]))]
        assert blocks.count(todo) == 3


@pytest.mark.parametrize("mode,fence", [("obsidian", "```"), ("notion", "````")])  # (Notion's widens)
def test_code_holding_an_indented_fence_comes_back_whole(guest, mode, fence):
    code = "py\ndef f():\n    \"\"\"\n    ```\n    x\n    ```\n    \"\"\"\n"
    for page in _cycles(guest, _page_of(guest, "Indented fence", [f"```{code}```", "after"]), mode):
        assert [c["content"] for c in _subtree(guest, page["id"])["children"]] == [f"{fence}{code}{fence}", "after"]


@pytest.mark.parametrize("block,back", [
    ("intro\n```\ncode\n```", ["intro", "```\ncode\n```"]),
    ("intro\n````\n```\n````", ["intro", "````\n```\n````"]),
    ("intro\n~~~\ncode\n~~~", ["intro", "~~~\ncode\n~~~"]),
], ids=["ticks", "long", "tildes"])
def test_a_linked_block_ending_in_a_fence_leaves_the_page_after_it(guest, block, back):
    kid = f"fenced{next(_CYCLES)}"
    folder = _new_folder(guest)
    page = make_page(guest, "Fence last", properties={"folders": [folder]})
    _put_children(guest, page["id"], [
        {"id": kid, "content": block, "properties": {}, "children": []},
        {"id": f"{kid}-link", "content": f"see [[{kid}]]", "properties": {}, "children": []},
        {"id": f"{kid}-head", "content": "# Heading", "properties": {}, "children": []},
        {"id": f"{kid}-list", "content": "- a list", "properties": {}, "children": []},
    ])
    data = _exported(guest, folder, "obsidian")
    page = _import(guest, io.BytesIO(data), folder=_new_folder(guest))["pages"][0]
    children = _subtree(guest, page["id"])["children"]
    assert [c["content"] for c in children[:2]] == back
    assert children[2]["content"] == f"see [[{children[1]['id']}]]"
    assert [(c["content"], [g["content"] for g in c["children"]]) for c in children[3:]] == [("# Heading", ["- a list"])]


def test_a_linked_heading_with_a_fence_under_it_leaves_the_page_after_it(guest):
    kid = f"fenced{next(_CYCLES)}"
    folder = _new_folder(guest)
    page = make_page(guest, "Fence last", properties={"folders": [folder]})
    _put_children(guest, page["id"], [
        {"id": kid, "content": "# Head\n```\ncode\n```", "properties": {}, "children": []},
        {"id": f"{kid}-link", "content": f"see [[{kid}]]", "properties": {}, "children": []},
        {"id": f"{kid}-next", "content": "# Next", "properties": {}, "children": []},
    ])
    data = _exported(guest, folder, "obsidian")
    page = _import(guest, io.BytesIO(data), folder=_new_folder(guest))["pages"][0]
    head, last = _subtree(guest, page["id"])["children"]
    code, link = head["children"]
    assert (head["content"], code["content"], link["content"]) == ("# Head", "```\ncode\n```", f"see [[{code['id']}]]")
    assert last["content"] == "# Next"


def _linked_page(client, child):
    """A folder of a page whose block ``child``, under ``host``, a sibling
    mentions; and that block's id."""
    kid = f"kid{next(_CYCLES)}"
    folder = _new_folder(client)
    page = make_page(client, "Linked", properties={"folders": [folder]})
    node = {"id": kid, "content": child, "properties": {}, "children": []}
    _put_children(client, page["id"], [
        {"id": f"{kid}-host", "content": "host", "properties": {}, "children": [node]},
        {"id": f"{kid}-link", "content": f"see [[{kid}]]", "properties": {}, "children": []},
    ])
    return folder, kid


def _no_settings(data):
    """A vault's zip without ``.obsidian``: folder sync's files, zipped."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as zf:
        for name in source.namelist():
            if ".obsidian" not in name.split("/"):
                zf.writestr(name, source.read(name))
    return out.getvalue()


@pytest.mark.parametrize("child", ["$$\nb\n$$", "$$c$$", "| x |\n| --- |\n| 1 |", "---", "***",
                                   "- ", "1. ", "- [ ] ", "- [x] "])
@pytest.mark.parametrize("keep", [lambda data: data, _no_settings], ids=["vault", "no .obsidian"])
def test_a_linked_block_a_bullet_wraps_comes_back_unwrapped(guest, child, keep):
    folder, _ = _linked_page(guest, child)
    data = _exported(guest, folder, "obsidian")
    for _ in range(3):
        folder = _new_folder(guest)
        page = _import(guest, io.BytesIO(keep(data)), folder=folder)["pages"][0]
        host, link = _subtree(guest, page["id"])["children"]
        assert [node["content"] for node in host["children"]] == [child]
        assert link["content"] == f"see [[{host['children'][0]['id']}]]"
        data = _exported(guest, folder, "obsidian")


def test_a_paper_keeps_its_bibtex_through_a_vault(guest):
    folder = _new_folder(guest)
    page = make_page(guest, "A paper", properties={
        "folders": [folder], "bibtex": "@article{mykey2020,\n  title={A paper},\n}",
        "meta": {"title": "A paper", "doi": "10.1/x", "authors": ["Ada Doe"], "year": "2020", "source": "manual"}})
    _put_children(guest, page["id"], [{"id": f"note{next(_CYCLES)}", "content": "a note", "properties": {}, "children": []}])
    for back in _cycles(guest, folder, "obsidian"):
        back = _subtree(guest, back["id"])
        assert "mykey2020" in back["properties"]["bibtex"]
        assert [node["content"] for node in back["children"]] == ["a note"]
