"""Metadata lookup robustness: registry records found via the PDF *text* are
only trusted when the PDF is their title page (the first DOI on page 1 can
belong to a cited paper, a short registry title can be a phrase of a longer
one); records the text merely names go to the AI, which picks the one that
is this document, and stand flagged without it. A Crossref bibliographic
search rescues publisher PDFs without a page-1 identifier, AI-extracted
output is verified — fabricated or cited identifiers are dropped instead of
stored — and every record says what tied it to the document (meta.match).
"""

import json

from conftest import make_page

from gamma.ai_client import AllowanceExhausted
from gamma.routers import metadata
from gamma.routers.metadata import (
    MATCH_PROMPT,
    _Document,
    _answer,
    _picked,
    _find_doi_candidates,
    _same_work,
    _tie,
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


def test_title_in_text_matches_whole_words_only():
    assert not _title_in_text("Ion trapping in cavities", "Fusion trapping in cavitiesx and more")
    assert _title_in_text("Ion trapping in cavities", "We study ion trapping in cavities here")
    # A subtitle as short as a letter is no evidence of anything.
    assert not _title_in_text("Quasi-phase-matching: a", QPM_TEXT)


def test_years_compatible():
    assert _years_compatible("2015", "2016")      # preprint/publication skew
    assert _years_compatible("", "2015")          # unknown year never rejects
    assert not _years_compatible("2015", "2019")


def test_tie_needs_the_title_page():
    doc = _Document(PAGE_TEXT)
    assert _tie(doc, REAL_META) == "title"                 # its title as whole lines, Lee named
    assert _tie(doc, CITED_META) == ""                     # not in the text at all
    assert _tie(doc, dict(REAL_META, authors=["Q. Nobody"])) == "title_only"  # no author by it
    # A registry title that is only a phrase of the document's own
    assert _tie(_Document(QPM_TEXT), CHAPTER) == "phrase"
    # The first page's largest type counts as the title too.
    assert _tie(_Document("one line of body text naming Lee", guess=REAL_TITLE), REAL_META) == "title"


def test_same_work_needs_near_identical_titles_and_years():
    reading = {"title": REAL_TITLE, "year": "2015"}
    assert _same_work(REAL_META, reading)
    assert not _same_work(CITED_META, reading)
    assert not _same_work(dict(REAL_META, year="2019"), reading)
    assert not _same_work(REAL_META, None)


# --- _verify_ai_meta ---------------------------------------------------------

def test_verify_drops_fabricated_identifiers(monkeypatch):
    monkeypatch.setattr(metadata, "_fetch_arxiv", lambda i: None)
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (None, ""))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    ai = {"title": "Ferroelectricity paper", "authors": ["X"], "year": "2015",
          "venue": "Science", "volume": "348", "pages": "1235-1239",
          "doi": "10.1126/science.fake999", "arxiv_id": "", "source": "ai"}
    meta, bib, match = _verify_ai_meta(ai, "text without that doi", "")
    assert meta["doi"] == ""      # resolves nowhere, not in the PDF → dropped
    assert meta["source"] == "ai" and match == "ai_reading"


def test_verify_keeps_unresolvable_doi_that_is_in_the_pdf(monkeypatch):
    # doi.org may just be down — a DOI that literally occurs in the text stays
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (None, ""))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    ai = {"title": "T " * 10, "authors": [], "year": "", "venue": "", "volume": "",
          "pages": "", "doi": "10.1000/real456", "arxiv_id": "", "source": "ai"}
    meta, _, _ = _verify_ai_meta(ai, PAGE_TEXT, "")
    assert meta["doi"] == "10.1000/real456"


def test_verify_upgrades_via_a_doi_naming_the_work_it_read(monkeypatch):
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (REAL_META, "@article{x}") if d == "10.1000/real456" else (None, ""))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    ai = {"title": REAL_TITLE, "authors": ["D. Lee"], "year": "2015",
          "venue": "", "volume": "", "pages": "", "doi": "10.1000/real456",
          "arxiv_id": "", "source": "ai"}
    meta, bib, match = _verify_ai_meta(dict(ai), "", "")
    assert meta == REAL_META and bib == "@article{x}" and match == "ai"
    # The DOI names another work than the one the AI read (one the paper
    # cites, a model's guess): dropped, the reading stands.
    meta, bib, match = _verify_ai_meta(dict(ai, title="A Different Work Entirely By Others"), "", "")
    assert meta["source"] == "ai" and meta["doi"] == "" and match == "ai_reading"


