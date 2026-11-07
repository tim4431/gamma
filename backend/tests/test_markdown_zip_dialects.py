"""The rules of each kind of Markdown zip (gamma/markdown_zip_dialects.py): a
Notion rule stops at Notion's exports, a vault's at vaults. One test per rule
that parts them; what they share is in test_markdown_zip_import.py and the
golden zips."""

import io
import itertools
import zipfile

import pytest

from conftest import login, make_user
from gamma import markdown_import, markdown_zip_import as mzi, notion_import
from gamma.markdown_zip_dialects import dialect_of

A, B, C, D = ("a" * 32, "b" * 32, "c" * 32, "d" * 32)
VAULT = {".obsidian/app.json": "{}"}
LEFT_OUT = "no new note links this CSV: not imported"
_ACCOUNTS = itertools.count()


@pytest.fixture
def library():
    """An empty library of the test's own, so nothing a zip holds is found
    there."""
    name = f"mzd_{next(_ACCOUNTS)}"
    make_user(name, "pw-mzd")
    return login(name, "pw-mzd")


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _post(client, files, path="markdown-zip"):
    return client.post(f"/api/import/{path}", files={"file": ("notes.zip", _zip(files), "application/zip")})


def _import(client, files) -> dict:
    """A zip's report, its preview's agreeing on what it warned."""
    preview = _post(client, files, "markdown-zip/preview")
    assert preview.status_code == 200, preview.text
    r = _post(client, files)
    assert r.status_code == 200, r.text
    assert preview.json()["warnings"] == r.json()["warnings"]
    return r.json()


def _page(client, report, title) -> dict:
    page = next(p for p in report["pages"] if p["title"] == title)
    return client.get(f"/api/blocks/{page['id']}/subtree").json()["block"]


def _lines(client, report, title) -> list:
    return [child["content"] for child in _page(client, report, title)["children"]]


def _reasons(report) -> dict:
    return {w["title"]: w["reason"] for w in report["warnings"]}


# --- which dialect ---------------------------------------------------------

@pytest.mark.parametrize("paths,name", [
    (["a.md", "b.csv"], "plain"),
    ([f"Home {A}.md"], "notion"),
    ([f"Tasks {A}.csv"], "notion"),
    ([f"Tasks {A}_all.csv"], "notion"),
    ([f"Home/Sub {A}.markdown"], "notion"),
    ([".obsidian/app.json", f"Home {A}.md"], "vault"),
    (["Vault/.obsidian/app.json", f"Tasks {A}.csv"], "vault"),
    ([f"pic {A}.png", "a.md"], "plain"),          # (no note or CSV holds the id)
    ([f".trash/Old {A}.md", "a.md"], "plain"),    # (a file that is no note of the zip)
    (["a abcd-ef01.md"], "plain"),
])
def test_a_zip_s_dialect(paths, name):
    assert dialect_of(paths).name == name


# --- notion_id and the match by id -----------------------------------------

def test_a_vault_note_keeps_its_clean_title_and_no_notion_id(library):
    report = _import(library, {**VAULT, f"Plan {A}.md": "Body.\n"})
    assert (report["notion"], report["obsidian"]) == (False, True)
    page = _page(library, report, "Plan")
    assert "notion_id" not in page["properties"]


def test_a_vault_note_is_not_the_page_of_a_notion_id_in_its_name(library):
    first = _import(library, {f"Plan {A}.md": "# Plan\n\nFrom Notion.\n"})
    assert _page(library, first, "Plan")["properties"]["notion_id"] == A
    again = _import(library, {**VAULT, f"Plan {A}.md": "Edited in a vault.\n"})
    assert (again["pages_created"], again["pages_skipped"]) == (1, 0)
    assert _lines(library, again, "Plan") == ["Edited in a vault."]


# --- a CSV ------------------------------------------------------------------

@pytest.mark.parametrize("zipped", [{}, VAULT, {f"Home {A}.md": "# Home\n"}], ids=["plain", "vault", "notion"])
def test_a_csv_with_no_id_a_note_links_is_an_attachment(library, zipped):
    report = _import(library, {**zipped, "Note.md": "[The data](data.csv)\n", "data.csv": "Name\nx\n"})
    assert [p["title"] for p in report["pages"] if p["title"] in ("data", "Note")] == ["Note"]
    assert report["assets_stored"] == 1 and report["warnings"] == []
    assert _lines(library, report, "Note")[0].startswith("[The data](/api/uploads/")


