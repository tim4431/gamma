"""Gamma's blocks and the Notion model, both ways (gamma/notion_gamma.py): what
one block's content holds (``blocks_of``), what a page's items become
(``from_gamma``), the nodes a model page writes (``to_gamma``), and every row
of the design's table of constructs carried through and back."""

import dataclasses
import time

import pytest

from gamma.export_walk import walk
from gamma.notion_blocks import Block, Page, callout_color
from gamma.notion_gamma import blocks_of, from_gamma, leaves_of, to_gamma

BOLD, ITALIC = {"bold": True}, {"italic": True}
UPLOAD = "/api/uploads/ab12cd.pdf"


def text(s, **marks):
    return ("text", s, marks)


def para(s, *children):
    return Block("paragraph", [text(s)], list(children))


def bullet(s, *children):
    return Block("bullet", [text(s)], list(children))


def heading(s, level=2):
    return Block("heading", [text(s)], attrs={"level": level})


def node(content, *children, **properties):
    return {"content": content, "properties": properties, "children": list(children)}


def nodes_of(*blocks, refs=None):
    return to_gamma(Page("Title", list(blocks)), refs)


class Links:
    """An export of two pages, A and B (and C, outside it), and a block in A.
    Images are bundled, as in the zip; ``None`` for ``images`` leaves them out."""

    REFS = {"A": {"content": "Page A", "page_title": "Page A", "page_id": "A"},
            "B": {"content": "Page B", "page_title": "Page B", "page_id": "B"},
            "C": {"content": "Page C", "page_title": "Page C", "page_id": "C"},
            "a1": {"content": "**Block** one\nline two", "page_title": "Page A", "page_id": "A"},
            "list": {"content": "- x\n- y", "page_title": "Page B", "page_id": "B"},
            "math": {"content": "$$\nx\n$$", "page_title": "Page B", "page_id": "B"},
            "out": {"content": "text", "page_title": "Page C", "page_id": "C"},
            "self": {"content": "mine", "page_title": "Page A", "page_id": "A"},
            "loop": {"content": "see ![[loop]]", "page_title": "Page B", "page_id": "B"},
            "fence": {"content": "```\n[[B]]\n```", "page_title": "Page C", "page_id": "C"},
            "empty": {"content": "", "page_title": "Page B", "page_id": "B"},
            "code": {"content": "```py\nx = 1\n```", "page_title": "Page B", "page_id": "B"},
            "image": {"content": "![|300](/api/uploads/c.png)", "page_title": "Page B", "page_id": "B"},
            "orphan": {"content": "```\nx\n```", "page_title": "", "page_id": "B"},
            "item": {"content": "- item\n- more", "page_title": "Page B", "page_id": "B"},
            "number": {"content": "2. second", "page_title": "Page B", "page_id": "B"},
            "task": {"content": "- [x] done", "page_title": "Page B", "page_id": "B"},
            "numbered": {"content": "1. Intro", "page_title": "1. Intro", "page_id": "numbered"},
            "ref": {"content": "[[abc123]]", "page_title": "Page B", "page_id": "B"},
            "item_ref": {"content": "- [[abc123]]", "page_title": "Page B", "page_id": "B"},
            "ref_in_words": {"content": "see [[abc123]] here", "page_title": "Page B", "page_id": "B"},
            "rule": {"content": "---", "page_title": "Page B", "page_id": "B"},
            "nested": {"content": "- - nested", "page_title": "Page B", "page_id": "B"},
            "quoted": {"content": "> - quoted", "page_title": "Page B", "page_id": "B"},
            "callout": {"content": "> [!note] Title\n> text", "page_title": "Page B", "page_id": "B"},
            "long": {"content": "**a " * 50000, "page_title": "Page B", "page_id": "B"}}
    FILES = {"A": "A.md", "B": "B.md"}

    def __init__(self, images="bundle/"):
        self.images, self.asked, self.warned = images, [], []

    def page(self, ref):
        return self.FILES.get(ref)

    def asset(self, url, kind):
        self.asked.append((url, kind))
        return self.images + url.rsplit("/", 1)[-1] if self.images and kind == "image" else None

    def warn(self, title, reason):
        self.warned.append((title, reason))

    def ref(self, block_id):
        return self.REFS.get(block_id)


def tree(*nodes):
    return walk({"children": list(nodes)})


def model_of(*nodes, page_id=None):
    """Nodes (``to_gamma``'s, or blocks of a page) as model blocks, without
    the Gamma ids, which only a page read from Gamma has."""
    def bare(block):
        return dataclasses.replace(block, id=None, children=[bare(b) for b in block.children])
    return [bare(b) for b in from_gamma(tree(*nodes), Links(), page_id)]


def position(page=3):
    return {"pageNumber": page, "boundingRect": {"x1": 0, "y1": 0, "x2": 1, "y2": 1}, "rects": []}


# --- every row of the table, both ways -----------------------------------------------

CALLOUT = Block("callout", [text("body")], attrs={"icon": "💡", "color": "yellow_bg"})
TABLE = Block("table", attrs={"header": True, "rows": [[[text("a")], [text("b")]], [[text("1")], [text("2")]]]})
NO_HEADER = Block("table", attrs={"header": False, "rows": [[[text("1")], [text("2")]]]})
TOGGLE_HEADING = Block("toggle", [text("x")], [para("kid")], {"heading": 2})
PAGE = Block("page", [text("Page B")], attrs={"ref": "B", "url": "B.md"})

