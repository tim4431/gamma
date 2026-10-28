"""Zotero notes as block trees, both ways.

A Zotero note is HTML, a Gamma note a block subtree whose contents are
markdown. ``note_tree`` reads a note into one subtree: the note's first block
element is the root, each element after it one more child of the root, and a
list's items children too, each with its nested lists as its own children.
``note_html`` writes a subtree back: the root as its element, its children as
a list. The two are each other's exact inverse for every tree ``note_tree``
returns, so a note that goes out to Zotero and comes back is the note it was.

The export reads a block's text with the inline grammar the PDF writers use
(``note_markup.INLINE_RE``), and the import writes only markdown that grammar
reads back as written: literal markdown characters are escaped, a web address
in text is the link Gamma reads it as, a mark never opens or closes on a
space, what a construct cannot hold goes beside it (a backtick next to its
code, an asterisk out of its italic run, a "]" out of its link label), and an
italic run that fits neither ``*`` nor ``_`` keeps its text without the
italic.
"""

import base64
import binascii
import html as html_mod
import re
import urllib.parse
from html.parser import HTMLParser

from .note_markup import BARE_URL, INLINE_RE, LINK_PARTS, LINK_TARGET, LITERAL_SPANS

MARK_STYLE = "background-color: #ffd40080"  # Gamma's ==mark== in a Zotero note


# --- colored runs --------------------------------------------------------------

# A color value a style may carry; anything else (url(), expressions) is dropped.
_COLOR_VALUE_RE = re.compile(r"#[0-9a-f]{3,8}|[a-z]+|(?:rgba?|hsla?)\([0-9.,%\s/+-]*\)")


def _style(css: str) -> dict:
    """A run's inline CSS → ``{"color": v, "background": v}`` (either absent),
    each a plain color value. Gamma's editor writes ``background``, Zotero's
    ``background-color``; both are the background."""
    out = {}
    for decl in (css or "").split(";"):
        key, _, value = decl.partition(":")
        key, value = key.strip().lower(), " ".join(value.split()).lower()
        key = "background" if key == "background-color" else key
        if key in ("color", "background") and _COLOR_VALUE_RE.fullmatch(value):
            out[key] = value
    return out


_MARK = _style(MARK_STYLE)


def _zotero_css(style: dict) -> str:
    return "; ".join(f"{'background-color' if k == 'background' else k}: {v}"
                     for k, v in sorted(style.items(), key=lambda kv: kv[0] != "color"))


def _gamma_css(style) -> str:
    """The style as Gamma's editor writes it (mdMarks.js styleCss)."""
    return "; ".join(f"{k}:{v}" for k, v in style)


# --- export: block markdown → note HTML ----------------------------------------

# A markdown image (Obsidian "|300" size in the alt, or the legacy Logseq
# suffix), or a span INLINE_RE reads as written, in which nothing is an
# image ("\![a](b)" is a link, "`![a](b)`" code): images are found with the
# group "src" set.
_IMAGE_RE = re.compile(
    LITERAL_SPANS
    + r"|!\[(?P<alt>[^\]\n]*)\]\(\s*(?P<src>" + LINK_TARGET + r")(?:[ \t][^)\n]*)?\)"
    r"(?:\{:width\s+(?P<lw>\d+)\})?")
# The display width Gamma keeps in an image's alt ("fig|300", mdMarks.js
# ALT_WIDTH_RE), which a note's <img> holds as its width attribute.
_ALT_WIDTH_RE = re.compile(r"(.*?)\|(\d+)(?:x\d+)?", re.S)
# What an alt read from HTML drops: the characters that would end it or
# read as markup around it.
_ALT_DROP_RE = re.compile(r"[\[\]\\`$*~=<>]")
_DATA_IMAGE_RE = re.compile(LITERAL_SPANS + r"|!\[(?P<alt>[^\]\n]*)\]\((?P<src>data:[^)\s]*)\)")
# Gamma's own uploads, by the names the server writes (markdown_export.UPLOAD_RE's).
_UPLOAD_SRC_RE = re.compile(r"/api/uploads/([0-9a-fA-F]+\.[A-Za-z0-9]+)")
_PLACEHOLDER_RE = re.compile("\x00(\\d+)\x01")
_ORDERED_RE = re.compile(r"(\d{1,9})[.)](?:[ \t]+|$)")
# A fence opening a code block (a backtick fence's info string holds no backtick).
_FENCE_RE = re.compile(r"[ \t]{0,3}(?:(`{3,})[^`\n]*|(~{3,})[^\n]*)")
_HEADING_RE = re.compile(r"[ \t]{0,3}(#{1,6})[ \t]+(.*)")
_QUOTE_RE = re.compile(r"[ \t]{0,3}> ?")
_RULE_RE = re.compile(r"[ \t]{0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*")
_DELIM_CELL_RE = re.compile(r":?-+:?")
# What a wrapping construct's text sits between: (opening length, closing length).
_WRAPS = {"bold": (2, 2), "italic": (1, 1), "strike": (2, 2), "mark": (2, 2), "underline": (3, 4)}
_WRAP_TAGS = {"bold": ("<strong>", "</strong>"), "italic": ("<em>", "</em>"), "strike": ("<s>", "</s>"),
              "mark": (f'<span style="{MARK_STYLE}">', "</span>"), "underline": ("<u>", "</u>")}


def _esc(text: str) -> str:
    return html_mod.escape(text, quote=False)


def _attr(text: str) -> str:
    return html_mod.escape(_PLACEHOLDER_RE.sub("", text), quote=True)


# Whitespace HTML collapses; a no-break space and its kin are text.
_HTML_WS = " \t\n\f"


def _content(node: dict) -> str:
    return (node.get("content") or "").replace("\r\n", "\n").replace("\r", "\n").strip(_HTML_WS)


