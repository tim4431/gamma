"""The lens, block by block (``gamma/md_lens.py``): each class of block, each law
on small files, and every counter-example of the review that shaped it. Over
the whole corpus, with random edits: ``test_md_lens_corpus.py``."""

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_md_lens_corpus as gate
from test_md_lens_corpus import check_read

from gamma import md_lens
from gamma.markdown_import import block_class, md_to_blocks
from gamma.md_lens import (MEANING, OPEN_BLOCK, Unwritable, content_hash, flatten, match, parse, patch_front, project,
                           render, required, shape, tree_equal, tree_of, write)


def read(raw: str) -> tuple:
    """A file's doc, its nodes' ids, and its tree."""
    doc = parse(raw)
    ids = [f"b{i}" for i in range(len(doc.flat))]
    return doc, ids, tree_of(doc, ids)


def contents(tree: list) -> list:
    return [(node["content"], contents(node["children"])) if node["children"] else node["content"] for node in tree]


def node_with(tree: list, content: str) -> dict:
    return next(node for node in flatten(tree) if node["content"] == content)


def holder_of(tree: list, node: dict) -> list:
    pending = [tree]
    while pending:
        nodes = pending.pop()
        if any(n is node for n in nodes):
            return nodes
        pending += [n["children"] for n in nodes]
    raise KeyError(node["content"])


def edited(raw: str, old: str, new: str) -> str:
    """``raw`` with the block ``old`` retyped as ``new``, through ``shape``."""
    doc, ids, tree = read(raw)
    node_with(tree, old)["content"] = new
    return shape(doc, ids, tree)[1]


def without(raw: str, content: str) -> str:
    doc, ids, tree = read(raw)
    node = node_with(tree, content)
    holder_of(tree, node).remove(node)
    return shape(doc, ids, tree)[1]


def child_added(raw: str, parent: str, content: str) -> str:
    doc, ids, tree = read(raw)
    node_with(tree, parent)["children"].append({"id": "new", "content": content, "children": []})
    return shape(doc, ids, tree)[1]


def inserted(raw: str, after: str, content: str) -> str:
    doc, ids, tree = read(raw)
    node = node_with(tree, after)
    holder = holder_of(tree, node)
    holder.insert(holder.index(node) + 1, {"id": "new", "content": content, "children": []})
    return shape(doc, ids, tree)[1]


# Files the review ran through the real parser, and others like them. Laws 1 and 4
# and the tiling are checked of each, and a good part are looked at again below.
FILES = [
    "", "\n", "\n\n", "   \n", "plain", "plain\n", "a\n\nb", "a\r\nb\r\n", "a\rb\r\rc\r", "\ufeffa\n", "\ufeff",
    "- a\n\tb\n", "- a\n  b\tc  \n", "- a\n\t```py\n\tif x:\n\t\ty\n\t```\n", "- a\n   ```\n   x\n ```\n- b\n",
    "- ```py\n  x\n\n  y\n- z\n", "- ```py\n  x\n- y\n\ntail\n", "para\n\n```\ncode\n\nmore", "$$\nx\ny\n\n- z",
    "- is $$\n  x\n  $$\n  next\n", "- is $$\n$$\nnot closed\n", "- a $$ b\n  ```\n  x\n  ```\n",
    "- a\n\n      deep child\n      more\n  back at item\n- b\n", "- a\n\n    kid\n    next\n\n  after\n",
    "- a\n\n  b\n\n  c\n", "- \n- [ ] \n1. \n", "-\n- x\n", "- # H\n  text\n  more\n- - x\n  y\n- 1. x\n   y\n",
    "- - - x\n  - y\n", "- > q\n  > r\n  plain\n", "1) a\n2) b\n   c\n", "* [X] done\n+ [ ]   two\n",
    "- a\n  # H\n  b\n- c\n", "- a\n# H\n- c\n", "Title\n=====\n\nSub\n---\ntext\n",
    "a\n\n---\n\nb\n\n***\n\n- - -\n\n* * *\n", "a\n---\nb\n", "a\n   \nb\n", "- a\n   \n- b\n",
    "Intro:\n- a\n- b\nafter\n", "para\n> quote\nmore\n", "text\n```\ncode\n```\nafter\n",
    "para\n| a | b |\n|---|---|\n| 1 | 2 |\nafter\n", "    code\n    more\n\ntext\n", "  a\n  b\n",
    "<div>\n- x\n\ny\n</div>\n", "[^1]: first\n    second\n\n    third\n", "> - a\n> - b\n", "- a\nlazy\n",
    "> a\n\n> b\n", "> [!quote] t\n> q\nnote\n", "> [!quote] [[P.pdf#page=3|p. 3]]\n> quoted\n>\n> note\n\n^a1b2c3\n",
    "> [!quote] t\n> - x\n> ^abc\n> $$\n> ```\n", "> [!quote] t\n> $$\n\n$$\nfoo\n$$\n",
    "- a\n  > [!quote] t\n  > q\n  >\n  > note\n\n  ^abc\n- b\n", "- a\n  - > [!quote] t\n    > q\n\n    ^abc\n  - b\n",
    "- h\n  > [!quote] t\n  > q\n  - kid\n", "text ^abc\n", "- a\n- b\n\n^abc\n\npara\n", "- a\n^abc\n- b\n",
    "- a ^abc\n  more\n", "- ^abc\n", "- \n  ^abc\n", "# Head ^abc\n", "---\nfoo\n", "---\n\nabc\n\n---\nrest\n",
    "\ufeff---\na: 1\n---\nbody\n", "---\r\na: 1\r\n---\r\nbody\r\n", "a\x0cb\n\x0c\n", "a\x85b\n", "- a\u2028b\n",
    "\u00a0\u00a0x\n", "a\n\u00a0\nb\n", "1. step\n\n    explain\n\n2. next\n", "- a\n\n\tpara under a\n- b\n",
    "  # H\n\ntext\n", "- * x\n", "- # H\n  - kid\n  text\n", "- a\n\n\n  b\n", "- a\n\n      kid\n  $$x$$\n",
    "- # H\n\n      deep\n      ```\n      x\n      ```\n", "---\n---\n\nfoo\n\n---\n\nbar\n",
    "---\ntitle: x\n", "--- \na: 1\n...\nbody", "---\na: 1\n---", "    - x\n    # y\n    ```\n    z\n\nnext\n",
    "10. a\n  - kid\n", "<!--\n- x\n\ny\n-->\n", "%%\n- x\n\ny\n%%\n", "It was the year\n1986. A great year\nfor all\n",
    "- ```\n  x\n", "- ```\n  x\n\nNext\n", "```\ncode\n", "$$\n", "- $$\n  x\n", "x ^2\n", "^2\n", "^abc\nnext typed\n",
    "- p\n  - k\n\n^abc\n", "- b\n\n^abc\n", "- p\n  - k\n\n^abc\n\nq\n", "a ^x\n\nb ^X\n", "\t\n\tcode\n",
]


@pytest.mark.parametrize("raw", FILES, ids=[repr(f)[:40] for f in FILES])
def test_every_small_file_comes_back_whole(raw):
    assert check_read(parse(raw), raw) == []


# --- the classes of block --------------------------------------------------------

@pytest.mark.parametrize("raw,kind,content", [
    ("- a\n", "item", "- a"),
    ("* a\n", "item", "- a"),
    ("3) a\n", "item", "3) a"),
    ("* [X]  a\n", "item", "- [x] a"),
    ("- # H\n", "wrapped", "# H"),
    ("- - x\n", "wrapped", "- x"),
    ("- > q\n", "wrapped", "> q"),
    ("- ```py\n  x\n  ```\n", "wrapped", "```py\nx\n```"),
    ("plain\n", "plain", "plain"),
    ("  plain  \n", "plain", "plain"),
    ("# H\n", "heading", "# H"),
    ("```py\n  x  \n```\n", "fence", "```py\n  x  \n```"),
    ("~~~\nx\n~~~\n", "fence", "~~~\nx\n~~~"),
    ("$$\nx\n$$\n", "math", "$$\nx\n$$"),
    ("    code  \n\n    more\n", "verbatim", "    code  \n\n    more"),
    ("> q\n> r\n", "quote", "> q\n> r"),
    ("| a |\n|---|\n", "table", "| a |\n|---|"),
    ("a | b\n---|---\n", "table", "a | b\n---|---"),
])
def test_a_block_knows_its_class_and_its_text(raw, kind, content):
    doc = parse(raw)
    node = doc.flat[0]
    assert (node.span.kind, node.content) == (kind, content)
    assert "".join(doc.lines[node.span.first:node.span.last]) == raw


@pytest.mark.parametrize("raw,marker", [
    ("+ [ ]   two\n", "+ [ ]   "), ("* a\n", "* "), ("-   a\n", "-   "), ("12)  a\n", "12)  "), ("- [x]\n", "- [x]"),
    ("  * [X]\tdone\n", "* [X]\t"), ("- # H\n", "- "),
])
def test_a_marker_is_kept_as_written(raw, marker):
    assert parse(raw).flat[0].span.marker == marker


def test_spans_say_where_every_line_came_from():
    span = parse("- a\n\n\n  b\n").flat[0].span
    assert (span.first, span.last, span.sources) == (0, 4, [0, 1, 3])
    node = parse("x\n\n- a\n\n      deep child\n      more\n  back at item\n- b\n").flat[2]
    assert (node.content, node.span.first, node.span.last) == ("deep child\nmore\nback at item", 4, 7)


def test_a_blocks_own_lines_are_one_run():
    # An item's text around its child would break the run: all of it is a child.
    assert contents(tree_of(parse("- a\n\n      kid\n  $$x$$\n"))) == [("- a", ["kid", "$$x$$"])]
    assert contents(tree_of(parse("- # H\n\n      deep\n      ```\n      x\n      ```\n"))) == [
        ("# H", ["deep", "```\nx\n```"])]
    assert contents(tree_of(parse("- # H\n  - kid\n\n  more\n"))) == [("# H", ["- kid", "more"])]


def test_spans_mode_keeps_a_block_to_its_lines():
    assert parse("- ```\n  x\n").flat[0].content == "```\nx"
    assert parse("```\ncode\n").flat[0].content == "```\ncode"
    assert md_to_blocks("- ```\n  x\n")[0]["content"] == "```\nx\n"       # the importer's own reading stands


def test_what_stands_four_columns_in_is_one_block_of_raw_lines():
    assert contents(tree_of(parse("    - x\n    # y\n    ```\n    z\n\nnext\n"))) == [
        "    - x\n    # y\n    ```\n    z", "next"]
    assert contents(tree_of(parse("# H\n\n    code\n\n\n    more\n\nnext\n"))) == [
        ("# H", ["    code\n\n\n    more", "next"])]
    assert contents(tree_of(parse("para\n    not code\n"))) == ["para\nnot code"]    # no blank line above
    assert contents(tree_of(parse("- a\n\n    kid\n"))) == [("- a", ["kid"])]         # under a list it is a child


def test_a_dollar_pair_left_open_in_an_item_ends_at_a_blank_line_and_the_left_edge():
    assert contents(tree_of(parse("- is $$\n\n$$\nfoo\nbar$$ tail\n"))) == ["- is $$", "$$\nfoo\nbar$$ tail"]
    assert contents(tree_of(parse("- is $$\n$$\nno closer\n"))) == ["- is $$\n$$", "no closer"]


def test_line_ends_are_the_parsers_and_no_others():
    for raw in ("a\x0cb\nc\n", "a\x85b\nc\n", "a\u2028b\nc\n", "a\x0bb\nc\n", "a\x1cb\nc\n"):
        doc = parse(raw)
        assert len(doc.lines) == 2 and doc.flat[0].span.last == 2
    assert parse("a\r\r\nb").lines == ["a\r", "\r\n", "b"]


def test_a_file_knows_its_ending_and_its_habits():
    assert parse("a\r\nb\r\nc\n").eol == "\r\n"
    assert parse("a\rb\r").eol == "\r"
    assert parse("").eol == "\n"
    assert parse("* a\n    * b\n").bullet == "*"
    assert parse("* a\n    * b\n").unit == "    "
    assert parse("- a\n\t- b\n").unit == "\t"


# --- an edit keeps the dress -------------------------------------------------------

def test_a_marker_comes_back_when_its_kind_is_the_same():
    assert edited("+ [ ]   two\n", "- [ ] two", "- [ ] two more") == "+ [ ]   two more\n"
    assert edited("+ [ ]   two\n", "- [ ] two", "- [x] two") == "+ [x]   two\n"
    assert edited("* [X] done\n", "- [x] done", "- [x] done well") == "* [X] done well\n"
    assert edited("3.  x\n", "3. x", "4. x") == "4.  x\n"
    assert edited("*   a\n", "- a", "- b") == "*   b\n"
    assert edited("- [ ]\n", "- [ ] ", "- [ ] text") == "- [ ] text\n"


def test_a_new_kind_of_marker_is_written_as_it_is():
    assert edited("*   a\n", "- a", "1. a") == "1. a\n"
    assert edited("* a\n", "- a", "- [ ] a") == "- [ ] a\n"


def test_an_edit_leaves_a_child_left_of_a_numbers_text_alone():
    """Two columns under ``1.`` is a child here, and in many a file. Its neighbour
    must not be moved to a column no new marker asked for."""
    assert edited("1. n\n  - k\n  y\n", "- k", "- k word") == "1. n\n  - k word\n  y\n"


def test_every_kind_of_dress_stays_on_an_edited_line():
    assert edited("hello  \n", "hello", "hello world") == "hello world  \n"       # a hard break
    assert edited("  hi\n", "hi", "hi there") == "  hi there\n"
    assert edited("hello\r\n\nnext\n", "hello", "hello world") == "hello world\r\n\nnext\n"
    assert edited("# H  \n", "# H", "# Hi") == "# Hi  \n"
    assert edited(" >  q  \n > r\n", ">  q\n> r", ">  q2\n> r") == " >  q2  \n > r\n"
    assert edited("| a |  \n|---|\n", "| a |\n|---|", "| b |\n|---|") == "| b |  \n|---|\n"
    assert edited("$$\n  x\n$$\n", "$$\nx\n$$", "$$\ny\n$$") == "$$\n  y\n$$\n"


def test_lines_of_a_block_not_edited_stay_as_they_were():
    raw = "- a\n    deep  \n\n\n  far\n"
    doc, ids, tree = read(raw)
    assert tree[0]["content"] == "- a\n    deep\n\n  far"
    tree[0]["content"] = "- changed\n    deep\n\n  far"
    assert shape(doc, ids, tree)[1] == "- changed\n    deep  \n\n\n  far\n"


def test_a_tabbed_fence_keeps_its_tabs_when_one_line_is_edited():
    raw = "- ```make\n\tall:\n\t\tgcc -o a\n\t```\n"
    doc, ids, tree = read(raw)
    assert tree[0]["content"] == "```make\n  all:\n  \tgcc -o a\n  ```"          # the item's two columns go
    tree[0]["content"] = "```make\n  all:\n  \tgcc -o b\n  ```"
    assert shape(doc, ids, tree)[1] == "- ```make\n\tall:\n\t\tgcc -o b\n\t```\n"
    assert edited("```\n\tx\n```\n", "```\n\tx\n```", "```\n\ty\n```") == "```\n\ty\n```\n"


def test_an_added_line_stays_in_its_block():
    assert edited("- a\n", "- a", "- a\n  b") == "- a\n  b\n"
    assert edited("- a\n", "- a", "- a\nb") == "- a\n  b\n"
    assert edited("  - a\n", "- a", "- a\n  b") == "  - a\n    b\n"
    assert edited("- # H\n", "# H", "# H\nb") == "- # H\n  b\n"
    assert edited("  x\n", "x", "x\ny") == "  x\n  y\n"
    assert edited("- ```\n  x\n  ```\n", "```\nx\n```", "```\nx\ny\n```") == "- ```\n  x\n  y\n  ```\n"


def test_a_line_added_to_a_last_line_without_a_newline():
    assert edited("a\n\nb", "b", "b\nc") == "a\n\nb\nc"
    assert edited("a\r\n\r\nb", "b", "b\nc") == "a\r\n\r\nb\r\nc"


def test_a_line_taken_out_goes_and_the_rest_stay():
    assert edited("a  \nb\nc  \n", "a\nb\nc", "a\nc") == "a  \nc  \n"


def test_a_new_class_is_written_as_new():
    assert edited("- p\n  - a\n", "- a", "# a") == "- p\n  - # a\n"           # review 6: `  # a` would fold into p
    assert edited("- # H\n", "# H", "plain") == "plain\n"                      # `- plain` is another block's text
    assert edited("- p\n  - # H\n", "# H", "plain") == "- p\n  - plain\n"
    assert edited("a\n\nb\n", "b", "- b") == "a\n\n- b\n"


def test_retyping_a_heading_leaves_its_section_alone():
    assert edited("# H\n\npara\n\n- item\n", "# H", "H") == "H\n\npara\n\n- item\n"
    assert edited("# H\n\npara\n", "# H", "## H") == "## H\n\npara\n"
    assert edited("# H\n\npara\n", "# H", "- H") == "- H\n\npara\n"


def test_an_item_under_a_heading_retyped_keeps_its_child():
    assert edited("# H\n\n- a\n  - kid\n", "- a", "a") == "# H\n\n- a\n  - kid\n"


def test_a_new_marker_moves_a_child_only_if_it_must():
    assert edited("10. a\n    - kid\n", "10. a", "- a") == "- a\n    - kid\n"      # four still clears two
    assert edited("- a\n  - kid\n", "- a", "10. a") == "10. a\n    - kid\n"        # two is left of the text now


# --- anchors ---------------------------------------------------------------------

def test_an_anchor_is_read_off_a_lines_end():
    for raw, content, anchor in (("text ^abc\n", "text", "abc"), ("# Head ^abc\n", "# Head", "abc"),
                                 ("- a ^abc\n", "- a", "abc"), ("- a\n  b ^abc\n", "- a\n  b", "abc"),
                                 ("two\nlines ^abc\n", "two\nlines", "abc"), ("- # H ^abc\n", "# H", "abc"),
                                 ("text  ^abc  \n", "text", "abc")):
        node = parse(raw).flat[0]
        assert (node.content, node.anchor) == (content, anchor), raw


def test_an_anchor_needs_a_letter_and_text_before_it():
    for raw, content in (("x ^2\n", "x ^2"), ("E = mc ^2\n", "E = mc ^2"), ("- ^abc\n", "- ^abc"), ("# ^abc\n", "# ^abc"),
                         ("- [ ] ^abc\n", "- [ ] ^abc"), ("- - ^abc\n", "- ^abc")):
        node = parse(raw).flat[0]
        assert (node.content, node.anchor) == (content, ""), raw
    assert contents(tree_of(parse("a\n\n^2\n"))) == ["a", "^2"]


@pytest.mark.parametrize("raw", ["> q ^abc\n", "| a ^abc |\n|---|\n| 1 | ^abc\n", "```\nx ^abc\n```\n", "$$\nx ^abc\n$$\n",
                                 "- > q ^abc\n", "    x ^abc\n", "```\ncode ^abc\n", "```\ncode\n^abc\n", "- ```\n  x ^abc\n"])
def test_only_text_takes_an_anchor_at_a_lines_end(raw):
    node = parse(raw).flat[0]
    assert node.anchor == "" and "^abc" in node.content


@pytest.mark.parametrize("raw,content", [("> q\n\n^abc\n", "> q"), ("| a |\n|---|\n\n^abc\n", "| a |\n|---|"),
                                         ("```\nx\n```\n\n^abc\n", "```\nx\n```"), ("$$\nx\n$$\n\n^abc\n", "$$\nx\n$$"),
                                         ("- a\n- b\n\n^abc\n", "- b"), ("    code\n\n^abc\n", "    code")])
def test_a_compound_block_takes_it_on_its_own_line(raw, content):
    doc = parse(raw)
    assert (doc.flat[-1].content, doc.flat[-1].anchor) == (content, "abc")
    assert [tile.kind for tile in doc.tiles][-1] == "anchor"


def test_an_anchor_in_an_items_lines_keeps_the_children_below():
    tree = tree_of(parse("- > [!quote] t\n  > q\n\n  ^abc\n  - kid\n- b\n"))
    assert tree[0]["anchor"] == "abc"
    assert contents(tree) == [("> [!quote] t\n> q", ["- kid"]), "- b"]


def test_an_anchor_edit_never_doubles_it():
    assert edited("text ^abc\n", "text", "text\nmore") == "text\nmore ^abc\n"      # review 7a
    assert edited("text ^abc\n", "text", "new words") == "new words ^abc\n"
    assert edited("text  ^abc  \n", "text", "new") == "new  ^abc  \n"
    assert edited("- a\n  b ^abc\n", "- a\n  b", "- a\n  c") == "- a\n  c ^abc\n"
    assert edited("# H ^abc\n", "# H", "# I") == "# I ^abc\n"