# Each row: the name, the model, its Gamma nodes. ``to_gamma`` writes the nodes
# and ``from_gamma`` reads them back as the model.
ROWS = [
    ("paragraph", para("x"), [node("x")]),
    ("paragraph with children", para("x", para("kid")), [node("x", node("kid"))]),
    ("heading 1", heading("x", 1), [node("# x")]),
    ("heading 2", heading("x", 2), [node("## x")]),
    ("heading 3", heading("x", 3), [node("### x")]),
    ("heading 4, Gamma only", heading("x", 4), [node("#### x")]),
    ("toggle heading", TOGGLE_HEADING, [node("## x", node("kid"), collapsed=True)]),
    ("toggle", Block("toggle", [text("x")], [para("kid")]), [node("x", node("kid"), collapsed=True)]),
    ("toggle in a toggle", Block("toggle", [text("x")], [Block("toggle", [text("y")], [para("z")])]),
     [node("x", node("y", node("z"), collapsed=True), collapsed=True)]),
    ("bullet", bullet("x"), [node("- x")]),
    ("bullet with children", bullet("x", para("kid"), bullet("y")), [node("- x", node("kid"), node("- y"))]),
    ("empty bullet", Block("bullet"), [node("- ")]),
    ("empty bullet with children", Block("bullet", [], [para("kid")]), [node("- ", node("kid"))]),
    ("number", Block("number", [text("x")], attrs={"start": 3}), [node("3. x")]),
    ("empty number", Block("number", attrs={"start": 2}), [node("2. ")]),
    ("to-do", Block("todo", [text("x")], attrs={"checked": False}), [node("- [ ] x")]),
    ("empty to-do", Block("todo", attrs={"checked": False}), [node("- [ ] ")]),
    ("to-do done, with children", Block("todo", [text("x")], [para("kid")], {"checked": True}),
     [node("- [x] x", node("kid"))]),
    ("quote", Block("quote", [text("x")]), [node("> x")]),
    ("quote of lines", Block("quote", [text("x"), ("br",), text("y")]), [node("> x\n> y")]),
    ("quote with paragraph children", Block("quote", [text("x")], [para("y")]), [node("> x\n>\n> y")]),
    ("quote with list children", Block("quote", [text("x")], [bullet("a"), bullet("b")]),
     [node("> x\n>\n> - a\n> - b")]),
    ("quote with a nested child", Block("quote", [text("x")], [bullet("a", bullet("b"))]),
     [node("> x", node("- a", node("- b")))]),
    ("callout", CALLOUT, [node("> [!warning] 💡\n> body")]),
    ("callout, no icon", Block("callout", [text("b")], attrs={"color": "blue_bg"}), [node("> [!note]\n> b")]),
    ("callout with a held child", Block("callout", [text("b")], [Block("code", attrs={"text": "c"})],
                                        {"icon": "💡", "color": "red_bg"}),
     [node("> [!danger] 💡\n> b", node("```\nc\n```"))]),
    ("code", Block("code", attrs={"text": "a\n\n- b", "language": "python"}), [node("```python\na\n\n- b\n```")]),
    ("code, no language", Block("code", attrs={"text": "x"}), [node("```\nx\n```")]),
    ("code, a language of two words", Block("code", attrs={"text": "x", "language": "visual basic"}),
     [node("```visual basic\nx\n```")]),
    ("equation", Block("equation", attrs={"text": "a\n+ b"}), [node("$$\na\n+ b\n$$")]),
    ("divider", Block("divider"), [node("---")]),
    ("table", TABLE, [node("| a | b |\n| --- | --- |\n| 1 | 2 |")]),
    ("table, no header", NO_HEADER, [node("|  |  |\n| --- | --- |\n| 1 | 2 |")]),
    ("image", Block("image", [text("cap")], attrs={"url": "a.png", "width": 300}), [node("![cap|300](a.png)")]),
    ("image, a caption of markup", Block("image", [text("E = mc^2 <b> $5 *x*")], attrs={"url": "a.png"}),
     [node(r"![E = mc^2 \<b> \$5 \*x\*](a.png)")]),
    ("image, no caption", Block("image", attrs={"url": "a.png"}), [node("![](a.png)")]),
    ("file", Block("file", [text("n")], attrs={"url": UPLOAD}), [node(f"[n]({UPLOAD})")]),
    ("audio", Block("file", [text("n")], attrs={"url": "/api/uploads/ab.mp3"}), [node("[n](/api/uploads/ab.mp3)")]),
    ("bookmark", Block("paragraph", [text("title", link="https://x.org/a")]), [node("[title](https://x.org/a)")]),
    ("embed", Block("paragraph", [text("https://x.org/a", link="https://x.org/a")]), [node("https://x.org/a")]),
]


@pytest.mark.parametrize("model, nodes", [r[1:] for r in ROWS], ids=[r[0] for r in ROWS])
def test_a_model_block_is_these_nodes(model, nodes):
    assert nodes_of(model) == nodes


@pytest.mark.parametrize("model, nodes", [r[1:] for r in ROWS], ids=[r[0] for r in ROWS])
def test_these_nodes_are_the_model_block(model, nodes):
    assert model_of(*nodes) == [model]


@pytest.mark.parametrize("model, nodes", [r[1:] for r in ROWS], ids=[r[0] for r in ROWS])
def test_gamma_through_the_model_is_gamma(model, nodes):
    assert nodes_of(*model_of(*nodes)) == nodes


def test_a_whole_page_through_the_model_and_back():
    model = [m for _, m, _ in ROWS]
    assert model_of(*nodes_of(*model)) == model


def test_a_page_block_through_the_model_is_its_ref():
    assert model_of(node("[[B]]"), page_id="A") == [PAGE]
    assert nodes_of(PAGE) == [node("[[B]]")]


def test_a_child_page_read_from_notion_is_a_link_for_rewrite_links():
    block = Block("page", [text("Child page")], attrs={"url": "Child page.html"})
    assert nodes_of(block) == [node("[Child page](Child%20page.html)")]


def test_an_inline_database_is_a_link_to_its_csv():
    block = Block("page", [text("People")], attrs={"database": "d1"})
    assert nodes_of(block, refs={"d1": "People d1.csv"}) == [node("[People](People%20d1.csv)")]


def test_a_page_with_nowhere_to_go_is_its_title():
    assert nodes_of(Block("page", [text("People")], attrs={"database": "d1"})) == [node("People")]


# --- rows with no way back -----------------------------------------------------------

def test_empty_paragraphs_are_dropped_and_their_children_stay():
    assert nodes_of(Block("paragraph"), para("x")) == [node("x")]
    assert nodes_of(Block("paragraph", [], [para("kid")])) == [node("kid")]


def test_a_childless_toggle_is_a_plain_paragraph():
    assert nodes_of(Block("toggle", [text("x")])) == [node("x")]
    assert model_of(node("x")) == [para("x")]


def test_a_childless_toggle_heading_is_a_heading():
    assert nodes_of(Block("toggle", [text("x")], attrs={"heading": 2})) == [node("## x")]


def test_a_toggle_whose_children_are_empty_is_a_plain_paragraph():
    assert nodes_of(Block("toggle", [text("x")], [Block("paragraph")])) == [node("x")]


def test_a_heading_is_flat():
    assert nodes_of(Block("heading", [text("x")], [para("kid")], {"level": 2})) == [node("## x"), node("kid")]


def test_an_open_heading_with_children_loses_its_nesting():
    assert model_of(node("## x", node("kid"))) == [heading("x"), para("kid")]
    assert nodes_of(*model_of(node("## x", node("kid")))) == [node("## x"), node("kid")]


@pytest.mark.parametrize("rich", [[text("a"), ("br",), text("b")], [text("a\nb")]])
def test_a_line_break_cannot_stand_in_a_heading_or_a_cell(rich):
    assert nodes_of(Block("heading", rich, attrs={"level": 1})) == [node("# a b")]
    assert nodes_of(Block("table", attrs={"header": True, "rows": [[rich]]})) == [node("| a b |\n| --- |")]


