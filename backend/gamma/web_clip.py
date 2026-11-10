"""A web page as note blocks: the HTML the Gamma Connector clipped — a page's
article, which Mozilla's Readability finds in the browser, or the text
selected on it — as Markdown, which ``markdown_import.md_to_blocks`` makes
into blocks the way it does a .md file's.

The browser does what needs the live page (extension/clip.js): finding the
article, an image's real address (a lazy one's, a srcset's), a formula's TeX
or MathML, a code block's language, and the pictures' bytes, which it uploads
itself and names here by their address (``images``). What arrives is a clean
fragment, so the reading is shallow: a tree from ``html.parser`` and one walk
over it.

- Block elements (paragraphs, headings, lists, quotes, code, tables, figures,
  rules) become Markdown blocks, inline ones (emphasis, links, code, images,
  math) the text inside them. Headings keep their level, so the blocks nest
  under them as a .md file's do, and a list nests by its indentation.
- A picture the browser uploaded points at its stored copy, any other at its
  address on the web: a picture Gamma can't store or the browser couldn't
  fetch still shows while the site serves it. A link points at its absolute
  address; an anchor into the page itself is plain text.
- Math is ``$…$`` / ``$$…$$``: the TeX the page kept, else its MathML read by
  ``mathml.to_latex``.
- A table of plain cells is a Markdown table; a layout table (cells holding
  paragraphs, lists or tables) is its cells' content in order.
- Scripts, styles, forms' controls, inline SVG and canvases say nothing; a
  frame (a video) is a link to it.
- Text is escaped where Markdown would read it as markup (the editor renders
  raw HTML, ``$`` math and ``*`` emphasis).
"""

import re
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

from . import mathml
from .markdown_import import md_to_blocks

# The clipped fragment, as the request may carry it.
MAX_HTML_CHARS = 5 * 1024 * 1024

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
         "track", "wbr"}
# Nothing a note shows.
_SKIP = {"script", "style", "noscript", "template", "svg", "canvas", "button", "input", "select", "textarea",
         "option", "datalist", "head", "title", "meta", "link", "object", "embed", "map", "area", "audio",
         "source", "track", "dialog", "nav"}
_BLOCK = {"address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details", "div", "dl",
          "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
          "header", "hgroup", "hr", "html", "iframe", "legend", "li", "main", "ol", "p", "pre", "section",
          "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul", "video"}
_HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# A stored copy the browser uploaded (POST /api/upload-image).
_STORED = re.compile(r"/api/uploads/[A-Za-z0-9._-]+")
_SPACE = re.compile(r"[ \t\n\r\f\v   ]+")
# What a line of a paragraph must not start with, or Markdown reads it as a
# heading, a quote, a list item, a rule, a fence or a table row.
_LEAD = re.compile(r"(#{1,6}(?:\s|$)|>|[-+](?:\s|$)|\d{1,9}[.)](?:\s|$)|=+\s*$|```|~~~|\|)")


class _Node:
    __slots__ = ("tag", "attrs", "kids")

    def __init__(self, tag: str, attrs: dict):
        self.tag = tag
        self.attrs = attrs
        self.kids: list = []


