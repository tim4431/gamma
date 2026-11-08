"""Notion-flavored Markdown (``markdown_version: "v2"``) and the block model.

``write`` turns model blocks (``notion_blocks``) into NFM, ``read`` turns NFM
back. Both are pure: what lies outside a page reaches the writer through a
``Links``, the seam the API's id map and uploads will sit behind.

``_Writer`` is one form of the output, Notion's own. Inline it writes tags,
never ``**``: tags have no flanking rules, which fail next to CJK. A form is a
subclass in ``_FORMS`` that changes the class's tables and small methods; the
block code is shared. ``_FileWriter`` is the form for Notion's file importer,
for blocks through ``notion_blocks.lower_for_file``: standard Markdown, its
inline text ``zotero_notes.markdown``'s, and a tag only for a row of ``CAPS``
that a probe turned on.

``read`` takes Notion's output, which has its marks in tags and escapes only
"<" (migrate-to-notion-flavored-markdown), so a "*" or a backtick in it is
text, and ``read`` leaves ``**`` as it finds it. ``read`` also takes what
``write`` escaped, which is why it reads "\\" before any punctuation.

What a read cannot hold it keeps or names: ``<table-of-contents />`` and a
mention with no name are dropped and any other block tag becomes an
``unknown`` block with its source, each reported through ``warn``, so a caller
counts them.
"""

import html
import re
from typing import Protocol

from .markdown_import import shuts_fence
from .note_markup import LINK_TARGET
from .notion_blocks import _LIST_ITEMS, CAPS, Block, color_name, merge_colors
from .notion_gamma import _SPACED_LANGUAGES, _TASK_RE, _address
from .zotero_notes import _DELIM_CELL_RE, _RULE_RE, _cells, _url_md, escape, markdown


class Links(Protocol):
    def page(self, ref) -> str | None: ...         # relative path; later a Notion URL
    def asset(self, url, kind) -> str | None: ...  # bundled path; later a placeholder URL
    def warn(self, title, reason) -> None: ...


# --- writing ---------------------------------------------------------------------

# The order marks nest in, outermost first, as in zotero_notes: a link holds
# its marks, so a run of them stays one link.
_ORDER = ("link", "color", "underline", "strike", "bold", "italic")
# Notion's list of code languages has neither of these.
_LANGUAGES = {"svg": "xml", "bibtex": ""}
_FILE_TAGS = ("file", "audio", "video", "pdf")
_TAG_OF_EXTENSION = {"pdf": "pdf", "mp3": "audio", "wav": "audio", "m4a": "audio", "ogg": "audio", "flac": "audio",
                     "mp4": "video", "mov": "video", "webm": "video", "mkv": "video"}
# The "\" zotero_notes.escape puts before a web address's first "/": Notion
# makes no link of a bare address, so there is nothing to break.
_URL_SLASH_RE = re.compile(r"(?<=http:)\\/|(?<=https:)\\/")
_EMOJI_RE = re.compile(r":(?=[\w+-]+:)")           # a ":word:" would read as a custom emoji
_CLOSING_HASHES_RE = re.compile(r"(?:^|(?<=[ \t]))#+\Z")  # what CommonMark takes off a "# x #" line
_DATE_RE = re.compile(r"@(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?(?: \(remind @[^)]*\))?")
_REF_RE = re.compile(r"!?\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")


def _attr(value) -> str:
    return html.escape(str(value), quote=True)


def _escaped(text: str, prev: str, nxt: str, label: bool) -> str:
    """Literal text as NFM: ``zotero_notes.escape`` with every "$", since a
    formula is any pair, then what NFM adds. In a ``label``, a link's or an
    image's, "]" would end it. A table's "|" waits for ``_Writer.cell``, which
    has the marks' tags as well."""
    out = _URL_SLASH_RE.sub("/", escape(text, prev, nxt, True))
    out = _EMOJI_RE.sub(r"\\:", out)
    return out.replace("]", "\\]") if label else out


def _held(marks: dict) -> list:
    """A run's marks, outermost first. Notion holds one colour on a run, so a
    pair keeps its text colour."""
    held = [(key, marks[key]) for key in _ORDER if marks.get(key)]
    return [(k, v[0] if k == "color" and isinstance(v, tuple) else v) for k, v in held]


def _plain(rich: list) -> str:
    return "".join(leaf[1] for leaf in rich if leaf[0] != "br")


def _colored(block: Block) -> str:
    color = block.attrs.get("color")
    return f' color="{_attr(color)}"' if color else ""


