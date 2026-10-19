"""Metadata lookup robustness: registry records found via the PDF *text* are
only trusted when their title actually appears in the text (the first DOI on
page 1 can belong to a cited paper), a Crossref bibliographic search rescues
publisher PDFs without a page-1 identifier, and AI-extracted output is
verified — fabricated identifiers are dropped instead of stored.
"""

from conftest import make_page

from gamma.routers import metadata
from gamma.routers.metadata import (
    _find_doi_candidates,
    _pick_crossref_match,
    _title_in_text,
    _verify_ai_meta,
    _years_compatible,
)

REAL_TITLE = "Emergence of room-temperature ferroelectricity at reduced dimensions"
PAGE_TEXT = (
    "SCIENCE | REPORTS\n"
    "FERROELECTRICITY\n"
    "Emergence of room-temperature ferroelec-\ntricity at reduced dimensions\n"
    "D. Lee, H. Lu, Y. Gu, S.-Y. Choi\n"
    "Building on earlier work [1] doi:10.1000/cited123 we study thin films.\n"
    "More text follows here. DOI: 10.1000/real456 is printed in the footer.\n"
)

REAL_META = {
    "title": REAL_TITLE, "authors": ["D. Lee", "H. Lu"], "year": "2015",
    "venue": "Science", "volume": "349", "pages": "1314-1317",
    "doi": "10.1000/real456", "arxiv_id": "", "source": "doi",
}
CITED_META = {
    "title": "Some Other Cited Paper About Films", "authors": ["A. Nother"],
    "year": "2011", "venue": "Journal B", "volume": "1", "pages": "1-4",
    "doi": "10.1000/cited123", "arxiv_id": "", "source": "doi",
}


def _paper_page(guest, text, monkeypatch, ai_enabled=False):
    page = make_page(guest, "paper.pdf", properties={"doc_id": "d" * 24})
    monkeypatch.setattr(metadata, "_pdf_excerpt", lambda u, d, limit: (text, None, len(text)))
    monkeypatch.setattr(metadata, "_ensure_indexed", lambda u, d: True)
    monkeypatch.setattr(metadata, "ai_runtime", lambda u: {"enabled": ai_enabled})
    return page


# --- pure helpers ------------------------------------------------------------

def test_doi_candidates_finds_every_doi_in_reading_order():
    cands = _find_doi_candidates("", PAGE_TEXT)
    assert cands.index("10.1000/cited123") < cands.index("10.1000/real456")


def test_title_in_text_survives_extraction_artifacts():
    # Line-break hyphenation, case, and punctuation drift don't break the match
    assert _title_in_text(REAL_TITLE, PAGE_TEXT)
    assert _title_in_text("EMERGENCE of Room-Temperature Ferroelectricity at reduced dimensions!", PAGE_TEXT)
    assert not _title_in_text("A Completely Different Paper Title", PAGE_TEXT)
    assert not _title_in_text("Errata", "Errata for volume 12")  # too short to be distinctive


def test_years_compatible():
    assert _years_compatible("2015", "2016")      # preprint/publication skew
    assert _years_compatible("", "2015")          # unknown year never rejects
    assert not _years_compatible("2015", "2019")


def test_pick_crossref_match_requires_strong_evidence():
    assert _pick_crossref_match([REAL_META], PAGE_TEXT) == REAL_META      # title in text
    assert _pick_crossref_match([CITED_META], PAGE_TEXT) is None          # title not in text
    ai_meta = {"title": REAL_TITLE, "year": "2015"}
    assert _pick_crossref_match([REAL_META], "", ai_meta) == REAL_META    # ≈identical AI title
    assert _pick_crossref_match([CITED_META], "", ai_meta) is None
    wrong_year = dict(REAL_META, year="2019")
    assert _pick_crossref_match([wrong_year], "", ai_meta) is None


# --- _verify_ai_meta ---------------------------------------------------------