def _empty(node: dict) -> bool:
    return not _content(node) and all(_empty(c) for c in node.get("children") or [])


def _cells(line: str) -> list[str]:
    """A GFM row → its cells (outer pipes dropped, ``\\|`` a literal pipe), as
    ``pdf_document`` splits them."""
    s = line.strip(_HTML_WS)
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip(_HTML_WS).replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _math_end(lines: list[str], i: int):
    """The last line of the display math opening on line ``i``, or None when
    the line is not display math (text after its closing ``$$`` makes it a
    paragraph, and so does a ``$$`` nothing closes)."""
    rest = lines[i].strip()[2:]
    if "$$" in rest:
        return i if rest.index("$$") == len(rest) - 2 and rest[:-2].strip() else None
    for j in range(i + 1, len(lines)):
        if "$$" in lines[j]:
            line = lines[j].rstrip()
            return j if line.endswith("$$") and line.count("$$") == 1 else None
    return None


def _element_at(lines: list[str], i: int):
    """The block element other than a paragraph that starts on line ``i``, and
    the line after it, or None."""
    line = lines[i]
    fence = _FENCE_RE.fullmatch(line)
    if fence:
        mark = fence.group(1) or fence.group(2)
        closing = re.compile(rf"[ \t]{{0,3}}{re.escape(mark[0])}{{{len(mark)},}}[ \t]*")
        j = i + 1
        while j < len(lines) and not closing.fullmatch(lines[j]):
            j += 1
        return ("code", "\n".join(lines[i + 1:j])), j + 1
    if line.strip().startswith("$$"):
        end = _math_end(lines, i)
        if end is not None:
            return ("math", "\n".join(lines[i:end + 1]).strip()), end + 1
    heading = _HEADING_RE.fullmatch(line)
    if heading:
        return ("heading", len(heading.group(1)), heading.group(2)), i + 1
    if _QUOTE_RE.match(line):
        j = i
        while j < len(lines) and _QUOTE_RE.match(lines[j]):
            j += 1
        return ("quote", "\n".join(_QUOTE_RE.sub("", l, count=1) for l in lines[i:j])), j
    if _RULE_RE.fullmatch(line):
        return ("rule",), i + 1
    # A table's delimiter row holds a pipe here, so "a | b⏎---" (a line, then
    # a rule, in one list item) stays a line and a rule.
    if "|" in line and i + 1 < len(lines) and "-" in lines[i + 1] and "|" in lines[i + 1]:
        head, delim = _cells(line), _cells(lines[i + 1])
        if len(head) == len(delim) and all(_DELIM_CELL_RE.fullmatch(c) for c in delim):
            rows, j = [head], i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append(_cells(lines[j]))
                j += 1
            return ("table", rows), j
    return None


def _elements(content: str) -> list[tuple]:
    """A block's markdown → its block elements in order: ``("code", text)``,
    ``("math", source)``, ``("heading", level, text)``, ``("quote", text)``,
    ``("rule",)``, ``("table", rows)`` and ``("para", text)``. A paragraph runs,
    blank lines and all, until a line that starts another element."""
    lines = content.split("\n")
    out, para, i = [], [], 0

    def flush():
        text = "\n".join(para).strip("\n")
        if text.strip(_HTML_WS):
            out.append(("para", text))
        para.clear()

    while i < len(lines):
        found = _element_at(lines, i)
        if found:
            flush()
            out.append(found[0])
            i = found[1]
        else:
            para.append(lines[i])
            i += 1
    flush()
    return out


def _inner(which: str, body: str) -> str:
    """The text inside a wrapping construct INLINE_RE matched."""
    if which == "color":
        return body[body.index('">') + 2:-7]
    opens, closes = _WRAPS[which]
    return body[opens:-closes]


def _ref_text(name: str, resolve_ref) -> str:
    """What a [[ref]] or ![[embed]] reads as outside Gamma: the referenced
    block's first line as plain text, or the raw name when it is unknown."""
    ref = resolve_ref(name) if resolve_ref else None
    first = ((ref or {}).get("content") or "").strip().split("\n")[0]
    return title_text(first).replace("\x00", "") or name


def _spans_html(text: str, resolve_ref, in_link: bool = False) -> str:
    """Inline markdown (images already taken out) → note HTML; inside a
    link's label (``in_link``) a web address is text."""
    out, pos = [], 0
    for m in INLINE_RE.finditer(text):
        out.append(_esc(text[pos:m.start()]).replace("\n", "<br/>"))
        pos = m.end()
        which = m.lastgroup
        body = m.group(which)
        if which == "code":
            out.append(f"<code>{_esc(body.strip('`'))}</code>")
        elif which == "dmath":
            out.append(f'<span class="math">{_esc(body)}</span>')
        elif which == "math":
            tex = body if body[0] == "$" else f"${body[2:-2]}$"
            out.append(f'<span class="math">{_esc(tex)}</span>')
        elif which == "esc":
            out.append(_esc(body[1]))
        elif which == "color":
            style = _style(body[len('<span style="'):body.index('">')])
            inner = _spans_html(_inner(which, body), resolve_ref, in_link)
            out.append(f'<span style="{_zotero_css(style)}">{inner}</span>' if style else inner)
        elif which in _WRAP_TAGS:
            opening, closing = _WRAP_TAGS[which]
            out.append(opening + _spans_html(_inner(which, body), resolve_ref, in_link) + closing)
        elif which in ("embed", "ref"):
            out.append(_esc(_ref_text(body.strip("![]").split("|")[0].strip(), resolve_ref)))
        elif which == "link":
            label, href = LINK_PARTS.match(body).groups()
            out.append(f'<a href="{_attr(href)}">{_spans_html(label or href, resolve_ref, True)}</a>')
        elif in_link:
            out.append(_esc(body))
        else:                                    # bare URL
            out.append(f'<a href="{_attr(body)}">{_esc(body)}</a>')
    out.append(_esc(text[pos:]).replace("\n", "<br/>"))
    return "".join(out)


