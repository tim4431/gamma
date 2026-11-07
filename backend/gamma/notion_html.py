"""Notion's HTML export → the block model (``notion_blocks``).

``read_page`` reads one page of the export, of any era: what 2026 writes in
``data-notion-*`` attributes an older export says in classes and in the KaTeX
under a formula. Its blocks go through ``notion_gamma.to_gamma``, which writes
Markdown links, so links, images and sub-pages take the road a Markdown
export's take, and nothing here knows about the zip. A database's rows stay
the CSV's: a page gives the block that stands for one, the view's columns and
the uncut name.

Stdlib ``html.parser`` only, since an HTML5 parser would move ``div.indented``
out of its ``<p>``, which is where Notion writes a paragraph's children. Text
keeps its spaces, as the export's ``white-space: pre-wrap`` shows them, and a
newline in it is a line break. Colour stays on the block (``attrs["color"]``),
for ``to_gamma`` to put under the runs' own.
"""

import html as html_mod
import posixpath
import re
from dataclasses import dataclass, field, replace
from urllib.parse import unquote

from .notion_blocks import Block, color_name, merge_colors
from .notion_gamma import _add, _literal, _trim
from .zotero_notes import _TAG_MARKS, El, Parser, _href, _math_md, classes, node_tag, node_text

# How deep the walk goes, in elements. A page goes on past it as flat text.
_DEEPEST = 200
# How far down a block looks for what makes it one: a figure's picture, a link
# to a page. These walk from every level of the page, so with no bound their
# cost on a page nested to ``_DEEPEST`` is that depth times the page's size.
_NEAR = 8
_TOO_DEEP = "Blocks nested too deep to keep their structure came in as text"

_ARTICLE_RE = re.compile(rb'<article\b[^>]*\bclass="page\b')
_BODY_MARK = b'class="page-body"'
_ID_RE = re.compile(r"[0-9a-f]{32}")
_HEADING_RE = re.compile(r"h[1-6]")
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_LANGUAGE_RE = re.compile(r"language-(.+)")
_WIDTH_RE = re.compile(r"(?:^|;)\s*width:\s*(\d{1,6}(?:\.\d+)?)px")
# A list's start, in the nine digits Markdown holds of a number.
_START_RE = re.compile(r"\d{1,9}")
# A <time>'s datetime, as a date mention writes it (mentions.MENTION_RE).
_MOMENT_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?")
# A toggle heading's level, where <summary> holds no <h2> of its own.
_SUMMARY_SIZE_RE = re.compile(r"font-size:\s*(1\.875|1\.5|1\.25)em")
_SUMMARY_LEVELS = {"1.875": 1, "1.5": 2, "1.25": 3}

# Elements that stand on their own line; any other is inline. A div or a
# section nothing else claims is transparent.
_BLOCK_TAGS = {"html", "body", "article", "header", "section", "div", "nav", "p", "h1", "h2", "h3", "h4", "h5", "h6",
               "ul", "ol", "details", "blockquote", "aside", "figure", "pre", "hr", "table"}
_ICON_CLASSES = {"icon", "user-icon", "user-icon-inner"}
# The cell of a property that lists values, side by side and with nothing between.
_VALUE_CLASSES = {"selected-value", "user"}


@dataclass
class ReadPage:
    """One page of the export. ``title`` is "" and ``notion_id`` None when the
    page does not say: the file's name knows them too. ``links`` are the
    ``(label, href)`` of the lines that are one link, as ``_link_lines`` reads
    a Markdown page. ``table_order`` is a database id's column names in the
    view's order, ``table_title`` its uncut name, ``fields`` a row's values."""
    title: str = ""
    notion_id: str | None = None
    blocks: list = field(default_factory=list)
    fields: dict = field(default_factory=dict)
    links: list = field(default_factory=list)
    table_order: dict = field(default_factory=dict)
    table_title: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


class _Parser(Parser):
    """``Parser``, past a stray ``</div>``: an older audio block closes a
    div it never opened. It closes nothing, so the figure's container stays
    open and what follows the audio stays inside a callout. A page is a 5 MB
    file anyone can send, and this walk and ``Parser``'s go up all that is
    open at each tag that closes nothing: ``max_open`` bounds them, past
    ``_DEEPEST``, where the walk goes flat anyway."""
    max_open = 256

    def handle_endtag(self, tag):
        if tag == "div" and not self.hidden and not self.past:
            if next(el.tag for el in reversed(self.stack) if el.tag in ("div", "figure", "")) == "figure":
                return
        super().handle_endtag(tag)


