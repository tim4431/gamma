"""Rank PDF links on lab and repository pages before applying a result cap."""

import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


def title_key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").casefold()
    return "".join(c for c in text if c.isalnum())


class _Links(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.links, self.parts = base, [], []
        self.anchor = None
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "template"):
            self.hidden += 1
        if self.hidden:
            return
        if tag == "a":
            self.handle_endtag("a")
            href = dict(attrs).get("href") or ""
            try:
                url = urljoin(self.base, href)
                parsed = urlsplit(url)
                valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username
            except ValueError:
                valid = False
            if valid and href and not href.startswith("#"):
                self.anchor = {"url": url, "start": len(self.parts), "label": []}
        elif tag == "img" and self.anchor is not None:
            self.anchor["label"].append(dict(attrs).get("alt") or "")
        elif tag == "br":
            self.parts.append(" ")
        elif tag in ("p", "li", "tr", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "template"):
            self.hidden = max(0, self.hidden - 1)
        if self.hidden:
            return
        if tag == "a" and self.anchor is not None:
            self.anchor["end"] = len(self.parts)
            self.links.append(self.anchor)
            self.anchor = None
        elif tag in ("p", "li", "tr", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.hidden:
            return
        self.parts.append(data)
        if self.anchor is not None:
            self.anchor["label"].append(data)


def pdf_link_candidates(markup: str, base: str, query: str = "", limit: int = 8) -> list[dict]:
    """Return {url, title, context} links; query relevance precedes document order.

    Context is confined to the surrounding block where possible, so a generic
    "PDF" anchor can still be associated with the title in its citation.
    Returned URLs remain untrusted and must use the normal guarded fetch.
    """
    parser = _Links(base)
    parser.feed(markup)
    parser.handle_endtag("a")
    query_key = title_key(query)
    words = set(re.findall(r"\w{3,}", unicodedata.normalize("NFKD", query).casefold()))
    candidates = {}
    for link in parser.links:
        url = link["url"]
        title = re.sub(r"\s+", " ", " ".join(link["label"])).strip()[:500]
        path = urlsplit(url).path.lower()
        if not (path.endswith((".pdf", "/pdf")) or "/pdf/" in path
                or re.search(r"\bpdf\b", title, re.I)):
            continue
        before = "".join(parser.parts[max(0, link["start"] - 30):link["start"]]).rsplit("\n", 1)[-1][-250:]
        after = "".join(parser.parts[link["end"]:link["end"] + 30]).split("\n", 1)[0][:250]
        context = re.sub(r"\s+", " ", f"{before} {title} {after}").strip()[:800]
        label_key, context_key = title_key(title), title_key(context)
        hay = set(re.findall(r"\w{3,}", unicodedata.normalize("NFKD", context).casefold()))
        score = (100 if query_key and query_key == label_key else
                 80 if query_key and query_key in label_key else
                 60 if query_key and query_key in context_key else 0)
        score += len(words & hay) / max(1, len(words))
        candidate = {"url": url, "title": title, "context": context, "score": score}
        if url not in candidates or score > candidates[url]["score"]:
            candidates[url] = candidate
    ranked = sorted(candidates.values(), key=lambda c: -c["score"])
    return [{k: v for k, v in c.items() if k != "score"} for c in ranked[:max(1, min(limit, 50))]]
