"""The lens between a Markdown file a person owns and a page's blocks: how a
page is written back to its file without a render ever touching their bytes
(docs/research/vaults.md, "Notes both ways"). Pure: no database, no disk.

``parse`` reads a file with the one parser (``markdown_import.md_to_blocks``
with ``spans``) into a ``Doc``: the file's lines, the blocks, and the tiles that
lay every line out, a block's own lines, a blank gap or an ``^anchor`` on its
own line. ``write`` walks a tree of blocks over those lines. A block that did
not change, where it stands, is a straight copy of its lines, and that path is
the first law: for any text, writing what was read gives the text back. A block
whose text changed keeps its dress (indent, marker, spacing, trailing
whitespace, line ending), line by line; anything else is written as a new block
at that place, dressed like its neighbours. Between two blocks ``write`` keeps
the blank lines they had, or adds what Markdown needs (``required``).

``shape`` is what a pull calls: the tree a written file reads back as, relative
to the file, with ids carried by the line each block starts on. It may split a
block and reparent one (a file can only hold what Markdown can say) but it never
removes an id: a block about to be swallowed by an open fence is ``Unwritable``,
nothing is written, and the next change tries again.

The laws, tested on every parse in the suite and over a corpus
(tests/test_md_lens_corpus.py):

1. ``write(doc, ids, tree_of(doc, ids))`` is ``raw``, for any text.
2. One block's text edited, through ``shape``: only that block's lines differ.
3. ``shape`` converges, and ``parse(text)`` gives ``shaped``.
4. ``shape(doc, ids, tree_of(doc, ids))`` is that tree and the same text.
5. Nodes an edit did not touch keep content, parent and bytes, through ``shape``.
6. A text edit then its revert gives the original bytes.
7. ``shape`` never removes an id.

Where Markdown leaves a law no way to hold, the file's meaning goes first, and
the gate counts each case by name. A heading moved, added or retyped parents
what follows it. Text a list now stands before cannot stay indented past it, so
its indent changes, and code by indentation that has to move becomes a fence;
so does such code this parser reads as text, under an item or a heading,
wherever a change takes it, as CommonMark read it in the file. A
``---`` the file comes to open with would open front matter, so it becomes
``***``, and a U+FEFF would be its byte-order mark, so the file takes one
before it. A bullet whose text is a block (``- # H``) stands behind another,
since a bullet that only wraps a block is how a child is written: one list
more to CommonMark. No paragraph can be the first child of an item with
nothing on its line, whose text it would be: it becomes a list item. An
empty box with no space after it (``- [ ]``) reads as
one with a space, so an edit and its revert end on the latter; and a blank
line an edit needed outside its block (``10.`` under a line it cannot break
into) stays on the revert, which cannot tell it from one a person wrote. Text
the file would read as an anchor (``press ^c``) has its caret escaped, which
``tree_equal`` does not see. Files of blocks at random indents can do worse:
a neighbour's indent can shift, or an id's parent, as such a file means
something else with a block gone. Nothing is lost there: ``shape`` says what
the file reads.

The meaning is CommonMark's, which this parser does not always share: where it
reads two blocks and Markdown one (``- a\\nlazy``, ``Title\\n---``, a comment
over a blank line), the walk keeps two blocks side by side only as the file had
them, and refuses what would put a block into HTML left open. What CommonMark
leaves open after a block is read line by line (``_end_of``): the separators,
the anchors and the columns ask it, and nothing else does. A model can miss a
case, so before ``shape`` hands over a changed text a CommonMark parser
(markdown-it-py, with tables and ``$$`` as remark-math reads it) reads it and
the file, and a block the change left alone that would read otherwise is
``Unwritable`` (``MEANING``): the person makes that change in the file
(``_reads_otherwise``).

Trees here are lists of ``{id, content, anchor?, children}`` in file space:
links are the file's, and a page's blocks get here through ``project`` and
``vault_links``.
"""

import difflib
import functools
import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass, field

from .markdown_import import (_ITEM_BOX_RE, _LIST_SPAN_RE, _STACKED_RE, _TABLE_RULE_RE, _TASK_SPAN_RE,
                              EMPTY_ITEM_RE, FOOTNOTE_RE, LIST_BLOCK_RE, MAX_MARKDOWN_BYTES, Span, TooManyBlocks,
                              _fence_marks, block_class, breaks_paragraph, columns_in, content_column, item_rest,
                              md_to_blocks, parse_frontmatter, shuts_code, text_column, wraps_block, yaml_list,
                              yaml_text)

# Nesting a walk follows. Real notes stop long before; a file that does not is
# a ``problem``, not a RecursionError.
MAX_DEPTH = 200
# The indent that makes code of a line past a blank line, or past the text of
# the item it stands in.
CODE = 4
# How far past a list item's text a block goes that has to stand in the item
# and not be read as that text.
PAST_TEXT = 2
# The least similarity at which leftovers of one class pair up as an edit.
MIN_SIMILAR = 0.3
# How far along the leftovers a block looks for its pair.
PAIR_WINDOW = 20
# The most letters two texts may differ by, past what they share at either
# end, for ``difflib`` to align them: on text of few letters that takes a
# second at 1,000, and minutes at 8,000.
ALIGN = 300
# The most pairs of items, one of each list, ``difflib`` aligns at once
# (``_opcodes``): past that it is quadratic in items alike.
ALIGNED = 10_000
# How far along its siblings a new block looks for one to dress like.
NEARBY = 8
# How many times ``shape`` writes a file before it gives up.
MAX_PASSES = 3

OPEN_BLOCK = "an open code block"
TOO_DEEP = "nested too deep to edit"
# A change that would make a block it left alone read otherwise in another app.
MEANING = "this change would alter how other apps read the text around it: edit the file to make it"
# The same, where a formula Gamma writes has text after its closing $$.
MATH_TAIL = ("a formula changed in Gamma has text after its closing $$, which other apps read as more of the formula, "
             "and all that follows it: put that text on a line of its own")

_ITEMS = ("item", "wrapped")
# The classes whose lines the parser takes all the indent off.
_TEXT = ("plain", "quote", "table", "heading", "math")
_LINE_END_RE = re.compile(r"\r\n|\r|\n")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
# An anchor needs a letter: ``^2`` is text, and so is the end of ``x ^2``.
_ANCHOR_LINE_RE = re.compile(r"\^((?=[0-9-]*[A-Za-z])[A-Za-z0-9-]+)")
_ANCHOR_END_RE = re.compile(r"(?<![ \t])[ \t]+\^((?=[0-9-]*[A-Za-z])[A-Za-z0-9-]+)\s*$")
# A caret escaped where a file would read an anchor: ``press \^c``, ``\^ref``.
_ESCAPED_ANCHOR_RE = re.compile(r"(^|[ \t])\\(\^(?=[0-9-]*[A-Za-z])[A-Za-z0-9-]+)$", re.M)
# The start of a link's definition, which no anchor can end: ``[z]: http://z``.
_DEFINITION_RE = re.compile(r" {0,3}\[(?:\\.|[^\\\]])+\]:")
# A thematic break, and a setext heading's underline: lines this parser takes
# for text, which Markdown does not.
_RULE_RE = re.compile(r" {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*")
_UNDERLINE_RE = re.compile(r" {0,3}(?:=+|-+)[ \t]*")
# A heading with no text (``#``), and a list marker with nothing after it,
# an empty item: lines this parser takes for text, which Markdown does not.
_EMPTY_HEADING_RE = re.compile(r" {0,3}#{1,6}[ \t]*")
_BARE_MARKER_RE = re.compile(r"[ \t]*(?:[-*+]|\d{1,9}[.)])")
# A list item's mark: its bullet, or the delimiter after its number.
_MARK_RE = re.compile(r"[ \t]*\d{0,9}([-*+.)])")
# The marks a quote's line opens with.
_QUOTED_RE = re.compile(r"[ \t]*(?:>[ \t]?)+")
# A line that may start an HTML block, which runs to a blank line or further.
_HTML_RE = re.compile(r" {0,3}<[/!?A-Za-z]")
# One that runs to its closer, past any blank line: CommonMark's kinds 1 to 5.
_HTML_OPEN_RE = re.compile(r" {0,3}(?:(?P<raw><(?:pre|script|style|textarea)(?=[\s>]|$))|(?P<comment><!--)|"
                           r"(?P<pi><\?)|(?P<cdata><!\[CDATA\[)|(?P<decl><![A-Za-z]))", re.I)
# The kinds that break into a paragraph: those, and a block-level tag (kind 6).
_HTML_BREAKS_RE = re.compile(
    _HTML_OPEN_RE.pattern + r"| {0,3}</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|col|"
    r"colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|"
    r"header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|"
    r"section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:[\s>]|/>|$)", re.I)
_HTML_CLOSERS = {"raw": re.compile(r"</(?:pre|script|style|textarea)>", re.I), "comment": re.compile("-->"),
                 "pi": re.compile(r"\?>"), "cdata": re.compile(r"\]\]>"), "decl": re.compile(">")}
_FRONT_OPEN_RE = re.compile(r"---[ \t]*")
_FRONT_CLOSE_RE = re.compile(r"(?:---|\.\.\.)[ \t]*")
# What front matter holds: a blank line, a comment, a list item, an indented
# line, a key. Anything else, and the ``---`` is a divider.
_FRONT_LINE_RE = re.compile(r"\s*$|\s*#|\s*-(?:\s|$)|\s|[^\s:#-][^:]*:(?:\s|$)")
_FRONT_KEY_RE = re.compile(r"([^\s:#-][^:]*):(?:\s|$)")
_FRONT_ITEM_RE = re.compile(r"[ \t]*-[ \t]+")
# A list block's marker and box, in content and as written. A number's box is
# its text, as the parser reads it, and so is one no space, tab or line end
# closes (``- [ ]\xa0x``).
_MARKER_RE = re.compile(r"(?P<mark>(?P<bullet>[-*+])|\d{1,4}[.)])[ \t]+(?(bullet)(?:\[(?P<box>[ xX])\](?:[ \t]+|$))?)")


class Unwritable(ValueError):
    """The tree cannot be written: nothing is, and the reason is the page's
    ``problem`` until the next change."""


@dataclass(slots=True)
class Front:
    """A file's front matter: its lines up to ``last``, the closing fence's
    included."""
    last: int
    fields: dict


@dataclass(slots=True, eq=False)
class Tile:
    """A run of the file's lines: a blank ``gap``, a block's own lines
    (``node``), or an ``anchor`` on its own line."""
    kind: str
    first: int
    last: int
    node: "Node | None" = None
    index: int = 0


@dataclass(slots=True, eq=False)
class Node:
    """A block as read: ``content`` is the text Gamma holds, no anchor."""
    index: int              # its place in document order: ``ids[index]`` is its id
    content: str
    span: Span
    parent: "Node | None"
    children: list = field(default_factory=list)
    anchor: str = ""        # its ^anchor, as written, without the caret
    inline: bool = False    # that anchor ends its last line
    text_end: int = 0       # where its content's lines end: an anchor on its own line may follow in its span
    tile: Tile | None = None
    after: Tile | None = None   # its anchor's own tile, after its subtree

    @property
    def flow(self) -> bool:
        """Whether it stands in the document, not under a list item."""
        return self.parent is None or self.parent.span.kind == "heading"


@dataclass(slots=True)
class Doc:
    """A file as read. ``lines`` hold their terminators, ``nodes`` are its
    blocks and ``flat`` the same in document order, ``tiles`` lay out the lines
    of its body. ``problem`` is why it cannot be edited ("" when it can)."""
    raw: str
    bom: str = ""
    eol: str = "\n"
    lines: list = field(default_factory=list)
    front: Front | None = None
    nodes: list = field(default_factory=list)
    flat: list = field(default_factory=list)
    tiles: list = field(default_factory=list)
    problem: str = ""
    bullet: str = "-"       # the file's bullet character
    unit: str = "  "        # and its indent
    reading: tuple | None = None    # what CommonMark reads its lines as (``_read_once``)

    @property
    def final(self) -> bool:
        """Whether the file ends in a newline (a new one will)."""
        return not self.lines or _ending(self.lines[-1]) != ""


def _split(text: str) -> list[str]:
    """``text``'s lines, each with its terminator. Never ``splitlines``, which
    also breaks on what the parser takes for text (``\\x0b``, ``\\u2028``...)."""
    lines, at = [], 0
    for found in _LINE_END_RE.finditer(text):
        lines.append(text[at:found.end()])
        at = found.end()
    if at < len(text):
        lines.append(text[at:])
    return lines


def _ending(line: str) -> str:
    """A line's terminator."""
    if line.endswith("\r\n"):
        return "\r\n"
    return line[-1:] if line[-1:] in ("\r", "\n") else ""


def _bare(line: str) -> str:
    """A line without its terminator."""
    return line[:len(line) - len(_ending(line))]


def _blank(line: str) -> bool:
    """Whether a line is blank, to CommonMark and to the parser: spaces and tabs
    only. A line of no-break spaces or a form feed is text."""
    return not line.strip(" \t\r\n")


def _parts(line: str) -> tuple[str, str, str]:
    """A line's leading whitespace, its text and its trailing whitespace, as a
    change keeps a line's dress and ``_normal`` compares lines: whitespace of
    any kind around text (the parser keeps spacing that is no space or tab,
    which stays in the file all the same), but only spaces and tabs around a
    line of other spacing, which is its text, and after a quote's marker, past
    which it is (``>\\xa0``)."""
    text = line.strip()
    rest = line.lstrip(None if text else " \t\r\n")
    text = rest.rstrip(" \t\r\n" if text[:1] in ("", ">") else None)
    return line[:len(line) - len(rest)], text, rest[len(text):]


def _usual_ending(lines: list[str], none: str = "\n") -> str:
    """The terminator most of ``lines`` have, the first of those on a tie, and
    ``none`` when they have none."""
    counts = {}
    for line in lines:
        if end := _ending(line):
            counts[end] = counts.get(end, 0) + 1
    return max(counts, key=counts.get) if counts else none


def _front_of(lines: list[str]) -> Front | None:
    """The front matter opening ``lines``, if every line up to its close is what
    front matter holds. The zip reader's ``parse_frontmatter`` asks less, which
    would hide everything up to a later ``---`` from the page."""
    if not lines or not _FRONT_OPEN_RE.fullmatch(_bare(lines[0])):
        return None
    for i in range(1, len(lines)):
        line = _bare(lines[i])
        if _FRONT_CLOSE_RE.fullmatch(line):
            return Front(i + 1, parse_frontmatter("\n".join(_bare(k) for k in lines[:i + 1]), controls=True)[0])
        if not _FRONT_LINE_RE.match(line):
            return None
    return None


