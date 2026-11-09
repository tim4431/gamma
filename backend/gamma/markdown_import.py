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

``md_to_blocks(..., spans=True)`` is the same parser for ``md_lens``, which
writes a file back by patching its lines: every block also says which lines it
came from (``Span``), and a few rules change so that those lines are one run
(``md_to_blocks`` lists them).
"""

import json
import re
from dataclasses import dataclass

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
# A heading's line as CommonMark reads one, ``#`` alone an empty heading.
_ATX_RE = re.compile(r"#{1,6}(?:[ \t]|$)")
_LIST_RE = re.compile(r"^([ \t]*)([-*+]|\d{1,4}[.)])\s+(.*)$")
# The same with ``spans``, where only a space or a tab ends a heading's marks
# or a list marker, as in CommonMark and Gamma's editor: ``#\xa0h`` and
# ``-\xa0x`` are paragraphs.
_HEADING_SPAN_RE = re.compile(r"^(#{1,6})[ \t]+[^ \t]")
_LIST_SPAN_RE = re.compile(r"^([ \t]*)([-*+]|\d{1,4}[.)])[ \t]+(.*)$")
# A fence's line, unless it closes what it opens: ```` ```x``` y ```` is inline
# code. Its first three marks are all the line is searched for, and the rest of
# the run is taken for good: a whole run looked for along a line is cubic.
_FENCE_LINE_RE = re.compile(r"^[ \t]*(`{3}|~{3})[`~]*+(?!.*\1)")
_FENCE_MARKS_RE = re.compile(r"\s*(`+|~+)")
_NUMBER_MARKER_RE = re.compile(r"^\d{1,4}[.)]\s+")
# The markers and task box before a line's text: wrapped bullets stack them.
# With ``spans`` only spaces and tabs after the box go with it.
_ITEM_BOX_RE = re.compile(r"^(?:(?:[-*+]|\d{1,4}[.)]) +)*(?:\[[ xX]\]\s*)?")
_ITEM_BOX_SPAN_RE = re.compile(r"^(?:(?:[-*+]|\d{1,4}[.)]) +)*(?:\[[ xX]\][ \t]*)?")
# A list block with nothing in it yet: its space is all that makes it one. A
# to-do's box needs one too (the editor's ``MARKER_RE``).
EMPTY_ITEM_RE = re.compile(r"[-*+] (?:\[[ xX]\] )?|\d{1,4}[.)] ")
# A block that is a list item as it stands (the marker it keeps as content).
LIST_BLOCK_RE = re.compile(r"[-*+] |\d{1,4}[.)] ")
# The rule under a table's head row.
_TABLE_RULE_RE = re.compile(r"\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*")
# A thematic break, which CommonMark reads before a list item (``* * *``).
_RULE_LINE_RE = re.compile(r"[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*")
# A footnote's definition, which holds the paragraphs indented below it.
FOOTNOTE_RE = re.compile(r" {0,3}\[\^[^\]]+\]:")
# A list item that can start right after a paragraph (``breaks_paragraph``),
# and a setext heading's underline, which ends one.
_BREAKS_RE = re.compile(r"[ \t]*(?:[-*+]|1[.)])[ \t]+(?:\[[ xX]\][ \t]*)?+[^ \t]")
_UNDERLINE_RE = re.compile(r"=+|-+")
# The markers a list block's text starts with, stacked when a bullet wraps one.
_STACKED_RE = re.compile(r"^(?:(?:[-*+]|\d{1,4}[.)])[ \t]+)+")


def parse_frontmatter(text: str, controls: bool = False):
    """Return ``(fields, body)`` — YAML front matter is dropped and its
    top-level ``key: value`` lines are returned as a dict (keys lower-cased,
    a quoted value read as ``_fm_scalar`` does). A value is a string, or a list of strings for the two
    YAML list forms Obsidian's properties use — a flow list ``[a, b]`` and a
    block list (``key:`` followed by ``- item`` lines). Nested mappings and
    multi-line scalars are ignored. ``controls``: the vault's own reading
    (``_fm_scalar``)."""
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
            fields[pending].append(_fm_scalar(item.group(1) or "", controls))
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
            fields[key] = [_fm_scalar(v, controls) for v in _FM_FLOW_ITEM_RE.findall(value[1:-1]) if v.strip()]
        else:
            fields[key] = _fm_scalar(value, controls)
    return fields, text[match.end():]


# ``- item`` (indented or not) under a ``key:`` line
_FM_ITEM_RE = re.compile(r"^\s*-(?:\s+(.*))?$")
# One item of a ``[a, b]`` list: a quoted one holds its commas.
_FM_FLOW_ITEM_RE = re.compile(r"""\s*(?:"(?:[^"\\]|\\.)*+"|'(?:[^']|'')*+'|[^,]+)""")