def opens_notion_page(head: bytes) -> bool:
    """Whether a file's first bytes (64 KB: the stylesheet comes first) open a
    page of Notion's export; what follows can still prove it another."""
    return bool(_ARTICLE_RE.search(head))


def is_notion_page(data: bytes) -> bool:
    """Whether a file's bytes, as far as its body's start, are a page of
    Notion's export, not its ``index.html`` or any other HTML."""
    return opens_notion_page(data) and _BODY_MARK in data


def plain_text(rich: list) -> str:
    """Rich text's words, on one line."""
    return "".join(" " if leaf[0] == "br" else leaf[1] for leaf in rich).strip()


def _undashed(value: str | None) -> str | None:
    value = (value or "").replace("-", "").lower()
    return value if _ID_RE.fullmatch(value) else None


def _find(el: El, tag: str | None = None, name: str | None = None, within: int | None = None) -> El | None:
    """The first element under ``el`` of that tag and class, by no recursion,
    and ``within`` that many levels of it if that is given."""
    stack = [(node, 1) for node in reversed(el.children)]
    while stack:
        node, level = stack.pop()
        if node_tag(node) is None:
            continue
        if (tag is None or node.tag == tag) and (name is None or name in classes(node)):
            return node
        if within is None or level < within:
            stack.extend((child, level + 1) for child in reversed(node.children))
    return None


def _child(el: El, tag: str) -> El | None:
    return next((node for node in el.children if node_tag(node) == tag), None)


def _flat(nodes: list) -> str:
    """All the text under ``nodes``, by no recursion: it is what is left of
    what the walk does not go into."""
    out, stack = [], list(reversed(nodes))
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            out.append(node)
        else:
            stack.extend(reversed(node.children))
    return "".join(out)


def _is_block(node) -> bool:
    tag = node_tag(node)
    return tag in _BLOCK_TAGS or tag == "a" and "bookmark" in classes(node)


def _is_value(node) -> bool:
    return node_tag(node) == "a" or node_tag(node) is not None and bool(_VALUE_CLASSES.intersection(classes(node)))


def _color(el: El) -> dict:
    """A block's colour as its attrs: a callout's background, or the
    ``block-color-*`` class. Notion's default colours mean nothing."""
    names = [name.removeprefix("block-color-") for name in classes(el) if name.startswith("block-color-")]
    for name in [el.attrs.get("data-notion-callout-background"), *names]:
        if color := color_name(name):
            return {"color": color}
    return {}


def _tex(el: El) -> str:
    """A formula's TeX: the attribute, or the annotation under an older
    export's KaTeX. What KaTeX drew is read only if neither is there."""
    tex = el.attrs.get("data-notion-equation") or el.attrs.get("data-notion-inline-equation")
    if tex:
        return tex
    note = _find(el, "annotation")
    return node_text(note).strip() if note else _flat(el.children).replace("﻿", "").strip()


def _moment(el: El) -> str | None:
    m = _MOMENT_RE.match(el.attrs.get("datetime") or "")
    return " ".join(filter(None, m.groups())) if m else None


def _add_lines(out: list, kind: str, text: str, marks: dict):
    for i, line in enumerate(text.split("\n")):
        if i:
            out.append(("br",))
        _add(out, kind, line, marks)


def _icon_markup(src: str) -> str:
    """An icon that is a picture, as the Markdown export writes it in a
    callout's first line. A file of the zip has no way to be one there."""
    if not src.startswith(("http://", "https://")):
        return ""
    src = html_mod.escape(src, quote=True)
    return f'<img src="{src}" alt="{src}" width="40px" />'


def _callout_icon(el: El, box: El | None) -> str:
    icon = el.attrs.get("data-notion-callout-icon") or ""
    mark = _find(box, name="icon") if box is not None and not icon else None
    if mark is not None and mark.tag == "img":
        return _icon_markup(mark.attrs.get("src") or "")
    if mark is not None:
        icon = mark.attrs.get("data-emoji") or _flat(mark.children).strip()
    return _icon_markup(icon) if "/" in icon else icon


