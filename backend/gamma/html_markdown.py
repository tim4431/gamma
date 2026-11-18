"""HTML as a tree of elements, and such a tree as Markdown.

``Parser`` reads HTML into ``El``s and text, forgiving as a browser is about
an unclosed ``<p>`` or ``<li>``. The Zotero and Notion imports read notes and
pages with it, the web clipper a clipped fragment, the web reader a page.

``Markdown`` writes such a tree as Markdown blocks. The web clipper
(``web_clip.ClipMarkdown``) writes a note a reader keeps, so its text is
escaped where Markdown would read it as markup and its pictures point at the
copies it stored. The web reader (``web_page.PageMarkdown``) writes what the
model reads, so its text stays as the page has it.

- Block elements (paragraphs, headings, lists, quotes, code, tables, figures,
  rules) become blocks; inline ones (emphasis, links, code, images, math)
  become the text inside them. An inline element holding blocks (``<a><div>``)
  is a block too.
- A list nests by its indentation. A ``<pre>`` is fenced with its language.
- Math is ``$…$`` / ``$$…$$``. A formula is a ``<math>`` (its TeX annotation,
  its ``alttext``, else ``mathml.to_latex``) or a ``<span data-gamma-math=
  "inline"|"display">`` carrying ``data-tex`` or ``data-mathml``.
- A table of plain cells is a Markdown table. Its rows are found in one walk
  that skips the tables inside it. A cell's text fills every grid cell its
  ``rowspan`` and ``colspan`` cover. A layout table, whose cells hold
  paragraphs, lists, quotes, code, headings or tables, is its cells' content
  in order; a wrapper in a cell (a ``<div>``) does not make one.
- Scripts, styles, form controls, inline SVG, canvases and ``<nav>`` say
  nothing. A frame or a video is a link to it.

A subclass changes the writing through these hooks:

- ``text(s)``: a run of text, its spaces collapsed. Base: as it is.
- ``line(s)``: one line of a paragraph, trimmed. Base: as it is.
- ``href(raw)``: a link's address from its ``href``, or "" to write the link
  as its text. Base: an absolute http(s) address (``address``); an anchor
  into the page itself gives "".
- ``image(node)``: an ``<img>``. Base: "".
- ``heading(level, text)``: a heading block. It must stay a ``Heading``.
- ``code_lang(node)``: a ``<pre>``'s language. Base: its ``data-lang``, else
  a ``language-x``, ``lang-x``, ``highlight-x`` or ``sourceCode x`` class on
  the ``<pre>`` or its ``<code>``, else a ``language-x`` or ``highlight-x``
  class on one of the three blocks around it.

``link(node, inner)`` writes a link from its written text. A link whose text
opens with a ``![`` picture is that picture's link and stays the picture.

A hook can use ``resolve(raw)``, which gives an attribute's address and its
scheme against ``base``, and ``address(raw)``, an absolute http(s) address
written for Markdown. It can also read ``within``, the elements ``block`` is
writing, outermost first.

``blocks(nodes)`` writes a run of nodes as blocks. ``joined(blocks)`` joins
them and says where each heading among them starts. A heading is found by its
type, never by a ``#`` that opens a line, so a comment in a code block never
reads as one. A heading inside a quote or a list item is part of that block,
not a heading of the text.

The writer recurses a few frames per level of the tree, so a tree it writes
must come from a parser whose ``max_open`` is at most ``DEPTH``.
"""

import re
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

from . import mathml

# How deep a tree ``Markdown`` can write. Its walk takes about three Python
# frames per level, and Python stops at 1,000.
DEPTH = 120

VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
                  "track", "wbr"})
_HIDDEN = {"script", "style", "head", "title"}


class El:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict):
        self.tag, self.attrs, self.children = tag, attrs, []


