"""PDF alternatives, version policies and honest provenance; no live network."""

import json
from urllib.error import HTTPError

import pytest
from fastapi import HTTPException

import gamma.routers.pdf as pdf_mod
from test_pdf_proxy import FakeUpstream


DOI = "10.5555/cooling"
SOURCE = f"https://doi.org/{DOI}"


def _location(url, version="publishedVersion"):
    return {"url_for_pdf": url, "version": version}


def _transport(monkeypatch, locations, responses):
    calls = []

    def fetch(req, timeout=30):
        url = req.full_url
        calls.append((url, timeout))
        if url == SOURCE:
            raise HTTPError(url, 403, "publisher browser check", {}, None)
        if url.startswith("https://api.unpaywall.org/"):
            return FakeUpstream(url, json.dumps({"oa_locations": locations}).encode(),
                                "application/json")
        response = responses[url]
        if isinstance(response, Exception):
            raise response
        return response() if callable(response) else response

    monkeypatch.setattr(pdf_mod, "guarded_urlopen", fetch)
    return calls


def test_stale_best_location_continues_to_accessible_copy(monkeypatch):
    stale, good = "https://lab.example/stale.pdf", "https://repo.example/paper.pdf"
    final = "https://repo.example/download?token=temporary"
    calls = _transport(monkeypatch, [_location(stale), _location(good, "acceptedVersion")], {
        stale: HTTPError(stale, 404, "gone", {}, None),
        good: FakeUpstream(final),
    })
    trace = {}
    result = pdf_mod.resolve_source(SOURCE, trace=trace)
    assert [url for url, _ in calls][-2:] == [stale, good]
    assert result["source_url"] == good  # Keep cache identity stable across redirects.
    assert "accepted manuscript" in result["note"]
    assert result["provenance"] == {
        "source_kind": "unpaywall", "source_url": good, "final_url": final,
        "version": "acceptedVersion", "doi": DOI, "identity_verified": False,
        "version_verified": True, "version_evidence": "unpaywall",
    }
    assert [attempt["outcome"] for attempt in trace["oa_attempts"]] == ["failed", "pdf"]


def test_non_pdf_location_is_not_returned_as_success(monkeypatch):
    wall, good = "https://lab.example/wall.pdf", "https://repo.example/paper.pdf"
    _transport(monkeypatch, [_location(wall), _location(good)], {
        wall: FakeUpstream(wall, b"<html>Please sign in</html>", "text/html"),
        good: FakeUpstream(good),
    })
    trace = {}
    assert pdf_mod.resolve_source(SOURCE, trace=trace)["source_url"] == good
    assert trace["oa_attempts"][0]["outcome"] == "not_pdf"


def test_all_failed_candidates_raise_instead_of_returning_broken_url(monkeypatch):
    wall = "https://lab.example/wall.pdf"
    _transport(monkeypatch, [_location(wall)], {
        wall: FakeUpstream(wall, b"<html>Not available</html>", "text/html"),
    })
    with pytest.raises(HTTPException, match="No accessible open-access copy"):
        pdf_mod.resolve_source(SOURCE)


def test_candidates_rank_deduplicate_and_ignore_non_http(monkeypatch):
    preprint, accepted, published = [f"https://repo.example/{name}.pdf"
                                    for name in ("preprint", "accepted", "published")]
    locations = [_location(preprint, "submittedVersion"),
                 _location(accepted, "acceptedVersion"), _location(published),
                 _location(published + "#page=2"), _location("file:///tmp/paper.pdf"), None]
    _transport(monkeypatch, locations, {})
    candidates = pdf_mod._open_access_pdf_candidates_for_doi(DOI)
    assert [candidate["url"] for candidate in candidates] == [published, accepted, preprint]


def test_published_only_skips_manuscripts_and_unknown_versions(monkeypatch):
    published, accepted, preprint, unknown = [f"https://repo.example/{name}.pdf"
                                            for name in ("published", "accepted", "preprint", "unknown")]
    calls = _transport(monkeypatch, [_location(preprint, "submittedVersion"),
                                    _location(accepted, "acceptedVersion"),
                                    _location(unknown, ""), _location(published)], {
        published: HTTPError(published, 404, "gone", {}, None),
    })
    with pytest.raises(HTTPException, match="published_only"):
        pdf_mod.resolve_source(SOURCE, version_policy="published_only")
    assert [url for url, _ in calls if "repo.example" in url] == [published]


@pytest.mark.parametrize("source", ["arXiv:2301.12345", "10.48550/arXiv.2301.12345",
                                    "https://export.arxiv.org/pdf/2301.12345"])
def test_published_only_rejects_arxiv_before_fetch(monkeypatch, source):
    calls = _transport(monkeypatch, [], {})
    with pytest.raises(HTTPException, match="published_only"):
        pdf_mod.resolve_source(source, version_policy="published_only")
    assert calls == []


def test_unknown_direct_pdf_does_not_claim_verified_version(monkeypatch):
    url = "https://lab.example/cooling.pdf"
    _transport(monkeypatch, [], {url: FakeUpstream(url)})
    result = pdf_mod.resolve_source(url, version_policy="published_only")
    assert result["provenance"]["version"] == ""
    assert result["provenance"]["version_verified"] is False
    assert result["provenance"]["identity_verified"] is False