def test_verify_drops_fabricated_identifiers(monkeypatch):
    monkeypatch.setattr(metadata, "_fetch_arxiv", lambda i: None)
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (None, ""))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    ai = {"title": "Ferroelectricity paper", "authors": ["X"], "year": "2015",
          "venue": "Science", "volume": "348", "pages": "1235-1239",
          "doi": "10.1126/science.fake999", "arxiv_id": "", "source": "ai"}
    meta, bib = _verify_ai_meta(ai, "text without that doi", "")
    assert meta["doi"] == ""      # resolves nowhere, not in the PDF → dropped
    assert meta["source"] == "ai"


def test_verify_keeps_unresolvable_doi_that_is_in_the_pdf(monkeypatch):
    # doi.org may just be down — a DOI that literally occurs in the text stays
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (None, ""))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    ai = {"title": "T " * 10, "authors": [], "year": "", "venue": "", "volume": "",
          "pages": "", "doi": "10.1000/real456", "arxiv_id": "", "source": "ai"}
    meta, _ = _verify_ai_meta(ai, PAGE_TEXT, "")
    assert meta["doi"] == "10.1000/real456"


def test_verify_upgrades_via_resolvable_doi(monkeypatch):
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (REAL_META, "@article{x}") if d == "10.1000/real456" else (None, ""))
    ai = {"title": "Wrong Title", "authors": ["Wrong Author"], "year": "2013",
          "venue": "", "volume": "", "pages": "", "doi": "10.1000/real456",
          "arxiv_id": "", "source": "ai"}
    meta, bib = _verify_ai_meta(ai, "", "")
    assert meta == REAL_META and bib == "@article{x}"


def test_verify_upgrades_via_crossref_title_search(monkeypatch):
    monkeypatch.setattr(metadata, "_fetch_arxiv", lambda i: None)
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (None, ""))
    crossref_hit = dict(REAL_META, source="crossref")
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [crossref_hit])
    ai = {"title": REAL_TITLE, "authors": ["D Lee"], "year": "2015", "venue": "",
          "volume": "", "pages": "", "doi": "", "arxiv_id": "", "source": "ai"}
    meta, _ = _verify_ai_meta(ai, "", "")
    assert meta["source"] == "crossref" and meta["doi"] == "10.1000/real456"


def test_ai_extract_reports_document_kind(monkeypatch):
    monkeypatch.setattr(metadata, "_resolve_model", lambda rt, m: "m")
    monkeypatch.setattr(metadata, "_call_ai",
                        lambda *a, **kw: '{"title": "QM Lecture 5", "kind": "document"}')
    assert metadata._ai_extract_meta("text", "", "", {"enabled": True})["kind"] == "document"
    # Unknown kinds are read from the record's shape: a journal article, the
    # safe (warned) default
    monkeypatch.setattr(metadata, "_call_ai",
                        lambda *a, **kw: '{"title": "T", "kind": "mixtape"}')
    assert metadata._ai_extract_meta("text", "", "", {"enabled": True})["kind"] == "journalArticle"


def test_course_notes_keep_their_kind(guest, monkeypatch):
    """A DOI-less lecture-notes PDF: the AI classifies it as a document; the
    record stays source "ai" with kind "document" (the UI shows no red warning
    for it) and the status endpoint reports the kind."""
    page = _paper_page(guest, "Lecture 5: perturbation theory. Problem set due Friday.",
                       monkeypatch, ai_enabled=True)
    monkeypatch.setattr(metadata, "_resolve_model", lambda rt, m: "m")
    monkeypatch.setattr(metadata, "_call_ai",
                        lambda *a, **kw: '{"title": "Lecture 5: perturbation theory", '
                                         '"authors": ["Prof X"], "kind": "document"}')
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    monkeypatch.setattr(metadata, "_book_search", lambda t, a="": [])
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (None, ""))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "ai"
    assert r.json()["meta"]["kind"] == "document" and r.json()["meta"]["unverified"] is False
    st = guest.get("/api/metadata/status").json()["papers"]
    entry = next(p for p in st if p["id"] == page["id"])
    assert entry["meta_source"] == "ai" and entry["meta_kind"] == "document"


