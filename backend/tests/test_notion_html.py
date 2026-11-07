"""One page of Notion's HTML export read into the block model
(gamma/notion_html.py): every element of every era, the inline rules, and the
fixtures of tests/notion_fixtures through ``to_gamma``. What a whole zip gives
is the importer's to test."""

import time

import pytest

from gamma.notion_blocks import Block
from gamma.notion_gamma import to_gamma
from gamma.notion_html import _Parser, is_notion_page, opens_notion_page, plain_text, read_page
from gamma.zotero_notes import node_tag, node_text
from notion_fixtures import PAGE_ID, export_zip, files, html_page, page

UNDASHED = PAGE_ID.replace("-", "")
BOLD, ITALIC = {"bold": True}, {"italic": True}


def text(s, **marks):
    return ("text", s, marks)


def body(markup, header=""):
    return read_page(page(markup, header)).blocks


def one(markup):
    blocks = body(markup)
    assert len(blocks) == 1, blocks
    return blocks[0]


def rich(markup):
    return one(f"<p>{markup}</p>").rich


def contents(nodes):
    """What ``to_gamma`` wrote, as (content, children) with ``collapsed`` shown."""
    return [(node["content"], *(["collapsed"] if node["properties"].get("collapsed") else []),
             *([contents(node["children"])] if node["children"] else [])) for node in nodes]


def gamma(html):
    return contents(to_gamma(read_page(html)))


# --- is it a page ---------------------------------------------------------------

def test_a_page_of_the_export_is_one_whatever_its_article_says_first():
    assert is_notion_page(page().encode())
    assert is_notion_page(b'<article id="x" class="page sans"><div class="page-body">')


def test_the_stylesheet_before_the_article_is_inside_the_first_64_kb():
    head = (b"<style>" + b"x" * 17_696 + b"</style>" + page().encode())[:64 * 1024]
    assert is_notion_page(head)


def test_a_page_opens_as_one_before_its_body_shows():
    assert opens_notion_page(b'<article id="x" class="page sans"><header>')
    assert not opens_notion_page(b'<article class="post"><div class="page-body">')


@pytest.mark.parametrize("head", [
    b"", b"<html><body><p>Hello</p></body></html>",
    b'<article class="page sans"><div class="body">',     # no page body
    b'<div class="page-body"><article class="post">',     # no page
    b'<html><body><div class="index"><a href="Page.html">Page</a></div></body></html>',
])
def test_other_html_is_not(head):
    assert not is_notion_page(head)


# --- the page ---------------------------------------------------------------------

def test_a_page_says_its_title_and_id():
    read = read_page(page())
    assert (read.title, read.notion_id, read.blocks, read.warnings) == ("A page", UNDASHED, [], [])


def test_a_page_that_does_not_say_leaves_them_to_the_file_name():
    read = read_page('<article class="page"><div class="page-body"><p>x</p></div></article>')
    assert (read.title, read.notion_id) == ("", None)
    assert read.blocks == [Block("paragraph", [text("x")])]


def test_the_head_and_scripts_are_no_text():
    read = read_page("<html><head><title>The head</title><style>p { color: red }</style></head>"
                     '<body><article class="page"><div class="page-body"><script>let a;</script>'
                     '<link rel="stylesheet" href="x.css"/><p>Text</p></div></article></body></html>')
    assert read.blocks == [Block("paragraph", [text("Text")])]


def test_an_empty_description_goes_and_one_with_text_opens_the_page():
    assert body("<p>x</p>", '<p class="page-description" dir="auto"></p>') == [Block("paragraph", [text("x")])]
    assert body("<p>x</p>", '<p class="page-description">About</p>') == [Block("paragraph", [text("About")]),
                                                                       Block("paragraph", [text("x")])]


def test_line_ends_of_a_file_saved_on_windows_are_line_ends():
    assert rich("a\r\nb") == [text("a"), ("br",), text("b")]


# --- blocks -----------------------------------------------------------------------

@pytest.mark.parametrize("wrapper", [
    '<div style="display:contents" dir="auto">{}</div>',
    '<div class="column-list"><div class="column" style="width:50%">{}</div></div>',
])
def test_what_only_wraps_is_transparent(wrapper):
    assert one(wrapper.format("<p>x</p>")) == Block("paragraph", [text("x")])


def test_columns_flatten_in_reading_order():
    blocks = body('<div class="column-list"><div class="column"><p>a</p></div><div class="column"><p>b</p></div></div>')
    assert [plain_text(b.rich) for b in blocks] == ["a", "b"]


def test_a_paragraph_holds_its_indented_children():
    block = one('<p>a<div class="indented"><p>b<div class="indented"><p>c</p></div></p></div></p>')
    assert block == Block("paragraph", [text("a")], [Block("paragraph", [text("b")], [Block("paragraph", [text("c")])])])


def test_an_empty_paragraph_stays_for_to_gamma_to_drop():
    assert body("<p></p><p>\n</p><p>x</p>")[:2] == [Block("paragraph"), Block("paragraph")]
    assert gamma(page("<p></p><p>\n</p><p>x</p>")) == [("x",)]


@pytest.mark.parametrize("markup, level", [("<h1>x</h1>", 1), ("<h2>x</h2>", 2), ("<h3>x</h3>", 3)])
def test_headings(markup, level):
    assert one(markup) == Block("heading", [text("x")], attrs={"level": level})


def test_an_empty_heading_is_dropped():
    assert body("<h2></h2>") == []


def test_the_page_title_is_no_heading_of_the_body():
    assert read_page(page("<h1>Body</h1>")).blocks == [Block("heading", [text("Body")], attrs={"level": 1})]


@pytest.mark.parametrize("markup, heading", [
    ('<details open=""><summary><h2 style="display:inline-block">x</h2></summary>'
     '<div class="indented"><p>y</p></div></details>', 2),
    ('<details><summary><h1>x</h1></summary><p>y</p></details>', 1),
    ('<details open=""><summary style="font-weight:600;font-size:1.875em;line-height:1.3;margin:0">x</summary>'
     '<div class="indented"><p>y</p></div></details>', 1),
    ('<details open=""><summary style="font-weight:600;font-size:1.5em;">x</summary><p>y</p></details>', 2),
    ('<details open=""><summary style="font-size:1.25em">x</summary><p>y</p></details>', 3),
])
def test_toggle_headings_of_each_era(markup, heading):
    block = one(markup)
    assert block == Block("toggle", [text("x")], [Block("paragraph", [text("y")])], {"heading": heading})
    assert gamma(page(markup)) == [("#" * heading + " x", "collapsed", [("y",)])]