def _fm_scalar(value: str, controls: bool = False) -> str:
    """A value as YAML reads it, which only a quoted one asks for: its one pair
    of quotes goes, and a quote that is part of the text stays (``'Tis``,
    ``Say "hi"``). A double-quoted value is JSON, as the vault writes it
    (``yaml_text``), unless that is no JSON or gives a control character or a
    lone surrogate, which SQLite cannot take: ``"C:\\foo"`` by hand is a path,
    not a form feed. With ``controls``, the vault's own reading, a control
    character ``yaml_text`` wrote (``\\u0009``) is one, and only a NUL is
    refused; a one-letter escape of one, which ``yaml_text`` never writes,
    is still a path's."""
    value = value.strip()
    if len(value) < 2 or value[0] != value[-1] or value[0] not in "'\"":
        return value
    inner = value[1:-1].strip()
    if value[0] == "'":
        return inner.replace("''", "'")
    if controls and any(escape[1] in _CONTROL_ESCAPES for escape in _ESCAPE_RE.findall(inner)):
        return inner
    try:
        text = json.loads(value)
    except ValueError:
        return inner
    refused = "\x01" if controls else " "
    return inner if any(c < refused or "\ud800" <= c <= "\udfff" for c in text) else text


# A backslash and what it escapes, and the one-letter escapes of control
# characters, which a path typed by hand holds and ``yaml_text`` never writes.
_ESCAPE_RE = re.compile(r"\\.")
_CONTROL_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def yaml_text(value: str) -> str:
    """A scalar for front matter, quoted when YAML would misread it, or a line
    break in it would end it. A control character is written ``\\u0009``,
    which ``_fm_scalar`` reads back; but a tab inside the text needs no
    quotes, and every reader takes it as it stands."""
    v = str(value)
    special = re.search(r'[:#\[\]{}&*!|>\'"%@`,\x00-\x08\x0a-\x1f]|^\s|\s$', v)
    if special or v.lower() in ("true", "false", "null", "yes", "no") \
            or re.match(r"^[\d.+-]", v) and not re.match(r"^\d{4}$", v):
        return _ESCAPE_RE.sub(lambda found: f"\\u{ord(_CONTROL_ESCAPES[found[0][1]]):04x}"
                              if found[0][1] in _CONTROL_ESCAPES else found[0], json.dumps(v, ensure_ascii=False))
    return v


def yaml_list(key: str, items) -> list[str]:
    """A front-matter key holding a block list."""
    return [f"{key}:", *(f"  - {yaml_text(item)}" for item in items)]


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


def code_closer(line: str, closer):
    """What closes the fence or ``$$`` block open at a block's left edge after
    ``line``, as the importer reads one: ``closer`` is that mark before it,
    None outside one."""
    if closer == "$$":
        return None if "$$" in line else closer
    if closer:
        return None if shuts_code(line, closer, 0) else closer
    if _FENCE_LINE_RE.match(line) and not line[0].isspace():
        return _fence_marks(line)
    return "$$" if line.startswith("$$") and "$$" not in line[2:] else None


def item_rest(lines: list[str], own, pad: str, under: str) -> list[str]:
    """A list block's lines after its first, as a file holds them. ``own``: the
    block keeps its marker (``LIST_BLOCK_RE``), so its lines are laid out
    already and only text at its left edge moves ``under`` the marker, where it
    stays in the item; a fence or ``$$`` block at that edge moves whole,
    whatever its lines look like. Otherwise every line moves under the marker
    that wraps the block."""
    out, closer = [], None
    for line in lines:
        laid_out = own and not closer and (not line or line[0].isspace() or LIST_BLOCK_RE.match(line))
        out.append(f"{pad}{line}" if laid_out else f"{pad}{under}{line}")
        closer = code_closer(line, closer)
    return out