def _too_deep(blocks: list) -> bool:
    level, depth = blocks, 0
    while level and depth <= MAX_DEPTH:
        level = [child for block in level for child in block["children"]]
        depth += 1
    return depth > MAX_DEPTH


def parse(raw: str) -> Doc:
    """``raw`` as a ``Doc``. Never raises on text: a file past the importer's
    limits has a ``problem`` and no blocks. Its front matter's lines stand
    blank in what the parser reads, so that a line number is one in the file."""
    bom = "\ufeff" if raw.startswith("\ufeff") else ""
    lines = _split(raw[len(bom):])
    return _blocks_of(Doc(raw, bom, _usual_ending(lines), lines, _front_of(lines)))


def _part(text: str) -> Doc:
    """``text`` read as blocks that stand in a file, not as a file: what it
    opens with is no byte-order mark and no front matter."""
    lines = _split(text)
    return _blocks_of(Doc(text, "", _usual_ending(lines), lines))


def _blocks_of(doc: Doc) -> Doc:
    """``doc`` with the blocks its lines read as."""
    start = doc.front.last if doc.front else 0
    try:
        if len(doc.raw.encode("utf-8", "surrogatepass")) > MAX_MARKDOWN_BYTES:
            raise TooManyBlocks()
        blocks = md_to_blocks("\n" * start + "".join(doc.lines[start:]), spans=True)
    except TooManyBlocks:
        doc.problem = "too large to edit"
        return doc
    if _too_deep(blocks):
        doc.problem = TOO_DEEP
        return doc
    doc.nodes = _read(blocks, None, doc, set())
    _lay_tiles(doc, start)
    _note_habits(doc)
    return doc


def _read(blocks: list, parent, doc: Doc, seen: set) -> list:
    """``blocks`` as nodes. A paragraph that is only an ``^anchor`` is no block:
    it belongs to the block before it, and a block has one anchor. The first
    place an anchor is written wins, ignoring case; a later copy stays text."""
    kept = []
    for block in blocks:
        span, content = block["span"], block["content"]
        alone = _ANCHOR_LINE_RE.fullmatch(content) if span.kind == "plain" else None
        if alone and kept and not kept[-1].anchor and not block["children"] and alone[1].lower() not in seen:
            seen.add(alone[1].lower())
            kept[-1].anchor = alone[1]
            kept[-1].after = Tile("anchor", span.first, span.last, kept[-1])
            continue
        node = Node(len(doc.flat), content, span, parent, text_end=span.last)
        doc.flat.append(node)
        _take_anchor(node, seen)
        node.children = _read(block["children"], node, doc, seen)
        kept.append(node)
    return kept


def _take_anchor(node: Node, seen: set):
    """Move an anchor out of ``node``'s content: an own line closing the block,
    or the end of its last line. Only text takes the second form. The lines of
    code never hold one."""
    span = node.span
    if span.open or span.kind in ("fence", "math", "verbatim"):
        return
    lines = node.content.split("\n")
    alone = _ANCHOR_LINE_RE.fullmatch(lines[-1].strip()) if len(lines) > 1 else None
    if alone:
        if alone[1].lower() not in seen:
            keep = len(lines) - 1
            while _blank(lines[keep - 1]):
                keep -= 1
            seen.add(alone[1].lower())
            node.anchor, node.content, node.text_end = alone[1], "\n".join(lines[:keep]), span.sources[keep]
        return
    if (block_class(node.content) if span.kind == "wrapped" else span.kind) not in ("plain", "item", "heading"):
        return
    found = _ANCHOR_END_RE.search(lines[-1])
    if found and found[1].lower() not in seen and _has_text(lines[-1][:found.start()]):
        seen.add(found[1].lower())
        lines[-1] = lines[-1][:found.start()]
        node.anchor, node.inline, node.content = found[1], True, "\n".join(lines)


def _has_text(line: str) -> bool:
    """Whether a line holds more than markers: an anchor needs text to end."""
    return bool(_ITEM_BOX_RE.sub("", line.strip() + " ", count=1).lstrip("#").strip())


def _lay_tiles(doc: Doc, start: int):
    """Tile the body: the blocks in line order and the blank lines between."""
    tiles = []
    for node in doc.flat:
        node.tile = Tile("node", node.span.first, node.span.last, node)
        tiles.append(node.tile)
        if node.after:
            tiles.append(node.after)
    tiles.sort(key=lambda tile: tile.first)
    at = start
    for tile in tiles:
        if tile.first > at:
            doc.tiles.append(Tile("gap", at, tile.first))
        doc.tiles.append(tile)
        at = tile.last
    if at < len(doc.lines):
        doc.tiles.append(Tile("gap", at, len(doc.lines)))
    for i, tile in enumerate(doc.tiles):
        tile.index = i


def _note_habits(doc: Doc):
    """The bullet and the indent a new block in this file takes."""
    for node in doc.flat:
        if node.span.marker[:1] in ("-", "*", "+"):
            doc.bullet = node.span.marker[0]
            break
    for node in doc.flat:
        above = node.parent.span if node.parent else None
        if above and above.kind in _ITEMS and node.span.kind in _ITEMS and node.span.indent.startswith(above.indent):
            doc.unit = node.span.indent[len(above.indent):] or doc.unit
            break


def tree_of(doc: Doc, ids=None) -> list:
    """The tree ``doc`` reads as. ``ids``: its nodes' ids in document order (by
    default their places, which is all a test of the laws needs)."""
    ids = range(len(doc.flat)) if ids is None else ids

    def node_of(node: Node) -> dict:
        out = {"id": ids[node.index], "content": node.content, "children": [node_of(n) for n in node.children]}
        if node.anchor:
            out["anchor"] = node.anchor
        return out

    return [node_of(node) for node in doc.nodes]


def flatten(tree: list) -> list:
    """A tree's nodes in document order."""
    out, pending = [], list(reversed(tree))
    while pending:
        node = pending.pop()
        out.append(node)
        pending.extend(reversed(node["children"]))
    return out


# --- comparing ---------------------------------------------------------------

def _is_code(content: str) -> bool:
    """Whether a block's text is code, whose lines keep their indent."""
    return block_class(content) == "fence" or text_column(content) >= CODE


def _key(line: str, code: bool) -> str:
    """A line of a block as two are compared: its text (``_parts``), or a line
    of code with its indent, tabs as four columns, and not the whitespace it
    ends in."""
    if not code:
        return _parts(line)[1]
    return line.replace("\t", "    ").rstrip(None if line.strip() else " \t")


def _normal(content: str) -> str:
    """``content`` as ``tree_equal`` compares it: no outer whitespace on a
    line (code keeps its indent), one blank line at most in a row, and a list
    block's marker as it reads one (``*  [X]  a`` is ``- [x] a``). A caret
    the file escapes where it would read an anchor (``\\^ref``) is the caret
    Gamma holds."""
    code = _is_code(content)
    lines = [_key(line, code) for line in content.split("\n")]
    if code:
        return "\n".join(lines).strip("\n")
    found = None if _RULE_RE.fullmatch(lines[0]) else _MARKER_RE.match(lines[0])
    if found:
        box = f" [{found['box'].lower()}]" if found["box"] else ""
        lines[0] = f"{'-' if found['bullet'] else found['mark']}{box} {lines[0][found.end():]}".rstrip()
    text = "\n".join(lines).strip("\n")
    return _ESCAPED_ANCHOR_RE.sub(r"\1\2", _BLANK_LINES_RE.sub("\n\n", text))


def tree_equal(a: list, b: list) -> bool:
    """Whether two trees say the same: their parents and their text, modulo
    what ``parse`` normalises, a line's dress (``_normal``) and the escape a
    caret takes. A difference this ignores is written to the file and never
    sent to Gamma, where it would land under a person's caret."""
    pending = [(a, b)]
    while pending:
        xs, ys = pending.pop()
        if len(xs) != len(ys) or any(_normal(x["content"]) != _normal(y["content"]) for x, y in zip(xs, ys)):
            return False
        pending += [(x["children"], y["children"]) for x, y in zip(xs, ys)]
    return True


def content_hash(content: str) -> str:
    """What the base's ``gone`` records of a block's text."""
    return hashlib.sha256(_normal(content).encode("utf-8", "surrogatepass")).hexdigest()


# --- separators --------------------------------------------------------------

def required(prev: str, next_kind: str, next_line: str = "") -> int:
    """The blank lines CommonMark needs between a block of class ``prev`` and
    one of class ``next_kind`` that starts with ``next_line``. By CommonMark,
    not by this parser, which takes ``- a\\nlazy`` for two blocks where Obsidian
    sees one bullet, so a parse back cannot catch this. None after a heading,
    none between items, none before a list that can break into a paragraph;
    otherwise one, which covers text after a list or a quote (lazy continuation)
    and ``---`` or ``===`` after a paragraph (a setext heading). Code by
    indentation asks for one even after a heading, or the parser reads text."""
    if next_kind == "verbatim":
        return 1
    if prev == "heading" or (prev in _ITEMS and next_kind in _ITEMS):
        return 0
    return 0 if prev == "plain" and next_kind in _ITEMS and breaks_paragraph(next_line) else 1


# What a block leaves open for a line straight after it, by CommonMark: a
# paragraph ("open"), a list item's, a quote's or a footnote's paragraph, which
# takes a lazy line ("lazy"), a table, which takes a row ("table"), an HTML
# block, which runs to a blank line ("html") or to its closer ("html " and the
# closer's kind), code by indentation, which a line four columns in goes on
# past any blank line ("code"), an item whose text ends in HTML or in code,
# which holds what is in the item ("held", "held code"), or nothing
# ("closed"). This parser reads a paragraph's lines as one block, where
# CommonMark may read several (``Title\n---``, a ``<div>`` or a quote breaking
# in), so a paragraph is read line by line, and so is an item's text.

def _end_of(kind: str, lines: list[str]) -> str:
    """What a block of class ``kind`` written as ``lines`` leaves open."""
    first, last = _bare(lines[0]), _bare(lines[-1])
    if kind in ("plain", "table"):
        return _after(None, kind, lines)
    if kind in ("heading", "fence", "math", "verbatim"):
        return _after("code" if kind == "verbatim" else "closed", "plain", lines[_held(kind, lines):])
    if kind in _ITEMS and (item := _LIST_SPAN_RE.match(first)):
        end = _after(None, "plain", [item[3], *_repad(list(map(_bare, lines[1:])), content_column(first), 0)])
        if end and end.startswith("html"):
            return "held"
        return {"open": "lazy", "table": "lazy", "code": "held code", None: "closed"}.get(end, end)
    if _RULE_RE.fullmatch(last):
        return "closed"
    rest = _parts(_QUOTED_RE.sub("", last))[1]                  # a quote's text, which an empty one has none of
    text = rest and not _RULE_RE.fullmatch(rest) and block_class(rest) not in ("heading", "fence", "math")
    return "lazy" if text else "closed"


def _held(kind: str, lines: list[str]) -> int:
    """How many of ``lines`` a heading or a block of code holds: what a person
    typed after its line or its close is text after it."""
    if kind == "heading":
        return 1
    first = _bare(lines[0])
    for i in range(1, len(lines)):
        line = _bare(lines[i])
        if kind == "verbatim" and not _blank(line) and text_column(line) < CODE:
            return i
        if kind == "fence" and shuts_code(line, _fence_marks(first), text_column(first)) or (
                kind == "math" and "$$" in line):
            return i + 1
    return len(lines)


def _line_end(end: str | None, line: str) -> str | None:
    """What a line of a paragraph leaves open, straight after one that left
    ``end``: a line that breaks in leaves what a block of its class would, and
    one that follows a table's head is its rule. A blank line leaves nothing.
    Four columns in, where nothing holds text, a line is code, and ``#`` alone
    is a heading with no text, which this parser reads as text."""
    if _blank(line):
        return None
    cls = "heading" if _EMPTY_HEADING_RE.fullmatch(line) else block_class(line)
    if end is not None and _joins(end, cls, line):
        if end.startswith("html "):
            return "closed" if _HTML_CLOSERS[end[5:]].search(line) else end
        if end == "open" and _UNDERLINE_RE.fullmatch(line):
            return "closed"
        return "table" if end == "open" and "|" in line and _TABLE_RULE_RE.fullmatch(line.strip()) else end
    if end in (None, "closed", "code") and text_column(line) >= CODE:
        return "code"
    found = _HTML_OPEN_RE.match(line)
    if found:
        shut = _HTML_CLOSERS[found.lastgroup].search(line, found.start() + 2)
        return "closed" if shut else f"html {found.lastgroup}"
    if _HTML_RE.match(line):
        return "html"
    if _RULE_RE.fullmatch(line):
        return "closed"
    if FOOTNOTE_RE.match(line):
        return "lazy"
    return "open" if cls in ("plain", "table") else _end_of(cls, [line])


def _within(end: str | None, child: bool) -> str | None:
    """What a block finds open after one that left ``end``: in it, as its first
    child, an item's text is a paragraph, HTML or code; past it, that HTML or
    code is shut."""
    if child:
        return {"lazy": "open", "held": "html", "held code": "code"}.get(end, end)
    return "closed" if end in ("held", "held code") else end


def _joins(end: str | None, kind: str, line: str) -> bool:
    """Whether ``line``, the first of a block of class ``kind``, would be read
    as part of the block before it, which left ``end``: a paragraph's next line
    or its setext underline, a lazy line, an HTML block's next line. This parser
    reads two blocks either way, so only ``required`` and this keep them two.
    What can break into a paragraph does not join it: a heading, a fence, a
    quote, a footnote, a rule, a list that ``breaks_paragraph``, HTML but of
    the last kind; but nothing four columns in, which is the paragraph's text.
    A table takes only text, as a row."""
    if end and end.startswith("html") or end == "open" and text_column(line) >= CODE:
        return True
    if end not in ("open", "lazy", "table") or kind in ("heading", "fence", "math", "quote") or FOOTNOTE_RE.match(line):
        return False
    if _RULE_RE.fullmatch(line):
        return end == "open" and bool(_UNDERLINE_RE.fullmatch(line))
    if kind in _ITEMS:
        return end == "open" and not breaks_paragraph(line)
    return not _HTML_BREAKS_RE.match(line)


