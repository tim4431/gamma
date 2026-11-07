"""Notion-flavored Markdown (gamma/notion_nfm.py): what the writer puts out, what
the reader takes in, and each through the other. The spec is Notion's
(developers.notion.com/guides/data-apis/notion-flavored-markdown, its rules
quoted in docs/research/notion-roundtrip.md); nothing here has run against Notion."""

import random
import re
import time

import pytest

from gamma.notion_blocks import CAPS, Block, lower_for_file
from gamma.notion_gamma import blocks_of, leaves_of
from gamma.notion_nfm import inline, read, write

BOLD, ITALIC = {"bold": True}, {"italic": True}
LINK = {"link": "https://x.org/a"}


class FakeLinks:
    """A ``Links``: every ref a page but ``gone``, every file where it was."""

    def __init__(self, pages=None, assets=True):
        self.pages = pages or {}
        self.assets = assets
        self.warnings = []

    def page(self, ref):
        return self.pages.get(ref)

    def asset(self, url, kind):
        return url if self.assets else None

    def warn(self, title, reason):
        self.warnings.append((title, reason))


def nfm(*blocks, links=None) -> str:
    return write(list(blocks), links or FakeLinks())


def para(*leaves) -> Block:
    return Block("paragraph", list(leaves))


def text(s: str, marks=None) -> tuple:
    return ("text", s, marks or {})


def plain(s: str) -> list:
    return [text(s)]


# The tags the spec allows, for a page Gamma writes.
ALLOWED = {"b", "i", "s", "u", "code", "span", "mention", "date", "br", "p", "h1", "h2", "h3", "h4", "quote",
           "toggle", "callout", "ul", "ol", "li", "todo", "table", "tr", "td", "th", "page", "database",
           "image", "pdf", "file", "audio", "video"}
_TAG_RE = re.compile(r"(?<!\\)</?([a-zA-Z][\w-]*)[^<>]*>")


def assert_valid(page: str):
    """Fails on what Notion saves as text or refuses: a tag outside the allowed
    set, an image inside a line of text, one written with "$$" on a line with
    its formula, or a heading whose text CommonMark would cut as a closing
    sequence."""
    outside_code, fence = [], ""
    for line in page.split("\n"):
        stripped = line.strip()
        if fence:
            fence = "" if stripped and set(stripped) == {"`"} and len(stripped) >= len(fence) else fence
            continue
        if m := re.match(r"`{3,}", stripped):
            fence = m.group()
            continue
        outside_code.append(re.sub(r"<code>.*?</code>", " ", line))
    for line in outside_code:
        for tag in _TAG_RE.findall(line):
            assert tag in ALLOWED, f"<{tag}> in {line!r}"
        for image in re.finditer(r"(?<!\\)!\[", line):
            assert not line[:image.start()].strip(" \t"), f"an image inside text: {line!r}"
            assert line.rstrip().endswith(")"), f"an image inside text: {line!r}"
        assert not re.fullmatch(r"\s*\$\$.+\$\$\s*", line), f"same-line $$: {line!r}"
        assert not re.fullmatch(r"#{1,4} (.*[ \t])?#+", line), f"a closing sequence: {line!r}"


def round_trip(*blocks):
    page = write(list(blocks), FakeLinks())
    assert_valid(page)
    assert read(page) == list(blocks), page


# --- the validator ---------------------------------------------------------------

@pytest.mark.parametrize("page", ["<div>x</div>", "a <details> b", "text ![c](u) more", "$$x$$", "# a #",
                                  "## a ##", "# #"])
def test_the_validator_fails_what_notion_refuses(page):
    with pytest.raises(AssertionError):
        assert_valid(page)


@pytest.mark.parametrize("page", ["\\<div>x", "<code><div></code>", "```\n<div>\n```", "![c](u)",
                                  "!<code>c</code>[d](u) e", "# C#",
                                  "# a \\#", "# \\#"])
def test_the_validator_passes_what_is_literal(page):
    assert_valid(page)


# --- inline ----------------------------------------------------------------------

@pytest.mark.parametrize("leaves, out", [
    ([text("x", BOLD)], "<b>x</b>"),
    ([text("x", ITALIC)], "<i>x</i>"),
    ([text("x", {"strike": True})], "<s>x</s>"),
    ([text("x", {"underline": True})], "<u>x</u>"),
    ([text("中", BOLD), text("文")], "<b>中</b>文"),
    ([text(" x ", BOLD), text("y")], "<b> x </b>y"),
    ([text("x", {"color": "red"})], '<span color="red">x</span>'),
    ([text("x", {"color": "red_bg"})], '<span color="red_bg">x</span>'),
    ([text("x", LINK)], "[x](https://x.org/a)"),
    ([text("x", {**LINK, **BOLD})], "[<b>x</b>](https://x.org/a)"),
    ([text("a", BOLD), text("b", {**BOLD, **ITALIC}), text("c", BOLD)], "<b>a<i>b</i>c</b>"),
    ([("code", "a*b", {})], "<code>a*b</code>"),
    ([("code", "x", BOLD)], "<b><code>x</code></b>"),
    ([("math", "$x^2$", {})], "$x^2$"),
    ([text("a"), ("br",), text("b")], "a<br />b"),
    ([text("a\nb")], "a<br />b"),
    ([text("a", BOLD), ("br",), text("b", BOLD)], "<b>a<br />b</b>"),
])
def test_runs_are_written_in_tags(leaves, out):
    assert nfm(Block("paragraph", leaves)) == out
    assert_valid(out)


def test_a_pair_of_colours_keeps_the_text_colour():
    assert nfm(para(text("x", {"color": ("red", "blue_bg")}))) == '<span color="red">x</span>'


def test_a_formula_on_one_line_takes_single_dollars():
    assert nfm(para(("math", "$$x$$", {}))) == "$x$"
    assert nfm(para(("math", r"$a\$$", {}))) == r"$a\$$"


@pytest.mark.parametrize("s, out", [
    (r"\*x\*", r"\\\*x\\\*"),
    ("*x*", r"\*x\*"),
    ("<tag>", r"\<tag>"),
    ("</callout>", r"\</callout>"),
    ("$5 and $6", r"\$5 and \$6"),
    ("&amp;", r"\&amp;"),
    ("# x", r"\# x"),
    ("- x", r"\- x"),
    ("1. Intro", r"1\. Intro"),
    ("> x", r"\> x"),
    ("---", r"\---"),
    (":smile:", r"\:smile:"),
    ("a :b_c: d", r"a \:b_c: d"),
    ("see https://x.org/a", "see https://x.org/a"),
    ("http://x", "http://x"),
    ("http:\\/x", "http:\\\\/x"),
    ("a|b", "a|b"),
])
def test_literal_text_is_escaped_and_reads_back(s, out):
    assert nfm(para(text(s))) == out
    assert read(out) == [para(text(s))]


def test_a_pipe_is_escaped_only_in_a_cell():
    table = Block("table", attrs={"header": True, "rows": [[plain("a|b"), [("code", "c|d", {})]],
                                                           [[text("e", {"link": "https://x.org/a|b"})], plain("\\|")]]})
    page = nfm(table)
    assert page.split("\n")[0] == r"| a\|b | <code>c\|d</code> |"
    assert read(page) == [table]


def test_a_mention_is_self_closing_so_a_title_cannot_break_it():
    links = FakeLinks({"p1": 'https://www.notion.so/a"b'})
    page = nfm(para(("raw", "[[p1]]", {})), links=links)
    assert page == '<mention url="https://www.notion.so/a&quot;b" />'
    assert_valid(page)


def test_a_reference_no_page_answers_is_its_name_as_text():
    assert nfm(para(("raw", "[[nope]]", {}))) == "nope"
    assert nfm(para(("raw", "[[nope|a*b]]", {}))) == r"a\*b"


