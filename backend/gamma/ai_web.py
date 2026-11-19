"""The agent's scholarly reach: searching the registries, following a
paper's citations, and reading a paper that is not in the library
(``search_papers`` / ``related_papers`` / ``fetch_paper`` in
``ai_tools/web.py``; general web search is gamma/search_services.py, reading
any web page gamma/web_fetch.py).

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
and the source is a web page, the page as fetch_url reads it (web_fetch.get,
cut at its size cap rather than refused), with the page's PDF links
(gamma/paper_links.py) for the model to rank against the paper it wants and
a note, in the model's words, of what kept the PDF out of reach. Fetched
papers live in a small in-memory cache (web_fetch.DocCache) so the model can
read a long paper in successive windows without re-downloading it — nothing
is stored on disk and a restart forgets everything.

A fetch that meets a wall only a person gets past — a CAPTCHA or bot check,
a sign-in page, a refusal, a paywall that serves only the article page — says
which (``FetchError.wall`` / a document's ``wall``, web_fetch.WALLS) and
where the person would go, so the chat can hand the fetch to the user's
browser (``gamma/fetch_handoff.py``).
"""

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit

from fastapi import HTTPException

from . import openalex, paper_links, publisher_sessions, web_fetch
from .net_guard import browsing_session
from .web_fetch import (BROWSER_HEADERS, WALLS, DocCache, FetchError, access_wall, entry, get, page_document,
                        pdf_document, read_bounded, refusal, uri)

SEARCH_LIMIT_DEFAULT = 8
SEARCH_LIMIT_MAX = 20
SORTS = ("relevance", "citations", "recent")
_ABSTRACT_SHOWN = 400  # chars of a record's abstract a search result shows

_DOI_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)$", re.I)
_ARXIV_RE = re.compile(
    r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?"
    r"([0-9]{4}\.[0-9]{4,5}|[a-z][a-z-]*(?:\.[a-z]{2})?/[0-9]{7})(?:v\d+)?(?:\.pdf)?$", re.I)


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
    return paper_links.title_key(title, 80)


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

# The papers read, under the source strings they were asked for as: 24 of
# them and 30 M characters of text at most, kept until crowded out (web
# pages read with fetch_url have a cache of their own, web_fetch's).
_papers = DocCache(24, 30_000_000)


def _source_url(source: str) -> str:
    """The URL a non-PDF fallback fetches for a source string."""
    kind, ident = identifier(source)
    if kind == "doi":
        return f"https://doi.org/{ident}"
    if kind == "arxiv":
        return f"https://arxiv.org/abs/{ident}"
    return source if source.lower().startswith(("http://", "https://")) else ""


def fetch_document(source: str, published_only: bool = False) -> dict:
    """The document behind ``source`` (a DOI, arXiv id or URL) as
    ``{"url", "kind": "pdf"|"html"|"text", "title", "pages": [text per
    page], "chars", "note", "version"}`` — from the cache when it was
    fetched before. ``version`` is the resolver's (``resolve_source``):
    "publisher", "preprint", "published", "accepted", "submitted" or ""
    (unknown); ``published_only`` refuses to substitute an unpublished
    open-access copy. A web page (web_fetch.page_document: also ``meta``,
    ``outline``, ``fetched`` and ``truncated``) says in ``note`` why no PDF
    was reachable, carries ``links`` (its PDF-looking links with their
    text, web_page.Page.links) and, when it is an article page whose PDF
    was out of reach, ``wall`` with ``open_url`` / ``pdf_url``. A URL is
    read as the URI it spells (web_fetch.uri), so an address copied as a
    page shows it reads too. Raises FetchError with a model-readable
    reason."""
    source = (source or "").strip()
    if not source:
        raise FetchError("empty source — pass a DOI, an arXiv id or an http(s) URL")
    if source.lower().startswith(("http://", "https://")):
        source = uri(source)
    scope = publisher_sessions.cache_scope()
    # A published-only read never gets the copy an any-version read cached.
    alias = f"{source} [published]" if published_only else source
    doc = _papers.get(scope, alias)
    if doc:
        return doc
    if not _source_url(source):
        raise FetchError("source must be a DOI (10.…), an arXiv id (2301.12345) or an http(s) URL")
    # One cookie jar for the resolver's walk, the download and the fallback.
    with browsing_session():
        doc = _fetch(source, published_only)
    _papers.put(scope, [alias], doc)
    return doc