def _after(end: str | None, kind: str, lines: list[str]) -> str:
    """What is left open once a block is written straight after one that left
    ``end`` (None: a blank line or nothing stands before it). Text four
    columns in there is code to CommonMark, whatever this parser reads."""
    if end is None and kind in ("plain", "table") and text_column(lines[0]) >= CODE:
        return _end_of("verbatim", lines)
    if kind in ("plain", "table"):
        for line in map(_bare, lines):
            end = _line_end(end, line)
        return end
    first = _bare(lines[0])
    if end is not None and _joins(end, kind, first):
        return "closed" if end == "open" and _UNDERLINE_RE.fullmatch(first) else end
    return _end_of(kind, lines)


# --- the walk ----------------------------------------------------------------

def _trim(content: str) -> str:
    """``content`` as lines can hold it: no blank lines around it (a line of
    no-break spaces is text), and ``\\n`` between its lines."""
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while len(lines) > 1 and _blank(lines[-1]):
        lines.pop()
    start = 0
    while start < len(lines) - 1 and _blank(lines[start]):
        start += 1
    return "\n".join(lines[start:])


def _fenced(content: str) -> str:
    """Code by indentation as a fence. That form needs a blank line above it and
    no list open: under a list its lines are a list's, and a line of ``` in them
    would open a block to the end of the file."""
    lines = content.split("\n")
    cut = min(text_column(line) for line in lines if not _blank(line))
    longest = max((len(run) for run in re.findall(r"`+", content)), default=0)
    marks = "`" * max(3, longest + 1)
    return "\n".join([marks, *(_repad([line], cut, 0)[0] for line in lines), marks])


def _neighbour(doc: Doc, tile: Tile, step: int) -> Tile | None:
    """The block or anchor tile next to ``tile``, past a gap."""
    i = tile.index + step
    if 0 <= i < len(doc.tiles) and doc.tiles[i].kind == "gap":
        i += step
    return doc.tiles[i] if 0 <= i < len(doc.tiles) else None


def _gap_lines(doc: Doc, tile: Tile, step: int) -> list[str]:
    """The blank lines next to ``tile``."""
    i = tile.index + step
    if 0 <= i < len(doc.tiles) and doc.tiles[i].kind == "gap":
        return doc.lines[doc.tiles[i].first:doc.tiles[i].last]
    return []


def _repad(lines: list[str], col: int, want: int) -> list[str]:
    """``lines`` moved from column ``col`` to ``want``, tabs counting 4 as the
    parser counts them. Nothing but their leading whitespace changes."""
    out = []
    for line in lines:
        i, width = columns_in(line, col)
        past = max(0, width - col)                  # what a tab left over
        out.append(line if _blank(line) else " " * (want + past) + line[i:])
    return out


def _split_dress(raw: str) -> tuple[str, str, str]:
    """A raw line's leading whitespace, trailing whitespace and terminator."""
    bare = _bare(raw)
    if _blank(bare):
        return "", "", _ending(raw)
    lead, _, trail = _parts(bare)
    return lead, trail, _ending(raw)


def _laid_out(line: str, span: Span) -> bool:
    """Whether a line of a block's text stands where it is written, behind the
    block's indent: a block that keeps its marker holds its lines laid out, but
    for text at their left edge, which goes under the marker."""
    return span.kind == "item" and bool(line[0] in " \t" or LIST_BLOCK_RE.match(line))


def _changed_line(raw: str, old: str, new: str, span: Span) -> str:
    """The line ``new`` in the dress of ``raw``, which held ``old``: its
    trailing whitespace and terminator always, and new whitespace of its own
    only when the line had none. Its indent stays when that is still what
    ``new`` has. Otherwise ``new``'s own, behind what the parser took off, which
    is only the block's indent in a block that keeps the rest; a line of text,
    which the parser takes all of it off, keeps the indent that holds it in its
    place unless ``new``'s is wider; and text a list item's lines hold at their
    left edge goes under its marker."""
    lead, trail, end = _split_dress(raw)
    if _blank(new):
        return end
    old_lead, (new_lead, text, new_trail) = _parts(old)[0], _parts(new)
    if text_column(old_lead) != text_column(new_lead):
        if span.kind in _TEXT:
            lead = new_lead if text_column(new_lead) > text_column(lead) else lead
        elif span.kind == "item" and not _laid_out(new, span):
            lead = span.rest_prefix
        else:
            lead = " " * (text_column(lead) - text_column(old_lead)) + new_lead
    return f"{lead}{text}{trail or new_trail}{end}"


def _added_line(line: str, span: Span, eol: str, after: str) -> str:
    """A new line in a block, ``after`` the line written before it:
    ``rest_prefix`` keeps it in the block, unless it is laid out already; in
    a block of text the indent of the line before does, which holds that line
    where it stands (a footnote's second paragraph)."""
    if _blank(line):
        return eol
    if span.kind in _TEXT and not _blank(after):
        return f"{_split_dress(after)[0]}{line}{eol}"
    return f"{span.indent if _laid_out(line, span) else span.rest_prefix}{line}{eol}"


def _marker_in_dress(written: str, new) -> str:
    """The marker ``new`` (a ``_MARKER_RE`` match on a block's new text) in the
    dress of the marker ``written``: a kind of marker unchanged keeps its
    character and spacing, and takes its number and its box from ``new``."""
    was = _MARKER_RE.match(written)
    if (was["box"] is None) != (new["box"] is None) or was["mark"][0].isdigit() != new["mark"][0].isdigit():
        return new.group()
    if was["box"] is not None and was["box"].lower() != new["box"].lower():
        written = written[:was.start("box")] + new["box"] + written[was.end("box"):]
    return new["mark"] + written[was.end("mark"):] if was["mark"][0].isdigit() else written


def _first_line(span: Span, raw: str, old: str, new: str) -> str:
    """A list block's first line, ``old`` edited to ``new``, its marker in the
    dress the line had, but where that would make a rule of the line."""
    if old == new:
        return raw
    bare = _bare(raw)
    had_text = bare[len(span.indent) + len(span.marker):].strip()
    trail = (bare[len(bare.rstrip()):] if had_text else "") or new[len(new.rstrip()):]
    if span.kind == "wrapped":
        return f"{span.indent}{_unruled(span.marker, new.strip())}{new.strip()}{trail}{_ending(raw)}"
    new = new.lstrip()
    found = _MARKER_RE.match(new)
    marker, text = _marker_in_dress(span.marker, found), new[found.end():].rstrip()
    if text and not marker[-1].isspace():
        marker += " "
    if _RULE_RE.fullmatch(marker + text):       # the file's mark would make a rule of it (``* **``)
        marker = found.group()
    return f"{span.indent}{marker}{text}{trail if text else ''}{_ending(raw)}"


def _shared_ends(a, b) -> tuple[int, int]:
    """How long a run two sequences start with, and then end with, in common."""
    head = len(os.path.commonprefix([a, b]))
    return head, len(os.path.commonprefix([a[head:][::-1], b[head:][::-1]]))


def _opcodes(a: list, b: list) -> list:
    """Opcodes from ``a`` to ``b`` as ``difflib``'s, near linear however the two
    differ: what they share at either end first; then, on a stretch past
    ``ALIGNED`` pairs, the items they share, the first of each in one with the
    first in the other and on, that keep their order (``_rising``), and between
    those the same again; ``difflib`` itself only on what is left, which is
    small. One block edited at either end of thousands alike, or every other
    one of thousands, is then no alignment of them all, which ``difflib``
    makes quadratic. Only ``equal`` is told apart: the rest is ``replace``."""
    same, pending = [], [(0, len(a), 0, len(b))]
    while pending:
        a0, a1, b0, b1 = pending.pop()
        head, tail = _shared_ends(a[a0:a1], b[b0:b1])
        same += [(a0 + k, b0 + k) for k in range(head)] + [(a1 - 1 - k, b1 - 1 - k) for k in range(tail)]
        a0, a1, b0, b1 = a0 + head, a1 - tail, b0 + head, b1 - tail
        if a0 == a1 or b0 == b1:
            continue
        if (a1 - a0) * (b1 - b0) <= ALIGNED:
            matcher = difflib.SequenceMatcher(None, a[a0:a1], b[b0:b1], autojunk=False)
            same += [(a0 + i + k, b0 + j + k) for i, j, n in matcher.get_matching_blocks() for k in range(n)]
            continue
        where, taken = {}, Counter()
        for j in range(b0, b1):
            where.setdefault(b[j], []).append(j)
        pairs = []
        for i in range(a0, a1):
            js = where.get(a[i], ())
            if taken[a[i]] < len(js):
                pairs.append((i, js[taken[a[i]]]))
                taken[a[i]] += 1
        kept = _rising(pairs)
        same += kept
        edges = [(a0 - 1, b0 - 1), *kept, (a1, b1)]
        pending += [(i + 1, i2, j + 1, j2) for (i, j), (i2, j2) in zip(edges, edges[1:]) if kept]
    out, i, j = [], 0, 0
    for x, y in sorted(same):
        if (x, y) != (i, j):
            out.append(("replace", i, x, j, y))
        if out and out[-1][0] == "equal" and (out[-1][2], out[-1][4]) == (x, y):
            out[-1] = ("equal", out[-1][1], x + 1, out[-1][3], y + 1)
        else:
            out.append(("equal", x, x + 1, y, y + 1))
        i, j = x + 1, y + 1
    return out + ([("replace", i, len(a), j, len(b))] if (i, j) != (len(a), len(b)) else [])


def _aligned_lines(span: Span, old: list[str], new: list[str], raws: list[list[str]], code: bool, eol: str) -> list:
    """``new`` written over ``old``, whose raw lines are ``raws``: a line
    unchanged keeps its raw lines, and a changed one, aligned by ``difflib``,
    its dress. Lines the same but for their outer whitespace are unchanged in
    what they say, and that whitespace still reaches the file."""
    out = []
    for tag, i1, i2, j1, j2 in _opcodes([_key(k, code) for k in old], [_key(k, code) for k in new]):
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                same = old[i] == new[j] or _blank(new[j])
                out += raws[i] if same else [_changed_line(raws[i][0], old[i], new[j], span)]
            continue
        paired = min(i2 - i1, j2 - j1)
        for k in range(paired):
            raw, line = raws[i1 + k][0], new[j1 + k]
            out.append(_changed_line(raw, old[i1 + k], line, span) if not _blank(raw)
                       else _added_line(line, span, eol, out[-1] if out else ""))
        for line in new[j1 + paired:j2]:
            out.append(_added_line(line, span, eol, out[-1] if out else ""))
    return out


def _end_anchor(raw: str) -> tuple[str, str]:
    """``raw`` without the anchor ending it, and that anchor as written."""
    bare = _bare(raw)
    found = _ANCHOR_END_RE.search(bare)
    return bare[:found.start()] + bare[len(bare.rstrip()):] + _ending(raw), bare[found.start():len(bare.rstrip())]


def _with_anchor(raw: str, written: str) -> str:
    """``raw`` ending in the anchor ``written``, before its trailing whitespace."""
    bare = _bare(raw)
    core = bare.rstrip()
    return f"{core}{written}{bare[len(core):]}{_ending(raw)}"


@dataclass(slots=True)
class _Place:
    """Where a walk's nodes go. The document's flow is the default; under a list
    item, ``parent`` is that item as read, ``indent`` its marker's whitespace
    and ``column`` its text column as written."""
    flow: bool = True
    parent: Node | None = None
    indent: str = ""
    column: int = 0
    wraps: bool = False          # that item's marker only wraps a block
    sibling: "_Piece | None" = None   # the list item written here just before
    above: "_Piece | None" = None   # that item as written

    @property
    def base(self) -> int:
        """The column a block here is read from: the three columns of indent
        Markdown allows a heading, a rule or HTML count from the text of the
        item it stands in, as do the four of code (``_code_column``)."""
        return 0 if self.flow else _code_column(_bare(self.above.lines[0]))


@dataclass(slots=True)
class _Piece:
    """Lines the walk has settled on. ``tile`` is the tile they come from, when
    they stand where it stood, and ``node`` the block they write, as read."""
    lines: list[str]
    kind: str
    tile: Tile | None = None
    indent: str = ""             # the whitespace before its first line
    column: int = 0              # a list item's text column
    holds: bool = False          # its children go under it, as a list's do
    tail: str = ""               # an anchor on its own line, after the subtree
    edited: bool = False
    node: Node | None = None
    base: int = 0                # its place's ``base``
    moved: bool = False          # it stands beside none of the blocks it stood beside (``_moved``)
    redressed: bool = False      # its lines are the file's but for their indent, or they are a fence (``_fitted``)
    marked: bool = False         # its mark is the walk's: the list's it joins (``_marked``), or a block's written anew