def test_a_person_is_text_and_a_date_is_a_date():
    assert nfm(para(("raw", "@ada", {}))) == "@ada"
    assert nfm(para(("raw", "@2026-10-09", {}))) == '<date start="2026-10-09" />'
    assert nfm(para(("raw", "@2026-10-09 09:30 (remind @ada)", {}))) == '<date start="2026-10-09" start-time="09:30" />'


def test_rendered_markdown_goes_out_as_it_is():
    assert nfm(para(("raw", "<mention url=\"u\" />", {}))) == '<mention url="u" />'


def test_a_closing_code_tag_in_code_goes_over_two_runs():
    block = para(("code", "a</code>b", {}))
    page = nfm(block)
    assert page.count("<code>") == 2
    assert read(page) == [block]


def test_a_label_escapes_what_would_end_it():
    block = para(text("a]b", LINK))
    assert nfm(block) == r"[a\]b](https://x.org/a)"
    assert read(nfm(block)) == [block]


# --- the reader's inline ---------------------------------------------------------

@pytest.mark.parametrize("source, leaves", [
    (r"\*x\* \$5 \<tag>", plain("*x* $5 <tag>")),
    ("&lt;tag&gt; &amp; &#65;", plain("<tag> & A")),
    ("a<br />b<br>c", [text("a"), ("br",), text("b"), ("br",), text("c")]),
    ("a\\\nb", [text("a"), ("br",), text("b")]),
    ("a  \nb", [text("a"), ("br",), text("b")]),
    ("a\nb", [text("a"), ("br",), text("b")]),
    ("<code>&lt; \\* </b></code>", [("code", "&lt; \\* </b>", {})]),
    ("<b>a <i>b</i></b>", [text("a ", BOLD), text("b", {**BOLD, **ITALIC})]),
    ('<span color="orange"><u>w</u></span>', [text("w", {"color": "orange", "underline": True})]),
    ('<span color="blue_bg">w</span>', [text("w", {"color": "blue_bg"})]),
    ('<span color="red"><span color="blue_bg">w</span></span>', [text("w", {"color": ("red", "blue_bg")})]),
    ('<span discussion-urls="a,b">w</span>', plain("w")),
    ('<span color="default">w</span>', plain("w")),
    ("$x^2$ and $y$", [("math", "$x^2$", {}), text(" and "), ("math", "$y$", {})]),
    ("$5 and $6", plain("$5 and $6")),
    ("$ x $", plain("$ x $")),
    ("[a](https://x.org/a)", [text("a", LINK)]),
    ("[a <b>b</b>](https://x.org/a)", [text("a ", LINK), text("b", {**LINK, **BOLD})]),
    ("[](https://x.org/a)", [text("https://x.org/a", LINK)]),
    ("[a](<https://x.org/a b>)", [text("a", {"link": "https://x.org/a%20b"})]),
    ("[a](https://x.org/a \"title\")", [text("a", LINK)]),
    ("[a](https://x.org/(b))", [text("a", {"link": "https://x.org/(b)"})]),
    ("x](y)", plain("x](y)")),
    ("](y)", plain("](y)")),
    (r"\[x](y)", plain("[x](y)")),
    ("[x] y", plain("[x] y")),
    ("[^https://x.org]", plain("[^https://x.org]")),
    ("[a [b] c](https://x.org/a)", [text("a [b] c", LINK)]),
    ("[a](https://x.org/?$top=1) [b](https://x.org/?$top=2)",
     [text("a", {"link": "https://x.org/?$top=1"}), text(" "), text("b", {"link": "https://x.org/?$top=2"})]),
    ("https://x.org/a", plain("https://x.org/a")),
    ("**a** *b* ~~c~~ `d` ==e== _f_", plain("**a** *b* ~~c~~ `d` ==e== _f_")),
    ("<div>a</div> <small>b</small>", plain("<div>a</div> <small>b</small>")),
    ("</b>a", plain("</b>a")),
    ("a < b > c & d", plain("a < b > c & d")),
    ("@ada", plain("@ada")),
    (":smile:", plain(":smile:")),
    ("<date start=\"2026-11-02\" />", [("raw", "@2026-11-02", {})]),
    ('<date start="2026-11-02" start-time="09:30" time-zone="America/Los_Angeles" />',
     [("raw", "@2026-11-02 09:30", {})]),
    ('<date start="2026-11-02" end="2026-11-04" />',
     [("raw", "@2026-11-02", {}), text(" → "), ("raw", "@2026-11-04", {})]),
    ('<mention url="https://www.notion.so/a">Title</mention>', [text("Title", {"link": "https://www.notion.so/a"})]),
    ('<mention url="https://www.notion.so/a" />', [text("https://www.notion.so/a", {"link": "https://www.notion.so/a"})]),
    ('<mention url="https://x.org/a">a</mention></b>', [text("a", LINK), text("</b>")]),
    ('<mention url="https://x.org/a" type="database">a *b*</mention>', [text("a *b*", LINK)]),
    ('<mention url="user://1">Ada</mention>', plain("Ada")),
    ('<mention url="user://1" />', []),
    ('<link-preview url="https://x.org/a" source="s" />', [text("https://x.org/a", LINK)]),
])
def test_what_inline_reads(source, leaves):
    assert inline(source) == leaves


@pytest.mark.parametrize("url", ["user://1", "collection://1", "agent://1", "group://1"])
def test_a_mention_with_no_name_goes_and_is_told(url):
    told = []
    assert read(f'a <mention url="{url}" /> b', lambda *warning: told.append(warning)) == [para(text("a  b"))]
    assert told == [(url, "mention with no name, dropped")]


@pytest.mark.parametrize("source", ['# <mention url="user://1" />', '| <mention url="user://1" /> |\n| --- |',
                                    '<callout><mention url="user://1" /></callout>',
                                    '<table header-row="false"><tr><td><mention url="user://1" /></td></tr></table>',
                                    '[<mention url="user://1" />x](https://x.org/a)'])
def test_every_kind_of_text_tells_of_a_mention_it_drops(source):
    told = []
    read(source, lambda *warning: told.append(warning))
    assert told == [("user://1", "mention with no name, dropped")]


def test_marks_the_spec_leaves_out_are_text_and_the_opener_marks_to_the_end():
    assert inline("<b>a") == [text("a", BOLD)]
    assert inline("<b>a<i>b</b>c") == [text("a", BOLD), text("b", {**BOLD, **ITALIC}), text("c")]


def test_an_unclosed_code_or_label_is_text():
    assert inline("<code>a") == plain("<code>a")
    assert inline('<mention url="u">a') == plain('<mention url="u">a')


def test_an_image_in_text_is_never_a_leaf():
    assert inline("a ![c](https://x.org/a) b") == [text("a "), text("c", LINK), text(" b")]


# --- blocks ----------------------------------------------------------------------

def test_a_blank_line_stands_between_blocks():
    assert nfm(para(text("a")), para(text("b"))) == "a\n\nb"


def test_an_empty_paragraph_is_p():
    assert nfm(Block("paragraph")) == "<p />"
    round_trip(para(text("a")), Block("paragraph"), para(text("b")))


def test_a_paragraph_with_children_takes_its_tag():
    block = Block("paragraph", plain("x"), [para(text("kid"))])
    assert nfm(block) == "<p>\nx\n\nkid\n</p>"
    round_trip(block)


@pytest.mark.parametrize("level, source", [(1, "# x"), (2, "## x"), (3, "### x"), (4, "#### x")])
def test_headings(level, source):
    block = Block("heading", plain("x"), attrs={"level": level})
    assert nfm(block) == source
    round_trip(block)