# --- the full fetch chain ----------------------------------------------------

def test_cited_doi_on_page_one_is_not_trusted(guest, monkeypatch):
    """The first DOI in the text belongs to a citation; the paper's own DOI is
    further down. The candidate whose registrar title appears in the text wins."""
    page = _paper_page(guest, PAGE_TEXT, monkeypatch)
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: {"10.1000/cited123": (CITED_META, "@article{cited}"),
                                   "10.1000/real456": (REAL_META, "@article{real}")}.get(d, (None, "")))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["doi"] == "10.1000/real456"


def test_unconfirmed_doi_still_beats_nothing(guest, monkeypatch):
    """Only a cited paper's DOI resolves and nothing confirms — the resolved
    record is still kept (old fallback behavior) rather than failing."""
    text = "An intro citing earlier work doi:10.1000/cited123 and nothing else."
    page = _paper_page(guest, text, monkeypatch)
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (CITED_META, "@x") if d == "10.1000/cited123" else (None, ""))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["title"] == CITED_META["title"]


def test_crossref_head_search_rescues_paper_without_identifiers(guest, monkeypatch):
    """Publisher PDF with no DOI/arXiv id in the head: the Crossref
    bibliographic search on the text head finds the record, accepted because
    its exact title is in the PDF — no AI involved."""
    text = PAGE_TEXT.replace("doi:10.1000/cited123", "").replace("DOI: 10.1000/real456", "")
    page = _paper_page(guest, text, monkeypatch)
    queries = []

    def fake_search(q, rows=5):
        queries.append(q)
        return [dict(REAL_META, source="crossref")]
    monkeypatch.setattr(metadata, "_crossref_search", fake_search)
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (REAL_META, "@article{real}") if d == "10.1000/real456" else (None, ""))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["title"] == REAL_TITLE
    assert body["source"] == "doi"
    assert queries and "emergence of room temperature" in queries[0]


def test_arxiv_id_from_text_needs_title_match(guest, monkeypatch):
    """An arXiv id found in the *text* whose record title isn't in the PDF is
    not trusted outright — a text-confirmed DOI further down wins."""
    text = ("See also arXiv:2601.99999 for related work.\n" + PAGE_TEXT)
    page = _paper_page(guest, text, monkeypatch)
    other = {"title": "A Related But Different Preprint Entirely", "authors": ["Z"],
             "year": "2026", "venue": "arXiv:2601.99999", "volume": "", "pages": "",
             "doi": "", "arxiv_id": "2601.99999", "source": "arxiv"}
    monkeypatch.setattr(metadata, "_fetch_arxiv", lambda i: other)
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (REAL_META, "@article{real}") if d == "10.1000/real456" else (None, ""))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["title"] == REAL_TITLE


ZENO_TITLE = "Deterministic generation of multiparticle entanglement by quantum Zeno dynamics"
ZENO_META = {
    "title": ZENO_TITLE, "authors": ["Giovanni Barontini", "Jakob Reichel"],
    "year": "2015", "venue": "Science", "volume": "349", "pages": "1317-1321",
    "doi": "10.1000/zeno754", "arxiv_id": "", "source": "doi",
}