def test_table_rows_are_as_wide_as_the_widest_and_cells_hold_pipes():
    rows = [[[text("a|b")], [text("c")]], [[("code", "x|y", {})]]]
    content = "| a\\|b | c |\n| --- | --- |\n| `x\\|y` |  |"
    assert nodes_of(Block("table", attrs={"header": True, "rows": rows})) == [node(content)]
    assert blocks_of(content)[0].attrs["rows"][1] == [[("code", "x|y", {})], []]


def test_a_fence_outruns_the_fences_in_it():
    code = Block("code", attrs={"text": "```\nx\n```"})
    assert nodes_of(code) == [node("````\n```\nx\n```\n````")]
    assert blocks_of("````\n```\nx\n```\n````") == [code]


def test_a_number_keeps_its_own_start():
    assert [b.attrs["start"] for b in blocks_of("3. a\n4. b")] == [3, 4]
    assert nodes_of(*blocks_of("3) a")) == [node("3. a")]


@pytest.mark.parametrize("model, content", [
    (Block("bullet", [text("a"), ("br",), text("b")]), "- a\n  b"),
    (Block("number", [text("a"), ("br",), text("b")], attrs={"start": 10}), "10. a\n    b"),
    (Block("todo", [text("a"), ("br",), text("b")], attrs={"checked": False}), "- [ ] a\n  b"),
])
def test_an_items_lines_sit_at_its_text(model, content):
    assert nodes_of(model) == [node(content)]
    assert blocks_of(content) == [model]


def test_a_callout_title_is_a_bold_first_line():
    assert blocks_of("> [!note] Be careful\n> body") == [
        Block("callout", [text("Be careful", bold=True), ("br",), text("body")], attrs={"color": "blue_bg"})]
    assert nodes_of(*blocks_of("> [!note] Be careful\n> body")) == [node("> [!note]\n> **Be careful**\n> body")]


@pytest.mark.parametrize("kind", ["note", "info", "tip", "warning", "danger", "important"])
def test_a_callouts_type_survives_as_its_colour(kind):
    assert blocks_of(f"> [!{kind}] 💡\n> x")[0].attrs["color"] == callout_color(kind)
    assert nodes_of(*blocks_of(f"> [!{kind}] 💡\n> x")) == [node(f"> [!{kind}] 💡\n> x")]


@pytest.mark.parametrize("marker, color", [("[!WARNING]", "yellow_bg"), ("[!note]-", "blue_bg"),
                                           ("[!note]+", "blue_bg")])
def test_a_callout_marker_reads_as_the_editor_reads_it(marker, color):
    assert blocks_of(f"> {marker} 💡\n> x")[0].attrs == {"color": color, "icon": "💡"}


@pytest.mark.parametrize("kind, into", [("quote", "info"), ("abstract", "info"), ("nosuch", "info")])
def test_other_callout_types_share_gray(kind, into):
    assert nodes_of(*blocks_of(f"> [!{kind}]\n> x")) == [node(f"> [!{into}]\n> x")]


@pytest.mark.parametrize("color", ["blue_bg", "blue_background"])
def test_a_callouts_colour_is_named_either_way(color):
    assert nodes_of(Block("callout", [text("x")], attrs={"color": color})) == [node("> [!note]\n> x")]


def test_a_notion_colour_with_no_type_is_info():
    assert nodes_of(Block("callout", [text("x")], attrs={"color": "gray_background"})) == [node("> [!info]\n> x")]
    assert nodes_of(Block("callout", [text("x")])) == [node("> [!info]\n> x")]


def test_a_block_colour_goes_on_its_runs():
    block = Block("paragraph", [text("a"), text("b", color="blue_bg")], attrs={"color": "red"})
    assert nodes_of(block) == [node('<span style="color:#e5484d">a</span>'
                                    '<span style="color:#e5484d; background:#3b82f655">b</span>')]
    assert nodes_of(Block("code", attrs={"text": "x", "color": "red"})) == [node("```\nx\n```")]


def test_an_unknown_block_keeps_its_raw_text_literal():
    assert nodes_of(Block("unknown", attrs={"raw": "<x> $5"})) == [node("\\<x> $5")]


# --- text stays what it is --------------------------------------------------------

@pytest.mark.parametrize("literal", [
    "$5", "$5 and $6", "<tag>", "[x]", "1. Intro", "- x", "> x", "# x", "---", "`a`", "*a*", "@name", "\\", "[[id]]",
    "![[id]]", "![a](b)", "&amp;", "| a |", "==x==", "~~x~~", "a_b_c", "_a_", "<u>x</u>", "$$x$$", "http:/x",
    "-", "*", "+", "1.", "2)",
])
def test_literal_text_comes_back_literal(literal):
    assert leaves_of(nodes_of(para(literal))[0]["content"]) == [text(literal)]
    assert blocks_of(nodes_of(para(literal))[0]["content"]) == [para(literal)]


def test_a_date_is_raw_and_goes_through():
    rich = [text("on "), ("raw", "@2026-10-09", {})]
    assert nodes_of(Block("paragraph", rich)) == [node("on @2026-10-09")]
    assert blocks_of("on @2026-10-09") == [Block("paragraph", rich)]


@pytest.mark.parametrize("rich", [
    [text("a", bold=True), text(" b")],
    [text("x", bold=True, italic=True)],
    [text("x", italic=True), text(" "), text("y", bold=True)],
    [text("a"), text("b", italic=True), text("c")],
    [text("a ", bold=True), text("b", bold=True, italic=True)],
    [text("b", bold=True, italic=True), text("c", bold=True)],
    [text("see @", bold=True, italic=True)],
    [text("x", underline=True)],
    [text("x", strike=True), text(" y")],
    [text("x", color="red")],
    [text("x", color="red_bg")],
    [text("x", color="brown")],
    [text("x", color="yellow_bg")],
    [text("x", color=("red", "blue_bg"))],
    [("code", "x", {}), text(" "), ("math", "$y$", {})],
    [("code", "x", {"bold": True})],
])
def test_marks_come_back(rich):
    assert blocks_of(nodes_of(Block("paragraph", rich))[0]["content"]) == [Block("paragraph", rich)]


@pytest.mark.parametrize("rich", [
    [text("a", bold=True), text("b", bold=True, italic=True)],
    [text("a", bold=True), text("b", bold=True, italic=True), text("c")],
    [text("日本", bold=True), text("語", bold=True, italic=True)],
    [text("snake_case", bold=True, italic=True)],
])
def test_the_writer_drops_an_italic_it_has_no_delimiter_for(rich):
    """Before a closing bold "*" would close the bold, and "_" cannot touch a
    word or hold a "_". That is pinned by Zotero's notes
    (``zotero_notes._italics``), so a Notion run of this shape loses its italic
    and keeps its text and its bold: this names the loss."""
    back = blocks_of(nodes_of(Block("paragraph", rich))[0]["content"])[0].rich
    assert "".join(leaf[1] for leaf in back) == "".join(leaf[1] for leaf in rich)
    assert all("italic" not in leaf[2] for leaf in back)
    assert any("bold" in leaf[2] for leaf in back)