@pytest.mark.parametrize("markup", [
    '<details class="toggle" open=""><summary>x</summary><div class="indented"><p>y</p></div></details>',
    '<details class="toggle"><summary>x</summary><div class="indented"><p>y</p></div></details>',
    '<ul class="toggle"><li><details open=""><summary>x</summary><p>y</p></details></li></ul>',
    '<ul class="toggle"><li><details open=""><summary>x</summary>'
    '<div style="display:contents" dir="ltr"><p>y</p></div></details></li></ul>',
])
def test_a_toggle_is_folded_whatever_details_open_says(markup):
    assert one(markup) == Block("toggle", [text("x")], [Block("paragraph", [text("y")])])
    assert gamma(page(markup)) == [("x", "collapsed", [("y",)])]


def test_a_toggle_with_nothing_in_it_is_a_plain_block():
    markup = '<details class="toggle" open=""><summary>x</summary><div class="indented"></div></details>'
    assert gamma(page(markup)) == [("x",)]


def test_toggles_nest():
    markup = ('<details class="toggle"><summary>a</summary><div class="indented">'
              '<details class="toggle"><summary>b</summary><div class="indented"><p>c</p></div></details>'
              "</div></details>")
    assert gamma(page(markup)) == [("a", "collapsed", [("b", "collapsed", [("c",)])])]


def test_lists_are_never_merged_and_nest_in_their_item():
    blocks = body('<ul class="bulleted-list"><li style="list-style-type:disc">a'
                  '<ul class="bulleted-list"><li style="list-style-type:circle">b</li></ul></li></ul>'
                  '<ul class="bulleted-list"><li style="list-style-type:disc">c</li></ul>')
    assert blocks == [Block("bullet", [text("a")], [Block("bullet", [text("b")])]), Block("bullet", [text("c")])]


def test_an_item_holds_any_block_in_div_indented():
    block = one('<ul class="bulleted-list"><li>a<div class="indented"><p>b</p><pre class="code"><code>c</code></pre>'
                "</div></li></ul>")
    assert block == Block("bullet", [text("a")], [Block("paragraph", [text("b")]), Block("code", attrs={"text": "c"})])


@pytest.mark.parametrize("start, numbers", [(' start="3"', [3]), ("", [1]), (' start="x"', [1])])
def test_a_number_keeps_the_start_of_its_list(start, numbers):
    block = one(f'<ol type="1" class="numbered-list"{start}><li>a</li></ol>')
    assert block == Block("number", [text("a")], attrs={"start": numbers[0]})


def test_a_list_of_several_numbers_counts_from_its_start():
    assert [b.attrs["start"] for b in body('<ol class="numbered-list" start="4"><li>a</li><li>b</li></ol>')] == [4, 5]


@pytest.mark.parametrize("box, checked", [
    ('<input type="checkbox" class="checkbox checkbox-on" disabled="" checked=""/>', True),
    ('<input type="checkbox" class="checkbox checkbox-off" disabled=""/>', False),
    ('<div class="checkbox checkbox-on"></div>', True),
    ('<div class="checkbox checkbox-off"></div>', False),
    ('<span class="checkbox checkbox-on"></span>', True),
])
def test_to_dos_of_each_era(box, checked):
    block = one(f'<ul class="to-do-list"><li>{box} <span class="to-do-children-unchecked">x</span>'
                '<div class="indented"><p>y</p></div></li></ul>')
    assert block == Block("todo", [text("x")], [Block("paragraph", [text("y")])], {"checked": checked})


@pytest.mark.parametrize("markup, expected", [
    ("<blockquote>a</blockquote>", Block("quote", [text("a")])),
    ('<blockquote class="" dir="auto"><p>a</p></blockquote>', Block("quote", [text("a")])),
    ("<blockquote><p>a</p><p>b</p></blockquote>", Block("quote", [text("a")], [Block("paragraph", [text("b")])])),
    ("<blockquote>a<blockquote>b</blockquote></blockquote>", Block("quote", [text("a")], [Block("quote", [text("b")])])),
])
def test_quotes(markup, expected):
    assert one(markup) == expected


def test_a_quote_goes_to_gamma_as_its_lines_with_no_tail():
    assert gamma(page("<blockquote><p>a</p><p>b</p></blockquote>")) == [("> a\n>\n> b",)]


@pytest.mark.parametrize("attributes, inner, attrs", [
    # 2026: the attributes say it all
    ('class="block-color-red_background callout" data-notion-callout="" data-notion-callout-icon="♻️" '
     'data-notion-callout-background="red_background"', '<span class="icon" data-emoji="♻️"></span>',
     {"icon": "♻️", "color": "red_bg"}),
    ('class="block-color-gray_background callout"', '<span class="icon" data-emoji="🔥"></span>',
     {"icon": "🔥", "color": "gray_bg"}),
    # older: the class and the icon's own text
    ('class="block-color-default_background callout"', '<span class="icon">💡</span>', {"icon": "💡"}),
    ('class="block-color-blue_background callout"', '<span class="icon">💡</span>', {"icon": "💡", "color": "blue_bg"}),
    ('class="block-color-teal_background callout"', '<span class="icon">💡</span>', {"icon": "💡", "color": "green_bg"}),
])
@pytest.mark.parametrize("tag", ["aside", "figure"])
def test_a_callout_says_its_icon_and_colour(tag, attributes, inner, attrs):
    block = one(f'<{tag} {attributes} style="white-space:pre-wrap;display:flex"><div style="font-size:1.5em">{inner}</div>'
                f'<div style="width:100%"><p>x</p></div></{tag}>')
    assert block == Block("callout", [text("x")], attrs=attrs)


def test_a_callout_icon_that_is_a_picture_is_the_markup_the_markdown_export_writes():
    url = "https://www.notion.so/icons/info-alternate_green.svg"
    markup = (f'<figure class="callout"><div style="font-size:1.5em"><img class="icon" src="{url}"/></div>'
              '<div style="width:100%">x</div></figure>')
    assert gamma(page(markup)) == [(f'> [!info] <img src="{url}" alt="{url}" width="40px" />\n> x',)]


def test_a_callout_icon_of_the_zip_is_left_out():
    markup = ('<figure class="callout"><div style="font-size:1.5em"><img class="icon" src="Page/icon.png"/></div>'
              '<div style="width:100%">x</div></figure>')
    assert one(markup).attrs == {}


