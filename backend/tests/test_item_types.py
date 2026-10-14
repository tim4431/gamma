"""Item types (``meta.kind``) under Zotero's names: what every source maps
onto, how records stored under the old kinds or none are read, and the
warning rule that depends on them."""

from conftest import make_page
from test_metadata_verify import _paper_page

from gamma import item_types
from gamma.routers import metadata


def test_normalize_reads_zotero_names_and_the_old_kinds():
    assert item_types.normalize("conferencePaper") == "conferencePaper"
    assert item_types.normalize("BookSection") == "bookSection"   # an AI's casing
    assert item_types.normalize("notes") == "document"
    assert item_types.normalize("slides") == "presentation"
    assert item_types.normalize("other") == "document"
    assert item_types.normalize("book") == "book" and item_types.normalize("thesis") == "thesis"
    # The old "paper" named any article: the record's shape decides.
    assert item_types.normalize("paper") == ""
    assert item_types.normalize("podcast") == "" and item_types.normalize(None) == ""


def test_item_type_infers_what_a_record_names_none():
    assert item_types.item_type({"publisher": "Wiley"}) == "book"
    assert item_types.item_type({"arxiv_id": "1706.03762", "venue": "arXiv:1706.03762"}) == "preprint"
    assert item_types.item_type({"arxiv_id": "1602.03837", "venue": "Phys. Rev. Lett."}) == "journalArticle"
    assert item_types.item_type({"kind": "paper", "arxiv_id": "1706.03762"}) == "preprint"
    # Unknown stays a journal article, so it keeps the paper's warning.
    assert item_types.item_type({}) == "journalArticle"
    assert item_types.item_type(None) == "journalArticle"
    assert item_types.item_type({"kind": "report", "publisher": "Wiley"}) == "report"


def test_registry_and_zotero_types_map_onto_item_types():
    reg = item_types.from_registry
    assert reg("journal-article") == reg("article-journal") == "journalArticle"
    assert reg("proceedings-article") == reg("paper-conference") == "conferencePaper"
    assert reg("posted-content") == reg("article") == "preprint"
    assert reg("book-chapter") == reg("reference-entry") == "bookSection"
    assert reg("monograph") == reg("edited-book") == "book"
    assert reg("dissertation") == "thesis" and reg("report") == "report"
    assert reg("journal-issue") == reg("dataset") == reg("") == ""
    zot = item_types.from_zotero
    assert zot("conferencePaper") == "conferencePaper" and zot("preprint") == "preprint"
    assert zot("newspaperArticle") == zot("webpage") == "document"
    assert zot("attachment") == zot("") == zot(None) == ""


def test_publisher_falls_back_to_the_venue_of_a_thesis_or_report():
    assert item_types.publisher({"kind": "thesis", "venue": "MIT"}) == "MIT"
    assert item_types.publisher({"kind": "report", "publisher": "NIST", "venue": "x"}) == "NIST"
    assert item_types.publisher({"kind": "journalArticle", "venue": "Nature"}) == ""


def _ai_reply(monkeypatch, reply):
    monkeypatch.setattr(metadata, "_resolve_model", lambda rt, m: "m")
    monkeypatch.setattr(metadata, "_call_ai", lambda *a, **kw: reply)
    monkeypatch.setattr(metadata, "_crossref_search", lambda q, rows=5: [])
    monkeypatch.setattr(metadata, "_book_search", lambda t, a="": [])


def test_ai_kinds_land_on_item_types_and_decide_the_warning(guest, monkeypatch):
    page = _paper_page(guest, "Notes on lasers in optical cavities, read by the AI.", monkeypatch, ai_enabled=True)

    def fetch(reply):
        _ai_reply(monkeypatch, reply)
        r = guest.post("/api/metadata/fetch", json={"block_id": page["id"], "force": True})
        assert r.status_code == 200, r.text
        return r.json()["meta"]

    meta = fetch('{"title": "A Study Of Lasers In Cavities", "authors": ["Q"], "kind": "conferencePaper"}')
    assert meta["kind"] == "conferencePaper" and meta["unverified"] is True
    # A customised prompt still answering the old kinds.
    meta = fetch('{"title": "Lecture 5: Lasers", "authors": ["Q"], "kind": "notes"}')
    assert meta["kind"] == "document" and meta["unverified"] is False
    meta = fetch('{"title": "A Study Of Lasers In Cavities", "authors": ["Q"], "kind": "paper", "arxiv_id": ""}')
    assert meta["kind"] == "journalArticle" and meta["unverified"] is True
    meta = fetch('{"title": "My PhD Thesis On Lasers", "authors": ["Q"], "kind": "thesis", "publisher": "MIT"}')
    assert meta["kind"] == "thesis" and meta["unverified"] is False


def test_status_reports_the_stored_kind(guest):
    make_page(guest, "Typed status page", properties={"meta": {"title": "Typed status page", "kind": "preprint",
                                                                "source": "manual"}})
    rows = guest.get("/api/metadata/status").json()["papers"]
    assert next(p for p in rows if p["title"] == "Typed status page")["meta_kind"] == "preprint"