def test_verify_upgrades_via_crossref_title_search(monkeypatch):
    monkeypatch.setattr(metadata, "_fetch_arxiv", lambda i: None)
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (None, ""))
    crossref_hit = dict(REAL_META, source="crossref")
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [crossref_hit])
    ai = {"title": REAL_TITLE, "authors": ["D Lee"], "year": "2015", "venue": "",
          "volume": "", "pages": "", "doi": "", "arxiv_id": "", "source": "ai"}
    meta, _, match = _verify_ai_meta(ai, "", "")
    assert meta["source"] == "crossref" and meta["doi"] == "10.1000/real456" and match == "ai"


def test_verify_takes_no_hit_whose_title_is_a_phrase_of_the_one_read(monkeypatch):
    """The AI read the document's title; a Crossref hit whose title is only a
    phrase of it (a textbook chapter on the topic) is not that work."""
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (None, ""))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [dict(CHAPTER, source="crossref")])
    monkeypatch.setattr(metadata, "_book_search", lambda t, a="": [])
    meta, _, match = _verify_ai_meta(dict(QPM_READING), QPM_TEXT, "")
    assert meta["title"] == QPM_READING["title"] and match == "ai_reading"


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
    assert r.json()["meta"]["match"] == "title" and r.json()["meta"]["unverified"] is False


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
    assert r.json()["meta"]["match"] == "id_only" and r.json()["meta"]["unverified"] is True


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
    assert r.json()["meta"]["unverified"] is False and r.json()["meta"]["match"] == "source"


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
    assert r.json()["meta"]["match"] == "page"
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


# --- a record the text only names: the AI decides ------------------------------
# The case behind meta.match: a hand-written note titled "Finite-Depth
# Quasi-Phase Matching and Converter Efficiency" got the record of an SPIE
# Field Guide chapter titled "Quasi-phase-matching", stored as confirmed
# because that title is a phrase of the note's.

QPM_TITLE = "Finite-Depth Quasi-Phase Matching and Converter Efficiency"
QPM_TEXT = (
    "[PDF page 1]\n" + QPM_TITLE + "\nAbhishek V. Karve\n"
    "Department of Applied Physics, Stanford University\n"
    "A separate 1188 nm lattice produces the periodic atomic density required for\n"
    "quasi-phase matching (QPM). Finite localization within each site reduces the overlap.\n"
    "1.2. Lattice localization and quasi-phase matching\n"
)
CHAPTER = {
    "title": "Quasi-phase-matching", "authors": [], "year": "", "venue": "Field Guide to Nonlinear Optics",
    "volume": "", "pages": "", "doi": "10.1117/3.1002081.ch35", "arxiv_id": "",
    "publisher": "SPIE", "kind": "bookSection", "source": "doi",
}
QPM_READING = {"title": QPM_TITLE, "authors": ["Abhishek V. Karve"], "year": "2026", "venue": "",
               "volume": "", "pages": "", "doi": "", "arxiv_id": "", "source": "ai", "kind": "document"}


def _qpm_page(guest, monkeypatch, ai_enabled, pick=None, refuse=False):
    """The note's page (its title still automatic), Crossref answering with
    the chapter for every query, and the AI — when on — answering the record
    check with ``pick`` (or refusing it) and reading the note itself. Returns
    the page and the record listings the AI was shown."""
    page = make_page(guest, "Quasi-phase-matching",
                     properties={"doc_id": "d" * 24, "auto_title": "Quasi-phase-matching"})
    monkeypatch.setattr(metadata, "_pdf_excerpt", lambda u, d, limit: (QPM_TEXT, None, len(QPM_TEXT)))
    monkeypatch.setattr(metadata, "_ensure_indexed", lambda u, d: True)
    monkeypatch.setattr(metadata, "ai_runtime", lambda u: {"enabled": ai_enabled})
    monkeypatch.setattr(metadata, "_resolve_model", lambda rt, m: "m")
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [dict(CHAPTER, source="crossref")])
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: (dict(CHAPTER), "@incollection{x}") if d == CHAPTER["doi"] else (None, ""))
    monkeypatch.setattr(metadata, "_book_search", lambda t, a="": [])
    shown = []

    def fake_ai(messages, system, model, rt, **kw):
        if system == MATCH_PROMPT:
            shown.append(messages[0]["content"])
            if refuse:
                raise AllowanceExhausted(10, 10)
            return json.dumps({"record": pick, "reason": "a textbook chapter on the topic"})
        if "Siegman, _Lasers_" in system:  # the slide citation
            return "Karve (2026)"
        return json.dumps(QPM_READING)
    monkeypatch.setattr(metadata, "_call_ai", fake_ai)
    return page, shown