@pytest.mark.parametrize("level", [5, 6])
def test_below_h4_is_h4(level):
    assert nfm(Block("heading", plain("x"), attrs={"level": level})) == "#### x"


@pytest.mark.parametrize("title, source", [
    ("a #", r"# a \#"),
    ("a ##", r"# a \##"),
    ("a\t#", "# a\t\\#"),
    ("#", r"# \#"),
    ("#######", r"# \#######"),
    ("a # b", "# a # b"),
    ("C#", "# C#"),
])
def test_a_heading_keeps_a_tail_of_hashes_from_commonmark(title, source):
    block = Block("heading", plain(title), attrs={"level": 1})
    assert nfm(block) == source
    round_trip(block)


def test_a_tail_of_hashes_in_a_mark_or_a_tag_is_safe_as_it_is():
    assert nfm(Block("heading", [text("a #", BOLD)], attrs={"level": 1})) == "# <b>a #</b>"
    assert nfm(Block("heading", plain("a #"), attrs={"level": 1, "color": "red"})) == '<h1 color="red">\na #\n</h1>'


def test_a_headings_edge_nbsp_stays():
    round_trip(Block("heading", plain("a\xa0"), attrs={"level": 2}))


@pytest.mark.parametrize("leaves", [[], [text(" ")]])
def test_an_empty_heading_in_its_tag_is_not_padded(leaves):
    block = Block("heading", leaves, attrs={"level": 1, "color": "red"})
    assert nfm(block) == '<h1 color="red">\n<p />\n</h1>'
    assert read(nfm(block)) == [Block("heading", attrs={"level": 1, "color": "red"})]


def test_a_plain_heading_has_its_children_after_it():
    assert nfm(Block("heading", plain("x"), [para(text("kid"))], {"level": 2})) == "## x\n\nkid"


def test_a_toggle_and_a_toggle_heading():
    toggle = Block("toggle", plain("t"), [para(text("kid"))])
    heading = Block("toggle", plain("t"), [para(text("kid"))], {"heading": 2})
    assert nfm(toggle) == "<toggle>\nt\n\nkid\n</toggle>"
    assert nfm(heading) == '<toggle heading="h2">\nt\n\nkid\n</toggle>'
    round_trip(toggle)
    round_trip(heading)


def test_toggle_in_toggle():
    round_trip(Block("toggle", plain("a"), [Block("toggle", plain("b"), [Block("toggle", plain("c"), [para(text("d"))])]),
                                            para(text("e"))]), para(text("after")))


def test_a_toggle_with_no_title_says_so():
    block = Block("toggle", [], [para(text("kid"))])
    assert nfm(block) == "<toggle>\n<p />\n\nkid\n</toggle>"
    round_trip(block)


def test_bullets_nest_by_two_and_numbers_by_three():
    block = Block("bullet", plain("a"), [Block("number", plain("b"), [Block("bullet", plain("c"))], {"start": 1})])
    assert nfm(block) == "- a\n  1. b\n     - c"
    round_trip(block)


def test_a_long_number_nests_past_its_marker():
    block = Block("number", plain("a"), [Block("bullet", plain("b"))], {"start": 100})
    assert nfm(block) == "100. a\n     - b"
    round_trip(block)


@pytest.mark.parametrize("wide", ["\u2028", "\u2029", "\x0c", "\x0b", "\x85", "\x1c"])
def test_a_child_is_indented_on_line_feeds_only(wide):
    number = Block("number", plain(f"d{wide}e"), attrs={"start": 1})
    block = Block("bullet", plain("a"), [Block("bullet", plain(f"b{wide}c")), number])
    assert nfm(block).count("  ") == 2
    round_trip(block)


@pytest.mark.parametrize("form, indent", [("nfm", 2), ("file", 4)])
@pytest.mark.parametrize("child, line", [
    (Block("number", plain("c"), attrs={"start": 3}), "3. c"),
    (Block("number", plain("c"), attrs={"start": 0}), "0. c"),
    (Block("bullet"), "- "),
    (Block("number", attrs={"start": 1}), "1. "),
])
def test_a_list_that_cannot_interrupt_text_follows_a_blank_line(form, indent, child, line):
    """CommonMark starts a list inside a paragraph only at 1 and with text:
    anything else right under "a" would be more of "a", or turn it into a heading."""
    block = Block("bullet", plain("a"), [child])
    assert write([block], FakeLinks(), form=form) == f"- a\n\n{' ' * indent}{line}"


@pytest.mark.parametrize("form, indent", [("nfm", 2), ("file", 4)])
@pytest.mark.parametrize("child, line", [
    (Block("bullet", plain("c")), "- c"),
    (Block("number", plain("c"), attrs={"start": 1}), "1. c"),
    (Block("todo", attrs={"checked": False}), "- [ ] "),
])
def test_a_list_that_can_interrupt_text_goes_straight_after_it(form, indent, child, line):
    assert write([Block("bullet", plain("a"), [child])], FakeLinks(), form=form) == f"- a\n{' ' * indent}{line}"


def test_an_item_with_no_text_takes_no_blank_line_before_what_it_holds():
    """After a marker alone a blank line ends the item, and what is indented
    under it is then a code block."""
    assert write([Block("bullet", [], [para(text("c"))])], FakeLinks(), form="file") == "- \n    c"
    number = Block("number", plain("c"), attrs={"start": 3})
    assert nfm(Block("bullet", [], [number])) == "- \n  3. c"


def test_a_todo_s_box_is_text_a_paragraph_could_join():
    block = Block("todo", [], [para(text("c"))], {"checked": False})
    assert write([block], FakeLinks(), form="file") == "- [ ] \n\n    c"


def test_a_file_takes_no_name_from_a_data_address():
    """What follows its last "/" is a piece of the picture."""
    links = FakeLinks(assets=False)
    url = "data:image/png;base64,QUJD/SECRETTAIL"
    assert nfm(Block("image", attrs={"url": url}), links=links) == "image"
    assert nfm(Block("image", plain("a"), attrs={"url": url}), links=links) == "a"
    assert nfm(Block("file", attrs={"url": "data:application/pdf;base64,AB/CD", "media": "pdf"}), links=links) == "pdf"
    assert links.warnings == [("image", "image left out"), ("a", "image left out"), ("pdf", "pdf left out")]


def test_a_number_keeps_where_it_starts():
    assert nfm(Block("number", plain("a"), attrs={"start": 3})) == "3. a"
    round_trip(Block("number", plain("a"), attrs={"start": 1}), Block("number", plain("b"), attrs={"start": 2}))


def test_sibling_lists_stay_apart_from_the_text_after():
    round_trip(Block("bullet", plain("a")), Block("bullet", plain("b")), para(text("c")))


def test_a_bullet_holding_no_list_item_takes_ul():
    block = Block("bullet", plain("a"), [para(text("kid"))])
    assert nfm(block) == "<ul>\n<li>\na\n\nkid\n</li>\n</ul>"
    round_trip(block)


def test_a_number_in_its_tag_cannot_say_where_it_starts():
    block = Block("number", plain("a"), [para(text("kid"))], {"start": 3})
    assert read(nfm(block))[0].attrs["start"] == 1


def test_a_number_holding_no_list_item_takes_ol():
    block = Block("number", plain("a"), [para(text("kid"))], {"start": 1})
    assert nfm(block) == "<ol>\n<li>\na\n\nkid\n</li>\n</ol>"
    round_trip(block)


def test_a_grandchild_that_is_no_list_item_takes_the_tags_all_the_way_up():
    deep = Block("bullet", plain("a"), [Block("bullet", plain("b"), [Block("code", attrs={"text": "c"})])])
    assert nfm(deep).startswith("<ul>\n<li>\na\n\n<ul>\n<li>\nb\n\n```")
    round_trip(deep)


