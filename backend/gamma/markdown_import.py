"""Convert a plain Markdown document into Gamma's nested note blocks.

Used by ``POST /api/import/markdown`` (uploading a .md file, alone or inside a
folder upload, turns it into a note page) and by the zip importer
(``markdown_zip_import.py``). The parser is deliberately shallow: block
contents keep their raw markdown — the block editor renders headings, math,
callouts etc. live — so all that matters here is how the document splits into
blocks and how they nest:

- headings nest by level (## under #, …) and keep their ``#`` markup;
- list items nest by indentation under the nearest heading and keep their
  marker, the way a list block is written in a note: a bullet is ``- `` (from
  ``*`` and ``+`` too), a number stays, and a task is ``- [ ]`` / ``- [x]``.
  A bullet that only wraps a block (``- # H``, ``- ```py``, ``- - x``: Gamma's
  vault writes those) gives way to the block;
- Gamma's readable export writes every block as a bullet, so there a ``- ``
  says "a block" and no more. With ``outline`` every one is dropped, which
  gives such a file its blocks back as they were; ``readable_outline`` knows
  the file by its shape;
- lines indented under a list item continue that item: text directly below
  it (or, after a blank line, text aligned with the item's own text column)
  joins the item's content — which is exactly how Gamma's Markdown export
  writes a multi-line block — while text indented deeper after a blank line
  becomes a child block (Notion's toggles export that way), whose next
  lines stay in it (a quote or callout in a toggle is one child);
- everything else groups into one block per paragraph (consecutive non-blank
  lines — which also keeps quote runs and tables together);
- fenced code blocks and multi-line ``$$`` display math are kept whole,
  delimiters included (a math row like ``- x + y &= 3 \\`` must not be
  mistaken for a list item), also when they sit inside a list item. Below an
  item that keeps its marker they are child blocks, since the editor only
  sees them at column 0. A line that closes the fence it opens is inline
  code and opens nothing (``- ```x``` y`` is a bullet), a fence closes only on
  a line of nothing but its mark, at least as long as what opened it and less
  than four columns past it (in a list item at any indent), and
  ``$$`` opens only at the start of a line's text, without its pair on that
  line (``- costs $$ and more`` is a bullet).
"""

import json
import re
from dataclasses import dataclass

from .note_markup import obsidian_image_sizes

# blocks per document. A 5 MB file of real notes holds about 100,000; only
# one of nothing but very short lines gets past this (a 5 MB file of "- x"
# lines is 1.3 million blocks: a minute and a gigabyte to write, a page no
# editor opens). Such a document is refused, as one past the size cap is.
MAX_BLOCKS = 200_000
# one .md file (upload or zip member)
MAX_MARKDOWN_BYTES = 5 * 1024 * 1024


class TooManyBlocks(ValueError):
    """The document parses into more than MAX_BLOCKS blocks."""

    def __init__(self):
        super().__init__(f"more than {MAX_BLOCKS:,} blocks")


_FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|$)", re.S)
# What the readable export writes before a note's blocks: the H1, then BibTeX
# if the page has any.
_READABLE_HEAD_RE = re.compile(r"\s*#[ \t]+\S[^\n]*(?:\n\s*```bibtex[ \t]*\n.*?\n```[ \t]*(?=\n|$))?", re.S)
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_LIST_RE = re.compile(r"^([ \t]*)([-*+]|\d{1,4}[.)])\s+(.*)$")
# A fence's line, unless it closes what it opens: ```` ```x``` y ```` is inline
# code. Its first three marks are all the line is searched for, and the rest of
# the run is taken for good: a whole run looked for along a line is cubic.
_FENCE_LINE_RE = re.compile(r"^[ \t]*(`{3}|~{3})[`~]*+(?!.*\1)")
_FENCE_MARKS_RE = re.compile(r"\s*(`+|~+)")
_NUMBER_MARKER_RE = re.compile(r"^\d{1,4}[.)]\s+")
# The markers and task box before a line's text: wrapped bullets stack them.
_ITEM_BOX_RE = re.compile(r"^(?:(?:[-*+]|\d{1,4}[.)]) +)*(?:\[[ xX]\]\s*)?")
# A list block with nothing in it yet: its space is all that makes it one. A
# to-do's box needs one too (the editor's ``MARKER_RE``).
EMPTY_ITEM_RE = re.compile(r"[-*+] (?:\[[ xX]\] )?|\d{1,4}[.)] ")