class _Writer:
    marks = {"underline": ("<u>", "</u>"), "strike": ("<s>", "</s>"), "bold": ("<b>", "</b>"),
             "italic": ("<i>", "</i>")}
    bullet_indent = 2
    number_indent = 3
    languages = _LANGUAGES
    empty_paragraph = "<p />"

    def __init__(self, links: Links, caps: dict = CAPS):
        self.links, self.caps = links, caps

    # inline

    def wrap(self, mark: tuple) -> tuple:
        key, value = mark
        if key == "link":
            return "[", f"]({_url_md(value)})"
        if key == "color":
            return f'<span color="{_attr(value)}">', "</span>"
        return self.marks[key]

    def code(self, text: str) -> str:
        """A code run. It is literal, so a "</code>" in it goes over two runs."""
        return "<code>" + text.replace("\n", " ").replace("</code>", "<</code><code>/code>") + "</code>"

    def math(self, text: str) -> str:
        """A formula on one line, in a single pair of dollars: ``$$`` is
        display math, and only on lines of its own."""
        body = text[2:-2] if text.startswith("$$") and len(text) > 4 else text[1:-1]
        return f"${body.strip()}$".replace("\n", " ")

    def mention(self, url: str) -> str:
        """Self-closing, so a title holding "</mention>" cannot break it."""
        return f'<mention url="{_attr(url)}" />'

    def date(self, day: str, time: str | None) -> str:
        start = f' start-time="{time}"' if time else ""
        return f'<date start="{day}"{start} />'

    def raw(self, text: str, label: bool):
        """A raw leaf: a date, a reference to a page, a person or Markdown
        already rendered. What no page answers is its name, as text."""
        if m := _DATE_RE.fullmatch(text):
            return self.date(m.group(1), m.group(2))
        if m := _REF_RE.fullmatch(text):
            url = self.links.page(m.group(1))
            return self.mention(url) if url else (m.group(2) or m.group(1), label)
        return (text, label) if text.startswith("@") else text

    def tokens(self, leaves: list, label: bool) -> list:
        """A rich text as what ``rich`` puts together: strings as they go out,
        and each piece of text as (text, in a label)."""
        out, held = [], []
        for leaf in leaves:
            wanted = held if leaf[0] == "br" else _held(leaf[2])
            keep = 0
            while keep < min(len(held), len(wanted)) and held[keep] == wanted[keep]:
                keep += 1
            out += [self.wrap(mark)[1] for mark in reversed(held[keep:])]
            out += [self.wrap(mark)[0] for mark in wanted[keep:]]
            held = wanted
            in_label = label or any(key == "link" for key, _ in held)
            if leaf[0] == "br":
                out.append("<br />")
            elif leaf[0] == "code":
                out.append(self.code(leaf[1]))
            elif leaf[0] == "math":
                out.append(self.math(leaf[1]))
            elif leaf[0] == "raw":
                out.append(self.raw(leaf[1], in_label))
            else:
                for i, line in enumerate(leaf[1].split("\n")):
                    if i:
                        out.append("<br />")
                    if line and out and isinstance(out[-1], tuple):
                        out[-1] = (out[-1][0] + line, in_label)
                    elif line:
                        out.append((line, in_label))
        return out + [self.wrap(mark)[1] for mark in reversed(held)]

    def rich(self, leaves: list, label: bool = False) -> str:
        tokens = [t for t in self.tokens(leaves, label) if t]
        edges = [t[0] if isinstance(t, tuple) else t for t in tokens]
        out = []
        for i, token in enumerate(tokens):
            if isinstance(token, tuple):
                prev = edges[i - 1][-1] if i else ""
                nxt = edges[i + 1][0] if i + 1 < len(edges) else ""
                out.append(_escaped(token[0], prev, nxt, token[1]))
            else:
                out.append(token)
        return "".join(out)

    def cell(self, leaves: list) -> str:
        return self.rich(leaves).replace("|", "\\|")

    # blocks

    def blocks(self, blocks: list) -> str:
        return "\n\n".join(filter(None, (getattr(self, f"write_{block.type}")(block) for block in blocks)))

    def title(self, block: Block) -> str:
        """A tag's first line, ``<p />`` for none or a blank one. Blank is what
        ``_Reader.container`` skips, an NBSP included: a line it skips would
        leave the first child to read as the title."""
        body = self.rich(block.rich)
        return body if body.strip() else "<p />"

    def tagged(self, tag: str, block: Block, attrs: str = "") -> str:
        """The container shape: the title on the line after the tag and the
        children after a blank line."""
        body = self.title(block)
        if children := self.blocks(block.children):
            body += "\n\n" + children
        return f"<{tag}{attrs}{_colored(block)}>\n{body}\n</{tag}>"

    def needs_tag(self, line: str, block: Block) -> bool:
        """Whether a block has what only its tag holds: a colour or edge whitespace."""
        return bool(block.attrs.get("color")) or line != line.strip(" \t")

    def heading_line(self, line: str) -> str:
        return _CLOSING_HASHES_RE.sub(r"\\\g<0>", line)

    def write_paragraph(self, block: Block) -> str:
        line = self.rich(block.rich)
        if block.children or self.needs_tag(line, block):
            return self.tagged("p", block)
        return line if line.strip() else self.empty_paragraph

    def write_heading(self, block: Block) -> str:
        """H5 and H6 are H4, Notion's last. A plain heading holds no children,
        so they follow it."""
        level = min(block.attrs.get("level", 1), 4)
        line = self.rich(block.rich)
        head = "#" * level + " " + self.heading_line(line)
        if self.needs_tag(line, block):
            head = f"<h{level}{_colored(block)}>\n{self.title(block)}\n</h{level}>"
        children = self.blocks(block.children)
        return f"{head}\n\n{children}" if children else head

    def write_toggle(self, block: Block) -> str:
        level = block.attrs.get("heading")
        return self.tagged("toggle", block, f' heading="h{min(level, 4)}"' if level else "")

    def write_quote(self, block: Block) -> str:
        line = self.rich(block.rich)
        return self.tagged("quote", block) if block.children or self.needs_tag(line, block) else f"> {line}"

    def write_callout(self, block: Block) -> str:
        icon = block.attrs.get("icon")
        return self.tagged("callout", block, f' icon="{_attr(icon)}"' if icon else "")

    def fits_a_line(self, block: Block) -> bool:
        """Whether a list item goes as a Markdown line: Notion nests only list
        items by indent, so all the way down it must hold nothing else."""
        return not self.needs_tag(self.rich(block.rich), block) and all(
            child.type in _LIST_ITEMS and self.fits_a_line(child) for child in block.children)

    def has_text(self, block: Block) -> bool:
        """Whether CommonMark finds text on a list item's line: a to-do's box
        is text to it."""
        return block.type == "todo" or bool(self.rich(block.rich).strip(" \t"))

    def item(self, block: Block, marker: str, indent: int) -> str:
        """Its lines at the text's indent, and its children under it: a list
        straight after the text if it can interrupt a paragraph, which takes
        a start of 1 and text, anything else after a blank line, or it would
        read as more of the text. With no text a blank line would end the item."""
        lines = (marker + self.rich(block.rich)).split("\n")
        # Only "\n": a form feed or a U+2028 in text is no line of ours.
        held = self.blocks(block.children).split("\n") if block.children else []
        first = block.children[0] if held else None
        interrupts = first and first.type in _LIST_ITEMS and first.attrs.get("start", 1) == 1 and self.has_text(first)
        gap = [""] if held and self.has_text(block) and not interrupts else []
        return "\n".join([lines[0], *(" " * indent + part if part else part for part in [*lines[1:], *gap, *held])])

    def write_bullet(self, block: Block) -> str:
        if self.fits_a_line(block):
            return self.item(block, "- ", self.bullet_indent)
        return "<ul>\n" + self.tagged("li", block) + "\n</ul>"

    def write_number(self, block: Block) -> str:
        """``<ol>`` has no attribute for where it starts, so a number that
        holds more than list items is back to 1 in Notion."""
        if self.fits_a_line(block):
            marker = f"{block.attrs.get('start', 1)}. "
            return self.item(block, marker, max(self.number_indent, len(marker)))
        return "<ol>\n" + self.tagged("li", block) + "\n</ol>"

    def write_todo(self, block: Block) -> str:
        checked = bool(block.attrs.get("checked"))
        if self.fits_a_line(block):
            return self.item(block, f"- [{'x' if checked else ' '}] ", self.bullet_indent)
        return self.tagged("todo", block, f' checked="{str(checked).lower()}"')

    def write_code(self, block: Block) -> str:
        text = block.attrs.get("text", "")
        fence = "`" * max([3] + [len(run) + 1 for run in re.findall("`+", text)])
        language = (block.attrs.get("language") or "").strip()
        language = self.languages.get(language.lower(), language)
        return f"{fence}{language if re.fullmatch(r'[^\r\n`]*', language) else ''}\n{text}\n{fence}"

    def write_equation(self, block: Block) -> str:
        return f"$$\n{block.attrs.get('text', '')}\n$$"

    def write_divider(self, block: Block) -> str:
        return "---"

    def write_table(self, block: Block) -> str:
        rows = block.attrs.get("rows") or []
        if not rows:
            return ""
        width = max(map(len, rows))
        rows = [[*row, *[[]] * (width - len(row))] for row in rows]
        if block.attrs.get("header", True):
            lines = ["| " + " | ".join(map(self.cell, row)) + " |" for row in rows]
            return "\n".join([lines[0], "| " + " | ".join(["---"] * width) + " |", *lines[1:]])
        # Only the tag says a table has no header row.
        cells = ("<tr>\n" + "\n".join(f"<td>{self.rich(cell)}</td>" for cell in row) + "\n</tr>" for row in rows)
        return '<table header-row="false">\n' + "\n".join(cells) + "\n</table>"

    def left_out(self, block: Block, kind: str) -> str:
        """A file ``Links.asset`` has no place for: a link, or its name as
        text. A ``data:`` address holds the file, not a name."""
        url = block.attrs.get("url") or ""
        name = _plain(block.rich) or (kind if url.startswith("data:") else url.rstrip("/").rsplit("/", 1)[-1])
        self.links.warn(name, f"{kind} left out")
        return self.rich([("text", name, {"link": url} if re.match(r"https?://", url) else {})])

    def write_image(self, block: Block) -> str:
        url = self.links.asset(block.attrs.get("url"), "image")
        if url is None:
            return self.left_out(block, "image")
        return f"![{self.rich(block.rich, label=True)}]({_url_md(url)})"

    def write_file(self, block: Block) -> str:
        url = block.attrs.get("url") or ""
        tag = block.attrs.get("media")
        if tag not in _FILE_TAGS:
            tag = _TAG_OF_EXTENSION.get(re.split(r"[?#]", url, maxsplit=1)[0].rpartition(".")[2].lower(), "file")
        source = self.links.asset(url, tag)
        return self.left_out(block, tag) if source is None else self.file_tag(tag, source, block)

    def file_tag(self, tag: str, source: str, block: Block) -> str:
        caption = self.rich(block.rich)
        attrs = f' source="{_attr(source)}"'
        return f"<{tag}{attrs}>{caption}</{tag}>" if caption else f"<{tag}{attrs} />"

    def write_page(self, block: Block) -> str:
        """Always with a url: a ``<page>`` without one makes a new sub-page."""
        ref = block.attrs.get("ref")
        url = block.attrs.get("url") or (self.links.page(ref) if ref else None)
        title = self.rich(block.rich)
        if not url:
            return title
        tag = "database" if block.attrs.get("database") else "page"
        return f'<{tag} url="{_attr(url)}">{title}</{tag}>'

    def write_unknown(self, block: Block) -> str:
        return block.attrs.get("raw", "")