def test_todos():
    round_trip(Block("todo", plain("a"), attrs={"checked": False}), Block("todo", plain("b"), attrs={"checked": True}))
    assert nfm(Block("todo", plain("a"), attrs={"checked": True})) == "- [x] a"


def test_a_todo_nests_list_items_and_tags_the_rest():
    nested = Block("todo", plain("a"), [Block("todo", plain("b"), attrs={"checked": True})], {"checked": False})
    assert nfm(nested) == "- [ ] a\n  - [x] b"
    round_trip(nested)
    tagged = Block("todo", plain("a"), [para(text("kid"))], {"checked": True})
    assert nfm(tagged) == '<todo checked="true">\na\n\nkid\n</todo>'
    round_trip(tagged)


@pytest.mark.parametrize("block", [
    Block("paragraph", plain(" x ")),
    Block("heading", plain(" x"), attrs={"level": 2}),
    Block("quote", plain("x ")),
    Block("bullet", plain(" x")),
    Block("number", plain("x "), attrs={"start": 1}),
    Block("todo", plain(" x "), attrs={"checked": False}),
    Block("paragraph", plain("    - x")),
])
def test_edge_whitespace_takes_the_tag(block):
    page = nfm(block)
    assert page.startswith("<") and page.endswith(">")
    round_trip(block)


def test_a_blank_title_is_no_title_so_a_child_stays_a_child():
    block = Block("quote", plain(" "), [para(text("kid"))])
    assert nfm(block) == "<quote>\n<p />\n\nkid\n</quote>"
    assert read(nfm(block)) == [Block("quote", [], [para(text("kid"))])]


@pytest.mark.parametrize("blank", ["\xa0", "\u3000", "\u2003", " \xa0 "])
@pytest.mark.parametrize("kind", ["toggle", "callout", "quote"])
def test_any_blank_title_is_no_title(kind, blank):
    block = Block(kind, plain(blank), [para(text("kid")), para(text("kid2"))])
    assert read(nfm(block)) == [Block(kind, [], block.children)]


@pytest.mark.parametrize("blank", ["\xa0", "\u3000"])
def test_a_paragraph_of_nothing_but_a_wide_space_is_empty_not_gone(blank):
    assert nfm(para(text(blank))) == "<p />"
    assert read(nfm(para(text(blank)), para(text("a")))) == [para(), para(text("a"))]


def test_a_quote_and_one_with_children():
    assert nfm(Block("quote", plain("x"))) == "> x"
    round_trip(Block("quote", plain("x")))
    block = Block("quote", plain("x"), [Block("bullet", plain("kid"))])
    assert nfm(block) == "<quote>\nx\n\n- kid\n</quote>"
    round_trip(block)


def test_two_quotes_stay_two():
    round_trip(Block("quote", plain("a")), Block("quote", plain("b")))


def test_the_specs_nested_quote():
    page = ('<quote color="purple_bg">\nTitle text\n\n- child bullet\n\n'
            '<callout icon="💡" color="yellow_bg">\nCallout body child\n</callout>\n</quote>')
    assert read(page) == [Block("quote", plain("Title text"), [
        Block("bullet", plain("child bullet")),
        Block("callout", plain("Callout body child"), attrs={"icon": "💡", "color": "yellow_bg"})], {"color": "purple_bg"})]


def test_a_callout():
    block = Block("callout", plain("t"), [Block("bullet", plain("kid"))], {"icon": "💡", "color": "blue_bg"})
    assert nfm(block) == '<callout icon="💡" color="blue_bg">\nt\n\n- kid\n</callout>'
    round_trip(block)


def test_a_callouts_attributes_are_escaped():
    block = Block("callout", plain("t"), attrs={"icon": '"><b>'})
    assert '"&quot;&gt;&lt;b&gt;"' in nfm(block)
    round_trip(block)


def test_a_fence_holding_closing_tags_inside_a_callout():
    code = Block("code", attrs={"text": "</callout>\n<callout>\n</quote>"})
    block = Block("quote", plain("q"), [Block("callout", plain("t"), [code], {"icon": "💡"})])
    round_trip(block, para(text("after")))


def test_inline_code_holding_a_closing_tag_inside_a_callout():
    block = Block("callout", [("code", "</callout>", {})], [para(text("kid"))], {"icon": "💡"})
    round_trip(block, para(text("after")))


@pytest.mark.parametrize("blocks", [
    [Block("paragraph", [("math", "$x<p>y$", {})], [para(text("kid"))]), para(text("after"))],
    [Block("bullet", [("math", "$a<li>b$", {})], [para(text("kid"))]), para(text("after"))],
    [Block("callout", [("math", "$a</callout>b$", {})]), para(text("after"))],
    [Block("callout", [text("a "), ("math", "$b</callout>$", {}), text(" c")]), para(text("after"))],
    [Block("table", attrs={"header": False, "rows": [[[("math", "$a</td>b$", {})], plain("c")]]})],
    [Block("toggle", plain("t"), [Block("equation", attrs={"text": "a</toggle>b"})]), para(text("after"))],
    [Block("toggle", plain("t"), [Block("equation", attrs={"text": "<toggle>\n\n```\nb"})]), para(text("after"))],
    [Block("callout", plain("c"), [Block("equation", attrs={"text": "x"}), Block("equation", attrs={"text": "</callout>"})])],
])
def test_a_formula_holding_a_tag_name_stays_in_its_block(blocks):
    round_trip(*blocks)


def test_a_dollar_that_is_money_hides_no_closing_tag():
    assert read("<callout>$5 and </callout>$6") == [Block("callout", plain("$5 and ")), para(text("$6"))]
    assert read("<callout>\n$5 </callout>\n\nafter") == [Block("callout", plain("$5 ")), para(text("after"))]


def test_a_stray_dollar_line_cannot_take_the_page_with_it():
    assert read("<callout>\n$$\n</callout>\n\nafter") == [
        Block("callout", children=[Block("equation", attrs={"text": ""})]), para(text("after"))]


def test_escaped_closing_tag_inside_a_callout():
    round_trip(Block("callout", plain("</callout>"), attrs={"icon": "💡"}), para(text("after")))


def test_a_fence_is_longer_than_any_run_in_it():
    block = Block("code", attrs={"text": "```\n`````\n", "language": "md"})
    assert nfm(block).startswith("``````md\n")
    round_trip(block)


@pytest.mark.parametrize("language, out", [("py", "py"), ("svg", "xml"), ("SVG", "xml"), ("bibtex", ""), (None, ""),
                                           ("a b", "a b"), ("a`b", "")])
def test_a_fences_language(language, out):
    assert nfm(Block("code", attrs={"text": "x", "language": language})).split("\n")[0] == "```" + out


def test_a_fence_keeps_its_text_as_it_is():
    round_trip(Block("code", attrs={"text": "\n  a *b* <i>\n\n\\$\n"}), Block("code", attrs={"text": ""}))


def test_an_equation_goes_on_bare_lines():
    block = Block("equation", attrs={"text": "x"})
    assert nfm(block) == "$$\nx\n$$"
    round_trip(block, Block("equation", attrs={"text": "a\n\\\\\nb"}))


def test_the_divider():
    assert nfm(Block("divider")) == "---"
    round_trip(para(text("a")), Block("divider"), para(text("b")))


def test_a_pipe_table():
    block = Block("table", attrs={"header": True, "rows": [[plain("a"), plain("b")], [[text("c", BOLD)], []]]})
    assert nfm(block) == "| a | b |\n| --- | --- |\n| <b>c</b> |  |"
    round_trip(block)


def test_a_cell_holds_a_line_break():
    round_trip(Block("table", attrs={"header": True, "rows": [[[text("a"), ("br",), text("b")]]]}))