def test_a_title_phrase_hit_stands_flagged_without_ai(guest, monkeypatch):
    page, shown = _qpm_page(guest, monkeypatch, ai_enabled=False)
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert meta["title"] == "Quasi-phase-matching" and meta["match"] == "phrase"
    assert meta["unverified"] is True
    # An unconfirmed record leaves the automatic title alone
    assert r.json()["title_updated"] is False and r.json()["page_title"] == "Quasi-phase-matching"
    props = guest.get(f"/api/blocks/{page['id']}").json()["properties"]
    assert props["auto_title"] == "Quasi-phase-matching"
    row = next(p for p in guest.get("/api/metadata/status").json()["papers"] if p["id"] == page["id"])
    assert row["meta_match"] == "phrase" and row["meta_unverified"] is True
    assert shown == []


def test_the_ai_rejects_a_title_phrase_hit_and_reads_the_note(guest, monkeypatch):
    page, shown = _qpm_page(guest, monkeypatch, ai_enabled=True, pick=0)
    asked = []
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: asked.append(d) or ((dict(CHAPTER), "@x") if d == CHAPTER["doi"] else (None, "")))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert meta["title"] == QPM_TITLE and meta["source"] == "ai" and meta["match"] == "ai_reading"
    assert asked == [CHAPTER["doi"]]   # the rejected record is not fetched again to check the reading
    assert meta["unverified"] is False            # a document: nothing a registry could confirm
    assert len(shown) == 1 and "1. Title: Quasi-phase-matching" in shown[0] and QPM_TITLE in shown[0]
    assert r.json()["title_updated"] is True and r.json()["page_title"] == QPM_TITLE


def test_the_ai_confirms_a_title_phrase_hit(guest, monkeypatch):
    page, shown = _qpm_page(guest, monkeypatch, ai_enabled=True, pick=1)
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert meta["doi"] == CHAPTER["doi"] and meta["match"] == "ai" and meta["unverified"] is False
    assert r.json()["title_updated"] is True and len(shown) == 1


def test_a_refused_record_check_keeps_the_hit_flagged(guest, monkeypatch):
    """A used-up allowance is not "no match": the hit stands, flagged, and
    the lookup does not fail."""
    page, shown = _qpm_page(guest, monkeypatch, ai_enabled=True, refuse=True)
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["match"] == "phrase" and r.json()["meta"]["unverified"] is True
    assert len(shown) == 1


def test_the_title_page_ties_a_search_hit_without_ai(guest, monkeypatch):
    """The registry holds the note itself: its title is the note's, its
    author named on it — taken as is, no AI asked."""
    page, shown = _qpm_page(guest, monkeypatch, ai_enabled=True, pick=0)
    own = dict(QPM_READING, doi="10.9999/qpm.1", source="doi", kind="report")
    monkeypatch.setattr(metadata, "_crossref_search",
                        lambda q, rows=5: [dict(CHAPTER, source="crossref"), dict(own, source="crossref")])
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: {CHAPTER["doi"]: (dict(CHAPTER), "@x"), own["doi"]: (dict(own), "@y")}.get(d, (None, "")))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["doi"] == "10.9999/qpm.1" and r.json()["meta"]["match"] == "title"
    assert shown == []


def test_a_cited_preprints_id_in_the_text_is_only_named(guest, monkeypatch):
    """A short note's head reaches its reference list: the arXiv id there is
    a cited preprint's, whose title the list prints — named, not tied."""
    cited = {"title": "Quantum-limited millimeter wave to optical transduction", "authors": ["A. Kumar"],
             "year": "2022", "venue": "arXiv", "volume": "", "pages": "", "doi": "",
             "arxiv_id": "2207.10121", "source": "arxiv"}
    text = QPM_TEXT + ("[2] A. Kumar et al., Quantum-limited millimeter wave to optical transduction "
                       "(2022), arXiv:2207.10121.\n")
    page = _paper_page(guest, text, monkeypatch)
    monkeypatch.setattr(metadata, "_fetch_arxiv", lambda i: dict(cited))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["match"] == "id_only" and r.json()["meta"]["unverified"] is True


# --- the title page: where the title is, and who is printed by it ----------------