class Parser(HTMLParser):
    """HTML → a tree of ``El`` and text, forgiving as a browser is about an
    unclosed <p> or <li>. What ``left_out`` names (scripts, styles, the head)
    is dropped with everything it holds. ``closes_p`` lists the start tags
    that close an open <p>.

    A subclass can bound ``max_open``, which bounds every walk up the open
    elements. What nests past that bound lies flat in the last element kept.
    The elements past the bound are still tracked, so an end tag closes the
    one it names; a count of their tags makes an end tag that names none of
    them cost nothing there, and a search that finds one is paid for by the
    elements it closes. ``hides`` says which of them keep their text out:
    such an element holds nothing, so a later walk that drops it could not
    drop its text with it."""
    max_open = float("inf")
    closes_p = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "ul", "ol", "li", "table",
                          "hr", "html", "body", "div"})

    def __init__(self, keep_p: bool = False, hidden=()):
        super().__init__(convert_charrefs=True)
        self.keep_p = keep_p                     # a <p> stays open around a block (Notion's export)
        self.left_out = _HIDDEN | set(hidden)
        self.root = El("", {})
        self.stack = [self.root]
        self.hidden = 0
        self.past: list[tuple[str, bool]] = []   # what is open past max_open: (tag, whether its text is kept out)
        self.past_tags: Counter = Counter()      # how many of each tag ``past`` holds

    def hides(self, tag: str, attrs: dict) -> bool:
        """Whether an element that opens past ``max_open`` keeps its text out
        of the tree. Base: none does."""
        return False

    def handle_starttag(self, tag, attrs):
        if self.hidden:
            self.hidden += 0 if tag in VOID else 1
            return
        if tag in self.left_out:
            self.hidden = 1
            return
        if len(self.stack) >= self.max_open:
            self._past(tag, dict(attrs))
            return
        if not self.keep_p and self.stack[-1].tag == "p" and tag in self.closes_p:
            self.stack.pop()
        if tag == "li":
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag in ("ul", "ol"):
                    break
                if self.stack[i].tag == "li":
                    del self.stack[i:]
                    break
        el = El(tag, dict(attrs))
        self.stack[-1].children.append(el)
        if tag not in VOID:
            self.stack.append(el)

    def _past(self, tag: str, attrs: dict):
        out = bool(self.past) and self.past[-1][1]
        if not out:
            self.stack[-1].children.append(El(tag, attrs))
        if tag not in VOID:
            self.past.append((tag, out or self.hides(tag, attrs)))
            self.past_tags[tag] += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.hidden:
            self.hidden -= 1
            return
        if self.past_tags[tag]:
            i = len(self.past) - 1
            while self.past[i][0] != tag:
                i -= 1
            for closed, _ in self.past[i:]:
                self.past_tags[closed] -= 1
            del self.past[i:]
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                self.past.clear()
                self.past_tags.clear()
                return

    def handle_data(self, data):
        if self.hidden or self.past and self.past[-1][1]:
            return
        kids = self.stack[-1].children
        if kids and isinstance(kids[-1], str):
            kids[-1] += data
        else:
            kids.append(data)


# --- the writer -------------------------------------------------------------

# Nothing a reader is shown.
_SKIP = {"script", "style", "noscript", "template", "svg", "canvas", "button", "input", "select", "textarea",
         "option", "datalist", "head", "title", "meta", "link", "object", "embed", "map", "area", "audio",
         "source", "track", "dialog", "nav"}
_BLOCK = {"address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details", "div", "dl",
          "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
          "header", "hgroup", "hr", "html", "iframe", "legend", "li", "main", "ol", "p", "pre", "section",
          "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul", "video"}
_HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# What makes a table a layout table when a cell holds it.
_CELL_BLOCKS = frozenset({"p", "ul", "ol", "li", "dl", "table", "pre", "blockquote", "figure", "hr",
                          "h1", "h2", "h3", "h4", "h5", "h6", "iframe", "video"})