def test_a_callout_with_no_icon_box_reads_all_it_holds():
    block = one('<figure class="callout"><div style="width:100%">a<p>b</p></div></figure>')
    assert block == Block("callout", [text("a")], [Block("paragraph", [text("b")])])


def test_a_callout_merges_paragraphs_and_lists_and_keeps_the_rest_as_children():
    markup = ('<aside class="callout"><div style="font-size:1.5em"><span class="icon">💡</span></div>'
              '<div style="width:100%"><p>a</p><ul class="bulleted-list"><li>b</li></ul>'
              '<pre class="code"><code>c</code></pre></div></aside>')
    assert gamma(page(markup)) == [("> [!info] 💡\n> a\n>\n> - b", [("```\nc\n```",)])]


@pytest.mark.parametrize("markup, attrs", [
    ('<pre class="code code-wrap" data-notion-code-syntax="python"><code class="language-python">a = 1</code></pre>',
     {"text": "a = 1", "language": "python"}),
    ('<pre class="code"><code class="language-Python">a = 1</code></pre>', {"text": "a = 1", "language": "python"}),
    ('<pre class="code"><code class="language-Plain Text">a</code></pre>', {"text": "a"}),
    ('<pre class="code" data-notion-code-syntax="plain text"><code>a</code></pre>', {"text": "a"}),
    ('<pre class="code"><code>a</code></pre>', {"text": "a"}),
    ('<pre class="code"><code class="language-Python">  <span class="token">a</span>\n\n  b\n</code></pre>',
     {"text": "  a\n\n  b\n", "language": "python"}),
])
def test_code_is_verbatim(markup, attrs):
    assert one(markup) == Block("code", attrs=attrs)


def test_a_script_holding_prism_beside_code_is_no_block():
    assert one('<script src="prism.js"></script><link rel="stylesheet" href="p.css"/>'
               '<pre class="code"><code>a</code></pre>').type == "code"


@pytest.mark.parametrize("markup", [
    '<figure class="equation" data-notion-equation="E=mc^2" dir="auto"><style>@import url("k.css")</style>'
    '<div class="equation-container"><span class="katex-display"><span class="katex">'
    '<span class="katex-html" aria-hidden="true">E=mc2</span></span></span></div></figure>',
    '<figure class="equation"><div class="equation-container"><span class="katex-display"><span class="katex">'
    '<span class="katex-mathml"><math><semantics><mrow><mi>E</mi></mrow>'
    '<annotation encoding="application/x-tex">E=mc^2</annotation></semantics></math></span>'
    '<span class="katex-html" aria-hidden="true">E=mc2</span></span></span></div></figure>',
])
def test_equations_of_each_era(markup):
    assert one(markup) == Block("equation", attrs={"text": "E=mc^2"})
    assert gamma(page(markup)) == [("$$\nE=mc^2\n$$",)]


def test_a_divider():
    assert one('<hr dir="auto"/>') == Block("divider")


def test_a_table_with_a_header_row():
    block = one('<table class="simple-table"><thead class="simple-table-header"><tr><th>A</th><th>B</th></tr></thead>'
                "<tbody><tr><td>1</td><td><strong>2</strong></td></tr></tbody></table>")
    assert block == Block("table", attrs={"header": True, "rows": [
        [[text("A")], [text("B")]], [[text("1")], [text("2", bold=True)]]]})


def test_a_table_with_no_thead_has_an_empty_header_and_a_th_in_the_body_is_a_cell():
    markup = '<table class="simple-table"><tbody><tr><th>A</th><td>1</td></tr></tbody></table>'
    assert one(markup).attrs == {"header": False, "rows": [[[text("A")], [text("1")]]]}
    assert gamma(page(markup)) == [("|  |  |\n| --- | --- |\n| A | 1 |",)]


def test_a_table_with_nothing_in_it_is_dropped():
    assert body('<table class="simple-table"><tbody><tr><td></td><td></td></tr></tbody></table>') == []


@pytest.mark.parametrize("markup, attrs, caption", [
    ('<figure class="image" data-notion-image="https://a.example/x.png" dir="ltr"><a href="https://a.example/x.png">'
     '<img src="https://a.example/x.png"/></a></figure>', {"url": "https://a.example/x.png"}, []),
    ('<figure class="image"><a href="P/x.png"><img style="width:576.5px" src="P/x.png"/></a>'
     "<figcaption>A <strong>cat</strong></figcaption></figure>",
     {"url": "P/x.png", "width": 576.5}, [text("A "), text("cat", bold=True)]),
    ('<figure><img src="P/x.png"/></figure>', {"url": "P/x.png"}, []),
    ('<figure class="image" data-notion-image="P/x.png"><img/></figure>', {"url": "P/x.png"}, []),
])
def test_images(markup, attrs, caption):
    assert one(markup) == Block("image", caption, attrs=attrs)


def test_an_image_goes_to_gamma_with_its_rounded_width_and_caption():
    markup = ('<figure class="image"><img style="width:576.5px" src="P/a%20cat%281%29.png"/>'
              "<figcaption>A cat</figcaption></figure>")
    assert gamma(page(markup)) == [("![A cat|576](P/a%20cat%281%29.png)",)]


def test_a_file_of_the_zip_keeps_a_hash_in_its_name_encoded():
    assert one('<figure class="image"><img src="P/a%23b.png"/></figure>').attrs == {"url": "P/a%23b.png"}


@pytest.mark.parametrize("markup, name, url", [
    ('<figure dir="ltr"><div class="source"><a href="https://a.example/d.pdf" data-notion-file="https://a.example/d.pdf" '
     'data-notion-file-alias="dummy.pdf">dummy.pdf</a></div></figure>', "dummy.pdf", "https://a.example/d.pdf"),
    ('<figure><div class="source"><a href="P/notes.pdf">notes.pdf</a></div></figure>', "notes.pdf", "P/notes.pdf"),
])
def test_files(markup, name, url):
    assert one(markup) == Block("file", [text(name)], attrs={"url": url})


@pytest.mark.parametrize("tag", ["audio", "video"])
def test_audio_and_video_are_files_of_their_kind(tag):
    block = one(f'<figure><{tag} controls="" src="P/a%20take.wav">https://x.example/a.wav" type="a"></{tag}></figure>')
    assert block == Block("file", [text("a take.wav")], attrs={"url": "P/a%20take.wav", "media": tag})