def test_published_only_checks_successful_publisher_pdf_against_registry(monkeypatch):
    pdf_url = "https://publisher.example/paper.pdf"
    calls = []

    def fetch(req, timeout=30):
        url = req.full_url
        calls.append(url)
        if url == SOURCE:
            return FakeUpstream("https://publisher.example/article",
                                f'<meta name="citation_pdf_url" content="{pdf_url}">'.encode(), "text/html")
        if url == pdf_url:
            return FakeUpstream(pdf_url)
        assert url.startswith("https://api.unpaywall.org/")
        return FakeUpstream(url, json.dumps({"oa_locations": [_location(pdf_url)]}).encode(), "application/json")

    monkeypatch.setattr(pdf_mod, "guarded_urlopen", fetch)
    result = pdf_mod.resolve_source(SOURCE, version_policy="published_only")
    assert result["source_url"] == pdf_url
    assert result["provenance"]["version"] == "publishedVersion"
    assert result["provenance"]["version_evidence"] == "unpaywall"
    assert result["referer"] == "https://publisher.example/article"
    assert calls.count(pdf_url) == 1


def test_published_only_does_not_accept_unverified_doi_pdf(monkeypatch):
    pdf_url = "https://lab.example/paper.pdf"

    def fetch(req, timeout=30):
        if req.full_url == SOURCE:
            return FakeUpstream(pdf_url)
        assert req.full_url.startswith("https://api.unpaywall.org/")
        return FakeUpstream(req.full_url, json.dumps({"oa_locations": [
            _location(pdf_url, "submittedVersion")]}).encode(), "application/json")

    monkeypatch.setattr(pdf_mod, "guarded_urlopen", fetch)
    with pytest.raises(HTTPException, match="published_only"):
        pdf_mod.resolve_source(SOURCE, version_policy="published_only")


def test_oa_disabled_never_queries_registry(monkeypatch):
    calls = _transport(monkeypatch, [], {})
    with pytest.raises(HTTPException, match="fallback is disabled"):
        pdf_mod.resolve_source(SOURCE, allow_oa=False)
    assert calls == [(SOURCE, 20)]


def test_candidate_attempt_count_is_bounded(monkeypatch):
    urls = [f"https://repo.example/{i}.pdf" for i in range(20)]
    calls = _transport(monkeypatch, [_location(url) for url in urls], {
        url: HTTPError(url, 404, "gone", {}, None) for url in urls
    })
    with pytest.raises(HTTPException):
        pdf_mod.resolve_source(SOURCE)
    attempts = [(url, timeout) for url, timeout in calls if "repo.example" in url]
    assert len(attempts) == pdf_mod.OA_MAX_CANDIDATES
    assert all(0 < timeout <= 10 for _, timeout in attempts)


def test_successful_probe_preserves_bounded_unprobed_alternatives(monkeypatch):
    urls = [f"https://repo.example/{i}.pdf" for i in range(10)]
    calls = _transport(monkeypatch, [_location(url) for url in urls], {urls[0]: FakeUpstream(urls[0])})
    result = pdf_mod.resolve_source(SOURCE)
    assert result["source_url"] == urls[0]
    assert [item["source_url"] for item in result["alternatives"]] == urls[1:pdf_mod.OA_MAX_CANDIDATES]
    assert [url for url, _ in calls if "repo.example" in url] == [urls[0]]
    assert all(item["provenance"]["identity_verified"] is False for item in result["alternatives"])
    assert all(item["provenance"]["final_url"] == "" for item in result["alternatives"])


def test_alternatives_follow_version_policy(monkeypatch):
    primary, alternative, preprint = [f"https://repo.example/{name}.pdf"
                                     for name in ("primary", "alternative", "preprint")]
    _transport(monkeypatch, [_location(preprint, "submittedVersion"),
                            _location(primary), _location(alternative)], {primary: FakeUpstream(primary)})
    result = pdf_mod.resolve_source(SOURCE, version_policy="published_only")
    assert [item["source_url"] for item in result["alternatives"]] == [alternative]
    assert result["alternatives"][0]["provenance"]["version"] == "publishedVersion"


def test_oa_deadline_stops_new_attempts_and_caps_timeout(monkeypatch):
    urls = [f"https://repo.example/{i}.pdf" for i in range(5)]
    now = [100.0]
    monkeypatch.setattr(pdf_mod.time, "monotonic", lambda: now[0])

    def slow_wall():
        now[0] += 13
        return FakeUpstream("https://repo.example/wall", b"not a PDF", "text/html")

    calls = _transport(monkeypatch, [_location(url) for url in urls], {
        url: slow_wall for url in urls
    })
    with pytest.raises(HTTPException):
        pdf_mod.resolve_source(SOURCE)
    attempts = [(url, timeout) for url, timeout in calls if "repo.example" in url]
    assert [timeout for _, timeout in attempts] == [10, 10, 4]


def test_resolve_endpoint_validates_version_policy(guest):
    response = guest.post("/api/resolve-pdf", json={"source_url": SOURCE, "version_policy": "anything"})
    assert response.status_code == 422


@pytest.mark.parametrize("version, verified, policy, status", [
    ("", False, "published_only", 400),
    ("publishedVersion", False, "published_only", 400),
    ("submittedVersion", True, "published_only", 400),
    ("publishedVersion", True, "published_only", 200),
    ("", False, "allow_preprint", 200),
])
def test_resolve_endpoint_enforces_verified_published_version(guest, monkeypatch, version, verified, policy, status):
    resolved = {"source_url": "https://lab.example/paper.pdf",
                "provenance": {"version": version, "version_verified": verified}}
    calls = []

    def resolve(source, allow_oa, *, version_policy):
        calls.append((source, allow_oa, version_policy))
        return resolved

    monkeypatch.setattr(pdf_mod, "resolve_source", resolve)
    response = guest.post("/api/resolve-pdf", json={"source_url": SOURCE, "version_policy": policy})
    assert response.status_code == status, response.text
    assert calls == [(SOURCE, True, policy)]
    if status == 400:
        assert "published version could not be confirmed" in response.json()["detail"]
    else:
        assert response.json() == resolved
