"""The agent's scholarly reach: searching the registries, following a
paper's citations, and reading a document that is not in the library
(``search_papers`` / ``related_papers`` / ``fetch_paper`` in
``ai_tools.py``; general web search is gamma/search_services.py).

Nothing here writes to a workspace. ``search_papers`` asks three registries
at once — Crossref and arXiv (the ones the metadata lookup uses) and
OpenAlex (gamma/openalex.py) — and merges what they return into one record
per work, with its abstract and citation count when a registry has them; a
bare DOI / arXiv id is looked up directly. ``related_papers`` walks
OpenAlex's citation graph from one work. ``fetch_document`` turns a DOI,
arXiv id or URL into text: the PDF behind it through the resolver the
extension and "open a link" use (``routers.pdf.resolve_source`` — arXiv abs
pages, publisher ``citation_pdf_url`` tags, several Unpaywall open-access
copies, the SSRF guard), extracted page by page; when no PDF is reachable
and the source is a web page, its readable text instead, with the page's
PDF links (gamma/paper_links.py) for the model to rank against the paper it
wants. Fetched documents live in a small in-memory cache so the model can
read a long paper in successive windows without re-downloading it — nothing
is stored on disk and a restart forgets everything.

A fetch that meets a wall only a person gets past — a CAPTCHA or bot check,
a sign-in page, a refusal, a paywall that serves only the article page — says
which (``FetchError.wall`` / a document's ``wall``) and where the person
would go, so the chat can hand the fetch to the user's browser
(``gamma/fetch_handoff.py``).
"""

import html
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, quote, urljoin, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest

from fastapi import HTTPException

from . import openalex, paper_links, publisher_sessions
from .logbuf import log
from .net_guard import browsing_session, guarded_urlopen
from .pdf_text import extract_pages

SEARCH_LIMIT_DEFAULT = 8
SEARCH_LIMIT_MAX = 20
SORTS = ("relevance", "citations", "recent")
_ABSTRACT_SHOWN = 400  # chars of a record's abstract a search result shows
FETCH_MAX_BYTES = 40_000_000    # a PDF larger than this is refused, not read
HTML_MAX_BYTES = 2_000_000      # of a web page, before tag stripping
# The document cache: entries and total extracted chars kept in memory.
_CACHE_MAX_DOCS = 24
_CACHE_MAX_CHARS = 30_000_000

_DOI_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)$", re.I)
_ARXIV_RE = re.compile(
    r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?"
    r"([0-9]{4}\.[0-9]{4,5}|[a-z][a-z-]*(?:\.[a-z]{2})?/[0-9]{7})(?:v\d+)?(?:\.pdf)?$", re.I)

_cache: dict = {}        # (account/session scope, resolved URL) → document
_aliases: dict = {}      # (account/session scope, source string) → cache key
_cache_lock = threading.Lock()


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

    @property
    def access_blocked(self) -> bool:
        return bool(self.wall)


# ---------------------------------------------------------------- search

def identifier(text: str) -> tuple[str, str]:
    """``("doi", id)`` / ``("arxiv", id)`` when ``text`` is a bare or URL-form
    identifier, else ``("", "")``."""
    text = (text or "").strip().rstrip(".,;")
    m = _ARXIV_RE.match(text)
    if m:
        return "arxiv", m.group(1)
    m = _DOI_RE.match(text)
    if m:
        return "doi", m.group(1)
    return "", ""


def _keys_of(rec: dict) -> set:
    """What makes two registries' records the same work: a DOI, an arXiv
    id, or the whole normalized title."""
    return {k for k in (
        f"doi:{rec.get('doi', '').lower()}" if rec.get("doi") else "",
        f"arxiv:{rec.get('arxiv_id', '').lower()}" if rec.get("arxiv_id") else "",
        "title:" + _title_key(rec.get("title", "")),
    ) if k and k != "title:"}