def parse_frontmatter(text: str):
    """Return ``(fields, body)`` — YAML front matter is dropped and its
    top-level ``key: value`` lines are returned as a dict (keys lower-cased,
    a quoted value read as ``_fm_scalar`` does). A value is a string, or a list of strings for the two
    YAML list forms Obsidian's properties use — a flow list ``[a, b]`` and a
    block list (``key:`` followed by ``- item`` lines). Nested mappings and
    multi-line scalars are ignored."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fields = {}
    pending = None    # key whose block-list items may follow
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = _FM_ITEM_RE.match(line)
        if item and pending is not None:
            fields[pending].append(_fm_scalar(item.group(1) or ""))
            continue
        if line[:1].isspace():
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            pending = None
            continue
        key, value = key.strip().lower(), value.strip()
        pending = None
        if not value:
            fields[key] = []
            pending = key
        elif value.startswith("[") and value.endswith("]"):
            fields[key] = [_fm_scalar(v) for v in _FM_FLOW_ITEM_RE.findall(value[1:-1]) if v.strip()]
        else:
            fields[key] = _fm_scalar(value)
    return fields, text[match.end():]


# ``- item`` (indented or not) under a ``key:`` line
_FM_ITEM_RE = re.compile(r"^\s*-(?:\s+(.*))?$")
# One item of a ``[a, b]`` list: a quoted one holds its commas.
_FM_FLOW_ITEM_RE = re.compile(r"""\s*(?:"(?:[^"\\]|\\.)*+"|'(?:[^']|'')*+'|[^,]+)""")


def _fm_scalar(value: str) -> str:
    """A value as YAML reads it, which only a quoted one asks for: its one pair
    of quotes goes, and a quote that is part of the text stays (``'Tis``,
    ``Say "hi"``). A double-quoted value is JSON, as the vault writes it
    (``_yaml_text``), unless that is no JSON or gives a control character or a
    lone surrogate, which SQLite cannot take: ``"C:\\foo"`` by hand is a path,
    not a form feed."""
    value = value.strip()
    if len(value) < 2 or value[0] != value[-1] or value[0] not in "'\"":
        return value
    inner = value[1:-1].strip()
    if value[0] == "'":
        return inner.replace("''", "'")
    try:
        text = json.loads(value)
    except ValueError:
        return inner
    return inner if any(c < " " or "\ud800" <= c <= "\udfff" for c in text) else text


def fm_text(fields: dict, key: str) -> str:
    """A front-matter value as text (a list reads as its first item)."""
    value = fields.get(key)
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def fm_list(fields: dict, key: str) -> list:
    """A front-matter value as a list of strings — a list as is, a string
    split on commas (``tags: a, b``)."""
    value = fields.get(key)
    if isinstance(value, list):
        items = value
    else:
        items = (value or "").split(",")
    return [v.strip() for v in items if v and v.strip()]


def _fence_marks(line: str) -> str:
    """The whole run of marks a fence's ``line`` opens with."""
    return _FENCE_MARKS_RE.match(line).group(1)


def shuts_fence(line: str, marks: str) -> bool:
    """Whether ``line`` closes the fence ``marks`` opened: all that mark, and
    at least as long, so inside ```` a ``` line is code, as in ``scanFences``.
    Its indent counts for nothing here, for lines a list item has dedented
    already: ``shuts_code`` is the rule for a fence on the page."""
    shut = line.strip()
    return bool(shut) and set(shut) == {marks[0]} and len(shut) >= len(marks)


def text_column(line: str) -> int:
    """The column a line's text starts at (tabs count 4)."""
    expanded = line.replace("\t", "    ")
    return len(expanded) - len(expanded.lstrip(" "))