@pytest.mark.parametrize("zipped", [{}, VAULT, {f"Home {A}.md": "# Home\n"}], ids=["plain", "vault", "notion"])
def test_a_csv_with_no_id_nothing_links_is_left_out_with_a_warning(library, zipped):
    report = _import(library, {**zipped, "Note.md": "No link.\n", "sub/data.csv": "Name\nx\n"})
    assert not any(p["title"] == "data" for p in report["pages"])
    assert report["assets_stored"] == 0
    assert report["warnings"] == [{"title": "sub/data.csv", "reason": LEFT_OUT, "selection_id": None}]


def test_a_vault_s_csv_is_an_attachment_whatever_its_name(library):
    report = _import(library, {**VAULT, "Note.md": f"![[Tasks {A}.csv]]\n", f"Tasks {A}.csv": "Name\nx\n"})
    assert [p["title"] for p in report["pages"]] == ["Note"]
    assert _lines(library, report, "Note")[0].startswith(f"[Tasks {A}.csv](/api/uploads/")


def test_a_csv_a_note_left_out_links_is_left_out_too(library):
    files = {"One.md": "[The data](data.csv)\n", "Two.md": "No link.\n", "data.csv": "Name\nx\n"}
    r = library.post("/api/import/markdown-zip", files={"file": ("notes.zip", _zip(files), "application/zip")},
                     data={"selected": '["Two.md"]'})
    assert r.status_code == 200, r.text
    assert _reasons(r.json()) == {"data.csv": LEFT_OUT}


def test_a_csv_a_skipped_note_links_is_not_called_unlinked(library):
    files = {**VAULT, "Note.md": "[The data](data.csv)\n", "data.csv": "Name\nx\n"}
    assert _import(library, files)["warnings"] == []
    again = _import(library, files)
    assert (again["pages_created"], again["pages_skipped"]) == (0, 1)
    assert _reasons(again) == {"data.csv": LEFT_OUT}


@pytest.mark.parametrize("zipped", [{}, VAULT], ids=["plain", "vault"])
def test_a_zip_of_csvs_with_no_ids_is_no_import(library, zipped):
    for path in ("markdown-zip", "markdown-zip/preview"):
        r = _post(library, {**zipped, "data.csv": "Name\nx\n"}, path)
        assert r.status_code == 400 and r.json()["detail"] == "no .md files in the zip"


def test_a_zip_of_notion_csvs_is_an_import(library):
    report = _import(library, {f"Tasks {A}.csv": "Name\nx\n"})
    assert _lines(library, report, "Tasks") == ["| Name |\n| --- |\n| x |"]


# --- relations --------------------------------------------------------------

@pytest.mark.parametrize("zipped", [{}, VAULT], ids=["plain", "vault"])
def test_a_relation_is_text_outside_notion_with_notion_pages_in_the_library(library, zipped):
    _import(library, {f"Foo {A}.md": "# Foo\n"})
    line = f"Blocked by: Foo (Foo%20{A}.md)"
    report = _import(library, {**zipped, "Note.md": f"{line}\n"})
    assert _lines(library, report, "Note") == [line]
    assert report["links_resolved"] == 0


# --- anchors ----------------------------------------------------------------

def test_a_plain_zip_keeps_a_line_s_end_no_link_names(library):
    report = _import(library, {"Note.md": "E = m c ^2\n\nAlone:\n\n^abc\n"})
    assert _lines(library, report, "Note") == ["E = m c ^2", "Alone:", "^abc"]