def _image_html(src: str, alt: str, width, resolve_image) -> str:
    """An image as a note's <img>, its alt and Gamma's display width
    ("fig|300", or the legacy "{:width 300}") as attributes."""
    size = _ALT_WIDTH_RE.fullmatch(alt or "")
    if size:
        alt, width = size.group(1), size.group(2)
    attrs = (f' alt="{_attr(alt)}"' if alt else "") + (f' width="{int(width)}"' if width else "")
    upload = _UPLOAD_SRC_RE.fullmatch(src)
    if upload:
        data = resolve_image(upload.group(1)) if resolve_image else None
        if data:
            mime, b64 = data
            return f'<img src="data:{mime};base64,{b64}"{attrs}/>'
        return _esc(f"(image: {upload.group(1)})")
    if src.startswith(("http://", "https://", "data:")):
        return f'<img src="{_attr(src)}"{attrs}/>'
    return _esc(f"(image: {src})")


class _Render:
    """One note's renderer: the callbacks every block's text goes through."""

    def __init__(self, resolve_image, resolve_ref):
        self.resolve_image, self.resolve_ref = resolve_image, resolve_ref

    def inline(self, text: str) -> str:
        """Inline markdown → HTML. Images come out first, so a mark or a link
        around one still reads as one; each is put back as its <img>."""
        images = []

        def take(m):
            if m.group("src") is None:               # a literal span, left for INLINE_RE
                return m.group()
            images.append(_image_html(m.group("src"), m.group("alt"), m.group("lw"), self.resolve_image))
            return f"\x00{len(images) - 1}\x01"

        text = _IMAGE_RE.sub(take, text.replace("\x00", "").replace("\x01", ""))
        return _PLACEHOLDER_RE.sub(lambda m: images[int(m.group(1))], _spans_html(text, self.resolve_ref))

    def element(self, el: tuple, bare: bool) -> str:
        """One block element as HTML; a paragraph is ``bare`` inline HTML
        when it is a list item's only element."""
        kind = el[0]
        if kind == "code":
            # HTML drops a newline right after <pre>, so a leading one is doubled.
            lead = "\n" if el[1].startswith("\n") else ""
            return f"<pre>{lead}{_esc(el[1])}</pre>"
        if kind == "math":
            return f'<pre class="math">{_esc(el[1])}</pre>'
        if kind == "heading":
            return f"<h{el[1]}>{self.inline(el[2])}</h{el[1]}>"
        if kind == "quote":
            return f"<blockquote><p>{self.inline(el[1])}</p></blockquote>"
        if kind == "rule":
            return "<hr/>"
        if kind == "table":
            head, *rows = el[1]
            return ("<table><tr>" + "".join(f"<th>{self.inline(c)}</th>" for c in head) + "</tr>"
                    + "".join("<tr>" + "".join(f"<td>{self.inline(c)}</td>" for c in row) + "</tr>"
                              for row in rows) + "</table>")
        return self.inline(el[1]) if bare else f"<p>{self.inline(el[1])}</p>"

    def item(self, content: str) -> str:
        elements = _elements(content)
        bare = len(elements) == 1
        return "".join(self.element(el, bare) for el in elements)

    def lists(self, nodes: list[dict]) -> str:
        """Sibling blocks as list runs: a ``<ul>``, or an ``<ol>`` for a run of
        siblings numbered on from each other (``N.`` or ``N)``, the number
        taken off), each item's children a nested run."""
        out, tag, last = [], None, None
        for node in nodes:
            if _empty(node):
                continue
            content = _content(node)
            ordered = _ORDERED_RE.match(content)
            number = int(ordered.group(1)) if ordered else None
            want = "ol" if ordered else "ul"
            if want != tag or (ordered and number != last + 1):
                if tag:
                    out.append(f"</{tag}>")
                out.append(f'<ol start="{number}">' if ordered and number != 1 else f"<{want}>")
                tag = want
            last = number
            out.append("<li>" + self.item(content[ordered.end():] if ordered else content)
                       + self.lists(node.get("children") or []) + "</li>")
        if tag:
            out.append(f"</{tag}>")
        return "".join(out)


def note_html(node: dict, resolve_image=None, resolve_ref=None) -> str:
    """A block subtree (a ``build_tree`` node: ``content`` and ``children``;
    other keys are ignored) → one Zotero note's HTML: the root as its block
    element, its children as a list after it. A root that is a numbered item
    or holds several elements is the list's first item instead, which is how
    ``note_tree`` reads it back. ``resolve_image(filename) -> (mime, base64) |
    None`` embeds pasted uploads as data URIs (a "(image: <name>)" placeholder
    when it gives none); ``resolve_ref(id) -> {content, ...} | None`` reads a
    [[ref]] as the text of the block it names. The exact inverse of
    ``note_tree`` for every tree that returns."""
    render = _Render(resolve_image, resolve_ref)
    content = _content(node)
    children = node.get("children") or []
    if not content:
        return render.lists(children)
    elements = _elements(content)
    if len(elements) == 1 and not _ORDERED_RE.match(content):
        return render.element(elements[0], bare=False) + render.lists(children)
    return render.lists([{"content": content}] + list(children))


# --- import: note HTML → block markdown ----------------------------------------

_VOID = {"br", "img", "hr", "input", "meta", "link", "col", "area", "base", "wbr", "source",
         "embed", "param", "track"}
_HIDDEN = {"script", "style", "head", "title"}
_BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "ul", "ol", "li",
           "table", "hr"}