def test_issue_clipped_pdf_finds_trailer_doi(guest, monkeypatch):
    """The real-world Science failure: page 1 is the *previous* article's tail
    (its trailer DOI included), this paper's title sits deep in the head, and
    its own DOI is printed only in the end-of-article trailer on the last
    page. The last-page scan + title confirmation must find the right record."""
    head = ("references of the previous article " * 150
            + " 10.1000/cited123 end of previous article trailer\n"
            + "QUANTUM OPTICS\nDeterministic generation of\nmultiparticle "
            + "entanglement by\nquantum Zeno dynamics\nGiovanni Barontini\n"
            + "abstract and body text " * 100)
    tail = "last page text, article ends here. 10.1000/zeno754 more trailer."
    page = make_page(guest, "paper.pdf", properties={"doc_id": "d" * 24})

    def fake_excerpt(u, d, limit, offset=0, start_page=1, with_pages=False):
        if start_page > 1:
            return (tail, None, len(tail))
        return (head, len(head), len(head))  # truncated: more pages follow
    monkeypatch.setattr(metadata, "_pdf_excerpt", fake_excerpt)
    monkeypatch.setattr(metadata, "_pdf_path", lambda u, d: "fake.pdf")
    monkeypatch.setattr(metadata, "_page_count", lambda p: 6)
    monkeypatch.setattr(metadata, "_ensure_indexed", lambda u, d: True)
    monkeypatch.setattr(metadata, "ai_runtime", lambda u: {"enabled": False})
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: {"10.1000/cited123": (CITED_META, "@article{cited}"),
                                   "10.1000/zeno754": (ZENO_META, "@article{zeno}")}.get(d, (None, "")))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["doi"] == "10.1000/zeno754"
    assert r.json()["meta"]["title"] == ZENO_TITLE


def test_page_title_feeds_crossref_query(guest, monkeypatch):
    """A page titled with the paper name (renamed by hand, or a title-like
    filename) becomes the first Crossref query when no identifier confirms."""
    text = "junk from another article " * 80 + ZENO_TITLE + " abstract follows " * 50
    page = make_page(guest, ZENO_TITLE, properties={"doc_id": "d" * 24})
    monkeypatch.setattr(metadata, "_pdf_excerpt", lambda u, d, limit: (text, None, len(text)))
    monkeypatch.setattr(metadata, "_ensure_indexed", lambda u, d: True)
    monkeypatch.setattr(metadata, "ai_runtime", lambda u: {"enabled": False})
    queries = []

    def fake_search(q, rows=5):
        queries.append(q)
        return [dict(ZENO_META, source="crossref")] if q == ZENO_TITLE else []
    monkeypatch.setattr(metadata, "_crossref_search", fake_search)
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (ZENO_META, "@article{zeno}") if d == "10.1000/zeno754" else (None, ""))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert queries[0] == ZENO_TITLE
    assert r.json()["meta"]["doi"] == "10.1000/zeno754"


def test_title_match_reaches_past_6000_chars():
    """Titles that start beyond the old 6000-char window still confirm."""
    text = "x " * 3500 + ZENO_TITLE + " and then the abstract."
    assert _title_in_text(ZENO_TITLE, text)


def _clipped_page(guest, monkeypatch, web_meta, text=None, crossref=None):
    """A page a clip made: what its web page said in ``web_meta``, a PDF
    whose head is ``text`` (None: a page with its web source, no PDF), and
    Crossref answering ``crossref(query)`` (nothing by default); returns
    the page and the queries Crossref was asked."""
    props = {"web_url": "https://repo.example/item", "web_meta": web_meta}
    if text is not None:
        props["doc_id"] = "d" * 24
        monkeypatch.setattr(metadata, "_pdf_excerpt", lambda u, d, limit: (text, None, len(text)))
        monkeypatch.setattr(metadata, "_ensure_indexed", lambda u, d: True)
    monkeypatch.setattr(metadata, "ai_runtime", lambda u: {"enabled": False})
    queries = []

    def search(q, rows=5):
        queries.append(q)
        return crossref(q) if crossref else []
    monkeypatch.setattr(metadata, "_crossref_search", search)
    return make_page(guest, "Saved page", properties=props), queries


