"""OpenAlex: the third scholarly registry ``search_papers`` asks (next to
Crossref and arXiv in routers/metadata.py) and the citation graph behind
``related_papers``.

OpenAlex indexes works across publishers and repositories with their
abstracts, citation counts, open-access locations and reference lists. It
answers without an account on a small daily budget (a search costs a tenth
of a cent of it, a lookup by id nothing) and pauses keyless searches when
its cluster is loaded; a free API key gives ten times the budget and
uninterrupted search. The key is the account's (Settings → AI → Tool usage →
Online search, gamma/search_services.py), else the server's
``GAMMA_OPENALEX_API_KEY``, and travels in the Authorization header, never
in a URL. Records come back in the shape the other registries use
(``title, authors, year, venue, doi, arxiv_id``) plus ``abstract``,
``cited_by``, ``oa_pdf`` (the best open-access PDF) and ``openalex_id``.
The registry switch (``GAMMA_METADATA_LOOKUP``) applies.
"""

import html
import json
import os
import re
import urllib.parse
from urllib.error import HTTPError

from .paper_links import title_key

API = "https://api.openalex.org"
SERVER_KEY = os.environ.get("GAMMA_OPENALEX_API_KEY", "").strip()
# What a record needs; a lookup also asks for the work's graph edges.
_SELECT = ("id,doi,display_name,publication_year,cited_by_count,authorships,"
           "primary_location,best_oa_location,locations,abstract_inverted_index")
_WORK_SELECT = _SELECT + ",referenced_works,related_works,referenced_works_count"
RELATIONS = ("references", "citations", "similar")
# Works named by id in one filter (OpenAlex ORs up to 100): a long
# reference list is ranked from its first this many.
_IDS_MAX = 50
_DOI_PREFIX_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.I)
_ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)
_WORK_ID_RE = re.compile(r"(W\d+)$")