# Containers whose children stand in their place (Zotero wraps a note in a
# <div data-schema-version>).
_TRANSPARENT = {"html", "body", "div"}
# Elements that break the text when they turn up inside a paragraph (invalid
# HTML, or a table read as text).
_LINE_BREAKERS = _BLOCKS | _TRANSPARENT | {"tr", "td", "th", "thead", "tbody", "tfoot", "dt", "dd"}
_TAG_MARKS = {"strong": "bold", "b": "bold", "em": "italic", "i": "italic", "u": "underline",
              "s": "strike", "strike": "strike", "del": "strike"}
# The order marks nest in, outermost first: a run's marks always open in this
# order, so bold holds italic and never the other way round.
_ORDER = ("link", "color", "mark", "underline", "strike", "bold", "italic")
_WS_RE = re.compile(r"[ \t\n\r\f]+")              # HTML's whitespace (not &nbsp;)
_BARE_URL_RE = re.compile(BARE_URL)                 # INLINE_RE's url
_URL_START_RE = re.compile(r"https?:(/)/")
_ENTITY_RE = re.compile(r"&#?[0-9A-Za-z]+;")
_EDGE_RE = re.compile(r"[\s\\]+$")              # what a mark may not close on
# A line start that would read as a heading, quote or list item (the
# character to escape is the group that matched) ...
_LINE_START_RE = re.compile(r"([#>]|[-+](?=\s))|\d{1,9}([.)])(?=\s)")
# ... and a whole line that would read as a list item or a rule, or
# underline the line above as a heading.
_WHOLE_LINE_RE = re.compile(r"(\+|-+|=+)[ \t]*|\d{1,9}([.)])[ \t]*")


class _El:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict):
        self.tag, self.attrs, self.children = tag, attrs, []


class _Parser(HTMLParser):
    """Note HTML → a tree of ``_El`` and text, forgiving as a browser is about
    an unclosed <p> or <li>; scripts, styles and the head are left out."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _El("", {})
        self.stack = [self.root]
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if self.hidden:
            self.hidden += 0 if tag in _VOID else 1
            return
        if tag in _HIDDEN:
            self.hidden = 1
            return
        if self.stack[-1].tag == "p" and tag in _BLOCKS | _TRANSPARENT:
            self.stack.pop()
        if tag == "li":
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag in ("ul", "ol"):
                    break
                if self.stack[i].tag == "li":
                    del self.stack[i:]
                    break
        el = _El(tag, dict(attrs))
        self.stack[-1].children.append(el)
        if tag not in _VOID:
            self.stack.append(el)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.hidden:
            self.hidden -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self.hidden:
            return
        kids = self.stack[-1].children
        if kids and isinstance(kids[-1], str):
            kids[-1] += data
        else:
            kids.append(data)


def _parse(html: str) -> _El:
    parser = _Parser()
    parser.feed(html or "")
    parser.close()
    return parser.root


def _tag(node) -> str | None:
    return None if isinstance(node, str) else node.tag


def _text(node) -> str:
    """Every bit of text under a node, a <br> a line break."""
    if isinstance(node, str):
        return node
    if node.tag == "br":
        return "\n"
    return "".join(_text(c) for c in node.children)


def _classes(el: _El) -> list[str]:
    return (el.attrs.get("class") or "").split()


def _flow(nodes) -> list[tuple]:
    """Children → their block elements in order, as ``("el", element)``, and
    the loose inline content between them, as ``("inline", [nodes])``; a
    transparent container's children stand in its place."""
    out, loose = [], []
    for node in nodes:
        tag = _tag(node)
        if tag in _TRANSPARENT or tag in _BLOCKS:
            if loose:
                out.append(("inline", loose))
                loose = []
            out.extend(_flow(node.children) if tag in _TRANSPARENT else [("el", node)])
        else:
            loose.append(node)
    if loose:
        out.append(("inline", loose))
    return out


def _url_md(url: str) -> str:
    """An address as markdown holds it, percent-encoding spaces and
    parentheses (which would end it) and backticks and dollars: only code and
    math hold those raw, so the export's image pass, which skips code and
    math (``_IMAGE_RE``), reads every other one as INLINE_RE does."""
    for ch, code in ((" ", "%20"), ("(", "%28"), (")", "%29"), ("`", "%60"), ("$", "%24")):
        url = url.replace(ch, code)
    return url


def _href(value) -> str:
    """A link's target as markdown holds it; "" for none or a script URL."""
    href = re.sub(r"[\t\n\r]", "", value or "").strip()
    if not href or re.match(r"(?i)(?:javascript|vbscript|data):", href):
        return ""
    return _url_md(href)


def _image_md(attrs: dict) -> tuple:
    """An <img> as a leaf: its picture for a data: or web source, else
    Zotero's embedded-image placeholder (a ``data-attachment-key`` image
    whose file travels as an attachment)."""
    src = (attrs.get("src") or "").strip()
    if src.startswith("data:"):
        src = re.sub(r"\s", "", src)
    elif not src.startswith(("http://", "https://")):
        return ("text", "(image)")
    alt = " ".join(_ALT_DROP_RE.sub("", attrs.get("alt") or "").split())
    if _ALT_WIDTH_RE.fullmatch(alt):             # an alt ending "|300" is no width
        alt = alt.replace("|", " ")
    width = (attrs.get("width") or "").strip()
    if width.isdigit() and int(width):
        alt += f"|{int(width)}"
    return ("img", f"![{alt}]({_url_md(src)})")


def _math_md(text: str) -> str | None:
    """A math span's text as Gamma's inline math: it carries its own dollars
    in Zotero (``$x$``, ``$$x$$``), bare TeX gets single ones. None when
    there is no TeX in it, or a "$" no math can hold."""
    tex = " ".join(text.split())
    dollars = 2 if tex.startswith("$$") and tex.endswith("$$") and len(tex) > 4 else (
        1 if tex.startswith("$") and tex.endswith("$") and len(tex) > 2 else 0)
    inner = tex[dollars:len(tex) - dollars].strip()
    return "$" * max(dollars, 1) + inner + "$" * max(dollars, 1) if inner and "$" not in inner else None