class _Walk:
    """One ``write``: the tree depth first, over the lines of ``doc``. For each
    node, ``_kept`` tries its own raw lines, and failing that ``_encoded`` writes
    it as new at that place."""

    def __init__(self, doc: Doc, ids, tree: list, fresh=()):
        self.doc = doc
        self.ids = ids
        self.known = dict(zip(ids, doc.flat))
        self.fresh = fresh        # blocks ``shape`` found merged in their old dress
        self.in_tree = {node["id"] for node in flatten(tree)}
        self.beside = {}          # node index -> the ids of the blocks before and after it, at its level
        for level in (doc.nodes, *(node.children for node in doc.flat)):
            names = [None, *(ids[node.index] for node in level), None]
            for k, node in enumerate(level):
                self.beside[node.index] = (names[k], names[k + 2])
        self.out = list(doc.lines[:doc.front.last]) if doc.front else []
        self.starts = {}          # line of the output -> the id of the block starting there
        self.prev = None          # the last _Piece
        self.list_root = None     # the column of the list open at the left edge, if one is
        self.list_text = 0        # and of its item's text, which a line past a blank line joins
        self.list_new = False     # that item is written anew, or edited: its columns may be others
        self.deepest = 0          # the text column of the last list item
        self.end = None           # what the last piece leaves open (``_end_of``)
        self.as_read = False      # or that piece left what it left in the file (``_settle``)
        self.read_in = {}         # tile index -> what was open before it in the file
        self.seen = set()         # the anchors written so far, as ``_read`` sees them
        # (line of the output, node) of each block whose own lines are copied as they were, whatever now
        # stands beside it (``_moved`` cannot tell which of two neighbours a person moved), under no block
        # that moved or is new, or so but for the mark of the list it joins; (line, how many, node) of each
        # block copied so but for its dress (``_fitted``), or written anew, as it was, on a pass of ``shape``
        # that found it merged; and the first lines in the file of those whose mark is the walk's
        self.copies = []
        self.redressed = []
        self.remarked = set()

    def nodes(self, tree: list, place: _Place, parent=None, moving: bool = False):
        """``tree``, the children of the block ``parent`` (by id), written at
        ``place``. ``moving``: a block above it moved, or is new, so that none
        of it stands where it stood."""
        for at, node in enumerate(tree):
            old = self.known.get(node["id"])
            moved = bool(old) and self._moved(old, tree, at)
            went = moving or old is None or moved or parent != (self.ids[old.parent.index] if old.parent else None)
            fresh = node["id"] in self.fresh
            piece = self._kept(old, node, place, fresh, at) if old else None
            if piece:
                piece = self._fitted(piece, old, node, place, at)
                piece.moved = moved
                copy = not piece.edited
                self._marked(piece, place, tree, at)
            else:
                piece = self._encoded(node, old, place, tree, at)
                piece.redressed = piece.marked = fresh and self._left_alone(old, node, place)
                copy = False
            piece.node, piece.base = old, place.base
            self._emit(piece, place, node["id"])
            if old and not moving and copy:
                self.copies.append((len(self.out) - len(piece.lines), old))
            elif old and not moving and piece.redressed:
                self.redressed.append((len(self.out) - len(piece.lines), len(piece.lines), old))
            if piece.marked:
                self.remarked.add(old.span.first)
            if not piece.tail:
                self.seen.add((node.get("anchor") or (old.anchor if old else "")).lower())
            elif not piece.holds:
                self._emit_anchor(old, piece)
            wraps = piece.kind == "wrapped"
            under = _Place(False, old, piece.indent, piece.column, wraps, above=piece) if piece.holds else _Place()
            self.nodes(node["children"], under, node["id"], went)
            if piece.tail and piece.holds:
                self._emit_anchor(old, piece)

    def _kept(self, old: Node, node: dict, place: _Place, fresh: bool, at: int) -> _Piece | None:
        """``node`` over its own lines, while their context holds: a list item
        under the item it was under, or anywhere in the document's flow, and a
        block that can hold the children it has. Unchanged, it is a straight
        copy of them, which with ``_gap`` keeping what stood between two tiles
        is the whole of law 1. Never weaken that path. Changed in its class, it
        is ``_encoded``'s; changed in its text, it keeps its dress. ``fresh``:
        its dress read as the block before it (``shape``), which it keeps only
        when the cause was a caret the file read as an anchor, which shows it
        was no first block where it stood."""
        span = old.span
        if not self._holds_here(old, node, place):
            return None
        content = node["content"] if node["content"] == old.content else _trim(node["content"])
        text = _as_text(content, self.seen, at == 0 and not fresh) if content != old.content or fresh else content
        if fresh and text == content:
            return None
        content, anchor = text, node.get("anchor") or old.anchor
        piece = _Piece([], span.kind, old.tile, span.indent, span.column, span.kind in _ITEMS, base=place.base)
        if anchor.lower() == old.anchor.lower() and _same_lines(content, old.content):
            piece.lines = self.doc.lines[span.first:span.last]
            piece.tail = anchor if old.after else ""
            return piece
        if not _same_class(old, content):
            return None
        piece.lines = self._redressed(old, content)
        piece.edited = True
        if span.kind == "item":
            piece.column = content_column(_bare(piece.lines[0]))
        self._attach_anchor(piece, old, anchor)
        return piece

    def _holds_here(self, old: Node, node: dict, place: _Place) -> bool:
        """Whether ``old``'s lines can write ``node`` here: it stands where it
        was read, and it can hold the children ``node`` has."""
        holds = old.span.kind in _ITEMS or (place.flow and old.span.kind == "heading")
        return self._stands_here(old, place) and (holds or not node["children"])

    def _left_alone(self, old: Node | None, node: dict, place: _Place) -> bool:
        """Whether ``node`` is ``old`` as the file has it, where it stood: a
        block ``shape`` found merged with the one before it and wrote anew,
        which is no change of the person's."""
        return bool(old) and self._holds_here(old, node, place) and _same_lines(node["content"], old.content) and (
            (node.get("anchor") or old.anchor).lower() == old.anchor.lower())

    @staticmethod
    def _stands_here(old: Node, place: _Place) -> bool:
        """Whether ``old`` still stands where it was read: in the flow, or under
        the item it was under, wherever that item now stands, or with whatever
        marker. ``_column`` moves it with that item's text; an item new in this
        change holds none of the old."""
        if place.flow:
            return old.flow
        return place.parent is not None and old.parent is place.parent

    def _redressed(self, old: Node, content: str) -> list[str]:
        """The lines of ``old`` holding ``content``, any anchor at a line's end
        taken off first. A line added takes the ending its block's lines have,
        and the last line the ending the block had: in a file of mixed endings
        the blank line after it keeps its own."""
        doc, span = self.doc, old.span
        was, now = old.content.split("\n"), content.split("\n")
        ends = [*span.sources[:len(was)], old.text_end]
        raws = [doc.lines[a:b] for a, b in zip(ends, ends[1:])]
        if old.inline:
            raws[-1][-1] = _end_anchor(raws[-1][-1])[0]
        code = _is_code(old.content)
        eol = _usual_ending(doc.lines[span.first:old.text_end], doc.eol)
        if not span.marker:
            lines = _aligned_lines(span, was, now, raws, code, eol)
        else:
            lines = [_first_line(span, raws[0][0], was[0], now[0]),
                     *_aligned_lines(span, was[1:], now[1:], raws[1:], code, eol)]
        lines[-1] = _bare(lines[-1]) + _ending(doc.lines[old.text_end - 1])
        return lines

    def _attach_anchor(self, piece: _Piece, old: Node, anchor: str):
        """Put ``anchor`` back once: in the form the block had it, or, new or
        at the end of a line that can no longer hold it, in the form its class
        takes. In an item it stands at the text of the marker now written."""
        span, eol = old.span, self.doc.eol
        if not anchor:
            return
        under = " " * (piece.column - text_column(span.indent)) if span.marker else ""
        own_line = f"{span.indent}{under}^{anchor}{eol}"
        if old.text_end < span.last:
            tail = self.doc.lines[old.text_end:span.last]
            kept = anchor == old.anchor and piece.column == span.column
            piece.lines += tail if kept else [*tail[:-1], own_line]
        elif old.after:
            piece.tail = anchor
        elif (at := _anchor_line(span.kind, piece.lines, anchor,
                                 old.inline and not _text_ends(span.kind, self._unanchored(old)),
                                 None if span.marker else _inside(self.doc, old))) is not None:
            line = self.doc.lines[old.text_end - 1]
            written = _end_anchor(line)[1].replace(old.anchor, anchor) if old.inline else f" ^{anchor}"
            piece.lines[at] = _with_anchor(piece.lines[at], written)
        elif span.kind in _ITEMS:
            piece.lines += [*_apart(span.kind, piece.lines, eol), own_line]
        else:
            piece.tail = anchor

    def _unanchored(self, old: Node) -> list[str]:
        """The text lines of ``old`` as the file has them, the anchor ending the
        last taken off."""
        lines = self.doc.lines[old.span.first:old.text_end]
        return [*lines[:-1], _end_anchor(lines[-1])[0]] if old.inline else lines

    def _fitted(self, piece: _Piece, old: Node, node: dict, place: _Place, at: int) -> _Piece:
        """``piece`` at a column that keeps it the block it was, whatever now
        stands before it. Next to the tile it was next to, read as the file
        read it, it is. Code by indentation that has to move is text at its new
        column, or the item's after a list, so the block it was is a fence, if
        it holds no children, which make it the item it is to this parser;
        moved with the text of the item it is in, it is code still. Straight
        after an item with nothing on its line it would be that item's text,
        and a blank line would end the item: a fence stays in it. A fence
        under an item stands at the item's text."""
        shift = 0 if place.flow else place.column - place.parent.span.column
        code_shift = self._code_shift(old, place)
        if self._adjacent(piece) and not shift and not code_shift and self._reads_as_read(old, place):
            return piece
        child = place.above is not None and place.above is self.prev
        blank = self.prev is None or bool(self._gap(piece, child))
        col, want = text_column(old.span.indent), self._column(old, place, at, blank)
        code = self._indented_code(old) and not node["children"]
        copied = not piece.edited
        if code and (want != col + code_shift or self._after_code(child) or child and _opens_empty(self.prev)):
            fence = self._flow_block(_fenced("\n".join(map(_bare, piece.lines))), node.get("anchor") or old.anchor)
            if not place.flow:
                want = min(want, place.base)
                fence.lines, fence.indent = _repad(fence.lines, 0, want), " " * want
            fence.redressed = copied
            return fence
        if want != col:
            piece.lines = _repad(piece.lines, col, want)
            piece.indent, piece.column = " " * want, piece.column + want - col
            piece.edited = True        # what stood beside it moves with it
            piece.redressed = copied
        return piece

    def _moved(self, old: Node, tree: list, at: int) -> bool:
        """Whether ``old``, written at ``tree[at]``, stands next to neither block
        it stood beside, one of which is still there: a block whose neighbour
        went, or had one put beside it, stays where it was."""
        was = self.beside[old.index]
        now = (tree[at - 1]["id"] if at else None, tree[at + 1]["id"] if at + 1 < len(tree) else None)
        return was[0] != now[0] and was[1] != now[1] and bool(self.in_tree & set(was))

    def _marked(self, piece: _Piece, place: _Place, tree: list, at: int):
        """A list item moved here in the mark of the list it joins: the item's
        written just before it, else the one's after it. A bullet of another
        mark, or a number of another delimiter, starts a list of its own."""
        if piece.kind not in _ITEMS or not piece.moved:
            return
        after = self.known.get(tree[at + 1]["id"]) if at + 1 < len(tree) else None
        if place.sibling:
            mark = _MARK_RE.match(_bare(place.sibling.lines[0]))[1]
        elif after and after.span.marker and self._stands_here(after, place):
            mark = _MARK_RE.match(after.span.marker)[1]
        else:
            return
        line = _bare(piece.lines[0])
        found = _MARK_RE.match(line)
        rest = line[found.end():]
        if found[1] != mark and (found[1] in "-*+") == (mark in "-*+") and not _RULE_RE.fullmatch(mark + rest):
            piece.lines = [line[:found.start(1)] + mark + rest + _ending(piece.lines[0]), *piece.lines[1:]]
            piece.edited = piece.marked = True

    def _reads_as_read(self, old: Node, place: _Place) -> bool:
        """Whether ``old``, next to the tile it was next to, is read as the file
        read it: that tile was read so, or leaves open what it left, and no
        list written anew before it would now take it in."""
        if not (self.as_read or self._ends_as_read()):
            return False
        if not place.flow or not self.list_new or not self._list_open(bool(_gap_lines(self.doc, self.prev.tile, 1))):
            return True
        col = text_column(old.span.indent)
        return self._past_list(col, _held_heading(old)) == col

    def _list_open(self, blank: bool) -> bool:
        """Whether a list is open at the left edge for a block after the last
        piece, ``blank`` a blank line between: one ends an item with nothing
        on its line, and the list with it when that item is the list's."""
        if self.list_root is None:
            return False
        return not (blank and _opens_empty(self.prev) and text_column(self.prev.indent) <= self.list_root)

    def _code_shift(self, old: Node, place: _Place) -> int:
        """How far code by indentation under an item moves with it: as far as
        the text of the item's innermost marker (``_code_column``), on the last
        of its lines that opens one."""
        if place.flow:
            return 0
        return _code_column_of(place.above.lines) - _code_column(_bare(self.doc.lines[old.parent.span.first]))

    def _indented_code(self, old: Node) -> bool:
        """``_indented_code`` of ``old``, after what the file left open before it."""
        return _indented_code(self.doc, old, self._read_in(old.tile))

    def _column(self, old: Node, place: _Place, at: int, blank: bool) -> int:
        """The column ``old`` can stay itself at. In the flow, ``_past_list``,
        and past a blank line ``CODE`` columns are code, as they are where
        nothing above holds them in a paragraph (a heading, a rule, code), to
        a block the file did not read as code; a list item there
        stands at the list's column, or it would go under the item before it.
        Under an item whose text a new marker moved it keeps clear of that text
        and short of code past it, and code by indentation moves with the text.
        Past a list item just before it, a list item at its column or further
        would go under that one, and a block at its text; but within its parent.
        Text at the item's column as this parser reads it, with no sibling
        before it, would be the item's own text, as would code below an item
        whose marker wraps a block."""
        span = old.span
        col = text_column(span.indent)
        if place.flow:
            if self._list_open(blank):
                if span.kind in _ITEMS:
                    return min(col, self.list_root)
                col = self._past_list(col, _held_heading(old))
            code = blank or self._open(False) in (None, "closed", "code") and not self._indented_code(old)
            return CODE - 1 if col >= CODE and span.kind != "verbatim" and code else col
        shift = place.column - place.parent.span.column
        floor = place.column if shift else 0
        if place.sibling and _class_read(span.kind, _bare(self.doc.lines[span.first])) in _ITEMS:
            return max(min(col, text_column(place.sibling.indent)), floor)
        if place.sibling:
            return max(col if col < place.sibling.column else text_column(place.sibling.indent), place.column)
        if self._indented_code(old):
            return col + self._code_shift(old, place)
        if at == 0 and span.kind not in _ITEMS and col <= _item_column(place.above.lines):
            return max(col, floor) if span.kind in ("fence", "math") and not place.wraps else self.deepest + PAST_TEXT
        return min(max(col, floor), place.column + CODE - 1) if shift else col

    def _past_list(self, col: int, heading: bool) -> int:
        """The column a block in the flow at ``col`` can stand at with a list
        open before it. Left of the text of the list's first item it is out of
        the list. Past the list's column it would join the list, so it goes
        there; unless its text is a heading only its indent holds back, which
        at that column would take every block after it: it goes a column past
        the list's, still left of its text, or failing that a column into it."""
        if col <= self.list_root or col < self.list_text:
            return col
        if not heading:
            return self.list_root
        return self.list_root + 1 if self.list_root + 1 < min(self.list_text, CODE) else self.deepest + 1

    def _after_code(self, child: bool = False) -> bool:
        """Whether code by indentation ends what stands just before, which is
        that block's parent when ``child``: two such runs in a row, a blank
        line between them, are one."""
        return bool(self.prev) and (self.prev.kind == "verbatim" or self._open(child) == "code")

    def _encoded(self, node: dict, old: Node | None, place: _Place, tree: list, at: int) -> _Piece:
        """``node`` as a new block at this place, dressed like its nearest
        sibling. A child of anything but a heading is a list item in the file,
        and so is a block with children that cannot hold them. A rule is no
        list item, whatever its marks. A block CommonMark read as code by
        indentation, which this parser reads as text under an item, is a fence
        of its lines wherever it now goes (``_read_as_code``), as ``_fitted``
        writes one: an anchor this parser read on them is code to CommonMark,
        and goes on its own line after the fence as well."""
        first = at == 0 and node["id"] not in self.fresh
        content, kids = _trim(node["content"]), node["children"]
        anchor = node.get("anchor") or (old.anchor if old else "")
        nested = kids or not place.flow or self.list_root is not None
        if old and not kids and _same_lines(node["content"], old.content) and _read_as_code(self.doc, old):
            content = _fenced("\n".join(map(_bare, self.doc.lines[old.span.first:old.span.last])))
        else:
            content = _as_text(content, self.seen, first)
            if text_column(content) >= CODE and (nested or self._after_code(place.above is not None
                                                                            and place.above is self.prev)):
                content = _fenced(content)
        cls = _class_of(content)
        flow_kids = old.span.kind == "heading" if old else cls == "heading"
        if place.flow and cls != "item" and (not kids or flow_kids):
            return self._flow_block(content, anchor)
        piece = self._list_item(content, anchor, *self._dress(place, tree, at))
        piece.holds = not (place.flow and flow_kids)
        return piece

    def _flow_block(self, content: str, anchor: str) -> _Piece:
        """``content`` as a new block in the document's flow. Past a list open
        before it, it would join the list, whatever whitespace it starts with,
        so it stands where ``_past_list`` puts it."""
        eol = self.doc.eol
        col = text_column(content)
        if self.list_root is not None and col < CODE:
            want = self._past_list(col, block_class(content) == "heading")
            content = "\n".join(_repad(content.split("\n"), col, want)) if want != col else content
        cls = "verbatim" if col >= CODE else _class_of(content)
        if cls == "heading" and content[:1] in " \t":
            cls = "plain"                  # its indent holds it back from a heading, to this parser
        lines = content.split("\n")
        tail = ""
        if anchor and (at := _anchor_line(cls, lines, anchor, col=0)) is not None:
            lines[at] += f" ^{anchor}"
        elif anchor:
            tail = anchor
        return _Piece([line + eol for line in lines], cls, tail=tail, edited=True)

    def _list_item(self, content: str, anchor: str, indent: str, bullet: str, delimiter: str = "") -> _Piece:
        """``content`` as a list item: its own marker, or ``bullet`` before it,
        which it stands behind when it only wraps a block (``- # H``), or when
        its own text would read as one (``- * x``): then its lines stand under
        its own marker first, as they would without the bullet. As a bare item
        a bullet here gives a plain block a marker; a file has no other way to
        nest it, and text that would read as its box (``[ ] x``) is escaped. A
        bullet around a block takes the other mark where the two would read as
        a rule: ``- ---`` and ``- - -`` are rules, as is ``* **``. A number
        takes ``delimiter``, its list's."""
        content = content.lstrip(" \t")
        lines = content.split("\n")
        rule = _RULE_RE.fullmatch(lines[0])
        own = None if rule else LIST_BLOCK_RE.match(content)
        if own and _wrapped_item(content):               # its text would be read as the block it wraps
            lines = [lines[0], *item_rest(lines[1:], own, "", " " * content_column(lines[0]))]
            own = None
        elif not own and _TASK_SPAN_RE.match(lines[0]):
            lines[0] = "\\" + lines[0]
        width = content_column(lines[0]) if own else 2
        if own and lines[0][0] in "-*+" and not _RULE_RE.fullmatch(bullet + lines[0][1:]):
            lines[0] = bullet + lines[0][1:]
        elif own and delimiter and lines[0][0].isdigit():
            lines[0] = lines[0][:own.end() - 2] + delimiter + lines[0][own.end() - 1:]
        first = lines[0] if own else f"{_unruled(bullet + ' ', lines[0])}{lines[0]}"
        out = [f"{indent}{first}", *item_rest(lines[1:], own, indent, " " * width)]
        kind = "wrapped" if not own and wraps_block(lines[0]) else "item"        # as the parser reads ``- p``
        if anchor and (at := _anchor_line(kind, out, anchor)) is not None:
            out[at] += f" ^{anchor}"
        elif anchor:
            out += [*_apart(kind, out, ""), f"{indent}{' ' * (content_column(out[0]) - text_column(indent))}^{anchor}"]
        eol = self.doc.eol
        out = [("" if _blank(line) else line) + eol for line in out]
        return _Piece(out, kind, None, indent, content_column(_bare(out[0])), edited=True)

    def _dress(self, place: _Place, tree: list, at: int) -> tuple[str, str, str]:
        """The indent, the bullet and the delimiter of a new list item here: a
        nearby sibling's, else the file's. Below an item, at its text column at
        least: ``10. a`` needs four. In the flow four columns are code after a
        blank line, so an indent there stops short of them."""
        indent, bullet, delimiter = None, self.doc.bullet, ""
        for sibling in (*reversed(tree[max(0, at - NEARBY):at]), *tree[at + 1:at + 1 + NEARBY]):
            old = self.known.get(sibling["id"])
            if old and old.span.marker and self._stands_here(old, place):
                mark = _MARK_RE.match(old.span.marker)[1]
                indent = old.span.indent if not place.flow or text_column(old.span.indent) < CODE else ""
                bullet, delimiter = (mark, "") if mark in "-*+" else (bullet, mark)
                break
        if indent is None:
            indent = "" if place.flow else place.indent + self.doc.unit
        if not place.flow and text_column(indent) < place.column:
            indent = " " * place.column
        return indent, bullet, delimiter

    def _adjacent(self, piece: _Piece) -> bool:
        """Whether the last piece and ``piece`` stood side by side in the file."""
        prev = self.prev
        return bool(prev and prev.tile and piece.tile) and _neighbour(self.doc, prev.tile, 1) is piece.tile

    def _stood_together(self, piece: _Piece) -> bool:
        """Whether the blocks the last piece and ``piece`` write stood side by
        side in the file, one or both written anew where it stood."""
        was = self.prev.tile or (self.prev.node.tile if self.prev.node else None)
        now = piece.tile or (piece.node.tile if piece.node else None)
        return bool(was and now) and _neighbour(self.doc, was, 1) is now

    def _merges(self, piece: _Piece) -> bool:
        """Whether this parser would read ``piece``, straight after the last,
        as part of it: a pair the file joined only where CommonMark reads it
        (a lazy line after a footnote's paragraph) can be joined no more once
        the last piece is a paragraph to this parser too. An anchor on its own
        line read as the last one's is no part of it, and a list item where a
        list is open goes on with that list (``breaks_paragraph``). The last
        piece is read where it stands: in an item, under one of its column, and
        after a line of text, past which four columns are no code, behind a
        heading, which leaves nothing else open."""
        if piece.kind in _ITEMS and self.list_root is not None:
            return False
        lines = [_bare(line) for line in [*self.prev.lines, piece.lines[0]]]
        at = len(self.out) - len(self.prev.lines)
        if self.prev.base:
            lines = [" " * (self.prev.base - 2) + "- x", "", *lines]
        elif at > (self.doc.front.last if self.doc.front else 0) and not _blank(self.out[at - 1]):
            lines = ["# x", *lines]
        return _read_as(_part("\n".join(lines))) <= _read_as(_part("\n".join(lines[:-1])))

    def _ends_as_read(self) -> bool:
        """Whether the last piece, edited where it stands, leaves open what it
        left in the file, a list item's text where it stood: what follows it
        is read as it was. One moved over takes what follows with it."""
        prev, tile, lines = self.prev, self.prev.tile, self.doc.lines
        if text_column(prev.lines[0]) != text_column(lines[tile.first]):
            return False
        if prev.kind in _ITEMS and tile.kind == "node" and prev.column != tile.node.span.column:
            return False
        return self.end == _after(self._read_in(tile), _tile_kind(tile), _tile_lines(self.doc, tile))

    def _open(self, child: bool) -> str | None:
        """What the last piece leaves open for the next, which is in it when it
        is its ``child`` (``_within``)."""
        end, tile = self.end, self.prev.tile
        if self.as_read:
            end = _after(self._read_in(tile), _tile_kind(tile), _tile_lines(self.doc, tile))
        return _within(end, child)

    def _joins(self, piece: _Piece, child: bool) -> bool:
        """Whether ``piece``, written straight after the last, would join it.
        With no list open a list item ``CODE`` columns in starts none: it is
        text. A child item left of its parent's text is no line of that text:
        it goes on the list its parent is in."""
        line = _repad([_bare(piece.lines[0])], piece.base, 0)[0]
        if child and piece.kind in _ITEMS and text_column(piece.lines[0]) < piece.base:
            child = False
        return _joins(self._open(child), _class_read(piece.kind, line, self.list_root is not None or child), line)

    def _settle(self, piece: _Piece, gap: list, child: bool):
        """Note what ``piece``, written after ``gap``, leaves open. A piece of the
        file's, read where the file read it, leaves what it left there, which
        is looked up only when a change beside it asks; so is one after a piece
        that left what it left in the file, once it is found to leave the same,
        which a run of blocks after an edit is, from the next block on."""
        tile, fresh = piece.tile, bool(gap) or self.prev is None
        copy = tile is not None and not piece.edited
        as_read = copy and (
            (fresh and (tile.index == 0 or self.doc.tiles[tile.index - 1].kind == "gap"))
            or (not fresh and self.as_read and self._adjacent(piece)))
        if not as_read:
            self.end = _after(None if fresh else self._open(child), piece.kind, _repad(piece.lines, piece.base, 0))
            if copy and not fresh and self._adjacent(piece):
                as_read = self.end == _after(self._read_in(tile), _tile_kind(tile), _tile_lines(self.doc, tile))
        self.as_read = as_read

    def _read_in(self, tile: Tile) -> str | None:
        """What the file left open before ``tile``: read along its run of tiles
        with no blank line between, from the run's start or the last tile read."""
        tiles, start = self.doc.tiles, tile.index
        while start not in self.read_in and start and tiles[start - 1].kind != "gap":
            start -= 1
        end = self.read_in.setdefault(start, None)
        for prev, run in zip(tiles[start:tile.index], tiles[start + 1:tile.index + 1]):
            end = _after(end, _tile_kind(prev), _tile_lines(self.doc, prev))
            end = _within(end, run.kind == prev.kind == "node" and run.node.parent is prev.node)
            self.read_in[run.index] = end
        return end

    def _gap(self, piece: _Piece, child: bool = False) -> list[str]:
        """The blank lines before ``piece``. A pair adjacent in the file keeps
        its gap, unless Markdown would join them where the file did not, and so
        does a pair that stood so, a block of it written anew. Any
        other takes the larger of the gaps its two blocks had, and at least
        what Markdown needs: ``required``, and one blank line where the two
        would join. Between list items only a gap that was between list items
        counts, or a block added to the end of a tight list would take the blank
        line after it; and of an item moved, the list it joins counts first.
        Classes are CommonMark's (``_class_read``). A new file
        gives everything but a list item one blank line. A heading with a line
        added ends in text. ``child``: ``piece`` is the first block under the
        item before it, which with nothing on its line yet a blank line would
        end."""
        doc, prev = self.doc, self.prev
        if child and _opens_empty(prev):
            return []
        if self._adjacent(piece):
            gap, tile = _gap_lines(doc, prev.tile, 1), piece.tile
            if gap or (self.as_read and not piece.edited) or not self._joins(piece, child):
                return gap
            line = _repad([_bare(doc.lines[tile.first])], _inside(doc, tile.node), 0)[0]
            joined = _joins(self._read_in(tile), _class_read(_tile_kind(tile), line), line)
            return gap if joined and not self._merges(piece) else [doc.eol]
        joins = self._joins(piece, child)
        if not child and self._stood_together(piece):
            gap = _gap_lines(doc, prev.tile or prev.node.tile, 1)
            return gap if gap or not (joins or piece.kind == "verbatim" or self._merges(piece)) else [doc.eol]
        ends = "plain" if prev.kind == "heading" and len(prev.lines) > 1 else prev.kind
        kind = _class_read(piece.kind, _bare(piece.lines[0]))
        both = prev.kind in _ITEMS and kind in _ITEMS
        need = max(required(ends, kind, piece.lines[0]), int(joins))
        if not doc.lines and not both:
            need = 1
        was = piece.tile or (piece.node.tile if piece.node else None)      # where a block written anew stood
        if both:
            found = self._item_gap(prev.tile, (1, -1))
            if found is None or not piece.moved:
                found = max(found or [], self._item_gap(was, (-1,)) or [], key=len)
        else:
            found = max((_gap_lines(doc, tile, step) for tile, step in ((prev.tile, 1), (was, -1)) if tile),
                        key=len, default=[])
        return found if len(found) >= need else [doc.eol] * need

    def _item_gap(self, tile: Tile | None, steps) -> list[str] | None:
        """The larger of the gaps between ``tile`` and the list items beside it
        on ``steps``, None where none stands: how loose its list is. Of an item
        moved into a list, the list's own say, not the one it came from."""
        found = None
        for step in steps:
            beyond = _neighbour(self.doc, tile, step) if tile else None
            if beyond and beyond.kind == "node" and beyond.node.span.kind in _ITEMS:
                gap = _gap_lines(self.doc, tile, step)
                found = gap if found is None or len(gap) > len(found) else found
        return found

    def _emit(self, piece: _Piece, place: _Place, block_id):
        """``piece``, and what it leaves open for the next: a block that follows
        a list is read against the list, and a footnote holds what is indented
        below it as a list item does. A lazy line of a list's text leaves the
        list open, though this parser reads it as a block of its own."""
        child = place.above is not None and place.above is self.prev
        gap = self._gap(piece, child) if self.prev else self._head()
        item = piece.kind in _ITEMS
        lazy = not item and place.flow and not gap and self.prev is not None and self._joins(piece, child)
        self._settle(piece, gap, child)
        self.out += gap
        self.starts[len(self.out)] = block_id
        self.out += piece.lines
        self.prev = piece
        col = text_column(piece.indent)
        text = piece.edited and piece.kind not in ("fence", "math", "verbatim")
        lists = [line for i, line in enumerate(map(_bare, piece.lines)) if _LIST_SPAN_RE.match(line)
                 and not _RULE_RE.fullmatch(line) and (i == 0 or breaks_paragraph(line))] if text else []
        if item or lists:
            self.deepest = max([piece.column, *map(content_column, lists)])
        place.sibling = piece if item else None
        if not place.flow:
            return
        if item:
            self.list_root, self.list_text = col, piece.column
            self.list_new = not self._item_as_read(piece)
            return
        if piece.kind == "plain" and FOOTNOTE_RE.match(_bare(piece.lines[0])):
            self.list_root, self.list_text = col, col + CODE
        elif not lazy and (self.list_root is None or col <= self.list_root or col < self.list_text):  # (else in it)
            opener = min(lists, key=text_column, default=None)
            self.list_root = text_column(opener) if opener else None
            self.list_text = content_column(opener) if opener else 0
        else:
            return
        self.list_new = piece.edited or piece.tile is None

    def _item_as_read(self, piece: _Piece) -> bool:
        """Whether the list item ``piece`` is one the file had there, at the
        columns it had, with nothing on its line or something as it had: what
        follows it reads it as the file did."""
        old = piece.node
        if old is None or old.span.kind not in _ITEMS:
            return False
        if piece.tile is not None and not piece.edited:
            return True
        was = _Piece(self.doc.lines[old.span.first:old.span.last], old.span.kind)
        return (text_column(old.span.indent), old.span.column, _opens_empty(was)) == (
            text_column(piece.indent), piece.column, _opens_empty(piece))

    def _emit_anchor(self, old: Node | None, piece: _Piece):
        """An anchor on its own line, after the subtree of its block, at that
        block's column, and at least at the text of the item it is in, left of
        which past a blank line it would end the item. After a blank line, at or
        left of the list open at the left edge, it ends that list; straight
        after an item it is the item's lazy line, and the list stays open."""
        doc = self.doc
        kept = old and old.after and old.anchor == piece.tail and old.span.indent == piece.indent
        code = piece.kind == "verbatim" or not piece.base and text_column(piece.indent) >= CODE
        indent = "" if code else piece.indent                        # in it, it would be code
        indent = " " * piece.base if text_column(indent) < piece.base else indent
        lines = doc.lines[old.after.first:old.after.last] if kept else [f"{indent}^{piece.tail}{doc.eol}"]
        line = _Piece(lines, "plain", old.after if kept else None, node=old, base=piece.base)
        gap = self._gap(line)
        self._settle(line, gap, False)
        self.out += gap + lines
        self.prev = line
        self.seen.add(piece.tail.lower())
        if gap and self.list_root is not None and text_column(lines[0]) <= self.list_root:
            self.list_root = None

    def _head(self) -> list[str]:
        """The blank lines the body opens with."""
        tiles = self.doc.tiles
        return _gap_lines(self.doc, tiles[0], 0) if tiles and tiles[0].kind == "gap" else []

    def finish(self) -> tuple[str, dict]:
        """The text and its provenance. The blank lines ending the file stay with
        the block they followed, written anew or not, so a deletion at the end
        leaves no blank tail; the last newline is the file's whatever now stands
        last. A line ending in a lone ``\\r`` gives the blank line after it its
        ending: a ``\\n`` there would make one ending of the two. A file that
        comes to open with a block's U+FEFF takes a byte-order mark before it,
        or that would be the mark."""
        doc = self.doc
        tail = doc.tiles[-1] if doc.tiles and doc.tiles[-1].kind == "gap" else None
        before = _neighbour(doc, tail, -1) if tail else None
        if tail and self.prev is None and not doc.flat:
            self.out += _gap_lines(doc, tail, 0)
        elif before and self.prev and (self.prev.tile is before or self.prev.node is before.node):
            self.out += _gap_lines(doc, tail, 0)
        lines = self.out
        for i in range(len(lines) - 1):
            if not _ending(lines[i]):
                lines[i] += doc.eol
            if lines[i].endswith("\r") and lines[i + 1].startswith("\n"):
                lines[i + 1] = "\r" + lines[i + 1][1:]
        if lines:
            lines[-1] = (_bare(lines[-1]) + (_ending(lines[-1]) or doc.eol)) if doc.final else _bare(lines[-1])
        if not doc.front and _front_of(lines):    # a divider, but it would open front matter
            lines[0] = "***" + lines[0][3:]
        bom = doc.bom or ("\ufeff" if lines and lines[0].startswith("\ufeff") else "")
        return bom + "".join(lines), self.starts


