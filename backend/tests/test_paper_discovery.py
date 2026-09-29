"""Discovery and real-fetch acceptance, independent of live search providers."""

import pytest
from urllib.error import HTTPError

from gamma import ai_web, ai_tools, fetch_handoff, web_search
from gamma.paper_links import pdf_link_candidates
from gamma.routers import metadata, pdf
from ai_fixtures import folder, org  # noqa: F401
from test_ai_tools_web import PDF, Upstream, _text_pdf, upstream  # noqa: F401


TITLE = "Degenerate Raman Sideband Cooling of Trapped Cesium Atoms at Very High Atomic Densities"


def test_old_paper_beyond_default_link_cap_ranks_first():
    markup = "<ul>" + "".join(f'<li><a href="new{i}.pdf">New experiment {i}</a></li>' for i in range(50))
    markup += f'<li><a href="prl_81_5768.pdf">{TITLE}</a> Vuletic et al., PRL 81, 5768 (1998).</li></ul>'
    results = pdf_link_candidates(markup, "https://lab.example.org/publications/", TITLE)
    assert len(results) == 8
    assert results[0]["url"].endswith("prl_81_5768.pdf")
    assert results[0]["title"] == TITLE
    assert "1998" in results[0]["context"]


def test_generic_pdf_anchor_uses_same_citation_across_line_break():
    markup = "".join(f'<li><a href="new{i}.pdf">New {i}</a></li>' for i in range(10))
    markup += f'<li>{TITLE}<br>Vuletic, 1998 <a href="old.pdf"><img alt="PDF"></a></li>'
    result = pdf_link_candidates(markup, "https://lab.example.org/", TITLE, limit=1)[0]
    assert result["url"].endswith("old.pdf") and TITLE in result["context"]


def test_bad_links_and_scripts_cannot_abort_or_pollute_candidates():
    markup = ('<script><a href="fake.pdf">PDF</a></script><a href="http://[broken">PDF</a>'
              '<a href="javascript:alert(1)">PDF</a><a href="https://user:pass@x.org/a.pdf">PDF</a>'
              '<a href="good.pdf">The &amp; paper</a><a href="good.pdf">PDF</a>')
    results = pdf_link_candidates(markup, "https://lab.example.org/")
    assert len(results) == 1 and results[0]["title"] == "The & paper"


def test_long_and_conflicting_titles_do_not_collapse(monkeypatch):
    long = "An exceptionally detailed investigation of cooling atoms inside an optical lattice " * 2
    records = [{"title": long + "using rubidium", "doi": "10.1234/rb"},
               {"title": long + "using cesium", "doi": "10.1234/cs"},
               {"title": "Cooling atoms", "doi": "10.1234/first"}]
    monkeypatch.setattr(metadata, "_crossref_search", lambda *a, **kw: records)
    monkeypatch.setattr(metadata, "_arxiv_search", lambda *a, **kw: [
        {"title": "Cooling atoms", "doi": "10.1234/different", "arxiv_id": "1234.56789"}])
    assert len(ai_web.search_papers("cooling atoms")) == 4


def test_registry_merge_preserves_available_abstract(monkeypatch):
    monkeypatch.setattr(metadata, "_crossref_search", lambda *a, **kw: [{"title": TITLE, "doi": "10.1234/a"}])
    monkeypatch.setattr(metadata, "_arxiv_search", lambda *a, **kw: [
        {"title": TITLE, "doi": "10.1234/a", "abstract": "Cesium atoms in a one-dimensional lattice."}])
    results = ai_web.search_papers(TITLE)
    assert len(results) == 1
    assert "Abstract: Cesium atoms" in ai_web.format_records(results)


def document(text, **provenance):
    return {"url": "https://lab.example.org/paper.pdf", "kind": "pdf", "pages": [text],
            "chars": len(text), "provenance": provenance}


@pytest.mark.parametrize("text", [
    "A different paper\nAuthors\nAbstract\nWe discuss\n" + TITLE,
    TITLE + " Revisited\nAuthors\nAbstract\nMore work",
    "A different paper\nDepartment of Physics\n" + TITLE,
])
def test_references_and_title_prefixes_do_not_verify_identity(text):
    with pytest.raises(ai_web.FetchError, match="identity mismatch"):
        ai_web.verify_document(document(text), expected_title=TITLE)