def _table_rows(table: El) -> tuple:
    """A table's ``<tr>``s, and whether it has a header row."""
    rows, header = [], False
    for node in table.children:
        tag = node_tag(node)
        if tag == "tr":
            rows.append(node)
        elif tag in ("thead", "tbody", "tfoot"):
            kept = [row for row in node.children if node_tag(row) == "tr"]
            rows += kept
            header = header or tag == "thead" and bool(kept)
    return rows, header


def _field_lines(fields: dict) -> Block:
    """A row's properties as the one block of ``Key: value`` lines a row page
    of the Markdown export starts with."""
    rich = []
    for key, value in fields.items():
        if rich:
            rich.append(("br",))
        _add(rich, "text", f"{key}: ", {})
        for leaf in value:
            if leaf[0] == "br":
                _add(rich, "text", " ", {})
            else:
                _add(rich, *leaf)
    return Block("paragraph", rich)


def _link_lines(blocks: list):
    """The ``(label, href)`` of each block that is one link and nothing else."""
    for block in blocks:
        links = {leaf[2].get("link") for leaf in block.rich if leaf[0] != "br"}
        if block.type == "page" and block.attrs.get("url"):
            yield plain_text(block.rich), block.attrs["url"]
        elif block.type == "paragraph" and len(links) == 1 and None not in links:
            yield plain_text(block.rich), links.pop()
        yield from _link_lines(block.children)