def test_authors_are_found_however_the_pdf_prints_them():
    """Two-letter surnames, affiliation marks glued to a name, accents the
    registry spells plain (or the other way round). A record that lists no
    authors is never more than "title_only": its title alone is no
    evidence (a supplement's component, a front-matter page and a
    textbook chapter on the topic list none)."""
    title = "Topological phases of matter in driven optical lattices"
    rec = {"title": title, "authors": ["Wei Li", "Xin Wu"]}
    assert _tie(_Document(f"{title}\nWei Li and Xin Wu\nAbstract text"), rec) == "title"
    rec = {"title": title, "authors": ["Nissim Ofek", "Robert J. Schoelkopf Jr."]}
    assert _tie(_Document(f"{title}\nNissim Ofek1*, Andrei Petrenko1*\nAbstract"), rec) == "title"
    rec = {"title": title, "authors": ["Jose Garcia"]}
    assert _tie(_Document(f"{title}\nJosé García²\nAbstract"), rec) == "title"
    rec = {"title": title, "authors": ["Jörg Müller"]}
    assert _tie(_Document(f"{title}\nJ. Muller\nAbstract"), rec) == "title"
    edited = {"title": title, "authors": []}
    assert _tie(_Document(f"{title}\nEdited by many\n", guess=title), edited) == "title_only"
    assert _tie(_Document(f"{title}\nEdited by many\n"), edited) == "title_only"   # no largest type to go by


def test_a_heading_or_a_reference_is_no_title_page():
    """A numbered section heading alike to a registry title, a short generic
    title, and an author named only in the reference list tie nothing."""
    note = ("[PDF page 1]\n" + QPM_TITLE + "\nAbhishek V. Karve\nAbstract text.\n"
            + "\n".join(f"Body line {i} about lattices and converters." for i in range(40))
            + "\n1 Introduction\nWe follow earlier work.\n2 Quasi-phase matching\nMore text.\n"
            + "\n".join(f"Body line {i} about cavities." for i in range(40))
            + "\n[1] M. O. Scully, Introduction, in Quantum Optics (1997).\n"
            + "[2] P. E. Powers, Quasi-phase-matching, in Field Guide to Nonlinear Optics.\n")
    doc = _Document(note, guess=QPM_TITLE)
    assert _tie(doc, {"title": "Introduction", "authors": ["Marlan O. Scully"]}) == ""
    assert _tie(doc, dict(CHAPTER, authors=["Peter E. Powers"])) == "phrase"
    # The note's own record still ties: its author is printed under its title.
    assert _tie(doc, {"title": QPM_TITLE, "authors": ["Abhishek V. Karve"]}) == "title"
    # Its title with an author only the references name: not its title page.
    far = {"title": QPM_TITLE, "authors": ["Peter E. Powers"]}
    assert _tie(doc, far) == "title_only"


def test_a_subtitle_ties_where_the_pdf_sets_it_apart():
    """doi.org joins the subtitle to the title; the PDF prints them on lines
    of their own. The main title alone is a series' and ties no volume."""
    text = "Planck 2018 results\nA&A 641, A6 (2020)\nVI. Cosmological parameters\nPlanck Collaboration\n"
    doc = _Document(text)
    vi = {"title": "Planck 2018 results: VI. Cosmological parameters", "authors": ["Planck Collaboration"]}
    viii = {"title": "Planck 2018 results: VIII. Gravitational lensing", "authors": ["Planck Collaboration"]}
    assert _tie(doc, vi) == "title"
    assert _tie(doc, viii) == ""


def test_without_ai_the_papers_own_record_beats_a_cited_one(guest, monkeypatch):
    """The own DOI's record names the PDF's title but no author is printed
    by it (nothing ties it); the cited DOI before it resolves to a work the
    PDF does not print at all. Without AI the better named record stands."""
    page = _paper_page(guest, PAGE_TEXT, monkeypatch)
    own = dict(REAL_META, authors=["Q. Nobody"])
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d: {"10.1000/cited123": (CITED_META, "@x"),
                                   "10.1000/real456": (own, "@article{real}")}.get(d, (None, "")))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["doi"] == "10.1000/real456"
    assert r.json()["meta"]["match"] == "title_only" and r.json()["meta"]["unverified"] is True