def _merge(lists: list[list[dict]]) -> list[dict]:
    """The registries' lists interleaved in their own relevance order, one
    record per work. A work two registries return keeps the fields the
    first lacks — a journal record its arXiv preprint (and an arXiv record
    its DOI), the abstract, the open-access PDF — and the larger citation
    count."""
    out: list[dict] = []
    kept: dict = {}  # DOI / arXiv id / title key → the record kept for it
    for rank in range(max((len(lst) for lst in lists), default=0)):
        for lst in lists:
            rec = lst[rank] if rank < len(lst) else None
            if not rec:
                continue
            keys = _keys_of(rec)
            twin = next((kept[k] for k in keys if k in kept), None)
            if twin is None:
                twin = dict(rec)
                out.append(twin)
            else:
                for field in ("doi", "arxiv_id", "abstract", "oa_pdf", "venue", "openalex_id"):
                    if rec.get(field) and not twin.get(field):
                        twin[field] = rec[field]
                if (rec.get("cited_by") or 0) > (twin.get("cited_by") or 0):
                    twin["cited_by"] = rec["cited_by"]
            for k in keys | _keys_of(twin):
                kept.setdefault(k, twin)
    return out


def _year(rec: dict) -> int:
    m = re.match(r"\d{4}", str(rec.get("year") or ""))
    return int(m.group(0)) if m else 0


def _enriched(rec: dict, key: str) -> dict:
    """An identifier lookup's record with what OpenAlex adds (abstract,
    citation count, open-access PDF) — a lookup by id, which costs nothing."""
    kind, ident = ("doi", rec["doi"]) if rec.get("doi") else ("arxiv", rec.get("arxiv_id", ""))
    try:
        work = openalex.lookup(kind, ident, key) if ident else None
    except openalex.OpenAlexError:
        work = None
    return _merge([[rec], [openalex.record(work)] if work else []])[0]


# What `kind` narrows a search to, and which registries can answer it: a
# work published in a journal (arXiv holds none), or a preprint (Crossref's
# bibliographic search is about the published record).
KINDS = ("any", "article", "preprint")


def _free_full_text(rec: dict) -> bool:
    """Whether a record names a PDF anyone can read (an arXiv copy, or an
    open-access location Unpaywall/OpenAlex found)."""
    return bool(rec.get("arxiv_id") or rec.get("oa_pdf"))


def search_papers(query: str, limit: int = SEARCH_LIMIT_DEFAULT, *, from_year: int = 0,
                  sort: str = "relevance", kind: str = "any", open_access: bool = False,
                  openalex_key: str = "", notes: list | None = None) -> list[dict]:
    """Records for a free-text query — Crossref, arXiv and OpenAlex asked at
    once, merged (``_merge``) — or the one record of an identifier query.
    ``from_year`` keeps works from that year on; ``sort`` orders the merged
    records by relevance (the registries' own order), "citations" or
    "recent" (the registries' own citation sorts ignore relevance, so the
    order is applied here, to the relevant candidates). ``kind`` (KINDS)
    narrows to published articles or to preprints, and ``open_access`` to
    works with a PDF anyone can read. A record whose
    title is exactly the query (a cited reference) ranks first. A
    ``notes`` list collects the registries that did not answer, so the
    model can say what the result lacks."""
    # Local import: keep gamma.* module load free of the routers package.
    from .routers import metadata as registry

    limit = max(1, min(int(limit or SEARCH_LIMIT_DEFAULT), SEARCH_LIMIT_MAX))
    notes = [] if notes is None else notes
    kind = kind if kind in KINDS else "any"
    ident_kind, ident = identifier(query)
    if ident_kind:
        rec = (registry._fetch_arxiv(ident) if ident_kind == "arxiv"
               else registry._fetch_doi(ident, with_bibtex=False)[0])
        return [_enriched(rec, openalex_key)] if rec else []

    def ask_openalex():
        try:
            return openalex.search(query, rows=limit, from_year=from_year, key=openalex_key,
                                   work_type="" if kind == "any" else kind, open_access=open_access)
        except openalex.OpenAlexError as e:
            notes.append(str(e))
            return []

    # OpenAlex leads the interleave: its relevance reads a topic best, where
    # Crossref's puts loosely matching book chapters first. An exact title
    # still ranks first whichever registry found it. A narrowed search skips
    # the registry that cannot hold that kind rather than filtering it out
    # after the fact.
    asks = [ask_openalex]
    if kind != "preprint":
        asks.append(lambda: registry._crossref_search(
            query, limit, True, from_year, "journal-article" if kind == "article" else ""))
    if kind != "article":
        asks.append(lambda: registry._arxiv_search(query, limit, True))
    with ThreadPoolExecutor(max_workers=len(asks)) as pool:
        found = [job.result() for job in [pool.submit(ask) for ask in asks]]
    out = _merge(found)
    if from_year:
        out = [rec for rec in out if not _year(rec) or _year(rec) >= from_year]
    if open_access:
        out = [rec for rec in out if _free_full_text(rec)]
    if sort == "citations":
        out.sort(key=lambda rec: -(rec.get("cited_by") or 0))
    elif sort == "recent":
        out.sort(key=lambda rec: -_year(rec))
    want = _title_key(query)
    out.sort(key=lambda rec: _title_key(rec.get("title", "")) != want)
    return out[:limit]


