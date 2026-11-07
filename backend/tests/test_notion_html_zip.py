"""A zip of Notion's HTML export through the import (gamma/notion_import.py
``NotionHtmlSource``, gamma/markdown_zip_import.py): the tree the Markdown export
of the same pages gives, its tables and links, and what a page's own HTML keeps.
How one page reads is test_notion_html.py's."""

import io
import itertools
import json
import re
import zipfile

import pytest

import gamma.markdown_zip_dialects as dialects
import gamma.markdown_zip_import as zip_import
import gamma.notion_import as ni
from conftest import login, make_folder, make_user
from gamma.markdown_zip_dialects import dialect_of
from notion_fixtures import export_zip

A, B, C, D = "a" * 32, "b" * 32, "c" * 32, "d" * 32
_ACCOUNTS = itertools.count()


def html(body="", title="A page", page_id=A, header=""):
    dashed = f"{page_id[:8]}-{page_id[8:12]}-{page_id[12:16]}-{page_id[16:20]}-{page_id[20:]}" if page_id else ""
    return (f'<html><head><title>{title}</title></head><body><article id="{dashed}" class="page sans">'
            f'<header>{f"""<h1 class="page-title">{title}</h1>""" if title else ""}{header}</header>'
            f'<div class="page-body">{body}</div></article></body></html>')


def collection(db_id, title, *columns):
    dashed = f"{db_id[:8]}-{db_id[8:12]}-{db_id[12:16]}-{db_id[16:20]}-{db_id[20:]}"
    heads = "".join(f"<th>{name}</th>" for name in columns)
    return (f'<div id="{dashed}" class="collection-content"><h4 class="collection-title">{title}</h4>'
            f'<div class="collection-content-wrapper"><table class="collection-content"><thead><tr>{heads}</tr></thead>'
            f'<tbody><tr><td>Rows are the CSV\'s</td></tr></tbody></table></div></div>')


@pytest.fixture
def library():
    """An empty library of the test's own, so nothing a zip holds is found
    there."""
    name = f"nh_{next(_ACCOUNTS)}"
    make_user(name, "pw-nh")
    return login(name, "pw-nh")