@pytest.mark.parametrize("url, target", [
    ("https://x.org/a b", "https://x.org/a%20b"),
    ("https://x.org/a(b)c", "https://x.org/a%28b%29c"),
    ("Other page.html", "Other%20page.html"),
    ("10.1016/S0140(20)30183-5", "10.1016/S0140%2820%2930183-5"),
])
def test_a_notion_link_goes_in_a_form_a_target_holds(url, target):
    written = nodes_of(Block("paragraph", [text("a", link=url)]))
    assert written == [node(f"[a]({target})")]
    assert blocks_of(written[0]["content"]) == [Block("paragraph", [text("a", link=target)])]


def test_an_address_as_its_own_label_stays_bare():
    url = "https://x.org/a(b)"
    assert nodes_of(Block("paragraph", [text(url, link=url)])) == [node(url)]
    assert nodes_of(*blocks_of(url)) == [node(url)]


def test_an_image_address_goes_in_a_form_a_target_holds():
    assert nodes_of(Block("image", [text("a")], attrs={"url": "x y(1).png"})) == [node("![a](x%20y%281%29.png)")]


@pytest.mark.parametrize("caption, alt", [
    ("a [b] *c*", r"a b \*c\*"),
    ("a|300", "a 300"),
    ("a|b", r"a\|b"),
    ("two\nlines", "two lines"),
])
def test_a_caption_is_what_an_alt_holds(caption, alt):
    assert nodes_of(Block("image", [text(caption)], attrs={"url": "a.png"})) == [node(f"![{alt}](a.png)")]


def test_a_line_break_in_a_caption_is_a_space():
    image = Block("image", [text("two"), ("br",), text("lines")], attrs={"url": "a.png"})
    assert nodes_of(image) == [node("![two lines](a.png)")]


@pytest.mark.parametrize("caption", ["E = mc^2", "<b>", "$5 and $6", "a\\b", "&amp;", "`x`", "- x", "1. x", "~~x~~",
                                     "==x==", "a|b", "*x*"])
def test_a_caption_comes_back(caption):
    image = Block("image", [text(caption)], attrs={"url": "a.png"})
    assert blocks_of(nodes_of(image)[0]["content"]) == [image]


def test_an_image_width_is_a_whole_number():
    assert nodes_of(Block("image", attrs={"url": "a.png", "width": 299.6})) == [node("![|300](a.png)")]


def test_an_alt_reads_as_text():
    assert blocks_of(r"![a\*b](c)") == [Block("image", [text("a*b")], attrs={"url": "c"})]
    assert blocks_of("![](c)")[0].rich == []


def test_a_legacy_width_reads_as_a_width():
    assert blocks_of("![a](b.png){:width 200}") == [Block("image", [text("a")], attrs={"url": "b.png", "width": 200})]
    assert blocks_of("`![a](b){:width 200}`")[0].rich == [("code", "![a](b){:width 200}", {})]


# --- one block's content ------------------------------------------------------------

def test_one_block_holds_several():
    assert blocks_of("p\n\n- a\n- b") == [para("p"), bullet("a"), bullet("b")]
    assert blocks_of("a\n\nb") == [para("a"), para("b")]
    assert blocks_of("# t\nbody\n---\n> q") == [heading("t", 1), para("body"), Block("divider"),
                                                Block("quote", [text("q")])]


def test_a_line_break_is_a_br():
    assert blocks_of("a\nb") == [Block("paragraph", [text("a"), ("br",), text("b")])]
    assert blocks_of("a\r\nb\rc") == [Block("paragraph", [text("a"), ("br",), text("b"), ("br",), text("c")])]


@pytest.mark.parametrize("marker, block", [
    ("-", Block("bullet")), ("*", Block("bullet")), ("+", Block("bullet")),
    ("1.", Block("number", attrs={"start": 1})), ("3)", Block("number", attrs={"start": 3})),
])
def test_a_marker_alone_is_an_empty_item(marker, block):
    assert blocks_of(marker) == [block]
    assert blocks_of(f"p\n\n{marker}") == [para("p"), block]
    assert blocks_of(f"> {marker}") == [Block("quote", [], [block])]


def test_a_marker_alone_with_more_after_it_is_text():
    assert blocks_of("-\nx") == [Block("paragraph", [text("-"), ("br",), text("x")])]


def test_an_empty_item_with_its_marker_stripped_ends_a_list():
    assert blocks_of("- a\n-") == [bullet("a"), Block("bullet")]


def test_a_bullet_ends_a_paragraph_and_a_year_does_not():
    assert blocks_of("a\n- b") == [para("a"), bullet("b")]
    assert blocks_of("a\n1. b") == [para("a"), Block("number", [text("b")], attrs={"start": 1})]
    assert blocks_of("a\n1986. b") == [Block("paragraph", [text("a"), ("br",), text("1986. b")])]
    assert blocks_of("a\n- ") == [Block("paragraph", [text("a"), ("br",), text("-")])]


def test_lists_nest_by_indent():
    assert blocks_of("- a\n  - b\n    - c\n- d") == [bullet("a", bullet("b", bullet("c"))), bullet("d")]
    assert blocks_of("1. a\n   - b\n2. c") == [Block("number", [text("a")], [bullet("b")], {"start": 1}),
                                                Block("number", [text("c")], attrs={"start": 2})]
    assert blocks_of("- a\n\tb\n") == [Block("bullet", [text("a"), ("br",), text("b")])]


def test_a_list_item_ends_where_its_indent_does():
    assert blocks_of("- a\n\n  b\n\nc") == [bullet("a", para("b")), para("c")]
    assert blocks_of("- a\nlazy") == [Block("bullet", [text("a"), ("br",), text("lazy")])]
    assert blocks_of("- a\n\nb") == [bullet("a"), para("b")]


def test_star_and_plus_are_bullets():
    assert blocks_of("* a\n+ b") == [bullet("a"), bullet("b")]


def test_a_bullet_with_no_text_wraps_what_it_holds():
    assert blocks_of("- - x") == [bullet("x")]
    assert blocks_of("- # H") == [heading("H", 1)]
    assert blocks_of("- ```py\n  x = 1\n\n  - y\n  ```\n- next") == [
        Block("code", attrs={"text": "x = 1\n\n- y", "language": "py"}), bullet("next")]


def test_a_number_around_a_fence_keeps_its_number():
    assert blocks_of("1. ```py\n   x\n   ```") == [
        Block("number", [], [Block("code", attrs={"text": "x", "language": "py"})], {"start": 1})]