def test_an_own_line_anchor_stays_on_its_line():
    assert edited("> q\n\n^abc\n\nnext\n", "> q", "> r") == "> r\n\n^abc\n\nnext\n"
    assert edited("- a\n\n  ^abc\n- b\n", "- a", "- c") == "- c\n\n  ^abc\n- b\n"
    assert edited("- \n  ^abc\n", "- ", "- x") == "- x\n  ^abc\n"


def test_an_anchor_after_a_subtree_is_the_tile_of_its_block():
    raw = "- p\n  - k\n\n^abc\n\nnext\n"
    doc, ids, tree = read(raw)
    assert tree[0]["anchor"] == "abc"
    assert without(raw, "- k") == "- p\n\n^abc\n\nnext\n"                            # review 7d: p keeps it
    assert child_added("- b\n\n^abc\n", "- b", "- new") == "- b\n  - new\n\n^abc\n"   # the child comes before it


def test_a_file_can_hold_no_more_than_one_anchor_for_a_block():
    assert contents(tree_of(parse("a ^x\n\n^y\n"))) == ["a", "^y"]


def test_the_first_of_a_copied_anchor_wins_ignoring_case():
    tree = tree_of(parse("a ^abc\n\nb ^ABC\n\n> q\n\n^Abc\n"))
    assert tree[0]["anchor"] == "abc"
    assert contents(tree) == ["a", "b ^ABC", "> q", "^Abc"]


def test_an_anchor_added_is_written_where_its_class_takes_it():
    def added(raw, content):
        doc, ids, tree = read(raw)
        node_with(tree, content)["anchor"] = "abc"
        shaped, text = shape(doc, ids, tree)
        assert node_with(shaped, content)["anchor"] == "abc"
        return text

    assert added("text\n", "text") == "text ^abc\n"
    assert added("text  \nnext\n\nb\n", "text\nnext") == "text  \nnext ^abc\n\nb\n"
    assert added("- a\n- b\n", "- a") == "- a ^abc\n- b\n"
    assert added("# H\n\npara\n", "# H") == "# H ^abc\n\npara\n"
    assert added("> q\n", "> q") == "> q\n\n^abc\n"
    assert added("```\nx\n```\n\nnext\n", "```\nx\n```") == "```\nx\n```\n\n^abc\n\nnext\n"
    assert added("| a |\n|---|\n", "| a |\n|---|") == "| a |\n|---|\n\n^abc\n"
    assert added("- ", "- ") == "- \n  ^abc"
    assert added("- > q\n- b\n", "> q") == "- > q\n\n  ^abc\n- b\n"                 # straight after, a lazy line of q
    assert added("- a\n  ```\n  x\n  ```\n", "```\nx\n```") == "- a\n  ```\n  x\n  ```\n\n  ^abc\n"
    assert added("    code\n", "    code") == "    code\n\n^abc\n"


def test_an_anchor_is_never_taken_away_by_a_tree_without_it():
    doc, ids, tree = read("text ^abc\n")
    del tree[0]["anchor"]
    assert shape(doc, ids, tree)[1] == "text ^abc\n"


def test_an_anchor_outlives_a_new_class():
    assert edited("text ^abc\n\nnext\n", "text", "> text") == "> text\n\n^abc\n\nnext\n"
    # On a heading's way to a quote it stays with the block, above the section.
    assert edited("# H ^abc\n\npara\n", "# H", "> q") == "> q\n\n^abc\n\npara\n"


# --- separators -------------------------------------------------------------------

@pytest.mark.parametrize("prev,next_kind,line,blanks", [
    ("plain", "plain", "x", 1), ("heading", "plain", "x", 0), ("heading", "heading", "# x", 0),
    ("heading", "item", "- x", 0), ("item", "item", "- x", 0), ("item", "wrapped", "- # x", 0),
    ("wrapped", "item", "- x", 0),
    # Lazy continuation: a line straight after these joins them in CommonMark,
    # though this parser reads two blocks.
    ("item", "plain", "x", 1), ("wrapped", "plain", "x", 1), ("quote", "plain", "x", 1),
    # A table runs to the next blank line.
    ("table", "plain", "x", 1),
    # What can interrupt a paragraph: a list, if it has text and, numbered, starts at 1.
    ("plain", "item", "- x", 0), ("plain", "item", "1. x", 0), ("plain", "item", "2. x", 1), ("plain", "item", "- ", 1),
    ("plain", "item", "- [ ] x", 0), ("plain", "item", "01. x", 1),
    # Setext headings.
    ("plain", "plain", "---", 1), ("plain", "plain", "===", 1), ("heading", "plain", "---", 0),
    ("fence", "plain", "x", 1), ("math", "plain", "x", 1), ("plain", "heading", "# x", 1), ("plain", "quote", "> x", 1),
    ("plain", "fence", "```", 1), ("plain", "verbatim", "    x", 1), ("heading", "verbatim", "    x", 1),
])
def test_required_is_what_commonmark_asks(prev, next_kind, line, blanks):
    assert required(prev, next_kind, line) == blanks


def test_a_deletion_never_merges_its_neighbours():
    assert without("Intro\n- a\nAfter\n", "- a") == "Intro\n\nAfter\n"
    assert without("para\n- a\n\nnext\n", "- a") == "para\n\nnext\n"
    assert without("a\n\n\n\nb\n\nc\n", "b") == "a\n\n\n\nc\n"                   # the larger of the two gaps
    assert without("a\n\nb\n\n\n\nc\n", "b") == "a\n\n\n\nc\n"


def test_a_deletion_at_the_end_leaves_no_blank_tail():
    assert without("a\n\nb\n", "b") == "a\n"
    assert without("a\n\nb\n\n\n", "b") == "a\n"
    assert without("a\n\nb", "b") == "a"
    assert without("only\n", "only") == ""


def test_the_tail_of_a_file_stays_when_its_last_block_does():
    assert without("a\n\nb\n\n\n", "a") == "b\n\n\n"


def test_moving_a_block_gives_it_the_blank_lines_it_needs():
    doc, ids, tree = read("A\n\nB\n\nC")
    tree.insert(1, tree.pop(2))
    assert shape(doc, ids, tree)[1] == "A\n\nC\n\nB"
    doc, ids, tree = read("para\n\n- a\n- b\n")
    para = tree.pop(0)
    tree.insert(1, para)
    text = shape(doc, ids, tree)[1]
    assert text == "- a\n\npara\n\n- b\n"                                         # no lazy line in a's bullet


def test_a_new_block_keeps_clear_of_what_it_stands_beside():
    assert inserted("- a\n- b\n", "- b", "text") == "- a\n- b\n\ntext\n"
    assert inserted("para\n", "para", "> [!quote] t\n> q") == "para\n\n> [!quote] t\n> q\n"
    assert child_added("# H\n", "# H", "new") == "# H\nnew\n"                     # nothing is needed after a heading


def test_a_list_stays_as_tight_or_as_loose_as_it_was():
    assert inserted("- a\n- b\n\nnext\n", "- b", "- c") == "- a\n- b\n- c\n\nnext\n"
    assert inserted("- a\n\n- b\n", "- b", "- c") == "- a\n\n- b\n\n- c\n"
    doc, ids, tree = read("para\n\n- a\n")
    tree.insert(1, {"id": "new", "content": "- new", "children": []})
    assert shape(doc, ids, tree)[1] == "para\n\n- new\n- a\n"


def test_a_file_keeps_its_ending_and_its_last_newline():
    assert inserted("a\r\n", "a", "b") == "a\r\n\r\nb\r\n"
    assert inserted("a", "a", "b") == "a\n\nb"
    assert inserted("\ufeffa\n", "a", "b") == "\ufeffa\n\nb\n"
    assert inserted("a\rb\r\rc\r", "c", "d") == "a\rb\r\rc\r\rd\r"
    doc, ids, tree = read("A\n\nB")
    tree.reverse()
    assert shape(doc, ids, tree)[1] == "B\n\nA"


def test_blocks_added_to_a_blank_file_keep_its_blank_lines_once():
    assert write(parse("\n\n"), [], [{"id": "a", "content": "new", "children": []}])[0] == "\n\nnew\n"
    assert write(parse(""), [], [{"id": "a", "content": "new", "children": []}])[0] == "new\n"


# --- children ---------------------------------------------------------------------

def test_a_block_that_cannot_hold_children_becomes_an_item():
    assert child_added("p\n", "p", "- kid") == "- p\n  - kid\n"
    assert child_added("p\nq\n\nnext\n", "p\nq", "kid") == "- p\n  q\n  - kid\n\nnext\n"
    assert child_added("> q\n> r\n", "> q\n> r", "- kid") == "- > q\n  > r\n  - kid\n"
    assert child_added("| a |\n|---|\n", "| a |\n|---|", "- kid") == "- | a |\n  |---|\n  - kid\n"
    assert child_added("```\nx\n```\n", "```\nx\n```", "- kid") == "- ```\n  x\n  ```\n  - kid\n"


def test_shape_says_the_file_holds_a_child_as_a_list_item():
    doc, ids, tree = read("p\n")
    tree[0]["children"].append({"id": "new", "content": "plain kid", "children": []})
    shaped, text = shape(doc, ids, tree)
    assert text == "- p\n  - plain kid\n"
    assert shaped == [{"id": "b0", "content": "- p", "children": [{"id": "new", "content": "- plain kid", "children": []}]}]


def test_a_plain_child_a_person_wrote_stays_while_its_context_holds():
    raw = "- a\n\n    kid\n\n- b\n"
    assert edited(raw, "- b", "- c") == "- a\n\n    kid\n\n- c\n"
    assert edited(raw, "kid", "kid two") == "- a\n\n    kid two\n\n- b\n"


def test_an_inserted_child_stands_past_its_parents_text():
    assert child_added("10. a\n", "10. a", "- kid") == "10. a\n    - kid\n"           # review 5: `  - kid` is no child in Obsidian
    assert child_added("1000. a\n", "1000. a", "- kid") == "1000. a\n      - kid\n"
    assert child_added("- a\n", "- a", "- kid") == "- a\n  - kid\n"


def test_a_child_is_dressed_like_its_siblings_else_like_the_file():
    assert child_added("* a\n    * b\n", "- a", "- c") == "* a\n    * b\n    * c\n"
    assert child_added("* a\n    * b\n* d\n", "- d", "- c") == "* a\n    * b\n* d\n    * c\n"
    assert child_added("- a\n\t- b\n- c\n", "- c", "- d") == "- a\n\t- b\n- c\n\t- d\n"
    assert inserted("+ a\n", "- a", "- b") == "+ a\n+ b\n"


def test_a_list_moved_deeper_goes_with_its_children():
    doc, ids, tree = read("- a\n- b\n  - c\n")
    b = tree.pop(1)
    tree[0]["children"].append(b)
    shaped, text = shape(doc, ids, tree)
    assert text == "- a\n  - b\n    - c\n"
    assert contents(shaped) == [("- a", [("- b", ["- c"])])]


def test_a_lifted_child_comes_to_the_left_edge():
    doc, ids, tree = read("- a\n  - b\n")
    tree[:] = tree[0]["children"]
    assert shape(doc, ids, tree)[1] == "- b\n"


def test_text_a_list_item_held_after_a_deleted_sibling_stays_a_block():
    # Spans mode makes `  kid` a child after `- b`; with b gone it would be a's text.
    assert without("- a\n  - b\n\n  kid\n", "- b") == "- a\n\n    kid\n"
    doc, ids, tree = read("- a\n  - b\n\n  kid\n")
    assert contents(tree) == [("- a", ["- b", "kid"])]


def test_code_below_a_wrapping_item_stays_a_child():
    text = without("- # H\n  - b\n\n  ```\n  x\n  ```\n", "- b")
    assert contents(tree_of(parse(text))) == [("# H", ["```\nx\n```"])]
    text = without("- a\n  - b\n  ```\n  x\n  ```\n", "- b")
    assert text == "- a\n\n  ```\n  x\n  ```\n"                                  # the fence's own lines stay as they were


def test_siblings_of_two_indents_stay_siblings_when_their_order_changes():
    doc, ids, tree = read("- a\n    - x\n  - y\n")
    tree[0]["children"].reverse()
    shaped, text = shape(doc, ids, tree)
    assert contents(shaped) == [("- a", ["- y", "- x"])]
    assert text == "- a\n  - y\n  - x\n"


# --- indentation a list would take --------------------------------------------------

def test_a_list_put_before_indented_text_does_not_swallow_it():
    doc, ids, tree = read("  c\n")
    tree.insert(0, {"id": "new", "content": "- x", "children": []})
    shaped, text = shape(doc, ids, tree)
    assert text == "- x\n\nc\n"
    assert [n["id"] for n in shaped] == ["new", "b0"]


def test_what_stood_four_columns_in_under_a_paragraph_is_not_code_when_it_goes():
    assert without("a\n    - d\n", "a") == "   - d\n"
    assert without("a\n    - d\n    w\n\nnext\n", "- d") == "a\n\n   w\n\nnext\n"
    assert contents(tree_of(parse("a\n    - d\n"))) == ["a", "- d"]


def test_a_list_brought_left_comes_whole():
    """To CommonMark ``- k`` stands in ``- x``, past its lazy line: brought to
    the left edge it is a list of its own, which the check refuses."""
    doc, ids, tree = read("- x\na\n  - k\n  - l\n")
    assert contents(tree) == ["- x", "a", "- k", "- l"]
    del tree[1]
    text = write(doc, ids, tree)[0]
    assert text == "- x\n- k\n- l\n"
    assert contents(tree_of(parse(text))) == ["- x", "- k", "- l"]
    with pytest.raises(Unwritable, match=MEANING):
        shape(doc, ids, tree)


def test_a_tab_past_the_column_a_block_moves_from_keeps_the_rest_of_its_width():
    doc, ids, tree = read("- x\na\n  - k\n\tt\n")
    assert tree[2]["content"] == "- k\n  t"
    del tree[1]
    text = write(doc, ids, tree)[0]
    assert text == "- x\n- k\n  t\n"
    assert contents(tree_of(parse(text))) == ["- x", "- k\n  t"]
    with pytest.raises(Unwritable, match=MEANING):            # it stood in ``- x`` (above)
        shape(doc, ids, tree)


def test_a_block_moved_over_stays_one_block_whatever_its_lines_stand_at():
    assert md_lens._repad(["      deep\n", "      more\n", "  back\n", "\n"], 6, 4) == [
        "    deep\n", "    more\n", "    back\n", "\n"]
    assert md_lens._repad(["\tt\n"], 2, 0) == ["  t\n"]
    doc, ids, tree = read("- p\n\n      deep\n      more\n  back\n")
    tree[0]["children"].insert(0, {"id": "new", "content": "- new", "children": []})
    assert contents(tree_of(parse(write(doc, ids, tree)[0]))) == [("- p", ["- new", "deep\nmore\nback"])]
    with pytest.raises(Unwritable, match=MEANING):          # code and a paragraph to CommonMark, then one paragraph
        shape(doc, ids, tree)


def test_a_list_stays_open_past_what_went_under_it():
    # The heading's text stands a column past the list, out of it, and the
    # next indented block must still be kept out of it.
    doc, ids, tree = read("  # H\n\n one\n\n    code\n")
    tree.insert(0, {"id": "new", "content": "- x", "children": []})
    shaped, text = shape(doc, ids, tree)
    assert text == "- x\n\n # H\n\n one\n\n    code\n"                             # ` one` closes the list
    assert contents(shaped) == ["- x", "# H", "one", "    code"]


def test_text_whose_indent_holds_it_back_from_a_heading_is_no_heading_at_the_left_edge():
    doc, ids, tree = read("  # H\n\ntext\n")
    assert contents(tree) == ["# H", "text"]                                     # review 2: never parents
    tree.insert(0, {"id": "new", "content": "- x", "children": []})
    shaped, text = shape(doc, ids, tree)
    assert text == "- x\n\n # H\n\ntext\n"
    assert contents(shaped) == ["- x", "# H", "text"]                            # out of the list, held back


def test_code_by_indentation_after_a_list_becomes_a_fence():
    doc, ids, tree = read("para\n\n    code\n    ```\n")
    assert without("- a\n\npara\n\n    code\n    ```\n", "para") == "- a\n\n````\ncode\n```\n````\n"


def test_code_by_indentation_under_a_list_becomes_a_fence():
    doc, ids, tree = read("- a\n\n    code\n\nnext\n")
    assert contents(tree) == [("- a", ["code"]), "next"]
    doc, ids, tree = read("para\n\n    code\n")
    code = tree.pop(1)
    tree[0]["children"].append(code)
    assert shape(doc, ids, tree)[1] == "- para\n  - ```\n    code\n    ```\n"


def test_two_runs_of_code_side_by_side_stay_two_blocks():
    doc, ids, tree = read("    one\n\ntext\n\n    two\n")
    del tree[1]
    shaped, text = shape(doc, ids, tree)
    assert text == "    one\n\n```\ntwo\n```\n"
    assert [n["id"] for n in shaped] == ["b0", "b2"]


def test_four_columns_typed_into_a_paragraph_is_code_by_a_new_class():
    assert edited("- a\nb\n", "b", "    b") == "- a\n```\nb\n```\n"              # a fence breaks into the list


def test_a_divider_a_file_comes_to_open_with_is_kept_from_opening_front_matter():
    assert without("---\n\nIntro\n\n---\n\nmore\n", "Intro") == "***\n\n---\n\nmore\n"
    assert without("para\n\n---\n\ntitle: x\n\n---\n", "para") == "***\n\ntitle: x\n\n---\n"


# --- shape ------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "1. step\n\n    explain\n\n2. next\n", "- a\n\n\tpara\n", "- * x\n", "  # H\n\ntext\n", "- # H\n\ntext\n",
    "```py\ndef a():\n    pass\n\n\ndef b():\n    pass\n```\n\nx\n",
])
def test_shape_is_a_fixed_point_on_what_parse_gives(raw):
    """Review 2: over the corpus a render-based shape moved 17 files of 146."""
    doc, ids, tree = read(raw)
    assert shape(doc, ids, tree) == (tree, raw)


def test_an_edit_leaves_the_rest_of_such_a_file_alone():
    raw = "1. step\n\n    explain\n\n2. next\n\npara\n"
    doc, ids, tree = read(raw)
    tree[-1]["content"] = "para two"
    text = shape(doc, ids, tree)[1]
    assert text == "1. step\n\n    explain\n\n2. next\n\npara two\n"


@pytest.mark.parametrize("content,parts", [
    ("- a\n- b", ["- a", "- b"]),
    ("a\n- b", ["a", "- b"]),
    ("a\n```\nx\n```", ["a", "```\nx\n```"]),
    ("a\n\nb", ["a", "b"]),
])
def test_a_block_a_file_reads_as_two_splits(content, parts):
    doc, ids, tree = read("x\n\nz\n")
    tree[0]["content"] = content
    shaped, _ = shape(doc, ids, tree)
    assert [n["content"] for n in shaped] == [*parts, "z"]
    assert [n["id"] for n in shaped] == ["b0", *[None] * (len(parts) - 1), "b1"]


def test_a_heading_with_text_under_it_splits_into_its_child():
    doc, ids, tree = read("x\n\nz\n")
    tree[0]["content"] = "# H\ntext"
    shaped, text = shape(doc, ids, tree)
    assert text == "# H\ntext\n\nz\n"
    assert contents(shaped) == [("# H", ["text", "z"])]


def test_ids_go_by_line_not_by_likeness():
    doc, ids, tree = read("same\n\nsame\n\nother\n")
    tree[1]["content"] = "same\n\nsame"
    shaped, _ = shape(doc, ids, tree)
    assert [n["id"] for n in shaped] == ["b0", "b1", None, "b2"]


@pytest.mark.parametrize("opener", ["```", "~~~", "```python\ndef f():", "$$", "$$\nx +"])
def test_a_block_held_open_in_the_middle_is_a_wait_not_a_loss(opener):
    """Review 1: a person typing a fence holds it open, and every block below it
    with its ids would be gone. Nothing is written until it closes."""
    raw = "intro\n\ntyping\n\n> [!quote] [[P.pdf#page=1|p. 1]]\n> q\n\nlast\n"
    doc, ids, tree = read(raw)
    tree[1]["content"] = opener
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        shape(doc, ids, tree)
    tree[1]["content"] = opener + "\nend" + ("\n```" if opener[0] == "`" else "\n~~~" if opener[0] == "~" else "\n$$")
    assert [n["id"] for n in shape(doc, ids, tree)[0]] == ["b0", "b1", "b2", "b3"]


def test_a_fence_typed_above_another_closes_on_it_and_is_still_held_back():
    doc, ids, tree = read("typing\n\ntext\n\n```\nx\n```\n")
    tree[0]["content"] = "```"
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        shape(doc, ids, tree)


