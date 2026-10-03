"""Finding papers: OpenAlex as the third registry and the citation graph,
merging, filtering and ordering the registries' records, the relaxed arXiv
retry, ranking a publication list's PDF links against the wanted paper,
trying several open-access copies, and what fetch_paper says about a
document's identity and version. Every fetch is faked; nothing reaches the
network."""

import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

import gamma.ai_web as web
import gamma.openalex as openalex
import gamma.routers.metadata as metadata_mod
import gamma.routers.pdf as pdf_mod
from gamma import paper_links
from gamma.ai_tools import run_agent_tool

from ai_fixtures import folder, org  # noqa: F401  (org is a fixture)
from test_ai_tools_web import Upstream, _text_pdf
from test_net_guard import transport  # noqa: F401 -- fake urllib transport fixture

WANTED = "Degenerate Raman Sideband Cooling of Trapped Cesium Atoms at Very High Atomic Densities"


# ------------------------------------------------------------- OpenAlex

def test_openalex_record_maps_a_work():
    rec = openalex.record({
        "id": "https://openalex.org/W1995491221", "doi": "https://doi.org/10.1103/PhysRevLett.81.5768",
        "display_name": "Degenerate <i>Raman</i> sideband cooling", "publication_year": 1998,
        "cited_by_count": 214, "authorships": [{"author": {"display_name": "Vladan Vuletić"}}],
        "primary_location": {"source": {"display_name": "Physical Review Letters"}},
        "best_oa_location": {"pdf_url": "https://lab.example.edu/prl_81_5768.pdf"}, "locations": [],
        "abstract_inverted_index": {"We": [0], "cesium": [2], "trap": [1]}})
    assert rec == {"title": "Degenerate Raman sideband cooling", "authors": ["Vladan Vuletić"],
                   "year": "1998", "venue": "Physical Review Letters", "volume": "", "pages": "",
                   "doi": "10.1103/PhysRevLett.81.5768", "arxiv_id": "", "source": "openalex",
                   "abstract": "We trap cesium", "cited_by": 214,
                   "oa_pdf": "https://lab.example.edu/prl_81_5768.pdf", "openalex_id": "W1995491221"}
    # An arXiv-registered DOI is the arXiv id, not a journal DOI.
    rec = openalex.record({"doi": "https://doi.org/10.48550/arxiv.1706.03762", "display_name": "Attention"})
    assert (rec["arxiv_id"], rec["doi"]) == ("1706.03762", "")
    # An arXiv location gives the preprint beside the journal DOI.
    rec = openalex.record({"doi": "https://doi.org/10.1080/x", "display_name": "Review",
                           "locations": [{"landing_page_url": "http://arxiv.org/abs/cond-mat/0606771v2"}]})
    assert (rec["arxiv_id"], rec["doi"]) == ("cond-mat/0606771", "10.1080/x")


def _json_get(seen, payload):
    def get(url, accept="", timeout=20, headers=None):
        seen.append((url, headers))
        return json.dumps(payload).encode()
    return get


def _refusing_get(code):
    def get(url, accept="", timeout=20, headers=None):
        raise HTTPError(url, code, "refused", {}, None)
    return get


def test_openalex_key_rides_in_the_header_and_failures_name_the_remedy(monkeypatch):
    seen = []
    monkeypatch.setattr(metadata_mod, "_http_get", _json_get(seen, {"results": [{"id": "W2", "display_name": "X"}]}))
    assert openalex.search("raman cooling", 3, from_year=2015, key="secret-key")[0]["title"] == "X"
    url, headers = seen[0]
    assert "secret-key" not in url and headers == {"Authorization": "Bearer secret-key"}
    query = parse_qs(urlparse(url).query)
    assert (query["search"], query["per-page"], query["filter"]) == (
        ["raman cooling"], ["3"], ["from_publication_date:2015-01-01"])
    openalex.search("raman cooling")
    assert seen[-1][1] == {}  # no key, no header

    monkeypatch.setattr(metadata_mod, "_http_get", _refusing_get(503))
    with pytest.raises(openalex.OpenAlexError, match="paused searches without an API key.*Online search"):
        openalex.search("q")
    with pytest.raises(openalex.OpenAlexError, match="answered HTTP 503"):
        openalex.search("q", key="k")  # with a key a 503 is just an outage
    monkeypatch.setattr(metadata_mod, "_http_get", _refusing_get(429))
    with pytest.raises(openalex.OpenAlexError, match="daily budget is used up"):
        openalex.search("q")
    monkeypatch.setattr(metadata_mod, "_http_get", _refusing_get(404))
    assert openalex.lookup("doi", "10.1/none") is None