def test_a_doi_found_twice_is_asked_and_shown_once(guest, monkeypatch):
    """The own DOI is printed and Crossref returns it again: one doi.org
    lookup, one record shown to the AI."""
    page = _paper_page(guest, PAGE_TEXT, monkeypatch, ai_enabled=True)
    own = dict(REAL_META, authors=["Q. Nobody"])
    asked = []

    def fetch(d):
        asked.append(d.lower())
        return {"10.1000/cited123": (CITED_META, "@x"), "10.1000/real456": (own, "@article{real}")}.get(d.lower(), (None, ""))
    monkeypatch.setattr(metadata, "_fetch_doi", fetch)
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [dict(own, source="crossref")])
    monkeypatch.setattr(metadata, "_resolve_model", lambda rt, m: "m")
    shown = []

    def fake_ai(messages, system, model, rt, **kw):
        if system == MATCH_PROMPT:
            shown.append(messages[0]["content"])
            return '{"record": 1, "reason": "same title and authors"}'
        return "Lee et al. (2015)"
    monkeypatch.setattr(metadata, "_call_ai", fake_ai)
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert asked.count("10.1000/real456") == 1
    listing = shown[0].split("Records:\n", 1)[1] if len(shown) == 1 else ""
    assert listing.count("10.1000/real456") == 1
    assert listing.startswith("1. Title: " + REAL_TITLE)   # the better named record first
    assert r.json()["meta"]["doi"] == "10.1000/real456" and r.json()["meta"]["match"] == "ai"


def test_the_ai_check_reads_a_lenient_answer():
    assert _picked({"record": 2}) == 2 and _picked({"record": "1"}) == 1 and _picked({"record": 1.0}) == 1
    assert _picked({"record": 0}) == 0 and _picked({"record": None}) == 0 and _picked({"record": "none"}) == 0
    assert _picked({"reason": "no number"}) is None and _picked(["record", 1]) is None
    assert _picked({"record": "the second one"}) is None


def test_verify_ties_a_subtitled_hit_on_its_full_record(monkeypatch):
    """The AI read a subtitled title; Crossref's hit carries the main title
    only. Its doi.org record is fetched and compared whole — the right
    paper is taken, a series' other volume is not."""
    full = {"title": "Deep Learning for Molecular Design: A Review of the State of the Art",
            "authors": ["A. Writer"], "year": "2019", "doi": "10.1000/dl.1", "source": "doi"}
    monkeypatch.setattr(metadata, "_crossref_search",
                        lambda q, rows=5: [dict(full, title="Deep Learning for Molecular Design", source="crossref")])
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (dict(full), "@x") if d == "10.1000/dl.1" else (None, ""))
    reading = {"title": full["title"], "authors": ["A. Writer"], "year": "2019", "doi": "", "arxiv_id": "",
               "source": "ai", "kind": "journalArticle"}
    meta, _, match = _verify_ai_meta(dict(reading), "", "")
    assert meta["doi"] == "10.1000/dl.1" and match == "ai"
    volume_i = {"title": "Planck 2018 results: I. Overview and the cosmological legacy of Planck",
                "authors": ["Planck Collaboration"], "year": "2020", "doi": "10.1000/planck.1", "source": "doi"}
    monkeypatch.setattr(metadata, "_crossref_search",
                        lambda q, rows=5: [dict(volume_i, title="Planck 2018 results", source="crossref")])
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (dict(volume_i), "@x"))
    monkeypatch.setattr(metadata, "_book_search", lambda t, a="": [])
    text = "Planck 2018 results\nVI. Cosmological parameters\nPlanck Collaboration: N. Aghanim\n"
    reading = dict(reading, title="Planck 2018 results. VI. Cosmological parameters", authors=["N. Aghanim"], year="2020")
    meta, _, match = _verify_ai_meta(dict(reading), text, "")
    assert match == "ai_reading" and meta["doi"] == ""


# --- names as PDFs print them, and what is no title page -------------------------

TITLE = "Coherent population trapping in a dense rubidium vapor cell"


def test_authors_printed_with_marks_and_old_accents_are_found():
    """Elsevier-style affiliation letters glued to names, pdflatex's old
    (OT1) accents set as marks of their own, letters with no decomposition,
    and a record whose authors are in a script names cannot be matched in
    (never more than "title_only")."""
    rec = {"title": TITLE, "authors": ["Alice Johnson", "Bob Lee"]}
    assert _tie(_Document(f"{TITLE}\nAlice Johnsona,b, Bob Leeb,*\n"), rec) == "title"
    rec = {"title": TITLE, "authors": ["Jürgen Müller", "Klaus Schäfer"]}
    assert _tie(_Document(f"{TITLE}\nJ. M¨uller and K. Sch¨afer\n"), rec) == "title"
    rec = {"title": TITLE, "authors": ["Ahmet Yildiz"]}
    assert _tie(_Document(f"{TITLE}\nAhmet Yıldız\n"), rec) == "title"
    rec = {"title": TITLE, "authors": ["김철수", "이영희"]}
    assert _tie(_Document(f"{TITLE}\n김철수, 이영희\n", guess=TITLE), rec) == "title_only"  # no name to match