def test_a_block_held_open_last_swallows_nothing():
    doc, ids, tree = read("intro\n\ntyping\n")
    tree[1]["content"] = "```"
    shaped, text = shape(doc, ids, tree)
    assert text == "intro\n\n```\n"
    assert [n["id"] for n in shaped] == ["b0", "b1"]


@pytest.mark.parametrize("raw", ["para\n\n```\ncode\n\nmore\n", "para\n\n$$\nx\n", "para\n\n- ```\n  x\n"])
def test_a_file_with_an_unclosed_block_takes_nothing_after_it(raw):
    doc, ids, tree = read(raw)
    tree.append({"id": "new", "content": "after", "children": []})
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        shape(doc, ids, tree)
    doc, ids, tree = read(raw)
    tree.insert(0, {"id": "new", "content": "before", "children": []})
    assert shape(doc, ids, tree)[1] == "before\n\n" + raw
    doc, ids, tree = read(raw)
    tree[-1]["anchor"] = "abc"                                                   # an anchor is something after it
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        shape(doc, ids, tree)


def test_an_unclosed_block_can_be_edited_and_closed():
    raw = "para\n\n```\ncode\n"
    assert edited(raw, "```\ncode", "```\ncode two") == "para\n\n```\ncode two\n"
    assert edited(raw, "```\ncode", "```\ncode\n```") == "para\n\n```\ncode\n```\n"


def test_whitespace_is_written_to_the_file_and_never_sent_back():
    """Review 4: a `set` from the disk would land under the caret and glue the
    next word on."""
    for raw, was, typed, text in (("hello\n", "hello", "hello ", "hello \n"),
                                  ("- hello\n", "- hello", "- hello ", "- hello \n"),
                                  ("a\nb\n", "a\nb", "a\n  b", "a\n  b\n"),
                                  ("hello\n", "hello", "hello\n", "hello\n")):
        doc, ids, tree = read(raw)
        node_with(tree, was)["content"] = typed
        shaped, written = shape(doc, ids, tree)
        assert written == text, typed
        assert tree_equal(tree, shaped), typed
        assert contents(shaped) == contents(tree_of(parse(raw)))


def test_whitespace_written_once_stays_as_it_is():
    for raw, typed in (("a\nb\n", "a\n  b"), ("hello\n", "hello "), ("- a\n  b\n", "- a\n      b")):
        doc, ids, tree = read(raw)
        tree[0]["content"] = typed
        text = shape(doc, ids, tree)[1]
        for _ in range(4):
            doc, ids, tree = read(text)
            tree[0]["content"] = typed
            assert shape(doc, ids, tree)[1] == text, typed


def test_blank_lines_in_a_block_do_not_grow_with_every_write():
    raw = "- a\n\n  b\n"
    doc, ids, tree = read(raw)
    tree[0]["content"] = "- a\n\n\n\n  b"
    assert shape(doc, ids, tree)[1] == raw


def test_tree_equal_is_blind_to_what_parse_normalises_alone():
    def tree(content):
        return [{"id": "a", "content": content, "children": []}]

    assert tree_equal(tree("hello "), tree("hello"))
    assert tree_equal(tree("a\n  b"), tree("a\nb"))
    assert tree_equal(tree("- a\n\n\n  b"), tree("- a\n\n  b"))
    assert tree_equal(tree("```\nx  \n```"), tree("```\nx\n```"))
    assert not tree_equal(tree("```\n  x\n```"), tree("```\nx\n```"))            # code keeps its indent
    assert not tree_equal(tree("    a"), tree("a"))
    assert not tree_equal(tree("a"), tree("b"))
    assert not tree_equal(tree("a"), [])
    assert not tree_equal([{"id": "a", "content": "a", "children": tree("b")}], [*tree("a"), *tree("b")])


def test_laws_two_and_six_on_a_block_of_each_class():
    raw = ("# H  \n\npara  \n\n*   a\n+ [X]   b\n\n> q\n\n| a |\n|---|\n\n```\n\tx  \n```\n\n$$\n  y\n$$\n\n"
           "    code\n\n- # w\n  more\n")
    doc, ids, tree = read(raw)
    for i, node in enumerate(flatten(tree)):
        was = node["content"]
        first = was.split("\n")[0]
        lines = was.split("\n")
        lines[-2 if len(lines) > 2 else 0] += " edit"
        node["content"] = "\n".join(lines)
        shaped, text = shape(doc, ids, tree)
        span = doc.flat[i].span
        assert text.startswith("".join(doc.lines[:span.first])), first
        assert text.endswith("".join(doc.lines[span.last:])), first
        assert "edit" in text
        read_doc, read_ids = parse(text), [n["id"] for n in flatten(shaped)]
        back = tree_of(read_doc, read_ids)
        next(n for n in flatten(back) if n["id"] == ids[i])["content"] = was
        assert shape(read_doc, read_ids, back)[1] == raw, first
        node["content"] = was


def test_an_empty_box_with_no_space_after_it_is_the_one_edit_with_no_way_back():
    """A counter-example to law 6, and why it cannot hold. ``- [ ]`` and
    ``- [ ] `` read as one block. Once text is typed the file says only
    ``- [ ] text``, and a revert writes the box as a person types one."""
    text = edited("- [ ]\n", "- [ ] ", "- [ ] text")
    assert edited(text, "- [ ] text", "- [ ] ") == "- [ ] \n"
    assert edited("- [ ] \n", "- [ ] ", "- [ ] text") == "- [ ] text\n"


# --- render ----------------------------------------------------------------------

def blocks(*texts) -> list:
    return [{"id": f"n{i}", "content": text, "children": []} for i, text in enumerate(texts)]


def test_a_new_file_keeps_blank_lines_inside_code():
    fence = "```py\ndef a():\n    pass\n\n\ndef b():\n    pass\n```"
    assert render(blocks(fence, "x")) == f"{fence}\n\nx\n"


def test_a_new_file_keeps_lists_tight_and_everything_else_apart():
    assert render(blocks("- a", "- b", "1. c", "para", "# H", "text")) == "- a\n- b\n1. c\n\npara\n\n# H\n\ntext\n"


def test_a_new_file_writes_children_as_a_list():
    tree = blocks("p")
    tree[0]["children"] = blocks("- a", "plain", "# H", "```\nx\n```")
    assert render(tree) == "- p\n  - a\n  - plain\n  - # H\n  - ```\n    x\n    ```\n"
    tree = blocks("# H")
    tree[0]["children"] = blocks("text", "- a")
    assert render(tree) == "# H\n\ntext\n\n- a\n"


def test_a_new_file_puts_each_anchor_where_it_is_read():
    tree = blocks("two\nlines", "> q", "- a\n  b")
    tree[0]["anchor"], tree[1]["anchor"], tree[2]["anchor"] = "one", "two", "three"
    assert render(tree) == "two\nlines ^one\n\n> q\n\n^two\n\n- a\n  b ^three\n"     # never on the first line


def test_a_new_file_opening_on_a_divider_has_no_front_matter():
    assert render(blocks("---", "a: 1", "---", "text")) == "***\n\na: 1\n\n---\n\ntext\n"
    assert render(blocks("---", "text")) == "---\n\ntext\n"


def test_a_new_file_reads_back_as_it_was_given():
    tree = blocks("# H", "para", "- a", "- b", "> q", "```\nx\n```")
    assert contents(tree_of(parse(render(tree)))) == [("# H", ["para", "- a", "- b", "> q", "```\nx\n```"])]


# --- project ----------------------------------------------------------------------

def snapshot(**rows) -> dict:
    return {key: {"parent": parent, "position": position, "content": content, "props": {}}
            for key, (parent, position, content) in rows.items()}


def by_kind(kinds: dict):
    return lambda block: kinds.get(block["content"], "note")


def test_project_keeps_what_has_a_span_in_order():
    tree = project(snapshot(page=("root", "a0", "Title"), b=("page", "a1", "b"), a=("page", "a0", "a"), c=("a", "a0", "c")),
                   by_kind({"Title": "page"}))
    assert contents(tree) == [("a", ["c"]), "b"]


def test_project_lifts_the_children_of_what_has_no_span():
    """Review 12: a note under a rectangle is written where the file can hold it."""
    tree = project(snapshot(page=("root", "a0", "Title"), box=("page", "a0", "the box"), kid=("box", "a0", "kid"),
                            next=("page", "a1", "next"), empty=("page", "a2", " "), under=("empty", "a0", "under")),
                   by_kind({"Title": "page", "the box": "text_box"}))
    assert contents(tree) == ["kid", "next", "under"]


@pytest.mark.parametrize("kind", ["ink", "text_box", "picture", "sheet", "recording", "link"])
def test_project_leaves_out_what_lives_in_gamma(kind):
    tree = project(snapshot(page=("root", "a0", "Title"), a=("page", "a0", "x")), by_kind({"Title": "page", "x": kind}))
    assert tree == []


def test_project_keeps_a_highlight_with_no_note():
    tree = project(snapshot(page=("root", "a0", "Title"), a=("page", "a0", "")), lambda b: "page" if b["content"] else "highlight")
    assert [n["id"] for n in tree] == ["a"]


# --- match ------------------------------------------------------------------------

def old_new(old, new) -> tuple:
    def of(items):
        return [item if isinstance(item, dict) else {"content": item} for item in items]

    return of(old), of(new)


def test_match_takes_anchors_first_whatever_the_text():
    old, new = old_new([{"content": "a", "anchor": "x"}, "b"], ["b", {"content": "changed", "anchor": "X"}])
    assert match(old, new) == {0: 1, 1: 0}


def test_match_gives_a_copied_anchor_to_the_first():
    old, new = old_new([{"content": "a", "anchor": "x"}], [{"content": "a copy", "anchor": "x"},
                                                           {"content": "a", "anchor": "x"}])
    assert match(old, new) == {0: 0}


def test_match_pairs_identical_text_in_order():
    old, new = old_new(["a", "b", "a", "c"], ["a", "c"])
    assert match(old, new) == {0: 0, 1: 3}                                       # what they share at either end first
    old, new = old_new(["x", "y", "z"], ["new", "x", "y", "z"])
    assert match(old, new) == {1: 0, 2: 1, 3: 2}


def test_match_pairs_leftovers_of_one_class_that_look_alike():
    old, new = old_new(["start", "the quick brown fox", "end"], ["start", "the quick red fox", "end"])
    assert match(old, new) == {0: 0, 1: 1, 2: 2}


def test_match_pairs_an_edit_with_the_block_it_reads_most_like_not_the_first_alike():
    """Round 1 of the attacks: a paragraph deleted and the next edited gave the
    edit the deleted one's block (its comments and anchors with it), for two
    English sentences share a third of their letters."""
    old, new = old_new(["# Notes", "Call the plumber about the leak.", "The budget meeting moved to Thursday."],
                       ["# Notes", "The budget meeting moved to Friday."])
    assert match(old, new) == {0: 0, 1: 2}


def test_match_leaves_what_is_not_alike_to_insert_and_delete():
    old, new = old_new(["start", "the quick brown fox", "end"], ["start", "0123456789", "end"])
    assert match(old, new) == {0: 0, 2: 2}


def test_match_never_pairs_two_classes():
    """Engine review 18: a new paragraph after a removed callout is no edit of it."""
    old, new = old_new(["start", "> [!quote] the same words here", "end"], ["start", "the same words here", "end"])
    assert match(old, new) == {0: 0, 2: 2}


def test_match_pairs_inside_the_window_its_matches_leave():
    old, new = old_new(["a", "one two three", "b", "four five six"], ["a", "four five six!", "b", "one two three!"])
    pairs = match(old, new)
    assert pairs[0] == 0 and pairs[2] == 2
    assert 1 not in pairs and 3 not in pairs


def test_match_keeps_the_blocks_in_order_and_finds_a_moved_one_only_by_its_anchor():
    """Text is matched in order (vaults.md, the match steps): a block cut and
    pasted elsewhere, with no anchor, is a delete and an insert."""
    old, new = old_new(["a", "b", "c"], ["c", "a", "b"])
    assert match(old, new) == {1: 0, 2: 1}
    old, new = old_new(["a", "b", {"content": "c", "anchor": "x"}], [{"content": "c", "anchor": "x"}, "a", "b"])
    assert match(old, new) == {0: 2, 1: 0, 2: 1}


def test_match_takes_an_id_back_from_gone():
    old, new = old_new(["a"], ["a", {"content": "came back", "anchor": "zz"}, "text again"])
    gone = [{"id": "x", "anchor": "zz", "hash": "none"}, {"id": "y", "anchor": "", "hash": content_hash("text again")}]
    assert match(old, new, gone) == {0: 0, 1: 1, 2: 2}


def test_match_prefers_a_block_still_in_the_base_to_its_twin_in_gone():
    """Why text in ``gone`` comes after identical text. A twin dropped from a pair
    stays in ``gone`` for thirty days, and would take the other's id every round."""
    old, new = old_new(["same", "next"], ["same", "next"])
    gone = [{"id": "twin", "anchor": "", "hash": content_hash("same")}]
    assert match(old, new, gone) == {0: 0, 1: 1}


def test_a_hash_is_of_the_text_a_file_gives_back():
    assert content_hash("hello ") == content_hash("hello")
    assert content_hash("a") != content_hash("b")


# --- front matter -----------------------------------------------------------------

def test_front_matter_is_only_what_front_matter_can_hold():
    assert parse("---\na: 1\n---\nbody\n").front.last == 3
    assert parse("---\n# comment\nkey:\n  - a\n  - b\n\nnested:\n  x: 1\n---\nbody\n").front.fields["key"] == ["a", "b"]
    assert parse("---\n\nIntro text.\n\n---\n\nmore\n").front is None
    assert contents(tree_of(parse("---\n\nIntro text.\n\n---\n\nmore\n"))) == ["---", "Intro text.", "---", "more"]
    assert parse("---\nno close\n").front is None


def test_an_empty_front_matter_hides_nothing_after_it():
    doc = parse("---\n---\n\nfoo\n\n---\n\nbar\n")
    assert doc.front.last == 2
    assert contents(tree_of(doc)) == ["foo", "---", "bar"]


def test_front_matter_is_read_whatever_ends_its_lines():
    doc = parse("---\r\na: 1\r\n---\r\nbody\r\n")
    assert (doc.front.fields, doc.front.last) == ({"a": "1"}, 3)
    assert contents(tree_of(doc)) == ["body"]
    assert parse("\ufeff---\na: 1\n---\nbody\n").front.fields == {"a": "1"}
    assert parse("---\ra: 1\r---\r").front.last == 3
    assert parse("---\na: 1\n...\nbody").front.last == 3


def test_a_body_starts_on_its_own_line_and_a_file_with_none_has_no_block():
    doc = parse("---\na: 1\n---")
    assert doc.flat == [] and doc.tiles == []
    assert parse("---\na: 1\n---\n\nbody\n").flat[0].span.first == 4


def test_a_block_added_under_front_matter_with_no_last_newline():
    text = write(parse("---\na: 1\n---"), [], [{"id": "a", "content": "new", "children": []}])[0]
    assert text == "---\na: 1\n---\nnew"


RAW_FRONT = """---
title: Old
# a comment
tags: [a, b]
cssclasses:
  - wide
custom:
    nested: 1
aliases:
    - one
    - two
---
body
"""


def test_patch_front_leaves_unknown_keys_alone_byte_for_byte():
    text = patch_front(parse(RAW_FRONT), {"title": "New"})
    assert text == RAW_FRONT.replace("title: Old", "title: New")


def test_patch_front_rewrites_a_key_in_the_form_it_has():
    assert "tags: [c, d]\n" in patch_front(parse(RAW_FRONT), {"tags": ["c", "d"]})
    assert "aliases:\n    - three\n---" in patch_front(parse(RAW_FRONT), {"aliases": ["three"]})


def test_patch_front_with_the_same_value_changes_nothing():
    assert patch_front(parse(RAW_FRONT), {"tags": ["a", "b"], "title": "Old"}) == RAW_FRONT
    raw = "---\ntitle:   'Old'   \n---\n"
    assert patch_front(parse(raw), {"title": "Old"}) == raw


def test_patch_front_appends_a_new_key():
    assert patch_front(parse("---\na: 1\n---\nbody\n"), {"doi": "10.1/x", "tags": ["t"]}) == (
        '---\na: 1\ndoi: "10.1/x"\ntags:\n  - t\n---\nbody\n')


def test_patch_front_puts_a_new_front_matter_first():
    assert patch_front(parse("body\n"), {"title": "T"}) == "---\ntitle: T\n---\nbody\n"
    assert patch_front(parse("\ufeffbody\r\n"), {"title": "T"}) == "\ufeff---\r\ntitle: T\r\n---\r\nbody\r\n"
    assert patch_front(parse(""), {"title": "T"}) == "---\ntitle: T\n---\n"
    assert patch_front(parse("---\n\nprose\n\n---\n"), {"title": "T"}) == "---\ntitle: T\n---\n---\n\nprose\n\n---\n"


def test_patch_front_never_leaves_an_empty_pair():
    assert patch_front(parse("---\na: 1\n---\n\nbody\n"), {"a": None}) == "body\n"
    assert patch_front(parse("---\na: 1\n---\nbody\n"), {"a": None}) == "body\n"
    assert patch_front(parse("---\n---\nbody\n"), {"b": None}) == "body\n"
    assert patch_front(parse("---\na: 1\nb: 2\n---\nbody\n"), {"a": None}) == "---\nb: 2\n---\nbody\n"
    assert patch_front(parse("---\nkey:\n  - a\n  - b\nc: 1\n---\n"), {"key": None}) == "---\nc: 1\n---\n"


def test_patch_front_keeps_the_files_line_endings():
    assert patch_front(parse("---\r\na: 1\r\n---\r\n"), {"b": "2"}) == '---\r\na: 1\r\nb: "2"\r\n---\r\n'
    assert patch_front(parse("---\r\na: 1\r\n---\r\n"), {"a": "x"}) == "---\r\na: x\r\n---\r\n"


def test_patch_front_with_nothing_to_set_on_a_file_without_it_changes_nothing():
    assert patch_front(parse("body\n"), {}) == "body\n"
    assert patch_front(parse("body\n"), {"a": None}) == "body\n"


def test_patch_front_leaves_the_body_as_it_was():
    raw = "---\na: 1\n---\n\n* item   \n\n\n\tcode\n"
    assert patch_front(parse(raw), {"a": "2"}) == raw.replace("a: 1", 'a: "2"')


# --- the review's other cases ----------------------------------------------------------

def test_text_under_a_callout_stays_in_its_block():
    assert contents(tree_of(parse("> [!quote] t\n> q\nnote\n"))) == ["> [!quote] t\n> q\nnote"]


def test_a_callout_holds_what_would_be_blocks_outside_one():
    assert contents(tree_of(parse("> [!quote] t\n> - x\n> ^abc\n> $$\n> ```\n"))) == [
        "> [!quote] t\n> - x\n> ^abc\n> $$\n> ```"]


def test_a_paragraph_opening_on_an_anchor_is_no_anchor():
    assert contents(tree_of(parse("a\n\n^abc\nnext typed\n"))) == ["a", "^abc\nnext typed"]


def test_a_comment_stays_in_its_block():
    assert contents(tree_of(parse("text %%hidden%% more\n\n%%\nblock comment\n%%\n"))) == [
        "text %%hidden%% more", "%%\nblock comment\n%%"]


def test_a_fence_holding_a_list_opens_no_list():
    # A word added to it must leave the indent of what follows alone.
    assert edited("```\n- x\n```\n\n  next\n", "```\n- x\n```", "```\n- y\n```") == "```\n- y\n```\n\n  next\n"


def test_a_block_a_rule_did_not_foresee_is_written_fresh_rather_than_merged():
    """``shape``'s passes. With its sibling gone a lone ``$$`` would close the pair
    left open in the item above it, and the walk has no rule for that: the first
    pass loses it, and the next writes it as a new block, a child of an item,
    so a list item: one CommonMark did not read around it, which the check
    asks of a block the change left alone, whatever pass wrote it."""
    doc, ids, tree = read("- is $$\n  - k\n  $$\n")
    del tree[0]["children"][0]
    first, starts = write(doc, ids, tree)
    assert first == "- is $$\n\n    $$\n"
    assert [n.content for n in parse(first).flat] == ["- is $$\n\n    $$"]       # b2 is gone from it
    text = write(doc, ids, tree, fresh={"b2"})[0]
    assert text == "- is $$\n  - $$\n"
    assert [n.content for n in parse(text).flat] == ["- is $$", "$$"]
    with pytest.raises(Unwritable, match=MEANING):
        shape(doc, ids, tree)