def test_a_stray_close_of_a_div_in_a_figure_closes_nothing():
    blocks = body('<div class="column-list"><div class="column"><figure><audio src="a.wav"></audio></div></figure></div>'
                  "<p>beside</p></div><p>after</p>")
    assert [b.type for b in blocks] == ["file", "paragraph", "paragraph"]
    html = page('<div class="column"><figure><audio src="a.wav"></audio></div><figcaption>A take</figcaption></figure></div>')
    assert [plain_text(b.rich) for b in read_page(html).blocks] == ["a.wav", "A take"]
    blocks = body('<figure class="callout"><div style="font-size:1.5em"><span class="icon">💡</span></div>'
                  '<div style="width:100%"><figure><audio src="a.wav"></audio></div></figure><p>inside</p></div></figure>'
                  "<p>after</p>")
    assert [b.type for b in blocks] == ["callout", "paragraph"] and plain_text(blocks[1].rich) == "after"
    assert [b.type for b in blocks[0].children] == ["file", "paragraph"]


def test_an_embed_is_its_address():
    markup = '<figure><div class="source"><a href="https://a.example/e">https://a.example/e</a></div></figure>'
    assert gamma(page(markup)) == [("https://a.example/e",)]
    frame = '<figure><div class="source"><iframe src="https://a.example/v" width="9"></iframe></div></figure>'
    assert gamma(page(frame)) == [("https://a.example/v",)]


def test_a_caption_under_an_embed_is_a_paragraph():
    markup = ('<figure><div class="source"><a href="https://a.example/e">https://a.example/e</a></div>'
              "<figcaption><strong>Seen</strong></figcaption></figure>")
    assert gamma(page(markup)) == [("https://a.example/e",), ("**Seen**",)]


def test_a_bookmark_is_its_title_as_a_link_and_loses_its_description():
    markup = ('<figure><a href="https://a.example/r" class="bookmark source"><div class="bookmark-info">'
              '<div class="bookmark-text"><div class="bookmark-title">Title</div>'
              '<div class="bookmark-description">Words</div></div><div class="bookmark-href">'
              '<img src="https://a.example/i" class="icon bookmark-icon"/>https://a.example/r</div></div>'
              '<img src="https://a.example/c" class="bookmark-image"/></a></figure>')
    assert gamma(page(markup)) == [("[Title](https://a.example/r)",)]


@pytest.mark.parametrize("attributes", ['class="link-to-page"', 'class="block-color-gray link-to-page"'])
def test_a_child_page_and_a_link_to_a_page_are_one_block(attributes):
    block = one(f'<figure {attributes}><a href="P/A%20B%20{UNDASHED}.html" data-notion-page-id="{PAGE_ID}">'
                '<span class="icon">📄</span>A B</a></figure>')
    assert block == Block("page", [text("A B")], attrs={"url": f"P/A%20B%20{UNDASHED}.html"})


def test_a_table_of_contents_is_dropped():
    assert body('<nav class="block-color-gray table_of_contents" data-notion-toc="">'
                '<a class="table_of_contents-link" href="#x">H</a></nav>') == []


# --- databases --------------------------------------------------------------------

DATABASE = ('<div id="40f85cc7-97e7-8085-90f6-e03efdcb070c" class="collection-content" dir="ltr">'
            '<h4 class="collection-title">Cases</h4><div class="collection-content-wrapper">'
            '<table class="collection-content"><thead><tr>'
            '<th><span class="icon property-icon"><img src="https://x.example/i.svg"/></span>Name</th>'
            '<th><span class="icon property-icon"><svg><path d="M1"></path></svg></span>Status</th></tr></thead>'
            '<tbody><tr id="r"><td class="cell-title"><a href="Cases/Row.html">Row</a></td><td>Done</td></tr></tbody>'
            "</table></div><br/><br/></div>")
DATABASE_ID = "40f85cc797e7808590f6e03efdcb070c"


def test_a_database_is_a_page_block_and_the_view_says_its_columns():
    read = read_page(page(DATABASE))
    assert read.blocks == [Block("page", [text("Cases")], attrs={"database": DATABASE_ID})]
    assert read.table_order == {DATABASE_ID: ["Name", "Status"]}
    assert read.table_title == {DATABASE_ID: "Cases"}


def test_a_database_goes_to_gamma_as_a_link_to_what_refs_name():
    read = read_page(page(DATABASE))
    assert contents(to_gamma(read, {DATABASE_ID: "Cases.csv"})) == [("[Cases](Cases.csv)",)]


def test_a_database_with_no_csv_is_its_title():
    assert gamma(page(DATABASE)) == [("Cases",)]


def test_a_bare_table_is_the_page_own_database():
    read = read_page(page('<table class="collection-content"><thead><tr><th>Name</th><th>Tag</th></tr></thead>'
                          "<tbody><tr><td>Row</td><td>a</td></tr></tbody></table>"))
    assert read.blocks == [Block("page", attrs={"database": UNDASHED})]
    assert read.table_order == {UNDASHED: ["Name", "Tag"]}


def test_a_collection_title_alone_is_no_heading():
    assert body('<h4 class="collection-title">Cases</h4>') == []


# --- a row's properties -----------------------------------------------------------

def row(name, cell, kind="text"):
    return (f'<tr class="property-row property-row-{kind}"><th><span class="icon property-icon">'
            f'<img src="https://x.example/i.svg"/></span>{name}</th><td>{cell}</td></tr>')


def properties(*rows):
    return f'<table class="properties"><tbody>{"".join(rows)}</tbody></table>'


@pytest.mark.parametrize("cell, line", [
    ('<span class="selected-value select-value-color-pink">Ready</span>', "Ready"),
    ('<span class="selected-value">math</span><span class="selected-value select-value-color-yellow">unicode</span>',
     "math, unicode"),
    ('<span class="status-value select-value-color-green"><div class="status-dot"></div>Done</span>', "Done"),
    ('<time datetime="2026-11-06">November 6, 2026</time>', "2026-11-06"),
    ('<time datetime="2020-07-14T10:10">14 juillet 2020 10:10</time>', "2020-07-14 10:10"),
    ("<time>@March 1, 2024</time>", "March 1, 2024"),
    ('<input type="checkbox" class="checkbox checkbox-on" disabled="" checked=""/>', "Yes"),
    ('<input type="checkbox" class="checkbox checkbox-off" disabled=""/>', "No"),
    ('<div class="checkbox checkbox-on"></div>', "Yes"),
    ('<a href="https://a.example/x" class="url-value">https://a.example/x</a>', "https://a.example/x"),
    ('<span class="user"><span class="icon text-icon user-icon"><span class="user-icon-inner">A</span></span>Ada</span>',
     "Ada"),
    ('<span class="user"><img src="u.png" class="icon user-icon"/>Ada</span><span class="user">'
     '<span class="icon"></span>Bob</span>', "Ada, Bob"),
    ('<a href="../G/A%201.html">A</a><a href="../G/B%202.html">B</a>', "[A](../G/A%201.html), [B](../G/B%202.html)"),
    ("12", "12"),
    ("<strong>rich</strong> text", "**rich** text"),
])
def test_a_property_as_the_line_of_its_row(cell, line):
    html = page("", properties(row("Key", cell)))
    assert gamma(html) == [(f"Key: {line}",)]