def _anchor_line(kind: str, lines: list[str], anchor: str, held: bool = False, col: int | None = None) -> int | None:
    """The line of ``lines``, a block of class ``kind`` as written, that
    ``anchor`` can end, if one can: the last of the block's first part, which
    keeps its id where the lines read as several, so that this parser reads
    the anchor back on that part, and CommonMark reads the line as text
    (``_text_ends``). ``held``: the block had it there already, where
    CommonMark read no text either, which is the person's to keep. ``col``:
    the column the lines are read from, by default their first's (a list
    item's marker); text keeps what indent it has past its item's text,
    which holds `` # x`` back from a heading."""
    if kind not in ("plain", "item", "wrapped", "heading"):
        return None
    bare = _repad([_bare(line) for line in lines], text_column(lines[0]) if col is None else col, 0)
    parts = _part("\n".join(bare)).flat
    if not parts:
        return None
    span = parts[0].span
    at = span.last - 1
    read = _part("\n".join([*bare[:at], f"{bare[at]} ^{anchor}", *bare[at + 1:]])).flat
    if not read or not read[0].inline or read[0].span.last != span.last:
        return None
    return at if held or _text_ends(span.kind, bare[span.first:span.last]) else None


def _text_ends(kind: str, lines: list[str]) -> bool:
    """Whether CommonMark reads the last of ``lines``, a block of class
    ``kind``, as the text a paragraph, an item or a heading ends with. No
    rule, underline, table row, quote, HTML or definition is, though this
    parser reads them as text. An item's lines are read from its text."""
    own = _repad([_bare(line) for line in lines], text_column(lines[0]), 0)
    if kind in _ITEMS:
        own = [_LIST_SPAN_RE.match(own[0])[3], *_repad(own[1:], content_column(own[0]), 0)]
    if block_class(own[0]) == "heading":
        return len(own) == 1
    return _after(None, "plain", own) == "open" and not _DEFINITION_RE.match(own[-1])


