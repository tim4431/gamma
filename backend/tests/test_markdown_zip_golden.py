"""Golden output of the Markdown zip import: one synthetic zip per kind of
export, and the orders and counts between its stages that no other test
pins. Each zip is run through a preview and an import, and the report's
pages in order, its warnings with ``selection_id`` and the trees written are
compared whole.

Ids are random, so a page shows as its title and a block as ``Title#n``, its
place in the page; an upload shows as ``<upload .ext>``."""

import io
import itertools
import json
import re
import zipfile

import pytest

from conftest import folder_names, label_names, login, make_folder, make_user

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
A, B, C, D, E, F, G = ("a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32, "f" * 32, "0" * 32)
COUNTS = ("pages_created", "pages_skipped", "blocks_imported", "assets_stored", "links_resolved",
          "notion", "obsidian")
_UPLOAD_RE = re.compile(r"/api/uploads/[^)\s\"]+\.(\w+)")
_ACCOUNTS = itertools.count()


@pytest.fixture
def library():
    """An empty library of the test's own: the shared guest's holds what
    other tests imported, which a golden zip would find and skip."""
    name = f"mzg_{next(_ACCOUNTS)}"
    make_user(name, "pw-mzg")
    return login(name, "pw-mzg")


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    return buf.getvalue()


def _post(client, data, path="markdown-zip", **form):
    r = client.post(f"/api/import/{path}", files={"file": ("notes.zip", data, "application/zip")}, data=form)
    assert r.status_code == 200, r.text
    return r.json()


def _labels(pages) -> dict:
    """Page id → its title (a second page of a title numbered), through the
    pages of a report."""
    labels, seen = {}, {}
    for page in pages:
        seen[page["title"]] = seen.get(page["title"], 0) + 1
        n = seen[page["title"]]
        labels[page["id"]] = page["title"] if n == 1 else f"{page['title']} ({n})"
    return labels


def _row(page, labels) -> dict:
    """A report's page entry, without what most pages share."""
    path, = page["selection_ids"]
    assert page["created"] == (page["action"] == "create")
    row = {"page": labels[page["id"]], "path": path}
    if page["folders"]:
        row["folders"] = page["folders"]
    if page["parent"]:
        row["parent"] = labels.get(page["parent"], "(library)")
    if "folders_alone" in page:
        row["alone"] = page["folders_alone"]
    if page["source_path"] != path or page["source_paths"] != [path]:
        row["source"] = (page["source_path"], page["source_paths"])
    if page["kind"] != "page":
        row["kind"] = page["kind"]
    if page["action"] != "create":
        row["action"] = page["action"]
    if page["warnings"]:
        row["warnings"] = [(w["title"], w["reason"], w["selection_id"]) for w in page["warnings"]]
    if page["missing"]:
        row["missing"] = True
    return row


def _summary(report) -> dict:
    labels = _labels(report["pages"])
    return {**{key: report[key] for key in COUNTS},
            "entries": len(report["entries"]),
            "warnings": [(w["title"], w["reason"], w["selection_id"]) for w in report["warnings"]],
            "pages": [_row(page, labels) for page in report["pages"]]}


def _blocks(block):
    for child in block["children"]:
        yield child
        yield from _blocks(child)


def _outline(block, depth=0) -> list:
    out = []
    for child in block["children"]:
        out.append("    " * depth + child["content"])
        out += _outline(child, depth + 1)
    return out


def _stored(client, report) -> dict:
    """Every page of a report as written: its properties and its tree, each
    id shown as its name."""
    labels = _labels(report["pages"])
    blocks = {pid: client.get(f"/api/blocks/{pid}/subtree").json()["block"] for pid in labels}
    names = dict(labels)
    for pid, block in blocks.items():
        for n, child in enumerate(_blocks(block), 1):
            names[child["id"]] = f"{labels[pid]}#{n}"
    folders, tags = folder_names(client), label_names(client)

    def named(text):
        text = _UPLOAD_RE.sub(lambda m: f"<upload .{m.group(1)}>", text)
        return re.sub(r"\[\[([\w-]+)\]\]", lambda m: f"[[{names.get(m.group(1), m.group(1))}]]", text)

    out = {}
    for pid, block in blocks.items():
        props = dict(block["properties"])
        if props.pop("markdown_import", None) is None:
            props["no_digest"] = True
        if "folders" in props:
            props["folders"] = [folders[f] for f in props["folders"]]
        if "labels" in props:
            props["labels"] = [tags[t] for t in props["labels"]]
        if (props.get("doc") or {}).get("hash"):
            props["doc"] = {**props["doc"], "hash": True}
        if "parent_page" in props:
            props["parent_page"] = names[props["parent_page"]]
        out[labels[pid]] = {"properties": props, "tree": [named(line) for line in _outline(block)]}
    return out


def _capture(client, files, under=None) -> dict:
    """A zip's preview and import, which agree but for the blocks written."""
    data = _zip(files)
    folder = make_folder(client, under) if under else ""
    preview = _post(client, data, "markdown-zip/preview", folder=folder)
    report = _post(client, data, folder=folder)
    summary = _summary(report)
    assert _summary(preview) == {**summary, "blocks_imported": 0}
    return {"report": summary, "stored": _stored(client, report)}


def _notion_folders():
    """Notion's export until late 2025: a page's directory beside its file,
    one wrapper, a database with a view's CSV, an ``_all`` CSV and a stub."""
    top = "Export-0123abcd-4567/Private & Shared/"
    home, tasks = f"Home%20{A}", f"Tasks%20{C}"
    return {
        f"{top}Home {A}.md": f"""# Home

Welcome.

<aside>
💡 Export with subpages.

</aside>

> Quoted.
>

[Sub page]({home}/Sub%20page%20{B}.md)

[Tasks]({home}/{tasks}.csv)

![diagram.png]({home}/diagram.png)

[Report]({home}/report.pdf)

[Gone]({home}/gone.png)

[Into it]({home}/Sub%20page%20{B}.md#Details)
""",
        f"{top}Home {A}/Sub page {B}.md": f"# Sub page\n\nBack to [Home](../{home}.md).\n\n## Details\n\nUnder it.\n",
        f"{top}Home {A}/diagram.png": PNG,
        f"{top}Home {A}/report.pdf": b"not a pdf",
        f"{top}Home {A}/Tasks {C}.md": f"# Tasks\n\nThe team's tasks.\n\n## Views\n\n[Tasks]({tasks}.csv)\n",
        f"{top}Home {A}/Tasks {C}.csv": "Name,Due\nWrite tests,Friday\nShip it,Monday\n",
        f"{top}Home {A}/Tasks {C}_all.csv": ("Due,Name,Blocked by\n"
                                             f"Friday,Write tests,Ship it (Tasks/Ship%20it%20{E}.md)\n"
                                             "Monday,Ship it,\n"),
        f"{top}Home {A}/Tasks {C}/Write tests {D}.md": ("# Write tests\n\nDue: Friday\n"
                                                        f"Blocked by: Ship it (Ship%20it%20{E}.md)\n\nThe row.\n"),
        f"{top}Home {A}/Tasks {C}/Ship it {E}.md": "# Ship it\n\nDue: Monday\n",
    }


def _notion_late():
    """Notion's export since late 2025, as one Part zip: directories named
    as the title, a second of a title with its id's first and last four."""
    second = "cccc" + "0" * 24 + "dddd"
    return {"Export-0123abcd-4567-Part-1.zip": _zip({
        f"Top {A}.md": f"# Top\n\n[Budget](Top/Budget%20{B}.md)\n\n[Budget](Top/Budget%20{second}.md)\n",
        f"Top/Budget {B}.md": "# Budget\n\nFirst.\n",
        f"Top/Budget {second}.md": "# Budget\n\nSecond.\n",
        f"Top/Budget/Spring {D}.md": "# Spring\n",
        "Top/Budget cccc-dddd/Summer " + E + ".md": "# Summer\n",
        f"Top/People {F}.csv": "Name,Role\nAda,Author\n",
        f"Top/People/Ada {G}.md": "# Ada\n\nRole: Author\n",
    })}


def _notion_flat():
    """Every file in one directory: the tree is in the pages' lines and the
    databases' rows. Two rows of a title go by their values."""
    return {
        f"Top {A}.md": f"# Top\n\n[Overview](Overview%20{B}.md)\n\n[Tasks](Tasks%20{C}.csv)\n",
        f"Overview {B}.md": f"# Overview\n\nBack to [Top](Top%20{A}.md).\n\n![pic](pic.png)\n",
        f"Tasks {C}.csv": "Name,Due\nWrite,Monday\nWrite,Tuesday\n",
        f"Write {D}.md": "# Write\n\nDue: Tuesday\n",
        f"Write {E}.md": "# Write\n\nDue: Monday\n",
        "README.md": "# Read me\n\nNo id, so no page of the tree.\n",
        "pic.png": PNG,
    }


def _vault():
    """An Obsidian vault: titles from file names, wikilinks and embeds of
    every kind, anchors, comments, properties, what a vault leaves out."""
    return {
        ".obsidian/app.json": "{}",
        ".obsidian/plugins/x/README.md": "# Not a note\n",
        ".trash/Old.md": "# Gone\n",
        "Board.canvas": "{}",
        "Home.md": """---
tags: [work, "#deep"]
aliases: [Start]
---
# Home

Intro %%hidden%% text.

```
%%kept in code%%
```

## Own heading

See [[Note]], [[Daily/Note#Plan|the plan]], [[Note#^para]] and [[#Own heading]].

![[Note#^para]]

![[Note]]

![[pic.png|300]]

![[pic.png|the picture]]

[[paper.pdf]]

[[Gone]]
""",
        "Note.md": "# Note\n\n## Plan\n\nA paragraph. ^para\n\n- item\n\n^listed\n",
        "Daily/Note.md": "# A title of its own\n\n## Plan\n\nThe day's plan.\n",
        "Reading.md": '---\nsource: "[[paper.pdf]]"\n---\nThe paper.\n',
        "Lost.md": '---\nsource: "[[gone.pdf]]"\n---\nNo paper.\n',
        "Kids/Kid.md": '---\nparent: "[[Home]]"\n---\nA sub-page.\n',
        "pic.png": PNG,
        "paper.pdf": PDF,
    }


def _gamma_export():
    """Gamma's own Markdown export: its front matter, BibTeX and sub-pages."""
    return {
        "Paper notes.md": """---
title: "Paper notes"
folder: Reading/2024
doi: 10.1000/xyz
authors: [Ada Lovelace, Alan Turing]
year: 2024
source: https://example.org/paper.pdf
tags: [paper]
---
# Paper notes

```bibtex
@article{x, title={T}}
```

- p.1 A highlight
    - A comment on it
- p.2 Another
""",
        "Paper notes/Sub page.md": '---\nparent: "../Paper%20notes.md"\n---\n# Sub page\n\nInside it.\n',
        "With a PDF.md": "---\nsource: With a PDF.pdf\n---\n# With a PDF\n\nRead it.\n",
        "With a PDF.pdf": PDF,
    }


def _plain():
    """A zipped folder of notes: directories make folders."""
    return {
        "notes/Intro.md": ("No heading here.\n\n"
                           "[Next](sub/Next.md), [gone](nope.md), [site](https://example.org), ![pic](img/pic.png), "
                           "[doc](doc.txt)\n"),
        "notes/sub/Next.md": "# Next title\n\nBody.\n\n## Section\n\nUnder it.\n",
        "notes/sub/deep/Deep.md": "---\ntitle: Deep title\n---\n[Intro](../../Intro.md), [in](../Next.md#Section)\n",
        "notes/Empty.md": "",
        "notes/latin.md": b"caf\xe9",
        "notes/big.md": "x" * (5 * 1024 * 1024 + 1),
        "notes/img/pic.png": PNG,
        "notes/doc.txt": "A text file.\n",
    }


ZIPS = {"notion_folders": (_notion_folders, None), "notion_late": (_notion_late, None),
        "notion_flat": (_notion_flat, None), "vault": (_vault, None), "gamma_export": (_gamma_export, "Library"),
        "plain": (_plain, "Inbox")}


@pytest.mark.parametrize("name", ZIPS)
def test_golden_zip(library, name):
    files, under = ZIPS[name]
    assert _capture(library, files(), under) == GOLDEN[name]


def test_a_second_import_skips_every_page_and_reports_where_they_are(library):
    data = _zip(_notion_folders())
    _post(library, data)
    again = _summary(_post(library, data))
    assert {key: again[key] for key in COUNTS} == {
        "pages_created": 0, "pages_skipped": 5, "blocks_imported": 0, "assets_stored": 0, "links_resolved": 0,
        "notion": True, "obsidian": False}
    assert [row.get("action") for row in again["pages"]] == ["skip"] * 5
    assert again["warnings"] == []


def test_a_selection_leaves_links_and_parents_to_what_it_leaves_out(library):
    data = _zip({
        "Other.md": "# Other\n\n[Mom](Mom.md) and [Kid](Mom/Kid.md).\n",
        "Mom.md": "# Mom\n",
        "Mom/Kid.md": '---\nparent: "../Mom.md"\n---\n# Kid\n',
    })
    assert _summary(_post(library, data, "markdown-zip/preview"))["pages"] == [
        {"page": "Mom", "path": "Mom.md"},
        {"page": "Other", "path": "Other.md"},
        {"page": "Kid", "path": "Mom/Kid.md", "parent": "Mom", "alone": [["Mom"]]},
    ]
    report = _post(library, data, selected=json.dumps(["Other.md", "Mom/Kid.md"]))
    reason = "Missing or unselected linked file: Mom.md; the link is kept as written"
    assert _summary(report)["pages"] == [
        {"page": "Other", "path": "Other.md", "warnings": [("Other.md", reason, "Other.md")], "missing": True},
        {"page": "Kid", "path": "Mom/Kid.md", "folders": [["Mom"]]},
    ]
    assert _stored(library, report)["Other"]["tree"] == ["[Mom](Mom.md) and [[Kid]]."]


# --- what the stages must keep ----------------------------------------------

def test_plan_order_is_depth_then_path_then_the_databases(library):
    report = _post(library, _zip({
        "b/two.md": "# two\n",
        f"Rows {A}.csv": "Name\nx\n",
        "B.md": "# B\n",
        "a/one.md": "# one\n",
        "A.md": "# A\n",
        "a/b/three.md": "# three\n",
    }))
    assert [p["selection_ids"][0] for p in report["pages"]] == ["A.md", "B.md", "a/one.md", "b/two.md",
                                                                "a/b/three.md", f"Rows {A}.csv"]


def test_report_order_is_a_stable_sort_by_depth_in_the_import(library):
    report = _post(library, _zip({
        "A.md": '---\nparent: "b/B.md"\n---\n# A\n',
        "Z.md": "# Z\n",
        "b/B.md": '---\nparent: "c/d/C.md"\n---\n# B\n',
        "b/Y.md": "# Y\n",
        "c/d/C.md": "# C\n",
    }))
    # Every parent first, and within a depth the plan's order.
    assert [p["title"] for p in report["pages"]] == ["Z", "Y", "C", "B", "A"]


def test_of_two_notes_of_one_id_a_relation_finds_the_last(library):
    """Its path names neither file, so the id decides: the plan read last."""
    report = _post(library, _zip({
        f"First {A}.md": "# First\n",
        f"Row {B}.md": f"# Row\n\nSee: Moved (nowhere/Moved%20{A}.md)\n",
        f"zz/Second {A}.md": "# Second\n",
    }))
    assert report["links_resolved"] == 1
    assert _stored(library, report)["Row"]["tree"] == ["See: [[Second]]"]


def test_page_warnings_are_the_page_s_slice_and_missing_reads_their_words(library):
    report = _post(library, _zip({
        "A.md": "# A\n\n[gone](gone.md)\n",
        "B.md": "# B\n\n[bad](bad.pdf)\n",
        "C.md": "# C\n\n[bad](bad.pdf) again, a file is tried once\n",
        "D.md": "---\nsource: gone.pdf\n---\n# D\n",
        "latin.md": b"caf\xe9",
        "bad.pdf": b"not a pdf",
    }))
    assert _summary(report)["pages"] == [
        {"page": "A", "path": "A.md", "missing": True, "warnings": [
            ("A.md", "Missing or unselected linked file: gone.md; the link is kept as written", "A.md")]},
        {"page": "B", "path": "B.md", "warnings": [("bad.pdf", "not a valid PDF", "B.md")]},
        {"page": "C", "path": "C.md"},
        {"page": "D", "path": "D.md", "missing": True, "warnings": [
            ("D", "Missing PDF attachment: gone.pdf", "D.md")]},
    ]
    assert report["warnings"][0] == {"title": "latin.md", "reason": "not UTF-8", "selection_id": None}


def test_past_the_warning_cap_a_page_shows_none(library):
    lines = "\n\n".join(f"[gone](gone{n}.md)" for n in range(205))
    report = _post(library, _zip({"A.md": f"# A\n\n{lines}\n", "B.md": "# B\n\n[gone](gone.md)\n"}))
    assert len(report["warnings"]) == 200
    assert (len(report["pages"][0]["warnings"]), report["pages"][0]["missing"]) == (200, True)
    assert (report["pages"][1]["warnings"], report["pages"][1]["missing"]) == ([], False)


def test_a_stub_s_text_comes_above_the_table_and_its_headings_name_no_block(library):
    """A link to a heading of a database's stub finds the page: the links
    read the headings of the CSV's first parse, which has none. (A CSV's
    table gives no target either, so what the fold's ``db.ids.clear()`` drops
    is always empty: this cannot tell it from a fold without it.)"""
    report = _post(library, _zip({
        f"Home {A}.md": f"# Home\n\n[Views](Home%20{A}/People%20{B}.md#Views)\n",
        f"Home {A}/People {B}.md": f"# People\n\n## Views\n\n[People](People%20{B}.csv)\n",
        f"Home {A}/People {B}.csv": "Name\nAda\n",
    }))
    assert report["links_resolved"] == 1
    stored = _stored(library, report)
    assert stored["Home"]["tree"] == ["[[People]]"]
    assert stored["People"]["tree"] == ["## Views", "| Name |\n| --- |\n| Ada |"]


@pytest.mark.parametrize("length,cut", [(49, True), (48, False)])
def test_a_cut_title_is_read_before_the_flat_tree(library, length, cut):
    """A database's name of 49 characters or more is taken as cut, and its
    line's label is its title, which then names it for the flat tree."""
    short, whole = "t" * length, "t" * length + " and the rest"
    report = _post(library, _zip({
        f"Top {A}.md": f"# Top\n\n[{whole}]({short}%20{B}.csv)\n",
        f"{short} {B}.csv": "Name\nx\n",
    }))
    summary = _summary(report)["pages"]
    if cut:
        assert summary == [{"page": "Top", "path": f"Top {A}.md"},
                           {"page": whole, "path": f"{short} {B}.csv", "parent": "Top", "alone": []}]
    else:
        assert summary == [{"page": "Top", "path": f"Top {A}.md"}, {"page": short, "path": f"{short} {B}.csv"}]


def test_wikilinks_are_rewritten_before_markdown_links(library):
    """``[[One]](two.md)``: the wikilink's mention then reads as the label
    of a Markdown link, which wins. The other way round the mention would
    be looked up as a note and warned of."""
    report = _post(library, _zip({
        "Home.md": "# Home\n\n[[One]](Two.md)\n",
        "One.md": "# One\n",
        "Two.md": "# Two\n",
    }))
    assert report["links_resolved"] == 2 and report["warnings"] == []
    assert _stored(library, report)["Home"]["tree"] == ["[[Two]]"]


GOLDEN = {
    "notion_folders":
     {'report': {'pages_created': 5,
                 'pages_skipped': 0,
                 'blocks_imported': 18,
                 'assets_stored': 1,
                 'links_resolved': 6,
                 'notion': True,
                 'obsidian': False,
                 'entries': 9,
                 'warnings': [('Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/report.pdf',
                               'not a valid PDF',
                               'Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md'),
                              ('Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
                               'Missing or unselected linked file: '
                               'Home%20aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/gone.png; the link is kept as written',
                               'Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md')],
                 'pages': [{'page': 'Home',
                            'path': 'Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
                            'source': ('Export-0123abcd-4567/Private & Shared/Home '
                                       'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
                                       ['Export-0123abcd-4567/Private & Shared/Home '
                                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md']),
                            'warnings': [('Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/report.pdf',
                                          'not a valid PDF',
                                          'Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md'),
                                         ('Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
                                          'Missing or unselected linked file: '
                                          'Home%20aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/gone.png; the link is kept as '
                                          'written',
                                          'Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md')],
                            'missing': True},
                           {'page': 'Sub page',
                            'path': 'Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Sub page '
                                    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md',
                            'parent': 'Home',
                            'alone': [['Home']],
                            'source': ('Export-0123abcd-4567/Private & Shared/Home '
                                       'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Sub page '
                                       'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md',
                                       ['Export-0123abcd-4567/Private & Shared/Home '
                                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Sub page '
                                        'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md'])},
                           {'page': 'Tasks',
                            'path': 'Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Tasks '
                                    'cccccccccccccccccccccccccccccccc_all.csv',
                            'parent': 'Home',
                            'alone': [['Home']],
                            'source': ('Export-0123abcd-4567/Private & Shared/Home '
                                       'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Tasks '
                                       'cccccccccccccccccccccccccccccccc_all.csv',
                                       ['Export-0123abcd-4567/Private & Shared/Home '
                                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Tasks '
                                        'cccccccccccccccccccccccccccccccc_all.csv'])},
                           {'page': 'Ship it',
                            'path': 'Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Tasks '
                                    'cccccccccccccccccccccccccccccccc/Ship it eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.md',
                            'parent': 'Tasks',
                            'alone': [['Home', 'Tasks']],
                            'source': ('Export-0123abcd-4567/Private & Shared/Home '
                                       'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Tasks '
                                       'cccccccccccccccccccccccccccccccc/Ship it '
                                       'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.md',
                                       ['Export-0123abcd-4567/Private & Shared/Home '
                                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Tasks '
                                        'cccccccccccccccccccccccccccccccc/Ship it '
                                        'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.md'])},
                           {'page': 'Write tests',
                            'path': 'Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Tasks '
                                    'cccccccccccccccccccccccccccccccc/Write tests '
                                    'dddddddddddddddddddddddddddddddd.md',
                            'parent': 'Tasks',
                            'alone': [['Home', 'Tasks']],
                            'source': ('Export-0123abcd-4567/Private & Shared/Home '
                                       'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Tasks '
                                       'cccccccccccccccccccccccccccccccc/Write tests '
                                       'dddddddddddddddddddddddddddddddd.md',
                                       ['Export-0123abcd-4567/Private & Shared/Home '
                                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Tasks '
                                        'cccccccccccccccccccccccccccccccc/Write tests '
                                        'dddddddddddddddddddddddddddddddd.md'])}]},
      'stored': {'Home': {'properties': {'original_filename': 'Home aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
                                         'notion_id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'},
                          'tree': ['Welcome.',
                                   '> [!info] 💡 Export with subpages.',
                                   '> Quoted.',
                                   '[[Sub page]]',
                                   '[[Tasks]]',
                                   '![diagram.png](<upload .png>)',
                                   '[Report](Home%20aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/report.pdf)',
                                   '[Gone](Home%20aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/gone.png)',
                                   '[[Sub page#2]]']},
                 'Sub page': {'properties': {'original_filename': 'Sub page bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md',
                                             'notion_id': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                                             'parent_page': 'Home'},
                              'tree': ['Back to [[Home]].', '## Details', 'Under it.']},
                 'Tasks': {'properties': {'original_filename': 'Tasks cccccccccccccccccccccccccccccccc_all.csv',
                                          'notion_id': 'cccccccccccccccccccccccccccccccc',
                                          'parent_page': 'Home'},
                           'tree': ["The team's tasks.",
                                    '## Views',
                                    '| Name | Due | Blocked by |\n'
                                    '| --- | --- | --- |\n'
                                    '| Write tests | Friday | [[Ship it]] |\n'
                                    '| Ship it | Monday |   |']},
                 'Ship it': {'properties': {'original_filename': 'Ship it eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.md',
                                            'notion_id': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
                                            'parent_page': 'Tasks'},
                             'tree': ['Due: Monday']},
                 'Write tests': {'properties': {'original_filename': 'Write tests '
                                                                     'dddddddddddddddddddddddddddddddd.md',
                                                'notion_id': 'dddddddddddddddddddddddddddddddd',
                                                'parent_page': 'Tasks'},
                                 'tree': ['Due: Friday\nBlocked by: [[Ship it]]', 'The row.']}}},
    "notion_late":
     {'report': {'pages_created': 7,
                 'pages_skipped': 0,
                 'blocks_imported': 6,
                 'assets_stored': 0,
                 'links_resolved': 2,
                 'notion': True,
                 'obsidian': False,
                 'entries': 1,
                 'warnings': [],
                 'pages': [{'page': 'Top',
                            'path': 'Top aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
                            'source': ('Top aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
                                       ['Export-0123abcd-4567-Part-1.zip'])},
                           {'page': 'Budget',
                            'path': 'Top/Budget bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md',
                            'parent': 'Top',
                            'alone': [['Top']],
                            'source': ('Top/Budget bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md',
                                       ['Export-0123abcd-4567-Part-1.zip'])},
                           {'page': 'Budget (2)',
                            'path': 'Top/Budget cccc000000000000000000000000dddd.md',
                            'parent': 'Top',
                            'alone': [['Top']],
                            'source': ('Top/Budget cccc000000000000000000000000dddd.md',
                                       ['Export-0123abcd-4567-Part-1.zip'])},
                           {'page': 'People',
                            'path': 'Top/People ffffffffffffffffffffffffffffffff.csv',
                            'parent': 'Top',
                            'alone': [['Top']],
                            'source': ('Top/People ffffffffffffffffffffffffffffffff.csv',
                                       ['Export-0123abcd-4567-Part-1.zip'])},
                           {'page': 'Summer',
                            'path': 'Top/Budget cccc-dddd/Summer eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.md',
                            'parent': 'Budget (2)',
                            'alone': [['Top', 'Budget cccc-dddd']],
                            'source': ('Top/Budget cccc-dddd/Summer eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.md',
                                       ['Export-0123abcd-4567-Part-1.zip'])},
                           {'page': 'Spring',
                            'path': 'Top/Budget/Spring dddddddddddddddddddddddddddddddd.md',
                            'parent': 'Budget',
                            'alone': [['Top', 'Budget']],
                            'source': ('Top/Budget/Spring dddddddddddddddddddddddddddddddd.md',
                                       ['Export-0123abcd-4567-Part-1.zip'])},
                           {'page': 'Ada',
                            'path': 'Top/People/Ada 00000000000000000000000000000000.md',
                            'parent': 'People',
                            'alone': [['Top', 'People']],
                            'source': ('Top/People/Ada 00000000000000000000000000000000.md',
                                       ['Export-0123abcd-4567-Part-1.zip'])}]},
      'stored': {'Top': {'properties': {'original_filename': 'Top aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
                                        'notion_id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'},
                         'tree': ['[[Budget]]', '[[Budget (2)]]']},
                 'Budget': {'properties': {'original_filename': 'Budget bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md',
                                           'notion_id': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                                           'parent_page': 'Top'},
                            'tree': ['First.']},
                 'Budget (2)': {'properties': {'original_filename': 'Budget cccc000000000000000000000000dddd.md',
                                               'notion_id': 'cccc000000000000000000000000dddd',
                                               'parent_page': 'Top'},
                                'tree': ['Second.']},
                 'People': {'properties': {'original_filename': 'People ffffffffffffffffffffffffffffffff.csv',
                                           'notion_id': 'ffffffffffffffffffffffffffffffff',
                                           'parent_page': 'Top'},
                            'tree': ['| Name | Role |\n| --- | --- |\n| Ada | Author |']},
                 'Summer': {'properties': {'original_filename': 'Summer eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.md',
                                           'notion_id': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
                                           'parent_page': 'Budget (2)'},
                            'tree': []},
                 'Spring': {'properties': {'original_filename': 'Spring dddddddddddddddddddddddddddddddd.md',
                                           'notion_id': 'dddddddddddddddddddddddddddddddd',
                                           'parent_page': 'Budget'},
                            'tree': []},
                 'Ada': {'properties': {'original_filename': 'Ada 00000000000000000000000000000000.md',
                                        'notion_id': '00000000000000000000000000000000',
                                        'parent_page': 'People'},
                         'tree': ['Role: Author']}}},
    "notion_flat":
     {'report': {'pages_created': 6,
                 'pages_skipped': 0,
                 'blocks_imported': 8,
                 'assets_stored': 1,
                 'links_resolved': 3,
                 'notion': True,
                 'obsidian': False,
                 'entries': 7,
                 'warnings': [],
                 'pages': [{'page': 'Read me', 'path': 'README.md'},
                           {'page': 'Top', 'path': 'Top aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md'},
                           {'page': 'Overview',
                            'path': 'Overview bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md',
                            'parent': 'Top',
                            'alone': []},
                           {'page': 'Tasks',
                            'path': 'Tasks cccccccccccccccccccccccccccccccc.csv',
                            'parent': 'Top',
                            'alone': []},
                           {'page': 'Write',
                            'path': 'Write dddddddddddddddddddddddddddddddd.md',
                            'parent': 'Tasks',
                            'alone': []},
                           {'page': 'Write (2)',
                            'path': 'Write eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.md',
                            'parent': 'Tasks',
                            'alone': []}]},
      'stored': {'Read me': {'properties': {'original_filename': 'README.md'},
                             'tree': ['No id, so no page of the tree.']},
                 'Top': {'properties': {'original_filename': 'Top aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md',
                                        'notion_id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'},
                         'tree': ['[[Overview]]', '[[Tasks]]']},
                 'Overview': {'properties': {'original_filename': 'Overview bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md',
                                             'notion_id': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                                             'parent_page': 'Top'},
                              'tree': ['Back to [[Top]].', '![pic](<upload .png>)']},
                 'Tasks': {'properties': {'original_filename': 'Tasks cccccccccccccccccccccccccccccccc.csv',
                                          'notion_id': 'cccccccccccccccccccccccccccccccc',
                                          'parent_page': 'Top'},
                           'tree': ['| Name | Due |\n| --- | --- |\n| Write | Monday |\n| Write | Tuesday |']},
                 'Write': {'properties': {'original_filename': 'Write dddddddddddddddddddddddddddddddd.md',
                                          'notion_id': 'dddddddddddddddddddddddddddddddd',
                                          'parent_page': 'Tasks'},
                           'tree': ['Due: Tuesday']},
                 'Write (2)': {'properties': {'original_filename': 'Write eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.md',
                                              'notion_id': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
                                              'parent_page': 'Tasks'},
                               'tree': ['Due: Monday']}}},
    "vault":
     {'report': {'pages_created': 6,
                 'pages_skipped': 0,
                 'blocks_imported': 19,
                 'assets_stored': 2,
                 'links_resolved': 6,
                 'notion': False,
                 'obsidian': True,
                 'entries': 12,
                 'warnings': [('Board.canvas', 'Obsidian canvas files are not imported', None),
                              ('Home.md',
                               'Missing or unselected note or attachment: Gone; the link is kept as written',
                               'Home.md'),
                              ('Lost', 'Missing PDF attachment: gone.pdf', 'Lost.md')],
                 'pages': [{'page': 'Home',
                            'path': 'Home.md',
                            'warnings': [('Home.md',
                                          'Missing or unselected note or attachment: Gone; the link is kept as '
                                          'written',
                                          'Home.md')],
                            'missing': True},
                           {'page': 'Lost',
                            'path': 'Lost.md',
                            'warnings': [('Lost', 'Missing PDF attachment: gone.pdf', 'Lost.md')],
                            'missing': True},
                           {'page': 'Note', 'path': 'Note.md'},
                           {'page': 'Reading', 'path': 'Reading.md', 'kind': 'pdf'},
                           {'page': 'Note (2)', 'path': 'Daily/Note.md', 'folders': [['Daily']]},
                           {'page': 'Kid', 'path': 'Kids/Kid.md', 'parent': 'Home', 'alone': [['Kids']]}]},
      'stored': {'Home': {'properties': {'original_filename': 'Home.md',
                                         'aliases': ['Start'],
                                         'labels': ['work', 'deep']},
                          'tree': ['Intro  text.',
                                   '```\n%%kept in code%%\n```',
                                   '## Own heading',
                                   '    See [[Note]], [[Note (2)#2]], [[Note#2]] and [[Home#3]].',
                                   '    ![[Note#2]]',
                                   '    [[Note]]',
                                   '    ![|300](<upload .png>)',
                                   '    ![the picture](<upload .png>)',
                                   '    [paper.pdf](<upload .pdf>)',
                                   '    [[Gone]]']},
                 'Lost': {'properties': {'original_filename': 'Lost.md'}, 'tree': ['No paper.']},
                 'Note': {'properties': {'original_filename': 'Note.md'},
                          'tree': ['## Plan', '    A paragraph.', '    - item']},
                 'Reading': {'properties': {'original_filename': 'Reading.md',
                                            'doc': {'format': 'pdf', 'name': '', 'hash': True, 'ext': '.pdf', 'at': 'store'}},
                             'tree': ['The paper.']},
                 'Note (2)': {'properties': {'original_filename': 'Note.md', 'folders': [['Daily']]},
                              'tree': ['# A title of its own', '    ## Plan', "        The day's plan."]},
                 'Kid': {'properties': {'original_filename': 'Kid.md', 'parent_page': 'Home'},
                         'tree': ['A sub-page.']}}},
    "gamma_export":
     {'report': {'pages_created': 3,
                 'pages_skipped': 0,
                 'blocks_imported': 5,
                 'assets_stored': 1,
                 'links_resolved': 0,
                 'notion': False,
                 'obsidian': False,
                 'entries': 4,
                 'warnings': [],
                 'pages': [{'page': 'Paper notes',
                            'path': 'Paper notes.md',
                            'folders': [['Library', 'Reading', '2024']]},
                           {'page': 'With a PDF', 'path': 'With a PDF.md', 'folders': [['Library']], 'kind': 'pdf'},
                           {'page': 'Sub page',
                            'path': 'Paper notes/Sub page.md',
                            'folders': [['Library', 'Reading', '2024']],
                            'parent': 'Paper notes',
                            'alone': [['Library', 'Paper notes']]}]},
      'stored': {'Paper notes': {'properties': {'original_filename': 'Paper notes.md',
                                                'bibtex': '@article{x, title={T}}',
                                                'meta': {'title': 'Paper notes',
                                                         'doi': '10.1000/xyz',
                                                         'authors': ['Ada Lovelace', 'Alan Turing'],
                                                         'year': '2024',
                                                         'source': 'manual'},
                                                'doc': {'format': 'pdf', 'name': '', 'hash': '', 'ext': '.pdf',
                                                        'at': 'url', 'url': 'https://example.org/paper.pdf'},
                                                'folders': [['Library', 'Reading', '2024']],
                                                'labels': ['paper']},
                                 'tree': ['p.1 A highlight', '    A comment on it', 'p.2 Another']},
                 'With a PDF': {'properties': {'original_filename': 'With a PDF.md',
                                               'doc': {'format': 'pdf', 'name': '', 'hash': True, 'ext': '.pdf', 'at': 'store'},
                                               'folders': [['Library']]},
                                'tree': ['Read it.']},
                 'Sub page': {'properties': {'original_filename': 'Sub page.md', 'parent_page': 'Paper notes'},
                              'tree': ['Inside it.']}}},
    "plain":
     {'report': {'pages_created': 4,
                 'pages_skipped': 0,
                 'blocks_imported': 6,
                 'assets_stored': 2,
                 'links_resolved': 3,
                 'notion': False,
                 'obsidian': False,
                 'entries': 8,
                 'warnings': [('big.md', 'file exceeds 5 MB', None),
                              ('latin.md', 'not UTF-8', None),
                              ('Intro.md',
                               'Missing or unselected linked file: nope.md; the link is kept as written',
                               'Intro.md')],
                 'pages': [{'page': 'Empty',
                            'path': 'Empty.md',
                            'folders': [['Inbox']],
                            'source': ('notes/Empty.md', ['notes/Empty.md'])},
                           {'page': 'Intro',
                            'path': 'Intro.md',
                            'folders': [['Inbox']],
                            'source': ('notes/Intro.md', ['notes/Intro.md']),
                            'warnings': [('Intro.md',
                                          'Missing or unselected linked file: nope.md; the link is kept as written',
                                          'Intro.md')],
                            'missing': True},
                           {'page': 'Next title',
                            'path': 'sub/Next.md',
                            'folders': [['Inbox', 'sub']],
                            'source': ('notes/sub/Next.md', ['notes/sub/Next.md'])},
                           {'page': 'Deep title',
                            'path': 'sub/deep/Deep.md',
                            'folders': [['Inbox', 'sub', 'deep']],
                            'source': ('notes/sub/deep/Deep.md', ['notes/sub/deep/Deep.md'])}]},
      'stored': {'Empty': {'properties': {'original_filename': 'Empty.md', 'folders': [['Inbox']]}, 'tree': []},
                 'Intro': {'properties': {'original_filename': 'Intro.md', 'folders': [['Inbox']]},
                           'tree': ['No heading here.',
                                    '[[Next title]], [gone](nope.md), [site](https://example.org), ![pic](<upload '
                                    '.png>), [doc](<upload .txt>)']},
                 'Next title': {'properties': {'original_filename': 'Next.md', 'folders': [['Inbox', 'sub']]},
                                'tree': ['Body.', '## Section', '    Under it.']},
                 'Deep title': {'properties': {'original_filename': 'Deep.md',
                                               'folders': [['Inbox', 'sub', 'deep']]},
                                'tree': ['[[Intro]], [[Next title#2]]']}}},
}
