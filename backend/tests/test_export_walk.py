"""The export walker (gamma/export_walk.py): each kind, each switch, a block
with nothing to show giving its place to its children, and one tree through
``walk``, ``render_readable`` and ``render_vault_page``, which must show the
same blocks in the same order and depth. The vault's known differences are
named where they are allowed: ``no_ink``, ``in_vault`` and ``broken_as_box``."""

import re
from copy import deepcopy

import pytest

from gamma.export_walk import walk
from gamma.markdown_export import render_readable
from gamma.obsidian_export import VaultContext, render_vault_page
from gamma.picture import is_picture, markdown_of

SWITCHES = [(True, True), (True, False), (False, True), (False, False)]


def block(block_id, content="", props=None, *children):
    return {"id": block_id, "content": content, "properties": props or {}, "children": list(children)}


def page_of_blocks(*children):
    return block("page", "Paper", {}, *children)


def position(page=3):
    return {"pageNumber": page, "boundingRect": {"x1": 0, "y1": 0, "x2": 1, "y2": 1}, "rects": []}


def picture(name, **props):
    return {"picture": {"url": f"/api/uploads/{name}.png"}, **props}


def recording(*names):
    return {"recording": {"dur": 1000, "segments": [
        {"url": f"/api/uploads/{name}.webm", "dur": 1000} for name in names]}}


def box(page=None):
    return {"text_box": {"x": 1, "y": 1}, **({"pdf_page": page} if page else {})}


def kinds(p):
    """A block of every shape, each one named in everything a renderer may
    write of it, so ``shape`` finds it under either switch. ``p``: a letter
    for the names, as the shapes appear twice in ``tree``."""
    def b(n):
        return f"{p}{n:02d}"

    return [
        block(b(1), f"{b(1)} region", {"link_url": f"https://example.org/{b(1)}", "pdf_position": position()},
              block(b(2), f"{b(2)} under a region")),
        block(b(3), "", {"link_page_id": "other", "quote": f"{b(3)} region quote", "pdf_position": position()},
              block(b(4), f"{b(4)} under a region with only a quote")),
        block(b(5), f"{b(5)} note on a quote", {"pdf_position": position(3), "quote": f"{b(5)} quote"},
              block(b(6), f"{b(6)} under a highlight")),
        block(b(7), "", {"pdf_position": position(4), "quote": f"{b(7)} quote\nits second line"},
              block(b(8), f"{b(8)} under a bare quote")),
        block(b(9), f"{b(9)} note on an area", {"pdf_position": {**position(4), "area": True}},
              block(b(10), f"{b(10)} under an area with a note")),
        block(b(11), "", {"pdf_position": {**position(4), "area": True}},
              block(b(12), f"{b(12)} under an empty area")),
        block(b(13), f"{b(13)} caption", {"ink_url": f"/api/uploads/{b(13)}.ink", "pdf_position": position(5)},
              block(b(14), f"{b(14)} under ink")),
        block(b(15), "", {"ink_url": f"/api/uploads/{b(15)}.ink"},
              block(b(16), f"{b(16)} under bare ink")),
        block(b(17), f"{b(17)} lecture", recording(b(17)),
              block(b(18), f"{b(18)} transcript")),
        block(b(19), "", recording(b(19), f"{b(19)}2")),
        block(b(20), f"{b(20)} caption", picture(b(20), pdf_page=6),
              block(b(21), f"{b(21)} under a picture")),
        block(b(22), f"{b(22)} broken picture", {"picture": {"url": "https://example.org/x.png"}}),
        block(b(23), f"{b(23)} box", box(7),
              block(b(24), f"{b(24)} under a box")),
        block(b(25), f"{b(25)} box on no page", box()),
        block(b(26), "   ", {},
              block(b(27), "", {},
                    block(b(28), f"{b(28)} under two empty blocks"))),
        block(b(29), "", {"sheet": {"w": 100, "h": 100}},
              block(b(30), f"{b(30)} box on a sheet", box(8)),
              block(b(31), "", {},
                    block(b(32), f"{b(32)} picture on a sheet", picture(b(32), pdf_page=9)))),
        block(b(33), f"{b(33)} first line\nsecond line"),
        block(b(34), f"- {b(34)} a list block"),
        block(b(35), f"# {b(35)} heading", {},
              block(b(36), f"{b(36)} under a heading", {},
                    block(b(37), f"{b(37)} under that"))),
        block(b(38), f"{b(38)} parent", {},
              block(b(39), f"{b(39)} child", {},
                    block(b(40), f"{b(40)} grandchild"))),
        block(b(41), f"{b(41)} broken picture on a page",
              {"picture": {"url": "https://example.org/x.png"}, "pdf_page": 10},
              block(b(42), f"{b(42)} under a broken picture")),
    ]


