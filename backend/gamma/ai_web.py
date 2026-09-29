"""The agent's web reach: scholarly search and reading a document that is
not in the library (``search_papers`` / ``fetch_paper`` in ``ai_tools.py``).

Nothing here writes to a workspace. ``search_papers`` asks the keyless
registries the metadata lookup already uses (Crossref, arXiv — a bare DOI /
arXiv id is resolved directly). ``fetch_document`` turns a DOI, arXiv id or
URL into text: the PDF behind it through the resolver the extension and
"open a link" use (``routers.pdf.resolve_source`` — arXiv abs pages,
publisher ``citation_pdf_url`` tags, Unpaywall open-access fallback, the SSRF
guard), extracted page by page; when no PDF is reachable and the source is a
web page, its readable text instead. Fetched documents live in a small
in-memory cache so the model can read a long paper in successive windows
without re-downloading it — nothing is stored on disk and a restart forgets
everything.

A fetch that meets a wall only a person gets past — a CAPTCHA or bot check,
a sign-in page, a refusal, a paywall that serves only the article page — says
which (``FetchError.wall`` / a document's ``wall``) and where the person
would go, so the chat can hand the fetch to the user's browser
(``gamma/fetch_handoff.py``).
"""

import html
import re
import threading
import time
from urllib.parse import parse_qsl, quote, urljoin, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest

from fastapi import HTTPException

from . import publisher_sessions
from .logbuf import log
from .net_guard import browsing_session, guarded_urlopen
from .pdf_text import extract_pages
from .paper_links import pdf_link_candidates, title_key

SEARCH_LIMIT_DEFAULT = 8
SEARCH_LIMIT_MAX = 20
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


def search_papers(query: str, limit: int = SEARCH_LIMIT_DEFAULT) -> list[dict]:
    """Records for a free-text query (Crossref and arXiv, interleaved in
    their own relevance order, duplicates by DOI / arXiv id / title
    dropped), or the one record of an identifier query."""
    # Local import: keep gamma.* module load free of the routers package.
    from .routers import metadata as registry

    limit = max(1, min(int(limit or SEARCH_LIMIT_DEFAULT), SEARCH_LIMIT_MAX))
    kind, ident = identifier(query)
    if kind == "arxiv":
        rec = registry._fetch_arxiv(ident)
        return [rec] if rec else []
    if kind == "doi":
        rec, _ = registry._fetch_doi(ident, with_bibtex=False)
        return [rec] if rec else []
    crossref = registry._crossref_search(query, rows=limit)
    arxiv = registry._arxiv_search(query, rows=limit)
    out: list[dict] = []
    kept: dict = {}  # DOI / arXiv id / title key → the record kept for it

    def keys_of(rec):
        return {k for k in (
            f"doi:{rec.get('doi', '').lower()}" if rec.get("doi") else "",
            f"arxiv:{rec.get('arxiv_id', '').lower()}" if rec.get("arxiv_id") else "",
            "title:" + title_key(rec.get("title", "")),
        ) if k and k != "title:"}

    for a, b in zip(crossref + [None] * len(arxiv), arxiv + [None] * len(crossref)):
        for rec in (a, b):
            if not rec:
                continue
            keys = keys_of(rec)
            # Shared identifiers are authoritative. Equal titles alone must
            # not collapse distinct papers with conflicting identifiers.
            twin = next((kept[k] for k in sorted(keys) if k in kept and
                         (not k.startswith("title:") or all(
                             not rec.get(f) or not kept[k].get(f)
                             or rec[f].lower() == kept[k][f].lower()
                             for f in ("doi", "arxiv_id")))), None)
            if twin is None:
                twin = dict(rec)
                out.append(twin)
            else:
                # The same work from the other registry: the kept record
                # takes the identifiers it lacks, so a journal record keeps
                # its arXiv preprint (and an arXiv record its DOI).
                for field in ("doi", "arxiv_id", "abstract"):
                    if rec.get(field) and not twin.get(field):
                        twin[field] = rec[field]
            for k in keys | keys_of(twin):
                kept.setdefault(k, twin)
    # A query that is a record's exact title (a cited reference) ranks it first.
    want = title_key(query)
    out.sort(key=lambda rec: title_key(rec.get("title", "")) != want)
    return out[:limit]


def format_records(records: list[dict]) -> str:
    """One line per record — what the model reads — ending with the source
    string to hand fetch_paper."""
    lines = []
    for rec in records:
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
        if rec.get("doi"):
            parts.append(f"doi:{rec['doi']}")
        if rec.get("arxiv_id"):
            parts.append(f"arXiv:{rec['arxiv_id']} (PDF: https://arxiv.org/pdf/{rec['arxiv_id']})")
        if rec.get("arxiv_id") and rec.get("doi"):
            parts.append(f'→ fetch_paper(source="arXiv:{rec["arxiv_id"]}") for the arXiv version, '
                         f'fetch_paper(source="doi:{rec["doi"]}") for the publisher\'s')
        elif rec.get("arxiv_id") or rec.get("doi"):
            source = f"arXiv:{rec['arxiv_id']}" if rec.get("arxiv_id") else f"doi:{rec['doi']}"
            parts.append(f'→ fetch_paper(source="{source}")')
        lines.append("- " + " · ".join(parts))
        if rec.get("abstract"):
            abstract = re.sub(r"\s+", " ", rec["abstract"]).strip()
            lines.append("  Abstract: " + abstract[:1200] + ("…" if len(abstract) > 1200 else ""))
    return "\n".join(lines)


