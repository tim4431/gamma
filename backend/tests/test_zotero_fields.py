"""The Zotero Web API field mapping (``gamma.zotero_fields``): an item read
into Gamma's paper record exactly as the RDF import reads the same item, and
a record written back into each type's own fields. A push compares a page
with its item through ``normalized`` and ``project`` and writes what changed
with ``patch_for``, so the invariants here are what keeps a sync from
inventing edits or overwriting what only Zotero holds. The RDF import's
reading is the reference: the same works read either way are one record."""

import json
from pathlib import Path

import pytest

from gamma import item_types, zotero_fields
from gamma.zotero_export import build_rdf
from gamma.zotero_fields import (
    COMPARE_FIELDS, cite_key_from_item, field_for, fields_from_meta, meta_from_item, normalized, patch_for,
    primary_creator, project, retype,
)
from gamma.zotero_import import parse_zotero_rdf

RECORD_KEYS = ["title", "authors", "year", "venue", "volume", "pages", "doi", "arxiv_id", "publisher", "isbn",
               "source", "kind"]

# What Gamma held at the last sync, and the page's edits since.
OLD = {
    "title": "Neural Networks and Physical Systems", "authors": ["John Hopfield", "Ludwig van Beethoven",
                                                                 "Planck Collaboration"],
    "year": "2016", "venue": "Proc. Natl. Acad. Sci.", "volume": "79", "pages": "2554-2558",
    "doi": "10.1073/pnas.79.8.2554", "arxiv_id": "1602.00001", "publisher": "National Academy of Sciences",
    "isbn": "9780262035613",
}
NEW = {
    "title": "Attention Is All You Need", "authors": ["Ashish Vaswani", "Ludwig van Beethoven",
                                                      "Planck Collaboration"],
    "year": "2017", "venue": "NeurIPS", "volume": "30", "pages": "5998-6008", "doi": "10.5555/3295222.3295349",
    "arxiv_id": "1706.03762", "publisher": "Curran Associates", "isbn": "9781510860964",
}
# Which of these fields each of Gamma's types has room for in Zotero; every
# type holds a title, authors, a date, a DOI, an arXiv id and a citation key.
HOLDS = {
    "journalArticle": {"venue", "volume", "pages", "publisher"},
    "conferencePaper": {"venue", "volume", "pages", "publisher", "isbn"},
    "preprint": {"publisher"},
    "bookSection": {"venue", "volume", "pages", "publisher", "isbn"},
    "book": {"volume", "publisher", "isbn"},
    "thesis": {"publisher", "isbn"},
    "report": {"pages", "publisher", "isbn"},
    "presentation": {"venue"},
    "document": {"publisher"},
}
# Item data a push must leave as Zotero has it.
UNMODELED = ("key", "version", "abstractNote", "url", "language", "issue", "tags", "collections")


def _remote(kind):
    """An item of ``kind`` as Zotero holds it after a sync of OLD: what Gamma
    wrote, plus Zotero's own split of a name, an organisation kept whole, a
    contributor, a full date, a PMID line in Extra and fields Gamma does not
    model."""
    data = fields_from_meta({**OLD, "kind": kind}, "hopfield1982", title=OLD["title"])
    primary = primary_creator(kind)
    data.update({
        "key": "ABCD2345", "version": 41,
        "creators": [
            {"creatorType": primary, "firstName": "John", "lastName": "Hopfield"},
            {"creatorType": primary, "firstName": "Ludwig", "lastName": "van Beethoven"},
            {"creatorType": primary, "name": "Planck Collaboration"},
            {"creatorType": "contributor", "firstName": "Grace", "lastName": "Hopper"},
        ],
        "date": "2016-05-04",
        "extra": "\n".join(filter(None, ["PMID: 42", data.get("extra")])),
        "abstractNote": "What only Zotero holds.",
        "url": "https://example.org/item",
        "language": "en",
        "tags": [{"tag": "memory"}],
        "collections": ["QWER2345"],
    })
    if "issue" in _fields(kind):
        data["issue"] = "8"
    return data


def _fields(kind) -> set:
    """The fields the schema gives an item type."""
    return set(zotero_fields._spec(kind).get("fields") or ())


def _value(projected, field):
    return projected["cite_key"] if field == "cite_key" else projected["meta"].get(field, "")


