"""A web page as note blocks: the HTML the Gamma Connector clipped — a page's
article, which Mozilla's Readability finds in the browser, or the text
selected on it — as Markdown, which ``markdown_import.md_to_blocks`` makes
into blocks the way it does a .md file's.

The browser does what needs the live page (extension/clip.js): finding the
article, an image's real address (a lazy one's, a srcset's), a formula's TeX
or MathML, a code block's language, and the pictures' bytes, which it uploads
itself and names here by their address (``images``). What arrives is a clean
fragment, so the reading is shallow: ``html_markdown``'s tree and its
Markdown writer, which this module's ``ClipMarkdown`` makes a note's.

- Text is escaped where Markdown would read it as markup (the editor renders
  raw HTML, ``$`` math and ``*`` emphasis), and so is a paragraph's line that
  would open a heading, a quote, a list item, a rule, a fence or a table row.
- A picture the browser uploaded points at its stored copy, any other at its
  address on the web: a picture Gamma can't store or the browser couldn't
  fetch still shows while the site serves it. A tracking pixel says nothing.
- A link points at its absolute address, a ``mailto:`` one too; an anchor
  into the page itself is plain text.
- Headings keep their level, so the blocks nest under them as a .md file's
  do. A first heading that only repeats the title is left out, and a source
  line (``[site](url) · byline · date``) comes first.
"""

import re
from urllib.parse import urlsplit

from .html_markdown import DEPTH, SPACE, El, Markdown, Parser, attr
from .markdown_import import md_to_blocks

# The clipped fragment, as the request may carry it.
MAX_HTML_CHARS = 5 * 1024 * 1024

# A stored copy the browser uploaded (POST /api/upload-image).
_STORED = re.compile(r"/api/uploads/[A-Za-z0-9._-]+")
# What a line of a paragraph must not start with, or Markdown reads it as a
# heading, a quote, a list item, a rule, a fence or a table row.
_LEAD = re.compile(r"(#{1,6}(?:\s|$)|>|[-+](?:\s|$)|\d{1,9}[.)](?:\s|$)|=+\s*$|```|~~~|\|)")


class _Fragment(Parser):
    """The fragment as a tree no deeper than the writer writes. The browser's
    serializer closes every element, so it is read with ``keep_p``: a <p>
    holds what the page put in it."""
    max_open = DEPTH


def _escape(text: str) -> str:
    """Plain text as Markdown that shows it as it is."""
    text = text.replace("\\", "\\\\").replace("`", "\\`").replace("*", "\\*").replace("$", "\\$")
    text = re.sub(r"(?<![0-9A-Za-z])_|_(?![0-9A-Za-z])", r"\\_", text)
    text = re.sub(r"<(?=[A-Za-z/!?])", r"\\<", text)
    text = re.sub(r"&(?=#?[0-9A-Za-z]+;)", r"\\&", text)
    return text.replace("~~", "\\~\\~")


def _unlead(line: str) -> str:
    if not _LEAD.match(line):
        return line
    number = re.match(r"\d+", line)
    # A number's escape goes before its dot: "1\." (a backslash before a
    # digit is a backslash).
    return f"{number.group(0)}\\{line[number.end():]}" if number else "\\" + line


class ClipMarkdown(Markdown):
    """The clipper's writer: text escaped where Markdown would read it as
    markup, ``mailto:`` links kept, and pictures as ``![alt](url)`` — the
    copy the browser uploaded (``images``, a picture's address as the page
    had it → ``/api/uploads/<name>``) else the web address, never a tracking
    pixel. ``stored`` collects the names of the uploads it shows."""

    def __init__(self, base_url: str, images: dict):
        super().__init__(base_url)
        self.images = {k: v for k, v in (images or {}).items()
                       if isinstance(k, str) and isinstance(v, str) and _STORED.fullmatch(v)}
        self.stored: set[str] = set()

    def text(self, s: str) -> str:
        return _escape(s)

    def line(self, s: str) -> str:
        return _unlead(s)

    def href(self, raw: str) -> str:
        url, scheme = self.resolve(raw)
        if scheme == "mailto":
            return url.replace(" ", "%20")
        return super().href(raw)

    def image(self, node: El) -> str:
        src = attr(node, "src").strip()
        if any(attr(node, side).strip() in ("0", "1", "2") for side in ("width", "height")):
            return ""  # a tracking pixel
        url = self.images.get(src, "")
        if url:
            self.stored.add(url.rsplit("/", 1)[1])
        else:
            url = self.address(src)
        if not url:
            return ""
        alt = SPACE.sub(" ", attr(node, "alt")).strip().replace("[", "\\[").replace("]", "\\]")
        return f"![{alt}]({url})"


def to_markdown(html: str, base_url: str = "", images: dict | None = None) -> tuple[str, set[str]]:
    """The clipped fragment ``html`` as Markdown, read against the page's
    address ``base_url``; ``images`` maps a picture's address as the page had
    it to its uploaded copy (``/api/uploads/<name>``). → ``(markdown, the
    names of the uploads it shows)``."""
    parser = _Fragment(keep_p=True)
    parser.feed(html or "")
    parser.close()
    md = ClipMarkdown(base_url, images or {})
    return "\n\n".join(md.blocks(parser.root.children)), md.stored


def source_line(url: str, site: str = "", byline: str = "", published: str = "") -> str:
    """The first block of a clipped article: where it came from, by whom and
    when — ``[site](url) · byline · date``."""
    url = (url or "").strip()
    host = urlsplit(url).netloc if url else ""
    name = _escape(SPACE.sub(" ", site or host).strip()).replace("]", "\\]")
    parts = [f"[{name}]({url.replace(' ', '%20').replace('(', '%28').replace(')', '%29')})" if url and name else ""]
    parts.append(_escape(SPACE.sub(" ", byline or "").strip()[:200]))
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