class _Tree(HTMLParser):
    """The fragment as nested ``_Node``s (text as strings). The browser's
    serializer closes every element, so an end tag closes up to the element
    it names and one that names none is dropped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].kids.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].kids.append(_Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag):
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data):
        self.stack[-1].kids.append(data)


def _parse(html: str) -> _Node:
    tree = _Tree()
    tree.feed(html)
    tree.close()
    return tree.root


def _text_of(node) -> str:
    if isinstance(node, str):
        return node
    if node.tag in _SKIP:
        return ""
    if node.tag == "br":
        return "\n"
    return "".join(_text_of(k) for k in node.kids)


def _outer(node) -> str:
    """The element written back as markup (a ``<math>`` for mathml)."""
    if isinstance(node, str):
        return node.replace("&", "&amp;").replace("<", "&lt;")
    attrs = "".join(f' {k}="{v}"' for k, v in node.attrs.items() if '"' not in v)
    return f"<{node.tag}{attrs}>" + "".join(_outer(k) for k in node.kids) + f"</{node.tag}>"


def _escape(text: str) -> str:
    """Plain text as Markdown that shows it as it is."""
    text = text.replace("\\", "\\\\").replace("`", "\\`").replace("*", "\\*").replace("$", "\\$")
    text = re.sub(r"(?<![0-9A-Za-z])_|_(?![0-9A-Za-z])", r"\\_", text)
    text = re.sub(r"<(?=[A-Za-z/!?])", r"\\<", text)
    text = re.sub(r"&(?=#?[0-9A-Za-z]+;)", r"\\&", text)
    return text.replace("~~", "\\~\\~")


def _wrap(inner: str, mark: str) -> str:
    """``inner`` emphasised by ``mark``, its spaces outside the marks."""
    core = inner.strip()
    if not core:
        return inner
    if core.startswith(mark) and core.endswith(mark):
        return inner  # <b><b>x</b></b>
    lead = inner[:len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()):]
    return f"{lead}{mark}{core}{mark}{trail}"


def _code_span(text: str) -> str:
    text = _SPACE.sub(" ", text)
    if not text.strip():
        return ""
    ticks = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def _fence(text: str, lang: str = "") -> str:
    text = text.replace("\r\n", "\n").strip("\n")
    ticks = "`" * max(3, max((len(run) for run in re.findall(r"^`+", text, re.M)), default=0) + 1)
    lang = re.sub(r"[^\w+#.-]", "", lang)[:30]
    return f"{ticks}{lang}\n{text}\n{ticks}"


def _formula(tex: str, display: bool) -> str:
    tex = (tex or "").strip()
    # Wikipedia's alt text wraps the formula in a style switch.
    m = re.fullmatch(r"\{\\(?:display|text)style\s*(.*)\}", tex, re.S)
    if m:
        tex = m.group(1).strip()
    if not tex:
        return ""
    if display:
        return f"$$\n{tex}\n$$"
    return f"${_SPACE.sub(' ', tex)}$"


def _indent(block: str, pad: str) -> str:
    return "\n".join(pad + line if line else line for line in block.split("\n"))


class _Markdown:
    def __init__(self, base_url: str, images: dict):
        self.base = base_url or ""
        self.images = {k: v for k, v in (images or {}).items()
                       if isinstance(k, str) and isinstance(v, str) and _STORED.fullmatch(v)}
        self.stored: set[str] = set()
        self._blocky: dict[int, bool] = {}

    # --- what an element is ---------------------------------------------

    def _math_display(self, node) -> bool | None:
        """For a formula, whether it is a display one (None: no formula)."""
        if node.tag == "span" and "data-gamma-math" in node.attrs:
            return node.attrs["data-gamma-math"] == "display"
        if node.tag == "math":
            return node.attrs.get("display") == "block"
        return None

    def _is_block(self, node) -> bool:
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
                                        for k in node.kids)
        return self._blocky[key]

    # --- addresses ----------------------------------------------------------

    def _url(self, raw: str, *, link: bool = False) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        try:
            url = urljoin(self.base, raw)
            scheme = urlsplit(url).scheme.lower()
        except ValueError:
            return ""
        if link and scheme == "mailto":
            return url.replace(" ", "%20")
        if scheme not in ("http", "https"):
            return ""
        if link and self.base and raw.startswith("#") or (
                link and urldefrag(url)[1] and urldefrag(url)[0] == urldefrag(self.base)[0]):
            return ""  # an anchor into the page itself
        return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")

    # --- inline ---------------------------------------------------------------

    def inline(self, kids) -> str:
        return "".join(self._inline(k) for k in kids)

    def _inline(self, node) -> str:
        if isinstance(node, str):
            return _escape(_SPACE.sub(" ", node))
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
                not isinstance(k, str) and k.tag in ("a", "img") for k in node.kids):
            return _code_span(_text_of(node))
        inner = self.inline(node.kids)
        if tag in ("strong", "b"):
            return _wrap(inner, "**")
        if tag in ("em", "i", "cite", "dfn"):
            return _wrap(inner, "*")
        if tag in ("del", "s", "strike"):
            return _wrap(inner, "~~")
        if tag in ("sup", "sub") and inner.strip():
            return f"<{tag}>{inner.strip()}</{tag}>"
        if tag == "a":
            return self.link(node, inner)
        if tag == "q":
            return f"“{inner}”"
        return inner

    def link(self, node, inner: str) -> str:
        href = self._url(node.attrs.get("href", ""), link=True)
        text = _SPACE.sub(" ", inner).strip()
        if not href or not text or text.startswith("!["):
            return inner  # an anchor, an empty link, a picture's link (the picture stays)
        lead = inner[:len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()):]
        text = text.replace("]", "\\]")
        return f"{lead}[{text}]({href}){trail}"

    def image(self, node) -> str:
        src = (node.attrs.get("src") or "").strip()
        if any(node.attrs.get(side, "").strip() in ("0", "1", "2") for side in ("width", "height")):
            return ""  # a tracking pixel
        url = self.images.get(src, "")
        if url:
            self.stored.add(url.rsplit("/", 1)[1])
        else:
            url = self._url(src)
        if not url:
            return ""
        alt = _SPACE.sub(" ", node.attrs.get("alt", "")).strip().replace("[", "\\[").replace("]", "\\]")
        return f"![{alt}]({url})"

    def math(self, node) -> str:
        display = bool(self._math_display(node))
        if node.tag == "math":
            tex = next((_text_of(k) for k in _walk(node) if not isinstance(k, str) and k.tag == "annotation"
                        and k.attrs.get("encoding") == "application/x-tex"), "")
            tex = tex or node.attrs.get("alttext", "") or mathml.to_latex(_outer(node))
        else:
            tex = node.attrs.get("data-tex", "")
            if not tex.strip() and node.attrs.get("data-mathml"):
                tex = mathml.to_latex(node.attrs["data-mathml"])
            tex = tex or _text_of(node)
        return _formula(tex, display)

    # --- blocks ---------------------------------------------------------------

    def blocks(self, kids) -> list[str]:
        """The Markdown blocks of a run of nodes: block children are their own,
        the inline ones between them gather into paragraphs."""
        out: list[str] = []
        run: list[str] = []

        def flush():
            text = _paragraph("".join(run))
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

    def block(self, node) -> list[str]:
        tag = node.tag
        if self._math_display(node) is not None:
            formula = self.math(node)
            return [formula] if formula else []
        if tag in _HEADING:
            text = _SPACE.sub(" ", self.inline(node.kids)).strip()
            return [f"{'#' * _HEADING[tag]} {text}"] if text else []
        if tag in ("ul", "ol"):
            listed = self.listing(node)
            return [listed] if listed else []
        if tag == "blockquote":
            inner = "\n\n".join(self.blocks(node.kids))
            return ["\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))] if inner else []
        if tag == "pre":
            text = _text_of(node)
            lang = node.attrs.get("data-lang", "")
            return [_fence(text, lang)] if text.strip() else []
        if tag == "hr":
            return ["---"]
        if tag == "table":
            return self.table(node)
        if tag in ("dt", "summary", "legend"):
            text = _paragraph(self.inline(node.kids))
            return [f"**{text}**" if "\n" not in text else text] if text else []
        if tag in ("figcaption", "caption"):
            text = _paragraph(self.inline(node.kids))
            return [f"*{text}*" if "\n" not in text and not text.startswith("*") else text] if text else []
        if tag in ("iframe", "video"):
            src = self._url(node.attrs.get("src", "") or next(
                (k.attrs.get("src", "") for k in node.kids if not isinstance(k, str) and k.tag == "source"), ""))
            if not src:
                return []
            host = urlsplit(src).netloc or src
            return [f"[{'Video' if tag == 'video' else 'Embedded'}: {_escape(host)}]({src})"]
        return self.blocks(node.kids)

    def listing(self, node) -> str:
        ordered = node.tag == "ol"
        try:
            number = int(node.attrs.get("start", "1"))
        except ValueError:
            number = 1
        items = []
        for kid in node.kids:
            if isinstance(kid, str):
                continue
            if kid.tag in ("ul", "ol"):  # a list right inside a list (no <li>): under the item before it
                nested = self.listing(kid)
                if nested and items:
                    items[-1] += "\n" + _indent(nested, "  ")
                elif nested:
                    items.append(nested)
                continue
            if kid.tag != "li":
                continue
            marker = f"{number}." if ordered else "-"
            number += 1
            parts = self.blocks(kid.kids)
            if not parts:
                continue
            pad = " " * (len(marker) + 1)
            first, rest = parts[0], parts[1:]
            item = marker + " " + _indent(first, pad)[len(pad):]
            for part in rest:
                # A list goes right under its item; anything else after a
                # blank line, a child block of the item.
                sep = "\n" if re.match(r"(?:[-+*]|\d{1,9}[.)]) ", part) else "\n\n"
                item += sep + _indent(part, pad)
            items.append(item)
        return "\n".join(items)

    def table(self, node) -> list[str]:
        rows = [r for r in _walk(node) if not isinstance(r, str) and r.tag == "tr"
                and _nearest_table(node, r)]
        cells = [[c for c in r.kids if not isinstance(c, str) and c.tag in ("td", "th")] for r in rows]
        cells = [r for r in cells if r]
        layout = not cells or any(self._is_block(k) for row in cells for c in row for k in c.kids
                                  if not isinstance(k, str) and k.tag not in _SKIP)
        caption = next((k for k in node.kids if not isinstance(k, str) and k.tag == "caption"), None)
        head = self.block(caption) if caption else []
        if layout:
            return head + [b for row in cells for c in row for b in self.blocks(c.kids)]
        width = max(len(r) for r in cells)
        lines = []
        for row in cells:
            texts = [_SPACE.sub(" ", self.inline(c.kids).replace("\n", " ")).strip().replace("|", "\\|")
                     for c in row]
            texts += [""] * (width - len(texts))
            lines.append("| " + " | ".join(texts) + " |")
            if len(lines) == 1:
                lines.append("|" + "|".join(" --- " for _ in range(width)) + "|")
        return head + ["\n".join(lines)]


def _walk(node):
    for kid in node.kids:
        yield kid
        if not isinstance(kid, str):
            yield from _walk(kid)


def _nearest_table(table, row) -> bool:
    """Whether ``row`` belongs to ``table`` and not to a table inside it."""
    def find(node, inner):
        for kid in node.kids:
            if isinstance(kid, str):
                continue
            if kid is row:
                return not inner
            found = find(kid, inner or kid.tag == "table")
            if found is not None:
                return found
        return None
    return bool(find(table, False))


def _paragraph(text: str) -> str:
    """A paragraph's text: each line trimmed (four spaces would make code),
    blank lines at its ends gone, and a line Markdown would read as markup
    escaped."""
    lines = [line.strip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(_unlead(line) for line in lines)


def _unlead(line: str) -> str:
    if not _LEAD.match(line):
        return line
    number = re.match(r"\d+", line)
    # A number's escape goes before its dot: "1\." (a backslash before a
    # digit is a backslash).
    return f"{number.group(0)}\\{line[number.end():]}" if number else "\\" + line


def to_markdown(html: str, base_url: str = "", images: dict | None = None) -> tuple[str, set[str]]:
    """The clipped fragment ``html`` as Markdown, read against the page's
    address ``base_url``; ``images`` maps a picture's address as the page had
    it to its uploaded copy (``/api/uploads/<name>``). → ``(markdown, the
    names of the uploads it shows)``."""
    md = _Markdown(base_url, images or {})
    blocks = md.blocks(_parse(html or "").kids)
    return "\n\n".join(blocks), md.stored


def source_line(url: str, site: str = "", byline: str = "", published: str = "") -> str:
    """The first block of a clipped article: where it came from, by whom and
    when — ``[site](url) · byline · date``."""
    url = (url or "").strip()
    host = urlsplit(url).netloc if url else ""
    name = _escape(_SPACE.sub(" ", site or host).strip()).replace("]", "\\]")
    parts = [f"[{name}]({url.replace(' ', '%20').replace('(', '%28').replace(')', '%29')})" if url and name else ""]
    parts.append(_escape(_SPACE.sub(" ", byline or "").strip()[:200]))
    date = re.match(r"\d{4}-\d{2}-\d{2}", (published or "").strip())
    parts.append(date.group(0) if date else "")
    return " · ".join(p for p in parts if p)


def article_blocks(html: str, *, base_url: str = "", images: dict | None = None, title: str = "",
                   site: str = "", byline: str = "", published: str = "") -> tuple[list, set[str]]:
    """A clipped page's block tree (``md_to_blocks``) — its source line,
    then its content, a first heading that only repeats the title left out —
    and the uploads it shows. No content gives no blocks."""
    markdown, stored = to_markdown(html, base_url, images)
    if not markdown.strip():
        return [], set()
    first = re.match(r"#{1,6} (.*)(?:\n\n|$)", markdown)
    if first and title and _norm(first.group(1)) == _norm(title):
        markdown = markdown[first.end():]
    tree = md_to_blocks(markdown)
    line = source_line(base_url, site, byline, published)
    return ([{"content": line}] if line and tree else []) + tree, stored


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.replace("\\", "")).strip().lower()
