"""A response body as the text the chat's model reads (``read``): decoded by
what declares its charset and, for an HTML page, its main content as
Markdown with its title, metadata and an outline of its sections. Any other
text (plain, Markdown, JSON, XML, CSV, a script) is read as it is.

The model reads a page in windows and pays for every character, and a
page's menus, footers and language lists were 5–65 % of what a tag stripper
gave it, with the links, headings, lists, tables and code indentation lost
(docs/research/web-search.md). The heuristics below were tuned on real
pages: Wikipedia, docs sites, GitHub, news front pages and articles, blogs,
lab pages, arXiv, portals, a Shift_JIS novel, React-streamed pages.

How an HTML page is read:

- The head is read with regexes over the text before ``<body>`` (its first
  200 KB): the title, the site's name, description, author, dates,
  canonical address and language, and ``<base href>``, which relative links
  resolve against. Dates in the head's JSON-LD stand in for missing meta
  tags (the BBC writes its publication date only there).
- The document is parsed with ``html_markdown.Parser`` the way a browser
  builds what pages lean on it for: a ``<head>`` left open ends at the
  first element a head cannot hold, ``dt``/``dd``/``tr``/``td``/``th``/
  ``option`` close the open one before them, a ``<p>`` ends at any block,
  and ``</body>`` ends nothing. Only scripts and styles are left out while
  parsing (their content is raw text, so no tag inside can unbalance the
  count), and a MathJax ``math/tex`` script becomes its formula. The tree
  is at most ``DEPTH`` deep, which the writer and every pass here recurse
  within.
- What a browser never shows goes: the head, noscript, templates, SVG,
  frames, embeds, media, form controls, dialogs, ``hidden`` and
  ``aria-hidden`` elements, inline ``display:none``, and code line-number
  gutters. React's streamed segments (``<div hidden id="S:0">``), which a
  script moves into place, stay.
- Then the page's chrome: ``nav`` and ``menu``; ``header`` and ``footer``
  outside a ``main`` or ``article``, and ``aside`` outside the article (a
  main without one is the article); the navigation, banner, footer and
  sidebar roles; class or id tokens that name chrome (navbox, sidebar,
  cookie, share, breadcrumbs, toc …). A wrapper that holds the main, an
  article, an h1 or over 30 % of the page's text is no chrome: arXiv wraps
  its whole page in ``div.flex-wrap-footer``.
- The content is the largest ``main`` with 250+ characters, else the
  page's one ``article``, else what a descent from ``<body>`` reaches
  through each child that holds 85 % of its parent's text. The page's h1
  goes first when it lies outside.
- Outside a main or an article, link farms go: a block of 5+ links that are
  over 60 % of its text, with under four words of its own per link and no
  paragraph — unless they are over 80 % of the content, as on a news portal
  whose headline lists are what it has.

``PageMarkdown`` writes it for the model: text as the page has it, a
picture as ``[image: alt]`` (nothing without alt text), a link's address
absolute and readable (``%E5%85%89`` shows as 光; ``%2F``, ``%3F``, ``%23``
and the host stay as they are, so the address still names the same page),
``mailto:`` and ``javascript:`` links as their text, a code block's
language from SyntaxHighlighter's ``brush:`` class too, and a heading's
permalink mark (Sphinx's ¶), a footnote marker (Wikipedia's [12]) and a
code block's language label above it (MDN's "js") gone.
The outline is the h1–h3 the writer wrote, with their offsets in the text,
sampled to a budget, so a ``#`` line in a code block is never one.
"""

import codecs
import html
import re
from datetime import datetime, timezone
from typing import NamedTuple
from urllib.parse import urljoin

from .documents.text import OUTLINE_TEXT, _fit
from .html_markdown import DEPTH, SPACE, El, Heading, Markdown, Parser, attr, joined, text_of, walk

OUTLINE_BUDGET = 2000   # what a page's outline may cost (an entry costs its text and 10)
# What an outline entry keeps of its heading's Markdown: a link's text, not
# its address (a news page's headlines are links), and no emphasis or tags.
_OUTLINE_MARKUP = re.compile(r"\[([^\]]*)\]\([^)]*\)|\*\*|`|</?(?:sub|sup)>")
_HEAD_CHARS = 200_000   # the head is read in the text's first this many characters
_META_CHARS = 500       # a metadata value is cut here