def test_the_pages_tag_doi_is_trusted_by_every_lookup(guest, monkeypatch):
    """The DOI the publisher page's tags carried is kept on the page
    (web_meta), so the lookup the app starts when the page is opened trusts
    it as the clip's own did — no title match needed (the text here holds
    neither the DOI nor the title)."""
    page, _ = _clipped_page(guest, monkeypatch, {"doi": "10.1000/zeno754"},
                            text="scanned garbage with no identifiers at all")
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (ZENO_META, "@article{zeno}") if d == "10.1000/zeno754" else (None, ""))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["title"] == ZENO_TITLE and r.json()["source"] == "doi"
    assert r.json()["meta"]["unverified"] is False


def test_a_doi_from_the_pages_text_is_confirmed_by_the_pdf(guest, monkeypatch):
    """A DOI the web page only printed in its text is tried first, and
    trusted only when its title is in the PDF."""
    page, _ = _clipped_page(guest, monkeypatch, {"text_doi": "10.1000/real456"},
                            text=PAGE_TEXT.replace("DOI: 10.1000/real456", ""))
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: {"10.1000/cited123": (CITED_META, "@x"),
                                   "10.1000/real456": (REAL_META, "@article{real}")}.get(d, (None, "")))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["doi"] == "10.1000/real456" and r.json()["meta"]["unverified"] is False


def test_a_doi_from_the_pages_text_is_unconfirmed_without_its_title(guest, monkeypatch):
    """The page merely cited that paper: its record is only a fallback,
    flagged — with a PDF that does not carry its title, and on a page with
    its web source and no PDF to check against."""
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (CITED_META, "@x") if d == "10.1000/cited123" else (None, ""))
    for text in ("A blog post about films, citing nothing it prints.", None):
        page, _ = _clipped_page(guest, monkeypatch, {"text_doi": "10.1000/cited123"}, text=text)
        r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
        assert r.status_code == 200, r.text
        assert r.json()["meta"]["doi"] == "10.1000/cited123"
        assert r.json()["meta"]["unverified"] is True, text


THESIS_RECORD = {
    "title": "Trapped Ions in Cavities", "authors": ["Ada Lovelace"], "year": "2019",
    "publisher": "Example University", "kind": "thesis",
}


def test_the_pages_record_stands_when_no_registry_knows_the_work(guest, monkeypatch):
    """A thesis in a repository: no identifier, nothing in Crossref. The
    page's own citation record is the record — typed, confirmed, no AI —
    and its title and author were Crossref's first query."""
    page, queries = _clipped_page(guest, monkeypatch, THESIS_RECORD, text="A thesis without identifiers. " * 20)
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert r.json()["source"] == "page" and meta["kind"] == "thesis" and meta["unverified"] is False
    assert meta["title"] == "Trapped Ions in Cavities" and meta["publisher"] == "Example University"
    assert r.json()["bibtex"].startswith("@phdthesis{") and "school = {Example University}" in r.json()["bibtex"]
    assert queries[0] == "Trapped Ions in Cavities Ada Lovelace"
    # A page with its web source and no PDF gets it too.
    page, _ = _clipped_page(guest, monkeypatch, THESIS_RECORD)
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200 and r.json()["source"] == "page"


def test_the_pages_record_beats_an_unconfirmed_doi(guest, monkeypatch):
    """The only DOI in the PDF is a cited paper's: before, its record was
    stored (flagged); the page's own record wins now."""
    page, _ = _clipped_page(guest, monkeypatch, THESIS_RECORD,
                            text="An intro citing earlier work doi:10.1000/cited123 and nothing else.")
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (CITED_META, "@x") if d == "10.1000/cited123" else (None, ""))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "page" and r.json()["meta"]["title"] == THESIS_RECORD["title"]