class OpenAlexError(Exception):
    """OpenAlex did not answer; the message is for the model and names the
    remedy when there is one. ``status`` is the HTTP status (0 = no answer)."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _get(path: str, params: dict, key: str = "") -> dict:
    # Local import: the registry switch and User-Agent live with the other
    # registries, and gamma.* module load stays free of the routers package.
    from .routers.metadata import _http_get

    key = key or SERVER_KEY
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        return json.loads(_http_get(f"{API}{path}?{query}", accept="application/json", headers=headers))
    except HTTPError as e:
        remedy = "" if key else " — a free OpenAlex API key in Settings → AI → Tool usage → Online search avoids this"
        if e.code == 429:
            raise OpenAlexError("OpenAlex's daily budget is used up" + remedy, 429) from None
        if e.code == 503 and not key:
            raise OpenAlexError("OpenAlex paused searches without an API key (heavy load)" + remedy, 503) from None
        raise OpenAlexError(f"OpenAlex answered HTTP {e.code}", e.code) from None
    except (OSError, ValueError) as e:
        raise OpenAlexError(f"OpenAlex did not answer ({e})") from None


def _plain(text) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))).strip()


def _abstract(inverted) -> str:
    """OpenAlex keeps an abstract as {word: [positions]}; put it back in order."""
    if not isinstance(inverted, dict):
        return ""
    words = sorted((pos, word) for word, positions in inverted.items() for pos in positions or [])
    return _plain(" ".join(word for _, word in words))


def _short_id(value) -> str:
    m = _WORK_ID_RE.search(str(value or ""))
    return m.group(1) if m else ""


def _arxiv_id(work: dict, doi: str) -> str:
    from .routers.pdf import ARXIV_ID

    m = _ARXIV_DOI_RE.match(doi)
    if m:
        return m.group(1)
    pattern = re.compile(r"arxiv\.org/(?:abs|pdf)/(" + ARXIV_ID + r")", re.I)
    for loc in work.get("locations") or []:
        for url in (loc.get("landing_page_url"), loc.get("pdf_url")):
            m = pattern.search(url or "")
            if m:
                return m.group(1)
    return ""


def record(work: dict) -> dict:
    """A work as a registry record. An arXiv-registered DOI becomes the
    ``arxiv_id`` (``doi`` is kept for the published version, as the arXiv
    registry does)."""
    doi = _DOI_PREFIX_RE.sub("", work.get("doi") or "")
    arxiv_id = _arxiv_id(work, doi)
    if _ARXIV_DOI_RE.match(doi):
        doi = ""
    source = (work.get("primary_location") or {}).get("source") or {}
    best = work.get("best_oa_location") or {}
    return {
        "title": _plain(work.get("display_name")),
        "authors": [_plain((a.get("author") or {}).get("display_name"))
                    for a in work.get("authorships") or []],
        "year": str(work.get("publication_year") or ""),
        "venue": _plain(source.get("display_name")),
        "volume": "",
        "pages": "",
        "doi": doi,
        "arxiv_id": arxiv_id,
        "source": "openalex",
        "abstract": _abstract(work.get("abstract_inverted_index"))[:4000],
        "cited_by": work.get("cited_by_count"),
        "oa_pdf": best.get("pdf_url") or "",
        "openalex_id": _short_id(work.get("id")),
    }


def _year_filter(from_year: int) -> str:
    return f"from_publication_date:{int(from_year)}-01-01" if from_year else ""


# What ai_web's `kind` means to OpenAlex: its work types of the same name
# (a work published in a journal, or a preprint server's copy).
WORK_TYPES = ("article", "preprint")


def search(query: str, rows: int = 5, from_year: int = 0, key: str = "", *,
           work_type: str = "", open_access: bool = False) -> list[dict]:
    """Records for a free-text query in OpenAlex's relevance order, narrowed
    to a ``work_type`` (WORK_TYPES) and to works with a free full text
    (``open_access``). Raises OpenAlexError when OpenAlex does not answer."""
    if not (query or "").strip():
        return []
    narrow = [_year_filter(from_year)]
    if work_type in WORK_TYPES:
        narrow.append(f"type:{work_type}")
    if open_access:
        narrow.append("is_oa:true")
    data = _get("/works", {"search": query[:400], "per-page": rows, "select": _SELECT,
                           "filter": ",".join(f for f in narrow if f)}, key)
    return [record(w) for w in data.get("results") or [] if w.get("display_name")]


def find_title(title: str, key: str = "") -> dict | None:
    """The work whose title is exactly ``title`` (normalized), for a source
    without an identifier OpenAlex knows; None when there is none."""
    want = title_key(title)
    if not want:
        return None
    data = _get("/works", {"filter": "title.search:" + title[:300].replace(",", " "),
                           "per-page": 5, "select": _WORK_SELECT}, key)
    return next((w for w in data.get("results") or []
                 if title_key(_plain(w.get("display_name"))) == want), None)


def lookup(kind: str, ident: str, key: str = "") -> dict | None:
    """The work behind a DOI (``kind`` "doi"), an arXiv id ("arxiv") or an
    OpenAlex id ("openalex"), with its graph edges; None when OpenAlex has
    no such work. Lookups by id cost nothing. Raises OpenAlexError when
    OpenAlex does not answer."""
    path = {"doi": f"/works/doi:{ident}", "arxiv": f"/works/doi:10.48550/arXiv.{ident}",
            "openalex": f"/works/{ident}"}.get(kind)
    if not path:
        return None
    try:
        return _get(urllib.parse.quote(path, safe="/:"), {"select": _WORK_SELECT}, key)
    except OpenAlexError as e:
        if e.status == 404:
            return None
        raise


def related(work: dict, relation: str, rows: int = 8, from_year: int = 0, key: str = "",
            sort: str = "citations") -> list[dict]:
    """Works linked to ``work`` (a ``lookup`` result): the ones it cites
    ("references"), the ones citing it ("citations") or OpenAlex's
    related works ("similar"), most cited first or, with ``sort``
    "recent", newest first (the most cited works citing a classic are
    mostly reviews; the newest are the follow-up work). Raises
    OpenAlexError when OpenAlex does not answer."""
    if relation not in RELATIONS:
        raise ValueError(f"relation must be one of {', '.join(RELATIONS)}")
    recent = sort == "recent"
    years = _year_filter(from_year)
    if relation == "citations":
        params = {"filter": ",".join(f for f in (f"cites:{_short_id(work.get('id'))}", years) if f),
                  "sort": "publication_date:desc" if recent else "cited_by_count:desc",
                  "per-page": rows, "select": _SELECT}
    else:
        edges = work.get("referenced_works" if relation == "references" else "related_works") or []
        ids = [i for i in (_short_id(e) for e in edges) if i][:_IDS_MAX]
        if not ids:
            return []
        params = {"filter": ",".join(f for f in ("openalex:" + "|".join(ids), years) if f),
                  "per-page": len(ids), "select": _SELECT}
    results = _get("/works", params, key).get("results") or []
    records = [record(w) for w in results if w.get("display_name")]
    if recent:
        records.sort(key=lambda r: -int(r["year"] or 0))
    else:
        records.sort(key=lambda r: -(r["cited_by"] or 0))
    return records[:rows]
