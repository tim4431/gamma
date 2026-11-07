"""What a page's blocks are to an export, decided once: ``walk`` turns a page's
tree (``markdown_export.build_tree``) into ``Item``s, one per block with
something to show, which a renderer writes in its own dialect.

The rules are the readable export's (``_render_readable_block``), the fuller
of the two text exports: a sheet of paper among a block's ancestors, the
export dialog's two switches, the order the kinds are tested in, and a block
with nothing to show giving its place to its children. Links, image sizes
and bundling stay out, since each dialect has its own; so a ``text`` is the
block's content as stored, and a link region is one whether or not its
target is in the export, with its ``quote`` and ``page_no`` kept for a
renderer that finds none.

``render_readable`` and ``render_vault_page`` do not use it;
tests/test_export_walk.py holds all three to the same blocks.
"""

from dataclasses import dataclass

from .highlights import is_highlight, is_ink, is_link, page_of
from .notebook import is_sheet
from .picture import is_picture, markdown_of, picture_page
from .recording import is_recording
from .text_box import box_page


@dataclass(slots=True)
class Item:
    kind: str                # region | highlight | ink | recording | picture | note
    node: dict
    text: str                # the stripped content; "" with the notes switch off
    props: dict              # {} when a switch took the block's kind away; a picture keeps ``picture``
    quote: str
    page_no: int | None
    on_sheet: bool           # a sheet of paper is among the block's ancestors
    children: list["Item"]


def _kind(props, text, quote, picture) -> str | None:
    """A block's kind, None for one with nothing to show. A highlight with
    neither a quote nor a note of yours is such a block, whatever else it
    carries."""
    if is_link(props):
        return "region"
    if is_highlight(props):
        return "highlight" if quote or text else None
    if is_ink(props):
        return "ink"
    if is_recording(props):
        return "recording"
    if picture:
        return "picture"
    return "note" if text else None


def _page_no(kind, props, on_sheet) -> int | None:
    """The PDF page an item is on: a picture's and a note's by the rule that
    a sheet among its ancestors wins (a note names one only as a text box)."""
    if kind == "picture":
        return picture_page(props, on_sheet)
    return box_page(props, on_sheet) if kind == "note" else page_of(props)


def _items(nodes, highlights, notes, on_sheet) -> list[Item]:
    items = []
    for node in nodes:
        props = node.get("properties") or {}
        under_sheet = on_sheet or is_sheet(props)
        # A picture is placed on the page as a text box is, so the notes
        # switch takes it out with the other notes and the highlights switch
        # leaves it. A picture that does not normalize draws nothing, and is
        # a note if it has a caption.
        picture = notes and is_picture(props) and markdown_of(node.get("content"), props["picture"])
        # A highlight carries both a PDF region and (often) writing of your
        # own, so dropping highlights keeps its text as a plain note. A picture
        # that links stays a picture, on no page, as in the readable export.
        if not highlights and (is_highlight(props) or is_link(props) or is_ink(props)
                               or is_recording(props)):
            props = {"picture": props["picture"]} if picture else {}
        text = (node.get("content") or "").strip() if notes else ""
        quote = (props.get("quote") or "").strip()
        children = _items(node["children"], highlights, notes, under_sheet)
        kind = _kind(props, text, quote, picture)
        if kind:
            items.append(Item(kind, node, text, props, quote, _page_no(kind, props, on_sheet), on_sheet, children))
        else:
            # Empty blocks don't consume a level.
            items += children
    return items


def walk(page, *, highlights=True, notes=True) -> list[Item]:
    """``page``'s blocks as items, in document order. ``highlights``/``notes``
    are the export dialog's two switches (``render_readable``)."""
    return _items(page["children"], highlights, notes, False)