def test_short_rows_are_filled():
    block = Block("table", attrs={"header": True, "rows": [[plain("a"), plain("b")], [plain("c")]]})
    assert read(nfm(block))[0].attrs["rows"][1] == [plain("c"), []]


def test_a_table_with_no_rows_is_nothing():
    assert nfm(Block("table", attrs={"rows": []})) == ""


def test_a_table_with_no_header_row_takes_the_tag():
    block = Block("table", attrs={"header": False, "rows": [[plain("a|b"), plain("</td>")], [[], [("code", "</td>", {})]]]})
    assert nfm(block).startswith('<table header-row="false">\n<tr>\n<td>a|b</td>')
    round_trip(block)


def test_a_notion_styled_table():
    page = ('<table header-column="true">\n<colgroup><col width="180" /></colgroup>\n<tr>\n<th>\nA\n</th>\n<th>\nB\n</th>\n'
            '</tr>\n<tr color="red">\n<td color="blue">\n<b>c</b>\n</td>\n<td />\n</tr>\n</table>')
    assert read(page) == [Block("table", attrs={"header": True, "rows": [[plain("A"), plain("B")],
                                                                         [[text("c", BOLD)], []]]})]


def test_a_cells_formula_keeps_its_pipes():
    round_trip(Block("table", attrs={"header": True, "rows": [[[("math", "$|x|$", {})]]]}))


def test_an_image_is_its_own_block():
    block = Block("image", plain("a [b] c"), attrs={"url": "https://x.org/a.png"})
    assert nfm(block) == r"![a \[b\] c](https://x.org/a.png)"
    round_trip(para(text("a")), block, Block("image", attrs={"url": "https://x.org/b.png"}))


@pytest.mark.parametrize("formula", ["$[0,1)$", "$(a,b]$", "$]0,1[$"])
def test_a_formulas_brackets_are_not_a_labels(formula):
    round_trip(para(text("see ", LINK), ("math", formula, LINK), text(" after")))
    round_trip(para(("math", formula, LINK)))
    round_trip(para(text("["), ("math", formula, {}), text("]")))
    round_trip(Block("image", [("math", formula, {})], attrs={"url": "https://x.org/a.png"}))


def test_an_image_with_no_place_is_a_link_or_a_name():
    links = FakeLinks(assets=False)
    web = Block("image", plain("c"), attrs={"url": "https://x.org/a.png"})
    assert nfm(web, links=links) == "[c](https://x.org/a.png)"
    local = Block("image", attrs={"url": "/api/uploads/ab.png"})
    assert nfm(local, links=links) == "ab.png"
    assert links.warnings == [("c", "image left out"), ("ab.png", "image left out")]


@pytest.mark.parametrize("url, media, out", [
    ("https://x.org/a.pdf", None, '<pdf source="https://x.org/a.pdf" />'),
    ("https://x.org/a.PDF?x=1", None, '<pdf source="https://x.org/a.PDF?x=1" />'),
    ("https://x.org/a.pdf#page=2", None, '<pdf source="https://x.org/a.pdf#page=2" />'),
    ("https://x.org/a.mp3", None, '<audio source="https://x.org/a.mp3" />'),
    ("https://x.org/a.mp4", None, '<video source="https://x.org/a.mp4" />'),
    ("https://x.org/a.zip", None, '<file source="https://x.org/a.zip" />'),
    ("https://x.org/a", "audio", '<audio source="https://x.org/a" />'),
])
def test_a_file_goes_by_its_extension(url, media, out):
    attrs = {"url": url, **({"media": media} if media else {})}
    assert nfm(Block("file", attrs=attrs)) == out
    assert_valid(out)


def test_a_file_with_a_caption_and_one_with_no_place():
    block = Block("file", plain("c"), attrs={"url": "https://x.org/a.pdf", "media": "pdf"})
    assert nfm(block) == '<pdf source="https://x.org/a.pdf">c</pdf>'
    round_trip(block)
    links = FakeLinks(assets=False)
    assert nfm(block, links=links) == "[c](https://x.org/a.pdf)"
    assert links.warnings == [("c", "pdf left out")]


def test_a_page_keeps_the_url_from_gamma_gave_it():
    block = Block("page", plain("T"), attrs={"ref": "p1", "url": "https://www.notion.so/b"})
    assert nfm(block) == '<page url="https://www.notion.so/b">T</page>'


def test_a_page_always_has_a_url():
    block = Block("page", plain("T"), attrs={"ref": "p1"})
    assert nfm(block, links=FakeLinks({"p1": "https://www.notion.so/a"})) == '<page url="https://www.notion.so/a">T</page>'
    assert nfm(block) == "T"


def test_a_page_and_a_database_read_back():
    round_trip(Block("page", plain("T"), attrs={"url": "https://www.notion.so/a"}),
               Block("page", plain("D"), attrs={"url": "https://www.notion.so/d", "database": "https://www.notion.so/d"}))


def test_a_database_goes_by_the_id_in_its_address():
    url = "https://www.notion.so/w/Cases-0123456789abcdef0123456789abcdef?v=fedcba9876543210fedcba9876543210"
    assert read(f'<database url="{url}">C</database>')[0].attrs["database"] == "0123456789abcdef0123456789abcdef"


def test_a_title_holding_a_closing_tag_cannot_end_its_page():
    round_trip(Block("page", plain("a</page>b"), attrs={"url": "https://www.notion.so/a"}), para(text("after")))


# --- what only Notion writes -----------------------------------------------------

def test_a_block_tag_on_one_line_or_several():
    assert read("<toggle>a</toggle>") == [Block("toggle", plain("a"))]
    assert read('<callout icon="💡">\nbody\n</callout>') == [Block("callout", plain("body"), attrs={"icon": "💡"})]
    assert read("<callout>\na\n\nb\n\nc\n</callout>") == [Block("callout", plain("a"), [para(text("b")), para(text("c"))])]


def test_a_blank_line_touching_a_tag_changes_nothing():
    assert read("<toggle>\n\na\n\n\nb\n\n</toggle>") == [Block("toggle", plain("a"), [para(text("b"))])]


@pytest.mark.parametrize("block, source", [
    (Block("paragraph", plain("a"), attrs={"color": "gray_bg"}), '<p color="gray_bg">\na\n</p>'),
    (Block("heading", plain("a"), attrs={"level": 2, "color": "blue"}), '<h2 color="blue">\na\n</h2>'),
    (Block("quote", plain("a"), attrs={"color": "purple_bg"}), '<quote color="purple_bg">\na\n</quote>'),
    (Block("bullet", plain("a"), attrs={"color": "blue"}), '<ul>\n<li color="blue">\na\n</li>\n</ul>'),
    (Block("todo", plain("a"), attrs={"checked": False, "color": "green"}),
     '<todo checked="false" color="green">\na\n</todo>'),
    (Block("toggle", plain("a"), attrs={"heading": 2, "color": "red"}), '<toggle heading="h2" color="red">\na\n</toggle>'),
])
def test_a_blocks_colour_is_on_its_tag(block, source):
    assert nfm(block) == source
    round_trip(block)


def test_a_coloured_number_has_its_tag():
    block = Block("number", plain("a"), attrs={"color": "blue", "start": 1})
    assert nfm(block) == '<ol>\n<li color="blue">\na\n</li>\n</ol>'
    round_trip(block)


def test_a_colour_with_no_meaning_is_none():
    assert read('<p color="default">\na\n</p>') == [para(text("a"))]


def test_a_block_colour_under_a_list_takes_the_tags_all_the_way_up():
    block = Block("bullet", plain("a"), [Block("bullet", plain("b"), attrs={"color": "red"})])
    assert nfm(block) == '<ul>\n<li>\na\n\n<ul>\n<li color="red">\nb\n</li>\n</ul>\n</li>\n</ul>'
    round_trip(block)


