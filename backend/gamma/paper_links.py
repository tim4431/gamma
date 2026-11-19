"""The links on a web page that may lead to a paper's full text, ranked
against the paper being looked for.

A lab's publication list or a repository page can hold hundreds of PDF
links, and the one for the wanted paper is often far down: a lab page lists
one paper per item with its title as the link text, so a cap applied in
page order drops it. Each link keeps its own text and the text of the item
it sits in (the citation around it), and ``rank`` reads those against the
wanted title before any cap applies. Without a title the page order stays.

The links are read from the tree web_page.read parses the page into, before
it drops what a reader never sees (``Page.links``), so a page is parsed once.
"""

import html
import re
from urllib.parse import urljoin, urlsplit

from .html_markdown import El, attr

LINKS_MAX = 400      # candidates kept per page
_CONTEXT_MAX = 300   # chars of the surrounding item kept per link
_SHORT_ITEM = 40     # an item this short ("[PDF]") borrows the one before it
_HIDDEN = {"head", "template", "noscript", "svg"}  # scripts and styles never reach the tree
_BLOCKS = {"li", "p", "div", "tr", "td", "th", "dd", "dt", "article", "section", "table", "ul", "ol",
           "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "figure", "figcaption", "main", "aside"}
_PDF_TEXT_RE = re.compile(r"\b(?:pdf|full[\s-]?text|download)\b", re.I)
_PDF_PATH_RE = re.compile(r"(?:\.pdf$|/pdf(?:/|$)|/download(?:/|$)|/bitstream/|viewcontent\.cgi|/files/)", re.I)
_STOPWORDS = {"the", "and", "for", "with", "from", "into", "onto", "via", "its", "their", "are", "was",
              "were", "that", "this", "these", "those", "over", "under", "between", "using", "based"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


class _Links:
    """A tree's anchors, each with the index of the text item (block) it
    sits in."""

    def __init__(self, base: str):
        self.base = base
        self.blocks: list[list[str]] = [[]]
        self.links: list[dict] = []
        self.anchor: dict | None = None

    def _new_block(self):
        if self.blocks[-1]:
            self.blocks.append([])

    def read(self, node: El):
        """The nodes under ``node`` in document order (the tree is at most
        ``html_markdown.DEPTH`` deep)."""
        for kid in node.children:
            if isinstance(kid, str):
                self.blocks[-1].append(kid)
                if self.anchor is not None:
                    self.anchor["text"].append(kid)
                continue
            if kid.tag in _HIDDEN:
                continue
            block = kid.tag in _BLOCKS
            if block:
                self._new_block()
            if kid.tag == "a":
                self.close_anchor()
                href = attr(kid, "href").strip()
                if href and not href.startswith(("#", "javascript:", "mailto:")):
                    self.anchor = {"url": urljoin(self.base, href), "text": [], "block": len(self.blocks) - 1}
            self.read(kid)
            if kid.tag == "a":
                self.close_anchor()
            if block:
                self._new_block()

    def close_anchor(self):
        if self.anchor is not None:
            self.anchor["text"] = _clean(" ".join(self.anchor["text"]))
            self.links.append(self.anchor)
            self.anchor = None


def pdf_links(root: El, base: str) -> list[dict]:
    """The links under ``root`` (a page as html_markdown's tree, links
    resolved against ``base``) that look like a document — a ``.pdf`` or
    ``/pdf`` path, a repository download route, "PDF" / "full text" /
    "Download" in the link text — as ``[{url, text, context}]`` in page
    order, each URL once (its most descriptive link), at most LINKS_MAX."""
    walk = _Links(base)
    walk.read(root)
    walk.close_anchor()
    blocks = [_clean(" ".join(parts)) for parts in walk.blocks]
    found: dict[str, dict] = {}
    for link in walk.links:
        url, text = link["url"], link["text"]
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or parts.username:
            continue
        if not (_PDF_PATH_RE.search(parts.path) or _PDF_TEXT_RE.search(text)):
            continue
        context = blocks[link["block"]] if link["block"] < len(blocks) else ""
        if len(context) < _SHORT_ITEM and link["block"] > 0:
            context = f"{blocks[link['block'] - 1]} {context}".strip()
        entry = {"url": url, "text": text[:200], "context": context[:_CONTEXT_MAX]}
        kept = found.get(url)
        if kept is None:
            if len(found) >= LINKS_MAX:
                break
            found[url] = entry
        elif len(entry["context"]) > len(kept["context"]):
            found[url] = {**entry, "text": kept["text"] if len(kept["text"]) > len(text) else entry["text"]}
    return list(found.values())


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOPWORDS]


def title_key(text: str, limit: int | None = None) -> str:
    """A title reduced to its lowercase letters and digits, the form the
    paper modules compare titles in (``limit`` cuts it to that many
    characters)."""
    key = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
    return key if limit is None else key[:limit]


def score(link: dict, want: str) -> float:
    """How well a link's text, surrounding item and path match ``want`` (a
    title): the share of its words found, plus one when the whole title is
    there (normalized), plus a little for a direct ``.pdf``."""
    words = _words(want)
    if not words:
        return 0.0
    hay = " ".join((link.get("text", ""), link.get("context", ""), urlsplit(link["url"]).path))
    have = set(_words(hay))
    value = sum(w in have for w in words) / len(words)
    title = title_key(want)
    if len(title) >= 12 and title in title_key(hay):
        value += 1.0
    if urlsplit(link["url"]).path.lower().endswith(".pdf"):
        value += 0.05
    return round(value, 3)


def rank(links: list[dict], want: str = "", limit: int = 8) -> list[dict]:
    """The ``limit`` links most likely to be ``want``'s full text, best
    first, each with its ``score``; page order when nothing is wanted."""
    if not _words(want):
        return [dict(link, score=0.0) for link in links[:limit]]
    scored = [dict(link, score=score(link, want)) for link in links]
    order = sorted(range(len(scored)), key=lambda i: (-scored[i]["score"], i))
    return [scored[i] for i in order[:limit]]