def _zip(files: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return out.getvalue()


def _post(client, data, path="markdown-zip", **form):
    return client.post(f"/api/import/{path}", files={"file": ("export.zip", data, "application/zip")}, data=form)


def _import(client, data) -> dict:
    r = _post(client, data if isinstance(data, bytes) else _zip(data))
    assert r.status_code == 200, r.text
    return r.json()


def _tree(client, report, title) -> dict:
    page = next(p for p in report["pages"] if p["title"] == title)
    return client.get(f"/api/blocks/{page['id']}/subtree").json()["block"]


def _lines(client, report, title) -> list:
    def walk(node):
        return [line for child in node["children"] for line in [child["content"], *walk(child)]]
    return walk(_tree(client, report, title))


def _reasons(report) -> list:
    return [w["reason"] for w in report["warnings"]]


def _pages(report) -> dict:
    """{title: (the title of the page it is inside, its folders)}."""
    titles = {p["id"]: p["title"] for p in report["pages"]}
    return {p["title"]: (titles.get(p["parent"], ""), p["folders"]) for p in report["pages"]}


def _counts(client, report) -> dict:
    return {p["title"]: len(_lines(client, report, p["title"])) for p in report["pages"]}


# --- the same export in its two formats --------------------------------------------

def test_the_html_zip_gives_the_markdown_zip_s_tree():
    make_user("nh_md", "pw-nh-md")
    make_user("nh_html", "pw-nh-html")
    md_user, html_user = login("nh_md", "pw-nh-md"), login("nh_html", "pw-nh-html")
    md, read = _import(md_user, export_zip("roundtrip_md")), _import(html_user, export_zip("roundtrip_html"))
    assert read["warnings"] == [] and read["notion"] and not read["obsidian"]
    assert read["pages_created"] == md["pages_created"] == 10
    assert _pages(read) == _pages(md)
    assert read["links_resolved"] == md["links_resolved"]
    assert read["blocks_imported"] == md["blocks_imported"]
    assert _counts(html_user, read) == _counts(md_user, md)


def test_no_page_of_the_html_zip_is_a_bundled_file(library):
    report = _import(library, export_zip("roundtrip_html"))
    assert report["assets_stored"] == 0
    assert all(p["kind"] == "page" and p["action"] == "create" for p in report["pages"])


def test_a_preview_finds_what_the_import_does(library):
    data = export_zip("roundtrip_html")
    preview = _post(library, data, "markdown-zip/preview")
    assert preview.status_code == 200, preview.text
    report = _import(library, data)
    for key in ("pages_created", "links_resolved", "assets_stored", "warnings"):
        assert preview.json()[key] == report[key], key


def test_a_second_import_adds_nothing(library):
    data = export_zip("roundtrip_html")
    first = _import(library, data)
    second = _import(library, data)
    assert second["pages_created"] == 0 and second["pages_skipped"] == first["pages_created"]


def test_a_page_imported_from_markdown_stays_as_it_is(library):
    _import(library, export_zip("roundtrip_md"))
    report = _import(library, export_zip("roundtrip_html"))
    assert report["pages_created"] == 0 and report["pages_skipped"] == 10


# --- what the fixture's pages keep --------------------------------------------------

def test_a_toggle_is_folded_with_its_children_and_a_childless_one_is_text(library):
    report = _import(library, export_zip("roundtrip_html"))

    def by_content(node, out):
        for child in node["children"]:
            out[child["content"]] = child
            by_content(child, out)
        return out

    blocks = by_content(_tree(library, report, "Round trip"), {})
    for name in ("Toggle fixture", "## Expandable heading fixture"):
        assert blocks[name]["properties"].get("collapsed") is True and blocks[name]["children"]


def test_a_table_s_columns_are_the_view_s(library):
    report = _import(library, export_zip("roundtrip_html"))
    [table] = _lines(library, report, "Cases")
    assert table.split("\n")[0] == "| Name | Select | Multi-select | Date | Number | Checkbox | URL | Person | Formula | Groups |"


def test_a_relation_is_a_mention_in_the_cell_and_on_the_row_s_page(library):
    report = _import(library, export_zip("roundtrip_html"))
    group = next(p for p in report["pages"] if p["title"] == "Group A")["id"]
    assert f"| [[{group}]] |" in _lines(library, report, "Cases")[0]
    assert f"Groups: [[{group}]]" in _lines(library, report, "RT-001 Alpha")[0]


def test_a_database_on_a_page_is_a_mention_of_its_table(library):
    report = _import(library, export_zip("roundtrip_html"))
    lines = _lines(library, report, "Round trip")
    for title in ("Cases", "Groups"):
        table = next(p for p in report["pages"] if p["title"] == title)["id"]
        assert f"[[{table}]]" in lines


# --- a database, by hand -------------------------------------------------------------

def test_a_column_the_view_leaves_out_comes_after_the_others(library):
    report = _import(library, {
        f"Home {A}.html": html(collection(B, "Tasks", "Name", "Due", "Status")),
        f"Home/Tasks {B}.csv": "Name,Owner,Status,Due\nOne,Ada,Done,Today\n"})
    assert _lines(library, report, "Tasks")[0].split("\n")[0] == "| Name | Due | Status | Owner |"


def test_a_database_s_name_the_file_cut_comes_back_whole(library):
    whole = "A database whose name is well past the fifty characters of a file name"
    report = _import(library, {f"Home {A}.html": html(collection(B, whole, "Name")),
                               f"Home/{whole[:49]} {B}.csv": "Name\nOne\n"})
    assert _pages(report)[whole][0] == "A page"


def test_a_database_s_name_the_file_could_not_hold_comes_back_too(library):
    report = _import(library, {f"Home {A}.html": html(collection(B, "Q&A: costs", "Name")),
                               f"Home/QA costs {B}.csv": "Name\nOne\n"})
    assert "Q&A: costs" in _pages(report)


def test_a_database_with_no_csv_is_its_name(library):
    report = _import(library, {f"Home {A}.html": html("<p>Above</p>" + collection(B, "Linked view", "Name"))})
    assert _lines(library, report, "A page") == ["Above", "Linked view"]
    assert report["warnings"] == []


def test_a_full_page_database_is_one_page_with_its_rows_inside(library):
    table = '<table class="collection-content"><thead><tr><th>Name</th><th>Due</th></tr></thead></table>'
    report = _import(library, {
        f"Tasks {A}.html": html(table, "Tasks", A, '<p class="page-description">What is left to do</p>'),
        f"Tasks {A}.csv": "Due,Name\nToday,One\n",
        f"Tasks {A}/One {B}.html": html("<p>A row</p>", "One", B)})
    assert sorted(p["title"] for p in report["pages"]) == ["One", "Tasks"]
    assert _pages(report)["One"][0] == "Tasks"
    assert _lines(library, report, "Tasks") == ["What is left to do", "| Name | Due |\n| --- | --- |\n| One | Today |"]


# --- a flat export ------------------------------------------------------------------

def test_a_flat_html_export_gets_its_tree_from_its_links_and_rows(library):
    report = _import(library, {
        f"Home {A}.html": html(
            f'<figure class="link-to-page"><a href="Child%20{B}.html">Child</a></figure>'
            + collection(C, "Tasks", "Name"), "Home", A),
        f"Child {B}.html": html("<p>Inside</p>", "Child", B),
        f"Tasks {C}.csv": "Name\nOne\n",
        f"One {D}.html": html("<p>A row</p>", "One", D)})
    assert {title: up for title, (up, _) in _pages(report).items()} == {
        "Home": "", "Child": "Home", "Tasks": "Home", "One": "Tasks"}


def test_a_row_s_page_is_told_by_what_its_properties_show(library):
    """Two rows of one name, in two databases: a value the page shows as the
    CSV does tells them apart. A date (ISO here, long there) would not."""
    def row(page_id, status):
        return html(f'<p>{status}</p>', "One", page_id, f'<table class="properties"><tbody><tr><th>Status</th>'
                                                        f'<td><span class="selected-value">{status}</span></td></tr>'
                                                        f'<tr><th>Due</th><td><time>@November 6, 2026</time></td></tr></tbody></table>')
    report = _import(library, {
        f"Open {A}.csv": 'Name,Status,Due\nOne,Open,"November 6, 2026"\n',
        f"Shut {B}.csv": 'Name,Status,Due\nOne,Shut,"November 6, 2026"\n',
        f"One {C}.html": row(C, "Shut"),
        f"One {D}.html": row(D, "Open")})
    ids = {p["id"]: p for p in report["pages"]}
    last_line = {p["id"]: library.get(f"/api/blocks/{p['id']}/subtree").json()["block"]["children"][-1]["content"]
                 for p in report["pages"] if p["parent"]}
    assert {line: ids[ids[pid]["parent"]]["title"] for pid, line in last_line.items()} == {"Shut": "Shut", "Open": "Open"}


# --- what stays what it is -------------------------------------------------------------

def test_literal_text_stays_literal_through_the_import(library):
    report = _import(library, {f"Home {A}.html": html("<p>$5 and &lt;tag&gt; and [a](b.html) and $6</p>"),
                               f"b {B}.html": html("<p>Other</p>", "Other", B)})
    assert _lines(library, report, "A page")[0] == r"\$5 and \<tag> and \[a](b.html) and \$6"
    assert report["links_resolved"] == 0 and report["warnings"] == []


def test_a_link_in_code_or_a_formula_is_no_link(library):
    target = f"b%20{B}.html"
    report = _import(library, {
        f"Home {A}.html": html(
            f'<p><code>[a]({target})</code></p><pre class="code"><code>[a]({target})</code></pre>'
            f'<figure class="equation" data-notion-equation="[a]({target})"></figure>'
            f'<p><span class="notion-text-equation-token" data-notion-inline-equation="[a]({target})"></span></p>'),
        f"b {B}.html": html("<p>Other</p>", "Other", B)})
    assert report["links_resolved"] == 0 and report["warnings"] == []
    assert _lines(library, report, "A page") == [
        f"`[a]({target})`", f"```\n[a]({target})\n```", f"$$\n[a]({target})\n$$", f"$[a]({target})$"]


def test_a_preview_leaves_a_link_in_a_fence_alone_too(library):
    target = f"b%20{B}.html"
    data = _zip({f"Home {A}.html": html(f'<pre class="code"><code>[a]({target})</code></pre>'),
                 f"b {B}.html": html("<p>Other</p>", "Other", B)})
    preview = _post(library, data, "markdown-zip/preview").json()
    assert preview["links_resolved"] == 0 and preview["warnings"] == []


def test_a_link_with_code_in_its_label_is_a_link(library):
    report = _import(library, {
        f"Home {A}.html": html(f'<p>See <a href="b%20{B}.html"><code>x</code></a></p>'),
        f"b {B}.html": html("<p>Other</p>", "Other", B)})
    other = next(p for p in report["pages"] if p["title"] == "Other")["id"]
    assert _lines(library, report, "A page") == [f"See [[{other}]]"]


@pytest.mark.parametrize("title", ["a [b", "a ]b", "[WIP] Plan", "Plan [v2]", "a [b] c"])
@pytest.mark.parametrize("markup", [
    '<p><a href="{target}">{title}</a></p>',
    '<figure class="link-to-page"><a href="{target}">{title}</a></figure>',
])
def test_a_link_to_a_page_with_brackets_in_its_title_is_one_mention(library, markup, title):
    target = title.replace("[", "%5B").replace("]", "%5D").replace(" ", "%20") + f"%20{B}.html"
    report = _import(library, {f"Home {A}.html": html(markup.format(target=target, title=title)),
                               f"{title} {B}.html": html("<p>Other</p>", title, B)})
    other = next(p for p in report["pages"] if p["title"] == title)["id"]
    assert _lines(library, report, "A page") == [f"[[{other}]]"]
    assert report["links_resolved"] == 1 and report["warnings"] == []


def test_a_relation_in_a_table_cell_with_brackets_is_one_mention(library):
    cell = f'<td><a href="Plan%20%5Bv2%5D%20{B}.html">Plan [v2]</a></td>'
    report = _import(library, {f"Home {A}.html": html(f'<table class="simple-table"><tbody><tr>{cell}</tr></tbody></table>'),
                               f"Plan [v2] {B}.html": html("<p>Other</p>", "Plan [v2]", B)})
    other = next(p for p in report["pages"] if p["title"] == "Plan [v2]")["id"]
    assert f"| [[{other}]] |" in _lines(library, report, "A page")[0]


def test_a_database_with_brackets_in_its_name_is_one_mention(library):
    report = _import(library, {f"Home {A}.html": html(collection(B, "[Q3] Tasks", "Name")),
                               f"Home/Q3 Tasks {B}.csv": "Name\nOne\n"})
    table = next(p for p in report["pages"] if p["title"] == "[Q3] Tasks")["id"]
    assert _lines(library, report, "A page") == [f"[[{table}]]"]


def test_a_web_link_keeps_the_words_of_its_label(library):
    report = _import(library, {f"Home {A}.html": html('<p><a href="https://x.org/a.html">[WIP] Plan</a></p>')})
    assert "WIP" in _lines(library, report, "A page")[0] and "[" in _lines(library, report, "A page")[0]


@pytest.mark.parametrize("markup", [
    '<figure><div class="source"><a href="d/report%20%5Bv2%5D.pdf">report [v2].pdf</a></div></figure>',
    '<p><a href="d/report%20%5Bv2%5D.pdf">report [v2].pdf</a></p>',
])
def test_a_file_with_brackets_in_its_name_is_one_link_to_its_upload(library, markup):
    report = _import(library, {f"Home {A}.html": html(markup), "d/report [v2].pdf": b"%PDF-1.4"})
    assert re.fullmatch(r"\[report v2\.pdf\]\(/api/uploads/\w+\.pdf\)", _lines(library, report, "A page")[0])
    assert report["assets_stored"] == 1 and report["warnings"] == []


@pytest.mark.parametrize("markup", [
    collection(B, "[WIP] Tasks", "Name"),      # (no CSV: its name, and no link)
    '<figure><div class="source"><a href="https://x.org/a.pdf">[WIP] Tasks</a></div></figure>',
])
def test_a_name_that_links_into_no_file_of_the_zip_keeps_its_brackets(library, markup):
    report = _import(library, {f"Home {A}.html": html(markup)})
    assert r"\[WIP" in _lines(library, report, "A page")[0]


def test_a_relation_written_out_in_a_page_s_text_is_text(library):
    line = f"Other (Other%20{B}.html)"
    report = _import(library, {f"Home {A}.html": html(f"<p>{line}</p>"),
                               f"Other {B}.html": html("<p>Other</p>", "Other", B)})
    assert line in _lines(library, report, "A page")[0]


def test_an_escaped_link_is_text_in_a_markdown_note_too(library):
    report = _import(library, {"Home.md": "See \\[a](b.md) and [b](b.md)\n", "b.md": "Other\n"})
    other = next(p for p in report["pages"] if p["title"] == "b")["id"]
    assert _lines(library, report, "Home") == [f"See \\[a](b.md) and [[{other}]]"]


def test_a_link_in_code_stays_as_it_was_in_a_markdown_note(library):
    report = _import(library, {"Home.md": "`[b](b.md)`\n", "b.md": "Other\n"})
    other = next(p for p in report["pages"] if p["title"] == "b")["id"]
    assert _lines(library, report, "Home") == [f"`[[{other}]]`"]


# --- files around the pages ------------------------------------------------------------

def test_a_picture_and_a_file_beside_a_page_are_uploaded(library):
    png = b"\x89PNG\r\n\x1a\n"
    report = _import(library, {
        f"Home {A}.html": html(
            f'<figure class="image"><a href="Home%20{A}/p.png"><img style="width:300px" src="Home%20{A}/p.png"/></a></figure>'
            f'<figure><div class="source"><a href="Home%20{A}/notes.txt">notes.txt</a></div></figure>'),
        f"Home {A}/p.png": png,
        f"Home {A}/notes.txt": "words"})
    assert report["assets_stored"] == 2
    picture, file = _lines(library, report, "A page")
    assert picture.startswith("![|300](/api/uploads/") and file.startswith("[notes.txt](/api/uploads/")


def test_a_page_left_out_of_a_selection_is_not_uploaded_as_a_file(library):
    data = _zip({f"Home {A}.html": html(f'<p><a href="b%20{B}.html">Other</a></p>'),
                 f"b {B}.html": html("<p>Other</p>", "Other", B)})
    r = _post(library, data, selected=json.dumps([f"Home {A}.html"]))
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["assets_stored"] == 0 and [p["title"] for p in report["pages"]] == ["A page"]
    assert _lines(library, report, "A page") == [f"[Other](b%20{B}.html)"]
    assert _reasons(report) == [f"Missing or unselected linked file: b%20{B}.html; the link is kept as written"]


def test_an_index_html_stays_a_file(library):
    index = '<html><body><div class="page-body"><a href="Home.html">Home</a></div></body></html>'
    report = _import(library, {f"Home {A}.html": html("<p>Text</p>"), "index.html": index})
    assert [p["title"] for p in report["pages"]] == ["A page"]


# --- what a page does not say -------------------------------------------------------------

def test_a_page_of_no_title_or_id_takes_them_from_its_file(library):
    report = _import(library, {f"From the name {A}.html": html("<p>Text</p>", title="", page_id="")})
    assert [p["title"] for p in report["pages"]] == ["From the name"]
    props = _tree(library, report, "From the name")["properties"]
    assert props["notion_id"] == A
    assert report["notion"]


def test_the_id_in_a_page_counts_where_its_file_has_none(library):
    report = _import(library, {"Saved page.html": html("<p>Text</p>", "Saved page", B)})
    assert _tree(library, report, "Saved page")["properties"]["notion_id"] == B


def test_a_page_nested_too_deep_says_so(library):
    report = _import(library, {f"Deep {A}.html": html("<div>" * 250 + "<p>Far down</p>" + "</div>" * 250, "Deep")})
    assert any("too deep" in reason for reason in _reasons(report))
    assert _lines(library, report, "Deep") == ["Far down"]


def test_a_page_not_in_utf8_is_left_out_with_a_warning(library):
    bad = html("<p>caf\xe9</p>").encode("latin-1")
    report = _import(library, {f"Home {A}.html": bad, f"Other {B}.html": html("<p>Fine</p>", "Other", B)})
    assert [p["title"] for p in report["pages"]] == ["Other"]
    assert "not UTF-8" in _reasons(report)


def test_a_page_past_the_blocks_cap_is_left_out_with_a_warning(library, monkeypatch):
    monkeypatch.setattr(ni, "MAX_BLOCKS", 2)
    report = _import(library, {f"Home {A}.html": html("<p>a</p><p>b</p><p>c</p>"),
                               f"Other {B}.html": html("<p>Fine</p>", "Other", B)})
    assert [p["title"] for p in report["pages"]] == ["Other"]
    assert any("blocks" in reason for reason in _reasons(report))


# --- which zip is one --------------------------------------------------------------------

@pytest.mark.parametrize("paths,html_pages,name", [
    (["Home.html"], {"Home.html"}, "notion"),
    (["Home.html", "note.md"], {"Home.html"}, "notion"),
    (["Home.html", ".obsidian/app.json"], {"Home.html"}, "vault"),
    (["Home.html", "note.md"], set(), "plain"),
])
def test_a_page_of_the_html_export_makes_the_zip_notion_s(paths, html_pages, name):
    assert dialect_of(paths, html_pages).name == name


def _past_the_head(title, page_id):
    """A page whose properties push its body past the first 64 KB."""
    links = ", ".join(f'<a href="Small%20{B}.html">Small</a>' for _ in range(1500))
    page = html("<p>Words</p>", title, page_id,
                f'<table class="properties"><tbody><tr><th>Related</th><td>{links}</td></tr></tbody></table>')
    assert page.index("page-body") > 64 * 1024
    return page


def test_a_page_with_long_properties_is_a_page(library):
    report = _import(library, {f"Big {A}.html": _past_the_head("Big", A),
                               f"Small {B}.html": html(f'<p><a href="Big%20{A}.html">Big</a></p>', "Small", B),
                               f"Big {A}/Kid {C}.html": html("<p>Below</p>", "Kid", C)})
    big = next(p for p in report["pages"] if p["title"] == "Big")["id"]
    assert _pages(report)["Kid"][0] == "Big" and report["assets_stored"] == 0 and report["warnings"] == []
    assert "Words" in _lines(library, report, "Big") and _lines(library, report, "Small") == [f"[[{big}]]"]


def test_a_zip_of_one_page_with_long_properties_is_notion_s(library):
    assert [p["title"] for p in _import(library, {f"Big {A}.html": _past_the_head("Big", A)})["pages"]] == ["Big"]


def test_a_page_past_the_size_cap_says_so_wherever_its_body_starts(library, monkeypatch):
    for module in (dialects, zip_import):
        monkeypatch.setattr(module, "MAX_MARKDOWN_BYTES", 65 * 1024)
    report = _import(library, {f"Big {A}.html": _past_the_head("Big", A),
                               f"Small {B}.html": html("<p>Fine</p>", "Small", B)})
    assert [p["title"] for p in report["pages"]] == ["Small"] and report["assets_stored"] == 0
    assert _reasons(report) == ["file exceeds 5 MB"]


def test_a_long_html_that_opens_as_a_page_and_never_shows_a_body_stays_a_file(library):
    other = '<article class="page sans"><div class="body">' + "x" * (70 * 1024) + "</div></article>"
    report = _import(library, {f"Home {A}.html": html("<p>Text</p>"), "other.html": other})
    assert [p["title"] for p in report["pages"]] == ["A page"]


def test_a_vault_s_html_stays_a_file(library):
    report = _import(library, {".obsidian/app.json": "{}", "note.md": "Text\n", f"Page {A}.html": html()})
    assert [p["title"] for p in report["pages"]] == ["note"]


def test_html_of_no_notion_page_is_no_import(library):
    r = _post(library, _zip({"page.html": "<html><body><article class='post'><p>Text</p></article></body></html>"}))
    assert r.status_code == 400 and r.json()["detail"] == "no .md files in the zip"


def test_a_notion_page_s_folder_is_filed_as_the_markdown_export_s(library):
    folder = make_folder(library, "NH dest")
    r = library.post("/api/import/markdown-zip", files={"file": ("x.zip", export_zip("roundtrip_html"), "application/zip")},
                     data={"folder": folder})
    assert r.status_code == 200, r.text
    tops = [p for p in r.json()["pages"] if not p["parent"]]
    assert [p["title"] for p in tops] == ["Round trip"]
    assert tops[0]["folders"] == [["NH dest"]]


def test_a_long_page_damaged_past_its_head_fails_nothing(library):
    """The head tells it a page: only reading the whole of it finds the damage."""
    page = html("<p>padding</p>" * 6000 + "<p>end</p>")
    assert len(page) > dialects._HTML_HEAD
    files = {"Readable note.md": "# Readable note\n\ntext\n", f"Long {A}.html": page}
    data = _zip(files).replace(b"<p>end</p>", b"<p>enf</p>")
    for path in ("markdown-zip/preview", "markdown-zip"):
        report = _post(library, data, path=path)
        assert report.status_code == 200, report.text
        assert [p["title"] for p in report.json()["pages"]] == ["Readable note"]
        reasons = {w["title"]: w["reason"] for w in report.json()["warnings"]}
        assert reasons[f"Long {A}.html"] == "this file could not be read"