def test_empty_properties_are_left_out_and_the_rest_are_one_first_block():
    html = page("<p>Body</p>", '<p class="page-description"></p>'
                + properties(row("Empty", ""), row("Blank", " "), row("Formula", "12"), row("Number", "3")))
    read = read_page(html)
    assert list(read.fields) == ["Formula", "Number"]
    assert read.fields["Formula"] == [text("12")]
    assert gamma(html) == [("Formula: 12\nNumber: 3",), ("Body",)]


def test_no_properties_is_no_block():
    assert read_page(page("", properties())).blocks == []


# --- inline -----------------------------------------------------------------------

def test_the_marks():
    assert rich('<strong>b</strong> <em>i</em> <del>s</del> <code>c</code> <a href="https://a.example/">l</a> '
                '<span style="border-bottom:0.05em solid">u</span>') == [
        text("b", bold=True), text(" "), text("i", italic=True), text(" "), text("s", strike=True), text(" "),
        ("code", "c", {}), text(" "), text("l", link="https://a.example/"), text(" "), text("u", underline=True)]


def test_nested_marks_add_up_and_empty_ones_leave_nothing():
    assert rich("<strong></strong><em><strong>x</strong></em><code></code>") == [text("x", italic=True, bold=True)]


def test_a_mark_said_twice_is_one():
    assert rich("<strong><strong>x</strong></strong><strong>y</strong>") == [text("xy", bold=True)]


@pytest.mark.parametrize("markup, color", [
    ('<mark class="highlight-red">x</mark>', "red"),
    ('<mark class="highlight-red_background">x</mark>', "red_bg"),
    ('<mark class="highlight-teal">x</mark>', "green"),
    ('<mark class="highlight-teal_background" data-notion-highlight="teal_background">x</mark>', "green_bg"),
    ('<mark class="highlight-brown">x</mark>', "brown"),
    ('<mark class="highlight-pink"><mark class="highlight-yellow_background">x</mark></mark>', ("pink", "yellow_bg")),
    ('<mark class="highlight-red"><mark class="highlight-blue">x</mark></mark>', "blue"),
])
def test_colours(markup, color):
    assert rich(markup) == [text("x", color=color)]


@pytest.mark.parametrize("markup", ['<mark class="highlight-default">x</mark>',
                                    '<mark class="highlight-default_background">x</mark>', "<mark>x</mark>"])
def test_the_default_colour_means_nothing(markup):
    assert rich(markup) == [text("x")]


@pytest.mark.parametrize("markup, content", [
    ('<mark class="highlight-red">x</mark>', '<span style="color:#e5484d">x</span>'),
    ('<mark class="highlight-red_background">x</mark>', '<span style="background:#e5484d55">x</span>'),
    ('<mark class="highlight-yellow_background">x</mark>', "==x=="),
    ('<mark class="highlight-brown">x</mark>', '<span style="color:#9f765a">x</span>'),
    ('<mark class="highlight-pink"><mark class="highlight-blue_background">x</mark></mark>',
     '<span style="color:#e93d82; background:#3b82f655">x</span>'),
])
def test_a_colour_goes_to_gamma_as_the_editor_writes_it(markup, content):
    assert gamma(page(f"<p>{markup}</p>")) == [(content,)]


@pytest.mark.parametrize("markup, block_type", [
    ('<p class="block-color-red">{}</p>', "paragraph"),
    ('<h2 class="block-color-red">{}</h2>', "heading"),
    ('<blockquote class="block-color-red">{}</blockquote>', "quote"),
    ('<ul class="block-color-red bulleted-list"><li>{}</li></ul>', "bullet"),
    ('<ol class="block-color-red numbered-list" start="1"><li>{}</li></ol>', "number"),
    ('<details class="block-color-red toggle"><summary>{}</summary></details>', "toggle"),
    ('<details class="toggle"><summary><h2 class="block-color-red">{}</h2></summary></details>', "toggle"),
    ('<ul class="block-color-red toggle"><li><details open=""><summary>{}</summary></details></li></ul>', "toggle"),
    ('<ul class="bulleted-list"><li><p class="block-color-red">{}</p></li></ul>', "bullet"),
    ('<ol class="numbered-list" start="1"><li><p class="block-color-red">{}</p></li></ol>', "number"),
    ('<ul class="to-do-list"><li><p class="block-color-red">{}</p></li></ul>', "todo"),
    ('<blockquote><p class="block-color-red">{}</p></blockquote>', "quote"),
    ('<ul class="block-color-blue bulleted-list"><li><p class="block-color-red">{}</p></li></ul>', "bullet"),
])
def test_a_block_colour_stays_on_the_block(markup, block_type):
    block = one(markup.format("x"))
    assert (block.type, block.rich, block.attrs["color"]) == (block_type, [text("x")], "red")


def test_a_toggle_keeps_its_own_colour_in_a_coloured_list():
    block = one('<ul class="block-color-red toggle"><li><details class="block-color-blue toggle"><summary>x</summary>'
                '</details></li></ul>')
    assert block.attrs == {"color": "blue"}


def test_a_coloured_list_of_toggles_colours_only_the_toggles():
    assert [block.attrs for block in body('<ul class="block-color-red toggle"><li><details><summary>x</summary>'
                                          '</details></li><li><p>y</p></li></ul>')] == [{"color": "red"}, {}]


def test_a_callout_colour_is_its_type_and_a_coloured_paragraph_stays_coloured_text():
    markup = ('<aside class="callout" data-notion-callout-icon="💡"><div style="width:100%">'
              '<p class="block-color-red">a</p><p class="block-color-red">b</p></div></aside>')
    block = one(markup)
    assert (block.rich, [kid.rich for kid in block.children], block.attrs) == ([], [[text("a")], [text("b")]],
                                                                               {"icon": "💡"})
    assert gamma(page(markup)) == [
        ('> [!info] 💡\n> <span style="color:#e5484d">a</span>\n>\n> <span style="color:#e5484d">b</span>',)]