def tree():
    """Every shape at the top level, where the vault writes a document, and
    again under a plain block, where it writes a list."""
    return page_of_blocks(*kinds("a"), block("z01", "z01 root", {}, *kinds("b")))


def resolve_ref(block_id):
    return {"content": "Other", "page_title": "Other", "page_id": block_id} if block_id == "other" else None


def readable(page, highlights, notes):
    return render_readable(page, highlights, notes, resolve_ref, {"other": "Other.md"}.get)


def vault(page, highlights, notes):
    ctx = VaultContext(resolve_ref)
    ctx.place_pages({"page": "Paper.md", "other": "Other.md"})
    return render_vault_page(page, ctx, highlights=highlights, notes=notes)


def shape(text):
    """A rendered page as ``[(block, depth, PDF page)]``, read off its lines:
    a list item's depth is its indent, a line at the margin is at the top,
    and any other line belongs to the item above it."""
    blocks, seen, depth, pending = [], set(), 0, None
    for line in text.split("\n"):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if stripped.startswith("- "):
            depth = indent // 2
        elif not indent:
            depth = 0
        name = re.search(r"\b[abz]\d\d\b", line)
        page = re.search(r"\bp\. ?(\d+)", line)
        if name and name.group(0) not in seen:
            seen.add(name.group(0))
            blocks.append([name.group(0), depth, pending])
            pending = None
        if page and "[!quote]" in line:      # the vault's callout names its page first
            pending = int(page.group(1))
        elif page:
            blocks[-1][2] = int(page.group(1))
    return [tuple(b) for b in blocks]


def shown_page(item):
    """The page a renderer writes for an item: a link region names none and
    neither does a highlight without a quote, which reads as a note."""
    return None if item.kind == "region" or (item.kind == "highlight" and not item.quote) else item.page_no


def walked(items, depth=0):
    out = []
    for item in items:
        out.append((item.node["id"], depth, shown_page(item)))
        out += walked(item.children, depth + 1)
    return out


def each_block(page):
    stack = [page]
    while stack:
        node = stack.pop()
        yield node
        stack += node["children"]


def no_ink(page):
    """The vault's first known difference: it has no ink branch, and its
    highlights switch does not name ink, so handwriting is its caption, a
    note, and without one it is nothing. ``walk`` of the page with no ink on
    it is what the vault shows."""
    page = deepcopy(page)
    for node in each_block(page):
        node["properties"].pop("ink_url", None)
    return page


def in_vault(items):
    """The vault's second known difference, the depth its document style
    gives the top level, where the outline is headings and paragraphs: a
    heading's children stay at the top, a plain block with children starts
    a list, and any other block is a paragraph with its children in a list
    at the margin. Below that a list is a list."""
    out = []
    for item in items:
        out.append((item.node["id"], 0, shown_page(item)))
        paragraph = (item.kind in ("region", "recording", "picture") or (item.kind == "highlight" and item.quote)
                     or (item.kind == "note" and item.page_no))
        if paragraph or not item.text.startswith("#"):
            out += walked(item.children, 0 if paragraph else 1)
        else:
            out += in_vault(item.children)
    return out


def broken_as_box(page):
    """The vault's third known difference: a picture that does not normalize
    keeps its ``pdf_page`` there (``_box_link`` falls back to ``picture_page``),
    where ``walk`` and the readable export have a plain note. To the vault it
    is what a text box on that page is."""
    page = deepcopy(page)
    for node in each_block(page):
        props = node["properties"]
        if is_picture(props) and not markdown_of("", props["picture"]):
            props["text_box"] = props.pop("picture")
    return page


# --- each kind ---------------------------------------------------------------

def one(props=None, content="text", **switches):
    items = walk(page_of_blocks(block("x", content, props)), **switches)
    assert len(items) == 1
    return items[0]


