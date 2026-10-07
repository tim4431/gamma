"""External PDF resolution and proxying (with optional local caching).

Resolution handles the common academic-link shapes: bare arXiv ids and DOIs
pasted without a URL are promoted to one first, arXiv abstract and HTML URLs
are rewritten to their PDF, DOI links that land on paywalled/bot-blocking publisher
pages fall back to an open-access copy via the Unpaywall API, and failures come
back as human-readable messages. Institutional access depends on the backend's
network; publisher bot challenges may still require a browser.
"""

import hashlib
import html as html_lib
import json
import re
import urllib.parse
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel

from ..auth import require_user, resolve_ws, share_scope
from ..db import connect_pages_db
from .. import pdf_meta, storage
from ..logbuf import log
from ..net_guard import guarded_urlopen
from ..server_settings import can_store
from ..storage import DIGEST_CHARS, is_pdf

router = APIRouter(prefix="/api", tags=["pdf"])

# Identifier sent to Unpaywall's polite pool (its API requires an email
# parameter). A fixed project address — deliberately not configurable, so the
# open-access fallback needs zero setup.
CONTACT_EMAIL = "gamma-pdf-annotator@users.noreply.github.com"

# Realistic browser headers get past simple UA filters (many hosts 403 bare bots)
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# A new-style (2301.12345) or old-style (cond-mat/0402216, math.GT/0309136)
# arXiv id without its version, non-capturing — the one id shape clip.py,
# metadata.py and zotero_import.py build their patterns from. arxiv.org
# serves a paper at /abs/, /pdf/ and /html/<id>.
ARXIV_ID = r"(?:[0-9]{4}\.[0-9]{4,5}|[a-z][a-z.-]*/[0-9]{7})"
_ARXIV_PAGE_RE = re.compile(r"arxiv\.org/(?:abs|html)/(" + ARXIV_ID + r"(?:v\d+)?)", re.I)
_ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})", re.I)
_DOI_URL_RE = re.compile(r"(?:dx\.)?doi\.org/(10\.\d{4,9}/[^\s?#]+)", re.I)

# Bare identifiers pasted straight from a paper: "2301.12345" / "arXiv:2301.12345v2"
# (plus old-style "hep-th/9901001") and "10.1103/PhysRevLett…" / "doi:10.…".
_BARE_ARXIV_RE = re.compile(r"^(?:arxiv:\s*)?(" + ARXIV_ID + r"(?:v\d+)?)$", re.I)
_BARE_DOI_RE = re.compile(r"^(?:doi:\s*)?(10\.\d{4,9}/\S+)$", re.I)


def _identifier_to_url(text: str) -> str:
    """Turn a pasted bare identifier into a fetchable URL: an arXiv id goes
    straight to its PDF, a DOI through doi.org (whose page then feeds the
    citation_pdf_url / Unpaywall fallbacks). URL-shaped input is untouched."""
    if "://" in text:
        return text
    m = _BARE_ARXIV_RE.match(text)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}"
    m = _BARE_DOI_RE.match(text)
    if m:
        return f"https://doi.org/{m.group(1)}"
    return text