class Page(NamedTuple):
    title: str
    text: str
    kind: str      # "html": the main content as Markdown; "text": the body as it is
    meta: dict     # site, description, author, published, modified, canonical, lang: the keys the page has
    outline: list  # [(offset in text, "## Heading")] of its h1–h3
    visible: int   # characters of all the text a browser shows, menus and forms included


# --- decoding --------------------------------------------------------------------

_BOMS = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))
_HEADER_CHARSET = re.compile(r"""charset\s*=\s*["']?([\w.:-]+)""", re.I)
_DECLARED_CHARSET = re.compile(
    rb"""<meta\b[^<>]*?charset\s*=\s*["']?\s*([\w.:-]+)|<\?xml\b[^<>]*?encoding\s*=\s*["']([\w.:-]+)""", re.I)
# What a browser reads these names as (the WHATWG Encoding standard), or a
# name Python does not know.
_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "shift_jis": "cp932", "shift-jis": "cp932",
            "sjis": "cp932", "x-sjis": "cp932", "ms_kanji": "cp932", "windows-31j": "cp932", "iso-8859-1": "cp1252",
            "latin-1": "cp1252", "latin1": "cp1252", "us-ascii": "cp1252", "ascii": "cp1252", "euc-kr": "cp949",
            "big5": "big5hkscs"}


def decode(raw: bytes, ctype: str = "") -> str:
    """The body as text: by its byte-order mark, else the charset its
    Content-Type names, else the one a ``<meta>`` or ``<?xml?>`` in its first
    4 KB declares, else UTF-8 — what cannot be decoded (a body cut in the
    middle of a character) replaced."""
    for bom, codec in _BOMS:
        if raw.startswith(bom):
            return raw.decode(codec, "replace")
    names = [m.group(1) for m in [_HEADER_CHARSET.search(ctype or "")] if m]
    declared = _DECLARED_CHARSET.search(raw[:4096])
    if declared:
        name = (declared.group(1) or declared.group(2)).decode("ascii").lower()
        # A document read in ASCII to find this is no UTF-16.
        names.append("utf-8" if name.startswith(("utf-16", "utf16")) else name)
    for name in names:
        try:
            return raw.decode(_ALIASES.get(name.lower(), name), "replace")
        except (LookupError, UnicodeError):  # a name Python does not know, or a codec for no text
            continue
    return raw.decode("utf-8", "replace")


# --- the head --------------------------------------------------------------------

_BODY_TAG = re.compile(r"<body[\s>/]", re.I)
# A tag's attributes run to its ">" and never past a "<", so a page of
# unclosed tags is read in one pass.
_HEAD_TAG = re.compile(r"<(meta|link|base|html)\b([^<>]*)>", re.I)
_ATTR = re.compile(r"""\s*([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"']*)))?""")
_LD_DATE = re.compile(r'"(datePublished|dateModified)"\s*:\s*"([^"<]{4,40})"')
# Each key's meta names (name, property or itemprop), the first a page has.
_META_KEYS = {
    "site": ("og:site_name",),
    "description": ("description", "og:description", "twitter:description"),
    "author": ("author", "article:author", "dc.creator"),
    "published": ("article:published_time", "og:article:published_time", "datepublished", "date", "dc.date",
                  "dc.date.issued", "citation_publication_date", "citation_date"),
    "modified": ("article:modified_time", "og:article:modified_time", "og:updated_time", "datemodified",
                 "last-modified"),
}
_LD_KEYS = {"datePublished": "published", "dateModified": "modified"}
_DATE = re.compile(r"(\d{4})[-/](\d{2})[-/](\d{2})")


def _attrs(source: str) -> dict:
    found = {}
    for m in _ATTR.finditer(source):
        value = next((v for v in m.group(2, 3, 4) if v is not None), "")
        found.setdefault(m.group(1).lower(), html.unescape(value))
    return found


def _clean(value: str) -> str:
    return SPACE.sub(" ", value).strip()[:_META_CHARS]