def _no_lazy_line(stripped: str) -> bool:
    """Whether CommonMark reads a line left of a list item's text as a block of
    its own, which ends the item, and not as a lazy line of its paragraph: a
    heading, a fence or ``$$``, a quote or a rule."""
    return bool(_ATX_RE.match(stripped) or _FENCE_LINE_RE.match(stripped) or _opens_math(stripped)
                or stripped.startswith(">") or _RULE_LINE_RE.fullmatch(stripped))


def breaks_paragraph(line: str) -> bool:
    """Whether a list item can start right after a paragraph: CommonMark lets
    any bullet with text, and of numbers only 1, written so (``01.`` is text
    to remark)."""
    return bool(_BREAKS_RE.match(line))


def _opens_math(text: str) -> bool:
    """Whether a stripped line opens multi-line ``$$`` math: only the start of
    a line's text can, as in remark-math, and a line with the pair on it is
    whole."""
    return text.startswith("$$") and "$$" not in text[2:]


@dataclass(slots=True)
class Span:
    """Where a block's text is in the file, for ``md_lens``. A block's own
    lines are one run, ``first`` to ``last`` (half open). ``sources`` gives the
    line each line of its content starts on: a blank line of content stands for
    every blank line up to the next one, so the runs of raw lines are
    ``sources[i]`` to ``sources[i + 1]``."""
    first: int
    last: int
    kind: str              # item, wrapped, plain, heading, fence, math, verbatim, quote, table
    sources: list[int]
    indent: str = ""       # the raw whitespace before its first line
    marker: str = ""       # a list block's marker and box as written: ``+ [ ]   ``
    column: int = 0        # a list block's text column
    rest_prefix: str = ""  # what goes before a line added to it
    open: bool = False     # an unclosed fence or $$, which runs to the end of the file

    def grow(self, at: int, after_blank: bool):
        """Line ``at`` joins the block, a blank line of content before it when
        ``after_blank``."""
        if after_blank:
            self.sources.append(self.last)
        self.sources.append(at)
        self.last = at + 1


def _lead(line: str) -> str:
    """The raw whitespace a line starts with."""
    return line[:len(line) - len(line.lstrip())]


def _para_kind(content: str) -> str:
    """The class of a paragraph's text: a quote or a table run stays one block,
    and neither takes an anchor at a line's end."""
    if content.startswith(">"):
        return "quote"
    lines = content.split("\n", 2)
    table = content.startswith("|") or (len(lines) > 1 and "|" in lines[1] and _TABLE_RULE_RE.fullmatch(lines[1]))
    return "table" if table else "plain"


def block_class(content: str) -> str:
    """The class ``content`` has as a block at a line's start: ``Span.kind``'s
    words but ``wrapped`` and ``verbatim``, which are ways of writing one. As
    the parser reads it with ``spans``: only spaces and tabs are whitespace
    (``-\\xa0x`` and ``\\xa0> q`` are text)."""
    line = content.lstrip("\n").split("\n", 1)[0].lstrip(" \t")
    if _HEADING_SPAN_RE.match(line):
        return "heading"
    if _LIST_SPAN_RE.match(line):
        return "item"
    if _FENCE_LINE_RE.match(line):
        return "fence"
    if _opens_math(line):
        return "math"
    return _para_kind(content.strip(" \t\n"))


def wraps_block(body: str) -> bool:
    """Whether a bullet with ``body`` after it only wraps that block."""
    return bool(_WRAPPED_RE.match(body) or _FENCE_LINE_RE.match(body))