def shuts_code(line: str, marks: str, col: int) -> bool:
    """``shuts_fence`` for a fence at column ``col``: four columns past it a
    line is code, as in CommonMark (a docstring holding a fence)."""
    return text_column(line) < col + 4 and shuts_fence(line, marks)


def _opens_math(text: str) -> bool:
    """Whether a stripped line opens multi-line ``$$`` math: only the start of
    a line's text can, as in remark-math, and a line with the pair on it is
    whole."""
    return text.startswith("$$") and "$$" not in text[2:]


def _item_state(state, line: str):
    """A list item's ``state`` past its next ``line``. A fence's marks and
    ``"math"`` are an unclosed block, which takes the lines below whatever they
    look like, and the fence's close gives back the ``"pair"`` written before
    its marks. ``"pair"`` is a ``$$`` left over mid-line: it takes nothing, but the block is one text to the editor (``scanMathSpans``),
    where the item's next ``$$`` closes it, so that one opens nothing
    (``- is $$\n  x\n  $$``), unless it is a formula of its own
    (``_closer_below``). Otherwise None."""
    if state and state[-1] in "`~":
        before, _, marks = state.rpartition(" ")
        return (before or None) if shuts_fence(line, marks) else state
    if state != "math" and _FENCE_LINE_RE.match(line):
        return f"{state or ''} {_fence_marks(line)}".lstrip()
    if line.count("$$") % 2 == 0:
        return state
    if state:
        return None
    return "math" if _opens_math(_ITEM_BOX_RE.sub("", line.strip(), count=1)) else "pair"


@dataclass(slots=True)
class _Item:
    """A list item of the current run."""
    indent: int
    node: dict
    column: int          # its text column
    strip: int           # what its next lines lose
    state: str | None    # _item_state
    # Its content so far. A dict's string grown by += copies itself every time.
    parts: list[str]

    def add_line(self, line: str, sep: str = "\n"):
        self.parts.append(sep + line)
        self.state = _item_state(self.state, line)


def _closer_below(lines: list[str], start: int, indent: int) -> bool:
    """Whether the next ``$$`` in ``lines`` from ``start`` stands alone on its
    line, which is remark-math's close, before a line at ``indent`` or left of
    it leaves the item. A search ends by that ``$$``, the first line the next
    search can start on, so together they read each line once."""
    for i in range(start, len(lines)):
        line = lines[i]
        expanded = line.replace("\t", "    ")
        if line.strip() and len(expanded) - len(expanded.lstrip(" ")) <= indent:
            return False
        if line.count("$$") % 2:
            return line.strip() == "$$"
    return False


def _dedent(raw: str, col: int) -> str:
    """Drop up to ``col`` columns of leading whitespace (tabs count 4)."""
    line = raw.replace("\t", "    ").rstrip()
    lead = len(line) - len(line.lstrip(" "))
    return line[min(lead, col):]


def _dedent_code(raw: str, col: int) -> str:
    """A fenced line without its fence's indent ``col``: the rest of the line,
    tabs included, is the code's."""
    i = width = 0
    while i < len(raw) and width < col and raw[i] in " \t":
        width += 4 if raw[i] == "\t" else 1
        i += 1
    return raw[i:].rstrip()


# A task item's box after its bullet: ``[ ]`` / ``[x]``, then the text (Notion
# writes two spaces after the box), or nothing.
_TASK_RE = re.compile(r"^\[([ xX])\](?:\s+(.*))?$")
# A bullet's body that is a block of its own, the bullet only a wrapper: Gamma's
# vault writes every nested block ``- <content>``, so ``- - x`` is the list block
# ``- x``, ``- # H`` a heading, ``- ```py`` a fence. The pipe, ``$$`` and ``>``
# arms take the whole construct: ``- |x| is the norm``, ``- $$x$$ is the
# energy`` and ``- >5 is big`` are bullets. ``- 2020. A year`` unwraps with
# ``- 1. x``, which the vault needs. Past these, a fence's own line wraps one
# (``_FENCE_LINE_RE``): ``- ```x``` y`` is a bullet.
_WRAPPED_RE = re.compile(r"^(?:[-*+] |\d{1,4}[.)] |#{1,6} |>(?: |$)|\|.*\|\s*$|\$\$(?:.*\$\$)?\s*$|---+$|\*\*\*+$)")