def test_openalex_graph_filters(monkeypatch):
    seen = []
    results = {"results": [{"id": "W3", "display_name": "Less cited", "cited_by_count": 2},
                           {"id": "W4", "display_name": "Most cited", "cited_by_count": 90}]}
    monkeypatch.setattr(metadata_mod, "_http_get", _json_get(seen, results))
    work = {"id": "https://openalex.org/W9", "referenced_works": ["https://openalex.org/W3", "https://openalex.org/W4"],
            "related_works": []}
    refs = openalex.related(work, "references", rows=5, from_year=2000)
    assert [r["title"] for r in refs] == ["Most cited", "Less cited"]  # most cited first
    assert parse_qs(urlparse(seen[-1][0]).query)["filter"] == ["openalex:W3|W4,from_publication_date:2000-01-01"]
    openalex.related(work, "citations", rows=5)
    query = parse_qs(urlparse(seen[-1][0]).query)
    assert query["filter"] == ["cites:W9"] and query["sort"] == ["cited_by_count:desc"]
    openalex.related(work, "citations", rows=5, sort="recent")
    assert parse_qs(urlparse(seen[-1][0]).query)["sort"] == ["publication_date:desc"]
    assert openalex.related(work, "similar") == []  # no related works, no request
    with pytest.raises(ValueError):
        openalex.related(work, "siblings")


# ------------------------------------------------------------- search_papers

@pytest.fixture
def three_registries(monkeypatch):
    crossref = [{"title": "Degenerate Raman sideband cooling", "doi": "10.1103/PhysRevLett.81.5768",
                 "year": "1998", "authors": ["V. Vuletić"], "cited_by": 200, "abstract": ""}]
    found = [{"title": "Degenerate Raman Sideband Cooling", "doi": "10.1103/physrevlett.81.5768",
              "year": "1998", "authors": [], "cited_by": 214, "abstract": "We trap cesium.",
              "oa_pdf": "https://lab.example.edu/prl.pdf", "arxiv_id": ""},
             {"title": "Newer lattice cooling", "doi": "10.1/new", "year": "2021", "authors": [], "cited_by": 5}]
    arxiv = [{"title": "Old preprint", "arxiv_id": "quant-ph/9901001", "year": "1999", "authors": []}]
    asked = {}
    monkeypatch.setattr(metadata_mod, "_crossref_search",
                        lambda q, rows=5, detail=False, from_year=0, work_type="":
                        asked.update(crossref=(detail, from_year, work_type)) or crossref)
    monkeypatch.setattr(metadata_mod, "_arxiv_search",
                        lambda q, rows=5, detail=False: asked.update(arxiv=detail) or arxiv)
    monkeypatch.setattr(openalex, "search",
                        lambda q, rows=5, from_year=0, key="", work_type="", open_access=False:
                        asked.update(openalex=(from_year, key, work_type, open_access)) or found)
    return asked


def test_search_papers_merges_three_registries(three_registries):
    records = web.search_papers("raman sideband cooling", openalex_key="k")
    # OpenAlex leads the interleave; Crossref's record of the same work merges in.
    assert [r["title"] for r in records] == ["Degenerate Raman Sideband Cooling", "Old preprint",
                                             "Newer lattice cooling"]
    merged = records[0]
    assert (merged["cited_by"], merged["abstract"], merged["oa_pdf"]) == (214, "We trap cesium.",
                                                                        "https://lab.example.edu/prl.pdf")
    assert three_registries == {"crossref": (True, 0, ""), "arxiv": True,
                                "openalex": (0, "k", "", False)}
    text = web.format_records(records)
    assert "cited by 214" in text and "open-access PDF: https://lab.example.edu/prl.pdf" in text
    assert "\n  Abstract: We trap cesium.\n" in text


