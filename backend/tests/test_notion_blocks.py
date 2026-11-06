"""The Notion block model and colours (gamma/notion_blocks.py), against
tests/shared/notioncolors.json; the rich text ``notion_gamma.leaves_of`` reads
out of a block's Markdown; and what ``zotero_notes`` gives the Notion routes
beyond what its own notes need."""

import json
import random
import time
from pathlib import Path

import pytest

from gamma import notion_blocks
from gamma.notion_blocks import (CAPS, Block, Page, callout_color, callout_type, color_name, gamma_leaves,
                                 gamma_marks, lower_for_file, merge_colors, style_color)
from gamma.notion_gamma import leaves_of
from gamma.zotero_notes import Parser, block_elements, css_style, markdown, node_tag

SHARED = Path(__file__).resolve().parents[2] / "tests/shared"
COLORS = json.loads((SHARED / "notioncolors.json").read_text(encoding="utf-8"))
MENTIONS = json.loads((SHARED / "mentions.json").read_text(encoding="utf-8"))["scan"]
BOLD, ITALIC, BOTH = {"bold": True}, {"italic": True}, {"bold": True, "italic": True}


def written(leaves) -> str:
    """Rich text as ``to_gamma`` writes it."""
    return markdown(gamma_leaves(leaves), collapse=False, mentions=True)


# --- the model -------------------------------------------------------------------

def test_blocks_do_not_share_their_lists():
    a, b = Block("paragraph"), Block("paragraph")
    a.children.append(Block("bullet"))
    a.attrs["color"] = "red"
    assert b.children == [] and b.attrs == {} and b.rich == [] and b.id is None


def test_a_page_holds_its_top_blocks():
    page = Page("Title", [Block("divider")], gamma_id="g1", notion_id="n1")
    assert page.blocks[0].type == "divider" and (page.gamma_id, page.notion_id) == ("g1", "n1")


# --- colours ---------------------------------------------------------------------

def test_the_palette_is_the_shared_one():
    assert notion_blocks.TEXT_COLORS == COLORS["text"]
    assert notion_blocks.BROWN == COLORS["brown"] and notion_blocks.TINT == COLORS["tint"]


@pytest.mark.parametrize("notion, model", COLORS["names"].items())
def test_notion_names_read_as_the_models(notion, model):
    assert color_name(notion) == model


def test_no_name_means_no_colour():
    assert color_name(None) is None and color_name("") is None and color_name("red_ish") is None


@pytest.mark.parametrize("color", [c for c in COLORS["spans"] if c != "yellow_bg"])
def test_a_colour_is_written_as_the_editor_writes_its_span(color):
    assert written([("text", "x", {"color": color})]) == f"{COLORS['spans'][color]}x</span>"


def test_a_lone_yellow_background_is_a_mark():
    assert gamma_marks({"color": "yellow_bg", "bold": True}) == {"mark": True, "bold": True}
    assert written([("text", "x", {"color": "yellow_bg"})]) == "==x=="


def test_both_colours_are_one_span():
    color = tuple(COLORS["pair"]["color"])
    assert written([("text", "x", {"color": color})]) == f"{COLORS['pair']['span']}x</span>"
    assert leaves_of(f"{COLORS['pair']['span']}x</span>") == [("text", "x", {"color": color})]


def test_yellow_in_a_pair_is_its_tint():
    assert written([("text", "x", {"color": ("red", "yellow_bg")})]) == (
        '<span style="color:#e5484d; background:#d4a01755">x</span>')


def test_colours_merge_into_a_pair_the_inner_winning():
    assert merge_colors(None, None) is None
    assert merge_colors("red", None) == "red" and merge_colors(None, "red_bg") == "red_bg"
    assert merge_colors("red", "blue_bg") == merge_colors("blue_bg", "red") == ("red", "blue_bg")
    assert merge_colors("red", "blue") == "blue"
    assert merge_colors(("red", "blue_bg"), "green_bg") == ("red", "green_bg")


