"""Reading a URL for the chat: one guarded GET of a page, a file or a PDF,
read for the model (``fetch_url`` in ``ai_tools/web.py``, and ``fetch_paper``'s
page fallback in gamma/ai_web.py), and the windows a long one is read in.

``get`` is the one page GET both readers use: the browser's headers,
through the SSRF guard (gamma/net_guard.py), its body capped by what it is
— a PDF (or a body that starts like one) is refused past
:data:`PDF_MAX_BYTES`, anything else is cut at :data:`PAGE_MAX_BYTES` and
flagged ``truncated`` — and a page read with gamma/web_page.py. A meta
refresh is followed only from a page with almost nothing to read (Elsevier's
linkinghub is such an interstitial; a readable page that refreshes to a
sign-in after five minutes is not one), at most two hops.

A response that is a wall only a person gets past — a CAPTCHA or bot check,
a sign-in page, a refusal, a rate limit, a page that needs JavaScript —
raises FetchError naming it (``WALLS``) and the page a person would open
(``entry``). A paper's fetch hands that to the user's browser
(gamma/fetch_handoff.py); a web page's fetch tells the model in a sentence.
The two read walls differently (``access_wall``'s ``general``): a paper's
landing page with a password field is a sign-in, while any web page may
mention a CAPTCHA or sit under ``/login/help``.

Read documents are kept in memory (``DocCache``), never on disk, by account
and usable publisher cookies (publisher_sessions.cache_scope()). The web
pages ``fetch_page`` reads have a cache of their own, which forgets a page
unused for 15 minutes, so a page read again later is read as it is now; the
papers' cache (gamma/ai_web.py) keeps a paper until it is crowded out, and
neither ever answers for the other.
"""

import codecs
import http.client
import re
import threading
import time
from html import unescape
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import Request as URLRequest

from . import publisher_sessions, web_page
from .db import page_now
from .logbuf import log
from .net_guard import browsing_session, guarded_urlopen
from .pdf_text import extract_pages

# Realistic browser headers get past simple UA filters (many hosts 403 bare bots)
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# What a page GET asks for: a page first, then text, JSON and PDF.
_PAGE_ACCEPT = ("text/html,application/xhtml+xml,text/plain;q=0.9,text/*;q=0.8,application/json;q=0.8,"
                "application/pdf;q=0.8,*/*;q=0.5")

PDF_MAX_BYTES = 40_000_000   # a PDF larger than this is refused, not read
PAGE_MAX_BYTES = 5_000_000   # anything else is read up to here and flagged truncated
_PDF_TYPES = ("application/pdf", "application/octet-stream")  # refused by Content-Length before reading
_REFRESH_HOPS = 2            # meta refreshes followed from an interstitial
_REDIRECT_PAGE = 500         # readable chars below which a page with a meta refresh only redirects
_THIN_PAGE = 2000            # readable chars below which a page may be all widget or form


# What stood between the server and a document, in the model's words. A
# person can get past each of them in their own browser.
WALLS = {
    "captcha": "a CAPTCHA or bot check",
    "login": "a sign-in page",
    "denied": "a refusal to this server (no access)",
    "rate": "a rate limit",
    "script": "a page that only works in a browser (it runs on JavaScript)",
    "abstract": "the article page only (the full text needs access)",
}


class FetchError(Exception):
    """A document could not be fetched; the message is for the model.
    ``wall`` (a WALLS key) is set when a person could get past what stopped
    it in a browser; ``open_url`` is the page they would open and
    ``pdf_url`` the PDF link it was about."""

    def __init__(self, message: str, *, wall: str = "", open_url: str = "", pdf_url: str = ""):
        super().__init__(message)
        self.wall, self.open_url, self.pdf_url = wall, open_url, pdf_url


# ------------------------------------------------------------------ walls