def test_search_papers_filters_and_orders(three_registries):
    by = lambda records: [r["title"] for r in records]  # noqa: E731
    assert by(web.search_papers("cooling", sort="citations")) == [
        "Degenerate Raman Sideband Cooling", "Newer lattice cooling", "Old preprint"]
    assert by(web.search_papers("cooling", sort="recent")) == [
        "Newer lattice cooling", "Old preprint", "Degenerate Raman Sideband Cooling"]
    assert by(web.search_papers("cooling", from_year=2000)) == ["Newer lattice cooling"]
    assert three_registries["crossref"] == (True, 2000, "")  # the filter goes to the registries too
    # A cited reference's exact title ranks first whatever the order.
    assert by(web.search_papers("Old preprint", sort="citations"))[0] == "Old preprint"


def test_search_papers_narrows_by_kind_and_open_access(three_registries):
    """A filter goes to the registries that can answer it, and skips the
    one that cannot hold that kind — instead of the model spelling
    "journal" or "preprint" into the query."""
    web.search_papers("cooling", kind="article")
    assert three_registries["crossref"] == (True, 0, "journal-article")
    assert three_registries["openalex"][2] == "article"
    assert "arxiv" not in three_registries          # arXiv holds no journal articles

    three_registries.clear()
    web.search_papers("cooling", kind="preprint")
    assert three_registries["arxiv"] is True and three_registries["openalex"][2] == "preprint"
    assert "crossref" not in three_registries       # Crossref searches the published record

    three_registries.clear()
    records = web.search_papers("cooling", open_access=True)
    assert three_registries["openalex"][3] is True
    # And a record no one can read for free is dropped from the merge.
    assert [r["title"] for r in records] == ["Degenerate Raman Sideband Cooling", "Old preprint"]


def test_a_work_listed_twice_in_one_reply_shrinks_to_one_line(three_registries):
    from gamma.ai_tools import Tally

    records = web.search_papers("cooling")
    tally = Tally()
    first = web.format_records(records, first_sight=tally.first_sight)
    assert "cited by 214" in first and "Abstract: We trap cesium." in first
    again = web.format_records(records, first_sight=tally.first_sight)
    assert "cited by 214" not in again and "listed earlier in this reply" in again
    assert len(again) < len(first) / 3


def test_search_papers_notes_a_registry_outage(three_registries, monkeypatch):
    def down(q, rows=5, from_year=0, key="", work_type="", open_access=False):
        raise openalex.OpenAlexError("OpenAlex's daily budget is used up")
    monkeypatch.setattr(openalex, "search", down)
    notes = []
    records = web.search_papers("cooling", notes=notes)
    assert records and notes == ["OpenAlex's daily budget is used up"]


def test_search_papers_tool_reports_filters_and_outages(org, three_registries, monkeypatch):
    monkeypatch.setattr(openalex, "search", lambda *a, **kw: (_ for _ in ()).throw(
        openalex.OpenAlexError("OpenAlex paused searches without an API key (heavy load)")))
    text, action = run_agent_tool(org[1]["ws"], folder(""), "search_papers",
                                  {"query": "cooling", "from_year": 1990, "sort": "recent"})
    assert "newest first, from 1990 on" in text
    assert "(Not searched: OpenAlex paused searches without an API key (heavy load).)" in text
    assert action["kind"] == "websearch"


def test_arxiv_search_retries_a_long_query_with_its_longest_words(monkeypatch):
    calls = []

    def api(url, accept="", timeout=20, headers=None):
        calls.append(parse_qs(urlparse(url).query)["search_query"][0])
        return b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

    monkeypatch.setattr(metadata_mod, "_http_get", api)
    assert metadata_mod._arxiv_search("Raman sideband cooling density limit rubidium lattice waist") == []
    assert len(calls) == 2
    assert calls[1] == "all:sideband AND all:rubidium AND all:cooling AND all:density AND all:lattice"
    calls.clear()
    metadata_mod._arxiv_search("raman sideband cooling")
    assert len(calls) == 1  # a short query has nothing to relax