def test_a_plain_first_paragraph_is_a_coloured_callout_own_text():
    block = one('<aside class="block-color-red_background callout"><p>a</p></aside>')
    assert (block.rich, block.children, block.attrs) == ([text("a")], [], {"color": "red_bg"})


def test_a_block_colour_goes_under_a_run_of_its_own():
    assert gamma(page('<p class="block-color-red_background">a <mark class="highlight-blue">b</mark></p>')) == [
        ('<span style="background:#e5484d55">a</span> '
         '<span style="color:#3b82f6; background:#e5484d55">b</span>',)]


@pytest.mark.parametrize("name", ["block-color-default", "block-color-default_background"])
def test_the_default_block_colour_means_nothing(name):
    assert one(f'<p class="{name}">x</p>').attrs == {}


def test_an_inline_equation_of_2026_reads_the_attribute_and_no_katex():
    markup = ('a <span data-notion-inline-equation="x^2" class="notion-text-equation-token" contenteditable="false">'
              '<span class="katex"><span class="katex-html" aria-hidden="true">x2</span></span></span>.')
    assert rich(markup) == [text("a "), ("math", "$x^2$", {}), text(".")]


def test_an_older_inline_equation_reads_the_annotation_past_its_byte_order_marks():
    markup = ('<span class="notion-text-equation-token" contenteditable="false"><span>﻿</span><span><span class="katex">'
              '<span class="katex-mathml"><math><semantics><mrow><mi>x</mi></mrow>'
              '<annotation encoding="application/x-tex">x^2</annotation></semantics></math></span>'
              '<span class="katex-html">x2</span></span></span><span>﻿</span></span>')
    assert rich(markup) == [("math", "$x^2$", {})]


def test_an_equation_under_marks_keeps_them():
    assert rich('<strong><span data-notion-inline-equation="x" class="notion-text-equation-token"></span></strong>') == [
        ("math", "$x$", BOLD)]


def test_a_control_space_at_the_end_of_an_equation_leaves_no_backslash_on_the_dollar():
    assert rich('<span data-notion-inline-equation="h(x)\\ " class="notion-text-equation-token"></span>') == [
        ("math", "$h(x)$", {})]


def test_an_equation_math_cannot_hold_stays_its_text():
    assert rich('<span data-notion-inline-equation="a$b" class="notion-text-equation-token"></span>') == [text("a$b")]


@pytest.mark.parametrize("markup, leaf", [
    ('<time datetime="2026-10-09">October 9, 2026</time>', ("raw", "@2026-10-09", {})),
    ('<time datetime="2026-10-09T14:30">October 9</time>', ("raw", "@2026-10-09 14:30", {})),
    ("<time>@October 9, 2026</time>", text("@October 9, 2026")),
])
def test_dates(markup, leaf):
    assert rich(markup) == [leaf]


def test_a_date_is_a_mention_and_a_literal_at_sign_is_not():
    assert gamma(page('<p><time datetime="2026-10-09">x</time> and @ada</p>')) == [("@2026-10-09 and \\@ada",)]


def test_icons_and_a_person_initial_are_no_text():
    assert rich('<span class="user"><span class="icon text-icon user-icon"><span class="user-icon-inner">A</span>'
                "</span>@Ada</span>") == [text("@Ada")]
    assert rich('<a href="P.html"><span class="icon">📄</span><img class="icon" src="i.png"/>Page</a>') == [
        text("Page", link="P.html")]


def test_a_picture_in_text_is_a_link_since_rich_text_holds_no_image():
    assert rich('a <img src="https://a.example/i.png" alt="pic"/>') == [text("a "), text("pic", link="https://a.example/i.png")]


def test_a_link_to_a_block_of_the_page_is_its_words():
    assert rich('<a href="#40f85cc7">the block</a>') == [text("the block")]


def test_a_script_url_is_no_link():
    assert rich('<a href="javascript:alert(1)">x</a>') == [text("x")]


def test_a_line_break_stays_and_trailing_ones_go():
    assert rich("a<br/>b<br/><br/>") == [text("a"), ("br",), text("b")]
    assert rich("a\nb\n") == [text("a"), ("br",), text("b")]


def test_spaces_stay_inside_a_line_and_go_from_its_edges():
    assert rich("a  b ") == [text("a  b")]
    assert rich("        a") == [text("a")]


@pytest.mark.parametrize("markup, content", [
    ("$5, A &amp; B, &lt;tag&gt;.", "$5, A & B, \\<tag>."),
    ("$5 and $6", "\\$5 and \\$6"),
    ("1. Intro", "1\\. Intro"),
    ("[x](y)", "\\[x](y)"),
    ("*a*", "\\*a\\*"),
])
def test_text_stays_literal_through_to_gamma(markup, content):
    assert gamma(page(f"<p>{markup}</p>")) == [(content,)]


# --- depth ------------------------------------------------------------------------

def test_a_walk_stops_at_its_depth_with_the_text_kept():
    deep = "<blockquote>" * 400 + "bottom" + "</blockquote>" * 400
    read = read_page(page(deep))
    assert len(read.warnings) == 1
    block, levels = read.blocks[0], 1
    while block.children:
        block, levels = block.children[0], levels + 1
    assert block.rich == [text("bottom")]
    assert levels < 210


@pytest.mark.parametrize("markup, block_type", [
    ("<p><span style>x</span></p>", "paragraph"),
    ('<figure class="image"><img style src="a.png"/></figure>', "image"),
    ('<figure class="image"><img style="width:1.2.3px" src="a.png"/></figure>', "image"),
    ('<figure class="image"><img style="width:...px" src="a.png"/></figure>', "image"),
    (f'<figure class="image"><img style="width:{"9" * 400}px" src="a.png"/></figure>', "image"),
    ("<details><summary style>x</summary></details>", "toggle"),
    ('<aside class="callout"><div style><span class="icon">💡</span></div><div>x</div></aside>', "callout"),
    ('<ol class="numbered-list" start><li>x</li></ol>', "number"),
    ('<ol class="numbered-list" start="²"><li>x</li></ol>', "number"),
    ('<ol class="numbered-list" start="x"><li>x</li></ol>', "number"),
    (f'<ol class="numbered-list" start="{"9" * 5000}"><li>x</li></ol>', "number"),
])
def test_an_attribute_with_no_sense_in_it_is_no_failure(markup, block_type):
    block = one(markup)
    assert block.type == block_type and "width" not in block.attrs and block.attrs.get("start", 1) == 1
    assert gamma(page(markup))


