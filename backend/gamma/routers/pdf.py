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
import time
import urllib.parse
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel

from ..auth import require_user, resolve_ws, share_scope
from ..db import connect_pages_db, ws_uploads_dir
from .. import pdf_meta
from ..logbuf import log
from ..net_guard import guarded_urlopen
from ..server_settings import can_store
from ..storage import DIGEST_CHARS, is_pdf, write_atomic

router = APIRouter(prefix="/api", tags=["pdf"])

# Identifier sent to Unpaywall's polite pool (its API requires an email
# parameter). A fixed project address — deliberately not configurable, so the
# open-access fallback needs zero setup.
CONTACT_EMAIL = "gamma-pdf-annotator@users.noreply.github.com"
OA_MAX_CANDIDATES = 4
OA_FETCH_BUDGET_SECONDS = 30
VersionPolicy = Literal["allow_preprint", "published_only"]

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


def _open_access_pdf_candidates_for_doi(doi: str) -> list[dict]:
    """Rank distinct Unpaywall PDF locations, keeping their version evidence.

    A best location can be stale or return an HTML wall, so resolution tries
    alternatives. Published copies precede accepted manuscripts and preprints.
    These are registry claims, not verification of the downloaded document.
    """
    try:
        url = (f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}"
               f"?email={urllib.parse.quote(CONTACT_EMAIL)}")
        req = URLRequest(url, headers={"User-Agent": "gamma-pdf-annotator/1.0"})
        with guarded_urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read(2_000_000))
        locs = [loc for loc in (data.get("oa_locations") or [])
                if isinstance(loc, dict) and isinstance(loc.get("url_for_pdf"), str)]
        best = data.get("best_oa_location")
        if isinstance(best, dict) and isinstance(best.get("url_for_pdf"), str):
            locs.insert(0, best)
        order = {"publishedVersion": 0, "acceptedVersion": 1, "submittedVersion": 2}
        locs.sort(key=lambda l: order.get(str(l.get("version") or ""), 3))
        candidates, seen = [], set()
        for loc in locs:
            url = urllib.parse.urldefrag(loc["url_for_pdf"].strip())[0]
            if not url.lower().startswith(("https://", "http://")) or url in seen:
                continue
            seen.add(url)
            version = str(loc.get("version") or "")
            candidates.append({"url": url, "version": version if version in order else "",
                               "source_kind": "unpaywall", "doi": doi})
        return candidates
    except Exception as e:
        log.warning(f"[resolve-pdf] unpaywall lookup failed: {e}")
        return []


class ResolvePdfRequest(BaseModel):
    source_url: str
    allow_oa: bool = True  # substitute an open-access copy when the publisher PDF is unavailable
    version_policy: VersionPolicy = "allow_preprint"


# Plain `def` on purpose: FastAPI runs sync endpoints in its threadpool, so the
# (potentially slow) upstream fetches here don't block the event loop.
@router.post("/resolve-pdf")
def resolve_pdf(payload: ResolvePdfRequest, request: Request):
    require_user(request)
    result = resolve_source(payload.source_url, payload.allow_oa, version_policy=payload.version_policy)
    if payload.version_policy == "published_only":
        provenance = result.get("provenance") or {}
        if provenance.get("version") != "publishedVersion" or provenance.get("version_verified") is not True:
            raise HTTPException(
                status_code=400,
                detail="A PDF was found, but its published version could not be confirmed. "
                       "The published_only policy requires a verified published version; "
                       "try the paper's journal DOI.",
            )
    return result