def test_wrapped_title_and_unicode_match_with_explicit_evidence():
    text = "PRL 81 1998\nDegenerate Raman Sideband Cooling of Trapped Cesium Atoms\nat Very High Atomic Densities\nV. Vuletic\nDepartment of Physics"
    result = ai_web.verify_document(document(text), expected_title=TITLE)
    assert result["provenance"]["identity_verified"]
    assert result["provenance"]["identity_evidence"] == "title_in_opening_lines"


def test_doi_alone_is_not_document_identity():
    result = ai_web.verify_document(document("References\n10.1234/a", doi="10.1234/a"), expected_doi="10.1234/a")
    assert result["provenance"]["doi_in_text"]
    assert not result["provenance"]["identity_verified"]
    with pytest.raises(ai_web.FetchError, match="resolved DOI differs"):
        ai_web.verify_document(document("A paper", doi="10.1234/b"), expected_doi="10.1234/a")


RETITLED = ("Rev. Sci. Instrum. 84, 043109 (2013); doi: 10.1063/1.\n4802682\n"
            "Creation of quantum-degenerate gases of ytterbium in a compact 2D-/3D-magneto-optical trap setup\n"
            "S. Dörscher, A. Thobe\nAbstract\nWe present a compact source.")
PREPRINT_TITLE = "Creation of Quantum-Degenerate Gases of Ytterbium in a Compact 2D-/3D-MOT Setup"


def test_a_pdf_printing_the_requested_doi_is_that_paper_under_a_reworded_title():
    # The model took its title from the preprint; the published PDF names itself otherwise.
    fetched = dict(doi="10.1063/1.4802682", requested_source="doi:10.1063/1.4802682")
    result = ai_web.verify_document(document(RETITLED, **fetched), expected_title=PREPRINT_TITLE,
                                    expected_doi="10.1063/1.4802682")
    provenance = result["provenance"]
    assert provenance["identity_verified"] and provenance["identity_evidence"] == "doi_on_first_page"
    assert provenance["title_differs"]
    # Without a title to weigh, the printed DOI alone identifies it.
    alone = ai_web.verify_document(document(RETITLED, requested_source="https://doi.org/10.1063/1.4802682"))
    assert alone["provenance"]["identity_evidence"] == "doi_on_first_page"


@pytest.mark.parametrize("text", [
    # The DOI only in the references: another paper citing it, however alike its title.
    PREPRINT_TITLE + ": a comment\nAbstract\nText\nReferences\n[1] doi: 10.1063/1.4802682",
    # Another paper printing the DOI up front shares no title.
    "doi: 10.1063/1.4802682\nA Comment on lattice clocks\nAbstract",
    # A longer DOI is not the requested one.
    "doi: 10.1063/1.48026821\nCreation of quantum-degenerate gases of ytterbium in a compact setup",
])
def test_a_printed_doi_does_not_identify_another_paper(text):
    with pytest.raises(ai_web.FetchError, match="identity mismatch"):
        ai_web.verify_document(document(text, doi="10.1063/1.4802682"), expected_title=PREPRINT_TITLE)


def test_a_printed_doi_counts_only_for_the_doi_the_pdf_was_got_for():
    result = ai_web.verify_document(document(RETITLED, requested_source="https://lab.example.org/paper.pdf"))
    assert result["provenance"]["identity_evidence"] == "unverified"


def _delivered(source, publisher, from_url, text):
    rid = fetch_handoff.open_request("reader", source, wall="captcha", url=publisher)["id"]
    fetch_handoff.deliver("reader", rid, _text_pdf([text]), from_url)
    return fetch_handoff.delivered("reader", source)


def test_a_pdf_the_browser_took_from_the_publisher_site_is_the_published_version():
    doc = _delivered("doi:10.1038/nphys3061", "https://www.nature.com/articles/nphys3061",
                     "https://www.nature.com/articles/nphys3061.pdf",
                     "NATURE PHYSICS DOI: 10.1038/NPHYS3061 A one-dimensional liquid of fermions")
    result = ai_web.verify_document(doc, version_policy="published_only", expected_doi="10.1038/nphys3061")
    provenance = result["provenance"]
    assert (provenance["version"], provenance["version_verified"], provenance["version_evidence"]) == (
        "publishedVersion", True, "publisher_site_in_browser")
    assert provenance["identity_evidence"] == "doi_on_first_page"