def _patch(remote, meta, cite_key, changed, title=""):
    """``patch_for``'s body for a change that keeps the item's type: a PATCH."""
    body, replace = patch_for(remote, meta, cite_key, changed, title)
    assert replace is False
    return body


# --- the schema ---------------------------------------------------------------

def test_the_schema_names_each_type_with_its_fields_and_its_primary_creator():
    schema = json.loads(Path(zotero_fields.__file__).with_name("zotero_schema.json").read_text(encoding="utf-8"))
    assert schema["version"] == 42 and set(item_types.ITEM_TYPES) <= set(schema["types"])
    assert field_for("conferencePaper", "publicationTitle") == "proceedingsTitle"
    assert field_for("bookSection", "publicationTitle") == "bookTitle"
    assert field_for("thesis", "publisher") == "university"
    assert field_for("report", "publisher") == "institution"
    assert field_for("preprint", "publisher") == "repository"
    assert field_for("journalArticle", "publisher") == "publisher"  # schema 42 gives a journal article one
    assert field_for("book", "publicationTitle") is None
    assert primary_creator("presentation") == "presenter" and primary_creator("journalArticle") == "author"
    assert primary_creator("hologram") == "author"


# --- reading an item ----------------------------------------------------------

def test_each_type_reads_its_own_fields_into_the_record():
    cases = [
        ({"itemType": "journalArticle", "publicationTitle": "Nature", "publisher": "NPG", "volume": "521"},
         {"venue": "Nature", "publisher": "NPG", "volume": "521"}),
        ({"itemType": "conferencePaper", "proceedingsTitle": "CVPR", "conferenceName": "CVPR 2016",
          "publisher": "IEEE", "ISBN": "978-1-4673-8851-1"},
         {"venue": "CVPR", "publisher": "IEEE", "isbn": "9781467388511"}),
        ({"itemType": "bookSection", "bookTitle": "Handbook of X", "publisher": "Springer", "pages": "1-20"},
         {"venue": "Handbook of X", "publisher": "Springer", "pages": "1-20"}),
        ({"itemType": "presentation", "meetingName": "APS March Meeting", "sessionTitle": "Session A1"},
         {"venue": "APS March Meeting"}),
        ({"itemType": "preprint", "repository": "arXiv"}, {"venue": "", "publisher": "arXiv"}),
        ({"itemType": "thesis", "university": "MIT", "thesisType": "PhD"}, {"venue": "", "publisher": "MIT"}),
        ({"itemType": "report", "institution": "NIST", "pages": "1-20", "reportNumber": "TN 1297"},
         {"publisher": "NIST", "pages": "1-20"}),
        ({"itemType": "book", "volume": "2", "numPages": "775", "publisher": "MIT  Press"},
         {"volume": "2", "pages": "", "venue": "", "publisher": "MIT Press"}),
        ({"itemType": "webpage", "websiteTitle": "Quanta"}, {"venue": "Quanta", "kind": "document"}),
    ]
    for data, want in cases:
        meta = meta_from_item({"title": "  A \n title ", **data})
        assert list(meta) == RECORD_KEYS
        assert meta["title"] == "A title" and meta["source"] == "zotero"
        for key, value in want.items():
            assert meta[key] == value, (data["itemType"], key)
        assert meta["kind"] == want.get("kind", data["itemType"])


def test_what_an_item_leaves_empty_is_empty_and_a_standalone_file_has_no_kind():
    assert meta_from_item({"itemType": "journalArticle"}) == {
        "title": "", "authors": [], "year": "", "venue": "", "volume": "", "pages": "", "doi": "", "arxiv_id": "",
        "publisher": "", "isbn": "", "source": "zotero", "kind": "journalArticle",
    }
    assert "kind" not in meta_from_item({"itemType": "attachment", "title": "scan.pdf"})