def test_a_generic_title_or_a_common_word_is_no_author():
    """A short generic title is no document's, even as the largest type of
    a record listing no authors; a two-letter surname matches a name, not
    the word it spells."""
    intro = {"title": "Introduction", "authors": []}
    assert _tie(_Document("Introduction\nJohn Smith\n", guess="Introduction"), intro) != "title"
    rec = {"title": "A review of quantum optics textbooks", "authors": ["Jae-Won An"]}
    text = "A review of quantum optics textbooks\nJohn Smith\nThis is an accessible review.\n"
    assert _tie(_Document(text), rec) == "title_only"
    assert _tie(_Document(text.replace("John Smith", "Jae-Won An")), rec) == "title"


def test_titles_that_look_like_headings_are_titles():
    rec = {"title": "C. elegans neurons sense mechanical stimuli through a conserved channel",
           "authors": ["Jane Smith"]}
    text = ("RESEARCH ARTICLE\nC. elegans neurons sense mechanical stimuli\nthrough a conserved channel\n"
            "Jane Smith, Robert Brown\n")
    assert _tie(_Document(text), rec) == "title"
    rec = {"title": "100 years of the Raman effect", "authors": ["Jane Smith"]}
    assert _tie(_Document("100 years of the Raman effect\nJane Smith\n"), rec) == "title"


def test_numbered_parts_of_a_series_are_different_works():
    one = {"title": "Nonlinear dynamics of trapped ion chains. I", "authors": ["A. Writer"]}
    text = "Nonlinear dynamics of trapped ion chains. II\nA. Writer\n"
    assert _tie(_Document(text), one) != "title"
    assert _tie(_Document(text), dict(one, title="Nonlinear dynamics of trapped ion chains. II")) == "title"


def test_a_series_hit_whose_record_cannot_be_fetched_ties_no_volume(guest, monkeypatch):
    """doi.org failing for a search hit: its subtitle-less title is a
    series', so at most a phrase, never the title page."""
    text = "Planck 2018 results\nVIII. Gravitational lensing\nPlanck Collaboration: N. Aghanim\n"
    page = _paper_page(guest, text, monkeypatch)
    hit = {"title": "Planck 2018 results", "authors": ["Planck Collaboration"], "year": "2020",
           "doi": "10.1000/planck.1", "source": "crossref"}
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [dict(hit)])
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (None, ""))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["match"] == "phrase" and r.json()["meta"]["unverified"] is True


def test_a_page_record_naming_a_series_takes_no_volume(guest, monkeypatch):
    """The web page's record says "Planck 2018 results" alone: Crossref's
    volume is the same work only on its full record, so the page's own
    record stands."""
    record = {"title": "Planck 2018 results", "authors": ["Planck Collaboration"], "year": "2020"}
    vi = {"title": "Planck 2018 results: VI. Cosmological parameters", "authors": ["Planck Collaboration"],
          "year": "2020", "doi": "10.1000/planck.6", "source": "doi"}
    page, _ = _clipped_page(guest, monkeypatch, record,
                            crossref=lambda q: [dict(vi, title="Planck 2018 results", source="crossref")])
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (dict(vi), "@x"))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "page" and r.json()["meta"]["match"] == "page"


def test_the_ai_answer_is_read_past_reasoning_and_odd_numbers():
    assert _picked(_answer('Checking set {1, 2}: record 2 matches.\n{"record": 2, "reason": "same"}')) == 2
    assert _picked(_answer('{"record": 0}\nOn reflection: {"record": 2, "reason": "x"}')) == 2
    assert _picked(_answer('```json\n{"record": 1, "reason": "same"}\n```')) == 1
    assert _picked(_answer("no JSON at all")) is None
    assert _picked({"record": "inf"}) is None
    assert _picked(json.loads('{"record": Infinity}')) is None


# --- the clear case only: no author, no notice, no common word, no cited book ----

def test_a_record_listing_no_authors_is_never_confirmed(guest, monkeypatch):
    """A topic-titled note and the textbook chapter of that title, which
    lists no authors: unconfirmed, whatever the title (the incident's class),
    and a supplement's component is never a candidate."""
    real_search = metadata._crossref_search
    note = "Quasi-Phase Matching\nAbhishek V. Karve\nNotes on lattices.\n"
    assert _tie(_Document(note, guess="Quasi-Phase Matching"), CHAPTER) == "title_only"
    page = _paper_page(guest, note, monkeypatch)
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [dict(CHAPTER, source="crossref")])
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (dict(CHAPTER), "@x"))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["match"] == "title_only" and r.json()["meta"]["unverified"] is True
    import json as json_mod
    items = [{"DOI": "10.1021/x.s001", "type": "component", "title": ["Ultrafast plasmonic control of harmonics"]},
             {"DOI": "10.1021/x", "type": "journal-article", "title": ["Ultrafast plasmonic control of harmonics"]}]
    monkeypatch.setattr(metadata, "_http_get", lambda url, accept="", timeout=20, headers=None:
                        json_mod.dumps({"message": {"items": items}}).encode())
    assert [c["doi"] for c in real_search("Ultrafast plasmonic control")] == ["10.1021/x"]