def _item_state(state, line: str, ws: str | None = None):
    """A list item's ``state`` past its next ``line``. A fence's marks and
    ``"math"`` are an unclosed block, which takes the lines below whatever they
    look like, and the fence's close gives back the ``"pair"`` written before
    its marks. ``"pair"`` is a ``$$`` left over mid-line: it takes nothing, but the block is one text to the editor (``scanMathSpans``),
    where the item's next ``$$`` closes it, so that one opens nothing
    (``- is $$\n  x\n  $$``), unless it is a formula of its own
    (``_closer_below``). Otherwise None. ``ws``: the whitespace before a
    ``$$`` that opens a formula, and after a task's box, " \\t" with
    ``spans``, as at the top."""
    if state and state[-1] in "`~":
        before, _, marks = state.rpartition(" ")
        return (before or None) if shuts_fence(line, marks) else state
    if state != "math" and _FENCE_LINE_RE.match(line):
        return f"{state or ''} {_fence_marks(line)}".lstrip()
    if line.count("$$") % 2 == 0:
        return state
    if state:
        return None
    box = _ITEM_BOX_RE if ws is None else _ITEM_BOX_SPAN_RE
    return "math" if _opens_math(box.sub("", line.strip(ws), count=1)) else "pair"


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
    span: Span | None = None

    def add_line(self, line: str, sep: str = "\n", at: int = 0):
        self.parts.append(sep + line)
        self.state = _item_state(self.state, line, " \t" if self.span else None)     # a span says ``spans``
        if self.span:
            self.span.grow(at, sep == "\n\n")

    def opens_empty(self, lines: list[str]) -> bool:
        """Whether it is still a marker with nothing after it, which a blank line
        ends: an item opens with one blank line at most (``spans`` only)."""
        marker_only = not _LIST_SPAN_RE.match(lines[self.span.first])[3].strip(" \t")
        return marker_only and len(self.parts) == 1 and not self.node["children"]


def _closer_below(lines: list[str], start: int, indent: int, ws: str | None = None) -> bool:
    """Whether the next ``$$`` in ``lines`` from ``start`` stands alone on its
    line, which is remark-math's close, before a line at ``indent`` or left of
    it leaves the item. A search ends by that ``$$``, the first line the next
    search can start on, so together they read each line once. ``ws``: what
    is whitespace (``_item_state``)."""
    for i in range(start, len(lines)):
        line = lines[i]
        expanded = line.replace("\t", "    ")
        if line.strip(ws) and len(expanded) - len(expanded.lstrip(" ")) <= indent:
            return False
        if line.count("$$") % 2:
            return line.strip(ws) == "$$"
    return False


def _dedent(raw: str, col: int, end: str | None = None) -> str:
    """Drop up to ``col`` columns of leading whitespace (tabs count 4), and the
    ``end`` characters that close it, whitespace by default."""
    line = raw.replace("\t", "    ").rstrip(end)
    lead = len(line) - len(line.lstrip(" "))
    return line[min(lead, col):]


def columns_in(line: str, col: int) -> tuple[int, int]:
    """Where ``col`` columns of ``line``'s leading whitespace end, and how many
    columns that is, tabs counting 4: a tab can go past ``col``."""
    i = width = 0
    while i < len(line) and width < col and line[i] in " \t":
        width += 4 if line[i] == "\t" else 1
        i += 1
    return i, width


def _dedent_code(raw: str, col: int, spans: bool = False) -> str:
    """A fenced line without its fence's indent ``col``: the rest of the line,
    tabs included, is the code's, but for its trailing whitespace, which with
    ``spans`` stays too: a line of code keeps all it holds."""
    line = raw[columns_in(raw, col)[0]:]
    return line if spans else line.rstrip()


def _dedent_kept(raw: str, col: int) -> str:
    """``_dedent`` of a line of code (``spans``): the columns it takes off are
    the same, a tab that reaches past ``col`` leaving the rest as spaces, and
    every character after them stays, tabs and trailing whitespace of any kind
    included."""
    i, width = columns_in(raw, col)
    return " " * max(0, width - col) + raw[i:]


# A task item's box after its bullet: ``[ ]`` / ``[x]``, then the text (Notion
# writes two spaces after the box), or nothing. With ``spans`` only a space or
# a tab ends the box: ``[ ]\xa0x`` is text.
_TASK_RE = re.compile(r"^\[([ xX])\](?:\s+(.*))?$")
_TASK_SPAN_RE = re.compile(r"^\[([ xX])\](?:[ \t]+(.*))?$")
# A bullet's body that is a block of its own, the bullet only a wrapper: Gamma's
# vault writes every nested block ``- <content>``, so ``- - x`` is the list block
# ``- x``, ``- # H`` a heading, ``- ```py`` a fence. The pipe, ``$$`` and ``>``
# arms take the whole construct: ``- |x| is the norm``, ``- $$x$$ is the
# energy`` and ``- >5 is big`` are bullets. ``- 2020. A year`` unwraps with
# ``- 1. x``, which the vault needs. Past these, a fence's own line wraps one
# (``_FENCE_LINE_RE``): ``- ```x``` y`` is a bullet.
_WRAPPED_RE = re.compile(r"^(?:[-*+] |\d{1,4}[.)] |#{1,6} |>(?: |$)|\|.*\|\s*$|\$\$(?:.*\$\$)?\s*$|---+$|\*\*\*+$)")