def test_a_library_read_through_the_api_lands_as_the_rdf_import_reads_it():
    """The same works exported by Gamma as RDF and parsed back, and held by
    Zotero as Web API data (with what Zotero adds: editors, a full date, the
    abstract, more ISBNs): one record, key for key."""
    journal = {"title": "Deep learning", "authors": ["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"],
               "year": "2015", "venue": "Nature", "volume": "521", "pages": "436-444", "doi": "10.1038/nature14539",
               "publisher": "Nature Publishing Group", "kind": "journalArticle"}
    book = {"title": "Deep Learning", "authors": ["Ian Goodfellow", "Yoshua Bengio", "Planck Collaboration"],
            "year": "2016", "publisher": "MIT Press", "isbn": "9780262035613", "kind": "book"}
    preprint = {"title": "Attention Is All You Need", "authors": ["Ashish Vaswani"], "year": "2017",
                "doi": "10.48550/arXiv.1706.03762", "arxiv_id": "1706.03762", "publisher": "arXiv",
                "kind": "preprint"}
    text = build_rdf([
        {"key": "#item_1", "title": journal["title"], "meta": journal, "cite_key": "lecun2015"},
        {"key": "#item_2", "title": book["title"], "meta": book, "cite_key": ""},
        {"key": "#item_3", "title": preprint["title"], "meta": preprint, "cite_key": "vaswani2017"},
    ])
    rdf = {item["key"]: item for item in parse_zotero_rdf(text)}

    def author(first, last, creator_type="author"):
        return {"creatorType": creator_type, "firstName": first, "lastName": last}

    api = {
        "#item_1": {
            "key": "LECU2345", "version": 12, "itemType": "journalArticle", "title": "Deep learning",
            "creators": [author("Yann", "LeCun"), author("Yoshua", "Bengio"), author("Geoffrey", "Hinton"),
                         author("Ed", "Itor", "editor")],
            "abstractNote": "Deep learning allows computational models…", "publicationTitle": "Nature",
            "volume": "521", "issue": "7553", "pages": "436-444", "date": "2015-05-28",
            "journalAbbreviation": "Nature", "DOI": "10.1038/nature14539", "publisher": "Nature Publishing Group",
            "url": "https://www.nature.com/articles/nature14539", "extra": "PMID: 26017442\nCitation Key: lecun2015",
            "tags": [{"tag": "deep learning"}], "collections": ["QWER2345"], "relations": {},
        },
        "#item_2": {
            "key": "GOOD2345", "version": 13, "itemType": "book", "title": "Deep Learning",
            "creators": [author("Ian", "Goodfellow"), author("Yoshua", "Bengio"),
                         {"creatorType": "author", "name": "Planck Collaboration"},
                         {"creatorType": "seriesEditor", "name": "Francis Bach"}],
            "series": "Adaptive Computation and Machine Learning", "date": "November 2016",
            "publisher": "MIT Press", "place": "Cambridge, MA", "numPages": "775",
            "ISBN": "978-0-262-03561-3 0-262-03561-8", "url": "http://www.deeplearningbook.org", "extra": "",
        },
        "#item_3": {
            "key": "VASW2345", "version": 14, "itemType": "preprint", "title": "Attention Is All You Need",
            "creators": [author("Ashish", "Vaswani")], "repository": "arXiv", "archiveID": "arXiv:1706.03762",
            "date": "2017-06-12", "DOI": "10.48550/arXiv.1706.03762", "url": "http://arxiv.org/abs/1706.03762",
            "citationKey": "vaswani2017",
        },
    }
    for key, data in api.items():
        assert meta_from_item(data) == rdf[key]["meta"], key
        assert list(meta_from_item(data)) == list(rdf[key]["meta"]), key
        assert cite_key_from_item(data) == rdf[key]["cite_key"], key


def test_arxiv_ids_come_from_the_url_the_doi_the_archive_id_or_extra():
    def read(item_type="journalArticle", **data):
        return meta_from_item({"itemType": item_type, **data})["arxiv_id"]

    assert read(url="https://arxiv.org/pdf/1706.03762v5") == "1706.03762"
    assert read(DOI="10.48550/arXiv.2101.00001") == "2101.00001"
    assert read("preprint", archiveID="arXiv:1706.03762") == "1706.03762"
    assert read(extra="PMID: 1\narXiv: hep-th/9901001") == "hep-th/9901001"
    assert read(url="https://example.org/paper", DOI="10.1038/x", extra="PMID: 1") == ""


def test_the_citation_key_is_the_field_or_better_bibtexs_extra_line():
    assert cite_key_from_item({"itemType": "book", "citationKey": "goodfellow2016"}) == "goodfellow2016"
    assert cite_key_from_item({"itemType": "book", "extra": "tex.ids: x\nCitation Key: Fösel2018 \nPMID: 1"}) == \
        "Fosel2018"
    assert cite_key_from_item({"itemType": "book", "citationKey": "mine", "extra": "Citation Key: old"}) == "mine"
    assert cite_key_from_item({"itemType": "book", "extra": "PMID: 1"}) == ""