@pytest.mark.parametrize("props, kind, page_no", [
    ({"link_url": "https://example.org", "pdf_position": position(2), "quote": "q"}, "region", 2),
    ({"link_page_id": "other"}, "region", None),
    ({"pdf_position": position(3), "quote": "q"}, "highlight", 3),
    ({"ink_url": "/api/uploads/a.ink", "pdf_position": position(4)}, "ink", 4),
    ({"ink_url": "", "pdf_position": position(4)}, "ink", 4),
    ({"ink_url": None}, "ink", None),
    (recording("r"), "recording", None),
    (picture("p", pdf_page=5), "picture", 5),
    (picture("p"), "picture", None),
    (box(6), "note", 6),
    (box(), "note", None),
    ({}, "note", None),
])
def test_each_kind(props, kind, page_no):
    item = one(props)
    assert (item.kind, item.text, item.props, item.page_no) == (kind, "text", props, page_no)
    assert item.node["id"] == "x" and item.on_sheet is False and item.children == []


def test_the_test_order_a_link_before_a_highlight_before_the_rest():
    assert one({"pdf_position": position(), "link_url": "https://example.org"}).kind == "region"
    assert one({"pdf_position": position(), **recording("r")}).kind == "highlight"
    assert one({"ink_url": "/api/uploads/a.ink", **recording("r")}).kind == "ink"
    assert one({**recording("r"), **picture("p")}).kind == "recording"


def test_text_and_quote_are_stripped_and_stored_as_written():
    item = one({"pdf_position": position(), "quote": "  q\n"}, "  ![a](/api/uploads/a.png){:width 300} [[id]]\n")
    assert item.quote == "q"
    assert item.text == "![a](/api/uploads/a.png){:width 300} [[id]]"


def test_a_picture_that_does_not_normalize_is_its_caption():
    broken = {"picture": {"url": "https://example.org/x.png"}}
    assert one(broken).kind == "note"
    assert walk(page_of_blocks(block("x", "", broken))) == []


# --- the two switches --------------------------------------------------------

@pytest.mark.parametrize("props", [
    {"link_url": "https://example.org", "pdf_position": position()},
    {"pdf_position": position(), "quote": "q"},
    {"ink_url": "/api/uploads/a.ink", "pdf_position": position()},
    recording("r"),
])
def test_highlights_off_leaves_your_text_as_a_note(props):
    item = one(props, highlights=False)
    assert (item.kind, item.text, item.props, item.quote, item.page_no) == ("note", "text", {}, "", None)
    assert walk(page_of_blocks(block("x", "", props)), highlights=False) == []


def test_highlights_off_leaves_pictures_and_text_boxes():
    assert one(picture("p", pdf_page=2), highlights=False).kind == "picture"
    assert one(box(2), highlights=False).page_no == 2


@pytest.mark.parametrize("props, kind", [
    ({"link_url": "https://example.org"}, "region"),
    ({"pdf_position": position(), "quote": "q"}, "highlight"),
    ({"ink_url": "/api/uploads/a.ink"}, "ink"),
    (recording("r"), "recording"),
])
def test_notes_off_keeps_the_kind_and_drops_the_text(props, kind):
    item = one(props, notes=False)
    assert (item.kind, item.text, item.props) == (kind, "", props)


@pytest.mark.parametrize("props", [{}, box(2), picture("p"), {"pdf_position": position()}])
def test_notes_off_takes_out_what_has_only_your_writing(props):
    assert walk(page_of_blocks(block("x", "text", props, block("y", "", {"quote": "q", "pdf_position": position()}))),
                notes=False)[0].node["id"] == "y"


@pytest.mark.parametrize("extra", [{"link_url": "https://example.org"}, recording("r")])
def test_highlights_off_keeps_a_picture_that_was_a_link_or_a_recording_too(extra):
    """Still a picture, so ``props`` holds it, on no page as the readable
    export has it. ``kinds`` leaves this shape out: the vault takes the picture
    away with the link and writes the caption."""
    props = picture("a01", pdf_page=3, **extra)
    page = page_of_blocks(block("a01", "a01 caption", props))
    [item] = walk(page, highlights=False)
    assert (item.kind, item.props, item.page_no) == ("picture", {"picture": props["picture"]}, None)
    assert walked([item]) == shape(readable(page, False, True)) == [("a01", 0, None)]
    assert vault(page, False, True).strip() == "a01 caption"


# --- promotion and sheets ----------------------------------------------------