def test_a_block_written_after_code_that_never_closes_is_refused_for_that():
    doc, ids, tree = read("- $$\n  x\n")
    tree.append({"id": "new", "content": "after", "children": []})
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        shape(doc, ids, tree)


def test_a_new_item_beside_a_list_four_columns_in_stops_short_of_code():
    doc, ids, tree = read("para\n    - x\n\nnext\n")
    assert tree[1]["content"] == "- x"
    tree.insert(1, {"id": "new", "content": "- new", "children": []})
    assert write(doc, ids, tree)[0] == "para\n- new\n- x\n\nnext\n"
    with pytest.raises(Unwritable, match=MEANING):          # ``- x`` is the paragraph's text to CommonMark
        shape(doc, ids, tree)


def test_unwritable_past_the_limits_says_so():
    raw = "- x\n" * 200_001
    doc = parse(raw)
    assert doc.problem and doc.flat == []
    assert md_lens.tree_of(doc) == []


# --- the adversaries' first round -------------------------------------------------------

def anchored(raw: str, content: str, anchor: str = "abc") -> str:
    """``raw`` with ``anchor`` set on the block ``content``, which keeps it."""
    doc, ids, tree = read(raw)
    node_with(tree, content)["anchor"] = anchor
    shaped, text = shape(doc, ids, tree)
    assert any(node.get("anchor") == anchor for node in flatten(shaped)), text
    return text


def first_child(raw: str, parent: str, content: str) -> str:
    doc, ids, tree = read(raw)
    node_with(tree, parent)["children"].insert(0, {"id": "new", "content": content, "children": []})
    return shape(doc, ids, tree)[1]


@pytest.mark.parametrize("raw,old,new,text", [
    ("A note ^abc\xa0\n", "A note", "A note, edited", "A note, edited ^abc\xa0\n"),
    ("- item ^abc\u3000\n", "- item", "- item edited", "- item edited ^abc\u3000\n"),
    ("# Head ^abc\xa0\n\ntext\n", "# Head", "# Head two", "# Head two ^abc\xa0\n\ntext\n"),
])
def test_an_anchor_before_whitespace_of_any_kind_is_put_back(raw, old, new, text):
    assert edited(raw, old, new) == text


def test_an_anchor_renamed_before_whitespace_of_any_kind():
    assert anchored("a ^k\x85", "a", "new") == "a ^new\x85"


def test_match_gives_an_id_in_gone_to_one_block_only():
    gone = [{"id": "g1", "anchor": "abc", "hash": content_hash("same text")}]
    assert match(*old_new([], [{"content": "same text", "anchor": "abc"}, "same text"]), gone) == {0: 0}
    gone = [{"id": "g", "anchor": "zz", "hash": content_hash("back")}]
    assert match(*old_new(["a"], ["a", {"content": "back", "anchor": "zz"}, "back"]), gone) == {0: 0, 1: 1}


def test_a_lone_cr_never_takes_the_lf_of_a_blank_line_after_it():
    assert inserted("a\n\nb\r", "b", "c") == "a\n\nb\r\rc\n"
    doc, ids, tree = read("a\n\nb\r")
    tree.append({"id": "new", "content": "t\n```", "children": []})
    shaped, text = shape(doc, ids, tree)
    assert text == "a\n\nb\r\rt\n```\n"
    assert contents(shaped) == ["a", "b", "t", "```"]
    assert without("2\r- x\n```\n\n", "- x") == "2\r\r```\n\n"


@pytest.mark.parametrize("raw,old,new,text", [
    ("x\n\n    code\n", "x", "-", "-\n\n    code\n"),
    ("x\n\n  y\n", "x", "*", "*\n\n  y\n"),
    ("9.\n\n e", "9.", "16.", "16.\n\n e"),
])
def test_a_marker_with_nothing_after_it_opens_no_list(raw, old, new, text):
    assert edited(raw, old, new) == text


def test_a_marker_with_nothing_after_it_put_before_a_line_leaves_it_alone():
    doc, ids, tree = read(" # x\n")
    tree.insert(0, {"id": "new", "content": "1.", "children": []})
    assert shape(doc, ids, tree)[1] == "1.\n\n # x\n"


def test_code_opening_on_a_comment_is_no_heading_to_a_list_before_it():
    doc, ids, tree = read("Intro\n\n    # comment\n    code\n")
    tree.insert(1, {"id": "new", "content": "1. step", "children": []})
    shaped, text = shape(doc, ids, tree)
    assert text == "Intro\n\n1. step\n\n```\n# comment\ncode\n```\n"
    assert contents(shaped) == ["Intro", "1. step", "```\n# comment\ncode\n```"]
    raw = "1. step\n\nIntro\n\n    # comment\n    code\n"
    assert without(raw, "Intro") == "1. step\n\n```\n# comment\ncode\n```\n"


def test_an_anchor_goes_on_its_own_line_after_code_in_an_item():
    assert anchored("1. ```py\n   print(1)\n   ```\n", "1. ```py\n   print(1)\n   ```", "code1") == (
        "1. ```py\n   print(1)\n   ```\n   ^code1\n")                               # no blank line: the list stays tight


@pytest.mark.parametrize("raw,old,new,text", [
    ("- # Heading ^a1\n", "# Heading", "```\ncode\n```", "- ```\n  code\n  ```\n  ^a1\n"),
    ("- buy milk ^a1\n", "- buy milk", "- ", "- \n  ^a1\n"),
    ("Some text ^a1\n\nNext\n", "Some text", "#", "#\n\n^a1\n\nNext\n"),
    ("Some text ^a1\n", "Some text", "Some text\n\n> a quote", "Some text ^a1\n\n> a quote\n"),
    ("- a ^x\n  - child\n", "- a", "- $$\nzed\n$$", "- - $$\n    zed\n    $$\n  ^x\n  - child\n"),
])
def test_an_anchor_at_a_lines_end_moves_to_its_own_line_when_the_new_text_cannot_end_on_it(raw, old, new, text):
    written = edited(raw, old, new)
    assert written == text
    assert any(node.anchor for node in parse(written).flat)


@pytest.mark.parametrize("raw,content,text", [
    ("Title\n=====\n\nbody\n", "Title\n=====", "Title\n=====\n\n^abc\n\nbody\n"),
    ("s\n---\n- \n", "s\n---", "s\n---\n\n^abc\n\n- \n"),
    ("- a\n***\n", "***", "- a\n***\n\n^abc\n"),
    ("***\n", "***", "***\n\n^abc\n"),
])
def test_an_anchor_never_ends_a_rule_or_an_underline(raw, content, text):
    assert anchored(raw, content) == text


@pytest.mark.parametrize("raw,old,new,text", [
    ("Para\n\nnote\n", "note", "^ref", "Para\n\n\\^ref\n"),
    ("a\n\nb\n", "b", "press ^c", "a\n\npress \\^c\n"),
    ("a\n\nb\n", "b", "two\nlines\n^ref", "a\n\ntwo\nlines\n\\^ref\n"),
])
def test_text_a_file_would_read_as_an_anchor_is_written_as_text(raw, old, new, text):
    doc, ids, tree = read(raw)
    node_with(tree, old)["content"] = new
    shaped, written = shape(doc, ids, tree)
    assert written == text
    assert not any(node.get("anchor") for node in flatten(shaped))


def test_a_note_that_is_only_an_anchor_is_its_own_block():
    assert inserted("Para\n", "Para", "^ref") == "Para\n\n\\^ref\n"
    doc, ids, tree = read("^ref\n")
    tree.insert(0, {"id": "new", "content": "Title", "children": []})
    shaped, text = shape(doc, ids, tree)
    assert text == "Title\n\n\\^ref\n"
    assert [n["id"] for n in shaped] == ["new", "b0"]


@pytest.mark.parametrize("raw,old,new,text", [
    ("- a\n- b\n  more\n", "- b\n  more", "  more", "- a\n\nmore\n"),
    ("2. x\n3. y\n   more\n", "3. y\n   more", "   more", "2. x\n\nmore\n"),
    ("- a\n- b\n", "- b", "  typed\n- b", "- a\n\ntyped\n- b\n"),
])
def test_text_written_anew_after_a_list_stays_out_of_it_whatever_it_starts_with(raw, old, new, text):
    assert edited(raw, old, new) == text


def test_a_new_block_with_a_leading_space_after_a_list_stays_out_of_it():
    assert inserted("- item\n", "- item", " indented note") == "- item\n\n indented note\n"    # left of its text
    assert inserted("- item\n", "- item", "  indented note") == "- item\n\nindented note\n"
    assert inserted("- ", "- ", " x") == "- \n\n x"
    assert inserted("Para\n", "Para", " indented note") == "Para\n\n indented note\n"


def test_match_pairs_a_long_block_with_one_letter_changed_in_no_time():
    started = time.perf_counter()
    big = "word " * 8000
    assert match(*old_new([big], ["x" + big[1:]])) == {0: 0}
    code = "```\n" + "".join(f"    line {k} = value {k % 89}\n" for k in range(2000)) + "```"
    assert match(*old_new([code], [code[:-3] + "    one more\n```"])) == {0: 0}
    text = "".join(f"{k % 7} " for k in range(4000))
    match(*old_new(["- " + text], ["- " + text[::-1]]))
    assert time.perf_counter() - started < 2


def test_match_still_pairs_a_short_block_with_a_typo_fixed():
    assert match(*old_new(["start", "teh", "end"], ["start", "the", "end"])) == {0: 0, 1: 1, 2: 2}


@pytest.mark.parametrize("raw,old,new,text", [
    ("text\n\n\n", "text", "# text", "# text\n\n\n"),
    ("# H\n\nsome words\n\n\n\n", "some words", "- some words", "# H\n\n- some words\n\n\n\n"),
    ("#\n\t\n", "#", "# alpha", "# alpha\n\t\n"),
])
def test_the_tail_of_a_file_stays_with_its_last_block_written_anew(raw, old, new, text):
    assert edited(raw, old, new) == text


@pytest.mark.parametrize("new,text", [
    ("- ```\nzed\n```", "- - ```\n    zed\n    ```\n- c\n"),
    ("- $$\nzed\n$$", "- - $$\n    zed\n    $$\n- c\n"),
])
def test_an_items_lines_retyped_into_code_stay_under_its_marker(new, text):
    """A bullet holding code is one a bullet wraps (``- ```py`` alone is the
    fence), its lines under the marker it holds."""
    assert edited("- a\n  b\n- c\n", "- a\n  b", new) == text
    assert tree_equal(tree_of(parse(text)), [{"content": new, "children": []}, {"content": "- c", "children": []}])


@pytest.mark.parametrize("opener,closer", [
    ("<!-- todo", " -->"), ("<pre>", "\n</pre>"), ("<script>", "</script>"), ("<style>", "\n</STYLE>"),
    ("<textarea>", "</textarea>"), ("<?php", " ?>"), ("<!DOCTYPE", " html>"), ("<![CDATA[", "]]>"),
])
def test_html_held_open_in_the_middle_is_a_wait_not_a_loss(opener, closer):
    """An HTML block of these kinds runs to its closer whatever stands between, so
    every block below it is gone in any other reader."""
    doc, ids, tree = read("p\n\nq\n\nr\n")
    tree[1]["content"] = opener
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        shape(doc, ids, tree)
    tree[1]["content"] = opener + closer
    assert [n["id"] for n in shape(doc, ids, tree)[0]] == ["b0", "b1", "b2"]
    doc, ids, tree = read("p\n")
    tree.insert(0, {"id": "new", "content": opener, "children": []})
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        shape(doc, ids, tree)


def test_html_a_file_holds_open_already_stays_editable():
    raw = "<!--\n- x\n\ny\n-->\n"
    assert edited(raw, "- x", "- x two") == "<!--\n- x two\n\ny\n-->\n"
    assert edited(raw, "<!--", "<!-- todo") == "<!--\n- x\n\ny\n-->\n".replace("<!--", "<!-- todo")


def test_a_block_after_html_that_runs_to_a_blank_line_stands_a_blank_line_away():
    doc, ids, tree = read("- a\n- b\n")
    tree.insert(0, {"id": "new", "content": "<div>\nzed\n</div>", "children": []})
    assert shape(doc, ids, tree)[1] == "<div>\nzed\n</div>\n\n- a\n- b\n"
    assert inserted('<img src="x.png">\n', '<img src="x.png">', "- zed") == '<img src="x.png">\n\n- zed\n'
    assert edited("- a\n- b\n", "- a", "<div>") == "<div>\n\n- b\n"


def test_a_first_child_that_cannot_break_into_its_parents_text_stands_a_blank_line_away():
    assert child_added("- a\n", "- a", "- ") == "- a\n\n  - \n"
    assert child_added("1. a\n2. b\n", "1. a", "3. zed") == "1. a\n\n   3. zed\n2. b\n"
    assert child_added("- a\n", "- a", "2. zed") == "- a\n\n  2. zed\n"
    assert child_added("- a\n", "- a", "1. zed") == "- a\n  1. zed\n"
    doc, ids, tree = read("- a\n- \n")
    tree[0]["children"].append(tree.pop(1))
    assert shape(doc, ids, tree)[1] == "- a\n\n  - \n"


@pytest.mark.parametrize("raw,old,new,text", [
    ("- a\n***\n", "***", "zed", "- a\n\nzed\n"),
    ("- \ntext\n", "- ", "- zed", "- zed\n\ntext\n"),
    ("Steps:\n- a\n- b\n", "- a", "2. zed", "Steps:\n\n2. zed\n- b\n"),
    # What a file joined already stays joined: an edit and its revert give it back.
    ("- a\nlazy\n", "lazy", "lazy two", "- a\nlazy two\n"),
    ("- a\nlazy\n", "- a", "- a two", "- a two\nlazy\n"),
])
def test_two_blocks_side_by_side_are_kept_apart_when_an_edit_would_join_them(raw, old, new, text):
    assert edited(raw, old, new) == text


def test_a_block_whose_neighbour_goes_is_kept_apart_from_the_next():
    assert without("- a\nlazy\n- \n", "- a") == "lazy\n\n- \n"


def test_a_child_under_a_wide_marker_stands_at_its_text():
    assert first_child("-   one\n\n    child\n", "- one", "zed") == "-   one\n    - zed\n\n    child\n"


def test_code_by_indentation_this_parser_reads_as_text_stays_code_when_it_moves():
    doc, ids, tree = read("- a\n\n      code\np\n")
    assert contents(tree) == [("- a", ["code"]), "p"]
    tree[0]["children"].insert(0, {"id": "new", "content": "zed", "children": []})
    shaped, text = shape(doc, ids, tree)
    assert text == "- a\n  - zed\n\n  ```\n  code\n  ```\np\n"                       # p stood straight after it
    assert contents(shaped) == [("- a", ["- zed", "```\ncode\n```"]), "p"]
    assert inserted("***\n\t- t\n", "***", "zed") == "***\n\nzed\n\n```\n- t\n```\n"
    assert first_child("# h\n    code\n", "# h", "zed") == "# h\nzed\n\n```\ncode\n```\n"


@pytest.mark.parametrize("raw,old,new,text", [
    ("- \n\t- t\n", "- ", "---", "* ---\n\t- t\n"),
    ("- \n\t- t\n", "- ", "***", "- ***\n\t- t\n"),
    ("p\n", "p", "* * *", "* * *\n"),
])
def test_a_rule_stays_a_rule_and_one_with_children_stays_an_item(raw, old, new, text):
    assert edited(raw, old, new) == text


def test_an_anchor_on_its_own_line_after_an_item_leaves_the_list_open():
    assert inserted("* a\n^id\n", "- a", "    code") == "* a\n^id\n\n```\ncode\n```\n"


@pytest.mark.parametrize("raw,text", [("a\n\nb\n", "- parent\n  - a\n\nb\n"),
                                      ("- a\n- b\n", "- parent\n  - a\n- b\n")])
def test_a_block_put_under_a_block_new_in_the_same_change(raw, text):
    doc, ids, tree = read(raw)
    tree[0] = {"id": "new", "content": "- parent", "children": [tree[0]]}
    shaped, written = shape(doc, ids, tree)
    assert written == text
    assert [n["id"] for n in flatten(shaped)] == ["new", "b0", "b1"]


def test_an_edit_of_one_line_among_thousands_alike_is_quick():
    started = time.perf_counter()
    lines = ["```", *["    x"] * 8000, "```"]
    doc, ids, tree = read("\n".join(lines) + "\n")
    lines[4000] += " y"
    tree[0]["content"] = "\n".join(lines)
    assert shape(doc, ids, tree)[1] == "\n".join(lines) + "\n"
    assert time.perf_counter() - started < 2


def test_patch_front_quotes_a_value_with_a_line_break():
    text = patch_front(parse("---\na: 1\n---\nbody\n"), {"title": "line one\nline two"})
    assert text == '---\na: 1\ntitle: "line one\\u000aline two"\n---\nbody\n'
    assert parse(text).front.fields == {"a": "1", "title": "line one\nline two"}
    assert contents(tree_of(parse(text))) == ["body"]


def test_patch_front_takes_a_value_whole_across_its_blank_lines():
    raw = "---\ndesc: |\n  a\n\n  b\nfoo: 1\n---\nx\n"
    assert patch_front(parse(raw), {"desc": "z"}) == "---\ndesc: z\nfoo: 1\n---\nx\n"
    assert patch_front(parse(raw), {"desc": None}) == "---\nfoo: 1\n---\nx\n"
    assert patch_front(parse("---\na: 1\n\nb: 2\n---\n"), {"a": "3"}) == '---\na: "3"\n\nb: 2\n---\n'


def test_patch_front_sets_every_copy_of_a_key():
    assert patch_front(parse("---\na: 1\nb: 2\na: 3\n---\n"), {"a": "x"}) == "---\na: x\nb: 2\na: x\n---\n"


def test_a_new_file_takes_nothing_after_code_left_open():
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        render(blocks("```\nopen", "after"))


def test_a_number_written_01_breaks_into_no_paragraph():
    assert inserted("p\np\n", "p\np", "01. zed") == "p\np\n\n01. zed\n"
    assert inserted("- a\n\np\n", "p", "01. zed") == "- a\n\np\n\n01. zed\n"


def test_html_that_breaks_into_text_is_no_lazy_line_of_it():
    """A ``<div>`` after a list item starts HTML, which runs to a blank line, so
    what comes next stands a blank line away; so in a paragraph this parser
    reads as one, after a setext heading."""
    raw = "3. a\n<div>\nx\n</div>\n"
    assert inserted(raw, "<div>\nx\n</div>", "- [ ] zed") == raw + "\n- [ ] zed\n"
    raw = "s\n---\n<div>\nx\n</div>\n"
    assert inserted(raw, "s\n---\n<div>\nx\n</div>", "1. zed") == raw + "\n1. zed\n"
    # HTML that runs to its closer ends there: what followed it was its own.
    assert edited("<pre>\nx\n</pre>\n- a\n", "<pre>\nx\n</pre>", "<div>") == "<div>\n\n- a\n"


def test_html_in_a_list_item_holds_what_the_item_holds():
    assert edited("- a\n\t- b\n", "- a", "- <div>") == "- <div>\n\n\t- b\n"
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        edited("- a\n\t- b\n", "- a", "- <pre>")
    assert edited("- a\n- b\n", "- a", "- <!-- zed -->") == "- <!-- zed -->\n- b\n"


def test_a_rule_retyped_or_retyped_into_is_a_new_class():
    assert edited("- a\n\n    para4\n", "- a", "- ---") == "* - ---\n\n    para4\n"
    assert edited("* * *\n| a |\n|---|\n", "* * *", "- zed") == "- zed\n\n| a |\n|---|\n"


def test_a_tab_after_a_marker_goes_on_to_the_next_stop():
    assert parse("-\ttabbed\n").flat[0].span.column == 4
    assert edited("-\ttabbed\n\n    a\n", "- tabbed", "- tabbed\nmore") == "-\ttabbed\n    more\n\n    a\n"
    text = edited("-\ttabbed\n\n    a\n", "- tabbed", "- $$\nzed\n$$")
    assert text == "- - $$\n    zed\n    $$\n\n    a\n"                             # in the item, as remark reads it
    assert tree_equal(tree_of(parse(text)), [{"content": "- $$\nzed\n$$", "children": blocks("a")}])


def test_the_gate_catches_a_walk_that_moves_untouched_text_over(monkeypatch):
    """Law 5 allows a new indent only where a list stands before a block or where
    code would become text: a walk that indents a paragraph with no list near
    must fail it."""
    column = md_lens._Walk._column

    def sabotaged(self, old, place, at, blank):
        want = column(self, old, place, at, blank)
        moved = place.flow and self.list_root is None and blank and old.span.kind in ("plain", "quote")
        return 1 if moved and want == 0 and self.prev else want

    raw = "".join(f"para {k}\n\n> quote {k}\n\n" for k in range(12))
    assert gate.gate_file("clean", raw, gate.Counter(), 30, gate.SEED) == []
    monkeypatch.setattr(md_lens._Walk, "_column", sabotaged)
    wrong = gate.gate_file("sabotaged", raw, gate.Counter(), 30, gate.SEED)
    assert not any("law 1" in w for w in wrong) and any("law 5: bytes" in w for w in wrong)