def test_authors_are_the_primary_creators_and_an_organisation_stays_whole():
    talk = {"itemType": "presentation", "creators": [
        {"creatorType": "presenter", "firstName": "Ada", "lastName": "Lovelace"},
        {"creatorType": "presenter", "name": "Planck Collaboration"},
        {"creatorType": "chair", "firstName": "Charles", "lastName": "Babbage"},
        {"creatorType": "presenter", "firstName": "", "lastName": "Aristotle"},
    ]}
    assert meta_from_item(talk)["authors"] == ["Ada Lovelace", "Planck Collaboration", "Aristotle"]
    assert fields_from_meta({"kind": "presentation", "authors": ["Ada Lovelace", "Aristotle"]})["creators"] == [
        {"creatorType": "presenter", "firstName": "Ada", "lastName": "Lovelace"},
        {"creatorType": "presenter", "name": "Aristotle"},
    ]
    # A push keeps the entries the item already has (the organisation whole),
    # splits a new name at its last space, and leaves the chair after them.
    patch = _patch(talk, {"kind": "presentation", "authors": ["Planck Collaboration", "Ada Lovelace",
                                                                 "Grace Brewster Hopper"]}, "", {"authors"})
    assert patch["creators"] == [
        {"creatorType": "presenter", "name": "Planck Collaboration"},
        {"creatorType": "presenter", "firstName": "Ada", "lastName": "Lovelace"},
        {"creatorType": "presenter", "firstName": "Grace Brewster", "lastName": "Hopper"},
        {"creatorType": "chair", "firstName": "Charles", "lastName": "Babbage"},
    ]


def test_the_isbn_is_the_first_of_several_in_digits():
    assert meta_from_item({"itemType": "book", "ISBN": "978-0-262-03561-3 0-262-03561-8"})["isbn"] == \
        "9780262035613"
    assert meta_from_item({"itemType": "book", "ISBN": "0-8044-2957-x"})["isbn"] == "080442957X"
    # The field already starts with Gamma's ISBN: nothing to write.
    remote = {"itemType": "book", "ISBN": "978-0-262-03561-3 0-262-03561-8"}
    assert _patch(remote, {"kind": "book", "isbn": "9780262035613"}, "", {"isbn"}) == {}
    assert _patch(remote, {"kind": "book", "isbn": "9781510860964"}, "", {"isbn"}) == {"ISBN": "9781510860964"}


def test_the_year_is_read_from_the_date_and_a_fuller_date_in_that_year_stays():
    assert meta_from_item({"itemType": "journalArticle", "date": "28 May 2015"})["year"] == "2015"
    remote = {"itemType": "journalArticle", "date": "2015-05-28"}
    assert _patch(remote, {"kind": "journalArticle", "year": "2015"}, "", {"year"}) == {}
    assert _patch(remote, {"kind": "journalArticle", "year": "2016"}, "", {"year"}) == {"date": "2016"}


def test_a_type_newer_than_the_schema_is_read_by_base_names_and_never_written():
    data = {"itemType": "hologram", "title": "A Hologram", "date": "1971", "extra": "DOI: 10.1/holo",
            "creators": [{"creatorType": "author", "firstName": "Dennis", "lastName": "Gabor"}]}
    meta = meta_from_item(data)
    assert (meta["title"], meta["authors"], meta["year"]) == ("A Hologram", ["Dennis Gabor"], "1971")
    assert meta["doi"] == "10.1/holo" and meta["kind"] == "document"
    # The schema cannot vouch for any field of it, so a push writes none.
    assert _patch(data, {**meta, "title": "B", "year": "1972"}, "", {"title", "year"}) == {}


# --- writing an item ----------------------------------------------------------