def _apart(kind: str, lines: list[str], eol: str) -> list[str]:
    """The blank line before an anchor on its own line in a list item written
    as ``lines``: one where the item ends in a block Obsidian reads an id apart
    from, a quote, a table or HTML, which it would join. None where they leave
    nothing open, or a paragraph, which the anchor ends (a box with nothing
    after it is one), so that the anchor is the item's and its list stays
    tight; and none under a marker wider than this parser's column
    (``-   x``), below which, past a blank line, it would be a child."""
    end, last = _end_of(kind, lines), _LIST_SPAN_RE.match(_bare(lines[-1]))
    text = (last[3] if last else _bare(lines[-1])).strip()
    paragraph = end == "lazy" and block_class(text) == "plain"
    wide = content_column(_bare(lines[0])) > _item_column(lines[:1])
    quote = block_class(_STACKED_RE.sub("", text, count=1)) == "quote"         # empty, it leaves nothing open
    return [] if end == "closed" and not quote or paragraph or wide else [eol]


def _item_column(lines: list[str]) -> int:
    """The column past which this parser reads a line below a list item written
    as ``lines``, past a blank line, as no text of it: one past the marker of
    its last list line, wherever the text stands (``-   a`` has 2)."""
    found = [hit for hit in map(_LIST_SPAN_RE.match, map(_bare, lines)) if hit]
    return max(text_column(hit[1]) + len(hit[2]) + 1 for hit in found) if found else 0


def _opens_empty(piece) -> bool:
    """Whether ``piece`` is a list item with nothing on its line, which a blank
    line ends."""
    found = piece.kind in _ITEMS and len(piece.lines) == 1 and _LIST_SPAN_RE.match(_bare(piece.lines[0]))
    return bool(found) and not found[3].strip(" \t")


def _class_read(kind: str, line: str, nested: bool = True) -> str:
    """The class CommonMark gives a block of class ``kind`` whose first line,
    read where it stands, is ``line``: with no list open (``nested``) a list
    item four columns in is text, and a list marker with nothing after it
    (``-``) is an empty item, which this parser reads as text."""
    if kind in _ITEMS and not nested and text_column(line) >= CODE:
        return "plain"
    return "item" if kind == "plain" and _BARE_MARKER_RE.fullmatch(line) else kind


def _as_text(content: str, seen: set, first: bool) -> str:
    """``content`` with a backslash before each caret the file would read as
    an anchor, as it would after a block (``press ^c``, a note that is only
    ``^ref``): Gamma holds it as text, and it stays text. A copy of an anchor
    in ``seen``, written before it, is text already. ``first``: no block
    stands before it in what holds it, and a note that is only ``^ref`` is
    text there."""
    if "^" not in content:
        return content
    lines = content.split("\n")
    head = "" if first else "x\n\n"
    for node in _part(head + content).flat:
        if node.anchor and node.anchor.lower() not in seen:
            at = (node.after or node.span).last - 1 - head.count("\n")
            caret = lines[at].rfind("^")
            lines[at] = f"{lines[at][:caret]}\\{lines[at][caret:]}"
    return "\n".join(lines)


def _inside(doc: Doc, node: Node) -> int:
    """The column ``node`` is read from (``_Place.base``): the text of the
    list item it stands in, 0 in the flow."""
    parent = node.parent
    return _code_column(_bare(doc.lines[parent.span.first])) if parent and parent.span.kind in _ITEMS else 0


def _tile_lines(doc: Doc, tile: Tile) -> list[str]:
    """A tile's lines as the item it stands in reads them, from its text."""
    return _repad(doc.lines[tile.first:tile.last], _inside(doc, tile.node), 0)


def _read_as(doc: Doc) -> int:
    """How many blocks and anchors on their own line ``doc`` reads as."""
    return len(doc.flat) + sum(1 for node in doc.flat if node.after)


def _held_heading(node: Node) -> bool:
    """Whether ``node`` is a heading's text its indent holds back."""
    return node.span.kind != "verbatim" and block_class(node.content) == "heading"


def _code_column(line: str) -> int:
    """The column code by indentation counts from below a list block whose
    first line is ``line``: the text of its innermost marker, as CommonMark
    nests a bullet that wraps one (``- - x`` has 4)."""
    col = 0
    while (found := _LIST_SPAN_RE.match(line)) and not _RULE_RE.fullmatch(line):
        col = content_column(line)
        line = " " * col + found[3]
    return col


def _class_of(content: str) -> str:
    """``block_class``, but a rule is a block of text, whatever its marks are
    (``* * *``)."""
    return "plain" if _RULE_RE.fullmatch(content.split("\n", 1)[0]) else block_class(content)


def _wrapped_item(content: str) -> bool:
    """Whether ``content`` is a bullet whose text a file would read as a block
    of its own (``- # H``, ``- - x``): only a bullet around it keeps it one.
    Its text is the parser's, spaces and all (``-  - `` holds ``- ``, ``- -  ``
    holds ``-``). A number never wraps a block, and a rule is none (``* * *``)."""
    first = content.split("\n", 1)[0]
    found = _LIST_SPAN_RE.match(first)
    if not found or first[0] not in "-*+" or _RULE_RE.fullmatch(first):
        return False
    return wraps_block(found[3] if EMPTY_ITEM_RE.fullmatch(found[3]) else found[3].strip())


def _unruled(marker: str, text: str) -> str:
    """``marker`` before ``text``, its bullet the other one where the two would
    read as a rule (``- - -``, ``* * *``)."""
    return ("*" if marker[0] == "-" else "-") + marker[1:] if _RULE_RE.fullmatch(marker + text) else marker


def _indented_code(doc: Doc, node: Node, before: str | None) -> bool:
    """Whether CommonMark reads ``node`` as code by indentation: every line four
    columns past where the text of the item it is in starts (its innermost
    marker's, ``_code_column``), after a blank line or a block that holds no
    paragraph (a heading, a rule, a closed fence or HTML), as ``before`` says
    what the file left open there. This parser reads it so only at the left
    edge after a blank line, and as text or a list item under an item, a
    heading or HTML."""
    span = node.span
    if span.kind == "verbatim":
        return True
    inside = _inside(doc, node)
    lines = [line for line in doc.lines[span.first:span.last] if not _blank(line)]
    if span.kind not in ("plain", "item") or any(text_column(line) < inside + CODE for line in lines):
        return False
    return before in (None, "closed", "code")


def _code_column_of(lines: list[str]) -> int:
    """``_code_column`` of the last of a list block's ``lines`` that opens a list
    item, as an edit can write one under its first: below them code by
    indentation counts from its text."""
    col = 0
    for i, line in enumerate(map(_bare, lines)):
        if _LIST_SPAN_RE.match(line) and not _RULE_RE.fullmatch(line) and (i == 0 or breaks_paragraph(line)):
            col = _code_column(line)
    return col


def _tile_kind(tile: Tile) -> str:
    return tile.node.span.kind if tile.kind == "node" else "plain"


def _same_lines(content: str, read: str) -> bool:
    """Whether ``content`` has nothing to write over the block read as ``read``.
    Blank lines are the exception to whitespace reaching the file: a block
    with two in a row reads back with one, and would add one on every write."""
    if content == read:
        return True
    return content.count("\n") != read.count("\n") and _normal(content) == _normal(read)


def _same_class(old: Node, content: str) -> bool:
    """Whether ``content`` can go on in ``old``'s dress. A rule is a class of
    its own, whatever this parser reads its marks as (``- ---``, ``* * *``),
    and so is a bullet that only a bullet around it keeps one (``- # H``)."""
    span = old.span
    if (span.kind == "verbatim") != (text_column(content) >= CODE):
        return False
    if bool(_RULE_RE.fullmatch(old.content.split("\n", 1)[0])) != bool(_RULE_RE.fullmatch(content.split("\n", 1)[0])):
        return False
    if span.kind == "verbatim":
        return True
    if span.kind == "wrapped":          # a bullet wraps it, a list block only as it wrapped one
        was, now = block_class(old.content), block_class(content)
        return wraps_block(content.split("\n", 1)[0]) and (was == "item") == (now == "item")
    if _wrapped_item(content):
        return False                    # a bullet wraps it, which did not
    return block_class(old.content) == block_class(content)