class _FileWriter(_Writer):
    """The form Notion's file importer reads. What ``lower_for_file`` left
    needs no tag, so a tag comes out only for a row of ``caps`` turned on."""
    languages = {}                               # the API's list of languages binds no file
    empty_paragraph = ""

    def __init__(self, links: Links, caps: dict = CAPS):
        super().__init__(links, caps)
        self.bullet_indent = self.number_indent = caps["list_indent"]

    def needs_tag(self, line: str, block: Block) -> bool:
        return bool(block.attrs.get("color"))

    def fits_a_line(self, block: Block) -> bool:
        return True

    def heading_line(self, line: str) -> str:
        return super().heading_line(line) if self.caps["escape"] else line

    def run(self, kind: str, text: str, marks: dict) -> list:
        """A leaf as ``zotero_notes.markdown`` takes it: what is already
        written here goes raw, as text does with escaping off. So does a "]"
        in a link, which ``markdown`` would move out of it: a title can hold
        one, and the link then stays one."""
        if kind == "code":
            return [("code", text.replace("\n", " "), marks)]
        if kind == "math":
            return [("math", self.math(text), marks)]
        if kind == "raw":
            shown = self.raw(text, False)
            return [("text", shown[0], marks) if isinstance(shown, tuple) else ("raw", shown, marks)]
        literal = "text" if self.caps["escape"] else "raw"
        parts = re.split(r"(\])", text) if "link" in marks else [text]
        return [("raw", "\\]", marks) if part == "]" else (literal, part, marks) for part in parts if part]

    def runs(self, leaves: list) -> list:
        """Rich text as ``zotero_notes.markdown`` takes it, a link's target in
        the form it writes and a colour, whose span it writes Gamma's way,
        as the tags around the run."""
        out = []
        for leaf in leaves:
            if leaf[0] == "br":
                out.append(leaf)
                continue
            kind, text, marks = leaf
            color = dict(_held(marks)).get("color")
            marks = {key: value for key, value in marks.items() if key != "color"}
            if "link" in marks:
                marks["link"] = _url_md(marks["link"])
            opening, closing = self.wrap(("color", color)) if color else ("", "")
            out += [("raw", opening, marks), *self.run(kind, text, marks), ("raw", closing, marks)]
        return out

    def rich(self, leaves: list, label: bool = False) -> str:
        if label:                                # an image's alt: its words, on one line
            alt = " ".join(_plain(leaves).split())
            return _escaped(alt, " ", " ", True) if self.caps["escape"] else alt
        return markdown(self.runs(leaves), collapse=False).replace("\n", self.caps["hard_break"])

    def write_quote(self, block: Block) -> str:
        """Its text and all it holds behind ">", the only quote a file has."""
        body = "\n\n".join(filter(None, (self.rich(block.rich), self.blocks(block.children))))
        return "\n".join(f"> {line}" if line else ">" for line in body.split("\n"))

    def file_tag(self, tag: str, source: str, block: Block) -> str:
        name = _plain(block.rich) or source.rpartition("/")[2]
        return self.rich([("text", name, {"link": source})])