def test_a_new_item_is_written_as_the_records_kind_or_the_type_asked_for():
    assert fields_from_meta({"arxiv_id": "1706.03762"})["itemType"] == "preprint"  # inferred, as item_types does
    assert fields_from_meta({"kind": "slides"})["itemType"] == "presentation"     # an old kind
    assert fields_from_meta({})["itemType"] == "document"                          # no record, as the RDF export
    assert fields_from_meta({"kind": "document"}, item_type="magazineArticle")["itemType"] == "magazineArticle"
    assert fields_from_meta({"kind": "book"}, item_type="note")["itemType"] == "book"
    item = fields_from_meta({"title": "From the record", "kind": "book"}, title="The page")
    assert item["title"] == "The page"  # the page's title names a new item, as in the RDF export
    assert fields_from_meta({"title": "From the record", "kind": "book"})["title"] == "From the record"
    assert normalized({"title": "From the record"}, title="The page")["meta"]["title"] == "From the record"
    assert normalized({"authors": ["A B"]}, title="The page")["meta"]["title"] == "The page"


@pytest.mark.parametrize("kind", item_types.ITEM_TYPES)
def test_a_record_keeps_what_its_type_holds_and_drops_the_rest(kind):
    item = fields_from_meta({**NEW, "kind": kind}, "vaswani2017")
    assert set(item) - {"itemType", "creators"} <= _fields(kind)
    got = normalized({**NEW, "kind": kind}, "vaswani2017")
    for field in ("title", "authors", "year", "doi", "arxiv_id"):
        assert got["meta"][field] == NEW[field], field
    for field in ("venue", "volume", "pages", "publisher", "isbn"):
        assert got["meta"][field] == (NEW[field] if field in HOLDS[kind] else ""), field
    assert got["meta"]["kind"] == kind and got["cite_key"] == "vaswani2017"


@pytest.mark.parametrize("kind", (None, *item_types.ITEM_TYPES))
def test_normalizing_a_record_twice_changes_nothing_more(kind):
    odd = {"title": "  Spaced   title ", "authors": ["Jean  Paul Sartre", "", "Aristotle"],
           "year": "2017-03 (online)", "isbn": "978-1-5108-6096-4", "doi": "10.48550/arXiv.1602.00001",
           "arxiv_id": "1706.03762", "venue": "arXiv:1706.03762"}
    thesis_of_old = {"title": "My thesis", "venue": "MIT", "publisher": ""}
    for meta in (NEW, odd, thesis_of_old, {"title": "Only a title"}, {}):
        meta = meta if kind is None else {**meta, "kind": kind}
        once = normalized(meta, "Key ü", None, "Page title")
        assert normalized(once["meta"], once["cite_key"], None, "Page title") == once, meta


def test_a_thesis_or_report_names_its_venue_as_publisher_when_it_has_none():
    thesis = fields_from_meta({"title": "T", "kind": "thesis", "venue": "MIT"})
    assert thesis["university"] == "MIT" and "publicationTitle" not in thesis
    assert normalized({"kind": "thesis", "venue": "MIT"})["meta"]["publisher"] == "MIT"
    assert fields_from_meta({"kind": "report", "venue": "NIST"})["institution"] == "NIST"
    assert fields_from_meta({"kind": "report", "venue": "x", "publisher": "NIST"})["institution"] == "NIST"
    assert "publisher" not in fields_from_meta({"kind": "journalArticle", "venue": "Nature"})


def test_arxiv_goes_out_as_a_preprints_archive_id_or_an_extra_line():
    pre = fields_from_meta({"title": "T", "arxiv_id": "1706.03762", "kind": "preprint"})
    assert pre["archiveID"] == "arXiv:1706.03762" and pre["repository"] == "arXiv"
    assert pre["url"] == "https://arxiv.org/abs/1706.03762"
    named = fields_from_meta({"arxiv_id": "1706.03762", "kind": "preprint", "publisher": "Cornell University"})
    assert named["repository"] == "Cornell University"
    article = fields_from_meta({"title": "T", "arxiv_id": "1706.03762", "kind": "journalArticle", "venue": "Nature"})
    assert article["extra"] == "arXiv: 1706.03762" and "url" not in article and "archiveID" not in article

    # The Extra line is added once; an arXiv URL naming another paper follows
    # Gamma's id, the item's own URL stays.
    page = {"kind": "journalArticle", "arxiv_id": "1706.03762"}
    remote = {**article, "url": "https://www.nature.com/x", "extra": "PMID: 7\narXiv: 1706.03762"}
    assert _patch(remote, page, "", {"arxiv_id"}) == {}
    assert _patch(remote, {**page, "arxiv_id": "2101.00001"}, "", {"arxiv_id"}) == \
        {"extra": "PMID: 7\narXiv: 2101.00001"}
    on_arxiv = {**article, "url": "https://arxiv.org/abs/1706.03762", "extra": ""}
    assert _patch(on_arxiv, {**page, "arxiv_id": "2101.00001"}, "", {"arxiv_id"}) == \
        {"url": "https://arxiv.org/abs/2101.00001", "extra": "arXiv: 2101.00001"}

    # A preprint whose id Gamma cleared loses the id everywhere it was named.
    cleared = _patch(pre, {"kind": "preprint", "arxiv_id": ""}, "", {"arxiv_id"})
    assert cleared == {"url": "", "archiveID": ""}
    assert project({**pre, **cleared})["meta"]["arxiv_id"] == ""