def test_a_callouts_colour_reads_in_any_form():
    assert read('<callout color="red_background">a</callout>') == [Block("callout", plain("a"), attrs={"color": "red_bg"})]


def test_children_of_a_plain_heading_are_its_siblings():
    assert read("<h2>\na\n\nb\n</h2>") == [Block("heading", plain("a"), attrs={"level": 2}), para(text("b"))]


def test_a_tag_list_of_several_items():
    assert read("<ul>\n<li>\na\n</li>\n<li>\nb\n</li>\n</ul>") == [Block("bullet", plain("a")), Block("bullet", plain("b"))]
    assert read('<ol type="a">\n<li>\na\n</li>\n<li>\nb\n</li>\n</ol>') == [
        Block("number", plain("a"), attrs={"start": 1}), Block("number", plain("b"), attrs={"start": 2})]


def test_tabs_still_nest_a_list():
    assert read("- a\n\t- b\n\t\t- c") == [
        Block("bullet", plain("a"), [Block("bullet", plain("b"), [Block("bullet", plain("c"))])])]


def test_a_line_after_a_quote_joins_it():
    assert read("> a\nb\n\nc") == [Block("quote", [text("a"), ("br",), text("b")]), para(text("c"))]


def test_a_quote_of_a_quote():
    assert read("> a\n>\n> > b") == [Block("quote", plain("a"), [Block("quote", plain("b"))])]


def test_a_line_in_a_paragraph_joins_it():
    assert read("a\nb") == [para(text("a"), ("br",), text("b"))]


def test_a_block_ends_a_paragraph():
    assert read("a\n- b") == [para(text("a")), Block("bullet", plain("b"))]


def test_an_equation_tag_and_one_line_of_dollars():
    assert read('<equation color="red">x + y</equation>') == [Block("equation", attrs={"text": "x + y"})]
    assert read("$$x$$") == [Block("equation", attrs={"text": "x"})]


def test_media_tags():
    assert read('<image source="https://x.org/a.png" color="red">c</image>') == [
        Block("image", plain("c"), attrs={"url": "https://x.org/a.png"})]
    assert read('<image source="https://x.org/a.png" />') == [Block("image", attrs={"url": "https://x.org/a.png"})]
    assert read('<file source="u">\nc\n</file>') == [Block("file", plain("c"), attrs={"url": "u", "media": "file"})]
    assert read('<video source="u" />') == [Block("file", attrs={"url": "u", "media": "video"})]


def test_a_page_on_several_lines_and_a_database():
    assert read('<page url="u">\nT\n</page>') == [Block("page", plain("T"), attrs={"url": "u"})]
    assert read('<database url="u" data-source-url="c" inline="true">\nT\n</database>') == [
        Block("page", plain("T"), attrs={"url": "u", "database": "u"})]


@pytest.mark.parametrize("tag", ["bookmark", "embed"])
def test_a_bookmark_and_an_embed_are_linked_paragraphs(tag):
    assert read(f'<{tag} source="https://x.org/a" />') == [para(text("https://x.org/a", LINK))]
    assert read(f'<{tag} source="https://x.org/a">c</{tag}>') == [para(text("c", LINK))]


def test_columns_tabs_and_synced_blocks_are_transparent():
    page = ('<columns><column ratio="50">\na\n</column><column ratio="50">\nb\n</column></columns>\n\n'
            '<tabs>\n<tab icon="💡">\nc\n</tab>\n</tabs>\n\n<synced-block url="u">\nd\n</synced-block>\n\n'
            '<synced-block-reference url="u" notice="n">\ne\n</synced-block-reference>')
    assert read(page) == [para(text(s)) for s in "abcde"]


def test_a_table_of_contents_is_dropped_and_told():
    told = []
    assert read('a\n\n<table-of-contents color="gray" />\n\nb', lambda *args: told.append(args)) == [para(text("a")), para(text("b"))]
    assert told == [("table of contents", "dropped")]


@pytest.mark.parametrize("source", [
    '<meeting-notes view-url="u">T<meeting-notes-section-notes>n</meeting-notes-section-notes></meeting-notes>',
    '<custom-block definition-id="1" url="u" />',
    '<unknown url="u" alt="x"/>',
    '<folder url="u">\nT\n</folder>',
    '<mail to="a">\nT\n</mail>',
])
def test_anything_else_is_kept_and_told(source):
    told = []
    assert read(f"a\n\n{source}\n\nb", lambda *args: told.append(args)) == [
        para(text("a")), Block("unknown", attrs={"raw": source}), para(text("b"))]
    assert len(told) == 1
    assert nfm(Block("unknown", attrs={"raw": source})) == source


def test_an_unclosed_tag_runs_to_the_end():
    assert read("<toggle>\na\n\nb") == [Block("toggle", plain("a"), [para(text("b"))])]


def test_crlf_reads_as_lf():
    assert read("a\r\n\r\n- b\r\n") == [para(text("a")), Block("bullet", plain("b"))]


def test_deep_nesting_stops_and_says_so():
    told = []
    blocks = read("<quote>\n" * 300 + "x" + "\n</quote>" * 300, lambda *args: told.append(args))
    assert blocks[0].type == "quote" and told == [("nesting", "too deep, kept as text")]


def test_long_input_reads_fast():
    started = time.monotonic()
    read("[" * 50000)
    read("<code>" * 20000)
    read("<b>" * 20000)
    read("$ " * 50000)
    read("$a " * 50000)
    read("$" + "\\" * 100001)
    read("[a](b " * 20000)
    read("<toggle>\n" + "$$\n" * 20001 + "</toggle>")
    read(" " * 50000 + "x")
    read("\n\n".join("<toggle>\na\n</toggle>" for _ in range(5000)))
    assert time.monotonic() - started < 5


# --- through both ----------------------------------------------------------------

def test_a_digit_after_a_formula_ends_it_as_text():
    """"$5 and $6" in Notion's output is money, so "$x$1" reads as text too."""
    assert read(nfm(para(("math", "$x$", {}), text("1")))) == [para(text("$x$1"))]


def test_a_cell_has_no_edge_whitespace():
    block = Block("table", attrs={"header": True, "rows": [[plain(" a ")]]})
    assert read(nfm(block))[0].attrs["rows"] == [[plain("a")]]


def test_a_whole_page():
    round_trip(
        Block("heading", plain("Title"), attrs={"level": 1}),
        para(text("a "), text("b", BOLD), text(" "), text("c", {"color": "red"}), text(" "), ("math", "$x$", {}),
             text(" "), ("code", "d", {}), text(" "), text("e", LINK), ("br",), text("f")),
        Block("toggle", plain("t"), [Block("callout", plain("c"), [Block("code", attrs={"text": "x", "language": "py"})],
                                           {"icon": "💡", "color": "blue_bg"}),
                                     Block("quote", plain("q"), [Block("bullet", plain("k"))])], {"heading": 3}),
        Block("bullet", plain("a"), [Block("number", plain("b"), attrs={"start": 1}),
                                     Block("todo", plain("c"), attrs={"checked": True})]),
        Block("divider"),
        Block("equation", attrs={"text": "x^2"}),
        Block("table", attrs={"header": True, "rows": [[plain("a")], [plain("b")]]}),
        Block("image", plain("i"), attrs={"url": "https://x.org/a.png"}),
        Block("page", plain("P"), attrs={"url": "https://www.notion.so/p"}),
        Block("paragraph"),
    )


def test_a_raw_date_reads_back():
    round_trip(para(text("on "), ("raw", "@2026-10-09", {}), text(" and "), ("raw", "@2026-10-09 09:30", {})))


