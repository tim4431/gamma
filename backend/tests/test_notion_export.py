"""The Notion export (gamma/notion_export.py, _NotionBuilder in
routers/export.py): the zip's layout, the title rule, what stays out and is
warned about, and that a page goes through ``lower_for_file``. Nothing here has
met Notion's importer: these tests say what the zip holds, not how it reads."""

import base64
import io
import re
import zipfile
from urllib.parse import unquote

import pytest

from conftest import doc_props, login, make_folder, make_label, make_user
from gamma import notion_export
from gamma.routers import export as export_router
from gamma.notion_export import NotionContext, PageLinks, render_notion_page
from test_ink import _bytes, _ink
from test_pdf_export import _blank_pdf, _position

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@pytest.fixture(scope="module")
def user():
    make_user("nx_user", "nx-user-pw")
    return login("nx_user", "nx-user-pw")


def _page(c, title, properties=None, **body):
    r = c.post("/api/pages", json={"title": title, **body})
    assert r.status_code == 200, r.text
    page = r.json()["id"]
    if properties:
        r = c.put(f"/api/blocks/{page}", json={"properties": properties})
        assert r.status_code == 200, r.text
    return page


def _note(text, **properties):
    return {"content": text, "properties": properties, "children": []}


def _put(c, page_id, nodes):
    def numbered(items, at):
        return [{"id": f"{at}-{n}", **item, "children": numbered(item["children"], f"{at}-{n}")}
                for n, item in enumerate(items)]
    r = c.put(f"/api/blocks/{page_id}/children", json={"blocks": numbered(nodes, page_id)})
    assert r.status_code == 200, r.text


def _image(c) -> str:
    up = c.post("/api/upload-image", files={"file": ("p.png", PNG, "image/png")})
    assert up.status_code == 200, up.text
    return up.json()["url"]


def _export(c, page_id, **params):
    r = c.get(f"/api/pages/{page_id}/export", params={"mode": "notion", **params})
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def _job(c, **body):
    from gamma import jobs
    started = c.post("/api/jobs/export", json={"mode": "notion", **body})
    assert started.status_code == 200, started.text
    job = jobs.wait(started.json()["id"])
    assert job["state"] == "done", job["error"]
    return job


def _text(z, name) -> str:
    return z.read(name).decode("utf-8")


def _one_page(c, title, nodes, **params) -> str:
    """The text of a page holding ``nodes``, exported alone."""
    page = _page(c, title)
    _put(c, page, nodes)
    z = _export(c, page, **params)
    return _text(z, next(n for n in z.namelist() if n.endswith(".md")))


# --- the zip's layout -------------------------------------------------------------

def test_a_page_alone_sits_at_the_top_with_its_images_beside_it(user):
    url = _image(user)
    page = _page(user, "Paper")
    _put(user, page, [_note(f"a figure\n\n![pic|300]({url})"), _note(f"again ![pic]({url})")])
    z = _export(user, page)
    name = url.rsplit("/", 1)[1]
    assert sorted(z.namelist()) == ["Paper.md", f"Paper/{name}"]
    assert z.read(f"Paper/{name}") == PNG
    assert _text(z, "Paper.md").count(f"![pic](Paper/{name})") == 2       # no |300, one file