def _item_content(marker: str, body: str, outline: bool) -> tuple[str, bool]:
    """A list item's block content, and whether its next lines lose the text
    column (the block starts where the text did) or only the item's indent
    (they stay aligned under a marker the block keeps). A number stays and a
    task keeps its box. With ``outline`` a bullet's marker is a block
    separator, so it goes and every item's lines lose the text column.
    Otherwise a bullet stays as ``- ``, unless it only wraps a block
    (``_WRAPPED_RE``)."""
    if len(marker) > 1:
        return f"{marker} {body}", outline
    task = _TASK_RE.match(body)
    if task:
        return f"- [{task.group(1).lower()}] {task.group(2) or ''}", outline
    if outline or _WRAPPED_RE.match(body) or _FENCE_LINE_RE.match(body):
        return body, True
    return f"- {body}", False


def readable_outline(fields: dict, body: str) -> bool:
    """Whether a note has the one shape ``render_readable`` writes, every
    ``- `` of it a block and no list (``outline`` of ``md_to_blocks``): a
    title in the front matter ``fields``, then in ``body``, its H1 still in
    it, that H1, a BibTeX block if any, and only bullets at the start of a
    line. The H1 need not repeat the title: the export writes a title with
    quotes or brackets one way in each. A note of another app of this shape
    reads as what it is written as, a list of blocks, and a readable file
    edited out of this shape keeps its bullets: no text goes either way. A
    ``gamma_id`` says the folder sync wrote it, whose bullets are lists."""
    head = _READABLE_HEAD_RE.match(body)
    return bool(fm_text(fields, "title") and head) and "gamma_id" not in fields and all(
        not line[:1].strip() or line.startswith("- ") for line in body[head.end():].split("\n"))