def test_the_spans_off_check_misses_no_fixture(monkeypatch):
    changed = [(name, text + "\nedited\n") for name, text in gate.fixture_files()]
    monkeypatch.setattr(gate, "fixture_files", lambda: changed)
    with pytest.raises(AssertionError):
        gate.test_spans_off_is_the_parser_as_it_was()


# --- the adversaries' second round ------------------------------------------------------

def anchor_kept(raw: str, old: str, new: str) -> str:
    """``raw`` with the block ``old`` retyped as ``new``, which keeps its anchor
    and adds no block."""
    doc, ids, tree = read(raw)
    node = node_with(tree, old)
    anchor = node["anchor"]
    node["content"] = new
    shaped, text = shape(doc, ids, tree)
    assert [n.get("anchor") for n in flatten(shaped) if n["id"] == node["id"]] == [anchor], text
    assert len(flatten(shaped)) == len(ids), text
    return text


@pytest.mark.parametrize("raw,old,new,text", [
    ("-   first\n\n    second\n", "- first", "- first word", "-   first word\n\n    second\n"),
    ("*  a\n\n   b\n", "- a", "- a b", "*  a b\n\n   b\n"),
    ("1.  first\n\n    second\n", "1. first", "1. first word", "1.  first word\n\n    second\n"),
    ("-\tfirst\n\n\tsecond\n", "- first", "- first word", "-\tfirst word\n\n\tsecond\n"),
])
def test_an_edit_under_a_wide_marker_leaves_its_child_where_it_stands(raw, old, new, text):
    """This parser reads a child past one column after the marker, wherever the
    marker's spaces put the item's text."""
    assert edited(raw, old, new) == text
    assert edited(text, new, old) == raw


@pytest.mark.parametrize("raw,old,new,text", [
    ("-   a ^x\n", "- a", "- a\nb", "-   a\n    b ^x\n"),
    ("-\ta ^x\n", "- a", "- a\nb", "-\ta\n    b ^x\n"),
    ("1.  a ^x\n", "1. a", "1. a\nb", "1.  a\n    b ^x\n"),
    ("- a ^id\n- b\n", "- a", "- a\nzed", "- a\n  zed ^id\n- b\n"),
])
def test_a_line_added_to_an_anchored_item_ends_on_the_anchor(raw, old, new, text):
    assert anchor_kept(raw, old, new) == text


@pytest.mark.parametrize("raw", ["- a ^x\n", "- a ^x\n  - b\n"])
def test_an_anchored_item_given_a_line_and_back_comes_back_whole(raw):
    text = anchor_kept(raw, "- a", "- a\nmore")
    assert text == raw.replace("- a ^x", "- a\n  more ^x")
    assert anchor_kept(text, "- a\n  more", "- a") == raw


def test_an_anchor_added_to_a_multi_line_item_keeps_the_list_tight():
    doc, ids, tree = read("- a\n- b\n")
    tree[0]["content"], tree[0]["anchor"] = "- a\nzed", "abc"
    assert shape(doc, ids, tree)[1] == "- a\n  zed ^abc\n- b\n"


@pytest.mark.parametrize("raw,new,text", [("a ^x\n", "y\n```", "y ^x\n```\n"),
                                          ("$$\nx\n$$\n^b\n", "$$\nx\n$$\n$$", None),
                                          ("~~~\n~~~\n^9x\n", "wNEWq ~~~\n~~~", "wNEWq ~~~ ^9x\n~~~\n")])
def test_a_block_left_open_last_never_takes_its_own_anchor(raw, new, text):
    """The anchor stays on the block's first part, which keeps its id; where it
    can only follow the block, code left open would take it in, and the write
    waits."""
    doc, ids, tree = read(raw)
    tree[0]["content"] = new
    if text is None:
        with pytest.raises(Unwritable, match=OPEN_BLOCK):
            shape(doc, ids, tree)
        return
    shaped, written = shape(doc, ids, tree)
    assert written == text
    assert shaped[0]["id"] == "b0" and shaped[0]["anchor"] == tree[0]["anchor"]


def test_a_leading_space_typed_into_a_child_keeps_it_under_its_parent():
    assert edited("- x\n\n    %\n", "%", " q") == "- x\n\n    q\n"
    assert edited("- a\n  - b\n\n  p\n  - c\n", "p", " p2") == "- a\n  - b\n\n  p2\n  - c\n"
    assert edited("a\nb\n", "a\nb", "a\n  b") == "a\n  b\n"                      # one of its own reaches the file


@pytest.mark.parametrize("content", [" # x", "  ## x"])
def test_a_heading_held_back_by_its_indent_is_text_to_what_follows_it(content):
    doc, ids, tree = read("a\n")
    tree.insert(0, {"id": "new", "content": content, "children": []})
    shaped, text = shape(doc, ids, tree)
    assert text == f"{content}\n\na\n"
    assert [n["id"] for n in shaped] == ["new", "b0"]


def test_a_caret_kept_from_reading_as_an_anchor_is_no_change_to_its_block():
    """Gamma's ``^k`` and the file's ``\\^k`` say the same: no op goes back."""
    doc, ids, tree = read("^k\n")
    tree.insert(0, {"id": "new", "content": "a new paragraph", "children": []})
    shaped, text = shape(doc, ids, tree)
    assert text == "a new paragraph\n\n\\^k\n"
    assert tree_equal(shaped, tree) and content_hash("\\^k") == content_hash("^k")
    doc, ids, tree = read("a\n\n^x\n\nb\n\n^x\n")                                # a copy of an anchor
    del tree[0]
    shaped, text = shape(doc, ids, tree)
    assert text == "b\n\n\\^x\n" and tree_equal(shaped, tree)
    assert not tree_equal([{"content": "press \\^c", "children": []}], [{"content": "press ^d", "children": []}])


def test_a_line_added_in_a_file_of_mixed_endings_takes_its_blocks():
    assert edited("\ra\r\n\n", "a", "a\nb") == "\ra\r\nb\r\n\n"
    assert edited("\ra\r\nb\r\n\n", "a\nb", "a") == "\ra\r\n\n"
    assert edited("`\r-\n\n", "`\n-", "x") == "x\n\n"                             # the blank line after it stays


def test_a_blank_line_a_new_number_needed_has_no_way_back():
    """A counter-example to law 6, and why it cannot hold. ``8.`` breaks into no
    paragraph, so the item it stands under needs a blank line first; on the
    revert that blank line is outside the block, and a person may have written
    it: law 2 keeps it."""
    text = edited("1. a\n   1. b\n      1. c\n", "1. b", "8. b")
    assert text == "1. a\n\n   8. b\n      1. c\n"
    assert edited(text, "8. b", "1. b") == "1. a\n\n   1. b\n      1. c\n"
    text = edited("Setext\n======\n10. ten\n", "Setext\n======", "Setext\n======\nbravo")
    assert text == "Setext\n======\nbravo\n\n10. ten\n"


def test_html_split_over_blocks_is_held_whole():
    """This parser reads ``<!--`` to ``-->`` as blocks; any other reader takes
    everything to the closer into the comment, or all that follows once the
    closer goes."""
    raw = "<!--\nnote\n\nb\n-->\n\nafter\n"
    assert contents(tree_of(parse(raw))) == ["<!--\nnote", "b\n-->", "after"]
    for change in (lambda: edited(raw, "b\n-->", "b"), lambda: without(raw, "b\n-->"),
                   lambda: inserted(raw, "<!--\nnote", "zed")):
        with pytest.raises(Unwritable, match=OPEN_BLOCK):
            change()
    assert edited(raw, "after", "after two") == raw.replace("after", "after two")


@pytest.mark.parametrize("change", [lambda: first_child("- a\n\n    para4\n", "- a", "<!-- zed"),
                                    lambda: edited("- a\n- b\n", "- a", "- <!-- zed")])
def test_html_left_open_in_a_list_item_is_held_back(change):
    """CommonMark closes it with the item, but the HTML a reader makes of it
    does not: everything after it is gone in a browser."""
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        change()


@pytest.mark.parametrize("raw,content,text", [
    ("1. \n2. a\n", "1. ", "1. \n   ^abc\n2. a\n"),
    ("- \n---\n", "- ", "- \n  ^abc\n---\n"),
    ("- a\n- \n- b\n", "- ", "- a\n- \n  ^abc\n- b\n"),
    ("- ___\n", "- ___", "- ___\n  ^abc\n"),
    ("1. ***\n", "1. ***", "1. ***\n   ^abc\n"),
])
def test_an_anchor_on_an_item_with_no_text_goes_on_the_next_line_in_it(raw, content, text):
    """After a blank line it would stand outside an empty item, and a list
    would turn loose; and a rule is no text to end."""
    assert anchored(raw, content) == text


def test_what_follows_an_empty_item_and_a_blank_line_is_out_of_it():
    """An item may open with one blank line, the empty first one: a second ends
    it, and what is indented below is code or a list of its own."""
    assert contents(tree_of(parse("- \n\n    code\n"))) == ["- ", "    code"]
    assert contents(tree_of(parse("1. \n\n   - t\n"))) == ["1. ", "- t"]
    assert edited("- \n\n    code\n", "- ", "- zed") == "- zed\n\n```\ncode\n```\n"
    assert edited("1. \n\n   - t\n", "1. ", "1. zed") == "1. zed\n\n- t\n"
    assert child_added("- \n", "- ", "- kid") == "- \n  - kid\n"                 # its child stands straight under it


def test_a_footnote_holds_its_indented_paragraphs():
    raw = "[^n]: a\n\n    more\n\nafter\n"
    assert contents(tree_of(parse(raw))) == ["[^n]: a\n\nmore", "after"]
    assert inserted(raw, "[^n]: a\n\nmore", "- zed") == "[^n]: a\n\n    more\n\n- zed\n\nafter\n"
    assert edited("p\n\n    code\n", "p", "[^1]: note") == "[^1]: note\n\n```\ncode\n```\n"
    assert edited(raw, "[^n]: a\n\nmore", "[^n]: a\n\nmore\nline") == "[^n]: a\n\n    more\n    line\n\nafter\n"
    raw = "[^1]: The note.\n\n    code\nTrailing  \nnext\n"                       # a lazy line of the footnote
    assert edited(raw, "[^1]: The note.\n\ncode", "a new paragraph") == "a new paragraph\n\nTrailing  \nnext\n"


@pytest.mark.parametrize("raw,old,new,text", [
    ("[^1]: note\n- a\n", "[^1]: note", "zed", "zed\n- a\n"),
    ("- a\n[^1]: note\n", "[^1]: note", "zed", "- a\n\nzed\n"),
    ("~~~\nc\n~~~\n3. a\n", "~~~\nc\n~~~", "~~~\nc\n~~~\nzed", "~~~\nc\n~~~\nzed\n\n3. a\n"),
])
def test_text_written_where_a_footnote_or_a_fence_stood_keeps_clear_of_its_neighbour(raw, old, new, text):
    """A footnote breaks into a paragraph and takes no list after it; text after
    a fence's close is a paragraph."""
    assert edited(raw, old, new) == text


@pytest.mark.parametrize("raw,content,text", [
    ("see [z] here\n\n[z]: http://z\n", "[z]: http://z", "see [z] here\n\n[z]: http://z\n\n^abc\n"),
    ("a | b\n--- | ---\n\npara\n", "a | b\n--- | ---", "a | b\n--- | ---\n\n^abc\n\npara\n"),
    ("<div>\nx\n</div>\n", "<div>\nx\n</div>", "<div>\nx\n</div>\n\n^abc\n"),
    ("<!-- c -->\n", "<!-- c -->", "<!-- c -->\n\n^abc\n"),
    ("<br>\n\npara\n", "<br>", "<br>\n\n^abc\n\npara\n"),
    ("text\n> quote\n", "text\n> quote", "text\n> quote\n\n^abc\n"),
])
def test_an_anchor_ends_only_a_line_commonmark_reads_as_text(raw, content, text):
    """A definition, a table's rows, HTML and a quote that breaks into a
    paragraph are text to this parser only."""
    assert anchored(raw, content) == text


@pytest.mark.parametrize("raw,old,new,text", [
    ("x\n\n---\n| x |\n|---|\n| 1 |\n\n^tb8\n", "---\n| x |\n|---|\n| 1 |", "--- alpha\n| x |\n|---|\n| 1 |",
     "x\n\n--- alpha\n| x |\n|---|\n| 1 |\n\n^tb8\n"),
    ("x\n\n---\n> quote\n\n^qt3\n", "---\n> quote", "--- charlie\n> quote", "x\n\n--- charlie\n> quote\n\n^qt3\n"),
])
def test_an_anchor_on_its_own_line_stays_there_when_its_block_is_retyped_into_text(raw, old, new, text):
    assert anchor_kept(raw, old, new) == text


def test_a_list_closed_only_to_this_parser_stays_open_past_a_lazy_line():
    assert inserted("- a\nlazy\n", "lazy", "    code") == "- a\nlazy\n\n```\ncode\n```\n"
    assert inserted("- a\nlazy\n", "lazy", "  indented") == "- a\nlazy\n\nindented\n"


def test_a_new_marker_keeps_a_plain_child_within_the_items_text():
    """Four columns past the item's text it is code; a child this parser reads
    as text there, and CommonMark as code, keeps its place in the item."""
    text = edited("10. a\n\n      para\n", "10. a", "- a")
    assert text == "- a\n\n     para\n"
    assert contents(tree_of(parse(text))) == [("- a", ["para"])]
    text = edited("- a\n\n      code\n", "- a", "10. a")
    assert text == "10. a\n\n        code\n"
    assert contents(tree_of(parse(text))) == [("10. a", ["code"])]
    assert edited("10. a\n      - b\n", "10. a", "- a") == "- a\n     - b\n"


def test_text_left_of_an_items_text_past_a_blank_line_is_out_of_the_list():
    raw = "1. a\n   - b\n\n  p\n"
    assert contents(tree_of(parse(raw))) == [("1. a", ["- b"]), "p"]
    assert without(raw, "- b") == "1. a\n\n  p\n"
    assert edited(raw, "- b", "- b two") == "1. a\n   - b two\n\n  p\n"


def test_a_rule_written_as_a_list_is_a_rule():
    """CommonMark reads a rule before a list item: ``* * *`` holds nothing."""
    doc, ids, tree = read("* * *\n\nb\n")
    assert contents(tree) == ["* * *", "b"]
    tree[0]["children"].append(tree.pop(1))
    assert shape(doc, ids, tree)[1] == "- * * *\n  - b\n"
    assert edited("* * *\n", "* * *", "* * zed") == "- * * zed\n"               # a bullet that holds a bullet


def test_code_by_indentation_under_a_setext_heading_stays_code():
    assert edited("s\n===\n\t- t\n", "s\n===", "___") == "___\n\t- t\n"
    assert edited("# Title\n    indented code\n", "# Title", "# Title\ntext under it") == (
        "# Title\ntext under it\n```\nindented code\n```\n")


@pytest.mark.parametrize("raw,old,new,text", [
    ("1. a ^abc\n", "1. a", "1. a\n- b", "1. a ^abc\n- b\n"),
    ("1. a ^abc\n", "1. a", "- b\n- c", "- b ^abc\n- c\n"),
    ("10. a\n\n    ^abc\n", "10. a", "- a", "- a\n\n  ^abc\n"),
])
def test_an_anchor_stays_with_its_block_at_the_text_of_the_marker_now_written(raw, old, new, text):
    """Of a block the file reads as several, the first keeps the id, and the
    anchor."""
    doc, ids, tree = read(raw)
    node_with(tree, old)["content"] = new
    shaped, written = shape(doc, ids, tree)
    assert written == text
    assert [n["id"] for n in flatten(shaped) if n.get("anchor") == "abc"] == ["b0"]


def test_a_block_moved_where_it_splits_keeps_its_anchor_on_its_first_part():
    doc, ids, tree = read("p\n\n- q\n")
    tree[1]["children"].append({"id": "new", "content": "# head\ntext under it", "children": [], "anchor": "abc"})
    shaped, text = shape(doc, ids, tree)
    doc, ids = parse(text), [n["id"] for n in flatten(shaped)]
    tree = tree_of(doc, ids)
    tree.insert(0, tree[1]["children"].pop())
    shaped, text = shape(doc, ids, tree)
    assert text.startswith("# head ^abc\ntext under it\n")
    assert [n["id"] for n in flatten(shaped) if n.get("anchor") == "abc"] == ["new"]


def test_text_the_file_comes_to_read_as_an_anchor_keeps_its_dress():
    """A copy of an anchor is text while the anchor comes first; renamed, the
    copy would be read as one, so its caret is escaped where it stands."""
    doc, ids, tree = read("a ^x\n\n# T\npara\n\n  ^x\n")
    assert contents(tree) == ["a", ("# T", ["para", "^x"])]
    tree[0]["anchor"] = "y"
    shaped, text = shape(doc, ids, tree)
    assert text == "a ^y\n\n# T\npara\n\n  \\^x\n"
    assert tree_equal(shaped, tree)


def test_patch_front_values_read_back_as_they_were_written():
    from gamma.markdown_import import _fm_scalar, parse_frontmatter, yaml_text
    for value in ("a\tb", "line one\nline two", 'say "hi"\tthere', "C:\\temp", "x\x0cy"):
        text = patch_front(parse("---\na: 1\n---\nbody\n"), {"title": value})
        assert parse(text).front.fields["title"] == value, text
        assert parse_frontmatter(f"---\ntitle: {yaml_text(value)}\n---\n", controls=True)[0]["title"] == value
    assert _fm_scalar('"C:\\temp\\new"', controls=True) == "C:\\temp\\new"      # a path typed by hand


def test_shape_and_match_stay_linear_in_blocks_alike():
    """Quadratic, either takes minutes. ``shape`` reads both texts with
    markdown-it too (``_reads_otherwise``), about a second of this."""
    started = time.perf_counter()
    doc, ids, tree = read("".join(f"p{i}\n\n" for i in range(20000)))
    tree[10000]["content"] = "edited"
    assert "\n\nedited\n\n" in shape(doc, ids, tree)[1]
    old = [{"content": "same"}] * 20000
    new = [dict(block) for block in old]
    new[10000] = {"content": "other"}
    assert len(match(old, new)) == 19999
    assert time.perf_counter() - started < 7


# --- the adversaries' third round -------------------------------------------------------

def moved(raw: str, change) -> tuple:
    """``raw`` read, ``change(tree)`` done, through ``shape``."""
    doc, ids, tree = read(raw)
    change(tree)
    return shape(doc, ids, tree)


@pytest.mark.parametrize("raw,text", [("- a ^x\n", "﻿﻿- a ^x\n"), ("x\n\n- a ^x\n", "x\n\n﻿- a ^x\n")])
def test_a_block_opening_on_a_zero_width_space_keeps_its_anchor(raw, text):
    """A block's text is read as a run of blocks, never as a file, whose U+FEFF
    would be its byte-order mark."""
    assert anchor_kept(raw, "- a", "﻿- a") == text


def test_a_zero_width_space_a_file_comes_to_open_with_takes_a_byte_order_mark_before_it():
    """Or it would be the mark, and the block's text would lose it."""
    shaped, text = moved("x\n\n﻿a\n", lambda tree: tree.append(tree.pop(0)))
    assert text == "﻿﻿a\n\nx\n" and shaped[0]["content"] == "﻿a"
    assert without("$$\n$$\n﻿a\n", "$$\n$$") == "﻿﻿a\n"
    assert edited("- a\n", "- a", "﻿- a") == "﻿﻿- a\n"
    assert contents(tree_of(parse("﻿﻿- a\n"))) == ["﻿- a"]


@pytest.mark.parametrize("raw", ["a\n\t- a\n", "a\n    - a\n", "a\n \t- a\n"])
def test_a_line_four_columns_in_after_a_paragraph_stays_where_the_file_has_it(raw):
    """CommonMark reads it as the paragraph's next line, this parser as a list
    item: an edit of the paragraph keeps them as the file had them, and puts
    no blank line between, which would make the item code."""
    after = raw.split("\n", 1)[1]
    assert edited(raw, "a", "a b") == f"a b\n{after}"
    assert anchored(raw, "a", "zz1") == f"a ^zz1\n{after}"
    assert edited(raw, "a", "a ") == f"a \n{after}"
    assert contents(tree_of(parse(edited(raw, "a", "a b")))) == ["a b", "- a"]