_FORMS = {"nfm": _Writer, "file": _FileWriter}


def write(blocks: list, links: Links, form: str = "nfm", caps: dict = CAPS) -> str:
    """Model blocks → a page's text, with a blank line between blocks.
    ``form="file"`` wants them through ``lower_for_file`` with the same ``caps``."""
    return _FORMS[form](links, caps).blocks(blocks)


# --- reading ---------------------------------------------------------------------

_TAG = r"<(?P<name>[a-z][a-z0-9_-]*)(?P<attrs>(?:\s+[\w-]+(?:=\"[^\"]*\")?)*)\s*(?P<closed>/?)>"
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_MATH = r"\$(?=[^\s$])(?:\\[^\n]|[^$\\\n])+(?<=\S)\$(?!\d)"
_OPEN_RE = re.compile(r"[ \t]{0,3}" + _TAG)
_SCAN_RE = re.compile(r"\\.|" + _MATH + r"|</(?P<end>[a-z][a-z0-9_-]*)\s*>|" + _TAG)
_FENCE_RE = re.compile(r"[ \t]*(`{3,}|~{3,})(.*)")
_HEADING_RE = re.compile(r"[ \t]{0,3}(#{1,6})(?:[ \t]+(.*))?$")
_QUOTE_RE = re.compile(r"[ \t]{0,3}> ?")
_ITEM_RE = re.compile(r"([ \t]{0,3})(?:[-+*]|(\d{1,9})[.)])(?:[ \t]+|$)")
_ONE_LINE_MATH_RE = re.compile(r"\$\$(.+)\$\$")
# A title is bounded: unbounded, each "](" of a long line with no ")" scans to its end.
_TARGET_RE = re.compile(r"\([ \t]*(<[^<>\n]*>|" + LINK_TARGET + r")(?:[ \t][^)\n]{0,1000})?\)")
_BRACKET_RE = re.compile(r"\\.|<code>|" + _MATH + r"|[\[\]]", re.S)
_INLINE_RE = re.compile(
    r"(?P<esc>\\[!-/:-@\[-`{-~])"
    r"|(?P<br><br\s*/?>|\\?\n[ \t]*)"
    r"|(?P<entity>&#?[0-9A-Za-z]+;)"
    r"|(?P<math>" + _MATH + ")"
    r"|(?P<shut></(?P<end>[a-z]+)>)"
    r"|(?P<tag>" + _TAG + ")"
    r"|(?P<bracket>!?\[)")