# ----------------------------------------------------------------- fetch

def _read_bounded(url: str, cap: int, headers: dict, timeout: int = 30) -> tuple[str, str, bytes, dict]:
    """``(final_url, content_type, body, response headers)`` through the SSRF
    guard, refusing bodies over ``cap`` bytes (by Content-Length up front,
    else while reading)."""
    req = URLRequest(url, headers=headers)
    deadline = time.monotonic() + timeout
    with guarded_urlopen(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        length = resp.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > cap:
            raise FetchError(f"document too large ({int(length) // 1_000_000} MB, cap {cap // 1_000_000} MB)")
        chunks, total = [], 0
        while True:
            if time.monotonic() >= deadline:
                raise FetchError("document download exceeded its time budget")
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


def pdf_document(url: str, data: bytes, note: str = "") -> dict:
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
            "chars": sum(len(p) for p in pages), "note": note}


def fetch_document(source: str, *, version_policy: str = "allow_preprint",
                   expected_title: str = "", expected_doi: str = "") -> dict:
    """The document behind ``source`` (a DOI, arXiv id or URL) as
    ``{"url", "kind": "pdf"|"html", "title", "pages": [text per page],
    "chars", "note"}`` — from the cache when it was fetched before. A web
    page also carries ``links`` (the PDF-looking links on it) and, when it is
    an article page whose PDF was out of reach, ``wall`` with ``open_url`` /
    ``pdf_url``. Raises FetchError with a model-readable reason."""
    source = (source or "").strip()
    if not source:
        raise FetchError("empty source — pass a DOI, an arXiv id or an http(s) URL")
    validate_fetch_options(version_policy, expected_title, expected_doi)
    # Never reuse a permissive or differently identified document for a
    # stricter request; HTML candidates also depend on the requested title.
    scope = (*publisher_sessions.cache_scope(), version_policy, expected_title, expected_doi)
    doc = cached(source, scope)
    if doc:
        return doc
    if not _source_url(source):
        raise FetchError("source must be a DOI (10.…), an arXiv id (2301.12345) or an http(s) URL")
    # One cookie jar for the resolver's walk, the download and the fallback.
    with browsing_session():
        doc = _fetch(source, version_policy=version_policy, expected_title=expected_title,
                     expected_doi=expected_doi)
        return _remember(source, doc, scope)