def test_a_list_four_columns_in_brought_to_the_top_comes_whole():
    text = without("p\n\t- a\n\t- b\n", "p")
    assert text == "   - a\n   - b\n"
    assert contents(tree_of(parse(text))) == ["- a", "- b"]


def test_code_by_indentation_given_a_child_stays_the_item_this_parser_reads():
    """Code holds no child: what a block was moved under is the item it is to
    Gamma, so it stays one, at a column that keeps it one. CommonMark read it
    as code, and would read the item: refused."""
    for raw in ("```\n```\n\t- x\n", "$$\n$$\n\t- x\n"):
        doc, ids, tree = read(raw)
        tree[1]["children"].append(tree.pop(0))
        text = write(doc, ids, tree)[0]
        assert contents(tree_of(parse(text))) == [("- x", [raw.split("\n\t")[0]])], text
        with pytest.raises(Unwritable, match=MEANING):
            shape(doc, ids, tree)


@pytest.mark.parametrize("raw,old,new,text", [
    ("1.\t[ ] a\n\n   p\n", "1. [ ] a", "1. [ ] a b", "1.\t[ ] a b\n\n   p\n"),
    ("  1.\t[x]\n\n  \t1\n", "1. [x]", "1. [x] c", "  1.\t[x] c\n\n  \t1\n"),
    ("1.  [ ] a\n", "1. [ ] a", "2. [x] a", "2.  [x] a\n"),
])
def test_a_numbers_spacing_comes_back_whatever_its_box(raw, old, new, text):
    """A number's box is its text, as the parser reads it: the marker as
    written is the number and its spacing."""
    assert edited(raw, old, new) == text
    assert edited(text, new, old) == raw


@pytest.mark.parametrize("raw,text", [("- \n\n - x\n", "- a\n\n- x\n"), ("* \n\n + \n", "* a\n\n+ \n")])
def test_text_typed_into_an_empty_item_leaves_the_item_after_it_a_sibling(raw, text):
    """The blank line ended the empty item; with text it goes on, and the item
    after it, one column in, would be its child here."""
    assert edited(raw, "- ", "- a") == text
    assert len(tree_of(parse(text))) == 2


@pytest.mark.parametrize("raw,old,new,text", [
    ("10. c\n\n   p\n", "10. c", "- x", "- x\n\np\n"),
    ("10. a\n    - kid\n\n   p\n", "10. a", "9. a", "9. a\n    - kid\n\np\n"),
    ("1. a\n   - kid\n\n  p\n", "1. a", "- a", "- a\n   - kid\n\np\n"),
    ("1. a\n\n    kid\n\n  p\n", "1. a", "> q", "- > q\n\n    kid\n\np\n"),
])
def test_text_after_an_item_whose_text_moved_left_stays_out_of_it(raw, old, new, text):
    """Past a blank line a block at the item's text is in it: one that was left
    of the old text goes out of the list, as text a list stands before does."""
    assert edited(raw, old, new) == text
    assert contents(tree_of(parse(text)))[-1] == "p"


@pytest.mark.parametrize("raw,text", [(" # x ^b\n", " # x\n more\n\n ^b\n"), ("  ## x ^b\n", "  ## x\n  more\n\n  ^b\n"),
                                      ("   # x ^b\n", "   # x\n   more\n\n   ^b\n")])
def test_a_heading_held_back_by_its_indent_keeps_its_anchor_when_it_gains_a_line(raw, text):
    """The indent that holds it back stays in what reads where its anchor can
    go: CommonMark reads a heading, then the text, which no anchor ends."""
    content = raw.strip()[:-3]
    assert anchor_kept(raw, content, f"{content}\nmore") == text


@pytest.mark.parametrize("raw,old,new,text", [
    ("a ^c\n", "a", "-  [ ]", "-  [ ]\n   ^c\n"),
    ("a ^c\n", "a", "2.  [X]", "2.  [X]\n    ^c\n"),
    ("a ^c\n", "a", "1.  q\n>", "1.  q\n    >\n    ^c\n"),
    ("+\ta ^c\n", "- a", "- >", "+ - >\n\n  ^c\n"),
    ("- b ^c\n", "- b", "-\t[ ]", "-\t[ ]\n    ^c\n"),
    ("a ^c\n", "a", "2. [X]", "2. [X]\n   ^c\n"),
])
def test_an_anchor_stays_on_an_item_whatever_its_marker_is_written_with(raw, old, new, text):
    """Past a blank line this parser reads no line in an item whose text is
    wider than its marker and a space: the anchor goes straight under it. It
    ends a box with nothing after it, which is a paragraph."""
    assert anchor_kept(raw, old, new) == text


@pytest.mark.parametrize("raw,content,text", [
    ("a\n\nb\n", "- ```\ncode\n```", "a\n\n- - ```\n    code\n    ```\n\nb\n"),
    ("p\n\nq\n", "- ```\n  x\n  ```", "p\n\n- - ```\n    x\n    ```\n\nq\n"),
    ("p\n\n    code\n", "- ```\n  x\n  ```", "p\n\n- - ```\n    x\n    ```\n\n```\ncode\n```\n"),
])
def test_a_bullet_holding_a_fence_is_written_closed_before_what_follows(raw, content, text):
    """The fence its text opens runs to its close in the bullet that wraps it,
    and its lines stand under the bullet it holds, as remark reads them."""
    doc, ids, tree = read(raw)
    tree.insert(1, {"id": "new", "content": content, "children": []})
    shaped, written = shape(doc, ids, tree)
    assert written == text and tree_equal(shaped[:2], tree[:2])                  # (code after a list is fenced)
    doc, ids, tree = read("a\n\n- ```\n  code\n  ```\n\nb\n")
    tree[1]["content"] = "- ```\ncode2\n```"
    shaped, written = shape(doc, ids, tree)
    assert written == "a\n\n- - ```\n    code2\n    ```\n\nb\n" and tree_equal(shaped, tree)


def test_a_first_block_that_is_only_a_caret_and_a_word_is_text_with_no_escape():
    text = edited("^x\n", "^x", "^x bravo")
    assert edited(text, "^x bravo", "^x") == "^x\n"
    assert edited("- a\n  - ^x\n", "- ^x", "- ^x two") == "- a\n  - ^x two\n"


@pytest.mark.parametrize("raw,text", [("- a\n- b\n^id\n", "- a2\n- b\n^id\n"),
                                      ("- a\n  - b\n  ^id\n", "- a2\n  - b\n  ^id\n")])
def test_an_anchor_on_the_lazy_line_of_an_item_stays_its(raw, text):
    """Read as the item's, it joins no block, so no blank line goes before it."""
    assert edited(raw, "- a", "- a2") == text
    assert moved(raw, lambda tree: tree.insert(0, {"id": "new", "content": "# T", "children": []}))[1] == f"# T\n{raw}"
    assert anchored(raw, "- a", "zz9") == raw.replace("- a\n", "- a ^zz9\n", 1)


def test_a_sub_item_after_a_lazy_line_stays_a_list_item():
    """Four columns in, after text and no list, it is that text's next line to
    CommonMark, and after a blank line code: it comes to three. A bullet put
    between the item and its lazy line would take that line from the item:
    another app would read it otherwise."""
    raw = "- item one\ncontinued here\n    - sub item\n"
    assert contents(tree_of(parse(raw))) == ["- item one", "continued here", "- sub item"]
    tabbed = raw.replace("    -", "\t-")
    for text in (without(raw, "- item one"), edited(raw, "- item one", "# item one"),
                 edited(tabbed, "- item one", "    code")):
        assert text.endswith("continued here\n   - sub item\n"), text
        assert node_with(tree_of(parse(text)), "- sub item")
    with pytest.raises(Unwritable, match=MEANING):
        inserted(raw, "- item one", "- new")


@pytest.mark.parametrize("raw,old,new,text", [
    ("* a\n* b\n", "- a", "- **", "- **\n* b\n"),
    ("* a\n\t- t\n", "- a", "- * * *", "* - * * *\n\t- t\n"),
])
def test_a_bullet_whose_text_is_stars_keeps_a_mark_that_makes_no_rule(raw, old, new, text):
    written = edited(raw, old, new)
    assert written == text
    assert node_with(tree_of(parse(written)), new)


@pytest.mark.parametrize("opener", ["<!-- c", "<style>", "<script>", "<pre>"])
def test_html_typed_into_an_items_paragraph_is_held_back_as_at_the_top(opener):
    """An item's lines are read from its text, which four columns of indent put
    at the left edge of its HTML."""
    with pytest.raises(Unwritable, match=OPEN_BLOCK):
        edited("* a\n\n    a\n\n    b\n", "a", opener)


def test_html_typed_into_an_items_paragraph_keeps_clear_of_the_block_after_it():
    text = edited("- a\n\n    p\n\t- t\n", "p", "<div>zed")
    assert text == "- a\n\n    <div>zed\n\n\t- t\n"
    assert contents(tree_of(parse(text))) == [("- a", ["<div>zed", "- t"])]


@pytest.mark.parametrize("raw,old,new,text", [
    ("- \n\n    code\n", "- ", "- zed\n  # x", "- zed\n  # x\n\n```\ncode\n```\n"),
    ("1. \n\n\tcode\n", "1. ", "1. zed\n   ---", "1. zed\n   ---\n\n```\ncode\n```\n"),
])
def test_code_after_an_empty_item_given_text_of_any_ending_stays_code(raw, old, new, text):
    """With text the item goes on past the blank line, whatever its last line
    leaves open, and takes the code in: it becomes a fence after the list."""
    assert edited(raw, old, new) == text


def test_a_bullet_whose_text_is_a_block_is_written_one_way_wherever_it_comes_from():
    """A bullet that only wraps a block is how the vault writes a child
    (``- # H`` is the heading ``# H``), so a bullet holding a heading needs a
    bullet of its own around it, new or retyped; ``- # H`` would read back as
    the heading, and Gamma's bullet would go."""
    for raw, old in (("p\n", "p"), ("- a\n", "- a")):
        for new in ("- # H", "- > q", "- - zed"):
            text = edited(raw, old, new)
            assert text == f"- {new}\n" and contents(tree_of(parse(text))) == [new]
    assert inserted("- a\n", "- a", "- # H") == "- a\n- - # H\n"
    assert render([{"id": "n", "content": "- # H", "children": []}]) == "- - # H\n"


@pytest.mark.parametrize("new,text", [("- - zed", "- - - zed\n\n          code\np\n"),
                                      ("- 1) zed", "- - 1) zed\n\n           code\np\n")])
def test_code_under_a_bullet_that_holds_one_moves_with_the_text_of_the_one_it_holds(new, text):
    """CommonMark counts code by indentation from the innermost marker's text."""
    written = edited("- a\n\n      code\np\n", "- a", new)
    assert written == text
    assert contents(tree_of(parse(written))) == [(new, ["code"]), "p"]


def test_a_number_that_cannot_break_into_a_paragraph_is_its_next_line():
    raw = "Later still, the cap\n8192. Raise the cap was declined\nas the deciding reason.\n"
    assert len(tree_of(parse(raw))) == 1
    assert len(md_to_blocks(raw)) == 3                                          # the importer's reading stands
    assert inserted(raw, raw.strip(), "new para") == raw + "\nnew para\n"
    assert contents(tree_of(parse("1. a\nlazy\n2. b\n"))) == ["1. a", "lazy", "2. b"]       # a list goes on
    assert contents(tree_of(parse("Title\n===\n10. ten\n"))) == ["Title\n===", "10. ten"]   # a heading ends it
    assert contents(tree_of(parse("1. a\n\n   p\n2. b\n")))[-1] == "2. b"


@pytest.mark.parametrize("line", ["\xa0", "\x0c", "　 \xa0"])
def test_a_line_of_other_spacing_is_text(line):
    """Only spaces and tabs make a line blank to CommonMark: a line of no-break
    spaces or a form feed is text, a line of a block or a block of its own,
    which a change copies once and deletes only when asked."""
    raw = f"a\n{line}\nb\n"
    assert contents(tree_of(parse(raw))) == [f"a\n{line}\nb"]
    assert inserted(raw, f"a\n{line}\nb", "zed") == raw + "\nzed\n"
    raw = f"a\n\n{line}\n\nb\n"
    assert contents(tree_of(parse(raw))) == ["a", line, "b"]
    assert without(raw, "a") == f"{line}\n\nb\n"
    assert inserted(raw, "a", "zed") == f"a\n\nzed\n\n{line}\n\nb\n"


def test_a_line_of_no_break_spaces_is_neither_lost_nor_copied():
    """The verifier's cases: kept whole, the line takes no item that cannot
    break into text, a deletion beside it leaves it, and a block inserted
    between an item and it, its lazy line, is refused."""
    assert inserted("\xa0\n10. a\n", "\xa0\n10. a", "11. new item") == "\xa0\n10. a\n\n11. new item\n"
    raw = "10. ten\n11. eleven\n~~~\ncode\n~~~\n\xa0\n-\n- after\n"
    assert "\n\xa0\n-\n- after\n" in without(raw, "~~~\ncode\n~~~")
    with pytest.raises(Unwritable, match=MEANING):
        inserted("- a\n\xa0\n- b\n", "- a", "- new")


def test_a_line_of_no_break_spaces_typed_in_gamma_is_text_in_the_file():
    assert edited("a\nb\n", "a\nb", "a\n\xa0\nb") == "a\n\xa0\nb\n"
    assert edited("> q\n>\xa0\n> r\n", "> q\n>\xa0\n> r", "> q\n>\n> r") == "> q\n>\n> r\n"
    assert edited("> q\n>\n> r\n", "> q\n>\n> r", "> q\n>\xa0\n> r") == "> q\n>\xa0\n> r\n"


@pytest.mark.parametrize("space", ["\xa0", "\x0c", "　", "\x85"])
def test_code_of_other_spacing_is_code_wherever_the_walk_takes_it(space):
    """The verifier's crash: a line of no-break spaces four columns in is code,
    which the walk moves, fences and measures as text, never as a blank line."""
    doc, ids, tree = read(f"    code\nmore\n\n{space}\n")
    tree.insert(1, tree.pop(0))
    assert shape(doc, ids, tree)[1] == f"more\n\n    code\n\n{space}\n"
    for lead in ("    ", "\t"):
        doc, ids, tree = read(f"a\n\n{lead}{space}\n")
        tree[0]["children"].append(tree.pop(1))
        assert shape(doc, ids, tree)[1] == f"- a\n  - ```\n    {space}\n    ```\n"
        assert child_added(f"a\n\n{lead}{space}\n", "a", "- new") == f"- a\n  - new\n\n```\n{space}\n```\n"


@pytest.mark.parametrize("raw,old,new,text", [
    ("- a\n\npara\n", "para", "- x", "- a\n\n- x\n"),
    ("- a\n\n* * *\n", "* * *", "* * * x", "- a\n\n- * * * x\n"),
    ("- a\n\n> q\n", "> q", "- x", "- a\n\n- x\n"),
])
def test_a_block_retyped_into_an_item_after_a_list_keeps_the_blank_line_it_had(raw, old, new, text):
    assert edited(raw, old, new) == text


def test_spacing_after_a_marker_is_what_parse_normalises():
    for content, back in (("- [ ]  alpha", "- [ ] alpha"), ("-  alpha", "- alpha"), ("1.  alpha", "1. alpha"),
                          ("* a", "- a"), ("+ [X] a", "- [x] a"), ("-\t[ ]", "- [ ] ")):
        assert tree_equal([{"content": content, "children": []}], [{"content": back, "children": []}])
    assert not tree_equal([{"content": "- a", "children": []}], [{"content": "1. a", "children": []}])
    assert not tree_equal([{"content": "* * *", "children": []}], [{"content": "- * *", "children": []}])
    for raw, new in (("- [ ] x\n", "- [ ]  alpha"), ("- x\n", "-  alpha"), ("1. x\n", "1.  alpha")):
        doc, ids, tree = read(raw)
        tree[0]["content"] = new
        assert tree_equal(shape(doc, ids, tree)[0], tree)


def test_the_gate_catches_a_walk_that_adds_a_blank_line_beside_an_edited_block(monkeypatch):
    """Law 2: a tight list edited stays tight."""
    gap = md_lens._Walk._gap

    def sabotaged(self, piece, child=False):
        lines = gap(self, piece, child)
        return lines or ([self.doc.eol] if piece.edited and piece.tile and self.prev else [])

    raw = "".join(f"- item {k}\n" for k in range(12)) + "\npara\n"
    assert gate.gate_file("clean", raw, gate.Counter(), 30, gate.SEED) == []
    monkeypatch.setattr(md_lens._Walk, "_gap", sabotaged)
    assert any("law 2" in w for w in gate.gate_file("sabotaged", raw, gate.Counter(), 30, gate.SEED))


def test_the_gate_catches_a_walk_that_moves_a_childs_indent_for_no_reason(monkeypatch):
    """Law 5: a block in a list item moves only with the item's text, or where
    its old indent would read as something else."""
    fitted = md_lens._Walk._fitted

    def sabotaged(self, piece, old, node, place, at):
        piece = fitted(self, piece, old, node, place, at)
        if self.prev and self.prev.tile is None and not place.flow and piece.kind == "plain" and not piece.edited:
            piece.lines = [" " + line if line.strip() else line for line in piece.lines]
        return piece

    raw = "".join(f"- item {k}\n  - sub {k}\n\n  back in {k}\n" for k in range(6))
    assert gate.gate_file("clean", raw, gate.Counter(), 30, gate.SEED) == []
    monkeypatch.setattr(md_lens._Walk, "_fitted", sabotaged)
    assert any("law 5: bytes" in w for w in gate.gate_file("sabotaged", raw, gate.Counter(), 30, gate.SEED))


def test_the_importer_reads_front_matter_as_it_did_and_the_vault_as_it_writes_it():
    from gamma.markdown_import import parse_frontmatter
    for value, text in (('"a\\u000ab"', "a\nb"), ('"a\\u0009b"', "a\tb"), ('"a\\u001bb"', "a\x1bb"),
                        ('"\\u0007"', "\x07")):
        front = f"---\ntitle: {value}\n---\nbody\n"
        assert parse_frontmatter(front)[0]["title"] == value[1:-1]                # no control character in a title
        assert parse(front).front.fields["title"] == text


def test_an_edited_rule_opens_no_list():
    assert edited("* * *\n\n  b\n", "* * *", "* * *\n  x") == "* * *\n  x\n\n  b\n"


# --- the adversaries' fourth round -------------------------------------------------------

@pytest.mark.parametrize("raw", [" ~~~\n\t ~~~\n^k\n\n", "- ```\n      ```\n ```\n^b\n\t"])
def test_an_open_fence_ending_in_a_blank_line_comes_back_whole(raw):
    """Its text, which holds that blank line, is the text read: a straight copy."""
    assert check_read(parse(raw), raw) == []


@pytest.mark.parametrize("raw,new,text", [
    ("# Title\n^x\n", "Title", "Title\n\n\\^x\n"),
    ("# Title\n\n^x\n", "Title", "Title\n\n\\^x\n"),
    ("# a\n^b\n", "> a", "> a\n\n\\^b\n"),
    ("# Title\n^x\n", "# Title\nmore", None),
])
def test_a_caret_and_a_word_under_a_heading_that_changes_stays_text(raw, new, text):
    """First under its heading it was text; after the text that heading becomes
    it would be that text's anchor, so its caret is escaped."""
    doc, ids, tree = read(raw)
    tree[0]["content"] = new
    shaped, written = shape(doc, ids, tree)
    assert text is None or written == text
    assert [md_lens._normal(n["content"]) for n in flatten(shaped) if n["id"] == "b1"] == [raw.split("\n")[-2]]


@pytest.mark.parametrize("raw,old,new,text", [
    ("a\n", None, "- - ", "a\n* - - \n"),
    ("- a\n", "- a", "- - ", "* - - \n"),
    ("- -\n", "- -", "- -  ", "- -  \n"),
    ("a\n", None, "-  - ", "a\n* -  - \n"),
    ("- a\n  - b\n", "- a", "- - ", "* - - \n  - b\n"),
    ("- a\n- b\n", "- a", "- - ", "* - - \n- b\n"),
    ("- a\n\t- b\n", "- a", "- - ", "* - - \n\t- b\n"),
])
def test_a_bullet_typed_into_an_item_is_no_rule(raw, old, new, text):
    """``- - `` under a bullet of its own mark would be ``- - -``: it takes the
    other mark. Its body is read as the parser reads it, spaces and all."""
    written = edited(raw, old, new) if old else inserted(raw, raw.strip(), new)
    assert written == text
    back = tree_of(parse(written))
    assert md_lens._normal(flatten(back)[0 if old else 1]["content"]) == md_lens._normal(new)
    assert len(back) == len(tree_of(parse(raw))) + (0 if old else 1)