# Walls a person passes in a browser. A bot-check page is recognized by its
# vendor's markers or its title (Cloudflare, Fastly, DataDome, PerimeterX,
# Imperva, Akamai, Google's "unusual traffic"); a CAPTCHA widget or a
# password field only on a page with little else to read — an article page
# may carry a newsletter form. A sign-in page also by where the request
# ended up.
_CHALLENGE_RE = re.compile(
    r"_cf_chl_opt|cf-browser-verification|/cdn-cgi/challenge-platform/|/_fs-ch-|"
    r"<title>\s*(?:just a moment|attention required|access denied|pardon our interruption|"
    r"client challenge|checking your browser|"
    r"are you a robot|robot check|security check|verify(?:ing)? (?:that )?you are (?:a )?human)|"
    r"""captcha-delivery\.com|id=["']px-captcha|_Incapsula_Resource|"""
    r"unusual traffic from your (?:computer|network)", re.I)
_WIDGET_RE = re.compile(r"g-recaptcha|hcaptcha\.com|h-captcha|cf-turnstile", re.I)
_PASSWORD_RE = re.compile(r"""<input\b[^>]*\btype\s*=\s*["']?password""", re.I)
_LOGIN_PATH_RE = re.compile(
    r"/(?:(?:show)?login|log-in|logon|signin|sign-in|sso|ssostart|idp|shibboleth|wayf|saml2?|"
    r"authorize|authorization|auth/realms|cas/login)(?:[/.?;]|$)", re.I)
_LOGIN_HOST_RE = re.compile(r"^(?:login|idp|sso|signin|auth|shibboleth|wayf)\.", re.I)
# Hosts that serve nothing but a bot check for other sites (Radware's
# validate.perfdrive.com in front of IOP, DataDome's captcha host).
_CHECK_HOST_RE = re.compile(
    r"(?:^|\.)(?:perfdrive\.com|captcha-delivery\.com|hcaptcha\.com|challenges\.cloudflare\.com)$", re.I)
# Query keys an interstitial keeps the page to go back to under.
_RETURN_KEYS = {"ssc", "referer", "referrer", "return", "returnurl", "return_url", "returnto", "redirect",
                "redirecturl", "redirect_uri", "next", "url", "target", "continue", "uri", "dest",
                "destination", "service", "goto"}


def access_wall(url: str, headers, body: bytes, visible: int | None = None, *, general: bool = False) -> str:
    """"captcha" / "login" when a response is an interstitial for a person
    rather than what was asked for, else "". ``visible`` is how much text
    the page shows (web_page's ``Page.visible``), when the caller has read
    it already: a page is thin by all of its text, menus included, not by
    its main content, so a short article under a header's sign-in box reads.

    Cloudflare's ``cf-mitigated`` header and a bot-check host always count.
    For a paper (``general`` off) so do a vendor's challenge markers and a
    sign-in address anywhere, and a CAPTCHA widget or password field on a
    thin page. For any web page (``general``) every body marker and the
    sign-in address count only on a thin HTML page: a docs page under
    ``/login/help`` or a script that mentions ``_cf_chl_opt`` is read."""
    headers = headers or {}
    if (headers.get("cf-mitigated") or "").lower() == "challenge" or _check_host(url):
        return "captcha"
    ctype = (headers.get("Content-Type") or "").lower()
    head = body[:300_000].decode("utf-8", "replace") if body else ""
    if general and not ("html" in ctype or (not ctype and head.lstrip().startswith("<"))):
        return ""
    if not general:
        if _CHALLENGE_RE.search(head):
            return "captcha"
        if _sign_in_url(url):
            return "login"
    if (visible if visible is not None else web_page.visible_chars(body, ctype)) >= _THIN_PAGE:
        return ""
    if _WIDGET_RE.search(head) or (general and _CHALLENGE_RE.search(head)):
        return "captcha"
    if _PASSWORD_RE.search(head) or (general and _sign_in_url(url)):
        return "login"
    return ""


def refusal(e: HTTPError) -> str:
    """The wall behind an HTTP error, else "": a bot check served as an error
    page (Cloudflare answers 403 or 503), a rate limit, a refusal."""
    try:
        body = e.read(300_000) or b""
    except Exception:
        body = b""
    if ((e.headers or {}).get("cf-mitigated") or "").lower() == "challenge" \
            or _CHALLENGE_RE.search(body.decode("utf-8", "replace")):
        return "captcha"
    if e.code == 429:
        return "rate"
    return "denied" if e.code in (401, 403, 418) else ""


