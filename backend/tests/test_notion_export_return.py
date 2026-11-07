"""Gamma → the Notion export's zip → Gamma: the zip read straight back, with no
Notion in between, is a Plain zip (no file name holds an id). What that keeps
and what it cannot is pinned here; what Notion's importer makes of the zip is
not, since nothing here has met it."""

import io
import zipfile

import pytest

from conftest import login, make_folder, make_user


@pytest.fixture(scope="module")
def user():
    make_user("nr_user", "nr-user-pw")
    return login("nr_user", "nr-user-pw")


def _page(c, title, **body):
    r = c.post("/api/pages", json={"title": title, **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _note(text, *children, **properties):
    return {"content": text, "properties": properties, "children": list(children)}


def _put(c, page_id, nodes):
    def numbered(items, at):
        return [{"id": f"{at}-{n}", **item, "children": numbered(item["children"], f"{at}-{n}")}
                for n, item in enumerate(items)]
    r = c.put(f"/api/blocks/{page_id}/children", json={"blocks": numbered(nodes, page_id)})
    assert r.status_code == 200, r.text


def _export(c, folder) -> bytes:
    r = c.get(f"/api/folders/{folder}/export", params={"mode": "notion", "sub_pages": 1})
    assert r.status_code == 200, r.text
    return r.content


def _files(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _back(c, folder) -> dict:
    data = _export(c, folder)
    r = c.post("/api/import/markdown-zip", files={"file": ("export.zip", data, "application/zip")},
               data={"folder": make_folder(c, f"{folder} back")})
    assert r.status_code == 200, r.text
    return r.json()


def _lines(c, report, title) -> list:
    page = next(p for p in report["pages"] if p["title"] == title)

    def walk(node, depth=0):
        return [item for child in node["children"]
                for item in [(depth, child["content"]), *walk(child, depth + 1)]]
    return walk(c.get(f"/api/blocks/{page['id']}/subtree").json()["block"])


def test_the_zip_reads_back_as_a_plain_zip_of_its_pages(user):
    folder = make_folder(user, "NR plain")
    _put(user, _page(user, "Paper", folders=[folder]), [_note("A note")])
    report = _back(user, folder)
    assert not report["notion"] and not report["obsidian"]
    assert [p["title"] for p in report["pages"]] == ["Paper"]
    assert report["warnings"] == []


def test_bullets_numbers_and_to_dos_stay_lists(user):
    folder = make_folder(user, "NR lists")
    _put(user, _page(user, "Lists", folders=[folder]),
         [_note("- one", _note("- nested")), _note("2. two"), _note("- [x] done"), _note("- [ ] open")])
    assert _lines(user, _back(user, folder), "Lists") == [
        (0, "- one"), (1, "- nested"), (0, "2. two"), (0, "- [x] done"), (0, "- [ ] open")]


def test_a_plain_block_with_children_comes_back_a_bullet(user):
    """A file has no other way to nest a paragraph. No text goes."""
    folder = make_folder(user, "NR nested")
    _put(user, _page(user, "Nested", folders=[folder]), [_note("Parent", _note("Child"))])
    assert _lines(user, _back(user, folder), "Nested") == [(0, "- Parent"), (1, "- Child")]


def test_a_second_trip_writes_the_same_file(user):
    folder = make_folder(user, "NR twice")
    nodes = [_note("Text"), _note("## Heading"), _note("- one", _note("- nested")), _note("```py\nx = 1\n```"),
             _note("$$\nx^2\n$$"), _note("> quoted"), _note("| a | b |\n| --- | --- |\n| 1 | 2 |")]
    _put(user, _page(user, "Twice", folders=[folder]), nodes)
    back = make_folder(user, "NR twice back")
    data = _export(user, folder)
    r = user.post("/api/import/markdown-zip", files={"file": ("export.zip", data, "application/zip")},
                  data={"folder": back})
    assert r.status_code == 200, r.text
    assert _files(_export(user, back)) == _files(data)


def test_a_page_that_opens_with_an_h1_keeps_its_title(user):
    """A file's opening H1 is read as its title, so a page whose notes open
    with one gets its title line above it, which a page opening any other way
    has no need of. Whether Notion takes the name or the H1, no title is lost."""
    folder = make_folder(user, "NR h1")
    _put(user, _page(user, "Named", folders=[folder]), [_note("# Opening"), _note("Text")])
    _put(user, _page(user, "Plain", folders=[folder]), [_note("Text"), _note("# Later")])
    files = _files(_export(user, folder))
    assert files["Named.md"].startswith(b"# Named\n\n# Opening\n")
    assert files["Plain.md"].startswith(b"Text\n")
    report = _back(user, folder)
    assert {p["title"] for p in report["pages"]} == {"Named", "Plain"}
    assert _lines(user, report, "Named") == [(0, "# Opening"), (1, "Text")]


def test_a_folded_h1_opening_a_page_keeps_the_title_too(user):
    """A folded heading is a toggle until it is lowered, and goes out as the
    heading all the same, so it counts as the H1 that opens the file. An H2
    is no title and asks for no line."""
    folder = make_folder(user, "NR folded h1")
    _put(user, _page(user, "Folded", folders=[folder]),
         [_note("# Opening", _note("inside"), collapsed=True), _note("Text")])
    _put(user, _page(user, "Lower", folders=[folder]),
         [_note("## Opening", _note("inside"), collapsed=True), _note("Text")])
    files = _files(_export(user, folder))
    assert files["Folded.md"].startswith(b"# Folded\n\n# Opening\n\ninside\n")
    assert files["Lower.md"].startswith(b"## Opening\n")
    assert {p["title"] for p in _back(user, folder)["pages"]} == {"Folded", "Lower"}


def test_a_heading_holds_what_a_gamma_note_nested_under_it_again(user):
    """Notion's headings are flat, and a Plain zip's are not: what the writer
    put after a heading comes back under it."""
    folder = make_folder(user, "NR heading")
    _put(user, _page(user, "Heading", folders=[folder]), [_note("## Part", _note("Under it"))])
    assert _lines(user, _back(user, folder), "Heading") == [(0, "## Part"), (1, "Under it")]


def test_a_sub_page_s_directory_is_a_folder_since_no_file_has_an_id(user):
    """``Paper/Child.md`` pairs only with files that carry an id: the tree that
    needs Notion to have given the pages ids comes back as a folder."""
    folder = make_folder(user, "NR sub")
    paper = _page(user, "Paper", folders=[folder])
    child = _page(user, "Child", parent_page=paper)
    _put(user, paper, [_note(f"[[{child}]]")])
    _put(user, child, [_note("Inside")])
    report = _back(user, folder)
    pages = {p["title"]: p for p in report["pages"]}
    assert pages["Child"]["parent"] == "" and pages["Paper"]["parent"] == ""
    assert pages["Child"]["folders"][0][-1] == "Paper"
    assert _lines(user, report, "Paper") == [(0, f"[[{pages['Child']['id']}]]")]


def test_a_database_comes_back_a_page_with_a_table_its_rows_in_its_folder(user):
    folder = make_folder(user, "NR table")
    cases = _page(user, "Cases", folders=[folder])
    row = _page(user, "RT-001", parent_page=cases)
    _put(user, row, [_note("Status: open"), _note("body")])
    _put(user, cases, [_note(f"| Name | Status |\n|---|---|\n| [[{row}]] | open |")])
    report = _back(user, folder)
    pages = {p["title"]: p for p in report["pages"]}
    assert pages["RT-001"]["folders"][0][-1] == "Cases"
    assert f"[[{pages['RT-001']['id']}]]" in _lines(user, report, "Cases")[0][1]
    assert _lines(user, report, "RT-001") == [(0, "Status: open"), (0, "body")]


def test_a_title_the_stem_could_not_hold_comes_back(user):
    folder = make_folder(user, "NR titles")
    _put(user, _page(user, "What: a study?", folders=[folder]), [_note("body")])
    assert [p["title"] for p in _back(user, folder)["pages"]] == ["What: a study?"]


def test_code_in_a_list_in_a_quote_gains_no_indent_over_cycles(user):
    folder = make_folder(user, "NR quote code")
    _put(user, _page(user, "Quoted code", folders=[folder]), [_note("> - item\n>     ```py\n>     code\n>     ```")])
    first = _files(_export(user, folder))["Quoted code.md"]
    again = make_folder(user, "NR quote code back")
    r = user.post("/api/import/markdown-zip", files={"file": ("export.zip", _export(user, folder), "application/zip")},
                  data={"folder": again})
    assert r.status_code == 200, r.text
    assert _files(_export(user, again))["Quoted code.md"] == first