@pytest.mark.parametrize("raw", ["a\n    - a\n    a\n", "a\n\t- a\n\ta\n", "p\n\t- a\n\t^x\n"])
def test_the_lines_after_a_list_four_columns_in_after_a_paragraph_stay_as_the_file_has_them(raw):
    """Read after the paragraph they follow, not as the start of a file, where
    four columns are code."""
    first = raw.split("\n")[0]
    text = edited(raw, first, f"{first} q")
    assert text == raw.replace(first, f"{first} q", 1)
    assert tree_equal(tree_of(parse(text))[1:], tree_of(parse(raw))[1:])


def test_text_four_columns_in_after_a_list_brought_to_the_top_stays_text():
    """It stays text to this parser, but CommonMark read the lines as the
    paragraph's, and would read a list: refused."""
    doc, ids, tree = read("1\n\t- a\n\ta\n")
    tree.append(tree.pop(0))
    text = write(doc, ids, tree)[0]
    assert text == "   - a\n   a\n\n1\n"
    assert contents(tree_of(parse(text))) == ["- a", "a", "1"]
    with pytest.raises(Unwritable, match=MEANING):
        shape(doc, ids, tree)


def test_code_after_an_empty_quote_that_gains_text_keeps_its_anchor():
    """An empty quote holds no paragraph: CommonMark read the item as code,
    which the quote's text would take as a lazy line. It becomes a fence."""
    shaped, text = moved(">\n\t- a\n\t^x\n", lambda tree: tree[0].update(content="> q"))
    assert [(n["content"], n.get("anchor")) for n in shaped] == [("> q", None), ("```\n- a\n```", "x")]


@pytest.mark.parametrize("raw,text", [
    ("1. a\n  ```\n  x\n  ```\n", "1. a\n  ```\n  x\n  ```\n\n  ^zz1\n"),
    ("- a\n $$\n x\n $$\n", "- a\n $$\n x\n $$\n\n ^zz1\n"),
    ("- \n $$\n $$\n", "- \n $$\n $$\n\n ^zz1\n"),
])
def test_an_anchor_on_a_block_left_of_its_items_text_lands_on_that_block(raw, text):
    """A fence or ``$$`` there is no lazy line: it ends the list, to this parser
    as to CommonMark, and its anchor follows it."""
    doc, ids, tree = read(raw)
    node = flatten(tree)[1]
    node["anchor"] = "zz1"
    shaped, written = shape(doc, ids, tree)
    assert written == text
    assert [n.get("anchor") for n in flatten(shaped)] == [None, "zz1"]


@pytest.mark.parametrize("raw,parent,text", [
    ("- a\n - b\n -\n", "- b", "- a\n - b\n   - k\n -\n"),
    ("- a\n-\n", "- a", "- a\n  - k\n-\n"),
])
def test_a_bare_marker_after_a_new_child_is_the_list_item_it_was(raw, parent, text):
    """CommonMark reads ``-`` alone as an empty item, which needs no blank line
    after an item; this parser reads it as text."""
    written = first_child(raw, parent, "k")
    assert written == text
    assert [n["content"] for n in tree_of(parse(written))] == [n["content"] for n in tree_of(parse(raw))]


def test_an_empty_child_item_ends_only_itself_at_a_blank_line():
    text = first_child("a\n\n  p\n", "a", "- ")
    assert text == "- a\n\n  - \n\np\n"
    assert contents(tree_of(parse(text))) == [("- a", ["- "]), "p"]


def test_a_paragraph_first_under_an_item_with_nothing_on_its_line_becomes_an_item():
    """CommonMark reads it as that item's text, and past a blank line the item
    has ended: no file holds both, so it is written as a child is."""
    shaped, text = moved("- \n  - x\n  para\n", lambda tree: tree[0]["children"].pop(0))
    assert text == "- \n  - para\n"
    assert [(n["id"], n["content"]) for n in flatten(shaped)] == [("b0", "- "), ("b2", "- para")]


def test_a_first_child_keeps_clear_of_the_text_of_its_parent_and_of_its_siblings():
    text = first_child("- a\n\n   p\n - c\n", "- a", "e")
    assert text == "- a\n  - e\n\n   p\n - c\n"
    assert contents(tree_of(parse(text))) == [("- a", ["- e", "p", "- c"])]


def test_an_item_left_of_the_text_of_the_item_above_needs_no_blank_line():
    """It goes on the list that item is in, which breaks into no paragraph."""
    assert edited("1. a\n 1. b\n", "1. b", "8. b") == "1. a\n 8. b\n"


@pytest.mark.parametrize("raw,change,text", [
    ("p\n\n    a\n", lambda tree: tree[0].update(content="p\n\n    x"), "p\n\n    x\n\n```\na\n```\n"),
    ("p\n\n    a\n", lambda tree: tree.insert(1, {"id": "new", "content": "q\n\n    x", "children": []}),
     "p\n\nq\n\n    x\n\n```\na\n```\n"),
    ("# h\n\n    a\n", lambda tree: tree[0].update(content="# h\n\n    x"), "# h\n\n    x\n\n```\na\n```\n"),
    ("---\n\n    a\n", lambda tree: tree[0].update(content="---\n    x"), "---\n    x\n\n```\na\n```\n"),
    ("---\n\n    a\n\n    b\n", lambda tree: tree[0].update(content="---\n\t- x"), "---\n\t- x\n\n```\na\n\nb\n```\n"),
])
def test_code_after_a_block_that_ends_in_code_is_a_block_of_its_own(raw, change, text):
    """Two runs of code by indentation, a blank line between, are one: the
    second, which was a block, becomes a fence."""
    shaped, written = moved(raw, change)
    assert written == text
    assert flatten(shaped)[-1]["id"] == "b1"


@pytest.mark.parametrize("raw", ["- a\n  - b\n", "- a\n\t- b\n", "10. a\n    - b\n", "1. a\n    1. b\n"])
def test_html_that_runs_to_a_blank_line_typed_into_an_item_keeps_its_child_out(raw):
    first = raw.split("\n")[0]
    text = edited(raw, first, f"{first}\n<div>")
    assert "<div>\n\n" in text
    assert contents(tree_of(parse(text)))[0][1] == [raw.split("\n")[1].strip()]


@pytest.mark.parametrize("raw,new,text", [
    ("- a\n\n      code\ns\n===\n", "- a\n  1. x", "- a\n  1. x\n\n         code\ns\n===\n"),
    ("- a\n\n      code\ns\n===\n", "- zed\n1. x", "- zed\n1. x\n\n       code\ns\n===\n"),
    ("- a\n\n      a\n", "- a\n\n      x", "- a\n\n      x\n\n  ```\n  a\n  ```\n"),
])
def test_code_under_an_item_stays_code_under_the_list_its_new_text_opens(raw, new, text):
    """Code by indentation counts from the text of the item open last."""
    assert edited(raw, raw.split("\n")[0], new) == text


@pytest.mark.parametrize("raw,new,text", [
    ("- a\n\n      code\n", "- ", "- \n  ```\n  code\n  ```\n"),
    ("1. a\n\n       code\n", "1. ", "1. \n   ```\n   code\n   ```\n"),
])
def test_code_under_an_item_emptied_is_fenced_in_it(raw, new, text):
    """A blank line ends an item with nothing on its line, and no blank line
    makes the code its text: a fence stays in the item."""
    written = edited(raw, raw.split("\n")[0], new)
    assert written == text
    assert contents(tree_of(parse(written))) == [(new, [md_lens._fenced("code".rjust(8))])]


@pytest.mark.parametrize("raw,change,text", [
    ("a\n\n[ ] x\n", lambda tree: tree[1]["children"].append(tree.pop(0)), "- \\[ ] x\n  - a\n"),
    ("a\n\n[x] done\n", lambda tree: tree[1]["children"].append(tree.pop(0)), "- \\[x] done\n  - a\n"),
    ("[ ] x\n", lambda tree: tree[0]["children"].append({"id": "new", "content": "kid", "children": []}),
     "- \\[ ] x\n  - kid\n"),
])
def test_text_that_reads_as_a_box_given_children_is_no_task(raw, change, text):
    shaped, written = moved(raw, change)
    assert written == text
    assert not any(n["content"].startswith("- [") for n in flatten(shaped))


@pytest.mark.parametrize("raw,new,text", [
    ("- a\nlazy\n- \n", "- #", "- #\nlazy\n\n- \n"),
    ("- a\nlazy\n3. b\n", "- zed\n===", "- zed\n  ===\nlazy\n\n3. b\n"),
])
def test_an_item_that_comes_to_end_in_a_heading_takes_no_lazy_line(raw, new, text):
    """``#`` alone is an empty heading, and ``===`` under text an underline:
    the line after it is a paragraph of its own, which the next item cannot
    break into."""
    assert edited(raw, "- a", new) == text


@pytest.mark.parametrize("raw,old,new,text", [
    ("[^1]: note\n\t- t\n", "[^1]: note", "# zed", "# zed\n   - t\n"),
    ("[^1]: note\n\t- t\n", "[^1]: note", "zed\n===", "zed\n===\n   - t\n"),
    ("[^1]: note\n\t- t\n", "[^1]: note", "$$\nzed\n$$", "$$\nzed\n$$\n   - t\n"),
    ("[r]: /u\n\t- t\n", "[r]: /u", "# r", "# r\n   - t\n"),
])
def test_an_item_four_columns_in_after_a_block_that_holds_no_text_stays_an_item(raw, old, new, text):
    """There, four columns are code: the item comes to three. CommonMark read
    code after the definition already, and would read the item: refused."""
    doc, ids, tree = read(raw)
    node_with(tree, old)["content"] = new
    written = write(doc, ids, tree)[0]
    assert written == text
    assert node_with(tree_of(parse(written)), "- t")
    with pytest.raises(Unwritable, match=MEANING):
        shape(doc, ids, tree)


@pytest.mark.parametrize("change,text", [
    (lambda tree: tree[0].update(anchor="zz"), "<!-- c -->\n\n^zz\n```\n- t\n```\n"),
    (lambda tree: tree.pop(0), "```\n- t\n```\n"),
    (lambda tree: tree[0].update(content="*"), "*\n```\n- t\n```\n"),
])
def test_code_after_closed_html_that_has_to_move_becomes_a_fence(change, text):
    """CommonMark reads ``\\t- t`` after a closed HTML block as code."""
    shaped, written = moved("<!-- c -->\n\t- t\n", change)
    assert written == text
    assert shaped[-1]["id"] == "b1"


@pytest.mark.parametrize("raw,change,text", [
    ("- a\n- b\n\n* c\n* d\n", lambda tree: tree.insert(1, tree.pop(3)), "- a\n- d\n- b\n\n* c\n"),
    ("- a\n- b\n\n* c\n* d\n", lambda tree: tree.insert(0, tree.pop(3)), "- d\n- a\n- b\n\n* c\n"),
    ("- a\n- b\n\n* c\n* d\n", lambda tree: tree.append(tree.pop(1)), "- a\n\n* c\n* d\n* b\n"),
    ("1) a\n2) b\n\n1. c\n2. d\n", lambda tree: tree.insert(1, tree.pop(3)), "1) a\n2) d\n2) b\n\n1. c\n"),
    ("1) a\n2) b\n", lambda tree: tree.insert(1, {"id": "new", "content": "1. zed", "children": []}),
     "1) a\n1) zed\n2) b\n"),
    ("- a\n- b\n\n* c\n", lambda tree: tree.pop(1), "- a\n\n* c\n"),
])
def test_an_item_moved_or_new_takes_the_mark_of_the_list_it_joins(raw, change, text):
    """A bullet of another mark, or a number of another delimiter, starts a
    list of its own. One whose neighbour went keeps its mark."""
    assert moved(raw, change)[1] == text


def test_an_anchor_on_an_empty_item_leaves_the_heading_text_after_it_out_of_the_list():
    shaped, text = moved("- \n\n  # h\n", lambda tree: tree[0].update(anchor="zz"))
    assert text == "- \n  ^zz\n\n # h\n"
    assert contents(shaped) == ["- ", "# h"]


def test_a_heading_left_of_an_items_text_ends_the_list():
    """It is no lazy line: CommonMark reads it at the top, and so does this
    parser."""
    assert contents(tree_of(parse("1. a\n   - b\n  # h\n"))) == [("1. a", ["- b"]), "# h"]
    assert contents(tree_of(parse(without("1. a\n   - b\n  # h\n", "- b")))) == ["1. a", "# h"]


def test_an_item_moved_into_a_tight_list_keeps_it_tight():
    shaped, text = moved("- a\n\n- b\n\npara\n\n- c\n- d\n", lambda tree: tree.append(tree.pop(1)))
    assert text == "- a\n\npara\n\n- c\n- d\n- b\n"


def test_match_stays_near_linear_when_both_ends_change():
    started = time.perf_counter()
    old = [{"content": "- [ ] todo" if i % 2 else "---"} for i in range(8000)]
    old[0] = {"content": "Heading text"}
    new = [dict(block) for block in old]
    new[0], new[-1] = {"content": "Heading text x"}, {"content": old[-1]["content"] + " x"}
    pairs = match(old, new)
    assert all(pairs.get(i) == i for i in range(1, 7999))
    old = [{"content": f"paragraph {i}"} for i in range(16000)]
    new = [{"content": block["content"] + (" z" if i % 2 else "")} for i, block in enumerate(old)]
    pairs = match(old, new)
    assert len(pairs) == 16000 and all(pairs[i] == i for i in range(0, 16000, 2))
    assert time.perf_counter() - started < 4


def test_a_tab_in_a_title_comes_back_from_the_zip_reader():
    from gamma.markdown_import import parse_frontmatter, yaml_text
    for value in ("a\tb", "a\tb\tc"):
        assert parse_frontmatter(f"---\ntitle: {yaml_text(value)}\n---\n")[0]["title"] == value
        assert parse_frontmatter(f"---\ntitle: {yaml_text(value)}\n---\n", controls=True)[0]["title"] == value


def test_a_file_past_the_limits_has_no_shape():
    deep = parse("".join(" " * (2 * i) + "- x\n" for i in range(md_lens.MAX_DEPTH + 2)))
    with pytest.raises(Unwritable, match="nested too deep"):
        shape(deep, [], [])


def chain(depth: int) -> list:
    tree = node = {"id": "n0", "content": "x", "children": []}
    for i in range(1, depth):
        node["children"].append({"id": f"n{i}", "content": "x", "children": []})
        node = node["children"][0]
    return [tree]


@pytest.mark.parametrize("depth", [md_lens.MAX_DEPTH + 50, 1200])
def test_a_tree_nested_too_deep_is_unwritable_for_that_reason(depth):
    started = time.perf_counter()
    with pytest.raises(Unwritable, match="nested too deep"):
        shape(parse(""), (), chain(depth))
    assert time.perf_counter() - started < 1
    snapshot = {"page": {"parent": "root", "position": "a", "content": ""}}
    for i in range(depth):
        snapshot[f"n{i}"] = {"parent": f"n{i - 1}" if i else "page", "position": "a", "content": "x"}
    assert len(flatten(project(snapshot, lambda block: "note"))) == depth


def test_an_edit_that_grows_a_file_past_the_limit_says_so(monkeypatch):
    monkeypatch.setattr(md_lens, "MAX_MARKDOWN_BYTES", 40)
    doc, ids, tree = read("a\n\nb\n")
    tree[1]["content"] = "b" * 50
    with pytest.raises(Unwritable, match="too large"):
        shape(doc, ids, tree)


def test_project_leaves_a_pdf_link_region_in_gamma():
    """A link region is a rectangle on the PDF with its target, as a rectangle
    highlight's geometry is: it has no text a file holds, and its label goes
    with it."""
    snapshot = {"page": {"parent": "root", "position": "a", "content": ""},
                "l": {"parent": "page", "position": "a", "content": "see here"},
                "k": {"parent": "l", "position": "a", "content": "a note under it"}}
    kinds = {"see here": "link"}
    assert project(snapshot, lambda block: kinds.get(block["content"], "note")) == [
        {"id": "k", "content": "a note under it", "children": []}]


def test_the_lens_holds_no_invisible_character():
    from pathlib import Path
    assert "﻿" not in Path(md_lens.__file__).read_text(encoding="utf-8")


# --- what other apps read ------------------------------------------------------------

def reads_otherwise(raw: str, written: str, *kept: int) -> bool:
    """``_reads_otherwise`` of ``written`` over ``raw``, as if a walk had copied
    the blocks of ``raw`` at the places ``kept`` line for line, to where their
    lines stand in ``written``, and written the others anew: the blocks of
    ``raw`` that are not kept, in order, as the others are, in order, edited,
    and any past those new."""
    doc = parse(raw)
    ids = [f"b{i}" for i in range(len(doc.flat))]
    walk = SimpleNamespace(copies=[], redressed=[], remarked=set(), out=md_lens._split(written), starts={})
    at = 0
    for i in kept:
        node = doc.flat[i]
        lines = doc.lines[node.span.first:node.span.last]
        at = next(k for k in range(at, len(walk.out)) if walk.out[k:k + len(lines)] == lines)
        walk.copies.append((at, node))
        walk.starts[at] = ids[i]
        at += len(lines)
    edited = iter([ids[i] for i in range(len(ids)) if i not in kept])
    for node in parse(written).flat:
        if node.span.first not in walk.starts:
            walk.starts[node.span.first] = next(edited, f"new{node.span.first}")
    return md_lens._reads_otherwise(doc, ids, walk)


@pytest.mark.parametrize("raw,written,kept", [
    ("- a\n\nb\n", "- a\nb\n", (0, 1)),                             # a lazy line
    ("a\n\n---\n", "a\n---\n", (0, 1)),                             # a setext heading
    ("p\n\n    code\n", "p\n    code\n", (0, 1)),                   # code by indentation, now the text before it
    ("x\n\n    code\n", "- new\n\n    code\n", (1,)),              # now a list item's text
    ("<div>\n\np\n", "<div>\np\n", (0, 1)),                         # HTML left open
    ("a\n\n  b\n", "- new\n\n  b\n", (1,)),                         # a list takes a line in
    ("a\n\nb\n", "> new\nb\n", (1,)),                               # a quote does
    ("| a |\n|---|\n\nb\n", "| a |\n|---|\nb\n", (0, 1)),           # a table does, as a row
    ("a\n\nb\n", "```\n\nb\n", (1,)),                               # a fence opened by accident
    ("a\n\nb\n", "a\nb\n", (0, 1)),                                 # two paragraphs made one
    ("<!--\na\n\nb\n-->\n", "<!--\na\nnew\nb\n-->\n", (0, 1)),      # a new line in a kept leaf
    ("a\n\nb\n", "- new\nb\n", (1,)),                               # a new item's lazy line
])
def test_a_block_left_alone_that_another_app_would_read_otherwise_is_caught(raw, written, kept):
    assert reads_otherwise(raw, written, *kept)


@pytest.mark.parametrize("raw,written,kept", [
    ("a\n\nb\n", "a\n\nnew\n\nb\n", (0, 1)),
    ("- a\n- b\n", "- a\n- b\n\n- c\n", (0, 1)),                    # a list made loose is spacing
    ("1. a\n2. b\n", "2. b\n", (1,)),                               # and what it counts from
    ("- a\nlazy\n", "lazy\n", (1,)),                                # an item deleted lets its lazy line go
    ("- a\nlazy\n", "- a2\nlazy\n", (1,)),                          # edited, it keeps it
    ("<!--\n- a\n- b\n", "## h\n- a\n- b\n", (1, 2)),               # what a comment hid is what it is alone
    ("1. a\n   - c\n", "1) a\n   - c\n", (1,)),                     # a parent's new marker holds its child
    ("-\ttabbed\n\n    a\n", "- - $$\n    zed\n    $$\n\n    a\n", (1,)),   # in markers it has more of
    ("text\n$$\nx\n$$\n", "text\n\npara\n\n$$\nx\n$$\n", (0, 1)),  # display math is a block of its own
    ("- is $$\n  x\n  $$\n  next\n- b\n", "- is $$\n  x\n  $$\n  next\n\nnew\n- b\n", (0, 1)),  # an open one's blank tail
])
def test_a_change_that_only_lets_a_block_go_is_no_refusal(raw, written, kept):
    assert not reads_otherwise(raw, written, *kept)


@pytest.mark.parametrize("raw,lazy", [
    ("- item\nlazy\n\npara\n", "lazy"),
    # docs/dev/block_centric.md, lines 379 to 381, and a paragraph after them
    ("inventory; `/api/search` serves the AI tools and is verified with curl (notes\n+ pdf hits). The chat sends "
     "`page_id`. Settings pane label \"Search PDF text\"\n→ \"Search library\" (`settings/AssistantTools.jsx`).)*\n\n"
     "A paragraph after it.\n", "→ \"Search library\" (`settings/AssistantTools.jsx`).)*"),
])
def test_a_block_moved_between_an_item_and_its_lazy_line_is_refused(raw, lazy):
    """The lazy line is the item's text to CommonMark, and a block moved between
    the two would take it from the item, as a block inserted there would. The
    walk finds the lazy line beside neither block it stood beside, but copies
    its lines as they were: they are compared all the same."""
    doc, ids, tree = read(raw)
    at = tree.index(node_with(tree, lazy))
    item = tree[at - 1]["content"]
    tree.insert(at, tree.pop())
    with pytest.raises(Unwritable, match=MEANING):
        shape(doc, ids, tree)
    with pytest.raises(Unwritable, match=MEANING):
        inserted(raw, item, "new")