@pytest.mark.parametrize("from_url, text", [
    ("", "doi:10.1088/0034-4885/77/12/124401 Ultracold Fermi gases"),  # dropped by hand
    ("https://www.researchgate.net/x.pdf", "doi:10.1088/0034-4885/77/12/124401 Ultracold Fermi gases"),
    ("https://arxiv.org/pdf/1403.2964", "doi:10.1088/0034-4885/77/12/124401 Ultracold Fermi gases"),
    ("https://iopscience.iop.org/article/10.1088/0034-4885/77/12/124401/pdf", "Ultracold Fermi gases, no DOI"),
])
def test_other_deliveries_stay_unverified_versions(from_url, text):
    source = "doi:10.1088/0034-4885/77/12/124401"
    doc = _delivered(source, "https://iopscience.iop.org/article/10.1088/0034-4885/77/12/124401", from_url, text)
    with pytest.raises(ai_web.FetchError, match="not verified as the published version"):
        ai_web.verify_document(doc, version_policy="published_only")


def test_a_delivered_pdf_that_fails_a_check_is_not_asked_for_again(org, monkeypatch):
    doc = _delivered("doi:10.1103/physreva.91.063414", "https://journals.aps.org/pra/abstract/10.1103/PhysRevA.91.063414",
                     "", "DOI: 10.1103/PhysRevA.91.063414 Site-resolved imaging of ytterbium atoms")
    monkeypatch.setattr(fetch_handoff, "delivered", lambda *a: doc)
    text, action = ai_tools.run_agent_tool(org[1]["ws"], folder(""), "fetch_paper", {
        "source": "doi:10.1103/PhysRevA.91.063414", "version_policy": "published_only"})
    assert "not verified as the published version" in text
    assert "do not ask them to download or drop it again" in text and "drop the PDF onto Gamma" not in text
    # Taken from the publisher's site, the same PDF reads under published_only.
    doc["provenance"]["delivered_from"] = "https://journals.aps.org/pra/pdf/10.1103/PhysRevA.91.063414"
    text, action = ai_tools.run_agent_tool(org[1]["ws"], folder(""), "fetch_paper", {
        "source": "doi:10.1103/PhysRevA.91.063414", "version_policy": "published_only"})
    assert not action.get("error") and "Version: publishedVersion (publisher_site_in_browser)" in text


def test_policy_and_identity_do_not_reuse_permissive_cache(upstream):
    source = "https://example.org/paper.pdf"
    ai_web.fetch_document(source)
    before = len(upstream)
    with pytest.raises(ai_web.FetchError, match="not verified as the published version"):
        ai_web.fetch_document(source, version_policy="published_only")
    assert len(upstream) > before
    with pytest.raises(ai_web.FetchError, match="identity mismatch"):
        ai_web.fetch_document(source, expected_title="A completely different paper")


@pytest.mark.parametrize("first", ["corrupt", "html", "http", "wrong_paper", "scan"])
def test_actual_failed_candidate_falls_through_to_next(monkeypatch, first):
    primary, alternate = "https://repo.example.org/bad.pdf", "https://lab.example.org/good.pdf"
    monkeypatch.setattr(pdf, "resolve_source", lambda *a, **kw: {
        "source_url": primary, "alternatives": [{"source_url": alternate, "provenance": {"version": "publishedVersion", "version_verified": True}}]})
    calls = []
    def read(req, timeout=30):
        url = req.full_url
        calls.append(url)
        if url == primary:
            if first == "http":
                raise HTTPError(url, 404, "Gone", {}, None)
            if first == "html":
                return Upstream(url, b"<html>Not a PDF</html>", "text/html")
            data = {"corrupt": b"%PDF-broken", "wrong_paper": _text_pdf(["An unrelated experiment"]),
                    "scan": _text_pdf([""])}[first]
        else:
            data = _text_pdf([TITLE])
        return Upstream(url, data, "application/pdf")
    monkeypatch.setattr(ai_web, "guarded_urlopen", read)
    ai_web.clear_cache()
    result = ai_web.fetch_document("doi:10.1234/a", expected_title=TITLE)
    assert result["url"] == alternate and calls == [primary, alternate]
    assert result["provenance"]["identity_verified"]