def test_a_plain_zip_anchors_what_a_later_note_links(library):
    """The link sorts after the anchor's note: every body is read before the
    first parse, and the second parse gives the block the same id."""
    report = _import(library, {
        "A.md": "Keep it. ^abc\n\nAnd E = m c ^2\n\nAfter a list:\n\n- item\n\n^ListEnd\n",
        "Z.md": "See [[A#^ABC]] and ![[A#^listend]].\n",
    })
    block = {c["content"]: c["id"] for c in _page(library, report, "A")["children"]}
    assert list(block) == ["Keep it.", "And E = m c ^2", "After a list:", "- item"]
    assert _lines(library, report, "Z") == [f"See [[{block['Keep it.']}]] and ![[{block['- item']}]]."]


@pytest.mark.parametrize("link", ["A.md#%5Eabc", "A.md#%5eABC"])
def test_a_plain_zip_anchors_what_a_percent_encoded_link_names(library, link):
    report = _import(library, {"A.md": "Keep it. ^abc\n", "Z.md": f"[x]({link})\n"})
    block = _page(library, report, "A")["children"][0]
    assert block["content"] == "Keep it."
    assert _lines(library, report, "Z") == [f"[[{block['id']}]]"]


def test_a_skipped_note_s_link_still_names_its_anchor(library):
    """A folder synced again with one note edited: the note that links it is
    skipped, and its link still counts."""
    z = "See [[A#^abc]].\n"
    _import(library, {"A.md": "Keep it. ^abc\n", "Z.md": z})
    again = _import(library, {"A.md": "Keep it, edited. ^abc\n", "Z.md": z})
    assert (again["pages_created"], again["pages_skipped"]) == (1, 1)
    assert _lines(library, again, "A") == ["Keep it, edited."]


def test_a_vault_anchors_any_marker(library):
    report = _import(library, {**VAULT, "Note.md": "E = m c ^2\n"})
    assert _lines(library, report, "Note") == ["E = m c"]


def test_notion_has_no_anchors(library):
    home = f"Home {A}.md"
    report = _import(library, {
        home: "# Home\n\nE = m c ^2\n\n^abc\n",
        f"Other {B}.md": f"# Other\n\n[it](Home%20{A}.md#^abc)\n",
    })
    assert _lines(library, report, "Home") == ["E = m c ^2", "^abc"]
    assert _lines(library, report, "Other") == [f"[[{_page(library, report, 'Home')['id']}]]"]


# --- wikilinks --------------------------------------------------------------

def test_notion_s_double_brackets_are_text(library):
    report = _import(library, {
        f"Home {A}.md": "# Home\n\nAn array [[Other]] and [[Gone]].\n",
        f"Other {B}.md": "# Other\n",
    })
    assert _lines(library, report, "Home") == ["An array [[Other]] and [[Gone]]."]
    assert report["warnings"] == [] and report["links_resolved"] == 0


def test_a_note_of_no_id_in_a_notion_zip_keeps_plain_rules(library):
    """A folder with a Notion leftover in it: the leftover's own text stays
    Notion's, and the notes of no id lose no link to it."""
    report = _import(library, {
        "Home.md": "See [[Other]] and [[Other#^abc]], and E = m c ^2\n",
        "Other.md": "Text. ^abc\n",
        f"Old {A}.md": "# Old\n\nAn array [[Other]] and [[Other#^abc]], ^2\n",
    })
    other = _page(library, report, "Other")
    block = other["children"][0]
    assert block["content"] == "Text."
    assert _lines(library, report, "Home") == [f"See [[{other['id']}]] and [[{block['id']}]], and E = m c ^2"]
    assert _lines(library, report, "Old") == ["An array [[Other]] and [[Other#^abc]], ^2"]
    assert report["links_resolved"] == 2 and report["notion"] and report["warnings"] == []


def test_a_notion_note_s_anchor_link_does_not_make_an_anchor(library):
    report = _import(library, {f"Old {A}.md": "# Old\n\n[[Other#^abc]] [x](Other.md#^abc)\n", "Other.md": "Text. ^abc\n"})
    assert _lines(library, report, "Other") == ["Text. ^abc"]


def test_a_plain_zip_reads_wikilinks(library):
    report = _import(library, {"Home.md": "See [[Other]].\n", "Other.md": "Other.\n"})
    assert _lines(library, report, "Home") == [f"See [[{_page(library, report, 'Other')['id']}]]."]


# --- what stays with one dialect ---------------------------------------------