SPACE = re.compile(r"[ \t\n\r\f\v   ]+")
# A code block's language in a class of its <pre> or <code> ...
_LANG_CLASS = re.compile(r"(?:^|\s)(?:(?:language|lang|highlight(?:-source)?)-|sourceCode\s+)([\w+#.-]+)")
# ... or of a wrapper around them (GitHub's highlight-source-python, Sphinx's
# highlight-python, Jekyll's language-python), where lang-x could be the
# page's language.
_WRAPPER_LANG_CLASS = re.compile(r"(?:^|\s)(?:language|highlight(?:-source)?)-([\w+#.-]+)")
# The most HTML lets a cell span.
_COLSPAN, _ROWSPAN = 1000, 65534
# What the tables of one tree may write beyond their cells' own text. A span
# repeats its cell's text in every grid cell it covers, and a short row is
# padded to the widest, so a few bytes of markup could write gigabytes. Past
# this budget a table's rows go as they come, each with its own cells.
_TABLE_CHARS = 2_000_000


class Heading(str):
    """A heading block as written (``## Text``). ``joined`` finds the headings
    among the blocks by this type."""

    def __new__(cls, level: int, text: str):
        self = super().__new__(cls, f"{'#' * level} {text}")
        self.level = level
        return self


def attr(el: El, name: str) -> str:
    """An attribute's value, "" when it is absent or has no value."""
    return el.attrs.get(name) or ""


def walk(node):
    """Every node under ``node``, in document order."""
    for kid in node.children:
        yield kid
        if not isinstance(kid, str):
            yield from walk(kid)


def text_of(node) -> str:
    """The text under a node as it stands, a <br> a line break."""
    if isinstance(node, str):
        return node
    if node.tag in _SKIP:
        return ""
    if node.tag == "br":
        return "\n"
    return "".join(text_of(k) for k in node.children)


def outer(node) -> str:
    """The element written back as markup (a ``<math>`` for ``mathml``)."""
    if isinstance(node, str):
        return node.replace("&", "&amp;").replace("<", "&lt;")
    attrs = "".join(f' {k}="{v or ""}"' for k, v in node.attrs.items() if '"' not in (v or ""))
    return f"<{node.tag}{attrs}>" + "".join(outer(k) for k in node.children) + f"</{node.tag}>"


def wrap(inner: str, mark: str) -> str:
    """``inner`` emphasised by ``mark``, its spaces outside the marks."""
    core = inner.strip()
    if not core:
        return inner
    if core.startswith(mark) and core.endswith(mark):
        return inner  # <b><b>x</b></b>
    lead = inner[:len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()):]
    return f"{lead}{mark}{core}{mark}{trail}"


def code_span(text: str) -> str:
    text = SPACE.sub(" ", text)
    if not text.strip():
        return ""
    ticks = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def fence(text: str, lang: str = "") -> str:
    text = text.replace("\r\n", "\n").strip("\n")
    ticks = "`" * max(3, max((len(run) for run in re.findall(r"^`+", text, re.M)), default=0) + 1)
    lang = re.sub(r"[^\w+#.-]", "", lang)[:30]
    return f"{ticks}{lang}\n{text}\n{ticks}"


def formula(tex: str, display: bool) -> str:
    tex = (tex or "").strip()
    # Wikipedia's alt text wraps the formula in a style switch.
    m = re.fullmatch(r"\{\\(?:display|text)style\s*(.*)\}", tex, re.S)
    if m:
        tex = m.group(1).strip()
    if not tex:
        return ""
    if display:
        return f"$$\n{tex}\n$$"
    return f"${SPACE.sub(' ', tex)}$"


def indent(block: str, pad: str) -> str:
    return "\n".join(pad + line if line else line for line in block.split("\n"))