def test_actual_arxiv_redirect_cannot_keep_published_claim(monkeypatch):
    source = "https://journal.example.org/paper.pdf"
    monkeypatch.setattr(pdf, "resolve_source", lambda *a, **kw: {
        "source_url": source, "provenance": {"final_url": source, "version": "publishedVersion", "version_verified": True}})
    monkeypatch.setattr(ai_web, "guarded_urlopen", lambda req, timeout=30:
                        Upstream("https://arxiv.org/pdf/1234.56789", PDF, "application/pdf"))
    ai_web.clear_cache()
    with pytest.raises(ai_web.FetchError, match="not verified as the published version"):
        ai_web.fetch_document(source, version_policy="published_only")


def test_continuation_preserves_identity_and_policy(org, upstream, monkeypatch):
    monkeypatch.setattr(pdf, "resolve_source", lambda *a, **kw: {
        "source_url": "https://example.org/paper.pdf", "provenance": {"version": "publishedVersion", "version_verified": True}})
    text, action = ai_tools.run_agent_tool(org[1]["ws"], folder(""), "fetch_paper", {
        "source": "doi:10.1234/a", "expected_title": "Cat qubits are bosonic codes.",
        "version_policy": "published_only", "pdf_chars": 10})
    assert not action.get("error")
    continuation = text.split("more chars — call ")[1]
    assert 'version_policy="published_only"' in continuation
    assert 'expected_title="Cat qubits are bosonic codes."' in continuation


def test_error_handoff_preserves_strict_policy_and_does_not_promise_verified_upload(org, monkeypatch):
    monkeypatch.setattr(ai_tools, "_open_handoff", lambda *a: {"host": "example.org", "id": "handoff"})
    monkeypatch.setattr(ai_web, "fetch_document", lambda *a, **kw: (_ for _ in ()).throw(
        ai_web.FetchError("blocked", wall="denied", open_url="https://example.org/article")))
    text, action = ai_tools.run_agent_tool(org[1]["ws"], folder(""), "fetch_paper", {
        "source": "doi:10.1234/a", "expected_title": TITLE, "version_policy": "published_only"})
    assert action["error"] and action["handoff"]
    assert 'version_policy="published_only"' in text and f'expected_title="{TITLE}"' in text
    assert "without published-version evidence remains unverified" in text


def test_delivered_document_must_satisfy_current_policy(org, monkeypatch):
    monkeypatch.setattr(fetch_handoff, "delivered", lambda *a: document(TITLE, version="submittedVersion"))
    text, action = ai_tools.run_agent_tool(org[1]["ws"], folder(""), "fetch_paper", {
        "source": "doi:10.1234/a", "expected_title": TITLE, "version_policy": "published_only"})
    assert action["error"] and "not verified as the published version" in text


def test_search_web_tool_results_permissions_and_unconfigured_error(org, monkeypatch):
    calls = []
    scope = {**folder(""), "search_user": org[1]["user"]}
    def search(query, limit, *, user):
        assert user == org[1]["user"]
        calls.append(query)
        return [{"title": "A [paper]", "url": "https://lab.example.org/a(b).pdf", "snippet": "Citation", "provider": "brave"}]
    monkeypatch.setattr(web_search, "search_web", search)
    text, action = ai_tools.run_agent_tool(org[1]["ws"], scope, "search_web", {"query": "cooling atoms"})
    assert "untrusted search snippets" in text and "a%28b%29.pdf" in text
    assert action["kind"] == "websearch" and len(calls) == 1
    allowed = ai_tools.agent_tools("folder", {"web_search": False})
    assert not {"search_web", "search_papers"} & {s["name"] for s in allowed}
    _, action = ai_tools.run_agent_tool(org[1]["ws"], folder(""), "search_web", {"query": "x"}, allowed_tools=[s["name"] for s in allowed])
    assert action["error"] and len(calls) == 1
    def unavailable(*args, **kwargs):
        raise web_search.WebSearchError("General web search is not configured.", code="not_configured")
    monkeypatch.setattr(web_search, "search_web", unavailable)
    text, action = ai_tools.run_agent_tool(org[1]["ws"], scope, "search_web", {"query": "cooling"})
    assert action["error"] and "not configured" in text and "0 results" not in text