@pytest.mark.parametrize("content, attrs, rich", [
    ("- [ ] x", {"checked": False}, [text("x")]),
    ("- [x] x", {"checked": True}, [text("x")]),
    ("- [X]  x", {"checked": True}, [text("x")]),
    ("- [ ]", {"checked": False}, []),
    ("* [ ] x", {"checked": False}, [text("x")]),
])
def test_to_dos(content, attrs, rich):
    assert blocks_of(content) == [Block("todo", rich, attrs=attrs)]


def test_a_box_in_a_number_is_text():
    assert blocks_of("1. [x] a")[0].type == "number"


def test_a_fence_in_a_list_stays_a_fence_and_lines_in_it_stay_lines():
    assert blocks_of("- a\n  ```\n  - b\n  ```\n") == [
        Block("bullet", [text("a")], [Block("code", attrs={"text": "- b"})])]
    assert blocks_of("- a\n  $$\n  x\n  $$") == [Block("bullet", [text("a")], [Block("equation", attrs={"text": "x"})])]


@pytest.mark.parametrize("line", ["\tx   ", "  x\t", "a\tb  "])
def test_a_fence_in_a_list_keeps_its_lines_as_they_are(line):
    code = [Block("code", attrs={"text": line})]
    assert blocks_of(f"- a\n  ```\n  {line}\n  ```") == [Block("bullet", [text("a")], code)]
    assert blocks_of(f"1. a\n   ```\n   {line}\n   ```")[0].children == code
    assert blocks_of(f"10. a\n\t```\n\t{line}\n\t```")[0].children == code


def test_a_tab_of_indent_past_a_lists_text_is_spaces_left_over():
    assert blocks_of("- a\n\tb")[0].rich == [text("a"), ("br",), text("b")]
    assert blocks_of("- a\n  ```\n\tx\n  ```")[0].children == [Block("code", attrs={"text": "  x"})]


def test_a_fence_keeps_its_language_and_a_tilde_fence_is_one():
    assert blocks_of("~~~js title\nx\n~~~") == [Block("code", attrs={"text": "x", "language": "js"})]


@pytest.mark.parametrize("info", ["plain text", "Plain Text"])
def test_plain_text_is_no_language(info):
    assert blocks_of(f"```{info}\nx\n```") == [Block("code", attrs={"text": "x"})]


@pytest.mark.parametrize("content", ["$$x$$", "$$\nx\n$$", "$$ x $$"])
def test_an_equation_is_its_tex(content):
    assert blocks_of(content) == [Block("equation", attrs={"text": "x"})]


def test_an_image_splits_its_paragraph_into_three():
    assert blocks_of("before ![a|200](b.png) after") == [
        para("before"), Block("image", [text("a")], attrs={"url": "b.png", "width": 200}), para("after")]
    assert blocks_of("![a](b)\n![c](d)") == [Block("image", [text("a")], attrs={"url": "b"}),
                                             Block("image", [text("c")], attrs={"url": "d"})]


def test_an_image_in_an_item_or_a_quote_is_a_block_there():
    image = Block("image", [text("a")], attrs={"url": "b"})
    assert blocks_of("- ![a](b)") == [image]
    assert blocks_of("> x\n>\n> ![a](b)") == [Block("quote", [text("x")], [image])]


def test_an_image_in_a_table_cell_or_a_link_is_a_link():
    assert blocks_of("| ![a](b) |\n| --- |")[0].attrs["rows"] == [[[text("a", link="b")]]]
    assert blocks_of("[![a](b)](c)") == [Block("paragraph", [text("a", link="c", image_lost=True)])]


def test_an_image_in_code_is_code():
    assert blocks_of("`![a](b)`") == [Block("paragraph", [("code", "![a](b)", {})])]


def test_only_a_whole_paragraph_is_a_page_or_a_file():
    assert blocks_of("[[abc]]") == [Block("page", attrs={"ref": "abc"})]
    assert blocks_of("see [[abc]]") == [Block("paragraph", [text("see "), ("raw", "[[abc]]", {})])]
    assert blocks_of("- [[abc]]") == [Block("bullet", [("raw", "[[abc]]", {})])]
    assert blocks_of(f"[n]({UPLOAD}) x") == [Block("paragraph", [text("n", link=UPLOAD), text(" x")])]


def test_a_link_elsewhere_is_no_file():
    assert blocks_of("[n](https://x.org/a.pdf)") == [Block("paragraph", [text("n", link="https://x.org/a.pdf")])]


def test_empty_text_holds_no_block():
    assert blocks_of("") == [] and blocks_of("  \n\n ") == [] and blocks_of(None) == []


def test_a_quote_opens_with_what_it_holds():
    assert blocks_of("> - a\n> - b") == [Block("quote", [], [bullet("a"), bullet("b")])]
    assert nodes_of(*blocks_of("> - a\n> - b")) == [node("> - a\n> - b")]
    assert blocks_of("> \\[!note] x") == [Block("quote", [text("[!note] x")])]


def test_a_fence_in_a_list_in_a_quote_leaves_its_indent_out_of_the_code():
    item = blocks_of("> - item\n>\n>     ```py\n>     code\n>       more\n>\n>     ```")[0].children[0]
    assert item.children == [Block("code", attrs={"text": "code\n  more\n", "language": "py"})]


def test_a_nested_quote_is_a_child():
    assert blocks_of("> a\n> > b") == [Block("quote", [text("a")], [Block("quote", [text("b")])])]


def test_deep_nesting_in_one_block_is_read_as_text():
    start = time.perf_counter()
    for content in ("- " * 50000 + "x", "> " * 50000 + "x", "\n".join(" " * (i * 2) + "- a" for i in range(400))):
        assert blocks_of(content)
    assert time.perf_counter() - start < 10


def test_a_text_too_long_for_its_marks_stays_as_written():
    long = "**a " * 6000
    assert [leaf[:2] for leaf in leaves_of(long)] == [("text", long)]
    assert leaves_of("**a** " + "x" * 19000)[0] == ("text", "a", {"bold": True})


@pytest.mark.parametrize("delimiter", ["**a ", "~~a ", "==a ", "__a ", "<u>", "[", "![", "[[", "![](x y", "](x y",
                                       "[a](x y", "\\(", "\\(a"])