def test_a_citation_key_goes_out_in_its_field_and_an_older_pin_follows():
    assert fields_from_meta({"kind": "book"}, "goodfellow2016")["citationKey"] == "goodfellow2016"
    remote = {"itemType": "book", "citationKey": "", "extra": "Citation Key: old2016\nPMID: 1"}
    assert _patch(remote, {"kind": "book"}, "new2017", {"cite_key"}) == \
        {"citationKey": "new2017", "extra": "Citation Key: new2017\nPMID: 1"}
    assert _patch(remote, {"kind": "book"}, "", {"cite_key"}) == {"extra": "PMID: 1"}


# --- a push's patch -------------------------------------------------------------

@pytest.mark.parametrize("kind", item_types.ITEM_TYPES)
def test_a_patch_writes_only_the_changed_field_and_keeps_the_rest(kind):
    remote = _remote(kind)
    before = project(remote)
    page = {**NEW, "kind": kind}
    want = normalized(page, "vaswani2017", kind)
    for field in COMPARE_FIELDS:
        if field == "kind":
            continue
        patch = _patch(remote, page, "vaswani2017", {field})
        assert set(patch) <= _fields(kind) | {"creators"}, (field, patch)
        after = {**remote, **patch}
        got = project(after)
        assert _value(got, field) == _value(want, field), field
        for other in COMPARE_FIELDS:
            if other != field:
                assert _value(got, other) == _value(before, other), (field, other)
        for key in UNMODELED:
            assert after.get(key) == remote.get(key), (field, key)
        assert "PMID: 42" in after["extra"].split("\n")
        assert after["creators"][-1] == remote["creators"][-1]  # the contributor, after the authors
    # Zotero's own split of a name the page still lists survives an authors' edit.
    creators = _patch(remote, page, "vaswani2017", {"authors"})["creators"]
    assert creators[1:3] == remote["creators"][1:3]


@pytest.mark.parametrize("kind", item_types.ITEM_TYPES)
def test_a_page_holding_what_its_item_holds_writes_nothing_and_every_change_lands(kind):
    remote = _remote(kind)
    pulled = meta_from_item(remote)
    assert _patch(remote, pulled, cite_key_from_item(remote), set(COMPARE_FIELDS)) == {}

    page = {**NEW, "kind": kind}
    after = {**remote, **_patch(remote, page, "vaswani2017", set(COMPARE_FIELDS))}
    assert project(after) == normalized(page, "vaswani2017", kind)
    assert after["date"] == "2017" and after["abstractNote"] == remote["abstractNote"]


def test_a_kind_change_answers_the_whole_item_retyped_for_a_put():
    remote = _remote("journalArticle")
    put, replace = patch_for(remote, {**NEW, "kind": "conferencePaper"}, "vaswani2017", {"kind", "venue"})
    assert replace is True
    assert put["itemType"] == "conferencePaper" and put["proceedingsTitle"] == "NeurIPS"
    assert "publicationTitle" not in put and (put["key"], put["version"]) == ("ABCD2345", 41)
    assert put["issue"] == "8" and put["tags"] == remote["tags"] and put["extra"] == remote["extra"]
    assert project(put)["meta"]["kind"] == "conferencePaper"
    # A kind Gamma did not change is no reason to retype.
    assert "itemType" not in _patch(remote, {**NEW, "kind": "journalArticle"}, "", {"kind", "venue"})