_MARK_TAGS = {"b": {"bold": True}, "i": {"italic": True}, "s": {"strike": True}, "u": {"underline": True}}
_INLINE_TAGS = {*_MARK_TAGS, "br", "code", "date", "link-preview", "mention", "span"}
_LITERAL_TAGS = ("code", "mention")                # what holds no markup, so no closing of ours
_TRANSPARENT = {"columns", "column", "tabs", "tab", "synced-block", "synced-block-reference"}
_CONTAINERS = {"p": "paragraph", "quote": "quote", "toggle": "toggle", "callout": "callout", "todo": "todo",
               "li": "bullet"}
_ID_RE = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}")
_MAX_DEPTH = 100


def _attrs(text: str) -> dict:
    return {key: html.unescape(value) for key, value in _ATTR_RE.findall(text or "")}


def _add(out: list, kind: str, text: str, marks: dict):
    """A leaf at the end of ``out``. Two code runs in a row are one, as
    ``_Writer.code`` splits them."""
    if kind in ("text", "code") and out and out[-1][0] == kind and out[-1][2] == marks:
        out[-1] = (kind, out[-1][1] + text, marks)
    elif text:
        out.append((kind, text, marks))


def _pairs(text: str) -> dict:
    """Each "[" that is one, at the "]" it closes. "](y)" has no "[", so it
    stays text. A formula's brackets are not ours, and a "$" in an address
    starts no formula."""
    pairs, opened, pos = {}, [], 0
    while m := _BRACKET_RE.search(text, pos):
        pos = m.end()
        if m.group() == "<code>":
            end = text.find("</code>", pos)
            pos = end + 7 if end >= 0 else pos
        elif m.group() == "[":
            opened.append(m.start())
        elif m.group() == "]" and opened:
            pairs[opened.pop()] = m.start()
            pos = target.end() if (target := _TARGET_RE.match(text, pos)) else pos
    return pairs


def _link_at(text: str, opening: int, pairs: dict):
    """The link whose "[" is at ``opening``: its label, address and end."""
    closing = pairs.get(opening)
    target = _TARGET_RE.match(text, closing + 1) if closing is not None else None
    return (text[opening + 1:closing], _address(target.group(1)), target.end()) if target else None


def _stamp(day: str, time: str | None) -> str:
    return f"@{day} {time}" if time else f"@{day}"


def _read_date(out: list, attrs: dict, marks: dict):
    """A date as the mention Gamma has for it. Gamma's holds no range, so an
    end is a second one, and the time zone goes."""
    if attrs.get("start"):
        out.append(("raw", _stamp(attrs["start"], attrs.get("start-time")), marks))
    if attrs.get("end"):
        _add(out, "text", " → ", marks)
        out.append(("raw", _stamp(attrs["end"], attrs.get("end-time")), marks))


def _read_mention(out: list, attrs: dict, label: str, marks: dict, warn):
    """A mention of a page is a link to it, until an id map can tell whose it
    is. Anything else, a person included, is its label as text, and with no
    label it is only an id, which goes."""
    url = attrs.get("url", "")
    if url.startswith("http"):
        _add(out, "text", label or url, {**marks, "link": url})
    elif label:
        _add(out, "text", label, marks)
    else:
        warn(url, "mention with no name, dropped")