def test_a_book_the_text_only_cites_with_its_isbn_is_named(guest, monkeypatch):
    """A report's reference list (or a syllabus) prints a textbook's title,
    author and ISBN: the book is named, not confirmed."""
    from test_metadata_books import OL_HIT
    text = ("Cavity-enhanced second-harmonic generation in a bow-tie ring resonator\nJane Q. Student\n"
            "Abstract text.\n[1] A. E. Siegman. Lasers. University Science Books, 1986. ISBN: 0-935702-11-3.\n")
    page = _paper_page(guest, text, monkeypatch)
    monkeypatch.setattr(metadata, "_fetch_isbn",
                        lambda i: dict({k: v for k, v in OL_HIT.items() if k not in ("isbns", "years")}, isbn=i, source="isbn"))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["title"] == "Lasers" and r.json()["meta"]["unverified"] is True
    assert r.json()["meta"]["match"] == "id_only" and r.json()["title_updated"] is False


def test_a_notice_is_not_the_work_it_is_about():
    """"Comment on X", "Correction to X", "Erratum: X" are works of their
    own: X's record does not tie to them, however long X is."""
    x = "Evidence of near-ambient superconductivity in a nitrogen-doped lutetium hydride"
    rec = {"title": x, "authors": ["Nathan Dasenbrock-Gammon"]}
    for notice in (f"Comment on \u201c{x}\u201d", f"Correction to \"{x}\"", f"Erratum: {x}"):
        doc = _Document(f"{notice}\nJ. E. Hirsch\nDasenbrock-Gammon et al. reported\n", guess=notice)
        assert _tie(doc, rec) != "title", notice


def test_an_author_is_a_name_not_a_word():
    """A surname matches a capitalized word: Sen's paper does not tie to a
    student's essay of the same title that says "in a sense"."""
    title = "An introduction to anyons"
    sen = {"title": title, "authors": ["Diptiman Sen"]}
    essay = f"{title}\nAvinash Deshmukh\nIn a sense they interpolate between bosons and fermions.\n"
    assert _tie(_Document(essay, guess=title), sen) == "title_only"
    assert _tie(_Document(essay.replace("Avinash Deshmukh", "Diptiman Sen"), guess=title), sen) == "title"


def test_beside_a_largest_type_a_title_line_only_ranks_a_record():
    """Where there is a largest type, only it confirms: a title-block line
    may be a heading naming a cited work, a CV's or a syllabus's entry, with
    that work's authors under it. Such a record is "title_only" — first in
    line for the AI, never confirmed without it. That holds for the title
    the largest type misses too (sharing its size with "Abstract", two
    words, under an Elsevier masthead): unconfirmed, not lost."""
    note = ("Optomechanical sideband cooling: group meeting notes\nTim Rydberg\nMarch 3\n"
            "Cavity optomechanics\nThe review by Aspelmeyer, Kippenberg and Marquardt sets the notation.\n")
    review = {"title": "Cavity optomechanics", "authors": ["Markus Aspelmeyer"]}
    guess = "Optomechanical sideband cooling: group meeting notes"
    assert _tie(_Document(note, guess=guess), review) == "title_only"
    cv = ("Jane Q. Researcher\nCurriculum vitae\nPublications\n"
          "Real-time quantum error correction beyond break-even\nV. V. Sivak, J. Q. Researcher, M. H. Devoret\n")
    paper = {"title": "Real-time quantum error correction beyond break-even", "authors": ["V. V. Sivak"]}
    assert _tie(_Document(cv, guess="Jane Q. Researcher"), paper) == "title_only"
    title = "A Quantum Approximate Optimization Algorithm"
    qaoa = {"title": title, "authors": ["Edward Farhi"]}
    page = f"{title}\nEdward Farhi and Jeffrey Goldstone\nAbstract\nWe introduce an algorithm.\n"
    assert _tie(_Document(page, guess=f"{title} Abstract"), qaoa) == "title_only"
    assert _tie(_Document(page, guess=title), qaoa) == "title"
    assert _tie(_Document(page), qaoa) == "title"   # no largest type: the first page's lines confirm
    masthead = "Journal of Computational Physics"
    page = (f"{masthead}\nContents lists available at ScienceDirect\n"
            "Physics-informed neural networks for forward and inverse problems\nM. Raissi, P. Perdikaris\n")
    own = {"title": "Physics-informed neural networks for forward and inverse problems", "authors": ["Maziar Raissi"]}
    assert _tie(_Document(page, guess=masthead), own) == "title_only"
    assert _tie(_Document(page, guess=masthead), {"title": masthead, "authors": []}) == "title_only"