def identity(doc: dict, title: str) -> bool | None:
    """Whether the fetched document is the paper titled ``title``: its
    normalized title appears on the first pages (a PDF) or in the page
    text. None when there is no title to check."""
    want = paper_links.title_key(title)
    if len(want) < 12:
        return None
    head = " ".join(doc.get("pages") or [])[:30_000] if doc.get("kind") != "pdf" else \
        " ".join((doc.get("pages") or [])[:3])
    return want in paper_links.title_key(head)


def _fetch(source: str, published_only: bool = False) -> dict:
    from .routers.pdf import resolve_source

    reason = wall = source_note = referer = version = ""
    trace: dict = {}
    try:
        resolved = resolve_source(source, trace=trace, published_only=published_only)
        pdf_url = resolved["source_url"]
        source_note = resolved.get("note", "")
        referer = resolved.get("referer", "")
        version = resolved.get("version", "")
    except HTTPException:
        # Its detail tells the person clipping what to do in their browser;
        # the model hears what the walk met.
        pdf_url, reason = "", _no_pdf(trace, published_only)
        wall = "denied" if trace.get("blocked") else ""
    page_url = trace.get("page_url") or _source_url(source)
    want_pdf = pdf_url or next(iter(trace.get("pdf_urls") or []), "")
    if pdf_url:
        try:
            got = read_bounded(pdf_url, {**BROWSER_HEADERS, **({"Referer": referer} if referer else {})})
        except FetchError:
            raise
        except HTTPError as e:
            wall = refusal(e)
            reason = f"the PDF at {pdf_url} answered HTTP {e.code}"
            if not wall:
                raise FetchError(reason)
        except (URLError, OSError, ValueError) as e:
            raise FetchError(f"could not fetch the PDF at {pdf_url}: {e}")
        else:
            if "application/pdf" in got.ctype or got.body[:5] == b"%PDF-":
                return pdf_document(pdf_url, got.body, source_note, version)
            wall = access_wall(got.url, got.headers, got.body)
            reason = (f"{pdf_url} is not a PDF ({got.ctype or 'no content type'})"
                      + (f" — it is {WALLS[wall]}" if wall else ""))
    # No PDF: the source's own page, if it is one, read as fetch_url reads it.
    fallback = _source_url(source)
    if wall and fallback == pdf_url:
        raise FetchError(reason, wall=wall, open_url=entry(page_url, pdf_url), pdf_url=want_pdf)
    try:
        got = get(fallback)
    except FetchError as e:
        raise FetchError(f"no PDF{f' ({reason})' if reason else ''} and {e}", wall=e.wall or wall,
                         open_url=entry(e.open_url, page_url, fallback), pdf_url=want_pdf)
    if got.page is None:  # a PDF the resolver did not take for one (served as application/octet-stream)
        return pdf_document(got.url, got.body)
    doc = page_document(got)
    # PDF links the resolver has not tried already (those met the wall).
    tried = {pdf_url, *(trace.get("pdf_urls") or [])}
    links = got.page.links
    served = got.ctype.split(";")[0].strip() or "a page without a content type"
    doc.update(note=reason or f"the address serves {served}, not a PDF",
               links=[link for link in links if link["url"] not in tried], version="")
    # An article page whose PDF was out of reach (it advertised one, or the
    # DOI's publisher refused): only a person with access gets the rest.
    if wall or trace.get("pdf_urls") or trace.get("doi"):
        doc.update(wall=wall or "abstract", open_url=got.url, pdf_url=want_pdf)
    return doc


def _no_pdf(trace: dict, published_only: bool) -> str:
    """What the resolver's walk met instead of a PDF, from its ``trace``:
    a refusal, the page's advertised PDF links that gave none, a DOI with
    no open-access copy."""
    said = []
    if trace.get("blocked"):
        said.append(f"{urlsplit(trace.get('page_url') or '').hostname or 'the site'} refused this server")
    tried = trace.get("pdf_urls") or []
    if tried:
        said.append("the PDF link the page advertises gave no PDF" if len(tried) == 1
                    else f"none of the {len(tried)} PDF links the page advertises gave a PDF")
    if trace.get("doi"):
        said.append(f"no {'published ' if published_only else ''}open-access copy of doi:{trace['doi']} was found")
    return "; ".join(said)


def clear_cache():
    """Forget every paper and web page read (both caches)."""
    _papers.clear()
    web_fetch.clear_cache()