def _span_marks(css: str, marks: dict) -> dict:
    """The marks a <span>'s style adds: Gamma's highlight for exactly
    MARK_STYLE, else its color, merged into an enclosing one; Zotero writes
    strike-through as a text-decoration."""
    style, marks = _style(css), dict(marks)
    if style == _MARK:
        marks["mark"] = True
    elif style:
        merged = {**dict(marks.get("color", ())), **style}
        marks["color"] = tuple((k, merged[k]) for k in ("color", "background") if k in merged)
    decoration = re.search(r"text-decoration(?:-line)?\s*:([^;]*)", css or "", re.I)
    if decoration and "line-through" in decoration.group(1).lower():
        marks["strike"] = True
    if decoration and "underline" in decoration.group(1).lower():
        marks["underline"] = True
    return marks


def _leaves(nodes, marks: dict, out: list, one_line: bool):
    """Inline HTML → leaves: ``(kind, text, marks)`` for "text", "code",
    "math" and "img", ``("br",)`` for a line break (a space when
    ``one_line``)."""
    for node in nodes:
        tag = _tag(node)
        if tag is None:
            out.append(("text", node, marks))
        elif tag == "br":
            out.append(("text", " ", marks) if one_line else ("br",))
        elif tag == "img":
            out.append((*_image_md(node.attrs), marks))
        elif tag == "code":
            out.append(("code", _text(node), marks))
        elif tag == "span" and "math" in _classes(node):
            math = _math_md(_text(node))
            out.append(("math", math, marks) if math else ("text", _text(node), marks))
        elif tag in _TAG_MARKS:
            _leaves(node.children, {**marks, _TAG_MARKS[tag]: True}, out, one_line)
        elif tag == "a":
            href = _href(node.attrs.get("href"))
            _leaves(node.children, {**marks, "link": href} if href else marks, out, one_line)
        elif tag == "span":
            _leaves(node.children, _span_marks(node.attrs.get("style"), marks), out, one_line)
        elif tag in _LINE_BREAKERS:
            brk = ("text", " ", marks) if one_line else ("br",)
            if out and out[-1] != brk:
                out.append(brk)
            _leaves(node.children, marks, out, one_line)
            out.append(brk)
        else:                                    # sub, sup, a citation, …: its text
            _leaves(node.children, marks, out, one_line)


def _collapse(leaves: list) -> list:
    """HTML's whitespace rules over a paragraph: each run of whitespace one
    space, none at a line's start or end."""
    out, space = [], True                    # the text so far ends a line or a space

    def trim_end():
        while out and out[-1][0] == "text":
            text = out[-1][1].rstrip(" ")
            if text:
                out[-1] = ("text", text, out[-1][2])
                return
            out.pop()

    for leaf in leaves:
        if leaf[0] == "br":
            trim_end()
            out.append(leaf)
            space = True
            continue
        kind, text, marks = leaf
        if kind != "img":
            text = _WS_RE.sub(" ", text)
        if kind == "text" and space:
            text = text.lstrip(" ")
        if not text:
            continue
        space = kind == "text" and text.endswith(" ")
        out.append((kind, text, marks))
    trim_end()
    return out


# What a mark's text cannot hold, since INLINE_RE ends the mark at it
# whatever escapes it: the first character of each goes outside the mark.
_TEXT_UNHOLDABLE = {"italic": "*", "link": "]", "underline": "</u>", "color": "</span>"}
# What a code span or math inside a mark cannot hold: such an atom stands
# outside the mark (italic is settled by its delimiter, in _italics).
_ATOM_UNHOLDABLE = {"link": "]", "color": "</span>", "underline": "</u>", "strike": "~~",
                    "mark": "==", "bold": "**"}


def _split(leaves: list) -> list:
    """A web address in text made the link Gamma reads it as, and what a
    construct cannot hold written beside it: a backtick out of its code, an
    asterisk out of its italic run, a "]" out of its link label, an atom out
    of a mark whose closing it holds. Neighbouring text with the same marks
    is merged."""
    out = []

    def add(kind, text, marks):
        if kind == "text" and out and out[-1][0] == "text" and out[-1][2] == marks:
            out[-1] = ("text", out[-1][1] + text, marks)
        elif text:
            out.append((kind, text, marks))

    linked = []
    for leaf in leaves:
        if leaf[0] == "text" and "link" not in leaf[2] and "://" in leaf[1]:
            pos = 0
            for m in _BARE_URL_RE.finditer(leaf[1]):
                linked += [("text", leaf[1][pos:m.start()], leaf[2]),
                           ("text", m.group(), {**leaf[2], "link": _url_md(m.group())})]
                pos = m.end()
            linked.append(("text", leaf[1][pos:], leaf[2]))
        else:
            linked.append(leaf)

    def atom(kind, text, marks):
        shown = f"`{text}`" if kind == "code" else text
        add(kind, text, {k: v for k, v in marks.items()
                         if k not in _ATOM_UNHOLDABLE or _ATOM_UNHOLDABLE[k] not in shown})

    for leaf in linked:
        kind = leaf[0]
        if kind == "br":
            out.append(leaf)
        elif kind == "code":
            for part in re.split(r"(`+)", leaf[1]):
                if part.startswith("`"):
                    add("text", part, leaf[2])
                elif part:
                    atom("code", part, leaf[2])
        elif kind == "math":
            atom(*leaf)
        elif kind == "text":
            text, marks = leaf[1], leaf[2]
            outside = {}                             # position → the marks its character leaves
            for name, closer in _TEXT_UNHOLDABLE.items():
                if name in marks:
                    for m in re.finditer(re.escape(closer), text):
                        outside.setdefault(m.start(), set()).add(name)
            pos = 0
            for at in sorted(outside):
                add("text", text[pos:at], marks)
                add("text", text[at], {k: v for k, v in marks.items() if k not in outside[at]})
                pos = at + 1
            add("text", text[pos:], marks)
        else:
            add(*leaf)
    return out