def test_a_page_with_no_images_is_still_a_zip(user):
    r = user.get(f"/api/pages/{_page(user, 'Bare')}/export", params={"mode": "notion"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert re.search(r"Bare-[^.]*-notion\.zip", unquote(r.headers["content-disposition"]))


def test_a_sub_page_sits_in_its_parent_s_directory_and_a_folder_is_one(user):
    folder, inner = make_folder(user, "NX lab"), make_folder(user, "NX lab/Inner")
    url = _image(user)
    _page(user, "Other", folders=[folder])
    top = _page(user, "Top", folders=[inner])
    child = _page(user, "Child", parent_page=top)
    _put(user, child, [_note(f"![c]({url})"), _note(f"up to [[{top}]]")])
    r = user.get(f"/api/folders/{folder}/export", params={"mode": "notion"})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name = url.rsplit("/", 1)[1]
    assert sorted(z.namelist()) == ["Inner/Top.md", "Inner/Top/Child.md", f"Inner/Top/Child/{name}", "Other.md"]
    assert _text(z, "Inner/Top/Child.md") == f"![c](Child/{name})\n\nup to [Top](../Top.md)\n"
    assert _text(z, "Inner/Top.md") == "[Child](Top/Child.md)\n"


def test_no_id_reaches_a_name_even_from_notion(user):
    page = _page(user, "From Notion", properties={"notion_id": "a" * 32})
    assert _export(user, page).namelist() == ["From Notion.md"]


# --- the title --------------------------------------------------------------------

def test_a_title_the_file_name_keeps_is_not_written_again(user):
    assert _one_page(user, "Plain title", [_note("body")]) == "body\n"


def test_a_title_the_file_name_cannot_keep_opens_the_page(user):
    assert _one_page(user, "What: a *study*?", [_note("body")]) == "# What: a \\*study\\*?\n\nbody\n"


def test_a_name_made_unique_keeps_the_title_in_a_heading(user):
    """The design's rule looks only at ``vault_name``, which would leave the
    second page called "Twin 2" in Notion."""
    folder = make_folder(user, "NX twins")
    for _ in range(2):
        _page(user, "Twin", folders=[folder])
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/folders/{folder}/export", params={"mode": "notion"}).content))
    assert _text(z, "Twin.md") == "\n" and _text(z, "Twin 2.md") == "# Twin\n"


def test_the_zip_import_takes_a_title_from_the_stem_or_the_heading(user):
    """What the title rule rests on, read back by the import as a Plain zip."""
    folder = make_folder(user, "NX titles")
    for title in ("Bare stem", "What: a study?"):
        _put(user, _page(user, title, folders=[folder]), [_note("body")])
    data = user.get(f"/api/folders/{folder}/export", params={"mode": "notion"}).content
    r = user.post("/api/import/markdown-zip", files={"file": ("export.zip", data, "application/zip")},
                  data={"folder": make_folder(user, "NX titles back")})
    assert r.status_code == 200, r.text
    assert sorted(p["title"] for p in r.json()["pages"]) == ["Bare stem", "What: a study?"]


def _read_back(c, folder) -> dict:
    """The folder's export through the zip import: each page by its title."""
    data = c.get(f"/api/folders/{folder}/export", params={"mode": "notion"}).content
    r = c.post("/api/import/markdown-zip", files={"file": ("export.zip", data, "application/zip")},
               data={"folder": make_folder(c, "NX back")})
    assert r.status_code == 200, r.text
    return {p["title"]: p["id"] for p in r.json()["pages"]}


def test_a_title_with_a_markdown_character_reads_back_as_itself(user):
    titles = ["Foo [bar]: baz", "a*b: c", "T<S>: a", "1. Intro: x", "Why? _no_", "C#: #", "$5: `x`", "A & B: c",
              "back\\slash: d"]
    folder = make_folder(user, "NX marks")
    for title in titles:
        _page(user, title, folders=[folder])
    assert sorted(_read_back(user, folder)) == sorted(titles)


def test_a_divider_opening_a_page_is_no_front_matter(user):
    """Gamma's other Markdown opens with real front matter, so only this file
    can meet it: the import would take what is between two "---" lines for it."""
    folder = make_folder(user, "NX rule")
    page = _page(user, "Rule first", folders=[folder])
    _put(user, page, [_note("---"), _note("kept: words"), _note("---"), _note("rest")])
    assert _text(_export(user, page), "Rule first.md") == "***\n\nkept: words\n\n---\n\nrest\n"
    back = _read_back(user, folder)["Rule first"]
    held = [b["content"] for b in user.get(f"/api/blocks/{back}/subtree").json()["block"]["children"]]
    assert "kept: words" in held and "rest" in held


# --- what a page says of itself ---------------------------------------------------

def test_the_bibtex_fence_comes_first_then_the_record_with_no_front_matter(user):
    label = make_label(user, "NX reading")
    page = _page(user, "A record", properties={"labels": [label], "meta": {
        "doi": "10.1/x", "authors": ["Ada", "Bob"], "year": 2024, "title": "A record"}})
    _put(user, page, [_note("my notes")])
    text = _text(_export(user, page), "A record.md")
    fence, _, rest = text.partition("```\n\n")
    assert fence.startswith("```bibtex\n@") and "---" not in text.split("my notes")[0]
    assert rest == "DOI: 10.1/x\n\nAuthors: Ada, Bob\n\nYear: 2024\n\nTags: NX reading\n\nmy notes\n"


# --- mentions ---------------------------------------------------------------------

def test_a_mention_links_the_file_a_block_mention_its_page_and_outside_is_its_label(user):
    folder = make_folder(user, "NX links")
    target = _page(user, "Target paper", folders=[folder])
    _put(user, target, [_note("a finding\nsecond line")])
    outside = _page(user, "Elsewhere")
    source = _page(user, "Source", folders=[folder])
    _put(user, source, [_note(f"page [[{target}]], block [[{target}-0]], out [[{outside}]], own [[{source}]], "
                              "lost [[nope404]]")])
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/folders/{folder}/export", params={"mode": "notion"}).content))
    assert _text(z, "Source.md") == ("page [Target paper](Target%20paper.md), block [a finding](Target%20paper.md), "
                                     "out Elsewhere, own Source, lost \\[\\[nope404]]\n")


def test_a_mention_of_a_block_with_no_words_is_its_page_s_title(user):
    folder = make_folder(user, "NX wordless")
    target = _page(user, "Target of code", folders=[folder])
    _put(user, target, [_note("```py\nx = 1\n```"), _note(f"![a|300]({_image(user)})"), _note("- item")])
    source = _page(user, "Mentions", folders=[folder])
    _put(user, source, [_note(f"[[{target}-0]], [[{target}-1]] and [[{target}-2]]")])
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/folders/{folder}/export", params={"mode": "notion"}).content))
    link = "(Target%20of%20code.md)"
    assert _text(z, "Mentions.md") == (f"[Target of code]{link}, [Target of code]{link} and [item]{link}\n")


def test_a_file_name_is_percent_encoded_in_a_link(user):
    folder = make_folder(user, "NX names")
    target = _page(user, "50% (déjà) & co", folders=[folder])
    source = _page(user, "Source", folders=[folder])
    _put(user, source, [_note(f"see [[{target}]]")])
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/folders/{folder}/export", params={"mode": "notion"}).content))
    assert "50% (déjà) & co.md" in z.namelist()
    assert _text(z, "Source.md") == "see [50% (déjà) & co](50%25%20%28d%C3%A9j%C3%A0%29%20%26%20co.md)\n"


def test_a_child_alone_on_a_line_stays_there_and_the_others_are_appended(user):
    top = _page(user, "Parent")
    linked = _page(user, "Linked", parent_page=top)
    _page(user, "Loose", parent_page=top)
    _put(user, top, [_note(f"[[{linked}]]"), _note("after it")])
    z = _export(user, top, sub_pages=1)
    assert _text(z, "Parent.md") == "[Linked](Parent/Linked.md)\n\nafter it\n\n[Loose](Parent/Loose.md)\n"


def test_a_child_named_inside_a_line_is_appended_too(user):
    top = _page(user, "Mentioning parent")
    child = _page(user, "Kid", parent_page=top)
    _put(user, top, [_note(f"see [[{child}]] there")])
    text = _text(_export(user, top, sub_pages=1), "Mentioning parent.md")
    assert text == ("see [Kid](Mentioning%20parent/Kid.md) there\n\n"
                    "[Kid](Mentioning%20parent/Kid.md)\n")


def test_a_database_is_its_table_and_rows_are_sub_pages(user):
    cases = _page(user, "Cases")
    row = _page(user, "RT-001", parent_page=cases)
    _put(user, row, [_note("Status: open"), _note("body")])
    _put(user, cases, [_note(f"| Name | Status |\n|---|---|\n| [[{row}]] | open |")])
    z = _export(user, cases, sub_pages=1)
    assert sorted(z.namelist()) == ["Cases.md", "Cases/RT-001.md"]
    assert "| [RT-001](Cases/RT-001.md) | open |" in _text(z, "Cases.md")
    assert _text(z, "Cases/RT-001.md") == "Status: open\n\nbody\n"


# --- what stays out ---------------------------------------------------------------

def test_a_missing_image_is_its_name_as_text_with_a_warning(user):
    page = _page(user, "Lost picture")
    _put(user, page, [_note("![gone](/api/uploads/deadbeef.png)")])
    job = _job(user, page_id=page)
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/jobs/{job['id']}/download").content))
    assert z.namelist() == ["Lost picture.md"]
    assert _text(z, "Lost picture.md") == "gone\n"
    assert job["result"]["warnings"] == [{"page_id": page, "title": "Lost picture", "reason": "gone: image left out"}]


def test_an_image_held_in_its_address_leaves_no_piece_of_it(user):
    page = _page(user, "Data picture")
    _put(user, page, [_note("![](data:image/png;base64,QUJD/SECRETTAIL)"),
                      _note("| ![](data:image/png;base64,QUJD) |\n| --- |")])
    job = _job(user, page_id=page)
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/jobs/{job['id']}/download").content))
    text = _text(z, "Data picture.md")
    assert "SECRETTAIL" not in text and "base64" not in text
    assert [w["reason"] for w in job["result"]["warnings"]] == ["image: image left out"]


@pytest.mark.parametrize("line", ["[![a](/api/uploads/x.png)](https://x.y)", "[![a](https://x.org/b.png)](https://x.y)"])
def test_an_image_inside_a_link_is_its_words_with_a_warning(user, line):
    page = _page(user, "Linked picture")
    _put(user, page, [_note(line)])
    job = _job(user, page_id=page)
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/jobs/{job['id']}/download").content))
    assert _text(z, "Linked picture.md") == "[a](https://x.y)\n"
    assert job["result"]["warnings"] == [{"page_id": page, "title": "Linked picture", "reason": "a: image left out"}]


def test_a_web_image_stays_at_its_address(user):
    assert _one_page(user, "Web picture", [_note("![w](https://x.org/a.png)")]) == "![w](https://x.org/a.png)\n"


def _pdf_page(c, title, **properties):
    up = c.post("/api/uploads", files={"file": (f"{title}.pdf", _blank_pdf(pages=1) + title.encode(), "application/pdf")})
    assert up.status_code == 200, up.text
    return _page(c, title, properties={**doc_props(up.json()["doc_id"], name=f"{title}.pdf"), **properties})


@pytest.mark.parametrize("pdf", [0, 1])
def test_a_page_s_pdf_stays_out_whatever_the_switch_says(user, pdf):
    page = _pdf_page(user, f"Held PDF {pdf}")
    z = _export(user, page, pdf=pdf)
    assert z.namelist() == [f"Held PDF {pdf}.md"]
    assert _text(z, f"Held PDF {pdf}.md") == f"Held PDF {pdf}.pdf\n"


def test_a_pdf_from_the_web_is_a_link_with_a_warning(user):
    page = _page(user, "Web PDF", properties=doc_props(url="https://x.org/paper.pdf"))
    job = _job(user, page_id=page)
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/jobs/{job['id']}/download").content))
    assert _text(z, "Web PDF.md") == "[paper.pdf](https://x.org/paper.pdf)\n"
    assert [w["reason"] for w in job["result"]["warnings"]] == ["paper.pdf: pdf left out"]


def test_an_uploaded_file_in_a_note_is_its_name(user):
    up = user.post("/api/uploads", files={"file": ("a.pdf", _blank_pdf(pages=1) + b"note", "application/pdf")})
    url = up.json()["source_url"]
    page = _page(user, "Linked file")
    _put(user, page, [_note(f"[the paper]({url})"), _note(f"see [this]({url}) now")])
    job = _job(user, page_id=page)
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/jobs/{job['id']}/download").content))
    assert z.namelist() == ["Linked file.md"] and _text(z, "Linked file.md") == "the paper\n\nsee this now\n"
    assert sorted(w["reason"] for w in job["result"]["warnings"]) == ["the paper: pdf left out", "this: file left out"]


def test_a_recording_is_its_length_and_its_title(user):
    recording = {"dur": 65_000, "segments": [{"url": "/api/uploads/ab12.m4a", "dur": 65_000}]}
    page = _page(user, "Recorded")
    _put(user, page, [{**_note("The talk", recording=recording), "children": [_note("what was said")]}])
    job = _job(user, page_id=page)
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/jobs/{job['id']}/download").content))
    assert z.namelist() == ["Recorded.md"]
    assert _text(z, "Recorded.md") == "**Recording · 1:05**\n\naudio\n\n- The talk\n    - what was said\n"
    assert [w["reason"] for w in job["result"]["warnings"]] == ["audio: audio left out"]


def test_handwriting_is_rendered_beside_its_page(user):
    up = user.post("/api/upload-file", files={"file": ("x.ink", _bytes(_ink()), "application/octet-stream")})
    assert up.status_code == 200, up.text
    url = up.json()["url"]
    page = _page(user, "Inked")
    _put(user, page, [_note("my caption", ink_url=url), _note("gone", ink_url="/api/uploads/deadbeef.ink")])
    job = _job(user, page_id=page)
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/jobs/{job['id']}/download").content))
    svg = url.rsplit("/", 1)[1].replace(".ink", ".svg")
    assert sorted(z.namelist()) == ["Inked.md", f"Inked/{svg}"]
    assert z.read(f"Inked/{svg}").startswith(b"<svg")
    assert _text(z, "Inked.md") == f"![Handwriting](Inked/{svg})\n\nmy caption\n\nHandwriting\n\ngone\n"
    assert [w["reason"] for w in job["result"]["warnings"]] == ["Handwriting: image left out"]


def test_warnings_list_the_limits(user, monkeypatch):
    url = _image(user)
    page = _page(user, "Big one")
    _put(user, page, [_note(f"![pic]({url})")])
    monkeypatch.setattr(notion_export, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(notion_export, "MAX_FILES", 1)
    job = _job(user, page_id=page)
    reasons = [w["reason"] for w in job["result"]["warnings"]]
    assert reasons == [f"{url.rsplit('/', 1)[1]}: over 5 MB, too big for Notion's Free plan",
                       "Big one.md: over 5 MB, too big for Notion's Free plan",
                       "over 1 files, which Notion's importer is likely to fail on"]


def test_the_notes_file_itself_can_be_over_the_limit(user, monkeypatch):
    page = _page(user, "Long one")
    _put(user, page, [_note("words " * 10)])
    monkeypatch.setattr(notion_export, "MAX_FILE_BYTES", 10)
    assert [w["reason"] for w in _job(user, page_id=page)["result"]["warnings"]] == [
        "Long one.md: over 5 MB, too big for Notion's Free plan"]


def test_a_text_too_long_for_its_marks_stays_as_written_with_a_warning(user):
    page = _page(user, "Long note")
    _put(user, page, [_note("==a " * 50000)])                # ops.MAX_CONTENT
    job = _job(user, page_id=page)
    text = _text(zipfile.ZipFile(io.BytesIO(user.get(f"/api/jobs/{job['id']}/download").content)), "Long note.md")
    assert text.count("a") == 50000 and "==" in text.replace("\\", "")
    assert job["result"]["warnings"] == [{"page_id": page, "title": "Long note", "reason": (
        "Long text: over 20,000 characters, so its marks stay as typed")}]


def _chain(c, page_id, depth):
    """A chain of bullets, each block's id short whatever its depth."""
    node = {**_note("- bottom"), "id": f"{page_id}-bottom"}
    for n in range(depth):
        node = {**_note("- level"), "id": f"{page_id}-{n}", "children": [node]}
    r = c.put(f"/api/blocks/{page_id}/children", json={"blocks": [node]})
    assert r.status_code == 200, r.text


def test_a_page_nested_too_deep_is_left_out_with_a_warning(user):
    folder = make_folder(user, "NX deep")
    deep = _page(user, "Too deep", folders=[folder])
    _chain(user, deep, 260)
    _put(user, _page(user, "Shallow", folders=[folder]), [_note("words")])
    job = _job(user, folder=folder)
    z = zipfile.ZipFile(io.BytesIO(user.get(f"/api/jobs/{job['id']}/download").content))
    assert z.namelist() == ["Shallow.md"]
    assert job["result"]["warnings"] == [{"page_id": deep, "title": "Too deep", "reason": (
        "Too deep.md: nested too deep to write, left out")}]


def test_a_page_alone_nested_too_deep_fails_with_the_reason(user, anon):
    from gamma import jobs
    page = _page(user, "Alone too deep")
    _chain(user, page, 260)
    started = user.post("/api/jobs/export", json={"mode": "notion", "page_id": page})
    job = jobs.wait(started.json()["id"])
    assert job["state"] == "failed" and job["error"] == "nested too deep to write as a Notion page"
    r = anon.get(f"/api/pages/{page}/export", params={"mode": "notion", "share": _share(user, page)})
    assert r.status_code == 400 and r.json()["detail"] == "nested too deep to write as a Notion page"


def test_a_clean_export_warns_of_nothing(user):
    page = _page(user, "Clean")
    _put(user, page, [_note("words")])
    assert _job(user, page_id=page)["result"] == {"pages": 1, "skipped": [], "warnings": []}


# --- the blocks -------------------------------------------------------------------

def test_a_page_goes_through_lower_for_file(user):
    assert _one_page(user, "Lowered", [
        _note("> [!note] 💡\n> a callout"),
        _note("#### deep"),
        {**_note("folded", collapsed=True), "children": [_note("inside")]},
        {**_note("parent"), "children": [_note("child")]},
        _note("==marked== <span style=\"color:#e5484d\">red</span> <u>under</u> @2026-01-05"),
        _note("one\ntwo"),
    ]) == ("> 💡 a callout\n\n### deep\n\n- folded\n    - inside\n\n- parent\n    - child\n\n"
           "**marked** red <u>under</u> @2026-01-05\n\none  \ntwo\n")


def test_a_list_stays_a_list(user):
    assert _one_page(user, "Lists", [_note("- a"), _note("3. b"), _note("- [x] c")]) == "- a\n\n3. b\n\n- [x] c\n"


def test_a_folded_heading_is_a_heading_with_its_children_after_it(user):
    assert _one_page(user, "Heading toggle", [
        {**_note("## folded", collapsed=True), "children": [_note("under")]},
    ]) == "## folded\n\nunder\n"


def _highlight(quote="the quote", text="", page=3):
    position = {**_position(), "pageNumber": page}
    return _note(text, quote=quote, color="rgba(255, 226, 143, 0.65)", pdf_position=position)


def test_a_highlight_is_a_quote_with_its_page_then_your_note(user):
    assert _one_page(user, "Quoted", [_highlight(text="my take")]) == "> the quote  \n> p. 3\n\nmy take\n"


def test_a_highlight_with_no_quote_names_no_page(user):
    """``Item.page_no`` holds a page for it; only a quote shows one."""
    assert _one_page(user, "Unquoted", [_highlight(quote="", text="my take")]) == "my take\n"


def test_the_switches_take_out_what_they_take_out_of_the_other_exports(user):
    nodes = [_highlight(text="my take"), _note("a note")]
    assert _one_page(user, "No highlights", nodes, highlights=0) == "my take\n\na note\n"
    assert _one_page(user, "No notes", nodes, notes=0) == "> the quote  \n> p. 3\n"


def test_a_recording_goes_with_the_highlights_and_its_title_with_the_notes(user):
    recording = {"dur": 65_000, "segments": [{"url": "/api/uploads/ab12.m4a", "dur": 65_000}]}
    nodes = [_note("The talk", recording=recording)]
    assert _one_page(user, "Heard", nodes, notes=0) == "**Recording · 1:05**\n\naudio\n"
    assert _one_page(user, "Unheard", nodes, highlights=0) == "The talk\n"


@pytest.mark.parametrize("highlights, notes, pasted, handwriting", [
    (1, 1, True, True), (0, 1, True, False), (1, 0, False, True), (0, 0, False, False)])
def test_images_go_with_the_notes_and_handwriting_with_the_highlights(user, highlights, notes, pasted, handwriting):
    """What the export card says of the zip (transferFormats.js notionFiles)."""
    up = user.post("/api/upload-file", files={"file": ("x.ink", _bytes(_ink()), "application/octet-stream")})
    assert up.status_code == 200, up.text
    page = _page(user, "Switched")
    _put(user, page, [_note(f"![p]({_image(user)})"), _note("my caption", ink_url=up.json()["url"]),
                      _highlight(text="my take")])
    names = _export(user, page, highlights=highlights, notes=notes, pdf=1).namelist()
    assert any(name.endswith(".png") for name in names) is pasted
    assert any(name.endswith(".svg") for name in names) is handwriting
    assert len(names) == 1 + pasted + handwriting


def test_a_mention_of_a_page_in_a_fence_is_left_alone(user):
    assert _one_page(user, "Fenced", [_note("```\n[[abc]]\n```")]) == "```\n[[abc]]\n```\n"


# --- the render, on its own -------------------------------------------------------

def _context(files) -> NotionContext:
    ctx = NotionContext(lambda block_id: None, lambda name, stem: None)
    ctx.files = files
    return ctx


def _render(page, ctx, **kw) -> str:
    return render_notion_page({"properties": {}, "children": [], **page}, ctx, **kw)


def test_a_link_is_relative_to_the_file_it_is_in():
    ctx = _context({"a": "x/A.md", "b": "x/A/B.md", "c": "y/C d.md"})
    links = PageLinks(ctx, "b", "B")
    assert (links.page("a"), links.page("c"), links.page("gone")) == ("../A.md", "../../y/C%20d.md", None)
    assert PageLinks(ctx, "a", "A").page("b") == "A/B.md"


def test_only_an_image_is_an_asset():
    sizes = {"/api/uploads/ab.png": 3}
    ctx = NotionContext(None, lambda name, stem: sizes.get(f"/api/uploads/{name}"))
    ctx.files = {"a": "A b.md"}
    links = PageLinks(ctx, "a", "A b")
    assert links.asset("/api/uploads/ab.png", "image") == "A%20b/ab.png"
    assert links.asset("/api/uploads/cd.png", "image") is None
    assert links.asset("https://x.org/a.png", "image") == "https://x.org/a.png"
    for kind in ("file", "pdf", "audio", "video"):
        assert links.asset("/api/uploads/ab.png", kind) is None
        assert links.asset("https://x.org/a.pdf", kind) is None


def test_the_same_loss_on_a_page_warns_once():
    ctx = _context({"a": "A.md"})
    links = PageLinks(ctx, "a", "A")
    links.warn("x", "gone")
    links.warn("x", "gone")
    assert ctx.warnings == [{"page_id": "a", "title": "A", "reason": "x: gone"}]


def test_an_empty_title_is_the_stem_of_untitled():
    assert _render({"id": "a", "content": "  "}, _context({"a": "Untitled.md"})) == "\n"


# --- through a share --------------------------------------------------------------

def _share(c, page_id) -> str:
    r = c.post(f"/api/share/{page_id}", json={"audience": "anyone", "role": "view"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _anon_zip(anon, page_id, token):
    r = anon.get(f"/api/pages/{page_id}/export", params={"mode": "notion", "share": token})
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def _private_page(c):
    secret = _page(c, "Secret page")
    _put(c, secret, [_note("TOPSECRET words"), _note(f"![private]({_image(c)})")])
    return secret


def test_a_share_s_zip_holds_nothing_of_a_page_outside_it(user, anon):
    secret = _private_page(user)
    shared = _page(user, "Public page")
    _put(user, shared, [_note(f"![[{secret}-0]]"), _note(f"mention [[{secret}-0]]"), _note(f"![[{secret}-1]]"),
                        _note(f"[[{secret}]]"), _note("mine")])
    z = _anon_zip(anon, shared, _share(user, shared))
    assert z.namelist() == ["Public page.md"]
    text = _text(z, "Public page.md")
    assert "TOPSECRET" not in text and "Secret page" not in text and "private" not in text
    assert text.endswith("mine\n") and f"[[{secret}-0]]" in text.replace("\\", "")  # an id's _ may be escaped


def test_a_share_s_zip_goes_by_what_the_share_can_read_not_what_a_ref_says(user, anon, monkeypatch):
    monkeypatch.setattr(export_router, "_ref_resolver", lambda conn, reach=None: export_router.block_ref_resolver(conn))
    secret = _private_page(user)
    shared = _page(user, "Public picture")
    _put(user, shared, [_note(f"![[{secret}-1]]")])
    z = _anon_zip(anon, shared, _share(user, shared))
    assert z.namelist() == ["Public picture.md"]
    assert "](Public" not in _text(z, "Public picture.md")


def test_a_share_s_zip_keeps_what_is_inside_it(user, anon):
    up = user.post("/api/upload-file", files={"file": ("x.ink", _bytes(_ink()), "application/octet-stream")})
    assert up.status_code == 200, up.text
    picture = _image(user)
    shared = _page(user, "Open page")
    _put(user, shared, [_note(f"![seen]({picture})"), _note("drawn", ink_url=up.json()["url"]), _note("words")])
    _put(user, _page(user, "Open child", parent_page=shared), [_note(f"![[{shared}-2]]")])
    z = _anon_zip(anon, shared, _share(user, shared))
    assert sorted(n.rsplit(".", 1)[1] for n in z.namelist()) == ["md", "png", "svg"]
    assert f"Open page/{picture.rsplit('/', 1)[1]}" in z.namelist()


def test_a_folder_share_s_zip_reaches_the_folder_s_pages_only(user, anon):
    secret = _private_page(user)
    folder = make_folder(user, "NX shared")
    other = _page(user, "Filed page", folders=[folder])
    _put(user, other, [_note("filed words")])
    filed = _page(user, "Filed mentions", folders=[folder])
    _put(user, filed, [_note(f"![[{other}-0]]"), _note(f"![[{secret}-0]]")])
    r = user.post(f"/api/share/folder/{folder}", json={"audience": "anyone", "role": "view"})
    assert r.status_code == 200, r.text
    r = anon.get(f"/api/folders/{folder}/export", params={"mode": "notion", "share": r.json()["token"]})
    assert r.status_code == 200, r.text
    text = _text(zipfile.ZipFile(io.BytesIO(r.content)), "Filed mentions.md")
    assert "filed words" in text and "TOPSECRET" not in text