def test_comments_go_in_a_vault_alone(library):
    body = "Before %%hidden%% after, "
    assert _lines(library, _import(library, {**VAULT, "V.md": body + "vault"}), "V") == ["Before  after, vault"]
    assert _lines(library, _import(library, {"P.md": body + "plain"}), "P") == [body + "plain"]
    assert _lines(library, _import(library, {f"N {A}.md": f"# N\n\n{body}notion"}), "N") == [body + "notion"]


def test_a_line_that_closes_its_own_fence_opens_none_in_the_zip_s_passes_either(library):
    body = "```x``` y\n\nkeep %%hidden%% this\n\n```\n%%kept in code%%\n```"
    assert _lines(library, _import(library, {**VAULT, "V.md": body}), "V") == [
        "```x``` y", "keep  this", "```\n%%kept in code%%\n```"]
    assert notion_import._drop_quote_tails("```x``` y\n> q\n>\nnext") == "```x``` y\n> q\nnext"


@pytest.mark.parametrize("code", [
    "````md\n```\n%% note %%\nkeep\n```\n%% two %%\n````",
    "~~~\n```\n%% note %%\n~~~",
    "```py\n    ```\n%% note %%\n    ```\n%% two %%\n```",
], ids=["longer fence", "other mark", "indented past the fence"])
def test_a_comment_in_a_fence_that_holds_fence_lines_stays(library, code):
    body = f"keep %%hidden%% this\n\n{code}\n\nlast %%hidden%% one"
    assert _lines(library, _import(library, {**VAULT, "V.md": body}), "V") == ["keep  this", code, "last  one"]


def test_a_vault_s_title_is_its_file_name_alone(library):
    in_vault = _import(library, {**VAULT, "Name.md": "# A heading\n\nIn a vault.\n"})
    assert _lines(library, in_vault, "Name") == ["# A heading"]
    assert _lines(library, _import(library, {"Name.md": "# A heading\n\nPlain.\n"}), "A heading") == ["Plain."]


def test_notion_s_directories_are_folders_in_a_vault(library):
    report = _import(library, {
        **VAULT,
        f"Home {A}.md": "Home.\n",
        f"Home {A}/Kid {B}.md": "Kid.\n",
        f"Tasks {C}.md": f"[Tasks](Tasks%20{C}.csv)\n",
        f"Tasks {C}.csv": "Name\nx\n",
    })
    kid = next(p for p in report["pages"] if p["title"] == "Kid")
    assert (kid["parent"], kid["folders"]) == ("", [["Home"]])
    assert [p["title"] for p in report["pages"] if p["title"] == "Tasks"] == ["Tasks"]
    assert _lines(library, report, "Tasks")[0].startswith("[Tasks](/api/uploads/")


def test_a_vault_s_notion_leftover_is_not_read_as_notion(library):
    report = _import(library, {**VAULT, f"Plan {A}.md": "<aside>\nA note.\n</aside>\n\n> Quote\n>\n\nStill here.\n"})
    assert _lines(library, report, "Plan")[0] == "<aside>\nA note.\n</aside>"


def test_a_link_to_an_absent_csv_is_missing_outside_notion(library):
    """In Notion's exports it reaches the note of that id."""
    report = _import(library, {**VAULT, f"Tasks {A}.md": "Tasks.\n", "Home.md": f"[Tasks](Tasks%20{A}.csv)\n"})
    assert _lines(library, report, "Home") == [f"[Tasks](Tasks%20{A}.csv)"]
    assert _reasons(report) == {"Home.md": f"Missing or unselected linked file: Tasks%20{A}.csv; the link is kept as written"}


# --- past MAX_BLOCKS ----------------------------------------------------------