def _tokens(leaves: list) -> list:
    """Leaves → a flat token list in which each mark opens and closes around
    its run (every mark closes at a line break): ``("open", mark)``,
    ``("close", mark)``, ``(kind, text)`` and ``("br",)``, a mark a
    ``(name, value)`` pair."""
    tokens, stack = [], []
    for leaf in leaves + [("br",)]:
        want = [] if leaf[0] == "br" else [(n, leaf[2][n]) for n in _ORDER if n in leaf[2]]
        k = 0
        while k < min(len(stack), len(want)) and stack[k] == want[k]:
            k += 1
        tokens += [("close", m) for m in reversed(stack[k:])]
        tokens += [("open", m) for m in want[k:]]
        stack = want
        tokens.append(leaf if leaf[0] == "br" else leaf[:2])
    return tokens[:-1]


def _tidy(tokens: list) -> list:
    """Marks off their edges: the spaces after an opening and the spaces (or a
    backslash) before a closing move outside the mark, a mark left empty is
    dropped, and neighbouring text merges."""
    out = []

    def add_text(text, at=None):
        at = len(out) if at is None else at
        if not text:
            return
        if at and out[at - 1][0] == "text":
            out[at - 1] = ("text", out[at - 1][1] + text)
        else:
            out.insert(at, ("text", text))

    for token in tokens:
        kind = token[0]
        if kind == "text":
            text = token[1]
            first = len(out)
            while first and out[first - 1][0] == "open":
                first -= 1
            if first < len(out):
                lead = text[:len(text) - len(text.lstrip())]
                text = text[len(lead):]
                add_text(lead, first)
            add_text(text)
        elif kind == "close":
            trail = ""
            if out and out[-1][0] == "text":
                kept = _EDGE_RE.sub("", out[-1][1])
                trail = out[-1][1][len(kept):]
                if kept:
                    out[-1] = ("text", kept)
                else:
                    out.pop()
            if out and out[-1] == ("open", token[1]):
                out.pop()
            else:
                out.append(token)
            add_text(trail)
        else:
            out.append(token)
    return out


def _bare_urls(tokens: list) -> list:
    """A link whose label is its own web address (in its target, the
    parentheses ``_url_md`` encoded), written as the bare address where
    Gamma's renderer and INLINE_RE both take exactly it: after a line start,
    a space or "(", and before a line end or a space. (A link is the
    outermost mark, so no other mark is open around one.)"""
    out, i = [], 0
    while i < len(tokens):
        token = tokens[i]
        if (token[0] == "open" and token[1][0] == "link" and i + 2 < len(tokens)
                and tokens[i + 1][0] == "text" and tokens[i + 2] == ("close", token[1])
                and tokens[i + 1][1].replace("(", "%28").replace(")", "%29") == token[1][1]
                and _BARE_URL_RE.fullmatch(tokens[i + 1][1])):
            address = tokens[i + 1][1]
            before = tokens[i - 1] if i else ("br",)
            after = tokens[i + 3] if i + 3 < len(tokens) else ("br",)
            if (before[0] == "br" or before[0] == "text" and (before[1][-1].isspace() or before[1][-1] == "(")) and (
                    after[0] == "br" or after[0] == "text" and after[1][0].isspace()):
                out.append(("url", address))
            else:                                # [address](address), the label as written
                out += [token, ("url", address), tokens[i + 2]]
            i += 3
            continue
        out.append(token)
        i += 1
    return out


def _delimiter(token) -> str:
    """A token's markdown, but for text (escaped once its neighbours are
    known) and italic (settled by ``_italics``)."""
    kind = token[0]
    if kind == "code":
        return f"`{token[1]}`"
    if kind in ("math", "img", "url", "italic"):
        return token[1]
    if kind == "br":
        return "\n"
    name, value = token[1]
    if name == "link":
        return "[" if kind == "open" else f"]({value})"
    if name == "color":
        return f'<span style="{_gamma_css(value)}">' if kind == "open" else "</span>"
    return {"bold": "**", "strike": "~~", "mark": "=="}.get(name) or ("<u>" if kind == "open" else "</u>")


def _italics(tokens: list) -> list:
    """Each italic run's delimiter, as an ``("italic", delimiter)`` token:
    "*" unless the run would end right before a "**" (which would close the
    bold on it) or holds a "*"; then "_" where no word touches it and it
    holds no "_"; else the run stays, without italic. Decided on the raw
    text, before anything is escaped next to it."""
    def shown(token):
        return token[1] if token[0] == "text" else _delimiter(token)

    out, dropped, i = list(tokens), set(), 0
    while i < len(out):
        if out[i] == ("open", ("italic", True)):
            close = out.index(("close", ("italic", True)), i)
            body = "".join(shown(t) for t in out[i + 1:close] if t[0] != "img")
            prev = shown(out[i - 1])[-1] if i and out[i - 1][0] != "br" else ""
            nxt = shown(out[close + 1])[0] if close + 1 < len(out) and out[close + 1][0] != "br" else ""
            if out[close + 1:close + 2] != [("close", ("bold", True))] and "*" not in body:
                out[i] = out[close] = ("italic", "*")
            elif "_" not in body and not (prev.isalnum() or prev == "_" or nxt.isalnum() or nxt == "_"):
                out[i] = out[close] = ("italic", "_")
            else:
                dropped |= {i, close}
            i = close
        i += 1
    merged = []
    for k, token in enumerate(out):
        if k in dropped:
            continue
        if token[0] == "text" and merged and merged[-1][0] == "text":
            merged[-1] = ("text", merged[-1][1] + token[1])
        else:
            merged.append(token)
    return merged