def test_registry_detail_adds_abstracts_and_citations(monkeypatch):
    seen = []
    item = {"title": ["T"], "DOI": "10.1/t", "is-referenced-by-count": 7,
            "abstract": "<jats:title>Abstract</jats:title><jats:p>We &amp; they.</jats:p>"}
    monkeypatch.setattr(metadata_mod, "_http_get", _json_get(seen, {"message": {"items": [item]}}))
    rec = metadata_mod._crossref_search("q", 2, detail=True, from_year=2015)[0]
    assert (rec["abstract"], rec["cited_by"]) == ("We & they.", 7)
    query = parse_qs(urlparse(seen[-1][0]).query)
    assert "abstract" in query["select"][0] and query["filter"] == ["from-pub-date:2015"]
    # The metadata lookup stores the record, so it asks without the extras.
    plain = metadata_mod._crossref_search("q")[0]
    assert "abstract" not in plain and "cited_by" not in plain
    assert "abstract" not in parse_qs(urlparse(seen[-1][0]).query)["select"][0]

    feed = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2101.00001v1</id>'
            "<title>A</title><summary>  Cats\n and qubits. </summary></entry></feed>").encode()
    monkeypatch.setattr(metadata_mod, "_http_get", lambda url, **kw: feed)
    assert metadata_mod._arxiv_search("cats", detail=True)[0]["abstract"] == "Cats and qubits."
    assert "abstract" not in metadata_mod._arxiv_search("cats")[0]


# ------------------------------------------------------------- related_papers

def test_related_papers_follows_an_arxiv_paper_through_its_published_doi(org, monkeypatch):
    looked = []
    work = {"id": "https://openalex.org/W9", "display_name": "Attention", "doi": "https://doi.org/10.5555/nips",
            "cited_by_count": 9000}
    monkeypatch.setattr(metadata_mod, "_fetch_arxiv", lambda aid: {"title": "Attention", "doi": "10.5555/nips"})
    monkeypatch.setattr(openalex, "lookup", lambda kind, ident, key="": looked.append((kind, ident)) or (
        work if kind == "doi" else None))
    monkeypatch.setattr(openalex, "related", lambda w, relation, rows=8, from_year=0, key="", sort="": [
        {"title": "Follow-up", "doi": "10.1/f", "cited_by": 50, "authors": [], "year": "2020"}])
    text, action = run_agent_tool(org[1]["ws"], folder(""), "related_papers", {"source": "arXiv:1706.03762"})
    assert looked == [("doi", "10.5555/nips")]
    recent, _ = run_agent_tool(org[1]["ws"], folder(""), "related_papers",
                               {"source": "doi:10.5555/nips", "sort": "recent"})
    assert ", 1 shown, newest first" in recent
    assert text.startswith('Works citing "Attention" (cited by 9000 in all), 1 shown, most cited first')
    assert "[Follow-up](https://doi.org/10.1/f)" in text and 'fetch_paper(source="doi:10.1/f")' in text
    assert action["kind"] == "websearch" and action["summary"].startswith("Followed citations of “Attention”")

    monkeypatch.setattr(openalex, "find_title", lambda title, key="": None)
    text, action = run_agent_tool(org[1]["ws"], folder(""), "related_papers", {"source": "An unknown title"})
    assert action["error"] and "OpenAlex has no record" in text
    text, action = run_agent_tool(org[1]["ws"], folder(""), "related_papers",
                                  {"source": "doi:10.1/x", "relation": "siblings"})
    assert action["error"] and "relation must be one of" in text


# ------------------------------------------------------------- link ranking

def _publication_list(n=30, at=27):
    items = []
    for i in range(n):
        title = WANTED if i == at else f"Paper number {i} on optical lattices"
        items.append(f'<li><span><a href="/uploads/p{i}.pdf"><b>{title}.</b></a> '
                     f"A. Author, Phys. Rev. Lett. {i} (1998).</span></li>")
    return ("<html><body><a href='/about'>About the lab</a><ul>" + "".join(items) + "</ul>"
            "<table><tr><td>Cooling review, Rev. Mod. Phys. 70 (1998)</td>"
            "<td><a href='/files/review.pdf'>[PDF]</a></td></tr></table>"
            "<a href='/uploads/p3.pdf'>PDF</a></body></html>")