def entry(*urls: str) -> str:
    """The page a person should open for a blocked fetch: the first of
    ``urls`` (most specific first) that is the site's own page — for a
    sign-in or bot-check page, the page it would return to. Starting there,
    the site sends the person through its check and back to the paper."""
    for url in urls:
        if url and not _interstitial(url):
            return url
        back = _return_url(url) if url else ""
        if back:
            return back
    return next((u for u in urls if u), "")


def _sign_in_url(url: str) -> bool:
    where = urlsplit(url or "")
    return bool(_LOGIN_PATH_RE.search(where.path) or _LOGIN_HOST_RE.match(where.hostname or ""))


def _check_host(url: str) -> bool:
    return bool(_CHECK_HOST_RE.search(urlsplit(url or "").hostname or ""))


def _interstitial(url: str) -> bool:
    return _sign_in_url(url) or _check_host(url)


def _return_url(url: str) -> str:
    """The page an interstitial sends the visitor back to, when its address
    carries it (Radware's ``ssc=``, a sign-in page's ``next=`` / ``uri=``)."""
    try:
        for key, value in parse_qsl(urlsplit(url).query):
            if key.lower() in _RETURN_KEYS:
                back = urljoin(url, value.strip())
                if back.lower().startswith(("http://", "https://")) and not _interstitial(back):
                    return back
    except ValueError:  # an address that does not parse (http://[oops/)
        pass
    return ""


# ---------------------------------------------------------------- addresses