def test_a_registry_record_beats_the_pages_record(guest, monkeypatch):
    """Crossref knows the work: its record, found with the page's title and
    author and accepted on a near-identical title and the same year (there
    is no PDF text to find it in), replaces the page's."""
    record = {"title": "Deterministic generation of multiparticle entanglement by quantum Zeno dynamics.",
              "authors": ["G. Barontini"], "year": "2015", "venue": "Science"}
    page, queries = _clipped_page(guest, monkeypatch, record,
                                  crossref=lambda q: [dict(ZENO_META, source="crossref")])
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (ZENO_META, "@article{zeno}") if d == "10.1000/zeno754" else (None, ""))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "doi" and r.json()["meta"]["doi"] == "10.1000/zeno754"
    assert queries == [f"{record['title']} G. Barontini"]


def test_the_pdfs_largest_type_is_asked_before_the_text_head(guest, monkeypatch):
    """The first page's largest-type lines (pdf_text.title_guess) are the
    Crossref query after the page's record and before the page title and
    the text head; a query asked once is not asked again."""
    text = "Downloaded from https://journal.example on 1 May 2024 " * 30 + ZENO_TITLE
    page = make_page(guest, ZENO_TITLE, properties={"doc_id": "d" * 24})
    monkeypatch.setattr(metadata, "_pdf_excerpt", lambda u, d, limit: (text, None, len(text)))
    monkeypatch.setattr(metadata, "_ensure_indexed", lambda u, d: True)
    monkeypatch.setattr(metadata, "ai_runtime", lambda u: {"enabled": False})
    monkeypatch.setattr(metadata, "_pdf_path", lambda ws, d: "paper.pdf")
    monkeypatch.setattr(metadata, "_title_guess", lambda path: ZENO_TITLE.upper())
    queries = []
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: queries.append(q) or [])
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 404
    assert queries[0] == ZENO_TITLE.upper() and len(queries) == 2  # the page title is the same words
    assert queries[1].startswith("downloaded from https journal example")


def test_web_url_supplies_trusted_doi(guest, monkeypatch):
    """The publisher page a clip came from (web_url) is scanned for
    identifiers too, with URL-level trust."""
    page = make_page(guest, "p", properties={
        "doc_id": "d" * 24, "web_url": "https://doi.org/10.1000/zeno754"})
    monkeypatch.setattr(metadata, "_pdf_excerpt",
                        lambda u, d, limit: ("no identifiers here", None, 19))
    monkeypatch.setattr(metadata, "_ensure_indexed", lambda u, d: True)
    monkeypatch.setattr(metadata, "ai_runtime", lambda u: {"enabled": False})
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (ZENO_META, "@article{zeno}") if d == "10.1000/zeno754" else (None, ""))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["doi"] == "10.1000/zeno754"


def test_arxiv_id_from_source_url_is_trusted(guest, monkeypatch):
    """URL-derived ids stay authoritative even when the text is garbage (a
    scanned PDF must not lose its arXiv record to a failed title match)."""
    page = make_page(guest, "paper", properties={
        "source_url": "https://arxiv.org/abs/2601.00001"})
    meta = {"title": "Scanned Preprint", "authors": ["A"], "year": "2026",
            "venue": "arXiv:2601.00001", "volume": "", "pages": "", "doi": "",
            "arxiv_id": "2601.00001", "source": "arxiv"}
    monkeypatch.setattr(metadata, "_fetch_arxiv", lambda i: meta)
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "arxiv"


def test_lookup_switch_fails_fast(monkeypatch):
    """GAMMA_METADATA_LOOKUP=off (the browser suite): every registry call
    fails before it connects, and the lookups find nothing."""
    monkeypatch.setattr(metadata, "METADATA_LOOKUP", False)
    try:
        metadata._http_get("https://api.crossref.org/works")
        raise AssertionError("the switched-off lookup went out")
    except OSError as e:
        assert "GAMMA_METADATA_LOOKUP" in str(e)
    assert metadata._crossref_search(REAL_TITLE) == []
    assert metadata._fetch_arxiv("2601.00001") is None