def write(doc: Doc, ids, tree: list, fresh=()) -> tuple[str, dict]:
    """``tree`` as the text of ``doc``'s file, patched. ``ids``: the ids of
    ``doc``'s nodes in document order. Gives the text, and its provenance:
    ``{line: id}`` for the line each block starts on. A block in ``fresh`` is
    written as new, whatever it was."""
    if doc.problem:
        if tree:
            raise Unwritable(doc.problem)
        return doc.raw, {}
    return _walked(doc, ids, tree, fresh).finish()


def _walked(doc: Doc, ids, tree: list, fresh=()) -> _Walk:
    """The walk of ``write``, done, for ``finish`` to give its text."""
    if _too_deep(tree):
        raise Unwritable(TOO_DEEP)
    walk = _Walk(doc, ids, tree, fresh)
    walk.nodes(tree, _Place())
    return walk


def render(tree: list) -> str:
    """``tree`` as a new file: ``shape`` with no lines to keep, so that a new
    file loses no block either."""
    return shape(parse(""), (), tree)[1]


def _in_code(read: Doc, line: int) -> bool:
    """Whether ``line`` of the text ``read`` landed in a code block: one that
    runs to the end of the file, or a typed fence, which only has to reach the
    next one, a list block's too (``- - ```py``)."""
    return any(node.span.first <= line < node.span.last and (
        node.span.open or block_class(_STACKED_RE.sub("", node.content, count=1)) in ("fence", "math"))
        for node in read.flat)


def _hidden(doc: Doc) -> set:
    """The places of the blocks HTML left open above them takes in: HTML of the
    kinds that run past every blank line to their closer (``<!--``, ``<pre>``)
    opened at the start of a block's line, as the item it stands in reads the
    line, and not closed before the block. This
    parser reads them as blocks; any other reader takes them into the HTML, and
    a browser hides what a comment holds, whatever list item it stands in."""
    hidden, closer = set(), None
    for node in doc.flat:
        if closer:
            hidden.add(node.index)
        code = node.span.kind in ("fence", "math", "verbatim")
        for at in range(node.span.first, node.text_end):
            if not closer and "<" not in doc.lines[at]:
                continue
            line = _repad([_bare(doc.lines[at])], _inside(doc, node), 0)[0]
            if at == node.span.first and node.span.marker:
                line = _bare(doc.lines[at])[len(node.span.indent) + len(node.span.marker):]
            if closer:
                closer = None if closer.search(line) else closer
            elif not code and (found := _HTML_OPEN_RE.match(line)):
                shut = _HTML_CLOSERS[found.lastgroup]
                closer = None if shut.search(line, found.start() + 2) else shut
    return hidden


# --- what other apps read ------------------------------------------------------

# The walk keeps a model of how CommonMark reads blocks side by side, and a model
# can miss a case. So a CommonMark parser reads the file and the text a change
# would write, and every block the change left alone has to read as it did,
# whatever pass of ``shape`` wrote it. One written line for line, its lines
# copied as they were beside whatever now stands beside it, or so but for the
# mark of the list it joins, moved over by their indent, or written anew where
# a pass found it merged: the quotes and list items that hold each of its lines,
# and the leaf each line is in (a paragraph, a heading of its level, a fence and
# its info, code, HTML, a table, a rule, math) with the lines that leaf spans.
# One the walk wrote as a fence: the kind and the text of its leaves, and what
# holds it. How loose a list is, what it counts from and a mark the walk chose
# are spacing, not meaning, and are not asked.
#
# What a block the change made held, or a leaf it was part of, may let the
# kept lines go: the person changed that block. A ``- a`` deleted lets its
# lazy line be a paragraph of its own, and ``<!--`` retyped lets the list it
# hid be a list. Nothing else takes a new hold of a kept line, and no leaf
# gathers kept lines of two.

# markdown-it's tokens that hold lines, and those that let them go. A list is
# said by its items' markers.
_HOLDERS = frozenset({"blockquote_open", "list_item_open"})
_LET_GO = frozenset({"blockquote_close", "list_item_close"})
# The tokens that are a line's leaf.
_LEAVES = frozenset({"paragraph_open", "heading_open", "fence", "code_block", "html_block", "table_open", "hr",
                     "math_block"})
# A holder that opens on a line of a block the change made: known by that block.
_CHANGED = ("changed",)


def _math(state, start: int, end: int, silent: bool) -> bool:
    """markdown-it's rule for ``$$`` display math, as remark-math, which Gamma
    renders with, reads it, and Obsidian and VS Code's preview show it: from a
    line whose text opens with ``$$`` and holds no other, to the next line of
    as many dollars or more, alone, less than four columns in, or to the end of
    what holds it, breaking into a paragraph as a fence does. This parser closes
    it on any line that holds ``$$``: such a line with more on it is noted in
    ``env["tails"]``, as what follows it reads on as math. CommonMark alone
    reads ``text\\n$$`` as one paragraph."""
    if state.is_code_block(start):
        return False
    text = state.src[state.bMarks[start] + state.tShift[start]:state.eMarks[start]].strip(" \t")
    if not text.startswith("$$") or "$$" in text[2:]:
        return False
    if silent:
        return True
    marks, line = len(text) - len(text.lstrip("$")), start + 1
    while line < end:
        first, stop = state.bMarks[line] + state.tShift[line], state.eMarks[line]
        if first < stop and state.sCount[line] < state.blkIndent:
            break
        line += 1
        if "$$" in state.src[first:stop]:
            shut = state.src[first:stop].strip(" \t")
            if len(shut) >= marks and not shut.strip("$"):
                if state.sCount[line - 1] - state.blkIndent < CODE:
                    break
                continue
            state.env.setdefault("tails", []).append(line - 1)
    state.line = line
    token = state.push("math_block", "math", 0)
    token.block, token.map = True, [start, line]
    token.content = state.getLines(start, line, state.blkIndent, False)
    return True


@functools.cache
def _commonmark():
    """markdown-it-py's CommonMark with tables, the parser family of VS Code's
    preview, and ``$$`` math (``_math``), reading blocks only, as deep as a
    file here may nest. Imported on the first write that asks: a server that
    never writes a vault has no need of it."""
    from markdown_it import MarkdownIt
    md = MarkdownIt("commonmark", {"maxNesting": 3 * MAX_DEPTH}).enable("table").disable(["inline", "text_join"])
    md.block.ruler.after("fence", "math", _math, {"alt": ["paragraph", "reference", "blockquote", "list"]})
    return md


def _readings(lines: list[str], front: int, env=None) -> tuple[list, list, dict]:
    """What CommonMark reads each of ``lines`` as, the first ``front`` (front
    matter) standing blank: the quotes and list items holding it, each as its
    token, its marker and the line it opens on, and the leaf it is in, as
    (token, tag, info), its first line and its end; and each leaf's text, by
    its first line, in the parts the parse gives it before inline rules (a
    table's, its cells). Blank lines an open fence takes in at the end of what
    holds it are no part of it. One parse, and one pass over its tokens.
    ``env``: the parse's, which ``_math`` notes in."""
    count = len(lines)
    holders, leaves, texts, chain, outer, leaf = [()] * count, [None] * count, {}, (), [], None
    for token in _commonmark().parse("\n" * front + "".join(lines[front:]), env):
        if token.type in _HOLDERS:
            first, end = token.map[0], min(token.map[1], count)
            outer.append(chain)
            chain = (*chain, (token.type, token.markup, first))
            holders[first:end] = [chain] * (end - first)
        elif token.type in _LET_GO:
            chain = outer.pop()
        elif token.type in _LEAVES:
            first = leaf = token.map[0]
            end = min(token.map[1], count)
            while end > first + 1 and _blank(lines[end - 1]):
                end -= 1
            leaves[first:end] = [((token.type, token.tag, token.info.strip()), first, end)] * (end - first)
            texts[first] = [token.content] if token.content else []
        elif token.type == "inline" and leaf is not None:
            texts[leaf].append(token.content)
    return holders, leaves, texts


def _read_once(doc: Doc) -> tuple:
    """``_readings`` of ``doc``'s file, made on the first ask: every pass of
    ``shape`` and its check read the same file."""
    if doc.reading is None:
        doc.reading = _readings(doc.lines, doc.front.last if doc.front else 0)
    return doc.reading


def _read_as_code(doc: Doc, node: Node) -> bool:
    """Whether CommonMark reads ``node``, a block this parser reads as text or
    a list item, as code by indentation in ``doc``'s file: every line of its
    text in a code block (``_readings``). Such lines stand four columns in at
    least, which is asked first, so that a block that stands anywhere else
    loads no CommonMark parser."""
    span = node.span
    rows = [k for k in range(span.first, node.text_end) if not _blank(doc.lines[k])]
    if span.kind in ("fence", "math", "verbatim") or any(text_column(doc.lines[k]) < CODE for k in rows):
        return False
    leaves = _read_once(doc)[1]
    return all(leaves[k] is not None and leaves[k][0][0] == "code_block" for k in rows)


def _owners(starts: dict, count: int) -> list:
    """The id of the block each of ``count`` lines is in, by ``starts``: ``{line:
    id}`` of the line each block starts on, which it runs from to the next."""
    owners, at = [None] * count, sorted(starts)
    for line, end in zip(at, [*at[1:], count]):
        owners[line:end] = [starts[line]] * (end - line)
    return owners


def _holders_of(chain: tuple, kept_as, owners: list, remarked: set) -> tuple:
    """A line's holders in terms the file and the text share: one opening on a
    line of a block left alone by the line of the file it is (``kept_as``),
    with its mark, unless the walk chose that (``remarked``), and those opening
    on a changed block's lines by that block, one for all of its, whose
    markers are the person's to change."""
    out = []
    for kind, mark, at in chain:
        line = kept_as(at)
        holder = (kind, "" if line in remarked else mark, line) if line is not None else (_CHANGED, owners[at])
        if not (holder[0] is _CHANGED and out and out[-1] == holder):
            out.append(holder)
    return tuple(out)


def _let_go(was: tuple, now: tuple, released: range) -> bool:
    """Whether a kept line held by ``now`` is held as by ``was`` (``_holders_of``):
    but for changed blocks' holders, which the change may let go, and for
    holders now opening on kept lines ``released``, the lines of a leaf that
    held a changed block's, which reads now as those lines do alone."""
    k = 0
    for holder in now:
        while k < len(was) and was[k] != holder and was[k][0] is _CHANGED:
            k += 1
        if k < len(was) and was[k] == holder:
            k += 1
        elif holder[0] is _CHANGED or holder[2] not in released:
            return False
    return all(holder[0] is _CHANGED for holder in was[k:])


@dataclass(slots=True)
class _Sides:
    """The file and the text a change would write, as the check reads them:
    their lines, their ``_readings``, the block each line is in, and the lines
    of the blocks the change left alone. A line of one written line for line is
    known by where it stood (``origin``) and went (``image``); the lines of one
    written as a fence all stand for its first (``stood``)."""
    lines: list
    out: list
    file: tuple
    text: tuple
    owners: list
    owners_now: list
    image: list
    origin: list
    left: list            # a line of the file is a block's the change left alone
    stood: list           # a line written of such a block -> the line of the file it stands for
    remarked: set         # the first lines of those whose mark the walk chose

    def was_held(self, line: int) -> tuple:
        """The holders of a line of the file (``_holders_of``)."""
        return _holders_of(self.file[0][line], lambda at: at if self.left[at] else None, self.owners, self.remarked)

    def now_held(self, line: int) -> tuple:
        """The holders of a line written."""
        return _holders_of(self.text[0][line], self.stood.__getitem__, self.owners_now, self.remarked)


def _reads_otherwise(doc: Doc, ids, walk: _Walk) -> str:
    """Why a block the change left alone reads otherwise in CommonMark in the
    text ``walk`` wrote than in ``doc``'s file, "" when none does: ``MATH_TAIL``
    where a formula the change wrote has text after its closing ``$$``, which
    takes what follows into it (``_math``), else ``MEANING``. A block edited,
    written anew or under one that moved reads as the person asked, and is not
    asked. One whose lines are copied as they were, but for the mark of the
    list it joins, or moved over by their indent, or written anew as it was on
    a pass of ``shape``, is asked line by line (``_kept_otherwise``); one
    written as a fence, by its leaves and what holds it (``_fenced_otherwise``)."""
    image, origin, fenced = [None] * len(doc.lines), [None] * len(walk.out), []
    for at, node in walk.copies:
        first, last = node.span.first, node.span.last
        skip = int(first in walk.remarked)               # its first line has the mark of the list it joins
        if walk.out[at + skip:at + last - first] == doc.lines[first + skip:last]:
            image[first:last] = range(at, at + last - first)
            origin[at:at + last - first] = range(first, last)
    for at, count, node in walk.redressed:
        first, last = node.span.first, node.span.last
        if count == last - first:
            image[first:last] = range(at, at + count)
            origin[at:at + count] = range(first, last)
        else:
            fenced.append((at, count, node))
    if all(line is None for line in image) and not fenced:
        return ""
    left, stood = [line is not None for line in image], list(origin)
    for at, count, node in fenced:
        left[node.span.first:node.span.last] = [True] * (node.span.last - node.span.first)
        stood[at:at + count] = [node.span.first] * count
    front, env = doc.front.last if doc.front else 0, {}
    owners = _owners({node.span.first: ids[node.index] for node in doc.flat}, len(doc.lines))
    sides = _Sides(doc.lines, walk.out, _read_once(doc), _readings(walk.out, front, env), owners,
                   _owners(walk.starts, len(walk.out)), image, origin, left, stood, walk.remarked)
    if not (_kept_otherwise(sides) or _fenced_otherwise(sides, fenced)):
        return ""
    alone = {ids[node.index] for *_, node in (*walk.copies, *walk.redressed)}
    return MATH_TAIL if any(sides.owners_now[line] not in alone for line in env.get("tails", ())) else MEANING