def _date(value: str) -> str:
    """A date as ``YYYY-MM-DD`` when it reads as one (an ISO date or time,
    ``2017/06/12``, a Unix time), else as the page wrote it."""
    m = _DATE.match(value)
    if m:
        return "-".join(m.groups())
    if re.fullmatch(r"\d{9,10}", value):
        return datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%d")
    return value[:40]


def _absolute(base: str, href: str) -> str:
    """``href`` read against ``base``; "" for an address that does not
    parse (``http://[oops/``), which a page may well carry."""
    try:
        return urljoin(base, href.strip())
    except ValueError:
        return ""


def _head(text: str, url: str) -> tuple[str, str, dict]:
    """``(title, base address, meta)`` from the text before ``<body>``."""
    head = text[:_HEAD_CHARS]
    body = _BODY_TAG.search(head)
    head = head[:body.start()] if body else head
    low = head.lower()
    title, start = "", low.find("<title")
    opened = low.find(">", start) + 1 if start >= 0 else 0
    if opened:
        end = low.find("</title", opened)
        title = _clean(html.unescape(head[opened:end if end >= 0 else opened + _META_CHARS]))
    names, meta, base = {}, {}, url
    for m in _HEAD_TAG.finditer(head):
        tag, attrs = m.group(1).lower(), _attrs(m.group(2))
        if tag == "meta":
            name = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
            content = _clean(attrs.get("content", ""))
            if name and content:
                names.setdefault(name, content)
        elif tag == "link" and "canonical" in attrs.get("rel", "").lower().split() and attrs.get("href"):
            meta.setdefault("canonical", attrs["href"].strip())
        elif tag == "base" and attrs.get("href") and base == url:
            base = _absolute(url, attrs["href"]) or url
        elif tag == "html" and attrs.get("lang"):
            meta.setdefault("lang", attrs["lang"].strip()[:20])
    for key, options in _META_KEYS.items():
        value = next((names[n] for n in options if names.get(n) and not (
            key == "author" and names[n].startswith(("http://", "https://")))), "")
        if value:
            meta[key] = _date(value) if key in ("published", "modified") else value
    for m in _LD_DATE.finditer(head):
        meta.setdefault(_LD_KEYS[m.group(1)], _date(m.group(2)))
    if "canonical" in meta:
        meta["canonical"] = _absolute(base, meta["canonical"])
        if not meta["canonical"]:
            del meta["canonical"]
    return title or names.get("og:title", ""), base, meta


# --- the tree --------------------------------------------------------------------

# What a head may hold; any other element ends a head left open.
_HEAD_TAGS = frozenset({"title", "base", "link", "meta", "style", "script", "noscript", "template", "basefont",
                        "bgsound"})
# The block starts that end an open <p>, and where looking for one stops.
_BLOCKS = frozenset({"address", "article", "aside", "blockquote", "body", "center", "dd", "details", "dialog", "dir",
                     "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
                     "h5", "h6", "header", "hgroup", "hr", "html", "li", "main", "menu", "nav", "ol", "p", "pre",
                     "search", "section", "summary", "table", "ul"})
_P_SCOPE = (_BLOCKS - {"p"}) | {"td", "th", "caption", "button", "object", "template"}
# A start tag → (the open elements it closes, where looking for one stops).
_IMPLIED = {
    **dict.fromkeys(("dt", "dd"), (frozenset({"dt", "dd"}), frozenset({"dl"}))),
    "tr": (frozenset({"tr"}), frozenset({"table"})),
    **dict.fromkeys(("td", "th"), (frozenset({"td", "th"}), frozenset({"tr", "table"}))),
    **dict.fromkeys(("thead", "tbody", "tfoot"), (frozenset({"thead", "tbody", "tfoot"}), frozenset({"table"}))),
    "option": (frozenset({"option"}), frozenset({"select", "datalist", "optgroup"})),
}
_TEX_TYPE = re.compile(r"\s*math/tex", re.I)