def test_text_after_the_dollars_that_close_a_formula_typed_in_gamma_is_refused_for_that():
    """remark-math, which Gamma renders with, closes ``$$`` only on a line of
    ``$$`` alone: text after it would make every block below it math. The
    check reads it so, and says which edit it is."""
    with pytest.raises(Unwritable) as refused:
        edited("$$\nx\n$$\n\npara\n", "$$\nx\n$$", "$$\nx\n$$ edited")
    assert str(refused.value) == md_lens.MATH_TAIL
    assert reads_otherwise("$$\nx\n$$\n\npara\n", "$$\nx\n$$ edited\n\npara\n", 1)
    with pytest.raises(Unwritable) as refused:                  # the file's own, which takes what follows
        inserted("$$\nx\n$$ tail\n", "$$\nx\n$$ tail", "para")
    assert str(refused.value) == MEANING
    raw = "$$\nx\n$$ tail\n\npara\n\nend\n"
    assert edited(raw, "end", "end two") == raw.replace("end\n", "end two\n")


def test_a_block_the_walk_moves_over_by_its_indent_keeps_its_reading_or_is_refused():
    """``    code`` straight after a fence is code to CommonMark and text to this
    parser. With the fence gone a blank line stands above it, where four
    columns would be code to this parser too, so the walk moves it to three:
    prose to CommonMark."""
    with pytest.raises(Unwritable, match=MEANING):
        without("para\n\n```\nx\n```\n    code\nmore\n", "```\nx\n```")
    raw = "- item one\ncontinued here\n    - sub item\n"
    assert without(raw, "- item one") == "continued here\n   - sub item\n"       # a list item still


def test_a_block_moved_over_keeps_the_list_items_it_stood_in_or_is_refused():
    """``2. two`` stands in ``- item`` to CommonMark, past the lazy line this
    parser reads as a block of its own: moved to the left edge, it would be a
    list of its own."""
    with pytest.raises(Unwritable, match=MEANING):
        without("- item\nlazy\n  1. one\n  2. two\n", "1. one")


def test_an_item_given_the_mark_of_the_list_it_joins_keeps_its_lazy_line_or_is_refused():
    """Its lines are the file's but for the mark, so its lazy line is asked as
    the lazy line of a block left alone."""
    doc, ids, tree = read("+ t\n\nS\n\n- a\nlazy\n")
    tree.insert(2, tree.pop(1))
    with pytest.raises(Unwritable, match=MEANING):
        shape(doc, ids, tree)


def test_a_block_written_anew_on_a_second_pass_is_asked_as_one_left_alone():
    """A block this parser read into the one before it on the first pass is
    written anew on the next, and is still a block the change left alone."""
    with pytest.raises(Unwritable, match=MEANING):          # ``10. ten`` and its lazy line, apart
        without("- b\n> q\n10. ten\nten lazy\n", "- b")
    raw = "    x\n> q\n>\xa0\n> r\n"                        # a quote's line of no-break spaces is its text
    assert contents(tree_of(parse(raw))) == ["    x", "> q\n>\xa0\n> r"]
    assert edited(raw, "    x", "    x\nmore") == "    x\nmore\n\n> q\n>\xa0\n> r\n"
    doc, ids, tree = read("a\n\n> q\n>\xa0\n> r\n")
    tree[0]["children"].append(tree.pop(1))
    assert shape(doc, ids, tree)[1] == "- a\n  - > q\n    >\xa0\n    > r\n"


@pytest.mark.parametrize("closer", ["    $$", "\t$$", "      $$"])
def test_dollars_four_columns_in_close_no_formula(closer):
    """remark-math reads ``$$`` four columns in as more of the formula, and all
    that follows it; three columns in, it closes."""
    with pytest.raises(Unwritable, match=MEANING):
        edited("$$\nx\n$$\n\npara\n", "$$\nx\n$$", f"$$\nx\n{closer}")
    assert edited("$$\nx\n$$\n\npara\n", "$$\nx\n$$", "$$\nx\n   $$") == "$$\nx\n   $$\n\npara\n"


def test_a_straight_copy_of_any_file_reads_as_it_did():
    """Every file of the gate written back unchanged, every block copied:
    ``shape`` asks the check only of a text that differs, and this one does
    not, but the check alone says the same."""
    for name, raw in gate.corpus():
        doc = parse(raw)
        if doc.problem:
            continue
        ids = [f"b{i}" for i in range(len(doc.flat))]
        walk = md_lens._walked(doc, ids, tree_of(doc, ids))
        assert walk.finish()[0] == raw and len(walk.copies) == len(doc.flat), name
        assert not md_lens._reads_otherwise(doc, ids, walk), name


def test_a_walk_that_drops_the_blank_line_markdown_needs_writes_nothing(monkeypatch):
    """The walk's model of CommonMark is what keeps text after a list item from
    being its lazy line, which this parser reads as a block of its own; with
    that model gone, the check still refuses, and says why."""
    doc, ids, tree = read("- a\n\nb\n")
    tree.insert(1, {"id": "new", "content": "new", "children": []})
    assert shape(doc, ids, tree)[1] == "- a\n\nnew\n\nb\n"
    gap = md_lens._Walk._gap

    def sabotaged(self, piece, child=False):
        return [] if self.prev.kind in md_lens._ITEMS and piece.kind == "plain" else gap(self, piece, child)

    monkeypatch.setattr(md_lens._Walk, "_gap", sabotaged)
    assert write(doc, ids, tree)[0] == "- a\nnew\n\nb\n"
    with pytest.raises(Unwritable) as refused:
        shape(doc, ids, tree)
    assert str(refused.value) == MEANING


def test_an_empty_bullet_given_text_takes_the_blank_line_text_needs():
    """``-`` alone is an empty item to CommonMark and text to this parser: given
    text, it would be the lazy line of the item before it."""
    assert edited("- text\n-\n", "-", "para") == "- text\n\npara\n"
    assert edited("- text\n-\n  ^empty\n", "-", "para\n\nafter") == "- text\n\npara\n\nafter\n  ^empty\n"


def test_a_block_moved_with_its_parent_is_the_persons_to_move():
    """A child copied as it stood under an item that moved has moved too: a
    new list item around it is where the person put it."""
    raw = "- top\n\t- child\n\t\t- grandchild\n\n1. one\n2. next\n"
    doc, ids, tree = read(raw)
    top = tree[0]
    child = top["children"].pop(0)
    node_with(tree, "2. next")["children"].append(child)
    text = shape(doc, ids, tree)[1]
    assert "\t\t- grandchild\n" in text
    assert contents(tree_of(parse(text)))[-1] == ("2. next", [("- child", ["- grandchild"])])


def test_the_lens_loads_markdown_it_only_for_a_write_that_needs_it():
    """The iPad and Android servers never install it: importing the lens, a new
    file and a file written back unchanged leave it unloaded."""
    code = "\n".join([
        "import sys",
        "from gamma import md_lens",
        "assert 'markdown_it' not in sys.modules",
        "md_lens.render([{'id': 'a', 'content': 'new', 'children': []}])",
        "doc = md_lens.parse('a\\n\\nb\\n')",
        "md_lens.shape(doc, ['a', 'b'], md_lens.tree_of(doc, ['a', 'b']))",
        "assert 'markdown_it' not in sys.modules",
        "tree = md_lens.tree_of(doc, ['a', 'b'])",
        "tree[0]['content'] = 'a2'",
        "md_lens.shape(doc, ['a', 'b'], tree)",
        "assert 'markdown_it' in sys.modules"])
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ,
                          cwd=str(Path(__file__).resolve().parents[1]), timeout=120)
    assert done.returncode == 0, done.stderr


def test_the_check_reads_a_file_as_deep_as_the_lens_takes():
    raw = "".join(" " * (2 * i) + f"- x{i}\n" for i in range(md_lens.MAX_DEPTH)) + "\ntail\n"
    doc, ids, tree = read(raw)
    flatten(tree)[-1]["content"] = "tail edited"
    assert shape(doc, ids, tree)[1] == raw.replace("tail\n", "tail edited\n")
    holders, leaves, _ = md_lens._readings(doc.lines, 0)
    assert len(holders[md_lens.MAX_DEPTH - 1]) == md_lens.MAX_DEPTH and leaves[md_lens.MAX_DEPTH - 1]


# --- code a change takes elsewhere, and the characters of its lines -----------------

def outdented(tree):
    """The first block's first child, put after it."""
    tree.insert(1, tree[0]["children"].pop(0))


def lifted(tree):
    """The first block gone, its children in its place."""
    tree[0:1] = tree[0]["children"]


def under_last(tree):
    """The first block's first child, put under the last block."""
    tree[-1]["children"].append(tree[0]["children"].pop(0))


@pytest.mark.parametrize("raw,change,text,content", [
    ("- item\n\n      code\n", outdented, "- item\n\n```\ncode\n```\n", "```\ncode\n```"),
    ("1. item\n\n       code\n", outdented, "1. item\n\n```\ncode\n```\n", "```\ncode\n```"),
    ("- item\n\n      code\n      more\n", outdented, "- item\n\n```\ncode\nmore\n```\n", "```\ncode\nmore\n```"),
    ("- item\n\n      def f():\n          return 1\n", outdented, "- item\n\n```\ndef f():\n    return 1\n```\n",
     "```\ndef f():\n    return 1\n```"),
    ("- item\n\n      - x\n", outdented, "- item\n\n```\n- x\n```\n", "```\n- x\n```"),
    ("- a\n  - item\n\n        code\n", lambda tree: tree[0]["children"].insert(1, tree[0]["children"][0]["children"].pop()),
     "- a\n  - item\n\n  - ```\n    code\n    ```\n", "```\ncode\n```"),
    ("- item\n\n      code\n\npara\n", lifted, "```\ncode\n```\n\npara\n", "```\ncode\n```"),
    ("- item\n\n      code\n\npara\n", under_last, "- item\n- para\n\n  - ```\n    code\n    ```\n", "```\ncode\n```"),
    ("  1. x\n\n\t- tab item\n", outdented, "  1. x\n\n```\n- tab item\n```\n", "```\n- tab item\n```"),
])
def test_code_this_parser_reads_as_text_is_a_fence_wherever_a_change_takes_it(raw, change, text, content):
    """The verifier's leak: under an item CommonMark reads it as code by
    indentation, which this parser reads as text or a list item. Moved out of
    the item or lifted, it is the code it was, a fence of its lines, and Gamma
    is given that fence."""
    doc, ids, tree = read(raw)
    code = next(node for node in doc.flat if md_lens.text_column(doc.lines[node.span.first]) >= 4)
    shaped, written = moved(raw, change)
    assert written == text
    assert next(node for node in flatten(shaped) if node["id"] == ids[code.index])["content"] == content
    assert any(leaf and leaf[0][0] == "fence" for leaf in md_lens._readings(md_lens._split(written), 0)[1])


def sibling_first(tree):
    tree[0]["children"].insert(0, {"id": "new", "content": "zed", "children": []})


@pytest.mark.parametrize("raw,change,text,code", [
    ("- a\n\n      code ^abc\np\n", sibling_first, "- a\n  - zed\n\n  ```\n  code ^abc\n  ```\n\n  ^abc\n\np\n",
     "code ^abc"),
    ("- a\n\n      code ^abc\n", outdented, "- a\n\n```\ncode ^abc\n```\n\n^abc\n", "code ^abc"),
    ("- a\n\n      code\n\n      ^abc\n", outdented, "- a\n\n```\ncode\n```\n\n^abc\n", "code"),
    ("- a\n\n      code ^abc\np\n", lambda tree: (sibling_first(tree), tree[0]["children"][1].update(content="code 2")),
     "- a\n  - zed\n\n  ```\n  code 2\n  ```\n\n  ^abc\n\np\n", "code 2"),
])
def test_an_anchor_on_code_made_a_fence_stays_its_block(raw, change, text, code):
    """The anchor this parser reads at the end of a line of code is code to
    CommonMark: the fence holds the line as written, whichever way the walk
    came to fence it, and the anchor stands after it, where it is the fence's."""
    shaped, written = moved(raw, change)
    assert written == text
    fence = next(node for node in flatten(shaped) if node["content"].startswith("```"))
    assert (fence["id"], fence.get("anchor"), fence["content"]) == ("b1", "abc", f"```\n{code}\n```")


def test_text_under_an_item_that_commonmark_reads_as_text_stays_text_wherever_it_goes():
    """Four columns in, two past the item's text: a paragraph of the item."""
    assert moved("- item\n\n    kid\n", outdented)[1] == "- item\n\nkid\n"
    assert moved("- item\n\n    kid\n\npara\n", lifted)[1] == "kid\n\npara\n"


@pytest.mark.parametrize("space", ["\xa0", "　", "\x0c", " ", " ", "\x1f", "\x85", " "])
@pytest.mark.parametrize("block,code", [
    ("```\nx{c}\n```", "```\nx{c}\n```"), ("```\n{c}\n```", "```\n{c}\n```"), ("```\n  {c}x\n```", "```\n  {c}x\n```"),
    ("~~~\n{c}\n~~~", "~~~\n{c}\n~~~"), ("$$\nx{c}\n$$", "$$\nx{c}\n$$"), ("$$\n{c}\n$$", "$$\n{c}\n$$"),
    ("$$\n {c}x\n$$", "$$\n{c}x\n$$"),
    ("- ```\n  x{c}\n  ```", "```\nx{c}\n```"), ("- $$\n  x{c}\n  $$", "$$\nx{c}\n$$"),
])
def test_code_and_math_keep_every_character_of_their_lines_wherever_they_go(space, block, code):
    """The verifier's loss: the parser took spacing of other kinds off the ends
    of a line of code, so a straight copy kept it and a block written anew lost
    it. A line of code or math keeps all but its line ending."""
    block, code = block.format(c=space), code.format(c=space)
    assert contents(tree_of(parse(block + "\n"))) == [code]
    for raw, change in ((f"{block}\n", lambda tree: tree[0]["children"].append(
            {"id": "new", "content": "- new", "children": []})),
            (f"p\n\n{block}\n", lambda tree: tree[0]["children"].append(tree.pop(1))),
            (f"p\n\n{block}\n", lambda tree: tree.insert(0, tree.pop(1)))):
        shaped, text = moved(raw, change)
        assert text.count(space) == raw.count(space), (raw, text)
        assert code in [node["content"] for node in flatten(shaped)], (raw, text)


@pytest.mark.parametrize("space", ["\xa0", "　", "\x0c", " ", "\x1f", "\x85"])
@pytest.mark.parametrize("block,text", [("x{c}", "x{c}"), ("{c}x", "{c}x"), ("- a{c}", "- a{c}"), ("# h{c}", "# h{c}"),
                                        ("> q{c}", "> q{c}"), ("- a\n  b{c}", "- a\n  b{c}")])
def test_spacing_of_other_kinds_at_either_end_of_text_is_text(space, block, text):
    """Only spaces and tabs are whitespace to CommonMark, and to the parser
    with spans: what else a line's text starts or ends with is its text, and a
    block written anew keeps it."""
    block, text = block.format(c=space), text.format(c=space)
    assert contents(tree_of(parse(block + "\n"))) == [text]
    for raw, change in ((f"{block}\n", lambda tree: tree[0]["children"].append(
            {"id": "new", "content": "kid", "children": []})),
            (f"p\n\n{block}\n", lambda tree: tree[0]["children"].append(tree.pop(1)))):
        shaped, written = moved(raw, change)
        assert written.count(space) == raw.count(space), (raw, written)


def test_a_line_that_opens_with_other_spacing_is_a_paragraph():
    """As in CommonMark: no heading, quote, fence or formula starts past it."""
    for raw in ("\xa0# h\n", "　> q\n", "\xa0```\nx\n", "\xa0$$\nx\n", "\xa0| a |\n"):
        assert [node.span.kind for node in parse(raw).flat] == ["plain"], raw
        assert block_class(raw) == "plain", raw                                  # and so is that text in Gamma
    assert md_to_blocks("\xa0# h\n")[0]["content"] == "# h"                     # the importers' reading stands


@pytest.mark.parametrize("space", ["\xa0", "　", "\x0c", "\x85", "\x1f"])
@pytest.mark.parametrize("block,kind,content", [
    ("#{c}h", "plain", "#{c}h"), ("-{c}x", "plain", "-{c}x"), ("1.{c}x", "plain", "1.{c}x"),
    ("*{c}[ ] x", "plain", "*{c}[ ] x"), ("- [ ]{c}x", "item", "- [ ]{c}x"), ("- {c}x", "item", "- {c}x"),
    ("- -{c}x", "item", "- -{c}x"), ("- #{c}h", "item", "- #{c}h"),
])
def test_other_spacing_after_a_marker_is_text(space, block, kind, content):
    """As in CommonMark and Gamma's editor, only a space or a tab ends a
    heading's marks, a list marker or a task's box: ``#\\xa0h`` and ``-\\xa0x``
    are paragraphs and ``[ ]\\xa0x`` no box. A block written anew writes that
    spacing as it was, never as a space."""
    raw, content = block.format(c=space) + "\n", content.format(c=space)
    assert [(node.span.kind, node.content) for node in parse(raw).flat] == [(kind, content)]
    assert block_class(content) == kind
    assert not tree_equal([{"id": 0, "content": content, "children": []}],
                          [{"id": 0, "content": content.replace(space, " "), "children": []}])
    for change in (lambda tree: tree[0]["children"].append(tree.pop(1)), lambda tree: tree.insert(0, tree.pop(1)),
                   lambda tree: tree[1]["children"].append({"id": "new", "content": "kid", "children": []})):
        shaped, written = moved(f"p\n\n{raw}", change)
        assert written.count(space) == raw.count(space), written
        assert any(content in node["content"] for node in flatten(shaped)), written
    assert edited(raw, content, content + " more") == raw.replace("\n", " more\n")
    if kind == "plain":                         # no heading or list item that takes the next line
        assert contents(tree_of(parse(f"{raw}text\n"))) == [f"{raw}text"]


def test_other_spacing_after_a_marker_keeps_the_importers_reading():
    assert md_to_blocks("-\xa0x\n")[0]["content"] == "- x"
    assert md_to_blocks("- [ ]\xa0x\n")[0]["content"] == "- [ ] x"
    assert md_to_blocks("#\xa0h\ntext\n") == [{"content": "#\xa0h", "children": [{"content": "text", "children": []}]}]


@pytest.mark.parametrize("space", ["\xa0", "　"])
def test_dollars_after_other_spacing_open_no_formula_in_an_item_either(space):
    """The verifier's "would not settle": text that ends on such a line, given
    a child, is an item whose text holds the line, which opened a formula
    there and took the child in."""
    raw = f"x\n{space}$$\n"
    assert contents(tree_of(parse(raw))) == [f"x\n{space}$$"]
    written = child_added(raw, f"x\n{space}$$", "$$\ny\n$$")
    assert written == f"- x\n  {space}$$\n  - $$\n    y\n    $$\n"
    assert contents(tree_of(parse(written))) == [(f"- x\n  {space}$$", ["$$\ny\n$$"])]
    assert contents(tree_of(parse(f"- a {space}$$\n  b\n  $$\n"))) == [f"- a {space}$$\n  b\n  $$"]


@pytest.mark.parametrize("raw,content", [
    ("```py  \n  x  \t\n```  \n", "```py  \n  x  \t\n```  "), ("$$\t\nx \t\n$$ \n", "$$\t\nx \t\n$$ "),
    ("- ```\n  a\tb\n  ```\n", "```\na\tb\n```"), ("- a\n\n  ```\n  \tx \n  ```\n", "```\n\tx \n```"),
])
def test_a_line_of_code_keeps_its_trailing_whitespace_and_its_tabs(raw, content):
    assert content in [node.content for node in parse(raw).flat]


def test_spans_off_keeps_the_code_it_made():
    """The importers' reading, byte for byte: the digest test over the corpus,
    and these."""
    assert md_to_blocks("```\nx\xa0 \n```\n")[0]["content"] == "```\nx\n```"
    assert md_to_blocks("$$\n x\xa0\n$$\n")[0]["content"] == "$$\nx\n$$"
    assert md_to_blocks("- ```\n  a\tb \n  ```\n")[0]["content"] == "```\na    b\n```"