@pytest.mark.parametrize("style, width", [("width:240px", 240), ("width:240.5px", 240.5), ("a:b;width: 40px", 40)])
def test_an_image_width_of_the_export_is_read(style, width):
    assert one(f'<figure class="image"><img style="{style}" src="a.png"/></figure>').attrs["width"] == width


def test_marks_stop_at_the_depth_too():
    read = read_page(page("<p>" + "<span>" * 400 + "x" + "</span>" * 400 + "</p>"))
    assert len(read.warnings) == 1
    assert plain_text(read.blocks[0].rich) == "x"


def test_a_page_past_the_stack_is_a_warning_and_no_crash():
    deep = page("<div>" * 30_000 + "x" + "</div>" * 30_000)
    read = read_page(deep)
    assert read.title == "A page"
    assert read.warnings


def test_a_guard_for_the_stack_itself(monkeypatch):
    def overflow(self, root):
        raise RecursionError
    monkeypatch.setattr("gamma.notion_html._Reader.read", overflow)
    read = read_page(page("<p>x</p>"))
    assert read.blocks == [] and read.warnings


# --- the fixtures -----------------------------------------------------------------

def test_the_export_holds_no_space_id():
    for name in ("roundtrip_html", "roundtrip_md"):
        for path, data in files(name).items():
            assert b"data-notion-space-id" not in data, path


def test_every_page_of_the_export_is_one():
    pages = [data for path, data in files("roundtrip_html").items() if path.endswith(".html")]
    assert len(pages) == 8
    assert all(is_notion_page(data[:64 * 1024]) for data in pages)


def test_the_fixture_zips_are_whole():
    assert export_zip("roundtrip_html")[:2] == b"PK" and export_zip("roundtrip_md")[:2] == b"PK"


def test_the_round_trip_page():
    read = read_page(html_page("roundtrip_html", "Round trip "))
    assert (read.title, read.notion_id) == ("Round trip", "40f85cc797e78099b2d9fe6f9e045640")
    assert read.warnings == []
    nodes = contents(to_gamma(read))
    shown = [node[0] for node in nodes]
    assert "- Bullet A" in shown and "# 01 · Text and formatting" in shown
    assert nodes[shown.index("- Bullet A")] == ("- Bullet A", [("- Nested A.1", [("- Nested A.1.a",)])])
    assert nodes[shown.index("1. Number one")] == ("1. Number one", [("1. Nested number one",), ("2. Nested number two",)])
    assert "2. Number two" in shown
    assert "> Quote fixture: preserve punctuation, nesting and links." in shown
    assert "---" in shown
    assert "$$\nE=mc^2\n$$" in shown
    assert "> [!info] ♻️\n> Roundtrip callout: preserve emoji, background and nested text." in shown
    assert nodes[shown.index("## Expandable heading fixture")] == (
        "## Expandable heading fixture", "collapsed", [("Child content under toggle heading.",)])
    assert nodes[shown.index("Toggle fixture")] == ("Toggle fixture", "collapsed", [("Nested toggle child",)])
    assert not any("Third-level heading" in s and s.startswith("[") for s in shown)     # the table of contents


def test_the_round_trip_page_keeps_literals_and_exact_code():
    shown = [node[0] for node in contents(to_gamma(read_page(html_page("roundtrip_html", "Round trip "))))]
    line = next(s for s in shown if s.startswith("**Bold**"))
    assert "`inline_code(x)`" in line and "<u>underline</u>" in line and "~~strike~~" in line
    assert line.endswith("Literal delimiters: $5, A & B, \\<tag>.")
    assert '```python\ndef round_trip(x):\n    return {"value": x, "ok": True}\n\nprint(round_trip("λ"))\n```' in shown
    assert next(s for s in shown if s.startswith("```mermaid")).startswith("```mermaid\ngraph LR\n  A[Notion] --> B[")
    assert next(s for s in shown if s.startswith("```xml")).startswith('```xml\n<svg xmlns="http://www.w3.org/2000/svg"')


def test_the_round_trip_page_says_its_databases():
    read = read_page(html_page("roundtrip_html", "Round trip "))
    cases, groups = "40f85cc797e7808590f6e03efdcb070c", "40f85cc797e780d39341dde6003507bc"
    assert read.table_order[cases] == ["Name", "Select", "Multi-select", "Date", "Number", "Checkbox", "URL",
                                       "Person", "Formula", "Groups"]
    assert read.table_order[groups] == ["Name"]
    assert read.table_title == {cases: "Cases", groups: "Groups"}
    databases = [block.attrs["database"] for block in read.blocks if block.type == "page" and "database" in block.attrs]
    assert databases == [groups, cases]


def test_the_round_trip_page_lists_its_link_lines():
    read = read_page(html_page("roundtrip_html", "Round trip "))
    assert [label for label, _ in read.links] == ["Child A — structure", "Child B — media and links"]
    assert read.links[0][1].endswith("40f85cc797e780f5bf59d8539ae0d55d.html")


def test_a_paragraph_that_is_one_link_is_a_link_line_and_one_with_words_is_not():
    read = read_page(page('<p><a href="A.html">A</a></p><p>See <a href="B.html">B</a></p>'
                          '<ul class="bulleted-list"><li>x<div class="indented"><p><a href="C.html">C</a></p></div></li></ul>'))
    assert read.links == [("A", "A.html"), ("C", "C.html")]


def test_a_row_page_writes_the_lines_of_the_markdown_export():
    html = html_page("roundtrip_html", "RT-001 Alpha")
    nodes = contents(to_gamma(read_page(html)))
    assert nodes[0][0].split("\n") == [
        "Checkbox: Yes", "Date: 2026-11-06", "Formula: 12",
        "Groups: [Group A](../Groups/Group%20A%2040f85cc797e780979671c7aba1a2a0df.html)",
        "Multi-select: math, unicode", "Number: 3.14159", "Person: Ada Example", "Select: Ready",
        "URL: https://github.com/tim4431/Gamma"]
    assert [node[0] for node in nodes[1:]] == [
        "# RT-001 row body", "This body must export as a page alongside the database CSV.",
        "- [x] Checked row task", "- [ ] Pending row task", "Inline equation: $x^2+y^2=z^2$.", "> Row-body quote."]