def _inline(out: list, text: str, marks: dict, warn, in_link: bool = False):
    stack, pos, pairs = [], 0, {} if in_link else _pairs(text)
    while m := _INLINE_RE.search(text, pos):
        before = text[pos:m.start()]
        pos = m.end()
        if m.group("br"):
            _add(out, "text", before.rstrip(" \t") if "\n" in m.group() else before, marks)
            out.append(("br",))
            continue
        _add(out, "text", before, marks)
        if m.group("esc"):
            _add(out, "text", m.group()[1], marks)
        elif m.group("entity"):
            _add(out, "text", html.unescape(m.group()), marks)
        elif m.group("math"):
            _add(out, "math", m.group(), marks)
        elif m.group("bracket"):
            link = _link_at(text, m.end() - 1, pairs)
            if not link:
                _add(out, "text", m.group(), marks)
                continue
            label, href, pos = link
            if label:
                _inline(out, label, {**marks, "link": href}, warn, True)
            else:
                _add(out, "text", href, {**marks, "link": href})
        elif m.group("shut"):
            tag = m.group("end")
            at = next((i for i in range(len(stack) - 1, -1, -1) if stack[i][0] == tag), None)
            if at is None:
                _add(out, "text", m.group(), marks)
            else:
                marks = stack[at][1]
                del stack[at:]
        else:
            name, attrs = m.group("name"), _attrs(m.group("attrs"))
            if name == "date":
                _read_date(out, attrs, marks)
            elif name == "link-preview":
                _read_mention(out, attrs, "", marks, warn)
            elif name == "code" or name == "mention" and not m.group("closed"):
                end = text.find(f"</{name}>", pos)
                if end < 0:
                    _add(out, "text", m.group(), marks)
                    continue
                if name == "code":
                    _add(out, "code", text[pos:end], marks)
                else:
                    _read_mention(out, attrs, text[pos:end], marks, warn)
                pos = end + len(name) + 3
            elif name == "mention":
                _read_mention(out, attrs, "", marks, warn)
            elif name in _MARK_TAGS and not m.group("closed"):
                stack.append((name, marks))
                marks = {**marks, **_MARK_TAGS[name]}
            elif name == "span" and not m.group("closed"):
                stack.append((name, marks))
                color = merge_colors(marks.get("color"), color_name(attrs.get("color")))
                marks = {**marks, "color": color} if color else marks
            else:                                # any other tag is saved as text
                _add(out, "text", m.group(), marks)
    _add(out, "text", text[pos:], marks)


def _no_warn(title, reason):
    pass


def inline(text: str, warn=_no_warn) -> list:
    """A block's text → rich text."""
    out = []
    _inline(out, text, {}, warn)
    return out


def _block_color(attrs: dict) -> dict:
    """A block's colour stays on the block: ``to_gamma`` puts it on the runs,
    under their own."""
    name = color_name(attrs.get("color"))
    return {"color": name} if name else {}


def _notion_id(url: str) -> str:
    """A database's id as a Notion route keeps it, undashed; its address if
    it has none. What follows a "?" is the id of a view."""
    found = _ID_RE.findall(url.partition("?")[0])
    return found[-1].replace("-", "") if found else url


def _shuts(line: str, fence: str) -> bool:
    """Whether ``line`` ends what ``fence``, or a "$$" line, opened."""
    return line.strip() == fence if fence == "$$" else shuts_fence(line, fence)


def _closing(lines: list, i: int, col: int, tag: str):
    """Where the ``tag`` opened before ``col`` on line ``i`` closes, as (line,
    start, end), or None. A fence, an equation, a formula, a code run, a
    mention's label and an escaped "<" hold no tags. A "$$" with no other
    after it opens nothing, or it would take the page with it."""
    depth, fence = 1, ""
    for j in range(i, len(lines)):
        line, pos = lines[j], col if j == i else 0
        if fence:
            fence = "" if _shuts(line, fence) else fence
            continue
        if j > i and (m := _FENCE_RE.match(line)):
            fence = m.group(1)
            continue
        if j > i and line.strip() == "$$" and any(lines[k].strip() == "$$" for k in range(j + 1, len(lines))):
            fence = "$$"
            continue
        while m := _SCAN_RE.search(line, pos):
            pos = m.end()
            name = m.group("name")
            if name in _LITERAL_TAGS and not m.group("closed"):
                end = line.find(f"</{name}>", pos)
                pos = end + len(name) + 3 if end >= 0 else pos
            elif name == tag and not m.group("closed"):
                depth += 1
            elif m.group("end") == tag:
                depth -= 1
                if not depth:
                    return j, m.start(), m.end()
    return None


def _between(lines: list, i: int, col: int, j: int, end: int) -> list:
    if i == j:
        return [lines[i][col:end]]
    return [lines[i][col:], *lines[i + 1:j], lines[j][:end]]


def _elements(lines: list) -> list:
    """The elements directly in a table or a row, as (tag, attrs, inner lines)."""
    out, i, col = [], 0, 0
    while i < len(lines):
        m = _SCAN_RE.search(lines[i], col)
        if not m:
            i, col = i + 1, 0
        elif not m.group("name"):
            col = m.end()
        elif m.group("closed"):
            out.append((m.group("name"), _attrs(m.group("attrs")), []))
            col = m.end()
        else:
            end = _closing(lines, i, m.end(), m.group("name"))
            if not end:
                break
            out.append((m.group("name"), _attrs(m.group("attrs")), _between(lines, i, m.end(), end[0], end[1])))
            i, col = end[0], end[2]
    return out