def _escape(text: str, prev: str, nxt: str, dollars: bool) -> str:
    """Literal text as markdown that reads as typed, between the characters
    ``prev`` and ``nxt`` of what surrounds it ("" at a line's start or end):
    backslash, ``*``, backtick and brackets always; ``_`` unless inside a
    word; a ``~`` or ``=`` next to another; ``$`` when the line holds two
    (``dollars``); ``<`` before a letter or "/"; ``!`` before "["; ``&`` that
    starts an entity; the first "/" of a web address (text outside a link
    holds none, ``_split`` made each a link; one in a link's label would run
    on past its end); a line start that would read as a heading, quote, list
    item or rule; and a line that would read as a table's delimiter row."""
    escape_at = {m.start(1) for m in _URL_START_RE.finditer(text)}
    if not prev:                                 # at a line's start
        whole_line = not nxt
        m = _LINE_START_RE.match(text) or (whole_line and _WHOLE_LINE_RE.fullmatch(text))
        if m:
            escape_at.add(m.start(1) if m.group(1) else m.start(2))
        if whole_line and "|" in text and "-" in text and all(_DELIM_CELL_RE.fullmatch(c) for c in _cells(text)):
            escape_at.add(text.index("-"))
    out = []
    for i, ch in enumerate(text):
        before = text[i - 1] if i else prev
        after = text[i + 1] if i + 1 < len(text) else nxt
        if (i in escape_at or ch in "\\*`[]"
                or ch == "_" and not (before.isalnum() and after.isalnum())
                or ch in "~=" and ch in (before, after)
                or ch == "$" and dollars
                or ch == "<" and (after.isalpha() or after == "/")
                or ch == "!" and after == "["
                or ch == "&" and _ENTITY_RE.match(text, i)):
            out.append("\\")
        out.append(ch)
    return "".join(out)


def _markdown(leaves: list) -> str:
    """A paragraph's leaves → its markdown."""
    tokens = _italics(_bare_urls(_tidy(_tokens(_split(_collapse(leaves))))))
    outs = [None if t[0] == "text" else _delimiter(t) for t in tokens]

    # Literal "$" is escaped on a line holding two or more (math could pair them).
    dollars, count, start = [False] * len(tokens), 0, 0
    for i, token in enumerate(tokens + [("br",)]):
        if token[0] == "br":
            for j in range(start, i):
                dollars[j] = count >= 2
            count, start = 0, i + 1
        elif token[0] != "img":
            count += (token[1] if token[0] == "text" else outs[i]).count("$")

    for i, token in enumerate(tokens):
        if token[0] == "text":
            prev = outs[i - 1][-1] if i and tokens[i - 1][0] != "br" else ""
            nxt = outs[i + 1][0] if i + 1 < len(tokens) and tokens[i + 1][0] != "br" else ""
            outs[i] = _escape(token[1], prev, nxt, dollars[i])
    md = "".join(outs).strip("\n")
    # A no-break space is text to HTML, but text of nothing else is blank:
    # Zotero 5's editor wrote each empty line as <p>&nbsp;</p>.
    return md if md.strip() else ""


def _inline_md(nodes, one_line: bool = False) -> str:
    """Inline HTML → markdown; ``one_line`` reads a line break as a space
    (a heading, a table cell)."""
    leaves = []
    _leaves(nodes, {}, leaves, one_line)
    return _markdown(leaves)


def _pre_md(el: _El) -> str:
    """A <pre> as a fenced code block, its text verbatim (a fence longer than
    any backtick line in it), or a ``pre.math`` as display math."""
    text = _text(el)
    text = (text[1:] if text.startswith("\n") else text).rstrip("\n")
    if not text.strip():
        return ""
    if "math" in _classes(el):
        tex = text.strip()
        return tex if tex.startswith("$$") and tex.endswith("$$") and len(tex) > 4 else f"$${tex}$$"
    runs = [len(m.group(1)) for m in re.finditer(r"^[ \t]{0,3}(`{3,})", text, re.M)]
    fence = "`" * max([3] + [n + 1 for n in runs])
    return f"{fence}\n{text}\n{fence}"


def _quote_lines(nodes) -> list[str]:
    """A quote's text, line by line: each paragraph-like element its lines,
    nested quotes, lists and tables read as their text (a quote line holds
    inline markdown only)."""
    out = []
    for kind, item in _flow(nodes):
        if kind == "inline":
            md = _inline_md(item)
        elif item.tag == "hr":
            continue
        elif item.tag in ("blockquote", "ul", "ol", "li", "table"):
            out += _quote_lines(item.children)
            continue
        else:
            md = _inline_md(item.children)
        if md:
            out.append(md)
    return out


def _table_md(el: _El) -> str:
    """A <table> as a GFM table: its first row the header, every row as wide
    as the widest, a "|" in a cell escaped."""
    def rows(node):
        for child in node.children:
            if _tag(child) == "tr":
                yield child
            elif _tag(child) in ("thead", "tbody", "tfoot"):
                yield from rows(child)

    table = [[_inline_md(cell.children, one_line=True).replace("|", "\\|")
              for cell in tr.children if _tag(cell) in ("td", "th")] for tr in rows(el)]
    table = [row for row in table if row]
    if not any(any(row) for row in table):
        return ""
    width = max(len(row) for row in table)
    table = [row + [""] * (width - len(row)) for row in table]
    return "\n".join("| " + " | ".join(row) + " |" for row in [table[0], ["---"] * width] + table[1:])