def test_a_row_of_empty_values_has_its_formula_and_checkbox():
    assert contents(to_gamma(read_page(html_page("roundtrip_html", "RT-002")))) == [("Checkbox: No\nFormula: 18",)]


def test_a_page_with_nothing_in_it_is_a_page():
    read = read_page(html_page("roundtrip_html", "Group A"))
    assert (read.title, read.blocks, read.fields) == ("Group A", [], {})


def test_the_media_page():
    nodes = contents(to_gamma(read_page(html_page("roundtrip_html", "Child B"))))
    shown = [node[0] for node in nodes]
    assert "![](https://github.com/tim4431.png)" in shown
    assert "[dummy.pdf](https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf)" in shown
    assert "[Sibling Child A](Child%20A%20%E2%80%94%20structure%2040f85cc797e780f5bf59d8539ae0d55d.html)" in shown


def test_the_older_page():
    read = read_page(html_page("older", "Older shapes"))
    assert (read.title, read.notion_id, read.warnings) == ("Older shapes", "1580080aa5a38194a9c0e77779ee82d5", [])
    assert contents(to_gamma(read, {"1590080aa5a3802488c2e857dea7aa00": "People.csv"})) == [
        ("What the page is for.",),
        ("# A heading",),
        ("### A plain heading",),
        ('## <span style="color:#3b82f6">A blue heading</span>',),
        ("An outer paragraph.", [('<span style="color:#e5484d">An indented paragraph.</span>', [("Deeper still.",)])]),
        ('<span style="color:#e5484d">red</span> <span style="background:#30a46c55">green ground</span> no colour '
         '<span style="color:#e93d82; background:#d4a01755">both</span> ==marked== '
         '<span style="color:#9f765a">brown</span>',),
        ("Inline $x^2$ math, on \\@June 1, 2024 and @2024-06-02.",),
        ("$$\nE = mc^2\n$$",),
        ("> [!info] 💡\n> A callout's text.\n>\n> Its second paragraph.",),
        ('> [!danger] <img src="https://www.notion.so/icons/info-alternate_green.svg" '
         'alt="https://www.notion.so/icons/info-alternate_green.svg" width="40px" />\n> Bare text in a callout.',),
        ("> [!note]\n> No icon.\n>\n> - An item in it",),
        ("A toggle.", "collapsed", [("What it folds.",)]),
        ("With nothing in it.",),
        ("# A toggle heading 1", "collapsed", [("### A toggle heading 3", "collapsed", [("With a hidden block.",)])]),
        ("- [ ] To do", [("- [x] Done under it",)]),
        ("- [x] A box in a span",),
        ("3. Third",), ("1. No start",), ("2. Two in one list",),
        ('```python\nprint("Hello <world>")\n```',),
        ("```\n# $5 *not* markdown\n```",),
        ("Left column.",), ("Right column.",),
        ("[take one.wav](Older%20shapes/take%20one.wav)",), ("The first take",),
        ("![A cat, asleep|576](Older%20shapes/a%20cat.png)",),
        ("[notes.pdf](Older%20shapes/notes.pdf)",),
        ("https://example.com/embed",), ("An embed",),
        ("https://example.com/video",),
        ("[A bookmark](https://example.com/read)",),
        ("[A sub page](Older%20shapes/A%20sub%20page%201590080aa5a3802488c2e857dea7aa7d.html)",),
        ("|  |  |\n| --- | --- |\n| A | 1 \\| 2 |\n| B | **3** |",),
        ("[People and the long name a file cuts off at fifty characters](People.csv)",),
    ]


def tree(markup):
    parser = _Parser(keep_p=True)
    parser.feed(markup)
    parser.close()
    return parser.root


@pytest.mark.parametrize("markup", [
    "<b>" * 100000 + "</i>" * 100000,
    "<b>" * 100000 + "<li></li>" * 100000,
    "<b>" * 200 + "</i>" * 50000,
    "<ul>" + "<b>" * 200 + "<li></li>" * 50000,
    "<figure>" + "<b>" * 200 + "</div>" * 50000,
], ids=["end tags", "items", "end tags in reach", "items in a list", "stray closes in a figure"])
def test_tags_that_close_nothing_cost_no_walk_up_a_long_stack(markup):
    start = time.perf_counter()
    tree(markup)
    assert time.perf_counter() - start < 10


@pytest.mark.parametrize("markup", ["<b>" * 50000 + "</i>" * 50000, "<b>" * 50000 + "<li></li>" * 50000],
                         ids=["end tags", "items"])
def test_a_whole_page_of_tags_that_close_nothing_reads_in_time(markup):
    start = time.perf_counter()
    read_page(page(markup))
    assert time.perf_counter() - start < 20


@pytest.mark.parametrize("opening", ["<figure>", '<div class="source">', '<div class="link-to-page">'],
                         ids=["figures", "sources", "links to pages"])
def test_a_page_of_blocks_that_look_for_what_they_hold_reads_in_time(opening):
    count = 4800000 // (len(opening) + 6)
    start = time.perf_counter()
    read_page(page(opening * count + "</div>" * count))
    assert time.perf_counter() - start < 20


def test_a_figure_finds_its_picture_a_few_levels_down():
    block = one('<figure class="image"><div><a href="p.png"><picture><img src="p.png"/></picture></a></div></figure>')
    assert block.type == "image" and block.attrs["url"] == "p.png"


def test_what_nests_past_the_limit_lies_flat_and_still_closes():
    root = tree("<section>a" + "<div>" * 300 + "deep" + "</div>" * 300 + "tail</section>after")
    assert [node_tag(n) for n in root.children] == ["section", None]
    assert root.children[1] == "after"
    assert node_text(root.children[0]) == "adeeptail"


def test_a_page_goes_on_after_blocks_nested_past_what_is_kept():
    blocks = body("<div>" * 300 + "<p>deep</p>" + "</div>" * 300 + "<p>after</p>")
    assert blocks[-1] == Block("paragraph", [text("after")])
    assert "deep" in " ".join(plain_text(block.rich) for block in blocks)


def test_the_older_page_says_its_database():
    read = read_page(html_page("older", "Older shapes"))
    database = "1590080aa5a3802488c2e857dea7aa00"
    assert read.table_order == {database: ["Name", "Role"]}
    assert read.table_title == {database: "People and the long name a file cuts off at fifty characters"}