def related_papers(source: str, relation: str, limit: int = SEARCH_LIMIT_DEFAULT, *,
                   from_year: int = 0, sort: str = "citations",
                   openalex_key: str = "") -> tuple[dict, list[dict]]:
    """``(the work, its linked works)`` for a DOI, an arXiv id or an exact
    title: the works it cites ("references"), the works citing it
    ("citations") or OpenAlex's related works ("similar"), most cited
    first or, with ``sort`` "recent", newest first. An arXiv paper is
    followed through its published DOI when it has one (the journal record
    carries the citations). Raises LookupError when OpenAlex has no such
    work, openalex.OpenAlexError when it does not answer."""
    from .routers import metadata as registry

    limit = max(1, min(int(limit or SEARCH_LIMIT_DEFAULT), SEARCH_LIMIT_MAX))
    kind, ident = identifier(source)
    work = None
    if kind == "arxiv":
        published = (registry._fetch_arxiv(ident) or {}).get("doi")
        work = openalex.lookup("doi", published, openalex_key) if published else None
    if work is None and kind:
        work = openalex.lookup(kind, ident, openalex_key)
    if work is None and not kind and not source.lower().startswith(("http://", "https://")):
        work = openalex.find_title(source, openalex_key)
    if work is None:
        raise LookupError(f'OpenAlex has no record of "{source}" — pass its DOI, arXiv id or exact title')
    return openalex.record(work), openalex.related(work, relation, limit, from_year, openalex_key, sort)


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:80]


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def format_records(records: list[dict], *, first_sight=None) -> str:
    """One line per record — what the model reads — ending with the source
    string to hand fetch_paper, then the start of its abstract (enough to
    judge relevance before fetching; never evidence of what it contains).

    ``first_sight(keys)`` (the message's ``ai_tools.Tally``) says whether a
    work has not been listed in this reply yet. One that has shrinks to a
    single line pointing back, so the second and third query of a search
    cost a line per repeat instead of a whole record.
    """
    lines = []
    for rec in records:
        if first_sight is not None and not first_sight(_keys_of(rec)):
            lines.append(f'- "{_clip(rec.get("title", ""), 120)}" — listed earlier in this reply')
            continue
        authors = [a for a in rec.get("authors") or [] if a]
        who = ", ".join(authors[:3]) + (f" (+{len(authors) - 3})" if len(authors) > 3 else "")
        title = rec.get("title", "")
        url = (f"https://doi.org/{quote(rec['doi'], safe='/')}" if rec.get("doi") else
               f"https://arxiv.org/abs/{quote(rec['arxiv_id'], safe='/')}" if rec.get("arxiv_id") else "")
        # A ready-to-use title link keeps search recommendations actionable.
        label = re.sub(r"([\\\[\]])", r"\\\1", title).replace("\n", " ")
        heading = f"[{label}]({url})" if url else f'"{title}"'
        parts = [heading + (f" — {who}" if who else "")]
        when = ", ".join(p for p in (rec.get("year", ""), rec.get("venue", "")) if p)
        if when:
            parts[0] += f" ({when})"
        if isinstance(rec.get("cited_by"), int):
            parts.append(f"cited by {rec['cited_by']}")
        if rec.get("doi"):
            parts.append(f"doi:{rec['doi']}")
        if rec.get("arxiv_id"):
            parts.append(f"arXiv:{rec['arxiv_id']} (PDF: https://arxiv.org/pdf/{rec['arxiv_id']})")
        elif rec.get("oa_pdf"):
            parts.append(f"open-access PDF: {rec['oa_pdf']}")
        if rec.get("arxiv_id") and rec.get("doi"):
            parts.append(f'→ fetch_paper(source="arXiv:{rec["arxiv_id"]}") for the arXiv version, '
                         f'fetch_paper(source="doi:{rec["doi"]}") for the publisher\'s')
        elif rec.get("arxiv_id") or rec.get("doi"):
            source = f"arXiv:{rec['arxiv_id']}" if rec.get("arxiv_id") else f"doi:{rec['doi']}"
            parts.append(f'→ fetch_paper(source="{source}")')
        lines.append("- " + " · ".join(parts))
        if rec.get("abstract"):
            lines.append("  Abstract: " + _clip(rec["abstract"], _ABSTRACT_SHOWN))
    return "\n".join(lines)