@pytest.mark.parametrize("css, color", [
    ("color:#e5484d", "red"),
    ("background:#e5484d55", "red_bg"),
    ("background-color:#E5484D", "red_bg"),
    ("color:#9f765a", "brown"),
    ("background:#9f765a55", "brown_bg"),
    ("color:red", "red"),
    ("color:#e00000", "red"),                    # by nearest hue
    ("color:rgb(59, 130, 246)", "blue"),
    ("color:#e5484d; background:#3b82f655", ("red", "blue_bg")),
    ("color:crimson", None),                     # a value no hue can stand for
    ("font-size:12px", None),
    ("color:teal", "green"),
    ("color:grey", "gray"),
    ("color:#808080", "gray"),                   # no hue in it, so never the nearest
    ("color:#555", "gray"),
    ("color:#000000", None),                     # the page's own ink and paper
    ("color:#111", None),
    ("color:rgb(0, 0, 0)", None),
    ("color:#ffffff", None),
    ("background:#ffffff", None),
    ("background:#000", None),
    ("background:#fafafa", None),
])
def test_a_style_reads_as_its_hue(css, color):
    assert style_color(css_style(css)) == color


@pytest.mark.parametrize("notion, kind", COLORS["callout_types"].items())
def test_a_callouts_colour_reads_as_its_type(notion, kind):
    assert callout_type(notion) == kind


def test_a_callout_with_no_colour_is_info():
    assert callout_type(None) == "info" and callout_type("teal") == "tip"


@pytest.mark.parametrize("kind, color", COLORS["callout_colors"].items())
def test_a_callouts_type_goes_out_as_its_colour(kind, color):
    assert callout_color(kind) == color


# --- leaves_of -------------------------------------------------------------------

@pytest.mark.parametrize("text, leaves", [
    ("plain", [("text", "plain", {})]),
    ("", []),
    (None, []),
    ("a **b** c", [("text", "a ", {}), ("text", "b", BOLD), ("text", " c", {})]),
    ("__b__ _i_", [("text", "b", BOLD), ("text", " ", {}), ("text", "i", ITALIC)]),
    ("***x***", [("text", "x", BOTH)]),
    ("*a **b** c*", [("text", "a ", ITALIC), ("text", "b", BOTH), ("text", " c", ITALIC)]),
    ("**a *b* c**", [("text", "a ", BOLD), ("text", "b", BOTH), ("text", " c", BOLD)]),
    ("**a *b***", [("text", "a ", BOLD), ("text", "b", BOTH)]),
    ("***a** b*", [("text", "a", BOTH), ("text", " b", ITALIC)]),
    ("*a **b***", [("text", "a ", ITALIC), ("text", "b", BOTH)]),
    ("**a***b*", [("text", "a", BOLD), ("text", "b", ITALIC)]),
    ("*a***b**", [("text", "a", ITALIC), ("text", "b", BOLD)]),
    ("**a *b***c", [("text", "a ", BOLD), ("text", "b", BOTH), ("text", "c", {})]),
    ("**a *b* c***d*", [("text", "a ", BOLD), ("text", "b", BOTH), ("text", " c", BOLD), ("text", "d", ITALIC)]),
    ("**a** and **b**", [("text", "a", BOLD), ("text", " and ", {}), ("text", "b", BOLD)]),
    ("~~s~~", [("text", "s", {"strike": True})]),
    ("<u>**x**</u>", [("text", "x", {"underline": True, "bold": True})]),
    ("**a\nb**", [("text", "a", BOLD), ("br",), ("text", "b", BOLD)]),
])
def test_marks_nest(text, leaves):
    assert leaves_of(text) == leaves


@pytest.mark.parametrize("leaves", [
    [("text", "a", BOLD), ("text", "b", ITALIC)],
    [("text", "a", ITALIC), ("text", "b", BOLD)],
    [("text", "a", ITALIC), ("text", "b", BOTH), ("text", "c", BOLD)],
    [("text", "a", BOTH), ("text", "b", ITALIC)],
    [("text", "a", BOLD), ("text", "b", ITALIC), ("text", "c", BOLD)],
    [("text", "a", ITALIC), ("text", "b", BOLD), ("text", "c", ITALIC)],
    [("text", "Note:", BOLD), ("text", "see", ITALIC), ("text", " end", {})],
])
def test_touching_bold_and_italic_read_back_as_they_went_out(leaves):
    assert leaves_of(written(leaves)) == leaves