_REFRESH_TAG_RE = re.compile(r"<meta\b[^>]*http-equiv\s*=\s*[\"']?refresh[^>]*>", re.I)
_REFRESH_CONTENT_RE = re.compile(r"""\bcontent\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_REFRESH_URL_RE = re.compile(r"""^\s*[\d.]*\s*[;,]\s*url\s*=\s*['"]?([^'"]+)""", re.I)


def meta_refresh(html: str, base: str) -> str:
    """Where a page's ``<meta http-equiv="refresh">`` sends the visitor,
    absolute, or "". Some DOI landings are only that (Elsevier's
    linkinghub → ScienceDirect), so a fetch follows it like a 3xx."""
    tag = _REFRESH_TAG_RE.search(html[:50_000])
    content = _REFRESH_CONTENT_RE.search(tag.group(0)) if tag else None
    m = _REFRESH_URL_RE.match((content.group(1) or content.group(2)) if content else "")
    if not m:
        return ""
    try:
        url = urljoin(base, unescape(m.group(1).strip()))
    except ValueError:  # an address that does not parse (http://[oops/)
        return ""
    return url if url.lower().startswith(("http://", "https://")) and url != base else ""


_HOST_PORT = re.compile(r"(.*?)(:[0-9]*)?", re.S)
# What stays as it is in a path, query or fragment: every printable ASCII
# character a browser sends as it is, the escapes an address has among them;
# anything else (光, a space, a quote, < and >) is escaped as UTF-8.
_KEEP = "".join(c for c in map(chr, range(33, 127)) if c not in '"<>')


def uri(url: str) -> str:
    """``url`` as a URI a request can carry: an internationalized host in
    its IDNA (punycode) form, and the other characters of its path, query
    and fragment percent-encoded as UTF-8. Printable ASCII and escapes stay,
    so it names the same page. A model copies addresses as a page shows
    them (web_page decodes them for reading), and this runs before the SSRF
    guard looks at the host: a host written with ideographic or enclosed
    digits and dots (``127。0。0。1``, ``①②⑦.0.0.1``) is the address it
    spells. Raises FetchError for an address it cannot encode."""
    try:
        parts = urlsplit((url or "").strip())
        netloc = parts.netloc
        if not netloc.isascii():
            userinfo, at, hostport = netloc.rpartition("@")
            host, port = _HOST_PORT.fullmatch(hostport).groups()
            netloc = quote(userinfo, safe=_KEEP) + at + host.encode("idna").decode("ascii") + (port or "")
        return urlunsplit((parts.scheme, netloc, quote(parts.path, safe=_KEEP),
                           quote(parts.query, safe=_KEEP), quote(parts.fragment, safe=_KEEP)))
    except (UnicodeError, ValueError) as e:
        raise FetchError(f"{url} is not an address that can be fetched ({e})")


# ------------------------------------------------------------------ fetching

class Response(NamedTuple):
    url: str          # where the request ended, after redirects
    ctype: str        # its Content-Type, lowercased
    body: bytes
    headers: object   # the response headers
    truncated: bool   # the body was cut at PAGE_MAX_BYTES


class Fetched(NamedTuple):
    url: str
    ctype: str
    body: bytes
    headers: object
    truncated: bool
    page: web_page.Page | None   # None: the body is a PDF


def _mb(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def _too_large(size: int) -> FetchError:
    return FetchError(f"document too large ({_mb(size)}, cap {_mb(PDF_MAX_BYTES)})")


def read_bounded(url: str, headers: dict, timeout: int = 30) -> Response:
    """One GET through the SSRF guard, its body capped by what it is: a PDF
    (``application/pdf``, or a body that starts ``%PDF-``) is refused past
    PDF_MAX_BYTES, and anything else is cut at PAGE_MAX_BYTES and flagged
    ``truncated``. A PDF or a download (``application/octet-stream``) whose
    Content-Length is over the cap is refused before a byte is read.
    Raises FetchError for a PDF too large, and urllib's errors as they
    come."""
    with guarded_urlopen(URLRequest(url, headers=headers), timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        length = resp.headers.get("Content-Length") or ""
        size = int(length) if length.isdigit() else 0
        if any(t in ctype for t in _PDF_TYPES) and size > PDF_MAX_BYTES:
            raise _too_large(size)
        chunks = [resp.read(65536)]
        pdf = "application/pdf" in ctype or chunks[0].startswith(b"%PDF-")
        if pdf and size > PDF_MAX_BYTES:
            raise _too_large(size)
        cap = PDF_MAX_BYTES if pdf else PAGE_MAX_BYTES
        total = len(chunks[0])
        while chunks[-1] and total <= cap:
            chunks.append(resp.read(65536))
            total += len(chunks[-1])
        if pdf and total > cap:
            raise FetchError(f"document too large (over {_mb(cap)})")
        return Response(resp.geturl(), ctype, b"".join(chunks)[:cap], resp.headers, total > cap)


def _response(url: str, headers: dict) -> Response:
    """read_bounded with urllib's errors as FetchErrors in the model's words,
    an HTTP error's wall named (``refusal``)."""
    try:
        return read_bounded(url, headers)
    except HTTPError as e:
        raise FetchError(f"the page {url} answered HTTP {e.code}"
                         + (" — the site blocks server-side fetching" if e.code in (401, 403) else ""),
                         wall=refusal(e), open_url=entry(e.geturl() or url))
    except (URLError, OSError, ValueError, http.client.HTTPException) as e:
        raise FetchError(f"{url} could not be fetched: {e}")


def _binary(got: Response) -> bool:
    """A body that is no text: a picture, sound, video or font, or one with
    a NUL byte near its start (an archive, an office file) that is no
    UTF-16 text."""
    return (got.ctype.split("/", 1)[0] in ("image", "audio", "video", "font", "model")
            or (b"\x00" in got.body[:1024] and not got.body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))))


def get(url: str, *, general: bool = False) -> Fetched:
    """The response ``url`` gives one GET (the browser's headers with an
    Accept for pages, text, JSON and PDF), with its body read: a PDF
    as it came (``page`` None), any other text by web_page.read. A meta
    refresh is followed from a page with almost nothing to read, at most
    two hops.

    Raises FetchError: an HTTP error with its wall (``refusal``); a wall on
    the page (``access_wall``, ``general`` as it says); an HTML page with no
    readable text (the "script" wall: it needs a browser); a picture,
    archive or other body that is no text. A wall's ``open_url`` is the
    page a person would open."""
    asked = {**BROWSER_HEADERS, "Accept": _PAGE_ACCEPT}
    for hop in range(_REFRESH_HOPS + 1):
        got = _response(url, asked)
        if "application/pdf" in got.ctype or got.body.startswith(b"%PDF-"):
            return Fetched(*got, None)
        if _binary(got):
            raise FetchError(f"{got.url} is not a web page or text ({got.ctype or 'no content type'})")
        page = web_page.read(got.body, got.url, got.ctype)
        redirect = page.kind == "html" and page.visible < _REDIRECT_PAGE and hop < _REFRESH_HOPS
        url = meta_refresh(web_page.decode(got.body, got.ctype), got.url) if redirect else ""
        if not url:
            break
    wall = access_wall(got.url, got.headers, got.body, page.visible, general=general)
    if wall:
        raise FetchError(f"the page {got.url} is {WALLS[wall]}", wall=wall, open_url=entry(got.url))
    if not page.text.strip():
        if page.kind == "html":
            # A page with nothing to read without its scripts: a browser shows it.
            raise FetchError(f"the page {got.url} has no readable text", wall="script", open_url=entry(got.url))
        raise FetchError(f"{got.url} is empty")
    return Fetched(*got, page)


def pdf_document(url: str, data: bytes, note: str = "", version: str = "") -> dict:
    """A PDF as the caches hold it: its text page by page. Raises FetchError
    when there is no text to read."""
    try:
        pages = extract_pages(data)
    except Exception as e:
        log.warning(f"[web_fetch] extraction failed for {url}: {e}")
        raise FetchError(f"the PDF at {url} could not be read ({e})")
    if not any(p.strip() for p in pages):
        raise FetchError(f"the PDF at {url} has no text layer (a scan?)")
    return {"url": url, "kind": "pdf", "title": "", "pages": pages,
            "chars": sum(len(p) for p in pages), "note": note, "version": version}


def page_document(got: Fetched) -> dict:
    """A read page (``got.page``) as the caches hold it: its text as one
    page, with its title, metadata, outline, the day it was fetched (UTC)
    and whether its body was cut at PAGE_MAX_BYTES."""
    page = got.page
    return {"url": got.url, "kind": page.kind, "title": page.title, "pages": [page.text],
            "chars": len(page.text), "meta": page.meta, "outline": page.outline,
            "fetched": page_now()[:10], "truncated": got.truncated}


# ------------------------------------------------------------------ caches

_clock = time.monotonic  # what a cache's idle time is measured by (tests replace it)


class DocCache:
    """Read documents in memory, the least recently used let go first: at
    most ``docs`` of them and ``chars`` characters of text in all and, with
    a ``ttl``, none unused for longer than that many seconds. Each entry is
    kept under its ``scope`` (publisher_sessions.cache_scope(): the account
    and its usable publisher cookies), so one account's authenticated text
    never answers another's and new access is never masked by an earlier
    blocked page. A document lives under its URL, and the keys ``put``
    names it by (the sources it was asked for as) lead to it too."""

    def __init__(self, docs: int, chars: int, ttl: float | None = None):
        self.docs, self.chars, self.ttl = docs, chars, ttl
        self._entries: dict = {}   # (scope, URL) → (document, last use); oldest use first
        self._aliases: dict = {}   # (scope, key) → (scope, URL)
        self._lock = threading.Lock()

    def get(self, scope, key: str) -> dict | None:
        with self._lock:
            where = self._aliases.get((scope, key), (scope, key))
            kept = self._entries.pop(where, None)
            if kept is None:
                return None
            doc, used = kept
            if self.ttl is not None and _clock() - used > self.ttl:
                self._unalias(where)
                return None
            self._entries[where] = (doc, _clock())
            return doc

    def put(self, scope, keys, doc: dict):
        where = (scope, doc["url"])
        with self._lock:
            self._entries.pop(where, None)
            self._entries[where] = (doc, _clock())
            for key in keys:
                self._aliases[scope, key] = where
            total = sum(kept["chars"] for kept, _ in self._entries.values())
            while self._entries and (len(self._entries) > self.docs or total > self.chars):
                old = next(iter(self._entries))
                total -= self._entries.pop(old)[0]["chars"]
                self._unalias(old)

    def _unalias(self, where):
        for key in [key for key, to in self._aliases.items() if to == where]:
            del self._aliases[key]

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._aliases.clear()


PAGE_TTL = 15 * 60   # a web page unused this long is read anew
_pages = DocCache(24, 10_000_000, ttl=PAGE_TTL)


def fetch_page(url: str) -> dict:
    """The page or file at ``url`` (fetch_url's reader), from the page cache
    when it was read in the last PAGE_TTL seconds of use: a web page or
    text as ``{"url", "kind": "html"|"text", "title", "pages": [text],
    "chars", "meta", "outline", "fetched": "YYYY-MM-DD", "truncated"}``
    (page_document), a PDF as pdf_document's (no version) with ``fetched``.
    A document this call downloaded also carries ``"fresh": True``, so a
    continuation can say the page was read anew and may have changed since
    the windows before it. Walls are read as for any web page
    (``general``). Raises FetchError."""
    url = urldefrag(uri(url)).url
    if not url.lower().startswith(("http://", "https://")):
        raise FetchError("pass an http(s) URL")
    scope = publisher_sessions.cache_scope()
    doc = _pages.get(scope, url)
    if doc is not None:
        return doc
    with browsing_session():
        got = get(url, general=True)
    doc = {**pdf_document(got.url, got.body), "fetched": page_now()[:10]} if got.page is None else page_document(got)
    _pages.put(scope, [url], doc)
    return {**doc, "fresh": True}


def clear_cache():
    _pages.clear()


# ------------------------------------------------------------------ windows

_PAGE_GAP = "\n\n"   # between the pages a window is cut from
_FIND_LEAD = 300     # how far ``find`` backs up to the start of the line


def window(doc: dict, limit: int, offset: int = 0, start_page: int = 1) -> tuple[str, int | None, int]:
    """``(text, next_offset, total)``: ``limit`` chars of the document from
    ``offset`` chars after the start of PDF page ``start_page`` (1-based; every
    page's text is prefixed ``[p. N]`` so the model can cite pages), the offset
    a follow-up read continues from (None = the document ended inside this
    window), and the total chars from start_page on."""
    full = _joined(doc, start_page)
    text = full[offset:offset + limit]
    next_offset = offset + limit if len(full) > offset + limit else None
    return text, next_offset, len(full)


def window_pages(doc: dict, limit: int, offset: int = 0, start_page: int = 1) -> tuple[int, int] | None:
    """The PDF pages that same window holds text of, ``(first, last)``; None
    for a web page, which has no pages, and for a window past the end."""
    at, held = 0, []
    for page, text in _page_texts(doc, start_page):
        if page and at < offset + limit and at + len(text) > offset:
            held.append(page)
        at += len(text) + len(_PAGE_GAP)
    return (held[0], held[-1]) if held else None


def find(doc: dict, phrase: str, offset: int = 0, start_page: int = 1) -> int | None:
    """The offset a window showing ``phrase`` starts at: its first match (in
    any case, its words apart by any white space) at or after ``offset`` in
    the text ``window`` cuts from ``start_page`` on, backed up to the start
    of its line, at most _FIND_LEAD characters and never before ``offset``.
    None when the text does not hold it."""
    words = (phrase or "").split()
    if not words:
        return None
    full = _joined(doc, start_page)
    m = re.compile(r"\s+".join(map(re.escape, words)), re.I).search(full, max(0, offset))
    if not m:
        return None
    return max(full.rfind("\n", 0, m.start()) + 1, m.start() - _FIND_LEAD, offset)


def _joined(doc: dict, start_page: int) -> str:
    return _PAGE_GAP.join(text for _, text in _page_texts(doc, start_page))


def _page_texts(doc: dict, start_page: int) -> list:
    """What a window is cut from: ``(page, text)`` for each PDF page with
    text from ``start_page`` on, its text under its ``[p. N]`` line; a web
    page's text as page 0."""
    pages = doc["pages"]
    if doc["kind"] != "pdf":
        return [(0, text) for text in pages]
    start = max(1, min(start_page, len(pages)))
    return [(n, f"[p. {n}]\n{text}") for n, text in enumerate(pages[start - 1:], start) if text.strip()]