def test_retype_moves_fields_by_base_field_and_drops_what_the_new_type_lacks():
    article = {
        "key": "ABCD2345", "version": 7, "itemType": "journalArticle", "title": "Deep learning",
        "creators": [{"creatorType": "author", "firstName": "Yann", "lastName": "LeCun"},
                     {"creatorType": "editor", "firstName": "Ed", "lastName": "Itor"}],
        "publicationTitle": "Nature", "volume": "521", "issue": "7553", "pages": "436-444",
        "journalAbbreviation": "Nat.", "ISSN": "0028-0836", "date": "2015-05-28", "DOI": "10.1038/x",
        "publisher": "NPG", "tags": [{"tag": "x"}], "collections": ["QWER2345"],
        "relations": {"dc:relation": "http://zotero.org/users/1/items/WXYZ2345"},
    }
    paper = retype(article, "conferencePaper")
    assert paper["proceedingsTitle"] == "Nature" and "publicationTitle" not in paper
    assert paper["issue"] == "7553" and paper["ISSN"] == "0028-0836" and "journalAbbreviation" not in paper
    assert paper["creators"] == article["creators"]
    assert {k: paper[k] for k in ("key", "version", "tags", "collections", "relations")} == \
        {k: article[k] for k in ("key", "version", "tags", "collections", "relations")}

    talk = retype(article, "presentation")
    assert talk["meetingName"] == "Nature" and "sessionTitle" not in talk
    assert [c["creatorType"] for c in talk["creators"]] == ["presenter", "contributor"]
    assert not {"volume", "pages", "publisher", "issue"} & set(talk)

    book = retype(article, "book")
    assert book["volume"] == "521" and book["publisher"] == "NPG"
    assert not {"publicationTitle", "pages", "issue"} & set(book)

    preprint = {"itemType": "preprint", "title": "T", "repository": "arXiv", "archiveID": "arXiv:1706.03762",
                "url": "https://arxiv.org/abs/1706.03762", "extra": "PMID: 1"}
    report = retype(preprint, "report")
    assert report["institution"] == "arXiv" and report["url"] == preprint["url"]
    assert report["extra"] == "PMID: 1\narXiv: 1706.03762" and not {"archiveID", "reportNumber"} & set(report)
    assert retype(retype(talk, "journalArticle"), "presentation")["meetingName"] == "Nature"

    report["creators"] = [{"creatorType": "author", "name": "X"}]
    retype(report, "book")["creators"][0]["name"] = "changed"
    assert report["creators"][0]["name"] == "X"  # the item passed in is left as it was
    for not_a_work in ("note", "attachment", "nonsense", ""):
        with pytest.raises(ValueError):
            retype(article, not_a_work)


def test_a_retyped_item_keeps_every_field_its_new_type_can_hold():
    for old in item_types.ITEM_TYPES:
        remote = fields_from_meta({**NEW, "kind": old}, "vaswani2017")
        held = project(remote)["meta"]
        for new in item_types.ITEM_TYPES:
            want = normalized(held, "vaswani2017", new)
            if not held["publisher"]:
                # A presentation names no publisher; the one a thesis (its
                # venue) or a preprint (arXiv) would take is the push's to write.
                want["meta"]["publisher"] = ""
            assert project(retype(remote, new)) == want, (old, new)


def test_a_type_outside_gammas_nine_reads_as_a_document_and_keeps_its_type():
    remote = {
        "key": "MAGA2345", "version": 3, "itemType": "magazineArticle", "title": "The Lab Notebook",
        "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}],
        "publicationTitle": "Wired", "date": "2019-02", "volume": "27", "pages": "40-45", "extra": "",
    }
    held = project(remote)
    assert held["meta"]["kind"] == "document" and held["meta"]["venue"] == "Wired"
    pulled = meta_from_item(remote)
    # Compared through the item's own type, an unedited page shows no change.
    assert normalized(pulled, "", "magazineArticle") == held
    assert _patch(remote, pulled, "", set(COMPARE_FIELDS)) == {}
    # Its kind still reads "document": even asked, the item keeps its type.
    assert _patch(remote, pulled, "", {"kind"}) == {}
    assert _patch(remote, {**pulled, "venue": "Wired UK"}, "", {"venue"}) == {"publicationTitle": "Wired UK"}
    # Gamma's kind changed: now it is retyped.
    put, replace = patch_for(remote, {**pulled, "kind": "journalArticle"}, "", {"kind"})
    assert replace is True and put["itemType"] == "journalArticle"
    assert put["publicationTitle"] == "Wired" and put["key"] == "MAGA2345"