def test_a_block_with_nothing_to_show_gives_its_place_to_its_children():
    page = page_of_blocks(block("a", "", {}, block("b", "  ", {}, block("c", "x"), block("d", "y"))), block("e", "z"))
    assert [item.node["id"] for item in walk(page)] == ["c", "d", "e"]


def test_children_follow_their_parent_in_order():
    page = page_of_blocks(block("a", "x", {}, block("b", "y", {}, block("c", "z")), block("d", "w")))
    [a] = walk(page)
    assert [item.node["id"] for item in a.children] == ["b", "d"]
    assert [item.node["id"] for item in a.children[0].children] == ["c"]


def test_a_sheet_among_the_ancestors_wins_through_promotion():
    sheet = {"sheet": {"w": 1, "h": 1}}
    page = page_of_blocks(
        block("s", "", sheet, block("e", "", {}, block("t", "x", box(2)), block("p", "y", picture("p", pdf_page=3)))),
        block("u", "z", box(4)))
    t, p, u = walk(page)
    assert (t.on_sheet, t.page_no, p.on_sheet, p.page_no) == (True, None, True, None)
    assert (u.on_sheet, u.page_no) == (False, 4)


def test_a_sheet_is_not_on_itself_and_counts_with_the_switches_off():
    sheet = {"sheet": {"w": 1, "h": 1}}
    [s] = walk(page_of_blocks(block("s", "title", sheet, block("t", "x", box(2)))), highlights=False)
    assert (s.on_sheet, s.children[0].on_sheet, s.children[0].page_no) == (False, True, None)


def test_the_tree_is_left_as_it_was():
    page = tree()
    before = deepcopy(page)
    for highlights, notes in SWITCHES:
        walk(page, highlights=highlights, notes=notes)
    assert page == before


# --- the three-way test ------------------------------------------------------

@pytest.mark.parametrize("highlights, notes", SWITCHES)
def test_walk_shows_what_the_readable_export_shows(highlights, notes):
    page = tree()
    assert walked(walk(page, highlights=highlights, notes=notes)) == shape(readable(page, highlights, notes))


@pytest.mark.parametrize("highlights, notes", SWITCHES)
def test_walk_shows_what_the_vault_shows_but_for_the_known_differences(highlights, notes):
    page = tree()
    shown = walk(broken_as_box(no_ink(page)), highlights=highlights, notes=notes)
    assert in_vault(shown) == shape(vault(page, highlights, notes))


def test_the_known_differences_are_real():
    """Each exception above is needed: the vault loses handwriting without a
    caption, the top level is not at the depths a list would give it, and a
    broken picture keeps its page, which makes it a paragraph at the top."""
    page = tree()
    shown = shape(vault(page, True, True))
    assert "a15" not in [name for name, _, _ in shown] and ("a13", 0, None) in shown
    assert in_vault(walk(no_ink(page))) != walked(walk(no_ink(page)))
    assert ("a15", 0, None) in walked(walk(page))
    assert ("a41", 0, 10) in shown and ("a42", 0, None) in shown
    assert ("a41", 0, None) in walked(walk(page)) and ("a42", 1, None) in walked(walk(page))


def test_with_highlights_off_ink_does_not_differ():
    page = tree()
    assert walked(walk(page, highlights=False)) == walked(walk(no_ink(page), highlights=False))


def test_a_region_stays_one_whatever_its_target():
    """The fourth difference, which is the walker's own: whether a target is
    in the export is not known here, so a region whose page is outside it
    stays a region, carrying what a renderer needs to do as the readable
    export does (a quote and its page) or as the vault does (the label)."""
    props = {"link_page_id": "elsewhere", "quote": "a01 quote", "pdf_position": position(2)}
    page = page_of_blocks(block("a01", "", props, block("a02", "a02 child")),
                          block("a03", "", {"link_page_id": "elsewhere"}, block("a04", "a04 child")))
    first, second = walk(page)
    assert (first.kind, first.quote, first.page_no) == ("region", "a01 quote", 2)
    assert "- > a01 quote\n  `p.2`" in readable(page, True, True)
    assert "a01 quote" in vault(page, True, True) and "[[" not in vault(page, True, True)
    # With nothing to say, both exports give its place to its children, which
    # a renderer that finds no target has to do as well.
    assert (second.kind, [item.node["id"] for item in second.children]) == ("region", ["a04"])
    assert ("a04", 0, None) in shape(readable(page, True, True))