class _Reader:
    """One page's walk. Each ``depth`` is the element's own: the blocks under
    it are one deeper."""

    def __init__(self):
        self.page = ReadPage()

    def too_deep(self):
        if _TOO_DEEP not in self.page.warnings:
            self.page.warnings.append(_TOO_DEEP)

    # --- inline ---------------------------------------------------------------

    def math(self, node: El, marks: dict, out: list):
        """A formula, whose KaTeX is never read as text. A control space at
        its end goes, or its backslash would be on the closing dollar."""
        tex = _tex(node).strip()
        if (len(tex) - len(tex.rstrip("\\"))) % 2:
            tex = tex[:-1].rstrip()
        math = _math_md(f"${tex}$") if tex else None
        if math:
            out.append(("math", math, marks))
        else:
            _add(out, "text", tex, marks)

    def inline(self, nodes: list, marks: dict, out: list, depth: int):
        """Inline HTML → rich text, into ``out``."""
        if depth >= _DEEPEST:
            self.too_deep()
            _add(out, "text", " ".join(_flat(nodes).split()), marks)
            return
        for node in nodes:
            tag = node_tag(node)
            names = [] if tag is None else classes(node)
            if tag is None:
                _add_lines(out, "text", node, marks)
            elif tag == "br":
                out.append(("br",))
            elif _ICON_CLASSES.intersection(names):
                continue
            elif "notion-text-equation-token" in names or "katex" in names:
                self.math(node, marks, out)
            elif tag == "code":
                _add_lines(out, "code", node_text(node), marks)
            elif tag == "time":
                moment = _moment(node)
                if moment:
                    out.append(("raw", f"@{moment}", marks))
                else:
                    _add(out, "text", _flat(node.children), marks)
            elif tag == "img":
                if src := _href(node.attrs.get("src")):
                    _add(out, "text", node.attrs.get("alt") or src, {"link": src, **marks})
            elif tag == "a":
                href = _href(node.attrs.get("href"))
                inside = {**marks, "link": href} if href and href[0] != "#" else marks
                self.inline(node.children, inside, out, depth + 1)
            elif tag == "mark":
                color = next((color_name(name.removeprefix("highlight-")) for name in names
                              if name.startswith("highlight-")), None)
                color = merge_colors(marks.get("color"), color)
                self.inline(node.children, {**marks, "color": color} if color else marks, out, depth + 1)
            elif tag in _TAG_MARKS or tag == "span" and "border-bottom" in (node.attrs.get("style") or ""):
                self.inline(node.children, {**marks, _TAG_MARKS.get(tag, "underline"): True}, out, depth + 1)
            else:
                self.inline(node.children, marks, out, depth + 1)

    def rich(self, nodes: list, depth: int) -> list:
        out = []
        self.inline(nodes, {}, out, depth)
        return _trim(out)

    # --- blocks ---------------------------------------------------------------

    def blocks(self, nodes: list, depth: int) -> list:
        """Sibling nodes → blocks, loose inline content a paragraph."""
        if depth >= _DEEPEST:
            self.too_deep()
            words = " ".join(_flat(nodes).split())
            return [Block("paragraph", [("text", words, {})])] if words else []
        out, loose = [], []

        def flush():
            rich = self.rich(loose, depth)
            loose.clear()
            if rich:
                out.append(Block("paragraph", rich))

        for node in nodes:
            if _is_block(node):
                flush()
                out += self.block(node, depth)
            else:
                loose.append(node)
        flush()
        return out

    def held(self, kind: str, nodes: list, depth: int, attrs: dict) -> Block:
        """A block of what it holds: the paragraph it opens with is its text,
        whose colour is nearer the words than the block's, and the blocks
        after that are its children. A callout's colour is its type, so there
        a coloured paragraph stays a child, which ``to_gamma`` makes a line."""
        kids = self.blocks(nodes, depth + 1)
        first = kids[0] if kids and kids[0].type == "paragraph" and not kids[0].children else None
        if first is None or kind == "callout" and first.attrs:
            return Block(kind, [], kids, attrs)
        return Block(kind, first.rich, kids[1:], {**attrs, **first.attrs})

    def block(self, el: El, depth: int) -> list:
        tag, names = el.tag, classes(el)
        if tag == "nav" or "table_of_contents" in names or "collection-title" in names:
            return []
        if "collection-content" in names:
            return self.database(el, depth)
        if "callout" in names:
            return self.callout(el, depth)
        if "link-to-page" in names:
            return self.page_link(el, depth)
        if "bookmark" in names:
            return self.bookmark(el)
        if "source" in names:
            return self.source(el, depth)
        if _HEADING_RE.fullmatch(tag):
            rich = self.rich(el.children, depth)
            return [Block("heading", rich, attrs={"level": int(tag[1]), **_color(el)})] if rich else []
        reader = self.READERS.get(tag)
        return reader(self, el, depth) if reader else self.blocks(el.children, depth + 1)

    def paragraph(self, el: El, depth: int) -> list:
        return [self.held("paragraph", el.children, depth, _color(el))]

    def quote(self, el: El, depth: int) -> list:
        return [self.held("quote", el.children, depth, _color(el))]

    def rule(self, el: El, depth: int) -> list:
        return [Block("divider")]

    def callout(self, el: El, depth: int) -> list:
        """Its icon's box, if it has one, and what it holds, bare text too.
        ``to_gamma`` makes ">" lines of what can be."""
        nodes = [node for node in el.children if node_tag(node) or node.strip()]
        box = nodes[0] if nodes and node_tag(nodes[0]) == "div" else None
        if box is not None and "font-size" not in (box.attrs.get("style") or ""):
            box = None
        icon = _callout_icon(el, box)
        attrs = {**_color(el), **({"icon": icon} if icon else {})}
        return [self.held("callout", nodes[1:] if box is not None else nodes, depth, attrs)]

    def toggle(self, el: El, depth: int) -> list:
        """A toggle, whatever ``details[open]`` says, and a toggle heading
        when its summary holds a heading or is the size of one."""
        summary = _child(el, "summary")
        title, attrs = (summary.children if summary is not None else []), _color(el)
        heading = next((node for node in title if _HEADING_RE.fullmatch(node_tag(node) or "")), None)
        size = _SUMMARY_SIZE_RE.search(summary.attrs.get("style") or "") if summary is not None else None
        if heading is not None:
            title, attrs = heading.children, {**attrs, **_color(heading), "heading": int(heading.tag[1])}
        elif size:
            attrs["heading"] = _SUMMARY_LEVELS[size[1]]
        kids = self.blocks([node for node in el.children if node is not summary], depth + 1)
        return [Block("toggle", self.rich(title, depth), kids, attrs)]

    def items(self, el: El, depth: int) -> list:
        """A list's items, one block each: Notion's own lists are never
        merged, a number keeps the ``start`` its list has, and an older
        export's toggle has its colour on the list around it."""
        names = classes(el)
        items = [node for node in el.children if node_tag(node) == "li"]
        if "toggle" in names:
            blocks = [block for li in items for block in self.blocks(li.children, depth + 1)]
            return [replace(block, attrs={**_color(el), **block.attrs}) if block.type == "toggle" else block
                    for block in blocks]
        start = el.attrs.get("start") or ""
        number = int(start) if _START_RE.fullmatch(start) else 1
        out = []
        for i, li in enumerate(items):
            nodes, attrs = li.children, _color(el)
            box = next((node for node in nodes if node_tag(node) and "checkbox" in classes(node)), None)
            if box is not None or "to-do-list" in names:
                kind, nodes = "todo", [node for node in nodes if node is not box]
                attrs["checked"] = box is not None and ("checkbox-on" in classes(box) or "checked" in box.attrs)
            elif el.tag == "ol":
                kind, attrs["start"] = "number", number + i
            else:
                kind = "bullet"
            out.append(self.held(kind, nodes, depth, attrs))
        return out

    def code(self, el: El, depth: int) -> list:
        code = _child(el, "code") or el
        text = node_text(code)
        language = _LANGUAGE_RE.match(code.attrs.get("class") or "")
        language = (el.attrs.get("data-notion-code-syntax") or (language[1] if language else "")).lower()
        if not text.strip():
            return []
        known = language not in ("", "plain text")
        return [Block("code", attrs={"text": text, **({"language": language} if known else {})})]

    def table(self, el: El, depth: int) -> list:
        """A simple table. A ``th`` in the body is a cell like any other, and
        a table with nothing in it is none."""
        if "properties" in classes(el):
            return []
        rows, header = _table_rows(el)
        rows = [[self.rich(cell.children, depth) for cell in row.children if node_tag(cell) in ("td", "th")]
                for row in rows]
        return [Block("table", attrs={"rows": rows, "header": header})] if any(any(row) for row in rows) else []

    def figure(self, el: El, depth: int) -> list:
        names = classes(el)
        if "equation" in names:
            tex = _tex(el)
            return [Block("equation", attrs={"text": tex})] if tex.strip() else []
        image = _find(el, "img", within=_NEAR) if "image" in names else _child(el, "img")
        if image is not None:
            return self.image(el, image, depth)
        media = _find(el, "audio", within=_NEAR) or _find(el, "video", within=_NEAR)
        if media is not None:
            return self.media(el, media, depth)
        return self.blocks(el.children, depth + 1)

    def image(self, el: El, image: El, depth: int) -> list:
        """``to_gamma`` makes ``![caption|W](src)`` of it. A file of the zip
        stays as its link has it: ``Links.resolve`` reads a target both ways,
        and one read here would be cut at a "#" in the name."""
        caption = _child(el, "figcaption")
        width = _WIDTH_RE.search(image.attrs.get("style") or "")
        attrs = {"url": _href(image.attrs.get("src") or el.attrs.get("data-notion-image"))}
        return [Block("image", self.rich(caption.children, depth) if caption is not None else [],
                      attrs={**attrs, **({"width": float(width[1])} if width else {})})]

    def media(self, el: El, media: El, depth: int) -> list:
        source = _find(media, "source")
        src = _href(media.attrs.get("src") or (source.attrs.get("src") if source is not None else ""))
        name = unquote(posixpath.basename(src.split("?")[0])) or src
        file, caption = Block("file", _literal(name), attrs={"url": src, "media": media.tag}), _child(el, "figcaption")
        return [file, *(self.blocks([caption], depth) if caption is not None else [])]

    def source(self, el: El, depth: int) -> list:
        """A figure's ``.source``: a file, of the zip or marked as one, or
        an embed, which is its link."""
        link, frame = _find(el, "a", within=_NEAR), _find(el, "iframe", within=_NEAR)
        if link is None:
            src = _href(frame.attrs.get("src")) if frame is not None else ""
            return [Block("paragraph", [("text", src, {"link": src})])] if src else self.blocks(el.children, depth + 1)
        href = _href(link.attrs.get("href"))
        if "data-notion-file" in link.attrs or not _SCHEME_RE.match(href):
            name = link.attrs.get("data-notion-file-alias") or plain_text(self.rich(link.children, depth))
            return [Block("file", _literal(name), attrs={"url": href})]
        return [Block("paragraph", self.rich([link], depth))]

    def bookmark(self, el: El) -> list:
        """Its title as a link; the description goes."""
        href = _href(el.attrs.get("href"))
        title = _find(el, name="bookmark-title")
        words = " ".join(_flat(title.children).split()) if title is not None else ""
        return [Block("paragraph", [("text", words or href, {"link": href} if href else {})])]

    def page_link(self, el: El, depth: int) -> list:
        """A sub-page and a link to a page are the same markup: the directory
        tells them apart, as on the Markdown route."""
        link = _find(el, "a", within=_NEAR)
        if link is None:
            return self.blocks(el.children, depth + 1)
        return [Block("page", self.rich(link.children, depth), attrs={"url": _href(link.attrs.get("href"))})]

    def database(self, el: El, depth: int) -> list:
        """The block standing for a database, and the two things the page
        knows that its CSV does not. A bare table is the page's own view."""
        table = el if el.tag == "table" else _find(el, "table", "collection-content")
        title = _find(el, "h4", "collection-title")
        database = _undashed(el.attrs.get("id")) or (self.page.notion_id if el.tag == "table" else None)
        rich = self.rich(title.children, depth) if title is not None else []
        if database and rich:
            self.page.table_title[database] = plain_text(rich)
        rows = _table_rows(table)[0] if table is not None else []
        if database and rows:
            self.page.table_order[database] = [plain_text(self.rich(cell.children, depth))
                                               for cell in rows[0].children if node_tag(cell) == "th"]
        return [Block("page", rich, attrs={"database": database} if database else {})]

    READERS = {"p": paragraph, "blockquote": quote, "hr": rule, "aside": callout, "details": toggle, "ul": items,
               "ol": items, "pre": code, "table": table, "figure": figure}

    # --- the page ---------------------------------------------------------------

    def value(self, cell: El) -> list:
        """A property's value as rich text: what the Markdown export writes,
        but for a date, which goes as it is stored."""
        box = _find(cell, name="checkbox")
        if box is not None:
            return [("text", "Yes" if "checkbox-on" in classes(box) or "checked" in box.attrs else "No", {})]
        values = [node for node in cell.children if node_tag(node) or node.strip()]
        if len(values) == 1 and node_tag(values[0]) == "time":
            return [("text", _moment(values[0]) or _flat(values[0].children).lstrip("@ "), {})]
        if len(values) > 1 and all(_is_value(node) for node in values):
            rich = []
            for node in values:
                rich += [("text", ", ", {})] * bool(rich) + self.rich([node], 1)
            return rich
        return self.rich(cell.children, 1)

    def properties(self, table: El):
        """A row page's properties, those with a value, as the Markdown export
        leaves the rest out."""
        for row in _table_rows(table)[0]:
            key, cell = _child(row, "th"), _child(row, "td")
            name = plain_text(self.rich(key.children, 1)) if key is not None else ""
            value = self.value(cell) if cell is not None else []
            if name and value:
                self.page.fields[name] = value

    def read(self, root: El):
        page = self.page
        article = _find(root, "article") or root
        header = _child(article, "header")
        page.notion_id = _undashed(article.attrs.get("id"))
        title = _find(header if header is not None else article, "h1", "page-title")
        if title is not None:
            page.title = plain_text(self.rich(title.children, 1))
        table = _find(article, "table", "properties")
        if table is not None:
            self.properties(table)
        lead = [_field_lines(page.fields)] if page.fields else []
        words = _find(header, "p", "page-description") if header is not None else None
        words = self.rich(words.children, 1) if words is not None else []
        if words:
            lead.append(Block("paragraph", words))
        page.blocks = lead + self.blocks([node for node in article.children if node is not header], 0)
        page.links = list(_link_lines(page.blocks))


def read_page(html: str) -> ReadPage:
    """One page of Notion's HTML export → its title, id, blocks and what else
    it says (``ReadPage``). A row page's properties are also its first block,
    so that ``to_gamma`` writes them as the Markdown export has them; an empty
    paragraph stays, for ``to_gamma`` to drop."""
    parser = _Parser(keep_p=True, hidden=("svg",))
    parser.feed((html or "").replace("\r\n", "\n").replace("\r", "\n"))
    parser.close()
    reader = _Reader()
    try:
        reader.read(parser.root)
    except RecursionError:
        reader.page.blocks = []
        reader.too_deep()
    return reader.page