def test_a_block_of_unclosed_delimiters_stalls_nothing(delimiter):
    start = time.perf_counter()
    blocks_of(delimiter * (200000 // len(delimiter)))    # ops.MAX_CONTENT
    assert time.perf_counter() - start < 10


def test_a_bound_leaves_what_real_text_holds():
    assert leaves_of('[a](x "a title") \\(x\\)') == [
        ("text", "a", {"link": "x"}), ("text", " ", {}), ("math", "$x$", {})]


def test_what_a_long_text_holds_still_comes_out():
    long = "x " * 11000
    assert [leaf[:2] for leaf in leaves_of(f"`**code**` {long}[[ref]]")] == [
        ("code", "**code**"), ("text", f" {long}"), ("raw", "[[ref]]")]


def test_a_link_in_a_long_text_stays_a_link():
    leaves = leaves_of("x" * 20001 + " [label](https://a.com/b) and [paper](/api/uploads/a.pdf) [](c) ")
    assert leaves[1:] == [("text", "label", {"link": "https://a.com/b"}), text(" and "),
                          ("text", "paper", {"link": "/api/uploads/a.pdf"}), text(" "), ("text", "c", {"link": "c"}),
                          text(" ")]


def test_a_long_text_of_links_warns_of_nothing():
    links = Links()
    from_gamma(tree(node("x" * 20001 + " [label](https://a.com/b)")), links)
    assert links.warned == []


def test_a_page_warns_of_a_text_too_long_for_its_marks():
    links = Links()
    long = "**a " * 6000
    assert from_gamma(tree(node(long)), links) == [para(long.rstrip())]
    assert links.warned == [("Long text", "over 20,000 characters, so its marks stay as typed")]
    links = Links()
    from_gamma(tree(node("**a** b")), links)
    assert links.warned == []


# --- children ------------------------------------------------------------------------

@pytest.mark.parametrize("content, type_", [
    ("x", "paragraph"), ("- x", "bullet"), ("1. x", "number"), ("- [ ] x", "todo"), ("> x", "quote"),
    ("> [!note]\n> x", "callout"),
])
def test_children_go_under_what_can_hold_them(content, type_):
    blocks = model_of(node(content, node("kid")))
    assert len(blocks) == 1 and blocks[0].type == type_


@pytest.mark.parametrize("content, type_", [
    ("```\nx\n```", "code"), ("$$x$$", "equation"), ("---", "divider"), ("| a |\n| --- |", "table"),
    ("![a](b)", "image"), (f"[n]({UPLOAD})", "file"), ("# x", "heading"), ("[[B]]", "page"),
])
def test_children_of_what_cannot_hold_them_follow_it(content, type_):
    blocks = model_of(node(content, node("kid")), page_id="A")
    assert [b.type for b in blocks] == [type_, "paragraph"] and blocks[0].children == []


def test_children_go_to_the_last_element_of_a_block_of_several():
    assert model_of(node("p\n\n- a\n- b", node("kid"))) == [para("p"), bullet("a"), bullet("b", para("kid"))]
    assert model_of(node("- a\n\n$$x$$", node("kid"))) == [
        bullet("a"), Block("equation", attrs={"text": "x"}), para("kid")]


def test_they_follow_a_nested_lists_outer_item():
    assert model_of(node("- a\n  - b", node("kid"))) == [bullet("a", bullet("b"), para("kid"))]


def test_a_block_with_nothing_to_show_gives_up_its_place():
    assert model_of(node("", node("kid"))) == [para("kid")]


def test_ids_go_on_a_blocks_first_element():
    blocks = from_gamma(tree({**node("p\n\nq"), "id": "b1"}), Links())
    assert [b.id for b in blocks] == ["b1", None]


# --- the toggle rule -----------------------------------------------------------------

def test_a_folded_block_with_children_is_a_toggle():
    assert model_of(node("x", node("kid"), collapsed=True)) == [Block("toggle", [text("x")], [para("kid")])]
    assert model_of(node("## x", node("kid"), collapsed=True)) == [TOGGLE_HEADING]


def test_an_open_block_with_children_is_no_toggle():
    assert model_of(node("x", node("kid"))) == [para("x", para("kid"))]


def test_a_folded_block_with_no_children_is_no_toggle():
    assert model_of(node("x", collapsed=True)) == [para("x")]
    assert model_of(node("## x", collapsed=True)) == [heading("x")]
    assert model_of(node("x", node(""), collapsed=True)) == [para("x")]


@pytest.mark.parametrize("content, type_", [("- x", "bullet"), ("1. x", "number"), ("- [ ] x", "todo"),
                                            ("> x", "quote")])
def test_a_folded_list_item_stays_a_list_item(content, type_):
    blocks = model_of(node(content, node("kid"), collapsed=True))
    assert blocks[0].type == type_ and blocks[0].children == [para("kid")]


def test_the_last_element_of_a_folded_block_is_the_toggle():
    assert model_of(node("a\n\nb", node("kid"), collapsed=True)) == [
        para("a"), Block("toggle", [text("b")], [para("kid")])]


def test_a_folded_code_block_hoists():
    assert [b.type for b in model_of(node("```\nx\n```", node("kid"), collapsed=True))] == ["code", "paragraph"]


# --- kinds ------------------------------------------------------------------------------

def highlight(content, *children, **props):
    return {"content": content, "properties": {"pdf_position": position(), **props}, "children": list(children)}


def test_a_highlight_is_a_quote_with_its_page_then_its_text():
    assert model_of(highlight("mine", quote="a *b*\nc")) == [
        Block("quote", [text("a *b*"), ("br",), text("c"), ("br",), text("p. 3")]), para("mine")]


def test_a_highlight_with_no_text():
    assert model_of(highlight("", quote="q")) == [Block("quote", [text("q"), ("br",), text("p. 3")])]


def test_a_highlights_children_go_under_its_last_block():
    assert model_of(highlight("", node("kid"), quote="q")) == [
        Block("quote", [text("q"), ("br",), text("p. 3")], [para("kid")])]
    assert model_of(highlight("mine", node("kid"), quote="q"))[1] == para("mine", para("kid"))


def test_a_highlight_with_no_quote_is_its_writing():
    assert model_of(highlight("mine")) == [para("mine")]


def test_a_highlight_is_no_toggle():
    blocks = model_of(highlight("mine", node("kid"), quote="q", collapsed=True))
    assert [b.type for b in blocks] == ["quote", "paragraph"]


def test_with_highlights_off_the_writing_stays():
    assert from_gamma(walk({"children": [highlight("mine", quote="q")]}, highlights=False), Links()) == [para("mine")]


def region(content="", **props):
    return {"content": content, "properties": {"pdf_position": position(), **props}, "children": []}


@pytest.mark.parametrize("props, content, rich", [
    ({"link_url": "https://x.org"}, "see *this*", [text("see ", link="https://x.org"),
                                                   text("this", italic=True, link="https://x.org")]),
    ({"link_url": "https://x.org", "quote": "q*"}, "", [text("q*", link="https://x.org")]),
    ({"link_url": "https://x.org"}, "", [text("https://x.org", link="https://x.org")]),
    ({"link_page_id": "B"}, "", [text("Page B", link="B.md")]),
    ({"link_page_id": "B", "link_url": "https://x.org"}, "there", [text("there", link="B.md")]),
    ({"link_page_id": "C", "link_url": "https://x.org"}, "there", [text("there", link="https://x.org")]),
])
def test_a_region_is_a_linked_line(props, content, rich):
    assert model_of(region(content, **props)) == [Block("paragraph", rich)]


def test_a_region_with_no_target_left_is_a_highlight():
    assert model_of(region("", link_page_id="C", quote="q")) == [Block("quote", [text("q"), ("br",), text("p. 3")])]


def linked_out(content, *children, **props):
    return {"content": content, "properties": {"link_page_id": "C", **props}, "children": list(children)}


def test_a_picture_whose_target_has_gone_stays_a_picture():
    picture_at = {"picture": {"url": "/api/uploads/ab.png"}, "pdf_page": 4}
    assert model_of(linked_out("cap", **picture_at), page_id="A") == [
        Block("image", [text("cap")], attrs={"url": "/api/uploads/ab.png"}), para("p. 4")]


def test_ink_whose_target_has_gone_stays_ink():
    assert model_of(linked_out("", ink_url="/api/uploads/ab.ink"), page_id="A") == [
        Block("image", [text("Handwriting")], attrs={"url": "/api/uploads/ab.svg"})]


def test_a_recording_whose_target_has_gone_stays_a_recording():
    recording = {"dur": 1000, "segments": [{"url": "/api/uploads/ab.webm", "dur": 1000}]}
    assert [b.type for b in model_of(linked_out("talk", recording=recording), page_id="A")] == [
        "paragraph", "file", "paragraph"]


def test_a_note_whose_target_has_gone_stays_a_note():
    assert model_of(linked_out("x", node("kid"), collapsed=True), page_id="A") == [
        Block("toggle", [text("x")], [para("kid")])]
    assert model_of(linked_out("mine", text_box={"x": 1}, pdf_page=5), page_id="A") == [
        Block("paragraph", [text("mine"), ("br",), text("p. 5")])]


def test_a_region_whose_target_has_gone_and_that_holds_nothing_gives_up_its_place():
    assert model_of(linked_out("", node("kid"), pdf_position=position()), page_id="A") == [para("kid")]


def test_ink_is_an_image_with_its_caption_under_it():
    props = {"ink_url": "/api/uploads/ab.ink", "pdf_position": position(2)}
    assert model_of({**node("cap", node("kid")), "properties": props}) == [
        Block("image", [text("Handwriting (p. 2)")], attrs={"url": "/api/uploads/ab.svg"}), para("cap", para("kid"))]
    assert model_of({**node(""), "properties": {"ink_url": "/api/uploads/ab.ink"}}) == [
        Block("image", [text("Handwriting")], attrs={"url": "/api/uploads/ab.svg"})]


def recording_node(*names, content="", children=()):
    segments = [{"url": f"/api/uploads/ab{name}.webm", "dur": 1000} for name in names]
    return {"content": content, "properties": {"recording": {"dur": 61000, "segments": segments}},
            "children": list(children)}


def test_a_recording_is_a_bold_line_and_its_audio():
    assert model_of(recording_node("1", content="talk", children=[node("kid")])) == [
        Block("paragraph", [text("Recording · 1:01", bold=True)]),
        Block("file", [text("audio")], attrs={"url": "/api/uploads/ab1.webm", "media": "audio"}),
        para("talk", para("kid"))]


def test_a_recording_in_parts_names_them():
    blocks = model_of(recording_node("1", "2"))
    assert [(b.type, b.rich) for b in blocks[1:]] == [("file", [text("part 1")]), ("file", [text("part 2")])]


def test_a_transcript_with_no_title_follows_the_audio():
    assert [b.type for b in model_of(recording_node("1", children=[node("kid")]))] == ["paragraph", "file", "paragraph"]


def picture(page=None, content="cap"):
    props = {"picture": {"url": "/api/uploads/ab.png"}}
    if page:
        props["pdf_page"] = page
    return {"content": content, "properties": props, "children": [node("kid")]}


def test_a_picture_is_an_image_and_its_page_with_its_children_after():
    assert model_of(picture(4)) == [Block("image", [text("cap")], attrs={"url": "/api/uploads/ab.png"}),
                                    para("p. 4"), para("kid")]


def test_a_picture_on_no_page():
    assert [b.type for b in model_of(picture())] == ["image", "paragraph"]


def box(content, *children):
    return {"content": content, "properties": {"text_box": {"x": 1}, "pdf_page": 5}, "children": list(children)}


def test_a_note_on_a_page_ends_with_its_page():
    assert model_of(box("mine", node("kid"))) == [
        Block("paragraph", [text("mine"), ("br",), text("p. 5")], [para("kid")])]


def test_a_note_ending_in_no_paragraph_gets_a_line_for_its_page():
    assert model_of(box("- a")) == [bullet("a"), para("p. 5")]


# --- links to uploads in text -------------------------------------------------------

def rich_of(*nodes, links):
    return [b.rich for b in from_gamma(tree(*nodes), links)]


@pytest.mark.parametrize("content, kept", [
    (f"see [paper]({UPLOAD}) now", [text("see "), text("paper"), text(" now")]),
    (f"- [paper]({UPLOAD})", [text("paper")]),
    (f"> [paper]({UPLOAD})", [text("paper")]),
    (f"**[paper]({UPLOAD})**", [text("paper", bold=True)]),
])
def test_a_link_to_a_file_in_text_is_its_words_with_a_warning(content, kept):
    links = Links()
    assert rich_of(node(content), links=links) == [kept]
    assert links.asked == [(UPLOAD, "file")] and links.warned == [("paper", "file left out")]


def test_a_link_to_a_file_in_a_cell_is_its_words_with_a_warning():
    links = Links()
    rows = from_gamma(tree(node(f"| [n]({UPLOAD}) |\n| --- |")), links)[0].attrs["rows"]
    assert rows == [[[text("n")]]] and links.warned == [("n", "file left out")]


def test_an_image_in_a_cell_is_a_link_to_its_bundled_file():
    links = Links()
    rows = from_gamma(tree(node("| ![a](/api/uploads/c.png) |\n| --- |")), links)[0].attrs["rows"]
    assert rows == [[[text("a", link="bundle/c.png")]]]
    assert links.asked == [("/api/uploads/c.png", "image")] and links.warned == []


def test_an_image_in_a_cell_left_out_is_its_alt_with_a_warning():
    links = Links(images=None)
    rows = from_gamma(tree(node("| ![a](/api/uploads/c.PNG) |\n| --- |")), links)[0].attrs["rows"]
    assert rows == [[[text("a")]]] and links.warned == [("a", "image left out")]


def test_a_web_address_of_an_upload_stays_a_link_with_a_warning():
    url = "https://x.org/api/uploads/ab.pdf"
    links = Links()
    assert rich_of(node(f"see [n]({url})"), links=links) == [[text("see "), text("n", link=url)]]
    assert links.warned == [("n", "file left out")]


def test_a_link_to_an_upload_keeps_its_other_marks_and_asks_once():
    links = Links()
    assert rich_of(node(f"[**a** b]({UPLOAD}) [c]({UPLOAD})"), links=links) == [
        [text("a", bold=True), text(" b"), text(" "), text("c")]]
    assert links.asked == [(UPLOAD, "file")] and len(links.warned) == 1


def test_each_page_answers_for_its_own_uploads():
    links = Links()
    for page_id in ("A", "B"):
        from_gamma(tree(node(f"see [n]({UPLOAD})")), links, page_id)
    assert len(links.warned) == 2


def test_a_region_linking_to_an_upload_is_settled_too():
    links = Links()
    assert rich_of(region("n", link_url=UPLOAD), links=links) == [[text("n")]]
    assert links.warned == [("n", "file left out")]


def test_an_upload_in_an_embed_is_settled_too():
    links = Links()
    links.REFS = {**Links.REFS, "up": {"content": f"[n]({UPLOAD}) x", "page_title": "Page C", "page_id": "A"}}
    assert rich_of(node("see ![[up]]"), links=links)[0][:2] == [text("see "), text("n")]
    assert links.warned == [("n", "file left out")]


def test_a_whole_paragraph_is_still_a_file_for_the_writer():
    links = Links()
    assert [b.type for b in from_gamma(tree(node(f"[n]({UPLOAD})")), links)] == ["file"]
    assert links.asked == [] and links.warned == []


def test_other_links_are_not_asked_about():
    links = Links()
    assert rich_of(node("[a](https://x.org/a.pdf)"), links=links) == [[text("a", link="https://x.org/a.pdf")]]
    assert links.asked == []


# --- mentions and embeds ----------------------------------------------------------

def test_a_mention_links_to_its_page():
    assert model_of(node("see [[B]] now"), page_id="A") == [
        Block("paragraph", [text("see "), text("Page B", link="B.md"), text(" now")])]


def test_a_block_mention_is_a_link_to_its_page():
    assert model_of(node("see [[a1]]"), page_id="B") == [
        Block("paragraph", [text("see "), text("Block one", link="A.md")])]


def test_a_mention_outside_the_export_is_its_label():
    assert model_of(node("see [[C]]"), page_id="A") == [Block("paragraph", [text("see "), text("Page C")])]


def test_a_mention_of_this_page_links_nowhere():
    assert model_of(node("see [[A]]"), page_id="A") == [Block("paragraph", [text("see "), text("Page A")])]


def test_a_ref_no_one_knows_stays_as_typed():
    assert model_of(node("see [[nosuch]] and [[two words]]")) == [
        Block("paragraph", [text("see "), text("[[nosuch]]"), text(" and "), text("[[two words]]")])]
    assert model_of(node("[[nosuch]]")) == [para("[[nosuch]]")]


def test_a_mention_keeps_the_marks_around_it():
    assert model_of(node("**[[B]]**"), page_id="A") == [Block("paragraph", [text("Page B", bold=True, link="B.md")])]


def test_a_line_that_is_a_mention_of_a_page_outside_is_its_title():
    assert model_of(node("[[C]]"), page_id="A") == [para("Page C")]


def test_a_mention_in_a_list_a_quote_and_a_table_resolves():
    blocks = model_of(node("- [[B]]"), node("> [[B]]"), node("| [[B]] |\n| --- |"), page_id="A")
    link = [text("Page B", link="B.md")]
    assert [blocks[0].rich, blocks[1].rich, blocks[2].attrs["rows"][0][0]] == [link, link, link]


def test_a_mention_of_a_person_or_a_date_stays():
    assert model_of(node("@tim on @2026-10-09")) == [
        Block("paragraph", [("raw", "@tim", {}), text(" on "), ("raw", "@2026-10-09", {})])]


def test_refs_in_code_stay():
    assert model_of(node("`[[B]]`\n\n```\n[[B]]\n```")) == [
        Block("paragraph", [("code", "[[B]]", {})]), Block("code", attrs={"text": "[[B]]"})]


def test_an_embed_in_text_is_what_it_names_with_where_from():
    assert model_of(node("see ![[a1]]"), page_id="B") == [Block("paragraph", [
        text("see "), text("Block", bold=True), text(" one"), ("br",), text("line two"), text(" "),
        text("(from ", italic=True), text("Page A", italic=True, link="A.md"), text(")", italic=True)])]


def test_a_line_that_is_an_embed_is_the_blocks_it_names():
    assert model_of(node("![[list]]"), page_id="A") == [
        bullet("x"), bullet("y"), Block("paragraph", [text("(from ", italic=True),
                                                      text("Page B", italic=True, link="B.md"),
                                                      text(")", italic=True)])]
    assert model_of(node("![[math]]"), page_id="B") == [Block("equation", attrs={"text": "x"})]


def test_an_embed_of_a_paragraph_carries_where_from_on_its_line():
    assert model_of(node("![[out]]"), page_id="A") == [
        Block("paragraph", [text("text"), text(" "), text("(from Page C)", italic=True)])]


def test_an_embed_from_this_page_says_nothing():
    assert model_of(node("![[self]]"), page_id="A") == [para("mine")]


def test_a_fence_in_an_embed_stays_a_fence():
    blocks = model_of(node("![[fence]]"), page_id="A")
    assert blocks[0] == Block("code", attrs={"text": "[[B]]"})


def test_an_embed_in_an_embed_is_a_mention():
    assert model_of(node("![[loop]]"), page_id="B") == [Block("paragraph", [text("see "), text("see")])]


def test_an_embed_of_nothing_is_a_mention():
    assert model_of(node("x ![[empty]]"), page_id="A") == [Block("paragraph", [text("x "), text("Page B", link="B.md")])]


def test_an_embed_no_one_knows_stays_as_typed():
    assert model_of(node("![[nosuch]]")) == [para("![[nosuch]]")]


@pytest.mark.parametrize("ref, label", [
    ("code", "Page B"), ("image", "Page B"), ("orphan", "Untitled"), ("item", "item"), ("number", "second"),
    ("task", "done"), ("numbered", "1. Intro"), ("math", "Page B"), ("ref", "Page B"), ("item_ref", "Page B"),
    ("ref_in_words", "see here"), ("rule", "Page B"), ("nested", "nested"), ("quoted", "quoted"), ("callout", "Title"),
])
def test_a_mention_shows_words_and_never_an_id(ref, label):
    assert model_of(node(f"see [[{ref}]]"), page_id="A")[0].rich[-1][1] == label
    assert model_of(node(f"[[{ref}]]"), page_id="A")[0].rich[0][1] == label


def test_a_mention_of_a_long_line_stalls_nothing():
    start = time.perf_counter()
    assert model_of(node("[[long]]"), page_id="A")[0].rich[0][1].startswith("**a **a")
    assert time.perf_counter() - start < 2


def test_children_go_under_an_embeds_last_block():
    assert model_of(node("![[list]]", node("kid")), page_id="B")[-1] == bullet("y", para("kid"))