def test_marks_on_any_text_lose_none_of_it_on_the_way_back():
    # The writer can leave an italic off, or a space out of a mark; what it
    # writes still reads as the same text, no delimiter left over in it.
    pieces = ["a", "b c", "x", "1", "Note:", "see", "(p)", "c.", "*", "**", "_", "~~", "==", "](", "[", "`", "$",
              "<u>", "@bob", "1. "]
    marks = [{}, BOLD, ITALIC, BOTH, {"strike": True}, {"underline": True}, {"color": "red"}, {"color": "yellow_bg"},
             {"link": "http://x.org"}, {"bold": True, "link": "u"}, {"italic": True, "strike": True}]

    def plain(leaves):
        return "".join(leaf[1] for leaf in leaves).strip()

    rng = random.Random(4)
    for _ in range(5000):
        leaves = [("text", rng.choice(pieces), rng.choice(marks)) for _ in range(rng.randint(1, 4))]
        assert plain(leaves_of(written(leaves))) == plain(leaves), repr(leaves)


@pytest.mark.parametrize("text", ["2 * 3 * 4", "snake_case_name", "a__b__c", "* x", "** x **", "~~ x ~~", "<b>x", "1 < 2"])
def test_what_is_no_mark_stays_text(text):
    assert leaves_of(text) == [("text", text, {})]


@pytest.mark.parametrize("text, leaves", [
    ("`*x*`", [("code", "*x*", {})]),
    ("``x``", [("code", "x", {})]),
    ("**`x`**", [("code", "x", BOLD)]),
    ("$a*b*c$", [("math", "$a*b*c$", {})]),
    ("*$a*b$*", [("math", "$a*b$", ITALIC)]),
    ("\\(x_1\\) y", [("math", "$x_1$", {}), ("text", " y", {})]),
    ("a $$x$$", [("text", "a ", {}), ("math", "$$x$$", {})]),
    ("$5 only", [("text", "$5 only", {})]),
])
def test_code_and_math_hold_no_marks(text, leaves):
    assert leaves_of(text) == leaves


@pytest.mark.parametrize("text, literal", [
    ("\\*x\\*", "*x*"),
    ("\\$5", "$5"),
    ("\\[x]", "[x]"),
    ("&amp; &lt;tag&gt; &#36;", "& <tag> $"),
    ("&nosuch;", "&nosuch;"),
    ("\\_a\\_", "_a_"),
])
def test_text_comes_out_literal(text, literal):
    assert leaves_of(text) == [("text", literal, {})]


@pytest.mark.parametrize("text", ["a<br>b", "a<br/>b", "a<br />b", "a\nb", "a  \nb", "a\\\nb"])
def test_each_line_break_is_a_br(text):
    assert leaves_of(text) == [("text", "a", {}), ("br",), ("text", "b", {})]


@pytest.mark.parametrize("text, leaves", [
    ("a\\ \nb", [("text", "a\\", {}), ("br",), ("text", "b", {})]),
    ("a \\\nb", [("text", "a", {}), ("br",), ("text", "b", {})]),
    ("a  ", [("text", "a  ", {})]),
])
def test_only_a_lines_own_end_is_a_break(text, leaves):
    assert leaves_of(text) == leaves


def test_a_run_of_spaces_is_read_once():
    start = time.perf_counter()
    assert leaves_of(" " * 100000) == [("text", " " * 100000, {})]
    assert leaves_of("a" + " " * 100000 + "b\n") == [("text", "a" + " " * 100000 + "b", {}), ("br",)]
    assert time.perf_counter() - start < 5


@pytest.mark.parametrize("tag", ["sup", "sub", "kbd"])
def test_a_tag_with_no_mark_keeps_its_text(tag):
    assert leaves_of(f"x<{tag}>**2**</{tag}>") == [("text", "x", {}), ("text", "2", BOLD)]


@pytest.mark.parametrize("text, marks", [
    ('<span style="color:#e5484d">x</span>', {"color": "red"}),
    ('<span style="background:#30a46c55">x</span>', {"color": "green_bg"}),
    ('<span style="color:#00f">x</span>', {"color": "blue"}),
    ('<span style="color:crimson">x</span>', {}),
    ("==x==", {"color": "yellow_bg"}),
    ('<span style="color:#e5484d">==x==</span>', {"color": ("red", "yellow_bg")}),
    ('<span style="color:#e5484d">**x**</span>', {"color": "red", "bold": True}),
])
def test_coloured_runs(text, marks):
    assert leaves_of(text) == [("text", "x", marks)]