def paragraph(text: str) -> str:
    """A paragraph's text: each line trimmed (four spaces would make code) and
    the blank lines at its ends gone."""
    lines = [line.strip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def markdown_url(url: str) -> str:
    """An address written so a Markdown link or picture holds it whole."""
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")


def joined(blocks: list[str]) -> tuple[str, list[tuple[int, Heading]]]:
    """Blocks as one Markdown text, with the offset in it of each heading
    among them."""
    headings, at = [], 0
    for block in blocks:
        if isinstance(block, Heading):
            headings.append((at, block))
        at += len(block) + 2
    return "\n\n".join(blocks), headings


def _rows(table: El) -> list[El]:
    """The ``<tr>``s of ``table`` in order, without those of a table inside it."""
    rows, todo = [], list(reversed(table.children))
    while todo:
        node = todo.pop()
        if isinstance(node, str) or node.tag == "table":
            continue
        if node.tag == "tr":
            rows.append(node)
        todo.extend(reversed(node.children))
    return rows


def _span(el: El, name: str, most: int) -> int:
    m = re.match(r"\s*([0-9]{1,6})", attr(el, name))
    return min(max(int(m.group(1)), 1), most) if m else 1


def _grid(rows: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """Rows of ``(text, rowspan, colspan)`` laid on the table's grid, each
    cell's text in every grid cell it covers."""
    grid, above = [], {}  # above: column → [rows it still covers, text]

    def take(col):
        left = above[col]
        left[0] -= 1
        if not left[0]:
            del above[col]
        return left[1]
    for row in rows:
        line: list[str] = []
        for text, down, across in row:
            while len(line) in above:
                line.append(take(len(line)))
            for _ in range(across):
                if len(line) in above:  # cells that overlap: this row is one of the span's anyway
                    take(len(line))
                if down > 1:
                    above[len(line)] = [down - 1, text]
                line.append(text)
        for col in sorted(c for c in above if c >= len(line)):
            line += [""] * (col - len(line))
            line.append(take(col))
        grid.append(line)
    return grid


class Markdown:
    """An ``El`` tree as Markdown blocks, read against the page's address
    ``base_url``. The module docstring lists the hooks."""

    def __init__(self, base_url: str = ""):
        self.base = base_url or ""
        self.within: list[El] = []
        self._blocky: dict[int, bool] = {}
        self._table_room = _TABLE_CHARS
        self._in_cell = False  # writing a table cell's text, where a block is set apart by spaces

    # --- hooks ----------------------------------------------------------------

    def text(self, s: str) -> str:
        return s

    def line(self, s: str) -> str:
        return s

    def href(self, raw: str) -> str:
        url, scheme = self.resolve(raw)
        if scheme not in ("http", "https"):
            return ""
        raw = raw.strip()
        if self.base and raw.startswith("#") or (
                urldefrag(url)[1] and urldefrag(url)[0] == urldefrag(self.base)[0]):
            return ""  # an anchor into the page itself
        return markdown_url(url)

    def image(self, node: El) -> str:
        return ""

    def heading(self, level: int, text: str) -> Heading:
        return Heading(level, text)

    def code_lang(self, node: El) -> str:
        if attr(node, "data-lang"):
            return attr(node, "data-lang")
        code = next((k for k in node.children if not isinstance(k, str) and k.tag == "code"), None)
        found = [_LANG_CLASS.search(attr(el, "class")) for el in (node, code) if el is not None]
        found += [_WRAPPER_LANG_CLASS.search(attr(el, "class")) for el in self.within[-2:-5:-1]]
        # Sphinx's highlight-default and highlight-none name no language.
        return next((m.group(1).lower() for m in found if m and m.group(1).lower() not in ("default", "none")), "")

    # --- addresses --------------------------------------------------------------

    def resolve(self, raw: str) -> tuple[str, str]:
        """An attribute's address read against ``base``, and its scheme in
        lower case; ``("", "")`` for none."""
        raw = (raw or "").strip()
        if not raw:
            return "", ""
        try:
            url = urljoin(self.base, raw)
            return url, urlsplit(url).scheme.lower()
        except ValueError:
            return "", ""

    def address(self, raw: str) -> str:
        """An attribute's absolute http(s) address written for Markdown, ""
        for any other."""
        url, scheme = self.resolve(raw)
        return markdown_url(url) if scheme in ("http", "https") else ""

    # --- what an element is -------------------------------------------------------

    def _math_display(self, node: El) -> bool | None:
        """For a formula, whether it is a display one (None: no formula)."""
        if node.tag == "span" and "data-gamma-math" in node.attrs:
            return node.attrs["data-gamma-math"] == "display"
        if node.tag == "math":
            return node.attrs.get("display") == "block"
        return None

    def _is_block(self, node: El) -> bool:
        """A block of its own: a block element, a display formula, or an
        inline one holding blocks (``<a><div>…</div></a>``)."""
        key = id(node)
        if key not in self._blocky:
            display = self._math_display(node)
            if display is not None:
                self._blocky[key] = display
            elif node.tag in _BLOCK:
                self._blocky[key] = True
            else:
                self._blocky[key] = any(not isinstance(k, str) and k.tag not in _SKIP and self._is_block(k)
                                        for k in node.children)
        return self._blocky[key]

    # --- inline -------------------------------------------------------------------

    def inline(self, kids) -> str:
        return "".join(self._inline(k) for k in kids)

    def _inline(self, node) -> str:
        if isinstance(node, str):
            return self.text(SPACE.sub(" ", node))
        tag = node.tag
        if tag in _SKIP:
            return ""
        if tag == "br":
            return "\n"
        if tag == "img":
            return self.image(node)
        if self._math_display(node) is not None:
            return self.math(node)
        if tag in ("code", "kbd", "samp", "tt", "var") and not any(
                not isinstance(k, str) and k.tag in ("a", "img") for k in node.children):
            return code_span(text_of(node))
        inner = self.inline(node.children)
        if tag in ("strong", "b"):
            return wrap(inner, "**")
        if tag in ("em", "i", "cite", "dfn"):
            return wrap(inner, "*")
        if tag in ("del", "s", "strike"):
            return wrap(inner, "~~")
        if tag in ("sup", "sub") and inner.strip():
            return f"<{tag}>{inner.strip()}</{tag}>"
        if tag == "a":
            return self.link(node, inner)
        if tag == "q":
            return f"“{inner}”"
        if tag in _BLOCK and self._in_cell:  # a cell's wrapper <div>s: apart from each other
            return f" {inner} "
        return inner

    def link(self, node: El, inner: str) -> str:
        href = self.href(attr(node, "href"))
        text = SPACE.sub(" ", inner).strip()
        if not href or not text or text.startswith("!["):
            return inner  # an anchor, an empty link, a picture's link (the picture stays)
        lead = inner[:len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()):]
        text = text.replace("]", "\\]")
        return f"{lead}[{text}]({href}){trail}"

    def math(self, node: El) -> str:
        display = bool(self._math_display(node))
        if node.tag == "math":
            tex = next((text_of(k) for k in walk(node) if not isinstance(k, str) and k.tag == "annotation"
                        and k.attrs.get("encoding") == "application/x-tex"), "")
            tex = tex or attr(node, "alttext") or mathml.to_latex(outer(node))
        else:
            tex = attr(node, "data-tex")
            if not tex.strip() and node.attrs.get("data-mathml"):
                tex = mathml.to_latex(node.attrs["data-mathml"])
            tex = tex or text_of(node)
        return formula(tex, display)

    # --- blocks -------------------------------------------------------------------

    def _paragraph(self, text: str) -> str:
        return "\n".join(map(self.line, paragraph(text).split("\n")))

    def blocks(self, kids) -> list[str]:
        """The Markdown blocks of a run of nodes: block children are their own,
        the inline ones between them gather into paragraphs."""
        out: list[str] = []
        run: list[str] = []

        def flush():
            text = self._paragraph("".join(run))
            run.clear()
            if text:
                out.append(text)
        for kid in kids:
            if isinstance(kid, str) or kid.tag in _SKIP or not self._is_block(kid):
                run.append(self._inline(kid))
                continue
            flush()
            out.extend(self.block(kid))
        flush()
        return out

    def block(self, node: El) -> list[str]:
        self.within.append(node)
        written = self._block(node)
        self.within.pop()
        return written

    def _block(self, node: El) -> list[str]:
        tag = node.tag
        if self._math_display(node) is not None:
            formula = self.math(node)
            return [formula] if formula else []
        if tag in _HEADING:
            text = SPACE.sub(" ", self.inline(node.children)).strip()
            return [self.heading(_HEADING[tag], text)] if text else []
        if tag in ("ul", "ol"):
            listed = self.listing(node)
            return [listed] if listed else []
        if tag == "blockquote":
            inner = "\n\n".join(self.blocks(node.children))
            return ["\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))] if inner else []
        if tag == "pre":
            text = text_of(node)
            return [fence(text, self.code_lang(node))] if text.strip() else []
        if tag == "hr":
            return ["---"]
        if tag == "table":
            return self.table(node)
        if tag in ("dt", "summary", "legend"):
            text = self._paragraph(self.inline(node.children))
            return [f"**{text}**" if "\n" not in text else text] if text else []
        if tag in ("figcaption", "caption"):
            text = self._paragraph(self.inline(node.children))
            return [f"*{text}*" if "\n" not in text and not text.startswith("*") else text] if text else []
        if tag in ("iframe", "video"):
            src = self.address(attr(node, "src") or next(
                (attr(k, "src") for k in node.children if not isinstance(k, str) and k.tag == "source"), ""))
            if not src:
                return []
            host = urlsplit(src).netloc or src
            return [f"[{'Video' if tag == 'video' else 'Embedded'}: {self.text(host)}]({src})"]
        return self.blocks(node.children)

    def listing(self, node: El) -> str:
        ordered = node.tag == "ol"
        try:
            number = int(attr(node, "start") or "1")
        except ValueError:
            number = 1
        items = []
        for kid in node.children:
            if isinstance(kid, str):
                continue
            if kid.tag in ("ul", "ol"):  # a list right inside a list (no <li>): under the item before it
                nested = self.listing(kid)
                if nested and items:
                    items[-1] += "\n" + indent(nested, "  ")
                elif nested:
                    items.append(nested)
                continue
            if kid.tag != "li":
                continue
            marker = f"{number}." if ordered else "-"
            number += 1
            parts = self.blocks(kid.children)
            if not parts:
                continue
            pad = " " * (len(marker) + 1)
            first, rest = parts[0], parts[1:]
            item = marker + " " + indent(first, pad)[len(pad):]
            for part in rest:
                # A list goes right under its item; anything else after a
                # blank line, a child block of the item.
                sep = "\n" if re.match(r"(?:[-+*]|\d{1,9}[.)]) ", part) else "\n\n"
                item += sep + indent(part, pad)
            items.append(item)
        return "\n".join(items)

    def table(self, node: El) -> list[str]:
        cells = [[c for c in r.children if not isinstance(c, str) and c.tag in ("td", "th")] for r in _rows(node)]
        cells = [r for r in cells if r]
        # A wrapper in a cell (a centering <div>, Wikipedia's {{center}})
        # only holds its text; a paragraph, list, quote, code, table, frame or
        # display formula makes the table a layout table.
        layout = not cells or any(not isinstance(n, str) and (n.tag in _CELL_BLOCKS or self._math_display(n))
                                  for row in cells for c in row for n in walk(c))
        caption = next((k for k in node.children if not isinstance(k, str) and k.tag == "caption"), None)
        head = self.block(caption) if caption else []
        if layout:
            return head + [b for row in cells for c in row for b in self.blocks(c.children)]
        self._in_cell = True
        rows = [[(SPACE.sub(" ", self.inline(c.children).replace("\n", " ")).strip().replace("|", "\\|"),
                  _span(c, "rowspan", _ROWSPAN), _span(c, "colspan", _COLSPAN)) for c in row] for row in cells]
        self._in_cell = False
        lines = [[text for text, _, _ in row] for row in rows]
        if any(down > 1 or across > 1 for row in rows for _, down, across in row):
            cost = sum((len(text) + 3) * across * min(down, len(rows) - i)
                       for i, row in enumerate(rows) for text, down, across in row)
            if cost <= self._table_room:
                self._table_room -= cost
                lines = _grid(rows)
        width = max(len(r) for r in lines)
        padding = 3 * sum(width - len(r) for r in lines)
        if padding <= self._table_room:
            self._table_room -= padding
        else:
            width = 0  # past the budget: each row with its own cells
        out = []
        for texts in lines:
            out.append("| " + " | ".join(texts + [""] * (width - len(texts))) + " |")
            if len(out) == 1:
                out.append("|" + "|".join(" --- " for _ in range(width or len(texts))) + "|")
        return head + ["\n".join(out)]