def test_a_stub_s_text_past_max_blocks_stays_out_and_the_table_comes_in(library, monkeypatch):
    """The stub and the table pass the first parse each and cross the limit
    together, which no parse at the write stage may be the first to find: a
    page left out there has its links and rows made already."""
    monkeypatch.setattr(markdown_import, "MAX_BLOCKS", 40)
    monkeypatch.setattr(mzi, "MAX_BLOCKS", 40)
    monkeypatch.setattr(notion_import, "TABLE_ROWS", 1)
    rows = "".join(f"r{n}\n" for n in range(30))
    text = "\n\n".join(f"Paragraph {n}." for n in range(30))
    report = _import(library, {
        f"Home {A}.md": f"# Home\n\n[People](Home%20{A}/People%20{B}.md)\n",
        f"Home {A}/People {B}.md": f"# People\n\n{text}\n\n[People](People%20{B}.csv)\n",
        f"Home {A}/People {B}.csv": f"Name\n{rows}",
        f"Home {A}/People {B}/r0 {C}.md": "# r0\n",
    })
    assert _reasons(report) == {f"Home {A}/People {B}.md": "has more than 40 blocks with its table: its text is not imported"}
    assert len(_lines(library, report, "People")) == 30
    people = next(p for p in report["pages"] if p["title"] == "People")
    assert next(p for p in report["pages"] if p["title"] == "r0")["parent"] == people["id"]
    assert _lines(library, report, "Home") == [f"[[{people['id']}]]"]


READABLE = "---\ntitle: {0}\n---\n\n# {0}\n\n- a\n  - - b\n"


def test_a_note_written_as_the_readable_export_reads_its_bullets_as_blocks(library):
    report = _import(library, {"Out.md": READABLE.format("Out")})
    assert [b["content"] for b in _page(library, report, "Out")["children"]] == ["a"]
    assert [b["content"] for b in _page(library, report, "Out")["children"][0]["children"]] == ["- b"]


def test_a_vault_or_a_notion_page_of_that_shape_keeps_its_bullets(library):
    vault = _import(library, {**VAULT, "Out.md": READABLE.format("Out")})
    assert _lines(library, vault, "Out") == ["- a"]
    notion = _import(library, {f"Page {A}.md": READABLE.format("Page")})
    assert _lines(library, notion, "Page") == ["- a"]


def test_a_note_of_no_id_in_a_notion_zip_is_read_as_any_note(library):
    report = _import(library, {f"Page {A}.md": "# Page\n", "Out.md": READABLE.format("Out")})
    assert _lines(library, report, "Out") == ["a"]


def test_notion_s_headings_hold_nothing_and_others_nest(library):
    body = "# Page\n\nIntro.\n\n## Section\n\nUnder it.\n\n- item\n  - nested\n"
    notion = _import(library, {f"Page {A}.md": body})
    page = _page(library, notion, "Page")
    assert [b["content"] for b in page["children"]] == ["Intro.", "## Section", "Under it.", "- item"]
    assert [b["content"] for b in page["children"][3]["children"]] == ["- nested"]
    plain = _import(library, {"Other.md": body.replace("Page", "Other")})
    assert _lines(library, plain, "Other") == ["Intro.", "## Section"]
    vault = _import(library, {**VAULT, "Third.md": "## Section\n\nUnder it.\n"})
    assert _lines(library, vault, "Third") == ["## Section"]


def test_a_note_of_no_id_in_a_notion_zip_keeps_its_nesting(library):
    report = _import(library, {f"Home {A}.md": "# Home\n\n## Here\n\nSub.\n", "Loose.md": "# Loose\n\n## There\n\nSub.\n"})
    assert _lines(library, report, "Home") == ["## Here", "Sub."]
    assert _lines(library, report, "Loose") == ["## There"]


def test_notion_s_tags_are_text_and_its_code_math_and_targets_stay():
    escape = notion_import._escape_tags
    assert escape("Use <tag> and </tag>, a<b, <image name>:<tag>") == (
        r"Use \<tag> and \</tag>, a\<b, \<image name>:\<tag>")
    assert escape(r"<u>under</u> and 1 < 2 and <3 and \<x>") == r"<u>under</u> and 1 < 2 and <3 and \<x>"
    assert escape('> [!info] <img src="i.svg" width="40px" /> and <b>') == (
        r'> [!info] <img src="i.svg" width="40px" /> and \<b>')
    assert escape("`<a>` ``<b> ` <c>`` $x<y$ $$p<q$$ [l <d>](<e f.md>) ![<g>](h<i.png) <j>") == (
        r"`<a>` ``<b> ` <c>`` $x<y$ $$p<q$$ [l \<d>](<e f.md>) ![\<g>](h<i.png) \<j>")
    assert escape("cost $5 <b> then $6") == r"cost $5 \<b> then $6"
    fenced = "```html\n<div>\n```\n    ```\n    <p>\n    ```\n$$\nx<y\n$$\n<k>"
    assert escape(fenced) == fenced[:-3] + r"\<k>"