@pytest.mark.parametrize("text, leaves", [
    ("[a](http://x.org)", [("text", "a", {"link": "http://x.org"})]),
    ('[a](http://x.org "title")', [("text", "a", {"link": "http://x.org"})]),
    ("[**a** b](u_v_w)", [("text", "a", {"link": "u_v_w", "bold": True}), ("text", " b", {"link": "u_v_w"})]),
    ("*[a](u*v)*", [("text", "a", {"link": "u*v", "italic": True})]),
    ("[`a`](u)", [("code", "a", {"link": "u"})]),
    ("[a [b] c](d)", [("text", "a [b] c", {"link": "d"})]),
    ("[](http://x.org)", [("text", "http://x.org", {"link": "http://x.org"})]),
    ("[x [y](z)", [("text", "[x ", {}), ("text", "y", {"link": "z"})]),
    ("x](y)", [("text", "x](y)", {})]),
    ("[a](10.1016/S0140(20)30183-5)", [("text", "a", {"link": "10.1016/S0140(20)30183-5"})]),
    ("see https://x.org/a_b_c now", [("text", "see ", {}),
                                     ("text", "https://x.org/a_b_c", {"link": "https://x.org/a_b_c"}),
                                     ("text", " now", {})]),
    ("[https://x.org](https://x.org)", [("text", "https://x.org", {"link": "https://x.org"})]),
    ("\\[a](b)", [("text", "[a](b)", {})]),
])
def test_links(text, leaves):
    assert leaves_of(text) == leaves


URL = "https://x.org/a"


def url_in(marks):
    return ("text", URL, {"link": URL, **marks})


@pytest.mark.parametrize("text, leaves", [
    (f"**{URL}**", [url_in(BOLD)]),
    (f"*{URL}*", [url_in(ITALIC)]),
    (f"_{URL}_", [url_in(ITALIC)]),
    (f"__{URL}__", [url_in(BOLD)]),
    (f"~~{URL}~~", [url_in({"strike": True})]),
    (f"=={URL}==", [url_in({"color": "yellow_bg"})]),
    (f"*=={URL}==*", [url_in({"italic": True, "color": "yellow_bg"})]),
    (f"**see {URL}** then", [("text", "see ", BOLD), url_in(BOLD), ("text", " then", {})]),
    (f"see {URL}.", [("text", "see ", {}), url_in({}), ("text", ".", {})]),
    (f"**{URL}**, and", [url_in(BOLD), ("text", ", and", {})]),
    (f"{URL}!?", [url_in({}), ("text", "!?", {})]),
    (f"'{URL}'", [("text", "'", {}), url_in({}), ("text", "'", {})]),
])
def test_a_bare_address_ends_where_gfm_ends_it(text, leaves):
    assert leaves_of(text) == leaves


@pytest.mark.parametrize("text", ["https://x.org/a_b*c~d", "https://x.org/?t=abc==", "https://x.org/?t=a=", "https://x.org/(a)"])
def test_what_is_inside_or_no_mark_stays_in_the_address(text):
    leaf = leaves_of(text)[0]
    assert leaf[1] == text


def test_an_address_written_again_reads_the_same():
    leaves = leaves_of(f"see {URL}.")
    assert written(leaves) == f"see [{URL}]({URL})."
    assert leaves_of(written(leaves)) == leaves


@pytest.mark.parametrize("text, address", [
    ("[a](<http://x.org/f.pdf>)", "http://x.org/f.pdf"),
    ("![a](<http://x.org/f.pdf>)", "http://x.org/f.pdf"),
    ("[a](<u(1.pdf>)", "u(1.pdf"),
    ("[a](<my file.pdf>)", "my%20file.pdf"),
    ("[a](http://x?a=1&amp;b=2)", "http://x?a=1&b=2"),
    ("[a](http://x/a\\_b)", "http://x/a_b"),
    ("[a](http://x?a=1&copy=2)", "http://x?a=1&copy=2"),     # an entity needs its ";"
    ("[a](http://x/\\&amp;)", "http://x/&amp;"),
    ("[a](<x)", "<x"),
])
def test_a_links_target_is_the_address_it_stands_for(text, address):
    assert leaves_of(text) == [("text", "a", {"link": address})]


def test_a_stray_target_hides_no_mark():
    assert leaves_of("**a\\[b](c d** e)") == [("text", "a[b](c d", BOLD), ("text", " e)", {})]
    assert leaves_of("](a\\*b)") == [("text", "](a*b)", {})]


def test_stray_targets_do_not_nest_the_reading():
    for piece in ("](a ", "[a]b](c ", "**[a** b](c "):
        assert leaves_of(piece * 3000 + ")")