def md_to_blocks(text: str, *, outline: bool = False, flat_headings: bool = False) -> list:
    """Parse markdown into a tree of ``{"content": str, "children": [...]}``.
    A bullet keeps its marker, as a list block does, and a fence or multi-line
    ``$$`` below one is a child block; ``outline`` reads every ``- `` as a
    block separator instead (Gamma's own outlines, see the module's
    docstring). ``flat_headings`` puts every heading at the top with nothing
    under it, as Notion's are. A Logseq image size outside code
    (``![alt](url){:width 300}``) becomes the one Gamma stores,
    ``![alt|300](url)``. Raises TooManyBlocks past MAX_BLOCKS blocks, where
    it stops reading."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = obsidian_image_sizes(text).split("\n")
    root = {"content": "", "children": []}
    headings = [(0, root)]   # (heading level, node) — nesting for everything
    lists: list[_Item] = []  # the current list run
    items: list[_Item] = []  # every list item, its content joined at the end
    para: list[str] = []     # pending paragraph lines
    para_target = root
    blank_before = True      # was the previous line blank?
    count = 0

    def flush_para():
        nonlocal para
        if para:
            add(para_target, "\n".join(para).strip())
        para = []

    def add(parent, content):
        nonlocal count
        count += 1
        if count > MAX_BLOCKS:
            raise TooManyBlocks()
        node = {"content": content, "children": []}
        parent["children"].append(node)
        return node

    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        indented = raw[:1] in (" ", "\t")

        # A fence / $$ block opened inside a list item swallows everything
        # (blank lines included) until it closes.
        if lists and lists[-1].state not in (None, "pair"):
            lists[-1].add_line(_dedent(raw, lists[-1].strip))
            blank_before = False
            i += 1
            continue

        if not stripped:
            flush_para()
            blank_before = True
            i += 1
            continue

        if _HEADING_RE.match(stripped) and not indented:
            flush_para()
            lists.clear()
            if flat_headings:
                add(root, stripped)
            else:
                level = len(stripped) - len(stripped.lstrip("#"))
                while len(headings) > 1 and headings[-1][0] >= level:
                    headings.pop()
                headings.append((level, add(headings[-1][1], stripped)))
            blank_before = False
            i += 1
            continue

        list_match = _LIST_RE.match(raw)
        if list_match:
            flush_para()
            indent = len(list_match.group(1).replace("\t", "    "))
            marker, body = list_match.group(2), list_match.group(3)
            body = body if EMPTY_ITEM_RE.fullmatch(body) else body.strip()
            while lists and lists[-1].indent >= indent:
                lists.pop()
            parent = lists[-1].node if lists else headings[-1][1]
            content, wrapped = _item_content(marker, body, outline)
            column = indent + len(marker) + 1
            state = _item_state(None, _NUMBER_MARKER_RE.sub("", content, count=1))
            lists.append(_Item(indent, add(parent, content), column, column if wrapped else indent, state, [content]))
            items.append(lists[-1])
            blank_before = False
            i += 1
            continue

        # Indented text belongs to the innermost list item whose bullet sits
        # left of it: directly below the item it continues the item's content
        # (a multi-line block — also when it opens a fence or $$ block); after
        # a blank line it does so only when aligned with the item's text,
        # deeper indentation becoming a child block instead (handled by the
        # branches below, which nest under the item). Unindented text ends
        # the list run. A block that keeps its marker is no place for a fence
        # or $$ block, which the editor only sees at column 0, so those are
        # its children, and what follows one stays after it. A $$ below one
        # left mid-line in the item (_item_state) is a formula if a $$ closes
        # it, in the item, or anywhere from column 0, which is out of the item.
        # Otherwise it closes that one and stays in the item, from any column.
        if indented:
            expanded = raw.replace("\t", "    ")
            indent = len(expanded) - len(expanded.lstrip(" "))
            while lists and lists[-1].indent >= indent:
                lists.pop()
        math_line = _opens_math(stripped)
        closes = False
        if math_line and lists and lists[-1].state == "pair":
            closes = not _closer_below(lines, i + 1, lists[-1].indent if indented else -1)
            if not closes:
                lists[-1].state = None
        if indented or closes:
            fence_line = _FENCE_LINE_RE.match(stripped)
            if (para and not blank_before and lists and para_target is lists[-1].node
                    and not fence_line and not stripped.startswith("$$")):
                para.append(stripped)
                i += 1
                continue
            opens = fence_line or (math_line and not closes)
            child = lists and lists[-1].strip < lists[-1].column and (opens or lists[-1].node["children"])
            if lists and not child and (closes or not blank_before or indent <= lists[-1].column):
                flush_para()
                lists[-1].add_line(_dedent(raw, lists[-1].strip), "\n\n" if blank_before else "\n")
                blank_before = False
                i += 1
                continue
        else:
            lists.clear()

        if _FENCE_LINE_RE.match(stripped):
            flush_para()
            marks = _fence_marks(stripped)
            col = text_column(raw)
            buf = [_dedent_code(raw, col)]
            i += 1
            while i < len(lines):
                buf.append(_dedent_code(lines[i], col))
                if shuts_code(lines[i], marks, col):
                    i += 1
                    break
                i += 1
            add(lists[-1].node if lists else headings[-1][1], "\n".join(buf))
            blank_before = False
            continue

        # Multi-line $$ display math is kept whole like a fence — its rows
        # would otherwise hit the list rule (`- x + y &= 3 \\`, `1. & ...`)
        # and shatter the formula across blocks.
        if math_line and not closes:
            flush_para()
            buf = [stripped]
            i += 1
            while i < len(lines):
                buf.append(lines[i].strip())
                if "$$" in lines[i]:
                    i += 1
                    break
                i += 1
            add(lists[-1].node if lists else headings[-1][1], "\n".join(buf))
            blank_before = False
            continue

        # Plain text: a paragraph under the current heading, or a child block
        # of the list item it is indented under.
        target = lists[-1].node if lists else headings[-1][1]
        if para and para_target is not target:
            flush_para()
        para_target = target
        para.append(stripped)
        blank_before = False
        i += 1

    flush_para()
    for item in items:
        item.node["content"] = "".join(item.parts)
    return root["children"]