def test_leaves_of_gamma_markdown_reach_notion_and_back():
    source = r"a **b** *c* ~~d~~ <u>e</u> `f` $g$ [h](https://x.org/a) \*i\* <span style='x'>"
    leaves = leaves_of(source)
    assert inline(write([Block("paragraph", leaves)], FakeLinks())) == leaves


NASTY = ["a", " ", "*", "**", "_", "`", "``", "~~", "=", "$", "\\", "<", ">", "&", "&amp;", "[", "]", "](y)", "(", ")",
         "!", "|", ":", ":x:", "#", "-", "1.", "<b>", "</b>", "<code>", "</code>", "</callout>", "<br />", "http://x",
         "https://x.org/a", "中", "💡", "---", "> ", "- ", "\\|", "\\\\", "$$", "@a", "[[x]]", "{", "}", "\xa0", "\u3000",
         "\u2028", "\x0c", "\x85", " #", "#"]
FORMULAS = ["$x_1$", "$x<p>y$", "$a<li>b$", "$a</callout>b$", "$a</toggle>b$", "$<b>$", "$[0,1)$", "$(a,b]$", "$]a[$",
            "$a<b$", r"$a\$b$", r"$\{x\}$", "$|x|$"]
EQUATIONS = ["x^2", "a</toggle>b", "<callout>", "</quote>", "a\n\nb", "```", "[0,1)"]
MARKS = [{}, BOLD, ITALIC, {"strike": True}, {"underline": True}, {"color": "red"}, {"color": "blue_bg"}, LINK,
         {**BOLD, **ITALIC}, {**LINK, **BOLD, "color": "red"}]


def _rich(rng: random.Random) -> list:
    out = []
    for _ in range(rng.randint(1, 5)):
        kind = rng.random()
        marks = rng.choice(MARKS)
        if kind < .1:
            one = ("code", "".join(rng.choices(NASTY, k=2)).strip() or "c", marks)
        elif kind < .2:
            one = ("math", rng.choice(FORMULAS), marks)
        elif kind < .3:
            one = ("br",)
        else:
            one = ("text", "".join(rng.choices(NASTY, k=rng.randint(1, 4))), marks)
        if out and one[0] in ("text", "code") and out[-1][0] == one[0] and out[-1][2] == one[2]:
            continue                             # the reader joins these
        if out and out[-1][0] == "math" and one[0] == "text" and one[1][0].isdigit():
            continue                             # see test_a_digit_after_a_formula_ends_it_as_text
        out.append(one)
    while out and out[0][0] == "br":             # a tag-less line cannot start or end on nothing
        out.pop(0)
    while out and out[-1][0] == "br":
        out.pop()
    return [] if not any(leaf[0] != "text" or leaf[1].strip() for leaf in out) else out


def _block(rng: random.Random, depth: int) -> Block:
    children = [_block(rng, depth + 1) for _ in range(rng.randint(0, 2))] if depth < 3 else []
    kind = rng.choice(["paragraph", "toggle", "quote", "callout", "bullet", "number", "todo", "heading", "code",
                       "equation", "divider", "table", "image", "page"])
    rich = _rich(rng)
    if kind == "number":
        if not children and rng.random() < .3:
            return Block(kind, [text("n")], attrs={"start": rng.randint(2, 12)})
        return Block(kind, rich, children, {"start": 1})
    if kind in ("paragraph", "toggle", "quote", "callout", "bullet"):
        return Block(kind, rich, children, {"color": rng.choice(["red", "blue_bg"])} if rng.random() < .1 else {})
    if kind == "todo":
        return Block(kind, rich, children, {"checked": rng.random() < .5})
    if kind == "heading":
        return Block(kind, rich, attrs={"level": rng.randint(1, 4)})
    if kind == "code":
        return Block(kind, attrs={"text": "\n".join(rng.choices(NASTY, k=3))})
    if kind == "equation":
        return Block(kind, attrs={"text": rng.choice(EQUATIONS)})
    if kind == "divider":
        return Block(kind)
    if kind == "table":
        rows = [[[text(f"x{rng.choice(NASTY)}x", rng.choice(MARKS))] for _ in range(2)] for _ in range(2)]
        return Block(kind, attrs={"header": rng.random() < .5, "rows": rows})
    if kind == "image":
        return Block(kind, [text("c")], attrs={"url": "https://x.org/a.png"})
    return Block(kind, [text("T")], attrs={"url": "https://www.notion.so/a"})


@pytest.mark.parametrize("seed", range(300))
def test_random_pages_read_back(seed):
    rng = random.Random(seed)
    blocks = [_block(rng, 0) for _ in range(rng.randint(1, 4))]
    page = write(blocks, FakeLinks())
    assert_valid(page)
    assert read(page) == blocks, page


# --- the file form ----------------------------------------------------------------

def filed(*blocks, links=None, **caps) -> str:
    """Blocks as ``_NotionBuilder`` writes them, ``caps`` over the safe rows."""
    table = {**CAPS, **caps}
    return write(lower_for_file(list(blocks), table), links or FakeLinks(), form="file", caps=table)


def bullet(s: str, *kids) -> Block:
    return Block("bullet", plain(s), list(kids))


def test_the_file_form_writes_markdown_marks():
    assert filed(para(text("b", {"bold": True}), text(" "), text("i", {"italic": True}), text(" "),
                      text("s", {"strike": True}), text(" "), text("u", {"underline": True}))) == "**b** *i* ~~s~~ <u>u</u>"


def test_the_file_form_leaves_a_mark_s_spaces_outside_it():
    assert filed(para(text("a "), text(" b ", {"bold": True}), text("c"))) == "a  **b** c"


def test_the_file_form_writes_a_code_run_and_a_formula_as_markdown():
    assert filed(para(("code", "a\nb", {}), text(" "), ("math", "$$x$$", {}))) == "`a b` $x$"


def test_the_file_form_escapes_text():
    assert filed(para(text("*x* $5 <tag> and $6"))) == "\\*x\\* \\$5 \\<tag> and \\$6"


def test_with_escaping_off_text_goes_as_it_is():
    assert filed(para(text("*x* $5 <tag>"), text(" "), text("b", {"bold": True})), escape=False) == "*x* $5 <tag> **b**"
    assert filed(Block("heading", plain("x #"), attrs={"level": 1}), escape=False) == "# x #"
    image = Block("image", plain("a*]"), attrs={"url": "https://x.org/a.png"})
    assert filed(image, escape=False) == "![a*]](https://x.org/a.png)"


def test_a_heading_ending_in_a_hash_keeps_it():
    assert filed(Block("heading", plain("C #"), attrs={"level": 1})) == "# C \\#"
    assert filed(Block("heading", plain("C#"), attrs={"level": 1})) == "# C#"


def test_a_heading_with_edge_spaces_needs_no_tag():
    assert filed(Block("heading", plain(" x "), attrs={"level": 2})) == "##  x "


def test_a_hard_break_keeps_each_line_s_start_in_view():
    assert filed(para(text("a"), ("br",), text("# b"), ("br",), text("---"))) == "a  \n\\# b  \n\\---"


def test_an_empty_paragraph_is_dropped():
    assert filed(para(), para(text("x")), para(text(" "))) == "x"


def test_a_file_list_is_indented_by_four():
    assert filed(bullet("a", bullet("b", Block("number", plain("c"), attrs={"start": 1})))) == (
        "- a\n    - b\n        1. c")
    assert filed(Block("todo", plain("t"), [bullet("u")], {"checked": True})) == "- [x] t\n    - u"


def test_a_long_number_indents_past_its_marker():
    assert filed(Block("number", plain("a"), [bullet("b")], {"start": 100})) == "100. a\n     - b"


def test_a_list_item_s_second_line_stays_in_it():
    assert filed(Block("bullet", [text("a"), ("br",), text("b")], [bullet("c")])) == "- a  \n    b\n    - c"