@pytest.mark.parametrize("text, leaves", [
    ("![a|300](u.png)", [("text", "a", {"link": "u.png"})]),
    ("![](u.png)", [("text", "u.png", {"link": "u.png"})]),
    ("![a](u.png){:width 300}", [("text", "a", {"link": "u.png"})]),
    ("[![a](u.png)](v)", [("text", "a", {"link": "v", "image_lost": True})]),
])
def test_an_image_is_never_a_leaf(text, leaves):
    assert leaves_of(text) == leaves


@pytest.mark.parametrize("text, leaves", [
    ("[[abc_d_]]", [("raw", "[[abc_d_]]", {})]),
    ("![[abc]]", [("raw", "![[abc]]", {})]),
    ("**[[abc]]**", [("raw", "[[abc]]", BOLD)]),
])
def test_page_links_stay_as_written(text, leaves):
    assert leaves_of(text) == leaves


@pytest.mark.parametrize("case", MENTIONS, ids=[c["note"] for c in MENTIONS])
def test_mentions_are_the_ones_the_shared_cases_name(case):
    raws = [leaf[1] for leaf in leaves_of(case["text"]) if leaf[0] == "raw"]
    assert raws == [token["raw"] for token in case["tokens"]]


def test_a_mention_keeps_a_last_underscore_as_the_rendered_view_does():
    # mentions.js takes it out of the source before any mark is read.
    assert leaves_of("_see @alice_") == [("text", "_see ", {}), ("raw", "@alice_", {})]


def test_a_mention_inside_a_link_is_its_text():
    assert leaves_of("[@alice](u)") == [("text", "@alice", {"link": "u"})]


@pytest.mark.parametrize("text", [
    "plain", "a **b** c", "**_x_**", "~~s~~ <u>u</u>", "a\nb", "`x` and $y$", "[a](http://x.org)",
    "see https://x.org/a_(b) now", "[[abc]] @2026-10-09 @alice", "\\@alice", "$5", "\\[x]", "\\*x\\*",
    "==x==", '<span style="color:#e5484d">x</span>', '<span style="color:#e5484d; background:#3b82f655">x</span>',
    "1\\. Intro", "\\<tag>", "**a** *b*",
])
def test_written_again_it_is_what_it_was(text):
    assert written(leaves_of(text)) == text


def test_strange_text_reads_and_settles():
    # Text the grammar was not written for: no crash, no placeholder left, and
    # what is written settles (the first passes may still drop a line's last
    # space, or read a layer of escape out of a target the writer leaves plain).
    pieces = ["*", "**", "_", "__", "~~", "==", "`", "$", "\\", "[", "]", "(", ")", "](x)", "<u>", "</u>", "<br>",
              "&amp;", "a", " ", "\n", "b c", "@bob", "[[d]]", "http://e.f/g", '<span style="color:red">', "</span>",
              "\x00", "\x01", "\x02", "0"]
    rng = random.Random(9)
    for _ in range(3000):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 14)))
        leaves = leaves_of(text)
        for leaf in leaves:
            assert leaf == ("br",) or (leaf[0] in ("text", "code", "math", "raw") and leaf[1])
            assert leaf == ("br",) or not set(leaf[1]) & set("\x00\x01\x02")
        settled = written(leaves_of(written(leaves_of(written(leaves)))))
        assert written(leaves_of(settled)) == settled, repr(text)


# --- what zotero_notes gives the Notion routes -----------------------------------

def test_markdown_can_keep_spaces():
    leaves = [("text", "a  b ", {}), ("br",), ("text", "  c", {})]
    assert markdown(leaves) == "a b\nc"
    assert markdown(leaves, collapse=False) == "a  b \n  c"


def test_markdown_escapes_a_literal_mention_when_asked():
    leaves = [("text", "ask @alice, a.b@x.org and @2026-10-09", {})]
    assert markdown(leaves) == "ask @alice, a.b@x.org and @2026-10-09"
    assert markdown(leaves, mentions=True) == "ask \\@alice, a.b@x.org and \\@2026-10-09"
    assert leaves_of(markdown(leaves, mentions=True)) == leaves


@pytest.mark.parametrize("text", ["$5", "<tag>", "[x]", "1. Intro", "  1. Intro", "  # x", "\t- x", "  > x", "   ---"])
def test_literal_text_stays_literal_in_markdown(text):
    md = markdown([("text", text, {})], collapse=False, mentions=True)
    assert leaves_of(md) == [("text", text, {})]
    assert [el[0] for el in block_elements(md)] == ["para"]