class _PageParser(Parser):
    """A whole page as a tree no deeper than the writer writes."""
    max_open = DEPTH
    closes_p = _BLOCKS

    def __init__(self):
        super().__init__()
        self.left_out = {"script", "style"}
        self.tex: El | None = None  # the MathJax formula whose script is being read

    def hides(self, tag: str, attrs: dict) -> bool:
        return _unseen(tag, attrs)

    def handle_starttag(self, tag, attrs):
        kind = (dict(attrs).get("type") or "") if tag == "script" else ""
        if _TEX_TYPE.match(kind):
            display = "mode=display" in kind.replace(" ", "").lower()
            self.tex = El("span", {"data-gamma-math": "display" if display else "inline", "data-tex": ""})
            if not (self.past and self.past[-1][1]):
                self.stack[-1].children.append(self.tex)
            return
        if not self.past:
            if tag == "body" or (self.stack[-1].tag == "head" and tag not in _HEAD_TAGS):
                self._close(frozenset({"head"}), frozenset())
            if tag in _BLOCKS:
                self._close(frozenset({"p"}), _P_SCOPE)
            if tag in _IMPLIED:
                self._close(*_IMPLIED[tag])
        super().handle_starttag(tag, attrs)

    def _close(self, names: frozenset, bounds: frozenset):
        for i in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[i].tag
            if tag in names:
                del self.stack[i:]
                return
            if tag in bounds:
                return

    def handle_endtag(self, tag):
        if self.tex is not None:
            self.tex = None  # the script's content is raw text: only its </script> ends it
            return
        if tag in ("body", "html"):
            return  # what follows still shows in the body
        super().handle_endtag(tag)

    def handle_data(self, data):
        if self.tex is not None:
            self.tex.attrs["data-tex"] += data
            return
        if self.stack[-1].tag == "head" and data.strip() and not self.past:
            self._close(frozenset({"head"}), frozenset())
        super().handle_data(data)


# --- what a reader never sees -------------------------------------------------------

_UNSEEN_TAGS = frozenset({"head", "title", "meta", "link", "base", "style", "script", "noscript", "noembed",
                          "noframes", "template", "svg", "canvas", "iframe", "frame", "object", "embed", "applet",
                          "audio", "video", "map", "dialog", "button", "input", "select", "textarea", "option",
                          "optgroup", "datalist", "label", "output", "progress", "meter"})
_REACT_SEGMENT = re.compile(r"S:\d+")
# A code block's line numbers beside it (Pygments, Prism, highlight.js,
# GitHub), dropped when all they hold is numbers.
_GUTTERS = frozenset({"linenos", "lineno", "line-numbers", "line-numbers-rows", "gutter", "hljs-ln-numbers",
                      "blob-num"})
_NUMBERS = re.compile(r"[\d\s]*")