def test_what_is_no_list_follows_a_blank_line_or_it_would_read_as_more_of_the_text():
    assert filed(bullet("a", Block("quote", plain("q")))) == "- a\n\n    > q"
    assert filed(bullet("a", Block("heading", plain("h"), attrs={"level": 2}))) == "- a\n\n    ## h"
    code = Block("code", attrs={"text": "x\n\ny", "language": "py"})
    assert filed(bullet("a", code)) == "- a\n\n    ```py\n    x\n\n    y\n    ```"


def test_a_table_in_a_list_is_a_pipe_table():
    table = Block("table", attrs={"rows": [[plain("a")], [plain("b|c")]], "header": True})
    assert filed(bullet("x", table)) == "- x\n\n    | a |\n    | --- |\n    | b\\|c |"


def test_a_file_quote_holds_its_children_behind_its_marker():
    quote = Block("quote", [text("a"), ("br",), text("b")], [para(text("c")), bullet("d"),
                                                               Block("code", attrs={"text": "x"})])
    assert filed(quote) == "> a  \n> b\n>\n> c\n>\n> - d\n>\n> ```\n> x\n> ```"


def test_a_callout_goes_as_its_quote():
    assert filed(Block("callout", plain("x"), [para(text("y"))], {"icon": "🔥", "color": "red_bg"})) == "> 🔥 x\n>\n> y"


def test_the_file_form_keeps_a_language_the_api_has_no_name_for():
    assert filed(Block("code", attrs={"text": "x", "language": "bibtex"})) == "```bibtex\nx\n```"
    assert filed(Block("code", attrs={"text": "x", "language": "svg"})) == "```svg\nx\n```"


def test_a_table_with_no_header_row_has_an_empty_one_in_a_file():
    table = Block("table", attrs={"rows": [[plain("a"), plain("b")]], "header": False})
    assert filed(table) == "|  |  |\n| --- | --- |\n| a | b |"


def test_an_image_s_alt_is_its_words():
    links = FakeLinks()
    assert filed(Block("image", plain("a [b] *c*"), attrs={"url": "https://x.org/a.png"}), links=links) == (
        "![a \\[b\\] \\*c\\*](https://x.org/a.png)")


def test_a_file_with_a_place_is_a_link_line():
    class Bundles(FakeLinks):
        def asset(self, url, kind):
            return "Paper/a.pdf"

    block = Block("file", plain("the paper"), attrs={"url": "/api/uploads/a.pdf", "media": "pdf"})
    assert filed(block, links=Bundles()) == "[the paper](Paper/a.pdf)"
    assert filed(Block("file", attrs={"url": "/api/uploads/a.pdf"}), links=Bundles()) == "[a.pdf](Paper/a.pdf)"


def test_a_page_is_a_link_line_in_a_file():
    assert filed(Block("page", plain("A [b]"), attrs={"url": "A%20b.md"})) == "[A \\[b\\]](A%20b.md)"


def test_a_target_the_writer_is_given_keeps_its_percent_signs():
    assert filed(para(text("x", {"link": "50%25%20a.md"}))) == "[x](50%25%20a.md)"


def test_no_tag_reaches_a_file():
    """Every construct, at the safe rows: a tag here is text in Notion."""
    blocks = [Block("toggle", plain("t"), [para(text("c"))]), Block("toggle", plain("h"), [para(text("c"))], {"heading": 1}),
              Block("callout", plain("x"), [para(text("y"))], {"icon": "🔥", "color": "red_bg"}),
              Block("quote", plain("q"), [bullet("a")], {"color": "red"}),
              para(text("c", {"color": "red"}), ("raw", "@2026-10-09 10:00", {}), ("raw", "@ada", {})),
              Block("todo", plain("t"), [para(text("c"))], {"checked": False, "color": "red"}),
              Block("number", plain("n"), [Block("quote", plain("q"))], {"start": 2}),
              Block("page", plain("P"), attrs={"url": "P.md", "database": "abc"}),
              Block("table", attrs={"rows": [[[text("a"), ("br",), text("b")]]], "header": False}),
              Block("file", plain("f"), attrs={"url": "https://x.org/a.mp4", "media": "video"})]
    assert not re.search(r"</?(?!u>)[a-z-]+[ >/]", filed(*blocks))


@pytest.mark.parametrize("cap, block, out", [
    ("callout", Block("callout", plain("x"), attrs={"icon": "🔥"}), '<callout icon="🔥">\nx\n</callout>'),
    ("toggle", Block("toggle", plain("x"), [bullet("y")]), "<toggle>\nx\n\n- y\n</toggle>"),
    ("toggle_heading", Block("toggle", plain("x"), [bullet("y")], {"heading": 2}),
     '<toggle heading="h2">\nx\n\n- y\n</toggle>'),
    ("paragraph_children", Block("paragraph", plain("x"), [para(text("y"))]), "<p>\nx\n\ny\n</p>"),
    ("date", Block("paragraph", [("raw", "@2026-10-09", {})]), '<date start="2026-10-09" />'),
    ("page", Block("page", plain("T"), attrs={"url": "T.md"}), '<page url="T.md">T</page>'),
    ("headerless_table", Block("table", attrs={"rows": [[plain("a")]], "header": False}),
     '<table header-row="false">\n<tr>\n<td>a</td>\n</tr>\n</table>'),
    ("color", Block("paragraph", [text("x", {"color": "red", "bold": True})]), '**<span color="red">x</span>**'),
])
def test_a_row_turned_on_writes_notion_s_tag(cap, block, out):
    assert filed(block, **{cap: True}) == out


def test_the_row_for_a_hard_break_and_a_list_s_indent_are_one_value_each():
    assert filed(Block("paragraph", [text("a"), ("br",), text("b")]), hard_break="\\\n") == "a\\\nb"
    assert filed(bullet("a", bullet("b")), list_indent=2) == "- a\n  - b"


def test_a_heading_goes_to_the_row_s_level():
    assert filed(Block("heading", plain("x"), attrs={"level": 6}), heading_level=4) == "#### x"


# --- what the file form and Gamma's reader agree on ------------------------------

@pytest.mark.parametrize("block", [
    para(text("b", {"bold": True}), text(" and "), text("i", {"italic": True})),
    para(text("a"), ("br",), text("b")),
    bullet("a", bullet("b"), bullet("c")),
    Block("number", plain("a"), [bullet("b")], {"start": 3}),
    Block("todo", plain("a"), attrs={"checked": True}),
    Block("code", attrs={"text": "x", "language": "visual basic"}),
    Block("equation", attrs={"text": "x^2"}),
    Block("table", attrs={"rows": [[plain("a|b")], [plain("c")]], "header": True}),
    Block("heading", plain("C #"), attrs={"level": 2}),
])
def test_a_file_reads_back_as_its_blocks(block):
    assert blocks_of(filed(block)) == [block]


# --- a fence's language ----------------------------------------------------------

def test_a_language_of_two_words_goes_out_whole():
    assert nfm(Block("code", attrs={"text": "x", "language": "visual basic"})) == "```visual basic\nx\n```"


def test_a_language_that_cannot_stand_after_a_fence_goes():
    assert nfm(Block("code", attrs={"text": "x", "language": "a`b"})) == "```\nx\n```"
    assert nfm(Block("code", attrs={"text": "x", "language": "a\nb"})) == "```\nx\n```"


@pytest.mark.parametrize("fence, language", [
    ("```visual basic", "visual basic"), ("```Visual Basic", "visual basic"), ("```python title=x", "python"),
    ("``` py", "py"), ("```plain text", None), ("```Plain Text", None), ("```", None),
])
def test_a_fence_s_language_reads_as_notion_names_it(fence, language):
    attrs = read(f"{fence}\nx\n```")[0].attrs
    assert attrs.get("language") == language