def test_a_newline_in_text_is_a_line_break_with_its_own_start():
    md = markdown([("text", "a\n# b\n1. c", {})], collapse=False, mentions=True)
    assert md == "a\n\\# b\n1\\. c"
    assert [el[0] for el in block_elements(md)] == ["para"]
    assert leaves_of(md) == [("text", "a", {}), ("br",), ("text", "# b", {}), ("br",), ("text", "1. c", {})]


def test_collapsing_still_leaves_no_indent():
    assert markdown([("text", "  # x", {})]) == "\\# x"


def test_a_raw_leaf_passes_through_unescaped():
    assert markdown([("text", "a *", {}), ("raw", "[[x_y]]", {})]) == "a \\*[[x_y]]"


def tree(html, **options):
    parser = Parser(**options)
    parser.feed(html)
    parser.close()
    return parser.root


def test_the_parser_can_keep_a_paragraph_open():
    html = '<p>a<div class="indented"><p>b</p></div></p>'
    assert [node_tag(n) for n in tree(html).children] == ["p", "div"]
    assert [node_tag(n) for n in tree(html, keep_p=True).children] == ["p"]


def test_the_parser_can_leave_out_more():
    html = "<p>a<svg><title>t</title><path/></svg></p>"
    assert "svg" in [node_tag(n) for n in tree(html).children[0].children]
    assert tree(html, hidden=("svg",)).children[0].children == ["a"]



def test_block_elements_is_the_public_name():
    assert block_elements("# a\n\nb") == [("heading", 1, "a"), ("para", "b")]


# --- lowering for the file route --------------------------------------------------

def line(s: str, marks=None) -> tuple:
    return ("text", s, marks or {})


def para(s: str, *kids) -> Block:
    return Block("paragraph", [line(s)], list(kids))


def lowered(block: Block, **caps) -> list:
    return lower_for_file([block], {**CAPS, **caps})


def test_a_callout_is_a_quote_opening_with_its_icon():
    callout = Block("callout", [line("x")], [para("y")], {"icon": "🔥", "color": "red_bg"})
    assert lowered(callout) == [Block("quote", [line("🔥 "), line("x")], [para("y")])]


@pytest.mark.parametrize("attrs", [{}, {"icon": '<img src="https://x.org/i.svg">'}])
def test_a_callout_with_no_icon_to_write_takes_the_default(attrs):
    assert lowered(Block("callout", [line("x")], attrs=attrs))[0].rich == [line("💡 "), line("x")]
    assert lowered(Block("callout", children=[para("y")], attrs=attrs))[0].rich == [line("💡")]


def test_a_toggle_is_a_bullet_and_with_no_children_a_paragraph():
    assert lowered(Block("toggle", [line("x")], [para("y")])) == [Block("bullet", [line("x")], [Block("bullet", [line("y")])])]
    assert lowered(Block("toggle", [line("x")])) == [para("x")]


def test_a_paragraph_with_children_is_a_bullet():
    assert lowered(para("x", para("y", para("z")))) == [
        Block("bullet", [line("x")], [Block("bullet", [line("y")], [Block("bullet", [line("z")])])])]


@pytest.mark.parametrize("kind", ["bullet", "number", "todo"])
def test_a_paragraph_in_a_list_is_a_bullet(kind):
    assert lowered(Block(kind, [line("x")], [para("y")]))[0].children == [Block("bullet", [line("y")])]


@pytest.mark.parametrize("kind", ["quote", "callout"])
def test_a_paragraph_in_a_quote_stays_one(kind):
    assert lowered(Block(kind, [line("x")], [para("y")]))[0].children == [para("y")]


def test_a_toggle_heading_is_a_heading_with_its_children_after_it():
    toggle = Block("toggle", [line("x")], [para("y")], {"heading": 2})
    assert lowered(toggle) == [Block("heading", [line("x")], attrs={"level": 2}), para("y")]
    assert lowered(Block("toggle", [line("x")], attrs={"heading": 2})) == [Block("heading", [line("x")], attrs={"level": 2})]


@pytest.mark.parametrize("level, out", [(1, 1), (3, 3), (4, 3), (6, 3)])
def test_a_heading_stops_at_the_third_level(level, out):
    assert lowered(Block("heading", [line("x")], attrs={"level": level}))[0].attrs["level"] == out