def resolve_source(source_url: str, allow_oa: bool = True, trace: dict | None = None,
                   *, version_policy: VersionPolicy = "allow_preprint") -> dict:
    """URL / bare identifier → ``{"source_url": <fetchable PDF url>, "note"?,
    "referer"?}``, or an HTTPException(400) with a human-readable reason.
    Shared by the resolve endpoint, the extension's /api/clip and the AI's
    fetch_paper. ``referer`` is the article page a PDF link came from (the
    download should send it, as the page's own link would). A ``trace`` dict
    collects what the walk saw — ``page_url`` (the landing page after
    redirects), ``pdf_urls`` (the PDF links it advertised), ``doi``,
    ``blocked`` (the first request was refused) — so a caller can say where
    a person could get the PDF instead. ``provenance`` records the URL and
    registry version claim without asserting document identity. ``published_only``
    rejects known preprints and restricts OA substitutions to published versions;
    direct PDFs whose version is unknown are flagged for caller verification."""
    if version_policy not in ("allow_preprint", "published_only"):
        raise HTTPException(status_code=400, detail="Invalid paper version policy.")
    url = _identifier_to_url((source_url or "").strip())
    trace = {} if trace is None else trace
    doi_m = _DOI_URL_RE.search(url)
    doi = urllib.parse.unquote(doi_m.group(1)).rstrip(".,;") if doi_m else ""

    # arXiv abstract and HTML pages (and arXiv DOIs) go straight to the PDF
    m = _ARXIV_PAGE_RE.search(url) or _ARXIV_DOI_RE.search(url)
    if m:
        url = f"https://arxiv.org/pdf/{m.group(1)}"
    trace.update(page_url=url, pdf_urls=[], doi=doi, blocked=False, oa_attempts=[])

    def is_arxiv(u: str) -> bool:
        return urllib.parse.urlsplit(u).hostname in ("arxiv.org", "www.arxiv.org", "export.arxiv.org")

    def resolved(pdf_url: str, *, final_url: str = "", source_kind: str = "direct",
                 version: str = "", referer: str = "", note: str = "") -> dict:
        if is_arxiv(final_url or pdf_url):
            source_kind, version = "arxiv", "submittedVersion"
        if version_policy == "published_only" and version and version != "publishedVersion":
            raise HTTPException(status_code=400, detail=
                                "This source is a preprint or manuscript. The published_only policy "
                                "requires the published paper; use its journal DOI or publisher PDF.")
        provenance = {"source_kind": source_kind, "source_url": pdf_url,
                      "final_url": final_url or pdf_url, "version": version, "doi": doi,
                      "identity_verified": False,
                      "version_verified": bool(version),
                      "version_evidence": "unpaywall" if source_kind == "unpaywall" else
                                          "arxiv_source" if source_kind == "arxiv" else ""}
        result = {"source_url": pdf_url, "provenance": provenance}
        if referer:
            result["referer"] = referer
        if note:
            result["note"] = note
        return result

    if version_policy == "published_only" and is_arxiv(url):
        # Do not fetch a known preprint before applying the caller's policy.
        return resolved(url)

    attempted_urls = set()
    successful_pdfs = {}

    def try_resolve(u: str, referer: str = "", timeout: float = 20, read_html: bool = True):
        """(final_url, content_type, body). Body is only read for non-PDF
        responses (capped) so HTML pages can be inspected for PDF pointers."""
        attempted_urls.add(u)
        req = URLRequest(u, headers={**BROWSER_HEADERS, **({"Referer": referer} if referer else {})})
        with guarded_urlopen(req, timeout=timeout) as resp:
            attempted_urls.add(resp.geturl())
            ctype = resp.headers.get("Content-Type", "").lower()
            body = b"" if "application/pdf" in ctype or not read_html else resp.read(600_000)
            if "application/pdf" in ctype:
                successful_pdfs[u] = successful_pdfs[resp.geturl()] = (resp.geturl(), referer)
            return resp.geturl(), ctype, body

    def can_return(result: dict) -> bool:
        # A DOI lets Unpaywall establish the version of an otherwise unknown
        # publisher PDF. Other direct PDFs retain unknown provenance for the
        # caller to verify, rather than pretending a lab upload is published.
        return (version_policy != "published_only" or not doi or
                result["provenance"]["version"] == "publishedVersion")

    blocked = False
    content_type = ""
    final_url = url
    body = b""
    try:
        final_url, content_type, body = try_resolve(url)
        if "application/pdf" in content_type:
            result = resolved(final_url)
            if can_return(result):
                return result
        trace["page_url"] = final_url
    except HTTPError as e:
        # arxiv.org refuses some PDFs to programs (406) that its export
        # host, the one arXiv asks automated clients to use, serves.
        mirror = _ARXIV_PDF_URL_RE.sub("https://export.arxiv.org/pdf/", url)
        if mirror != url:
            try:
                mirror_final, mirror_type, _ = try_resolve(mirror)
                if "application/pdf" in mirror_type:
                    return resolved(mirror, final_url=mirror_final)
            except Exception as mirror_error:
                log.warning(f"[resolve-pdf] arXiv export host failed too: {mirror_error}")
        if e.code not in (401, 403, 418, 429):
            raise HTTPException(status_code=400, detail=f"upstream HTTP error: {e.code}")
        blocked = trace["blocked"] = True
        trace["page_url"] = e.geturl() or url  # the refusing host, past doi.org's redirect
    except URLError as e:
        raise HTTPException(status_code=400, detail=f"upstream URL error: {e.reason}")
    except HTTPException:
        raise
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
            result = resolved(final_url)
            if can_return(result):
                return result
        trace["page_url"] = final_url
        html = body.decode("utf-8", "replace") if body else ""
    if not doi and html:
        doi = (_meta_content(html, "citation_doi") or _meta_content(html, "dc.identifier")).strip()
        doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
        if not re.match(r"^10\.\d{4,9}/", doi):
            doi = ""
    trace["doi"] = doi
    trace["pdf_urls"] = _publisher_pdf_candidates(final_url, html)
    for pdf_url in trace["pdf_urls"]:
        if version_policy == "published_only" and is_arxiv(pdf_url):
            continue
        try:
            pdf_final_url, ct2, _ = try_resolve(pdf_url, referer=final_url)
            if "application/pdf" in ct2:
                # Return the canonical URL, not the redirect target — hosts
                # like nature.com append one-time tokens on redirect, and the
                # doc id is a hash of this URL, so it must stay stable.
                result = resolved(pdf_url, final_url=pdf_final_url, referer=final_url,
                                  source_kind="article_page")
                if can_return(result):
                    return result
        except Exception as e:
            log.warning(f"[resolve-pdf] publisher PDF fetch failed: {e}")

    # For DOI links (or pages that state their DOI), the publisher PDF is
    # usually paywalled or bot-blocked — look for a legal open-access copy.
    if doi:
        if not allow_oa:
            raise HTTPException(
                status_code=400,
                detail="The publisher's PDF isn't accessible server-side (access restriction or browser check). "
                       "Open-access fallback is disabled in your settings — download the PDF in "
                       "your browser and drop it onto Gamma.",
            )
        deadline = time.monotonic() + OA_FETCH_BUDGET_SECONDS
        candidates = _open_access_pdf_candidates_for_doi(doi)
        eligible = [candidate for candidate in candidates
                    if (candidate["url"] not in attempted_urls or candidate["url"] in successful_pdfs) and
                    (version_policy != "published_only" or
                     (candidate["version"] == "publishedVersion" and not is_arxiv(candidate["url"])))]
        def oa_note(version: str) -> str:
            if version and version != "publishedVersion":
                pretty = {"acceptedVersion": "accepted manuscript",
                          "submittedVersion": "preprint (submitted version)"}.get(version, version)
                return (f"The publisher's PDF couldn't be fetched — loaded the open-access {pretty} instead. "
                        "For the published version, download it in your browser and replace the "
                        "source file via the page's source button.")
            if not version:
                return ("The publisher's PDF couldn't be fetched — loaded an open-access copy instead. "
                        "Its publication version has not been identified.")
            return ""

        bounded_candidates = eligible[:OA_MAX_CANDIDATES]
        for index, candidate in enumerate(bounded_candidates):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            oa_url, oa_version = candidate["url"], candidate["version"]
            attempt = {"url": oa_url, "version": oa_version}
            trace["oa_attempts"].append(attempt)
            try:
                oa_referer = ""
                if oa_url in successful_pdfs:
                    final_url, oa_referer = successful_pdfs[oa_url]
                    content_type = "application/pdf"
                else:
                    final_url, content_type, _ = try_resolve(oa_url, timeout=min(10, remaining), read_html=False)
                if "application/pdf" in content_type:
                    result = resolved(oa_url, final_url=final_url, note=oa_note(oa_version), referer=oa_referer,
                                      source_kind="unpaywall", version=oa_version)
                    # A PDF content type is only a probe. Readers can try the
                    # remaining registry copies if the actual bytes are corrupt,
                    # unreadable or belong to a different paper.
                    result["alternatives"] = [
                        resolved(other["url"], source_kind="unpaywall", version=other["version"],
                                 note=oa_note(other["version"]),
                                 referer=successful_pdfs.get(other["url"], ("", ""))[1])
                        for other in bounded_candidates[index + 1:]]
                    for alternative in result["alternatives"]:
                        # Do not invent an observed redirect URL for a location
                        # that has only been reported by the registry.
                        alternative["provenance"]["final_url"] = successful_pdfs.get(
                            alternative["source_url"], ("", ""))[0]
                    attempt["outcome"] = "pdf"
                    return result
                attempt["outcome"] = "not_pdf"
            except Exception as e:
                attempt["outcome"] = "failed"
                log.warning(f"[resolve-pdf] open-access PDF fetch failed: {e}")
        policy_note = (" No accessible published-version copy was found under published_only."
                       if version_policy == "published_only" else " No accessible open-access copy was found.")
        raise HTTPException(
            status_code=400,
            detail="This leads to a publisher page whose PDF isn't accessible server-side "
                   "(access restriction or browser check)." + policy_note + " Open the link in your "
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
    uploads = ws_uploads_dir(ws)
    # Proxy cache ids hash the URL (the bytes aren't known yet), same length
    # as the content-hash upload names.
    pdf_doc_id = hashlib.sha256(source_url.encode()).hexdigest()[:DIGEST_CHARS]
    local_path = uploads / f"{pdf_doc_id}.pdf"
    want_save = request.query_params.get("save") == "1"

    # If a local copy exists, redirect to the uploads route (supports Range
    # requests). The browser follows a redirect with no help from the app, so
    # the query that named the workspace — a share token, or ?ws= — rides
    # along, or the copy would be looked for in the session's own library.
    if local_path.exists():
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
        # Saving buffers chunks on the side and writes the file only after a
        # complete download — a client abort mid-stream must not leave a
        # truncated PDF in uploads (it would shadow the source forever).
        chunks = [] if want_save else None
        complete = False
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    complete = True
                    break
                if chunks is not None:
                    chunks.append(chunk)
                yield chunk
        finally:
            resp.close()
            if chunks is not None and complete:
                data = b"".join(chunks)
                # best-effort cache: over the user's storage limits (or not a
                # PDF after all), just skip the save — the bytes still
                # streamed through
                if not is_pdf(data):
                    log.info(f"[pdf] not caching {pdf_doc_id}: the body is not a PDF")
                elif can_store(ws, len(data)):
                    write_atomic(local_path, data)
                    pdf_meta.schedule(ws, pdf_doc_id)
                else:
                    log.info(f"[pdf] not caching {pdf_doc_id} ({len(data)} bytes): over storage limits")

    headers = {"Cache-Control": "private, no-store", "X-Source-Url": final_url}
    if length.isdigit():
        headers["Content-Length"] = length
    return StreamingResponse(stream(), media_type="application/pdf", headers=headers)