def _indent(line: str) -> int:
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(" "))


def _dedent(line: str, width: int) -> str:
    """``line`` less ``width`` of indent, a tab being four."""
    return " " * max(_indent(line) - width, 0) + line.lstrip(" \t")


def _is_delimiter(line: str) -> bool:
    return "-" in line and "|" in line and all(_DELIM_CELL_RE.fullmatch(cell) for cell in _cells(line))


def _is_image(line: str) -> bool:
    line = line.strip()
    return line.startswith("![") and bool(link := _link_at(line, 1, _pairs(line))) and link[2] == len(line)


def _kind(lines: list, i: int) -> str | None:
    """The block starting at line ``i``, None for a paragraph."""
    line = lines[i]
    if _FENCE_RE.match(line) and _indent(line) < 4:
        return "fence"
    if line.strip() == "$$" or _ONE_LINE_MATH_RE.fullmatch(line.strip()):
        return "math"
    if _RULE_RE.fullmatch(line):
        return "divider"
    if _HEADING_RE.match(line):
        return "heading"
    if _QUOTE_RE.match(line):
        return "quote"
    if _ITEM_RE.match(line):
        return "item"
    if (m := _OPEN_RE.match(line)) and m.group("name") not in _INLINE_TAGS:
        return "tag"
    if "|" in line and i + 1 < len(lines) and _is_delimiter(lines[i + 1]):
        return "table"
    return "image" if _is_image(line) else None


def _paragraph_end(lines: list, i: int) -> int:
    i += 1
    while i < len(lines) and lines[i].strip() and not _kind(lines, i):
        i += 1
    return i