def _kept_otherwise(sides: _Sides) -> bool:
    """Whether a block written line for line reads otherwise in the text than
    in the file. A leaf of the file that holds such lines alone is written as
    the same leaf, of those lines; one that held a changed block's may let them
    go (``_let_go``); no leaf written gathers lines of two."""
    image, origin, lines, out = sides.image, sides.origin, sides.lines, sides.out
    leaves, leaves_now = sides.file[1], sides.text[1]
    want, source = {}, {}       # the file's leaf -> the leaf it is written as, or None; a leaf written -> the file's
    for line, now in enumerate(image):
        if now is None or _blank(lines[line]):
            continue
        leaf, leaf_now, released = leaves[line], leaves_now[now], range(0)
        if leaf is None and leaf_now is not None:
            return True
        if leaf is not None:
            sig, start, end = leaf
            if start not in want:       # met first on its first line, if it holds kept lines alone
                want[start] = None
                if all(image[k] is not None or _blank(lines[k]) for k in range(start, end)):
                    want[start] = (sig, now, now + end - start)
                    if leaf_now != want[start] or any(origin[k] is None and not _blank(out[k])
                                                      for k in range(now, now + end - start)):
                        return True
            if want[start] is None:
                released = range(start, end)
            elif leaf_now != want[start] or now - leaf_now[1] != line - start:
                return True
            if leaf_now is not None and source.setdefault(leaf_now[1], start) != start:
                return True
        if not _let_go(sides.was_held(line), sides.now_held(now), released):
            return True
    return False


def _fenced_otherwise(sides: _Sides, fenced: list) -> bool:
    """Whether a block the walk wrote as a fence, ``(line written, how many,
    node)``, reads otherwise in the text than in the file: as the leaves its
    lines are in, by their kind and their text with no indent, code by
    indentation and a fence one kind, and as what holds its first line. A leaf
    of the file that held a changed block's lines may let it go, as it may a
    kept line."""
    leaves = sides.file[1]
    for at, count, node in fenced:
        rows = range(node.span.first, node.span.last)
        held = {leaves[k] for k in rows} - {None}
        if not all(sides.left[k] or _blank(sides.lines[k]) for _, first, end in held for k in range(first, end)):
            continue
        if _leaves_over(sides.lines, sides.file, rows) != _leaves_over(sides.out, sides.text, range(at, at + count)):
            return True
        if not _let_go(sides.was_held(rows[0]), sides.now_held(at), range(0)):
            return True
    return False


def _leaves_over(lines: list[str], reading: tuple, rows: range) -> list:
    """The leaves the ``rows`` of ``lines`` are in by ``reading`` (``_readings``),
    in order and each once, as their kind and their text with no indent; None
    for a line in none. Code by indentation is a fence of no info."""
    _, leaves, texts = reading
    out, last = [], False
    for k in rows:
        leaf = leaves[k]
        if _blank(lines[k]) or leaf is last:
            continue
        last = leaf
        if leaf is None:
            out.append(None)
            continue
        (kind, tag, info), first, _ = leaf
        body = "\n".join(line.lstrip(" \t") for part in texts[first] for line in part.split("\n"))
        out.append(("fence" if kind == "code_block" else kind, tag, info, body))
    return out


def shape(doc: Doc, ids, tree: list) -> tuple[list, str]:
    """What ``tree`` becomes in ``doc``'s file: ``(shaped, text)``, where
    ``shaped`` is what ``text`` reads as. A block keeps its id on the line it
    starts on; a block a split made has id None. By law 1 writing ``shaped``
    over ``text`` changes nothing, so a last pass only checks that.

    A block's old dress can read as the block before it: no rule of the walk's
    foresaw that, and ``_fitted`` knows the common cases. Such a block is written
    as new on the next pass, at most ``MAX_PASSES`` of them. A block that landed
    in code cannot be helped: ``Unwritable``, rather than lose an id, and so is
    an anchor code left open takes in. Nor can a block HTML left open takes in
    (``<!--``, ``<pre>``), which this parser does not see, unless the file held
    that block in it already. Nor, last, a text in which a block the change
    left alone would read otherwise to CommonMark (``_reads_otherwise``):
    ``MEANING``, as the walk's model of CommonMark has missed a case, or
    ``MATH_TAIL``, as a formula Gamma changed takes what follows. A file
    past the limits has no shape, and a text that would be is refused at once,
    for that reason."""
    if doc.problem:
        raise Unwritable(doc.problem)
    fresh = set()
    for _ in range(MAX_PASSES):
        walk = _walked(doc, ids, tree, fresh)
        text, starts = walk.finish()
        read = doc if text == doc.raw else parse(text)
        if read.problem:
            raise Unwritable(read.problem)
        held = [starts.get(node.span.first) for node in read.flat]
        kept = set(held)
        lost = {line: block_id for line, block_id in starts.items() if block_id not in kept}
        if not lost:
            break
        if any(_in_code(read, line) for line in lost):
            raise Unwritable(OPEN_BLOCK)
        fresh |= set(lost.values())
    else:
        raise Unwritable("the file would not settle")
    anchors = {node["anchor"].lower() for node in flatten(tree) if node.get("anchor")}
    if anchors - {node.anchor.lower() for node in read.flat} and any(node.span.open for node in read.flat):
        raise Unwritable(OPEN_BLOCK)
    hidden = _hidden(doc)
    was = {ids[i] for i in hidden}
    if any(held[i] is None or held[i] not in was for i in (hidden if read is doc else _hidden(read))):
        raise Unwritable(OPEN_BLOCK)
    if read is not doc and (why := _reads_otherwise(doc, ids, walk)):
        raise Unwritable(why)
    places = list(range(len(read.flat)))
    if read is not doc and write(read, places, tree_of(read, places))[0] != text:      # (law 1 is the file's own)
        raise Unwritable("the file would not settle")
    return tree_of(read, held), text


# --- matching a changed file to its base ---------------------------------------

def _rising(pairs: list) -> list:
    """The longest run of ``pairs`` (sorted by their first) rising in their
    second: the matches that kept their order."""
    tops, runs = [], {}
    for pair in pairs:
        lo, hi = 0, len(tops)
        while lo < hi:
            mid = (lo + hi) // 2
            lo, hi = (mid + 1, hi) if tops[mid] < pair[1] else (lo, mid)
        tops[lo:lo + 1] = [pair[1]]
        runs[lo] = (pair, runs.get(lo - 1))
    run, out = runs.get(len(tops) - 1), []
    while run:
        out.append(run[0])
        run = run[1]
    return out[::-1]


def _pair_leftovers(olds: list, news: list, old: list, new: list, pairs: dict):
    """Between two matches, what is left pairs up in order as an edit, one class
    only and similar enough: each new block with the old one it reads most
    like. A new paragraph after a removed callout is no edit of that callout,
    and a paragraph edited after one deleted is not the deleted one's."""
    start = 0
    for n in news:
        text = new[n]["content"]
        cls = block_class(text)
        window = [k for k in range(start, min(start + PAIR_WINDOW, len(olds))) if block_class(old[olds[k]]["content"]) == cls]
        likeness = {k: _likeness(old[olds[k]]["content"], text) for k in window}
        best = max(window, key=lambda k: (likeness[k], -k), default=None)    # the likest, the first of equals
        if best is not None and likeness[best] >= MIN_SIMILAR:
            pairs[n] = olds[best]
            start = best + 1


def _likeness(a: str, b: str) -> float:
    """How like ``a`` ``b`` is, 0 to 1, to be an edit of it: the letters they
    share, at either end and aligned by ``difflib`` between, against all theirs.
    Past ``ALIGN`` letters between, the pairs of letters there that they share,
    counted, stand for those aligned."""
    if not a and not b:
        return 1.0
    head, tail = _shared_ends(a, b)
    x, y = a[head:len(a) - tail], b[head:len(b) - tail]
    if len(x) + len(y) <= ALIGN:
        same = sum(block.size for block in difflib.SequenceMatcher(None, x, y, autojunk=False).get_matching_blocks())
    else:
        same = sum((Counter(zip(x, x[1:])) & Counter(zip(y, y[1:]))).values())
    return 2 * (head + tail + same) / (len(a) + len(b))


def match(old: list, new: list, gone=()) -> dict:
    """``{new_index: old_index}`` between two lists of blocks in document order
    (``flatten``). ``gone`` is the base's blocks the file dropped, and an index
    past ``old`` is ``gone``'s: an id coming back. By anchor first, in ``old``
    then in ``gone``, the first of a copied anchor taking it; by identical text
    in order, what the two share at either end taken first (``_opcodes``), so
    that a file of blocks alike is no quadratic search; then by text in
    ``gone``, which must not come earlier or a block
    dropped from a pair of equals would take its twin's place every round; then
    the leftovers between matches, pairwise. The rest are inserts and deletes."""
    pairs, taken = {}, set()
    by_anchor = {}
    for i, block in enumerate((*old, *gone)):
        if anchor := (block.get("anchor") or "").lower():
            by_anchor.setdefault(anchor, i)
    for n, block in enumerate(new):
        i = by_anchor.get((block.get("anchor") or "").lower())
        if i is not None and i not in taken:
            pairs[n] = i
            taken.add(i)

    olds = [i for i in range(len(old)) if i not in taken]
    news = [n for n in range(len(new)) if n not in pairs]
    texts = [_normal(old[i]["content"]) for i in olds], [_normal(new[n]["content"]) for n in news]
    for tag, a, a_end, b, _ in _opcodes(*texts):
        for k in range(a_end - a if tag == "equal" else 0):
            pairs[news[b + k]] = olds[a + k]
            taken.add(olds[a + k])

    by_hash = {}
    for j, block in enumerate(gone):
        if len(old) + j not in taken:
            by_hash.setdefault(block.get("hash"), []).append(len(old) + j)
    for n in range(len(new)):
        hits = by_hash.get(content_hash(new[n]["content"])) if n not in pairs and by_hash else None
        if hits:
            pairs[n] = hits.pop(0)

    fixed = _rising(sorted((n, i) for n, i in pairs.items() if i < len(old)))
    low_new, low_old = 0, 0
    for n, i in [*fixed, (len(new), len(old))]:
        _pair_leftovers([k for k in range(low_old, i) if k not in taken],
                        [k for k in range(low_new, n) if k not in pairs], old, new, pairs)
        low_new, low_old = n + 1, i + 1
    return pairs


# --- a page's blocks --------------------------------------------------------------

def _has_span(block: dict, kind) -> bool:
    """Whether a block is written to the file: a highlight, and a note with
    text, which a line of no-break spaces is in a file (``_blank``). Ink,
    boxes, pictures on a page, sheets and recordings live in Gamma,
    and so does a PDF's link region: a rectangle and its target, whose label
    is no text of the notes."""
    return kind == "highlight" or (kind == "note" and not _blank(block["content"]))


def project(snapshot: dict, kind_of) -> list:
    """What of a page reaches its file, as a tree: ``snapshot`` as
    ``sync_tree``'s, ``kind_of(block)`` as ``blocks_store.block_kind`` tells it.
    A block with no span goes, and its children take its place, so comparing,
    shaping and matching all see a note under a rectangle where the file has
    it. The page is the block whose parent is not in ``snapshot``. Read
    without recursion: a page nested past ``MAX_DEPTH`` is ``shape``'s to
    refuse."""
    kids = {}
    for block_id, block in snapshot.items():
        kids.setdefault(block["parent"], []).append(block_id)
    for ids in kids.values():
        ids.sort(key=lambda i: (snapshot[i]["position"], i))
    tree = []
    pending = [(block_id, tree) for block_id in reversed(kids.get(next(
        i for i, block in snapshot.items() if block["parent"] not in snapshot), ()))]
    while pending:
        block_id, holder = pending.pop()
        block = snapshot[block_id]
        if _has_span(block, kind_of(block)):
            holder.append({"id": block_id, "content": block["content"], "children": []})
            holder = holder[-1]["children"]
        pending += [(child, holder) for child in reversed(kids.get(block_id, ()))]
    return tree


# --- front matter -------------------------------------------------------------

def _front_value(value):
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else str(value)


def _front_entry(key: str, value) -> list[str]:
    """A new key's lines."""
    return yaml_list(key, value) if isinstance(value, list) else [f"{key}: {yaml_text(value)}"]


def _front_rewritten(lines: list[str], value) -> list[str]:
    """A key's ``lines`` with its new value, in the form they have."""
    name, _, rest = _bare(lines[0]).partition(":")
    if not isinstance(value, list):
        return [f"{name}: {yaml_text(value)}"]
    if rest.strip().startswith("["):
        return [f"{name}: [{', '.join(yaml_text(v) for v in value)}]"]
    dash = next((found.group() for line in lines[1:] if (found := _FRONT_ITEM_RE.match(line))), "  - ")
    return [f"{name}:", *(f"{dash}{yaml_text(v)}" for v in value)]


def patch_front(doc: Doc, fields: dict) -> str:
    """The file with its front matter's ``fields`` set (None takes a key out).
    Every other key stays byte for byte, and a key holding its value already.
    A known key is rewritten in the form it has, a flow or a block list, every
    copy of it, a new key goes last, a new front matter first, and one left
    with nothing in it goes, with the blank line after it, never leaving
    ``---`` ``---``. A key's value runs over the indented, list and blank lines
    below it, but the blank lines it ends on."""
    eol, front = doc.eol, doc.front
    wanted = {key.lower(): (key, _front_value(value)) for key, value in fields.items() if value is not None}
    dropped = {key.lower() for key, value in fields.items() if value is None}
    if front is None:
        entries = [line for key, value in wanted.values() for line in _front_entry(key, value)]
        if not entries:
            return doc.raw
        return doc.bom + "".join(f"{line}{eol}" for line in ("---", *entries, "---")) + doc.raw[len(doc.bom):]

    body = doc.lines[1:front.last - 1]
    kept, i, done = [], 0, set()
    while i < len(body):
        found = _FRONT_KEY_RE.match(body[i])
        end = i + 1
        if found:
            while end < len(body) and (body[end][:1].isspace() or _FRONT_ITEM_RE.match(body[end])):
                end += 1
            while end > i + 1 and not body[end - 1].strip():
                end -= 1
        key = found[1].strip().lower() if found else None
        if key in wanted and key not in dropped:
            if front.fields.get(key) == wanted[key][1]:
                kept += body[i:end]
            else:
                line_end = _ending(body[i]) or eol
                kept += [line + line_end for line in _front_rewritten(body[i:end], wanted[key][1])]
            done.add(key)
        elif key not in dropped:
            kept += body[i:end]
        i = end
    kept += [f"{line}{eol}" for low, (key, value) in wanted.items() if low not in done
             for line in _front_entry(key, value)]

    rest = doc.lines[front.last:]
    if not any(line.strip() for line in kept):
        return doc.bom + "".join(rest[1:] if rest and not rest[0].strip() else rest)
    return doc.bom + "".join([doc.lines[0], *kept, doc.lines[front.last - 1], *rest])