# ----------------------------------------------------------------- fetch

def _read_bounded(url: str, cap: int, headers: dict, timeout: int = 30) -> tuple[str, str, bytes, dict]:
    """``(final_url, content_type, body, response headers)`` through the SSRF
    guard, refusing bodies over ``cap`` bytes (by Content-Length up front,
    else while reading)."""
    req = URLRequest(url, headers=headers)
    with guarded_urlopen(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        length = resp.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > cap:
            raise FetchError(f"document too large ({int(length) // 1_000_000} MB, cap {cap // 1_000_000} MB)")
        chunks, total = [], 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                raise FetchError(f"document too large (over {cap // 1_000_000} MB)")
            chunks.append(chunk)
        return resp.geturl(), ctype, b"".join(chunks), resp.headers


_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|div|br|li|ul|ol|h[1-6]|tr|td|th|table|section|article|header|footer|"
    r"blockquote|pre|dd|dt|dl|figure|figcaption|main|nav|aside)\b[^>]*>", re.I)
_DROP_RE = re.compile(r"<(head|script|style|noscript|svg|template)\b.*?</\1\s*>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _decode(raw: bytes, ctype: str = "") -> str:
    m = re.search(r"charset=([\w-]+)", ctype or "")
    try:
        return raw.decode(m.group(1) if m else "utf-8", "replace")
    except LookupError:  # an unknown charset name
        return raw.decode("utf-8", "replace")


def html_text(raw: bytes, ctype: str = "") -> tuple[str, str]:
    """``(title, readable text)`` of an HTML page: scripts/styles dropped,
    block tags turned into line breaks, entities unescaped, whitespace
    collapsed. Deliberately simple — a paper's abstract page, not a news
    site's layout, is what the agent reads here."""
    text = _decode(raw, ctype)
    title = _TITLE_RE.search(text)
    title = re.sub(r"\s+", " ", html.unescape(title.group(1))).strip() if title else ""
    text = _DROP_RE.sub(" ", text)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = html.unescape(_TAG_RE.sub(" ", text))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return title, text


# Walls a person passes in a browser. A bot-check page is recognized by its
# vendor's markers or its title (Cloudflare, DataDome, PerimeterX, Imperva,
# Akamai, Google's "unusual traffic"); a CAPTCHA widget or a password field
# only on a page with little else to read — an article page may carry a
# newsletter form. A sign-in page also by where the request ended up.
_CHALLENGE_RE = re.compile(
    r"_cf_chl_opt|cf-browser-verification|/cdn-cgi/challenge-platform/|"
    r"<title>\s*(?:just a moment|attention required|access denied|pardon our interruption|"
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
_THIN_PAGE = 2000  # readable chars below which a page may be all widget or form


def access_wall(url: str, headers, body: bytes, text: str | None = None) -> str:
    """"captcha" / "login" when a response is an interstitial for a person
    rather than the document, else "". ``text`` is the page's readable text
    when the caller has it already."""
    if ((headers or {}).get("cf-mitigated") or "").lower() == "challenge" or _check_host(url):
        return "captcha"
    head = body[:300_000].decode("utf-8", "replace") if body else ""
    if _CHALLENGE_RE.search(head):
        return "captcha"
    if _sign_in_url(url):
        return "login"
    if len(html_text(body)[1] if text is None else text) < _THIN_PAGE:
        if _WIDGET_RE.search(head):
            return "captcha"
        if _PASSWORD_RE.search(head):
            return "login"
    return ""


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
    for key, value in parse_qsl(urlsplit(url).query):
        if key.lower() in _RETURN_KEYS:
            back = urljoin(url, value.strip())
            if back.lower().startswith(("http://", "https://")) and not _interstitial(back):
                return back
    return ""


def _entry(*urls: str) -> str:
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


def _refusal(e: HTTPError) -> str:
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


def _remember(source: str, doc: dict, scope: tuple) -> dict:
    key = (scope, doc["url"])
    with _cache_lock:
        _cache.pop(key, None)
        _cache[key] = doc
        _aliases[scope, source] = key
        total = sum(d["chars"] for d in _cache.values())
        while _cache and (len(_cache) > _CACHE_MAX_DOCS or total > _CACHE_MAX_CHARS):
            old_key = next(iter(_cache))  # insertion order = least recently used
            old = _cache.pop(old_key)
            total -= old["chars"]
            for alias in [k for k, v in _aliases.items() if v == old_key]:
                del _aliases[alias]
    return doc


def cached(source: str, scope: tuple) -> dict | None:
    with _cache_lock:
        key = _aliases.get((scope, source), (scope, source))
        doc = _cache.get(key)
        if doc:  # LRU touch
            _cache.pop(key)
            _cache[key] = doc
        return doc


def _source_url(source: str) -> str:
    """The URL a non-PDF fallback fetches for a source string."""
    kind, ident = identifier(source)
    if kind == "doi":
        return f"https://doi.org/{ident}"
    if kind == "arxiv":
        return f"https://arxiv.org/abs/{ident}"
    return source if source.lower().startswith(("http://", "https://")) else ""


def pdf_document(url: str, data: bytes, note: str = "", version: str = "") -> dict:
    """A PDF as the cache holds it: its text page by page. Raises FetchError
    when there is no text to read."""
    try:
        pages = extract_pages(data)
    except Exception as e:
        log.warning(f"[ai_web] extraction failed for {url}: {e}")
        raise FetchError(f"the PDF at {url} could not be read ({e})")
    if not any(p.strip() for p in pages):
        raise FetchError(f"the PDF at {url} has no text layer (a scan?)")
    return {"url": url, "kind": "pdf", "title": "", "pages": pages,
            "chars": sum(len(p) for p in pages), "note": note, "version": version}


def fetch_document(source: str, published_only: bool = False) -> dict:
    """The document behind ``source`` (a DOI, arXiv id or URL) as
    ``{"url", "kind": "pdf"|"html", "title", "pages": [text per page],
    "chars", "note", "version"}`` — from the cache when it was fetched
    before. ``version`` is the resolver's (``resolve_source``): "publisher",
    "preprint", "published", "accepted", "submitted" or "" (unknown);
    ``published_only`` refuses to substitute an unpublished open-access
    copy. A web page also carries ``links`` (its PDF-looking links with
    their text, paper_links.pdf_links) and, when it is an article page whose
    PDF was out of reach, ``wall`` with ``open_url`` / ``pdf_url``. Raises
    FetchError with a model-readable reason."""
    source = (source or "").strip()
    if not source:
        raise FetchError("empty source — pass a DOI, an arXiv id or an http(s) URL")
    scope = publisher_sessions.cache_scope()
    # A published-only read never gets the copy an any-version read cached.
    alias = f"{source} [published]" if published_only else source
    doc = cached(alias, scope)
    if doc:
        return doc
    if not _source_url(source):
        raise FetchError("source must be a DOI (10.…), an arXiv id (2301.12345) or an http(s) URL")
    # One cookie jar for the resolver's walk, the download and the fallback.
    with browsing_session():
        return _remember(alias, _fetch(source, published_only), scope)


def identity(doc: dict, title: str) -> bool | None:
    """Whether the fetched document is the paper titled ``title``: its
    normalized title appears on the first pages (a PDF) or in the page
    text. None when there is no title to check."""
    want = re.sub(r"[^a-z0-9]+", "", (title or "").lower())
    if len(want) < 12:
        return None
    head = " ".join(doc.get("pages") or [])[:30_000] if doc.get("kind") == "html" else \
        " ".join((doc.get("pages") or [])[:3])
    return want in re.sub(r"[^a-z0-9]+", "", head.lower())


def _fetch(source: str, published_only: bool = False) -> dict:
    from .routers.pdf import BROWSER_HEADERS, meta_refresh, resolve_source

    reason = wall = source_note = referer = version = ""
    trace: dict = {}
    try:
        resolved = resolve_source(source, trace=trace, published_only=published_only)
        pdf_url = resolved["source_url"]
        source_note = resolved.get("note", "")
        referer = resolved.get("referer", "")
        version = resolved.get("version", "")
    except HTTPException as e:
        reason, pdf_url = str(e.detail), ""
        wall = "denied" if trace.get("blocked") else ""
    page_url = trace.get("page_url") or _source_url(source)
    want_pdf = pdf_url or next(iter(trace.get("pdf_urls") or []), "")
    if pdf_url:
        try:
            final_url, ctype, data, headers = _read_bounded(
                pdf_url, FETCH_MAX_BYTES, {**BROWSER_HEADERS, **({"Referer": referer} if referer else {})})
        except FetchError:
            raise
        except HTTPError as e:
            wall = _refusal(e)
            reason = f"the PDF at {pdf_url} answered HTTP {e.code}"
            if not wall:
                raise FetchError(reason)
        except (URLError, OSError, ValueError) as e:
            raise FetchError(f"could not fetch the PDF at {pdf_url}: {e}")
        else:
            if "application/pdf" in ctype or data[:5] == b"%PDF-":
                return pdf_document(pdf_url, data, source_note, version)
            wall = access_wall(final_url, headers, data)
            reason = (f"{pdf_url} is not a PDF ({ctype or 'no content type'})"
                      + (f" — it is {WALLS[wall]}" if wall else ""))
    # No PDF: the source's own page, if it is one, as readable text.
    fallback = _source_url(source)
    if wall and fallback == pdf_url:
        raise FetchError(reason, wall=wall, open_url=_entry(page_url, pdf_url), pdf_url=want_pdf)
    url = fallback
    for hop in range(3):  # a page that only redirects (a meta refresh) is followed
        try:
            final_url, ctype, data, headers = _read_bounded(url, HTML_MAX_BYTES, {
                **BROWSER_HEADERS, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"})
        except FetchError:
            raise
        except HTTPError as e:
            raise FetchError(f"no PDF ({reason}) and the page {url} answered HTTP {e.code}"
                             + (" — the site blocks server-side fetching" if e.code in (401, 403) else ""),
                             wall=_refusal(e) or wall, open_url=_entry(e.geturl(), page_url, fallback),
                             pdf_url=want_pdf)
        except (URLError, OSError, ValueError) as e:
            raise FetchError(f"no PDF ({reason}) and {url} could not be fetched: {e}",
                             wall=wall, open_url=_entry(page_url, fallback), pdf_url=want_pdf)
        url = meta_refresh(_decode(data, ctype), final_url) if hop < 2 and "html" in ctype else ""
        if not url:
            break
    if "html" not in ctype and "xml" not in ctype:
        raise FetchError(f"no PDF ({reason}) and {fallback} is not a web page ({ctype or 'no content type'})",
                         wall=wall, open_url=_entry(page_url, fallback), pdf_url=want_pdf)
    title, text = html_text(data, ctype)
    page_wall = access_wall(final_url, headers, data, text)
    if page_wall:
        raise FetchError(f"no PDF ({reason}) and the page {final_url} is {WALLS[page_wall]}",
                         wall=page_wall, open_url=_entry(final_url, page_url, fallback), pdf_url=want_pdf)
    if not text:
        # A page with nothing to read without its scripts: a browser shows it.
        raise FetchError(f"no PDF ({reason}) and the page {final_url} has no readable text",
                         wall=wall or "script", open_url=_entry(final_url, page_url, fallback),
                         pdf_url=want_pdf)
    # PDF links the resolver has not tried already (those met the wall).
    tried = {pdf_url, *(trace.get("pdf_urls") or [])}
    links = [link for link in paper_links.pdf_links(_decode(data, ctype), final_url) if link["url"] not in tried]
    doc = {"url": final_url, "kind": "html", "title": title, "pages": [text],
           "chars": len(text), "note": reason, "links": links, "version": ""}
    # An article page whose PDF was out of reach (it advertised one, or the
    # DOI's publisher refused): only a person with access gets the rest.
    if wall or trace.get("pdf_urls") or trace.get("doi"):
        doc.update(wall=wall or "abstract", open_url=final_url, pdf_url=want_pdf)
    return doc


def window(doc: dict, limit: int, offset: int = 0, start_page: int = 1) -> tuple[str, int | None, int]:
    """``(text, next_offset, total)``: ``limit`` chars of the document from
    ``offset`` chars after the start of PDF page ``start_page`` (1-based; every
    page's text is prefixed ``[p. N]`` so the model can cite pages), the offset
    a follow-up read continues from (None = the document ended inside this
    window), and the total chars from start_page on."""
    pages = doc["pages"]
    start = max(1, min(start_page, len(pages)))
    if doc["kind"] == "pdf":
        full = "\n\n".join(f"[p. {n}]\n{t}" for n, t in enumerate(pages[start - 1:], start) if t.strip())
    else:
        full = "\n\n".join(pages)
    text = full[offset:offset + limit]
    next_offset = offset + limit if len(full) > offset + limit else None
    return text, next_offset, len(full)


def clear_cache():
    with _cache_lock:
        _cache.clear()
        _aliases.clear()