class _Reader:
    def __init__(self, warn):
        self.warn = warn
        self.depth = 0

    def blocks(self, lines: list) -> list:
        if self.depth >= _MAX_DEPTH:
            self.warn("nesting", "too deep, kept as text")
            return [Block("unknown", attrs={"raw": "\n".join(lines)})]
        self.depth += 1
        out, i = [], 0
        while i < len(lines):
            if not lines[i].strip():
                i += 1
            elif kind := _kind(lines, i):
                made, i = getattr(self, f"read_{kind}")(lines, i)
                out += made
            else:
                end = _paragraph_end(lines, i)
                out.append(Block("paragraph", inline("\n".join(line.strip(" \t") for line in lines[i:end]), self.warn)))
                i = end
        self.depth -= 1
        return out

    def container(self, lines: list) -> tuple:
        """The container shape → (title, children). The title keeps its edge
        whitespace, which is what the tag was for; ``<p />`` is no title."""
        i = 0
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and lines[i].strip() == "<p />":
            return [], self.blocks(lines[i + 1:])
        if i == len(lines) or _kind(lines, i):
            return [], self.blocks(lines[i:])
        end = _paragraph_end(lines, i)
        return inline("\n".join(lines[i:end]), self.warn), self.blocks(lines[end:])

    def read_fence(self, lines: list, i: int) -> tuple:
        m = _FENCE_RE.match(lines[i])
        fence, indent = m.group(1), _indent(lines[i])
        body, j = [], i + 1
        while j < len(lines) and not _shuts(lines[j], fence):
            body.append(_dedent(lines[j], indent))
            j += 1
        attrs = {"text": "\n".join(body)}
        info = m.group(2).split()
        if language := _SPACED_LANGUAGES.get(" ".join(info).lower(), info[0] if info else ""):
            attrs["language"] = language
        return [Block("code", attrs=attrs)], j + 1

    def read_math(self, lines: list, i: int) -> tuple:
        if m := _ONE_LINE_MATH_RE.fullmatch(lines[i].strip()):
            return [Block("equation", attrs={"text": m.group(1)})], i + 1
        j = i + 1
        while j < len(lines) and lines[j].strip() != "$$":
            j += 1
        return [Block("equation", attrs={"text": "\n".join(lines[i + 1:j])})], j + 1

    def read_divider(self, lines: list, i: int) -> tuple:
        return [Block("divider")], i + 1

    def read_heading(self, lines: list, i: int) -> tuple:
        m = _HEADING_RE.match(lines[i])
        rich = inline((m.group(2) or "").strip(" \t"), self.warn)
        return [Block("heading", rich, attrs={"level": min(len(m.group(1)), 4)})], i + 1

    def read_quote(self, lines: list, i: int) -> tuple:
        """A line right after a ">" line joins the quote, whatever it starts with."""
        body = []
        while i < len(lines) and lines[i].strip():
            m = _QUOTE_RE.match(lines[i])
            body.append(lines[i][m.end():] if m else lines[i])
            i += 1
        rich, children = self.container(body)
        return [Block("quote", rich, children)], i

    def read_item(self, lines: list, i: int) -> tuple:
        m = _ITEM_RE.match(lines[i])
        indent, width = _indent(lines[i]), m.end()
        body, j = [lines[i][m.end():]], i + 1
        while j < len(lines):
            if lines[j].strip():
                if _indent(lines[j]) <= indent:
                    break
                body.append(_dedent(lines[j], width))
            else:
                following = next((k for k in range(j + 1, len(lines)) if lines[k].strip()), None)
                if following is None or _indent(lines[following]) <= indent:
                    break
                body.append("")
            j += 1
        attrs, kind = {}, "bullet"
        if m.group(2):
            kind, attrs = "number", {"start": int(m.group(2))}
        elif todo := _TASK_RE.match(body[0]):
            kind = "todo"
            attrs["checked"] = todo.group(1) != " "
            body[0] = body[0][todo.end():]
        rich, children = self.container(body)
        return [Block(kind, rich, children, attrs)], j

    def read_table(self, lines: list, i: int) -> tuple:
        rows = [_cells(lines[i])]
        i += 2
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            rows.append(_cells(lines[i]))
            i += 1
        cells = [[inline(cell, self.warn) for cell in row] for row in rows]
        return [Block("table", attrs={"rows": cells, "header": True})], i

    def read_image(self, lines: list, i: int) -> tuple:
        line = lines[i].strip()
        label, url, _ = _link_at(line, 1, _pairs(line))
        return [Block("image", inline(label, self.warn), attrs={"url": url})], i + 1

    def read_tag(self, lines: list, i: int) -> tuple:
        m = _OPEN_RE.match(lines[i])
        name, start = m.group("name"), m.end()
        j, end, stop = i, start, start
        if not m.group("closed"):
            j, end, stop = _closing(lines, i, start, name) or (len(lines) - 1, len(lines[-1]), len(lines[-1]))
        body = [] if m.group("closed") else _between(lines, i, start, j, end)
        raw = "\n".join([*lines[i:j], lines[j][:stop]])
        rest = lines[j][stop:]
        made = self.element(name, _attrs(m.group("attrs")), body, raw)
        if rest.strip():                         # "</column><column>" on one line
            lines[j] = rest
            return made, j
        return made, j + 1

    def element(self, name: str, attrs: dict, body: list, raw: str) -> list:
        if name in _TRANSPARENT:
            return self.blocks(body)
        if name in _CONTAINERS:
            rich, children = self.container(body)
            block = Block(_CONTAINERS[name], rich, children, _block_color(attrs))
            if name == "callout" and attrs.get("icon"):
                block.attrs["icon"] = attrs["icon"]
            elif name == "todo":
                block.attrs["checked"] = attrs.get("checked") == "true"
            elif name == "toggle" and (level := re.fullmatch(r"h([1-4])", attrs.get("heading", ""))):
                block.attrs["heading"] = int(level.group(1))
            return [block]
        if re.fullmatch(r"h[1-4]", name):        # a plain heading's children are its siblings
            rich, children = self.container(body)
            return [Block("heading", rich, attrs={"level": int(name[1]), **_block_color(attrs)}), *children]
        if name == "ul":
            return self.blocks(body)
        if name == "ol":
            items = self.blocks(body)
            for n, block in enumerate(items, 1):
                if block.type == "bullet":
                    block.type = "number"
                    block.attrs["start"] = n
            return items
        if name == "equation":
            return [Block("equation", attrs={"text": "\n".join(body).strip()})]
        if name == "table":
            return [self.tagged_table(attrs, body)]
        caption = inline("\n".join(body).strip(), self.warn)
        url = attrs.get("source") or attrs.get("url") or ""
        if name == "image":
            return [Block("image", caption, attrs={"url": url})]
        if name in _FILE_TAGS:
            return [Block("file", caption, attrs={"url": url, "media": name})]
        if name in ("bookmark", "embed"):        # a linked paragraph
            return [Block("paragraph", [("text", _plain(caption) or url, {"link": url})])]
        if name == "page":
            return [Block("page", caption, attrs={"url": url})]
        if name == "database":
            return [Block("page", caption, attrs={"url": url, "database": _notion_id(url)})]
        if name == "table-of-contents":
            self.warn("table of contents", "dropped")
            return []
        self.warn(name, "unknown block, kept as text")
        return [Block("unknown", attrs={"raw": raw})]

    def tagged_table(self, attrs: dict, body: list) -> Block:
        """``<table>``: header flags, colours and widths go, and a head row
        counts as ``header-row`` does when there is no such attribute."""
        rows = [[(tag, inner) for tag, _, inner in _elements(row) if tag in ("td", "th")]
                for tag, _, row in _elements(body) if tag == "tr"]
        header = attrs.get("header-row", "true" if rows and rows[0] and rows[0][0][0] == "th" else "false") == "true"
        cells = [[inline("\n".join(line.strip() for line in inner).strip(), self.warn) for _, inner in row]
                 for row in rows]
        return Block("table", attrs={"rows": cells, "header": header})


def read(text: str, warn=None) -> list:
    """A page's NFM → model blocks. ``warn`` is called as ``Links.warn`` is,
    once for each thing dropped or kept only as text."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return _Reader(warn or _no_warn).blocks(lines)