def _unseen(tag: str, attrs: dict) -> bool:
    if tag in _UNSEEN_TAGS:
        return True
    if "hidden" in attrs and (attrs["hidden"] or "").lower() != "until-found":
        return not _REACT_SEGMENT.fullmatch(attrs.get("id") or "")
    if (attrs.get("aria-hidden") or "").lower() == "true":
        return True
    if "MathJax_Preview" in (attrs.get("class") or "").split():
        return True  # what MathJax shows until its script has typeset the formula
    style = (attrs.get("style") or "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _drop_unseen(node: El):
    keep = []
    for kid in node.children:
        if not isinstance(kid, str) and (_unseen(kid.tag, kid.attrs) or _gutter(kid)):
            continue
        if not isinstance(kid, str):
            _drop_unseen(kid)
        keep.append(kid)
    node.children = keep


def _gutter(el: El) -> bool:
    return not _GUTTERS.isdisjoint(attr(el, "class").lower().split()) and bool(_NUMBERS.fullmatch(text_of(el)))


# --- sizes ---------------------------------------------------------------------------

_ANCHORS = frozenset({"main", "article", "h1"})


def _main(el: El) -> bool:
    return el.tag == "main" or "main" in attr(el, "role").lower().split()


def _stats(node, memo: dict) -> tuple[int, int, int, bool]:
    """``(characters, characters inside links, links, whether a main, an
    article or an h1 lies inside)`` of what is under ``node``."""
    if isinstance(node, str):
        return len(node.strip()), 0, 0, False
    got = memo.get(id(node))
    if got is None:
        chars = linked = links = 0
        anchored = False
        for kid in node.children:
            c, lc, k, a = _stats(kid, memo)
            chars, linked, links = chars + c, linked + lc, links + k
            anchored = anchored or a or (not isinstance(kid, str) and (kid.tag in _ANCHORS or _main(kid)))
        if node.tag == "a" and attr(node, "href"):
            linked, links = chars, links + 1
        memo[id(node)] = got = (chars, linked, links, anchored)
    return got


def _size(node, memo: dict) -> int:
    return _stats(node, memo)[0]


def _own(node, memo: dict) -> int:
    """The characters under ``node`` outside its links."""
    chars, linked, _, _ = _stats(node, memo)
    return chars - linked


# --- chrome --------------------------------------------------------------------------

_CHROME_ROLES = frozenset({"navigation", "banner", "contentinfo", "complementary", "search", "dialog", "alertdialog",
                           "menu", "menubar", "toolbar", "tablist"})
_CHROME_NAME = re.compile(
    r"(?:^|[-_\s])(?:nav|navbar|navbox|navigation|menu|menubar|sidebar|side-bar|breadcrumbs?|cookie|cookies|"
    r"consent|gdpr|banner|share|sharing|social|newsletter|subscribe|promo|advert|advertisement|ads?|"
    r"sponsor|sponsored|related|recommended|recirculation|popup|modal|toolbar|skip|skiplink|skip-link|toc|"
    r"editsection|catlinks|printfooter|dropdown|portlet|sr-only|visually-hidden|screen-reader-text|"
    r"masthead|site-header|site-footer|global-header|global-footer|footer|jump-link|hatnote|"
    r"noprint|mw-indicators)(?:$|[-_\s])|VisuallyHidden", re.I)
# An inline element is chrome by its class only when it is never read (a
# Sphinx heading's text is an <a class="toc-backref">).
_INLINE_CHROME_NAME = re.compile(r"(?:^|[-_\s])(?:skip|skiplink|sr-only|visually-hidden|screen-reader-text|"
                                 r"editsection|jump-link|noprint)(?:$|[-_\s])|VisuallyHidden", re.I)
_INLINE = frozenset({"a", "span", "strong", "b", "em", "i", "code", "sup", "sub", "small", "abbr", "cite", "time",
                     "mark", "u", "s", "q", "img", "kbd", "samp", "var", "bdi", "bdo", "data", "font", "del", "ins"})
_NEVER_CHROME = frozenset({"html", "body", "main", "article"})


def _chrome(el: El, within: str) -> bool:
    """Whether ``el`` looks like the page's chrome, where ``within`` (see
    ``_drop_chrome``) says what holds it."""
    if el.tag in ("nav", "menu"):
        return True
    if el.tag in ("header", "footer") and not within or el.tag == "aside" and within != "article":
        return True
    if el.tag in _NEVER_CHROME:
        return False
    if not _CHROME_ROLES.isdisjoint(attr(el, "role").lower().split()):
        return True
    names = f"{attr(el, 'class')} {attr(el, 'id')}"
    return bool((_INLINE_CHROME_NAME if el.tag in _INLINE else _CHROME_NAME).search(names))


def _drop_chrome(node: El, within: str, memo: dict, total: int):
    """Drop the chrome under ``node``. ``within``: "" outside a main and an
    article; "article" inside an article, or a main without one; "main"
    inside a main that holds an article. An aside belongs to the content
    only in the article: one beside it in the main is the page's (the BBC's
    "Related" and "More from the BBC")."""
    keep = []
    for kid in node.children:
        if not isinstance(kid, str):
            if _chrome(kid, within) and not _holds_content(kid, memo, total):
                continue
            inner = within
            if kid.tag == "article":
                inner = "article"
            elif _main(kid) and within != "article":
                inner = "main" if any(not isinstance(n, str) and n.tag == "article" for n in walk(kid)) else "article"
            _drop_chrome(kid, inner, memo, total)
        keep.append(kid)
    node.children = keep


def _holds_content(el: El, memo: dict, total: int) -> bool:
    return _stats(el, memo)[3] or _own(el, memo) > 0.3 * total


# --- the content -----------------------------------------------------------------------

def _content(body: El, memo: dict) -> tuple[El, bool]:
    """The element that holds the page's content, and whether it is a main
    or an article."""
    mains = [n for n in walk(body) if not isinstance(n, str) and _main(n) and _size(n, memo) >= 250]
    if mains:
        return max(mains, key=lambda n: _size(n, memo)), True
    articles = [n for n in walk(body) if not isinstance(n, str) and n.tag == "article" and _size(n, memo) >= 250]
    if len(articles) == 1:
        return articles[0], True
    # A page of headlines is all links: it is weighed by all its text.
    own = _own(body, memo) >= 500

    def weight(n):
        return _own(n, memo) if own else _size(n, memo)
    node = body
    while weight(node):
        heavy = next((k for k in node.children if not isinstance(k, str) and weight(k) >= 0.85 * weight(node)), None)
        if heavy is None or heavy.tag in ("p", "ul", "ol", "table", "pre"):
            break
        node = heavy
    return node, False


_FARM_TAGS = frozenset({"ul", "ol", "div", "section", "menu", "p", "span", "dl"})


def _farm(el: El, memo: dict) -> bool:
    if el.tag not in _FARM_TAGS:
        return False
    chars, linked, links, _ = _stats(el, memo)
    if links < 5 or not chars or linked / chars <= 0.6 or (chars - linked) / 6 / links >= 4:
        return False
    return not any(not isinstance(n, str) and n.tag == "p" and _own(n, memo) > 120 for n in walk(el))


def _farms(node: El, memo: dict, total: int, found: dict):
    """The link farms under ``node`` → their sizes, by id; a link list that
    is most of the content (a headline index) is none."""
    for kid in node.children:
        if isinstance(kid, str):
            continue
        if _farm(kid, memo) and _size(kid, memo) < 0.4 * total:
            found[id(kid)] = _size(kid, memo)
        else:
            _farms(kid, memo, total, found)
    return found


def _drop(node: El, ids: dict):
    node.children = [k for k in node.children if isinstance(k, str) or id(k) not in ids]
    for kid in node.children:
        if not isinstance(kid, str):
            _drop(kid, ids)


# --- the writer ----------------------------------------------------------------------------

_SPACES = re.compile(r"  +")
_BRUSH = re.compile(r"(?:^|\s)brush:\s*([\w+#.-]+)")
_ESCAPES = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
_AUTHORITY = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*")
_IMAGE_ONLY = re.compile(r"\[image: ([^\]]*)\]")
_CITATION = re.compile(r"\[?\s*(?:\d+|[a-z]|note \d+|citation needed)\s*\]?", re.I)
_PERMALINK = re.compile(r"^[#¶§🔗]\s+|\s*[¶§🔗]$|\s+#$")
# A paragraph line a link around it would break: a list item, a quote, a
# table row, a fence, display math.
_BLOCK_LEAD = re.compile(r"(?:[-+*] |\d{1,9}[.)] |[>|]|```|\$\$)")


def _characters(m: re.Match) -> str:
    """A run of %-escapes with each UTF-8 encoded character in it shown,
    every ASCII escape and every byte that is no printable character kept."""
    escaped, out, at = m.group(0), [], 0
    for ch in bytes.fromhex(escaped.replace("%", "")).decode("utf-8", "surrogateescape"):
        width = 3 * len(ch.encode("utf-8", "surrogateescape"))
        out.append(ch if ord(ch) > 0x7F and ch.isprintable() else escaped[at:at + width])
        at += width
    return "".join(out)


def _readable(url: str) -> str:
    """``url`` with the characters its path, query and fragment encode in
    UTF-8 shown; its host and its ASCII escapes stay as they are."""
    authority = _AUTHORITY.match(url)
    at = authority.end() if authority else 0
    return url[:at] + _ESCAPES.sub(_characters, url[at:])


def _linked(text: str, href: str) -> str:
    picture = _IMAGE_ONLY.fullmatch(text)
    text = f"image: {picture.group(1)}" if picture else text
    return "[" + text.replace("]", "\\]") + f"]({href})"


class PageMarkdown(Markdown):
    """The page's writer: text as the page has it, links readable, a
    picture its alt text. A teaser card (a link around blocks) links its
    heading, else its first line."""

    def line(self, s: str) -> str:
        return _SPACES.sub(" ", s)  # the spaces between inline elements

    def href(self, raw: str) -> str:
        return _readable(super().href(raw))

    def image(self, node: El) -> str:
        alt = SPACE.sub(" ", attr(node, "alt")).strip()
        return f"[image: {alt}]" if alt else ""

    def heading(self, level: int, text: str) -> Heading:
        return super().heading(level, _PERMALINK.sub("", text.strip(" \u200b")))

    def code_lang(self, node: El) -> str:
        brush = _BRUSH.search(attr(node, "class"))  # SyntaxHighlighter's <pre class="brush: js">
        return super().code_lang(node) or (brush.group(1).lower() if brush else "")

    def blocks(self, kids) -> list[str]:
        written = super().blocks(kids)
        # A code block's language label above it (MDN's "js") says what its fence says.
        return [b for b, after in zip(written, written[1:] + [""])
                if not (after.startswith("```") and b.lower() == after.split("\n", 1)[0].lstrip("`"))]

    def link(self, node: El, inner: str) -> str:
        text = SPACE.sub(" ", inner).strip()
        if attr(node, "href").strip().startswith("#") and _CITATION.fullmatch(text):
            return ""  # a footnote marker
        if _IMAGE_ONLY.fullmatch(text):  # a picture's link: [image: alt](url)
            href = self.href(attr(node, "href"))
            return _linked(text, href) if href else inner
        return super().link(node, inner)

    def block(self, node: El) -> list[str]:
        written = super().block(node)
        href = self.href(attr(node, "href")) if node.tag == "a" and written else ""
        if not href:
            return written
        at = next((i for i, b in enumerate(written) if isinstance(b, Heading)), None)
        if at is not None:
            heading = written[at]
            if "](" not in heading:
                written[at] = Heading(heading.level, _linked(heading[heading.level + 1:], href))
            return written
        line, sep, rest = written[0].partition("\n")
        if "](" not in line and not _BLOCK_LEAD.match(line):
            written[0] = _linked(line, href) + sep + rest
        return written


# --- reading -----------------------------------------------------------------------------

# How an HTML body without a content type starts (the WHATWG sniffing rules).
_HTML_START = re.compile(r"\s*(?:<!--|<!doctype\s+html|<(?:html|head|script|iframe|h1|div|font|table|a|style|title|b|"
                         r"body|br|p|meta)[\s>/])", re.I)


def _is_html(text: str, ctype: str) -> bool:
    if ctype:
        return "html" in ctype.lower()
    return bool(_HTML_START.match(text[:1024]))


def read(raw: bytes, url: str, ctype: str = "") -> Page:
    """The body ``raw`` that ``url`` answered with ``ctype``, as text for
    the model (the module docstring says how)."""
    text = decode(raw, ctype)
    if not _is_html(text, ctype):
        return Page("", text, "text", {}, [], len(text.strip()))
    title, base, meta = _head(text, url)
    parser = _PageParser()
    parser.feed(text)
    parser.close()
    _drop_unseen(parser.root)
    body = next((n for n in walk(parser.root) if not isinstance(n, str) and n.tag == "body"), parser.root)
    visible = len(SPACE.sub(" ", "".join(n for n in walk(body) if isinstance(n, str))).strip())
    h1 = next((n for n in walk(body) if not isinstance(n, str) and n.tag == "h1"), None)
    memo: dict = {}
    _drop_chrome(body, "", memo, _own(body, memo))
    memo = {}
    root, semantic = _content(body, memo)
    if not semantic:
        total = _size(root, memo)
        farms = _farms(root, memo, total, {})
        if total - sum(farms.values()) >= 0.2 * total:
            _drop(root, farms)
    md = PageMarkdown(base)
    blocks = md.block(root)
    if h1 is not None and not any(n is h1 for n in walk(root)):
        first = md.block(h1)
        if first and first[0][2:] not in "\n\n".join(blocks)[:2000]:
            blocks = first + blocks
    text, headings = joined(blocks)
    outline = _fit([(at, _OUTLINE_MARKUP.sub(lambda m: m.group(1) or "", h)[:OUTLINE_TEXT])
                    for at, h in headings if h.level <= 3], OUTLINE_BUDGET)
    if not title and h1 is not None:
        title = SPACE.sub(" ", text_of(h1)).strip()
    return Page(title, text, "html", meta, outline, visible)


def visible_chars(raw: bytes, ctype: str = "") -> int:
    """How much text a browser shows of the body, its menus and forms
    included (``Page.visible``): a page with almost none may be all widget
    or form."""
    return read(raw, "", ctype).visible