_REFRESH_TAG_RE = re.compile(r"<meta\b[^>]*http-equiv\s*=\s*[\"']?refresh[^>]*>", re.I)
_REFRESH_CONTENT_RE = re.compile(r"""\bcontent\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_REFRESH_URL_RE = re.compile(r"""^\s*[\d.]*\s*[;,]\s*url\s*=\s*['"]?([^'"]+)""", re.I)
_ARXIV_PDF_URL_RE = re.compile(r"^https?://arxiv\.org/pdf/", re.I)


def meta_refresh(html: str, base: str) -> str:
    """Where a page's ``<meta http-equiv="refresh">`` sends the visitor,
    absolute, or "". Some DOI landings are only that (Elsevier's
    linkinghub → ScienceDirect), so a fetch follows it like a 3xx."""
    tag = _REFRESH_TAG_RE.search(html[:50_000])
    content = _REFRESH_CONTENT_RE.search(tag.group(0)) if tag else None
    m = _REFRESH_URL_RE.match((content.group(1) or content.group(2)) if content else "")
    if not m:
        return ""
    url = urllib.parse.urljoin(base, html_lib.unescape(m.group(1).strip()))
    return url if url.lower().startswith(("http://", "https://")) and url != base else ""


def _meta_content(html: str, name: str) -> str:
    """Value of a <meta name=... content=...> tag (either attribute order)."""
    m = re.search(rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
    if not m:
        m = re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*name=["\']{re.escape(name)}["\']', html, re.I)
    return m.group(1).strip() if m else ""


def _publisher_pdf_candidates(page_url: str, html: str) -> list[str]:
    """Prefer APS's PDF route over its legacy link.aps.org metadata URL,
    which can redirect back to the abstract even with institutional access.
    Other publishers use their advertised PDF link.
    """
    candidates = []
    page = urllib.parse.urlsplit(page_url)
    if page.hostname == "journals.aps.org":
        match = re.fullmatch(r"/([a-z0-9]+)/(?:abstract|article)/(10\.1103/[^?#]+)", page.path, re.I)
        if match:
            candidates.append(f"https://journals.aps.org/{match[1]}/pdf/{match[2]}")
    advertised = _meta_content(html, "citation_pdf_url")
    if advertised:
        advertised = urllib.parse.urljoin(page_url, advertised)
        if advertised not in candidates:
            candidates.append(advertised)
    return candidates


# Unpaywall's versions, best first, and how a resolved source names them.
_OA_ORDER = {"publishedVersion": 0, "acceptedVersion": 1, "submittedVersion": 2}
VERSION_LABELS = {"publishedVersion": "published", "acceptedVersion": "accepted", "submittedVersion": "submitted"}
OA_TRIES = 4  # open-access locations tried before giving up on a DOI


def _open_access_pdfs(doi: str) -> list[dict]:
    """The legal open-access PDFs of a DOI via Unpaywall, best first, as
    ``[{url, version, host}]``: the published PDF before accepted
    manuscripts before preprints (repositories often hold only the
    submitted version), the publisher's copy before a repository's. A copy
    that fails is not the end — the resolver tries the next."""
    try:
        url = (f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}"
               f"?email={urllib.parse.quote(CONTACT_EMAIL)}")
        req = URLRequest(url, headers={"User-Agent": "gamma-pdf-annotator/1.0"})
        with guarded_urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log.warning(f"[resolve-pdf] unpaywall lookup failed: {e}")
        return []
    locs = [loc for loc in data.get("oa_locations") or [] if isinstance(loc, dict) and loc.get("url_for_pdf")]
    locs.sort(key=lambda loc: (_OA_ORDER.get(loc.get("version"), 3), loc.get("host_type") != "publisher"))
    out, seen = [], set()
    for loc in locs:
        if loc["url_for_pdf"] not in seen:
            seen.add(loc["url_for_pdf"])
            out.append({"url": loc["url_for_pdf"], "version": loc.get("version") or "",
                        "host": loc.get("repository_institution") or loc.get("host_type") or ""})
    return out


def _oa_source(url: str, copy: dict) -> dict:
    """The resolver's answer for an open-access copy: its version, and a
    note when it is not the published one."""
    version = copy.get("version") or ""
    note = ""
    if version and version != "publishedVersion":
        pretty = {"acceptedVersion": "accepted manuscript",
                  "submittedVersion": "preprint (submitted version)"}.get(version, version)
        note = (f"The publisher's PDF couldn't be fetched — loaded the open-access {pretty} instead. "
                "For the published version, download it in your browser and replace the "
                "source file via the page's source button.")
    return {"source_url": url, "note": note, "version": VERSION_LABELS.get(version, "")}


class ResolvePdfRequest(BaseModel):
    source_url: str
    allow_oa: bool = True  # substitute an open-access copy when the publisher PDF is unavailable


# Plain `def` on purpose: FastAPI runs sync endpoints in its threadpool, so the
# (potentially slow) upstream fetches here don't block the event loop.
@router.post("/resolve-pdf")
def resolve_pdf(payload: ResolvePdfRequest, request: Request):
    require_user(request)
    return resolve_source(payload.source_url, payload.allow_oa)


def resolve_source(source_url: str, allow_oa: bool = True, trace: dict | None = None,
                   published_only: bool = False) -> dict:
    """URL / bare identifier → ``{"source_url": <fetchable PDF url>, "note"?,
    "referer"?, "version"?}``, or an HTTPException(400) with a human-readable
    reason. Shared by the resolve endpoint, the extension's /api/clip and the
    AI's fetch_paper. ``referer`` is the article page a PDF link came from
    (the download should send it, as the page's own link would).
    ``version`` says what the PDF is when the walk knows: "publisher" (the
    article page's own PDF), "preprint" (arXiv), or an open-access copy's
    "published" / "accepted" / "submitted". ``published_only`` substitutes
    only published open-access copies. A ``trace`` dict collects what the
    walk saw — ``page_url`` (the landing page after redirects), ``pdf_urls``
    (the PDF links it advertised), ``doi``, ``blocked`` (the first request
    was refused) — so a caller can say where a person could get the PDF
    instead."""
    url = _identifier_to_url((source_url or "").strip())
    trace = {} if trace is None else trace

    # arXiv abstract and HTML pages (and arXiv DOIs) go straight to the PDF
    m = _ARXIV_PAGE_RE.search(url) or _ARXIV_DOI_RE.search(url)
    if m:
        url = f"https://arxiv.org/pdf/{m.group(1)}"
    trace.update(page_url=url, pdf_urls=[], doi="", blocked=False)

    def try_resolve(u: str, referer: str = ""):
        """(final_url, content_type, body). Body is only read for non-PDF
        responses (capped) so HTML pages can be inspected for PDF pointers."""
        req = URLRequest(u, headers={**BROWSER_HEADERS, **({"Referer": referer} if referer else {})})
        with guarded_urlopen(req, timeout=20) as resp:
            ctype = resp.headers.get("Content-Type", "").lower()
            body = b"" if "application/pdf" in ctype else resp.read(600_000)
            return resp.geturl(), ctype, body

    def direct(pdf: str) -> dict:
        """A PDF the link itself served: arXiv's is a preprint, anyone
        else's is of unknown version."""
        host = urllib.parse.urlsplit(pdf).hostname or ""
        return {"source_url": pdf, "version": "preprint" if host.endswith("arxiv.org") else ""}

    blocked = False
    content_type = ""
    final_url = url
    body = b""
    try:
        final_url, content_type, body = try_resolve(url)
        if "application/pdf" in content_type:
            return direct(final_url)
        trace["page_url"] = final_url
    except HTTPError as e:
        # arxiv.org refuses some PDFs to programs (406) that its export
        # host, the one arXiv asks automated clients to use, serves.
        mirror = _ARXIV_PDF_URL_RE.sub("https://export.arxiv.org/pdf/", url)
        if mirror != url:
            try:
                _, mirror_type, _ = try_resolve(mirror)
                if "application/pdf" in mirror_type:
                    return direct(mirror)
            except Exception as mirror_error:
                log.warning(f"[resolve-pdf] arXiv export host failed too: {mirror_error}")
        if e.code not in (401, 403, 418, 429):
            raise HTTPException(status_code=400, detail=f"upstream HTTP error: {e.code}")
        blocked = trace["blocked"] = True
        trace["page_url"] = e.geturl() or url  # the refusing host, past doi.org's redirect
    except URLError as e:
        raise HTTPException(status_code=400, detail=f"upstream URL error: {e.reason}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"resolve failed: {str(e)}")

    # Landed on an HTML article page (nature.com/articles/…, journal abstract
    # pages, …). Publishers advertise the "Download PDF" target in the
    # citation_pdf_url meta tag — the same tag Google Scholar reads.
    html = body.decode("utf-8", "replace") if body else ""
    # A page that only redirects (a meta refresh) is followed like a 3xx.
    for _ in range(2):
        hop = meta_refresh(html, final_url) if html and not _publisher_pdf_candidates(final_url, html) else ""
        if not hop:
            break
        try:
            final_url, content_type, body = try_resolve(hop)
        except HTTPError as e:
            if e.code in (401, 403, 418, 429):
                blocked = trace["blocked"] = True
                trace["page_url"] = e.geturl() or hop
            html = ""
            break
        except Exception as e:
            log.warning(f"[resolve-pdf] following {hop} failed: {e}")
            break
        if "application/pdf" in content_type:
            return direct(final_url)
        trace["page_url"] = final_url
        html = body.decode("utf-8", "replace") if body else ""
    trace["pdf_urls"] = _publisher_pdf_candidates(final_url, html)
    for pdf_url in trace["pdf_urls"]:
        try:
            _, ct2, _ = try_resolve(pdf_url, referer=final_url)
            if "application/pdf" in ct2:
                # Return the canonical URL, not the redirect target — hosts
                # like nature.com append one-time tokens on redirect, and the
                # doc id is a hash of this URL, so it must stay stable.
                return {"source_url": pdf_url, "referer": final_url, "version": "publisher"}
        except Exception as e:
            log.warning(f"[resolve-pdf] publisher PDF fetch failed: {e}")

    # For DOI links (or pages that state their DOI), the publisher PDF is
    # usually paywalled or bot-blocked — look for a legal open-access copy.
    doi_m = _DOI_URL_RE.search(url)
    doi = urllib.parse.unquote(doi_m.group(1)).rstrip(".,;") if doi_m else ""
    if not doi and html:
        doi = (_meta_content(html, "citation_doi") or _meta_content(html, "dc.identifier")).strip()
        doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
        if not re.match(r"^10\.\d{4,9}/", doi):
            doi = ""
    trace["doi"] = doi
    if doi:
        if not allow_oa:
            raise HTTPException(
                status_code=400,
                detail="The publisher's PDF isn't accessible server-side (access restriction or browser check). "
                       "Open-access fallback is disabled in your settings — download the PDF in "
                       "your browser and drop it onto Gamma.",
            )
        copies = _open_access_pdfs(doi)
        usable = [c for c in copies if not published_only or c["version"] == "publishedVersion"]
        for copy in usable[:OA_TRIES]:
            try:
                oa_final, oa_type, _ = try_resolve(copy["url"])
            except Exception as e:
                log.warning(f"[resolve-pdf] open-access copy {copy['url']} failed: {e}")
                continue
            if "application/pdf" in oa_type:
                return _oa_source(oa_final, copy)
        if usable:
            return _oa_source(usable[0]["url"], usable[0])  # let the proxy give it a try
        if copies:
            raise HTTPException(
                status_code=400,
                detail="The publisher's PDF isn't accessible server-side, and the open-access copies are "
                       "not the published version (" + ", ".join(sorted({
                           VERSION_LABELS.get(c["version"], "unknown") for c in copies})) + "). "
                       "Open the link in your browser to get the published PDF, or allow another version.",
            )
        raise HTTPException(
            status_code=400,
            detail="This leads to a publisher page whose PDF isn't accessible server-side "
                   "(access restriction or browser check), and no open-access copy was found. Open the link in your "
                   "browser instead — if you can download the PDF there, drop the file onto Gamma.",
        )
    if blocked:
        raise HTTPException(
            status_code=400,
            detail="This site blocks server-side fetching. Download the PDF in your browser "
                   "and drop it onto the page.",
        )
    raise HTTPException(
        status_code=400,
        detail=f"Couldn't find a PDF behind this link (got {content_type or 'no content type'}, "
               "and the page doesn't advertise a PDF). Download it in your browser and drop it onto Gamma.")


def download_pdf(source_url: str, want_bytes: bool = True, referer: str = "") -> tuple[str, bytes]:
    """Fetch `source_url` the way the proxy does (browser headers, SSRF guard)
    and require a PDF content type. Returns ``(final_url, data)``; with
    ``want_bytes=False`` only the headers are checked and ``data`` is empty.
    ``referer`` is the article page the link came from (resolve_source's).
    Failures raise HTTPException(400) with the proxy's human-readable texts."""
    try:
        req = URLRequest(source_url, headers={**BROWSER_HEADERS, **({"Referer": referer} if referer else {})})
        resp = guarded_urlopen(req, timeout=30)
    except HTTPError as e:
        if e.code in (401, 403):
            raise HTTPException(
                status_code=400,
                detail="This site blocks server-side fetching. Please download the PDF in your browser and drop it onto the page.",
            )
        raise HTTPException(status_code=400, detail=f"upstream HTTP error: {e.code}")
    except URLError as e:
        raise HTTPException(status_code=400, detail=f"upstream URL error: {e.reason}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"pdf fetch failed: {str(e)}")
    try:
        content_type = resp.headers.get("Content-Type", "").lower()
        if "application/pdf" not in content_type:
            raise HTTPException(status_code=400, detail=f"final URL is not a PDF: {content_type}")
        final_url = resp.geturl()
        data = b""
        if want_bytes:
            chunks = []
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
        return final_url, data
    finally:
        resp.close()


def _share_allows_source(ws: str, scope, source_url: str) -> bool:
    """A share link may only proxy the exact source URL recorded on one of
    its own page blocks."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute(
            "SELECT id FROM unified_blocks WHERE parent_id = 'root' "
            "AND json_extract(properties, '$.source_url') = ?", (source_url,)).fetchall()
        return any(scope.allows_page(conn, r[0]) for r in rows)


@router.get("/pdf")
def proxy_pdf(source_url: str, request: Request):
    ws = resolve_ws(request)
    scope = share_scope(request)
    if scope is not None and not _share_allows_source(ws, scope, source_url):
        raise HTTPException(status_code=403, detail="not accessible via this share link")
    # Proxy cache ids hash the URL (the bytes aren't known yet), same length
    # as the content-hash upload names.
    pdf_doc_id = hashlib.sha256(source_url.encode()).hexdigest()[:DIGEST_CHARS]
    stored_name = f"{pdf_doc_id}.pdf"
    want_save = request.query_params.get("save") == "1"

    # If a local copy exists, redirect to the uploads route (supports Range
    # requests). The browser follows a redirect with no help from the app, so
    # the query that named the workspace — a share token, or ?ws= — rides
    # along, or the copy would be looked for in the session's own library.
    if storage.exists(ws, stored_name):
        carried = {k: v for k, v in request.query_params.items() if k in ("share", "ws")}
        target = f"/api/uploads/{pdf_doc_id}.pdf" + (f"?{urllib.parse.urlencode(carried)}" if carried else "")
        return RedirectResponse(target, status_code=302)

    # Download from source. Streamed through to the client as upstream bytes
    # arrive — buffering the whole file first meant the browser saw zero bytes
    # (and no progress) until the entire upstream download finished, which on
    # a slow link looked like a hang.
    try:
        req = URLRequest(source_url, headers=BROWSER_HEADERS)
        resp = guarded_urlopen(req, timeout=30)
    except HTTPError as e:
        if e.code in (401, 403):
            raise HTTPException(
                status_code=400,
                detail="This site blocks server-side fetching. Please download the PDF in your browser and drop it onto the page.",
            )
        raise HTTPException(status_code=400, detail=f"upstream HTTP error: {e.code}")
    except URLError as e:
        raise HTTPException(status_code=400, detail=f"upstream URL error: {e.reason}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"pdf proxy failed: {str(e)}")

    content_type = resp.headers.get("Content-Type", "").lower()
    if "application/pdf" not in content_type:
        resp.close()
        raise HTTPException(status_code=400, detail=f"final URL is not a PDF: {content_type}")
    final_url = resp.geturl()
    length = resp.headers.get("Content-Length") or ""

    def stream():
        # Saving spools the bytes to a file beside the uploads (storage.Spool;
        # never in memory: a scanned book is hundreds of MB) and stores it
        # only after a complete download — a client abort mid-stream must not leave a truncated
        # PDF in uploads (it would shadow the source forever). The storage
        # limits are asked as the bytes arrive: past them the spool is
        # dropped and the bytes only stream through, and an upstream whose
        # Content-Length is over them is never spooled at all.
        spool = None
        if want_save and (not length.isdigit() or can_store(ws, int(length))):
            try:
                spool = storage.Spool(ws)
            except OSError as e:
                log.info(f"[pdf] not caching {pdf_doc_id}: {e}")
        checked = complete = 0
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    complete = True
                    break
                if spool is not None:
                    spool.write(chunk)
                    if spool.size - checked >= storage.SPOOL_CHECK_BYTES:
                        checked = spool.size
                        if not can_store(ws, spool.size):
                            log.info(f"[pdf] not caching {pdf_doc_id} ({spool.size} bytes so far): over storage limits")
                            spool.discard()
                            spool = None
                yield chunk
        finally:
            resp.close()
            if spool is not None:
                spool.close()
                # best-effort cache: over the user's storage limits (or not a
                # PDF after all), just skip the save — the bytes still
                # streamed through
                if not complete:
                    spool.discard()
                elif not is_pdf(spool.head):
                    log.info(f"[pdf] not caching {pdf_doc_id}: the body is not a PDF")
                    spool.discard()
                elif can_store(ws, spool.size):
                    storage.put_path(ws, stored_name, spool.path)
                    pdf_meta.schedule(ws, pdf_doc_id)
                else:
                    log.info(f"[pdf] not caching {pdf_doc_id} ({spool.size} bytes): over storage limits")
                    spool.discard()

    headers = {"Cache-Control": "private, no-store", "X-Source-Url": final_url}
    if length.isdigit():
        headers["Content-Length"] = length
    return StreamingResponse(stream(), media_type="application/pdf", headers=headers)