def _item_content(marker: str, body: str, outline: bool, spans: bool = False) -> tuple[str, bool]:
    """A list item's block content, and whether its next lines lose the text
    column (the block starts where the text did) or only the item's indent
    (they stay aligned under a marker the block keeps). A number stays and a
    task keeps its box. With ``outline`` a bullet's marker is a block
    separator, so it goes and every item's lines lose the text column.
    Otherwise a bullet stays as ``- ``, unless it only wraps a block
    (``_WRAPPED_RE``)."""
    if len(marker) > 1:
        return f"{marker} {body}", outline
    task = (_TASK_SPAN_RE if spans else _TASK_RE).match(body)
    if task:
        return f"- [{task.group(1).lower()}] {task.group(2) or ''}", outline
    if outline or _WRAPPED_RE.match(body) or _FENCE_LINE_RE.match(body):
        return body, True
    return f"- {body}", False


def _width(text: str) -> int:
    """How many columns ``text`` takes at a line's start, a tab going on to the
    next stop of 4, as CommonMark counts them."""
    col = 0
    for c in text:
        col += 4 - col % 4 if c == "\t" else 1
    return col


def content_column(line: str) -> int:
    """The column a list item's text starts at, as CommonMark counts it: past
    its marker and the spaces after it, or one past the marker when nothing
    follows it or five spaces do, the rest being code. ``-   one`` is at 4,
    which this parser, for its own rules, takes for 2."""
    found = _LIST_SPAN_RE.match(line)
    marker, text = _width(line[:found.end(2)]), _width(line[:found.start(3)])
    return text if found.group(3).strip(" \t") and text - marker <= 4 else marker + 1


def _item_span(raw: str, found, at: int, wrapped: bool) -> Span:
    """The span of the list item ``found`` (``_LIST_SPAN_RE``'s match) on line
    ``at``: its marker as written runs to the text, past the box of a task."""
    start, text = found.start(2), found.start(3)
    body = found.group(3).strip(" \t")
    end = text
    if len(found.group(2)) == 1 and not wrapped and (box := _TASK_SPAN_RE.match(body)):
        end += box.start(2) if box.group(2) is not None else len(body)
    lead, column = raw[:start], content_column(raw)
    return Span(at, at + 1, "wrapped" if wrapped else "item", [at], lead, raw[start:end], column,
                lead + " " * (column - _width(lead)))


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