def test_pdf_links_keep_text_and_context_and_rank_against_the_title():
    links = paper_links.pdf_links(_publication_list(), "https://lab.example.edu/publications/")
    urls = [link["url"] for link in links]
    assert "https://lab.example.edu/about" not in urls  # not a document link
    assert len(urls) == len(set(urls)) == 31  # each URL once
    review = next(link for link in links if link["url"].endswith("review.pdf"))
    assert review["text"] == "[PDF]" and "Cooling review" in review["context"]  # a bare link borrows its item
    # Page order without a title: the wanted paper is far down.
    assert urls.index("https://lab.example.edu/uploads/p27.pdf") == 27
    best = paper_links.rank(links, WANTED, limit=3)
    assert best[0]["url"] == "https://lab.example.edu/uploads/p27.pdf" and best[0]["score"] > 1
    assert [link["url"] for link in paper_links.rank(links, "", limit=2)] == urls[:2]


# ------------------------------------------------------------- open-access copies

def test_resolver_tries_the_next_open_access_copy(transport, monkeypatch):
    routes, _ = transport
    doi = "https://doi.org/10.5555/oa"
    routes[doi] = (403, {"Content-Type": "text/html"}, b"blocked")
    routes["https://repo.example.org/broken.pdf"] = (404, {"Content-Type": "text/html"}, b"gone")
    routes["https://lab.example.edu/accepted.pdf"] = (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4")
    copies = [{"url": "https://repo.example.org/broken.pdf", "version": "publishedVersion", "host": "repository"},
              {"url": "https://lab.example.edu/accepted.pdf", "version": "acceptedVersion", "host": "repository"}]
    monkeypatch.setattr(pdf_mod, "_open_access_pdfs", lambda d: copies)
    resolved = pdf_mod.resolve_source(doi)
    assert resolved["source_url"] == "https://lab.example.edu/accepted.pdf"
    assert resolved["version"] == "accepted" and "accepted manuscript" in resolved["note"]
    # Published only: the accepted manuscript is not substituted; the published
    # copy that failed its probe is still the one handed on (the proxy retries).
    resolved = pdf_mod.resolve_source(doi, published_only=True)
    assert (resolved["source_url"], resolved["version"]) == ("https://repo.example.org/broken.pdf", "published")
    monkeypatch.setattr(pdf_mod, "_open_access_pdfs", lambda d: copies[1:])
    with pytest.raises(HTTPException) as refused:
        pdf_mod.resolve_source(doi, published_only=True)
    assert "not the published version (accepted)" in refused.value.detail


def test_open_access_copies_are_ranked_best_first(monkeypatch):
    data = {"oa_locations": [
        {"url_for_pdf": "https://arxiv.org/pdf/1", "version": "submittedVersion", "host_type": "repository"},
        {"url_for_pdf": "https://repo.example.org/a.pdf", "version": "publishedVersion", "host_type": "repository",
         "repository_institution": "MIT"},
        {"url_for_pdf": "https://pub.example.org/a.pdf", "version": "publishedVersion", "host_type": "publisher"},
        {"url_for_pdf": "https://pub.example.org/a.pdf", "version": "publishedVersion", "host_type": "publisher"},
        {"url_for_pdf": None, "version": "acceptedVersion"}]}
    monkeypatch.setattr(pdf_mod, "guarded_urlopen", lambda req, timeout=15: Upstream(
        req.full_url, json.dumps(data).encode(), "application/json"))
    assert [(c["url"], c["host"]) for c in pdf_mod._open_access_pdfs("10.1/a")] == [
        ("https://pub.example.org/a.pdf", "publisher"), ("https://repo.example.org/a.pdf", "MIT"),
        ("https://arxiv.org/pdf/1", "repository")]


# ------------------------------------------------------------- fetch_paper

PDF = _text_pdf(["Cat qubits are bosonic codes for bias-preserving gates.", "Results follow."])


@pytest.fixture
def fetched(monkeypatch):
    calls = []

    def resolve(source, **kw):
        calls.append((source, kw.get("published_only", False)))
        return {"source_url": "https://pub.example.org/paper.pdf", "version": "publisher"}

    monkeypatch.setattr(pdf_mod, "resolve_source", resolve)
    monkeypatch.setattr(web, "guarded_urlopen", lambda req, timeout=30: Upstream(
        req.full_url, PDF, "application/pdf"))
    web.clear_cache()
    yield calls
    web.clear_cache()


def test_fetch_paper_checks_the_title_and_names_the_version(org, fetched):
    ws = org[1]["ws"]
    text, _ = run_agent_tool(ws, folder(""), "fetch_paper",
                             {"source": "doi:10.1000/cat", "title": "Cat qubits are bosonic codes"})
    assert "Version: the publisher's PDF" in text
    assert "Identity: the expected title appears in the document." in text
    text, _ = run_agent_tool(ws, folder(""), "fetch_paper",
                             {"source": "doi:10.1000/cat", "title": "A completely different paper title"})
    assert 'Identity warning: the title "A completely different paper title" does not appear' in text
    assert len(fetched) == 1  # the second read came from the cache
    # A published-only read never reuses what an any-version read cached.
    run_agent_tool(ws, folder(""), "fetch_paper", {"source": "doi:10.1000/cat", "version": "published"})
    assert fetched == [("doi:10.1000/cat", False), ("doi:10.1000/cat", True)]


def test_fetch_paper_warns_when_a_published_only_read_got_a_preprint(org, monkeypatch, fetched):
    monkeypatch.setattr(pdf_mod, "resolve_source", lambda source, **kw: {
        "source_url": "https://arxiv.org/pdf/2101.00001", "version": "preprint"})
    text, _ = run_agent_tool(org[1]["ws"], folder(""), "fetch_paper",
                             {"source": "arXiv:2101.00001", "version": "published"})
    assert "Version: an arXiv preprint — not the published version the user asked for" in text


def test_fetch_paper_ranks_a_publication_lists_links(org, monkeypatch):
    def no_pdf(source, **kw):
        raise HTTPException(status_code=400, detail="no PDF behind this page")

    monkeypatch.setattr(pdf_mod, "resolve_source", no_pdf)
    monkeypatch.setattr(web, "guarded_urlopen", lambda req, timeout=30: Upstream(
        req.full_url, _publication_list().encode(), "text/html; charset=utf-8"))
    web.clear_cache()
    source = "https://lab.example.edu/publications/"
    text, _ = run_agent_tool(org[1]["ws"], folder(""), "fetch_paper", {"source": source, "title": WANTED})
    listed = text.split("PDF links on the page (31, best match for", 1)[1].splitlines()
    assert listed[1] == f"- {WANTED}.: https://lab.example.edu/uploads/p27.pdf"
    assert "Pass title=" not in text
    text, _ = run_agent_tool(org[1]["ws"], folder(""), "fetch_paper", {"source": source})
    assert "(Pass title= to rank the page's links against the paper you want.)" in text
    web.clear_cache()


def test_fetch_failure_allows_one_look_for_another_copy_only_with_web_search(org, monkeypatch):
    from gamma import fetch_handoff

    monkeypatch.setattr(pdf_mod, "resolve_source", lambda source, **kw: {"source_url": source})

    def blocked(req, timeout=30):
        raise HTTPError(req.full_url, 403, "Forbidden", {"cf-mitigated": "challenge"}, io.BytesIO(b""))

    monkeypatch.setattr(web, "guarded_urlopen", blocked)
    web.clear_cache()
    scope = {**folder(""), "handoff_user": org[1]["user_id"]}
    text, action = run_agent_tool(org[1]["ws"], scope, "fetch_paper", {"source": "https://pub.example.org/x.pdf"})
    assert action.get("handoff") and "fetch another version" in text and "search_web" not in text
    text, action = run_agent_tool(org[1]["ws"], {**scope, "web_engine": "brave"}, "fetch_paper",
                                  {"source": "https://pub.example.org/y.pdf"})
    assert "you may first run one search_web for the paper's exact title" in text
    assert "do not retry this source or answer from memory" in text
    fetch_handoff.clear()
    web.clear_cache()