def test_a_heading_naming_a_cited_review_is_not_confirmed(guest, monkeypatch):
    note = ("[PDF page 1]\nOptomechanical sideband cooling: group meeting notes\nTim Rydberg\nMarch 3\n"
            "Cavity optomechanics\nThe review by Aspelmeyer, Kippenberg and Marquardt [1] sets the notation.\n"
            "[PDF page 2]\n[1] M. Aspelmeyer et al., Rev. Mod. Phys. 86, 1391 (2014), doi:10.1103/RevModPhys.86.1391\n")
    review = {"title": "Cavity optomechanics", "authors": ["Markus Aspelmeyer", "Tobias J. Kippenberg"],
              "year": "2014", "doi": "10.1103/RevModPhys.86.1391", "source": "doi", "kind": "journalArticle"}
    page = make_page(guest, "notes.pdf", properties={"doc_id": "d" * 24, "auto_title": "notes.pdf"})
    monkeypatch.setattr(metadata, "_pdf_excerpt", lambda u, d, limit: (note, None, len(note)))
    monkeypatch.setattr(metadata, "_ensure_indexed", lambda u, d: True)
    monkeypatch.setattr(metadata, "ai_runtime", lambda u: {"enabled": False})
    monkeypatch.setattr(metadata, "_pdf_path", lambda ws, d: "notes.pdf")
    monkeypatch.setattr(metadata, "_title_guess", lambda path: "Optomechanical sideband cooling: group meeting notes")
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (dict(review), "@x"))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [dict(review, source="crossref")])
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["unverified"] is True and r.json()["meta"]["match"] == "title_only"
    assert r.json()["title_updated"] is False


def test_a_notice_after_the_title_or_wrapped_at_a_hyphen_is_another_work():
    x = "Single-Atom Platinum Catalysts Anchored on Nitrogen-Doped Graphene for Efficient Hydrogen Evolution"
    rec = {"title": x, "authors": ["Wei Zhang"]}
    for notice in (f"{x}: Erratum", f"{x} (Retracted)", f"{x}: a reply", f"{x} revisited"):
        assert _tie(_Document(f"{notice}\nWei Zhang\n", guess=notice), rec) != "title", notice
    wrapped = ("Correction to \u201cSingle-Atom Platinum Catalysts Anchored on Nitrogen-\n"
               "Doped Graphene for Efficient Hydrogen Evolution\u201d\nWei Zhang\n")
    assert _tie(_Document(wrapped), rec) != "title"
    assert _tie(_Document(f"{x}\nWei Zhang\n"), rec) == "title"
    # A footnote mark glued to the title is no other work.
    assert _tie(_Document(f"{x}*1\nWei Zhang\n", guess=f"{x}1"), rec) == "title"


def test_a_long_title_finds_its_authors_under_it():
    """An issue-clipped report: the previous article's tail fills the page's
    opening lines, and the title wraps over four lines."""
    title = "Room-temperature ferroelectricity in strained ultrathin SrTiO3 films grown by molecular-beam epitaxy"
    rec = {"title": title, "authors": ["Haruki Tanaka", "Wei Chen"]}
    tail = "\n".join(f"previous article reference line {i} by Someone Else" for i in range(60))
    page = (f"{tail}\nFERROELECTRICS\nRoom-temperature\nferroelectricity in strained\n"
            "ultrathin SrTiO3 films grown\nby molecular-beam epitaxy\nHaruki Tanaka,1 Wei Chen,2* Paul McDonald3\n")
    assert _tie(_Document(page, guess=title), rec) == "title"


def test_verify_does_not_take_back_a_rejected_record(monkeypatch):
    """The AI's check said the cited paper is not this document; its reading
    then names that paper's DOI: dropped, not confirmed."""
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d: (dict(CITED_META), "@x"))
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    monkeypatch.setattr(metadata, "_book_search", lambda t, a="": [])
    reading = dict(CITED_META, source="ai", kind="journalArticle")
    meta, _, match = _verify_ai_meta(dict(reading), PAGE_TEXT, "", rejected=frozenset({"10.1000/cited123"}))
    assert match == "ai_reading" and meta["doi"] == ""