def _fetch(source: str, *, version_policy: str = "allow_preprint",
           expected_title: str = "", expected_doi: str = "") -> dict:
    from .routers.pdf import BROWSER_HEADERS, meta_refresh, resolve_source

    reason = wall = ""
    trace: dict = {}
    try:
        resolved = resolve_source(source, trace=trace, version_policy=version_policy)
        pdf_url = resolved["source_url"]
    except HTTPException as e:
        reason, pdf_url = str(e.detail), ""
        wall = "denied" if trace.get("blocked") else ""
    page_url = trace.get("page_url") or _source_url(source)
    want_pdf = pdf_url or next(iter(trace.get("pdf_urls") or []), "")
    if pdf_url:
        # A successful header probe is not a successful paper fetch. Try
        # remaining locations when the real download, extraction or identity
        # check fails. The resolver already applies the version policy.
        deadline = time.monotonic() + 45
        seen, last_error = set(), None
        for candidate in [resolved, *resolved.get("alternatives", [])][:4]:
            candidate_url = candidate["source_url"]
            if candidate_url in seen:
                continue
            seen.add(candidate_url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            referer = candidate.get("referer", "")
            try:
                final_url, ctype, data, headers = _read_bounded(
                    candidate_url, FETCH_MAX_BYTES,
                    {**BROWSER_HEADERS, **({"Referer": referer} if referer else {})},
                    timeout=min(30, remaining))
                if "application/pdf" not in ctype and data[:5] != b"%PDF-":
                    candidate_wall = access_wall(final_url, headers, data)
                    wall = wall or candidate_wall
                    raise FetchError(f"{candidate_url} is not a PDF ({ctype or 'no content type'})")
                doc = pdf_document(candidate_url, data, candidate.get("note", ""))
                provenance = dict(candidate.get("provenance") or {})
                probed_url = provenance.get("final_url")
                if (probed_url and urlsplit(probed_url).hostname != urlsplit(final_url).hostname):
                    provenance["version_verified"] = False
                    provenance["version_evidence"] = "download_redirect_changed"
                provenance.update(requested_source=source, final_url=final_url)
                doc["provenance"] = provenance
                return verify_document(doc, version_policy=version_policy,
                                       expected_title=expected_title, expected_doi=expected_doi)
            except HTTPError as e:
                wall = wall or _refusal(e)
                reason = f"the PDF at {candidate_url} answered HTTP {e.code}"
                last_error = FetchError(reason)
            except (URLError, OSError, ValueError) as e:
                last_error = FetchError(f"could not fetch the PDF at {candidate_url}: {e}")
                reason = str(last_error)
            except FetchError as e:
                last_error, reason = e, str(e)
        if last_error and not wall:
            raise last_error
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
    candidates = [c for c in pdf_link_candidates(_decode(data, ctype), final_url,
                                                expected_title or expected_doi, limit=20)
                  if c["url"] not in tried][:8]
    links = [c["url"] for c in candidates]
    doc = {"url": final_url, "kind": "html", "title": title, "pages": [text],
           "chars": len(text), "note": reason, "links": links, "link_candidates": candidates,
           "provenance": {"requested_source": source, "final_url": final_url,
                          "version": "", "identity_verified": False}}
    # An article page whose PDF was out of reach (it advertised one, or the
    # DOI's publisher refused): only a person with access gets the rest.
    if wall or trace.get("pdf_urls") or trace.get("doi"):
        doc.update(wall=wall or "abstract", open_url=final_url, pdf_url=want_pdf)
    return verify_document(doc, version_policy=version_policy,
                           expected_title=expected_title, expected_doi=expected_doi)


def validate_fetch_options(version_policy: str, expected_title: str = "", expected_doi: str = ""):
    if version_policy not in ("allow_preprint", "published_only"):
        raise FetchError("version_policy must be allow_preprint or published_only")
    if len(expected_title) > 1000 or len(expected_doi) > 300:
        raise FetchError("expected paper title or DOI is too long")
    if expected_title and not title_key(expected_title):
        raise FetchError("expected_title must contain a paper title")
    if expected_doi and identifier(expected_doi)[0] != "doi":
        raise FetchError("expected_doi must be a DOI (10.… or doi:10.…)")


def _opening_title_matches(page: str, expected_title: str) -> bool:
    """Match complete title lines, before affiliation/abstract/body markers.

    A title quoted in an introduction/reference or the prefix of a different
    title is not evidence that this is the requested document.
    """
    lines = []
    for line in page[:3500].splitlines()[:35]:
        line = line.strip()
        if re.match(r"^(?:abstract\b|(?:\d+\.?\s*)?introduction\b|references\b|"
                    r"keywords\b|\(?received\b|department\b|university\b|institute\b)", line, re.I):
            break
        if line:
            lines.append(title_key(line))
    want = title_key(expected_title)
    return any("".join(lines[i:j]) == want for i in range(len(lines))
               for j in range(i + 1, min(i + 8, len(lines)) + 1))


def verify_document(doc: dict, *, version_policy: str = "allow_preprint",
                    expected_title: str = "", expected_doi: str = "") -> dict:
    """Attach bounded identity evidence; never treat a search hit as proof.

    HTML publication lists are discovery results, not the requested paper.
    Require a supplied title in the PDF's opening text; an isolated DOI may
    be a reference, so DOI-only evidence is reported without confirming identity.
    This also checks browser-delivered PDFs and therefore cannot be bypassed
    by the handoff cache.
    """
    validate_fetch_options(version_policy, expected_title, expected_doi)
    provenance = dict(doc.get("provenance") or {})
    provenance.update(expected_title=expected_title, expected_doi=expected_doi,
                      version_policy=version_policy, identity_verified=False)
    if doc["kind"] == "pdf":
        head = doc["pages"][0][:20000] if doc["pages"] else ""
        title_matches = bool(expected_title and _opening_title_matches(head, expected_title))
        if expected_title and not title_matches:
            raise FetchError("PDF identity mismatch: the requested title was not found in its opening title lines. "
                             "Do not use this document as the requested paper; inspect another candidate.")
        expected = identifier(expected_doi)[1].lower() if expected_doi else ""
        actual = str(provenance.get("doi") or "").lower()
        actual = identifier(actual)[1].lower() or actual
        if expected and actual and actual != expected:
            raise FetchError("PDF identity mismatch: the resolved DOI differs from expected_doi")
        provenance["identity_verified"] = title_matches
        provenance["identity_evidence"] = "title_in_opening_lines" if title_matches else "unverified"
        if expected and expected in head.lower():
            provenance["doi_in_text"] = True
        actual_host = urlsplit(provenance.get("final_url") or doc["url"]).hostname
        if actual_host in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"):
            provenance.update(version="submittedVersion", version_verified=True, version_evidence="arxiv_source")
        if version_policy == "published_only" and (
                provenance.get("version") != "publishedVersion" or not provenance.get("version_verified")):
            raise FetchError("The fetched PDF is not verified as the published version. "
                             "Use the journal DOI to locate a verified published copy; "
                             "do not substitute this file under published_only.")
    return {**doc, "provenance": provenance}


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