def test_a_heading_s_children_follow_it_and_its_text_keeps_to_a_line():
    heading = Block("heading", [line("a"), ("br",), line("b\nc")], [para("y")], {"level": 2})
    assert lowered(heading) == [Block("heading", [line("a"), line(" "), line("b c")], attrs={"level": 2}), para("y")]


@pytest.mark.parametrize("color, marks", [
    ("yellow_bg", {"bold": True}),
    ("red_bg", {"bold": True}),
    (("red", "blue_bg"), {"bold": True}),
    ("red", {}),
])
def test_a_background_is_bold_and_a_text_colour_goes(color, marks):
    assert lowered(Block("paragraph", [line("x", {"color": color})]))[0].rich == [line("x", marks)]


def test_a_block_s_colour_goes():
    assert lowered(Block("quote", [line("x")], attrs={"color": "red"}))[0].attrs == {}


def test_underline_stays():
    assert lowered(Block("paragraph", [line("x", {"underline": True})]))[0].rich == [line("x", {"underline": True})]


def test_a_date_and_a_person_are_their_text():
    block = Block("paragraph", [("raw", "@2026-10-09", {}), ("raw", "@ada", {"bold": True})])
    assert lowered(block)[0].rich == [line("@2026-10-09"), line("@ada", {"bold": True})]


def test_a_page_is_a_link_line():
    page = Block("page", [line("T", {"bold": True})], attrs={"ref": "p1", "url": "T.md"})
    assert lowered(page) == [Block("paragraph", [line("T", {"bold": True, "link": "T.md"})])]
    assert lowered(Block("page", attrs={"url": "T.md"})) == [Block("paragraph", [line("T.md", {"link": "T.md"})])]
    assert lowered(Block("page", [line("T")], attrs={"ref": "p1"})) == [para("T")]


def test_a_table_with_no_header_gets_an_empty_one_and_a_cell_keeps_to_a_line():
    table = Block("table", attrs={"rows": [[[line("a", {"color": "red"}), ("br",), line("b")]], [[], [line("c")]]],
                                  "header": False})
    assert lowered(table)[0].attrs == {"header": True, "rows": [
        [[], []], [[line("a"), line(" "), line("b")]], [[], [line("c")]]]}


def test_the_blocks_given_stay_as_they_are():
    inner = Block("paragraph", [line("y", {"color": "red"})])
    block = Block("toggle", [line("x")], [inner], {"color": "blue"})
    lower_for_file([block])
    assert block == Block("toggle", [line("x")], [Block("paragraph", [line("y", {"color": "red"})])], {"color": "blue"})


def test_what_needs_no_lowering_goes_through():
    for block in (Block("divider"), Block("code", attrs={"text": "x", "language": "py"}),
                  Block("equation", attrs={"text": "x"}), Block("image", [line("c")], attrs={"url": "u"}),
                  Block("bullet", [line("x")]), Block("todo", [line("x")], attrs={"checked": True})):
        assert lowered(block) == [block]


def test_an_id_stays_on_its_block():
    assert lowered(Block("toggle", [line("x")], [para("y")], id="g1"))[0].id == "g1"


# Each row of CAPS, turned round: the construct stays as it was given.
@pytest.mark.parametrize("cap, value, block", [
    ("callout", True, Block("callout", [line("x")], attrs={"icon": "🔥"})),
    ("toggle", True, Block("toggle", [line("x")], [Block("bullet", [line("y")])])),
    ("toggle_heading", True, Block("toggle", [line("x")], [Block("bullet", [line("y")])], {"heading": 2})),
    ("paragraph_children", True, para("x", para("y"))),
    ("heading_level", 4, Block("heading", [line("x")], attrs={"level": 4})),
    ("color", True, Block("paragraph", [line("x", {"color": "red_bg"})], attrs={"color": "blue"})),
    ("date", True, Block("paragraph", [("raw", "@2026-10-09", {})])),
    ("page", True, Block("page", [line("T")], attrs={"url": "T.md"})),
    ("headerless_table", True, Block("table", attrs={"rows": [[[line("a")]]], "header": False})),
])
def test_a_row_of_caps_turned_on_keeps_its_construct(cap, value, block):
    assert lowered(block, **{cap: value}) == [block]


def test_underline_turned_off_goes():
    assert lowered(Block("paragraph", [line("x", {"underline": True})]), underline=False)[0].rich == [line("x")]