def test_code_in_a_quote_or_on_a_marker_s_line_stays_and_a_fence_keeps_to_its_quote():
    escape = notion_import._escape_tags
    for code in ("> ```html\n> <div>\n> ```", "> > ```\n> > <div>\n> > ```", "   > ~~~\n   > <p>\n   > ~~~",
                 "- ```html\n  <div>\n  ```", "1. ```html\n   <p>\n   ```", "> - ```\n>   <p>\n>   ```",
                 "```\n> ```\n<a>\n```", "> ```\n> > ```\n> <a>\n> ```", "```\n- ```\n<a>\n```"):
        assert escape(f"{code}\n<k>") == code + "\n" + r"\<k>"
    assert escape("> ```\n> <a>\n\n<k>") == "> ```\n> <a>\n\n" + r"\<k>"
    assert escape("```x``` <a>\n1. ```y``` <b>\n<k>") == "```x``` \\<a>\n1. ```y``` \\<b>\n\\<k>"


def test_a_notion_callout_s_code_keeps_its_tags(library):
    report = _import(library, {f"Page {A}.md": "# Page\n\n<aside>\nLook:\n\n```html\n<div>hi</div>\n```\n\n</aside>\n\nA <tag>.\n"})
    assert _lines(library, report, "Page") == ["> [!info] Look:\n>\n> ```html\n> <div>hi</div>\n> ```", r"A \<tag>."]


def test_a_database_s_cells_are_escaped_as_a_note_is(library):
    md, _ = notion_import._csv_to_markdown('Name,<k>\nWrite,List<String>\nOther,"a <b>\nc | `<d>` <u>e</u>"\n'.encode())
    assert md.split("\n") == [
        r"| Name | \<k> |", "| --- | --- |", r"| Write | List\<String> |", r"| Other | a \<b><br>c \| `<d>` <u>e</u> |"]
    report = _import(library, {
        f"Top {A}.md": f"# Top\n\n[One](One%20{B}.csv)\n",
        f"One {B}.csv": f"Name,Kind,Link\nWrite,<x>,Re<lated (Related%20{C}.md)\n",
        f"Write {D}.md": "# Write\n\nKind: <x>\n",
        f"Related {C}.md": "# Related\n",
    })
    pages = {page["title"]: page for page in report["pages"]}
    assert pages["Write"]["parent"] == pages["One"]["id"]
    assert _lines(library, report, "One") == [
        "| Name | Kind | Link |\n| --- | --- | --- |\n" + rf"| Write | \<x> | [[{pages['Related']['id']}]] |"]


def test_a_notion_note_s_tag_is_text_where_another_s_stays_html(library):
    text = "# Page\n\nA <tag> and <u>under</u>.\n"
    report = _import(library, {f"Page {A}.md": text, "Other.md": text.replace("Page", "Other")})
    assert _lines(library, report, "Page") == [r"A \<tag> and <u>under</u>."]
    assert _lines(library, report, "Other") == ["A <tag> and <u>under</u>."]


def test_a_row_page_s_values_match_the_cells_as_notion_wrote_them(library):
    """A database's value is its CSV's, so a row goes by the line before
    that line is escaped."""
    report = _import(library, {
        f"Top {A}.md": f"# Top\n\n[One](One%20{B}.csv)\n\n[Two](Two%20{C}.csv)\n",
        f"One {B}.csv": "Name,Kind\nWrite,<x>\n",
        f"Two {C}.csv": "Name,Kind\nWrite,<y>\n",
        f"Write {D}.md": "# Write\n\nKind: <y>\n",
    })
    pages = {page["title"]: page for page in report["pages"]}
    assert pages["Write"]["parent"] == pages["Two"]["id"]
    assert _lines(library, report, "Write") == [r"Kind: \<y>"]