def _block_md(kind: str, item) -> str:
    """One block element (or the loose inline content between elements) →
    its block's markdown."""
    if kind == "inline":
        return _inline_md(item)
    tag = item.tag
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        # A heading is one line in markdown, so its line breaks are spaces.
        text = _inline_md(item.children, one_line=True)
        return f"{'#' * int(tag[1])} {text}" if text else ""
    if tag == "blockquote":
        lines = "\n".join(_quote_lines(item.children)).strip("\n").split("\n")
        return "\n".join(f"> {line}" if line else ">" for line in lines) if any(lines) else ""
    if tag == "pre":
        return _pre_md(item)
    if tag == "table":
        return _table_md(item)
    if tag == "hr":
        return "---"
    if tag == "li":                              # an item outside any list
        node = _item(item.children, None)
        return node["content"] if node else ""
    return _inline_md(item.children)             # p


def _item(nodes, prefix: str | None) -> dict | None:
    """A list item → its node: the block elements before its first nested
    list joined into its content (after ``prefix``, an ordered item's
    ``N.``), its nested lists' items and any element after them its
    children. None for an item with nothing in it."""
    parts, children = [], []
    for kind, item in _flow(nodes):
        if kind == "el" and item.tag in ("ul", "ol"):
            children += _items(item)
            continue
        content = _block_md(kind, item)
        if content and children:
            children.append({"content": content, "children": []})
        elif content:
            parts.append(content)
    content = "\n".join(parts)
    if content and prefix:
        content = f"{prefix} {content}"
    return {"content": content, "children": children} if content or children else None


def _items(list_el: _El) -> list[dict]:
    """A <ul> or <ol> → its items' nodes; an <ol>'s are numbered by position
    from its ``start``. A list straight inside a list (no <li> around it)
    belongs to the item before it."""
    number = None
    if list_el.tag == "ol":
        start = (list_el.attrs.get("start") or "").strip()
        number = int(start) if start.isdigit() and len(start) < 10 else 1
    items = []
    for child in list_el.children:
        tag = _tag(child)
        if tag is None and not child.strip():
            continue
        if tag in ("ul", "ol"):
            (items[-1]["children"] if items else items).extend(_items(child))
            continue
        node = _item(child.children if tag == "li" else [child], f"{number}." if number is not None else None)
        if number is not None:
            number += 1
        if node:
            items.append(node)
    return items


def note_tree(html: str) -> dict | None:
    """Zotero note HTML → one block subtree ``{"content", "children"}`` (each
    child the same shape): the note's first block element is the root, each
    element after it one more child of the root, and each list's items
    children too, with their nested lists as their own children. A note that
    starts with a list is rooted at its first item, the rest of the list after
    it. None when the note holds no text and no image."""
    nodes = []
    for kind, item in _flow(_parse(html).children):
        if kind == "el" and item.tag in ("ul", "ol"):
            nodes += _items(item)
            continue
        content = _block_md(kind, item)
        if content:
            nodes.append({"content": content, "children": []})
    if not nodes:
        return None
    root = {"content": nodes[0]["content"], "children": nodes[0]["children"] + nodes[1:]}
    while not root["content"]:                   # a first item with no text of its own
        first, rest = root["children"][0], root["children"][1:]
        root = {"content": first["content"], "children": first["children"] + rest}
    return root


# --- titles and images -----------------------------------------------------------

def _plain(text: str) -> str:
    """Inline markdown → its text: marks, link targets and math dollars dropped."""
    out, pos = [], 0
    for m in INLINE_RE.finditer(text):
        out.append(text[pos:m.start()])
        pos = m.end()
        which = m.lastgroup
        body = m.group(which)
        if which == "code":
            out.append(body.strip("`"))
        elif which in ("math", "dmath"):
            out.append(body.strip("$") if body[0] == "$" else body[2:-2])
        elif which == "esc":
            out.append(body[1])
        elif which in _WRAPS or which == "color":
            out.append(_plain(_inner(which, body)))
        elif which in ("embed", "ref"):
            out.append(body.strip("![]").split("|")[0].strip())
        elif which == "link":
            label, href = LINK_PARTS.match(body).groups()
            out.append(_plain(label) or href)
        else:
            out.append(body)
    out.append(text[pos:])
    return "".join(out)


def title_text(content: str) -> str:
    """A block's markdown as plain one-line text (a note page's title): heading
    and quote marks, fences, inline markup, link targets and images dropped,
    whitespace collapsed."""
    lines = []
    for line in _IMAGE_RE.sub(lambda m: m.group() if m.group("src") is None else " ", content or "").split("\n"):
        if not _FENCE_RE.fullmatch(line):
            lines.append(_plain(re.sub(r"^[ \t]{0,3}(?:>[ \t]?)*(?:#{1,6}[ \t]+)?", "", line)))
    return " ".join(" ".join(lines).split())


def _decode_data_uri(uri: str):
    """``data:<mime>[;base64],<data>`` → ``(mime, bytes)``, or None when it
    does not decode or holds nothing."""
    header, sep, data = uri[5:].partition(",")
    if not sep:
        return None
    params = [p.strip().lower() for p in header.split(";")]
    try:
        raw = (base64.b64decode(data, validate=True) if "base64" in params[1:]
               else urllib.parse.unquote_to_bytes(data))
    except (binascii.Error, ValueError):
        return None
    return (params[0] or "text/plain", raw) if raw else None


def store_images(node: dict, store) -> int:
    """Replace, in place, every ``![alt](data:<mime>;base64,<data>)`` in the
    subtree's contents with ``![alt](<url>)``, ``url = store(mime, raw_bytes)``,
    or with "(image)" when ``store`` gives None or the data URI does not
    decode. Returns how many images were stored."""
    stored = 0

    def put(m):
        nonlocal stored
        if m.group("src") is None:
            return m.group()
        decoded = _decode_data_uri(m.group("src"))
        url = store(*decoded) if decoded else None
        if not url:
            return "(image)"
        stored += 1
        return f"![{m.group('alt')}]({url})"

    pending = [node]
    while pending:
        current = pending.pop()
        if "data:" in (current.get("content") or ""):
            current["content"] = _DATA_IMAGE_RE.sub(put, current["content"])
        pending += current.get("children") or []
    return stored