def md_to_blocks(text: str, *, outline: bool = False, flat_headings: bool = False, spans: bool = False) -> list:
    """Parse markdown into a tree of ``{"content": str, "children": [...]}``.
    A bullet keeps its marker, as a list block does, and a fence or multi-line
    ``$$`` below one is a child block; ``outline`` reads every ``- `` as a
    block separator instead (Gamma's own outlines, see the module's
    docstring). ``flat_headings`` puts every heading at the top with nothing
    under it, as Notion's are. Raises TooManyBlocks past MAX_BLOCKS blocks,
    where it stops reading.

    With ``spans`` every node also has a ``"span"`` (``Span``), its lines
    counted as ``text`` holds them, and a block's own lines are one run: a
    paragraph under an item ends before anything that follows it is read, a
    line that would go on an item with children is one more child, a ``$$`` at
    the left edge after a blank line is out of any item, and the empty string
    after a last newline is no line. Only spaces and tabs are whitespace, as in
    CommonMark: a line of no-break spaces or a form feed is text, which a
    paragraph or an item holds as written, and so is such spacing at either end
    of a line's text (a line that opens with it is a paragraph's), after a
    quote's marker (``>\\xa0``), which ends no paragraph of the quote, and
    after a list marker, a heading's marks or a task's box, which it does not
    end: ``-\\xa0x`` and ``#\\xa0h`` are paragraphs and ``- [ ]\\xa0x`` holds no
    box, as in Gamma's editor. A line
    of code or math keeps all it holds past its indent, trailing whitespace
    and tabs included. After a blank line at the top, with no list open, what
    stands four columns in is one ``verbatim`` block of raw lines, whatever
    they look like, unless a footnote's definition stands above it, whose
    lines they are. Where CommonMark ends a list item these rules end it
    too: a blank line ends an item with nothing on its line yet, and past a
    blank line a line left of an item's text is out of it, as is one there that
    can be no lazy line (``_no_lazy_line``). A rule written like
    a list (``* * *``) is a block of its own, and a list item that cannot break
    into a paragraph (``8192. a``, ``breaks_paragraph``) and starts no list
    open before it is the paragraph's next line. A
    fence a wrapped item's text opens (``- - ```py``) runs to its close, as
    one a bare item's opens. A span's ``column`` is CommonMark's
    (``content_column``), not the one these rules read by."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if spans and not lines[-1]:
        lines.pop()
    root = {"content": "", "children": []}
    headings = [(0, root)]   # (heading level, node) — nesting for everything
    lists: list[_Item] = []  # the current list run
    items: list[_Item] = []  # every list item, its content joined at the end
    para: list[str] = []     # pending paragraph lines
    para_target = root
    para_at = 0              # the line ``para`` starts on
    para_lazy = False        # (spans) it is a list item's lazy line: the list's next item can follow it
    blank_before = True      # was the previous line blank?
    footnote = None          # (spans) the footnote's definition just read, at the top
    count = 0

    def flush_para():
        nonlocal para, footnote
        if para:
            content = "\n".join(para) if spans else "\n".join(para).strip()     # (a line of other spacing stays)
            node = mark(add(para_target, content), para_at, para_at + len(para), _para_kind(content) if spans else "")
            if spans and not lists and FOOTNOTE_RE.match(para[0]):
                footnote = node
        para = []

    def add(parent, content):
        nonlocal count, footnote
        footnote = None
        count += 1
        if count > MAX_BLOCKS:
            raise TooManyBlocks()
        node = {"content": content, "children": []}
        parent["children"].append(node)
        return node

    def mark(node, first, last, kind, unclosed=False):
        if spans:
            lead = _lead(lines[first])
            rest = "" if kind == "verbatim" else lead  # (its content is its raw lines)
            node["span"] = Span(first, last, kind, list(range(first, last)), lead, rest_prefix=rest, open=unclosed)
        return node

    i = 0
    while i < len(lines):
        raw = lines[i]
        # What the line loses at either end, and at its end alone (``cut``).
        # With ``spans`` only spaces and tabs, as CommonMark's whitespace: a
        # line of no-break spaces is text, and so is such spacing around a
        # line's text, past a quote's marker too (``>\xa0``).
        stripped, cut = (raw.strip(" \t"), " \t") if spans else (raw.strip(), None)
        indented = raw[:1] in (" ", "\t")
        listed = bool(lists)

        # A fence / $$ block opened inside a list item swallows everything
        # (blank lines included) until it closes.
        if lists and lists[-1].state not in (None, "pair"):
            lists[-1].add_line((_dedent_kept if spans else _dedent)(raw, lists[-1].strip), "\n", i)
            blank_before = False
            i += 1
            continue

        if not stripped:
            flush_para()
            if spans and lists and lists[-1].opens_empty(lines):
                lists.pop()
            blank_before = True
            i += 1
            continue

        list_match = (_LIST_SPAN_RE if spans else _LIST_RE).match(raw)
        rule = spans and list_match and _RULE_LINE_RE.fullmatch(raw)
        if rule or (spans and list_match and para and not lists and not para_lazy
                    and not _UNDERLINE_RE.fullmatch(para[-1]) and not breaks_paragraph(raw)):
            list_match = None
        if spans and lists and indented and not list_match and (blank_before or _no_lazy_line(stripped)):
            while lists and lists[-1].span.column > text_column(raw):
                lists.pop()

        if spans and not lists and blank_before and text_column(raw) >= 4:
            end = i + 1
            for j in range(i + 1, len(lines)):
                if lines[j].strip(" \t"):
                    if text_column(lines[j]) < 4:
                        break
                    end = j + 1
            if footnote:
                after_blank = True
                for j in range(i, end):
                    if lines[j].strip(" \t"):
                        text = _dedent(lines[j], 4, " \t") or lines[j].strip(" \t")
                        footnote["content"] += ("\n\n" if after_blank else "\n") + text
                        footnote["span"].grow(j, after_blank)
                    after_blank = not lines[j].strip(" \t")
            else:
                mark(add(headings[-1][1], "\n".join(lines[i:end])), i, end, "verbatim")
            blank_before = False
            i = end
            continue

        if (_HEADING_SPAN_RE if spans else _HEADING_RE).match(stripped) and not indented:
            flush_para()
            lists.clear()
            if flat_headings:
                mark(add(root, stripped), i, i + 1, "heading")
            else:
                level = len(stripped) - len(stripped.lstrip("#"))
                while len(headings) > 1 and headings[-1][0] >= level:
                    headings.pop()
                headings.append((level, mark(add(headings[-1][1], stripped), i, i + 1, "heading")))
            blank_before = False
            i += 1
            continue

        if rule:
            flush_para()
            if indented:
                while lists and lists[-1].indent >= text_column(raw):
                    lists.pop()
            else:
                lists.clear()
            mark(add(lists[-1].node if lists else headings[-1][1], stripped), i, i + 1, "plain")
            blank_before = False
            i += 1
            continue

        if list_match:
            flush_para()
            indent = len(list_match.group(1).replace("\t", "    "))
            marker, body = list_match.group(2), list_match.group(3)
            body = body if EMPTY_ITEM_RE.fullmatch(body) else body.strip(cut)
            while lists and lists[-1].indent >= indent:
                lists.pop()
            parent = lists[-1].node if lists else headings[-1][1]
            content, wrapped = _item_content(marker, body, outline, spans)
            column = indent + len(marker) + 1
            state = _item_state(None, (_STACKED_RE if spans else _NUMBER_MARKER_RE).sub("", content, count=1), cut)
            lists.append(_Item(indent, add(parent, content), column, column if wrapped else indent, state, [content]))
            items.append(lists[-1])
            if spans:
                lists[-1].span = lists[-1].node["span"] = _item_span(raw, list_match, i, wrapped)
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
            closes = not _closer_below(lines, i + 1, lists[-1].indent if indented else -1, cut)
            if spans and blank_before and not indented:
                closes = False
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
            if spans and lists:
                flush_para()
            child = lists and lists[-1].strip < lists[-1].column and (opens or lists[-1].node["children"])
            if spans and lists and lists[-1].node["children"]:
                child = True
            if lists and not child and (closes or not blank_before or indent <= lists[-1].column):
                flush_para()
                lists[-1].add_line(_dedent(raw, lists[-1].strip, cut) or stripped, "\n\n" if blank_before else "\n", i)
                blank_before = False
                i += 1
                continue
        else:
            lists.clear()

        if _FENCE_LINE_RE.match(stripped):
            flush_para()
            marks = _fence_marks(stripped)
            col = text_column(raw)
            start = i
            buf = [_dedent_code(raw, col, spans)]
            i += 1
            unclosed = True
            while i < len(lines):
                buf.append(_dedent_code(lines[i], col, spans))
                if shuts_code(lines[i], marks, col):
                    unclosed = False
                    i += 1
                    break
                i += 1
            mark(add(lists[-1].node if lists else headings[-1][1], "\n".join(buf)), start, i, "fence", unclosed)
            blank_before = False
            continue

        # Multi-line $$ display math is kept whole like a fence — its rows
        # would otherwise hit the list rule (`- x + y &= 3 \\`, `1. & ...`)
        # and shatter the formula across blocks.
        if math_line and not closes:
            flush_para()
            start = i
            buf = [raw.lstrip(" \t") if spans else stripped]
            i += 1
            unclosed = True
            while i < len(lines):
                buf.append(lines[i].lstrip(" \t") if spans else lines[i].strip())
                if "$$" in lines[i]:
                    unclosed = False
                    i += 1
                    break
                i += 1
            mark(add(lists[-1].node if lists else headings[-1][1], "\n".join(buf)), start, i, "math", unclosed)
            blank_before = False
            continue

        # Plain text: a paragraph under the current heading, or a child block
        # of the list item it is indented under.
        target = lists[-1].node if lists else headings[-1][1]
        if para and para_target is not target:
            flush_para()
        para_target = target
        if not para:
            para_at, para_lazy = i, listed and not blank_before
        para.append(stripped)
        blank_before = False
        i += 1

    flush_para()
    for item in items:
        item.node["content"] = "".join(item.parts)
        if spans:
            item.span.open = item.state not in (None, "pair")
    return root["children"]
