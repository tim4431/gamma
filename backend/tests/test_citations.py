"""The citation formats beside BibTeX (gamma/citations.py): CSL JSON, which
the web app renders every style from, RIS and BibLaTeX, each mapped as
Zotero's translators map an item, from records as the registries and hand
edits leave them — an author written as one string (a particle, a suffix,
an organisation), an arXiv preprint and the published paper read from
arXiv, an older thesis keeping its university as its venue, a date that no
longer agrees with the year.
"""

import json
import re

from gamma import bibtex, citations, zotero_fields

ARTICLE = {
    "title": "Collective processes of an\n   ensemble of spin-1/2 particles", "kind": "journalArticle",
    "authors": ["Bradley A. Chase", "Jan van der Berg", "Martin Luther King, Jr.", "ATLAS Collaboration"],
    "year": "2008", "date": "2008-11-03", "venue": "Physical Review A", "journal_abbr": "Phys. Rev. A",
    "volume": "78", "issue": "5", "pages": "052101–052110", "doi": "10.1103/PhysRevA.78.052101",
    "url": "https://journals.aps.org/pra/abstract/10.1103/PhysRevA.78.052101",
}
PREPRINT = {"title": "Attention Is All You Need", "authors": ["Ashish Vaswani"], "year": "2017",
            "date": "2017-06", "venue": "arXiv:1706.03762", "arxiv_id": "1706.03762v5", "kind": "preprint"}


def _ris(rec):
    """An RIS record's tags: (tag, value) per line."""
    return [tuple(line.split("  - ", 1)) for line in citations.ris_entry(rec, "key").split("\r\n") if line]


def test_a_journal_article_carries_every_field_into_csl():
    item = citations.csl_item(citations.record({"meta": ARTICLE}), "chase2008")
    assert item["id"] == item["citation-key"] == "chase2008" and item["type"] == "article-journal"
    assert item["title"] == "Collective processes of an ensemble of spin-1/2 particles"
    assert item["author"] == [{"family": "Chase", "given": "Bradley A."},
                              {"family": "Berg", "given": "Jan", "non-dropping-particle": "van der"},
                              {"family": "King", "given": "Martin Luther", "suffix": "Jr."},
                              {"literal": "ATLAS Collaboration"}]
    assert item["issued"] == {"date-parts": [[2008, 11, 3]]}
    assert (item["container-title"], item["container-title-short"]) == ("Physical Review A", "Phys. Rev. A")
    assert (item["volume"], item["issue"], item["page"]) == ("78", "5", "052101-052110")
    # The address of an article with pages stays in the file: leaving it out
    # is a style's choice, made where it renders.
    assert item["URL"] == ARTICLE["url"] and item["DOI"] == ARTICLE["doi"]
    assert json.loads(citations.csl_json([item, {"id": "b", "type": "book"}])) == [item, {"id": "b", "type": "book"}]


def test_an_arxiv_preprint_is_cited_as_zotero_saves_one():
    for rec in (PREPRINT, {**PREPRINT, "kind": "journalArticle"}):  # a journal article whose only venue is arXiv
        item = citations.csl_item(rec, "vaswani2017")
        assert item["type"] == "article" and "container-title" not in item
        assert (item["publisher"], item["number"]) == ("arXiv", "arXiv:1706.03762v5")
        assert item["URL"] == "https://arxiv.org/abs/1706.03762v5"
        assert item["DOI"] == "10.48550/arXiv.1706.03762"  # DataCite's, without the version
        tags = dict(_ris(rec))
        # No M1 for the number, as in Zotero's export: Zotero reads one back as an issue.
        assert tags["TY"] == "GEN" and "M1" not in tags and tags["PB"] == "arXiv"
        assert tags["UR"] == "https://arxiv.org/abs/1706.03762v5" and tags["DO"] == "10.48550/arXiv.1706.03762"
        assert tags["DA"] == "2017/06//" and tags["PY"] == "2017"
        entry = citations.biblatex_entry(rec, "vaswani2017")
        assert entry.startswith("@online{vaswani2017,\n")
        assert "eprint = {1706.03762v5}" in entry and "eprinttype = {arxiv}" in entry and "date = {2017-06}" in entry
        assert "doi" not in entry and "url" not in entry  # the eprint says it; no derived link beside it
    # A preprint elsewhere names its repository.
    assert citations.csl_item({"title": "T", "venue": "bioRxiv", "kind": "preprint"}, "k")["publisher"] == "bioRxiv"


def test_a_published_paper_read_from_arxiv_cites_its_journal():
    rec = {"title": "Observation of Gravitational Waves", "authors": ["B. P. Abbott"], "year": "2016",
           "venue": "Physical Review Letters", "volume": "116", "arxiv_id": "1602.03837",
           "doi": "10.1103/PhysRevLett.116.061102"}
    item = citations.csl_item(rec, "abbott2016")
    assert item["type"] == "article-journal" and item["container-title"] == "Physical Review Letters"
    assert "number" not in item and "publisher" not in item and item["DOI"] == rec["doi"]
    assert dict(_ris(rec))["TY"] == "JOUR" and ("T2", "Physical Review Letters") in _ris(rec)
    entry = citations.biblatex_entry(rec, "abbott2016")
    assert entry.startswith("@article{abbott2016,") and "journaltitle = {Physical Review Letters}" in entry
    assert "eprint = {1602.03837}" in entry


def test_a_thesis_and_a_report_name_who_published_them():
    # An older thesis record holds its university as the venue.
    thesis = {"title": "A thesis", "authors": ["A Student"], "year": "2019", "venue": "Stanford University",
              "kind": "thesis"}
    item = citations.csl_item(thesis, "k")
    assert item["type"] == "thesis" and item["publisher"] == "Stanford University" and "container-title" not in item
    assert ("PB", "Stanford University") in _ris(thesis) and dict(_ris(thesis))["TY"] == "THES"
    entry = citations.biblatex_entry(thesis, "k")
    assert entry.startswith("@thesis{k,") and "institution = {Stanford University}" in entry
    assert "type = {phdthesis}" in entry
    # A report with an institution of its own keeps its venue as its series.
    report = {"title": "R", "year": "2020", "publisher": "NIST", "venue": "NIST Technical Note", "kind": "report"}
    item = citations.csl_item(report, "k")
    assert (item["type"], item["publisher"], item["collection-title"]) == ("report", "NIST", "NIST Technical Note")
    assert dict(_ris(report))["TY"] == "RPRT" and ("T2", "NIST Technical Note") in _ris(report)
    assert "@report{k," in citations.biblatex_entry(report, "k") and "type = {techreport}" in \
        citations.biblatex_entry(report, "k")


def test_a_conference_paper_a_chapter_and_a_book_name_where_they_appeared():
    """As Zotero's translators write them: a conference paper's proceedings
    as RIS's C3 (not T2) and BibLaTeX's booktitle, a chapter's book as T2
    and booktitle with its publisher and ISBN, a book's publisher and ISBN;
    in CSL the venue is the container."""
    base = {"title": "T", "authors": ["Kaiming He"], "year": "2016"}
    conf = {**base, "kind": "conferencePaper", "venue": "CVPR", "publisher": "IEEE", "pages": "770-778"}
    chapter = {**base, "kind": "bookSection", "venue": "Handbook of X", "isbn": "9780000000002",
               "publisher": "Springer"}
    book = {**base, "kind": "book", "publisher": "Wiley", "isbn": "9780471878766"}
    tags = dict(_ris(conf))
    assert (tags["TY"], tags["C3"], tags["PB"]) == ("CONF", "CVPR", "IEEE") and "T2" not in tags
    assert {("TY", "CHAP"), ("T2", "Handbook of X"), ("PB", "Springer"), ("SN", "9780000000002")} <= set(_ris(chapter))
    assert {("TY", "BOOK"), ("PB", "Wiley"), ("SN", "9780471878766")} <= set(_ris(book))
    entry = citations.biblatex_entry(conf, "k")
    assert entry.startswith("@inproceedings{k,") and "booktitle = {{CVPR}}" in entry and "publisher = {IEEE}" in entry
    entry = citations.biblatex_entry(chapter, "k")
    assert entry.startswith("@incollection{k,") and "booktitle = {Handbook of X}" in entry
    assert "publisher = {Springer}" in entry and "isbn = {9780000000002}" in entry
    entry = citations.biblatex_entry(book, "k")
    assert entry.startswith("@book{k,") and "publisher = {Wiley}" in entry and "isbn = {9780471878766}" in entry
    assert [(i["type"], i.get("container-title")) for i in (citations.csl_item(r, "k") for r in (conf, chapter, book))] \
        == [("paper-conference", "CVPR"), ("chapter", "Handbook of X"), ("book", None)]


def test_a_presentation_names_its_meeting():
    talk = {"title": "A talk", "authors": ["Plato"], "year": "2020", "venue": "APS March Meeting",
            "kind": "presentation"}
    item = citations.csl_item(talk, "k")
    assert item["type"] == "speech" and "container-title" not in item
    assert item["event-title"] == item["event"] == "APS March Meeting"  # older styles read the old name
    assert item["author"] == [{"literal": "Plato"}]
    assert dict(_ris(talk))["TY"] == "SLIDE" and ("T2", "APS March Meeting") in _ris(talk)


def test_a_date_of_another_year_is_not_cited():
    meta = {"title": "T", "authors": ["A B"], "year": "2021", "date": "2020-03-05", "venue": "J"}
    rec = citations.record({"meta": meta})
    assert "date" not in rec
    assert citations.csl_item(rec, "k")["issued"] == {"date-parts": [[2021]]}
    assert dict(_ris(rec))["DA"] == "2021///" and "date = {2021}" in citations.biblatex_entry(rec, "k")
    assert "month" not in bibtex.build_entry(meta)
    # A year that is no number is printed as it is written.
    item = citations.csl_item({"title": "T", "year": "in press"}, "k")
    assert item["issued"] == {"literal": "in press"}
    assert ("DA", "///in press") in _ris({"title": "T", "year": "in press"})


def test_the_address_is_the_records_else_the_pages_and_only_http():
    meta = {"title": "T", "year": "2020"}
    assert citations.record({"meta": meta, "web_url": "https://example.org/a"})["url"] == "https://example.org/a"
    assert citations.record({"meta": {**meta, "url": "https://doi.org/x"},
                             "web_url": "https://example.org/a"})["url"] == "https://doi.org/x"
    bad = citations.record({"meta": {**meta, "url": 'javascript:alert(1)" onmouseover="x'}, "web_url": "/relative"})
    assert "url" not in bad and "URL" not in citations.csl_item(bad, "k")
    assert citations.record({"bibtex": "@misc{k}"}) is None


def test_ris_writes_every_value_on_one_line():
    lines = citations.ris_entry({**ARTICLE, "title": "Two\nlines", "authors": ["Ada\r\nLovelace"]}, "k").split("\r\n")
    assert lines[0] == "TY  - JOUR" and lines[-2:] == ["ER  - ", ""]
    assert all(re.fullmatch(r"[A-Z][A-Z0-9]  - .*", line) for line in lines[:-1])
    assert "TI  - Two lines" in lines and "AU  - Lovelace, Ada" in lines and "ID  - k" in lines
    assert ("SP", "052101") in _ris(ARTICLE) and ("EP", "052110") in _ris(ARTICLE)
    # A range as LaTeX writes it, or with a figure dash, has its two ends too.
    for pages in ("436--444", "436\u2012444"):
        rec = {"title": "T", "pages": pages}
        assert {("SP", "436"), ("EP", "444")} <= set(_ris(rec)) and citations.csl_item(rec, "k")["page"] == "436-444"
    assert ("AU", "King, Martin Luther, Jr.") in _ris(ARTICLE) and ("AU", "ATLAS Collaboration") in _ris(ARTICLE)


def test_a_biblatex_article_stays_an_article():
    entry = citations.biblatex_entry(ARTICLE, "chase2008")
    assert entry == (
        "@article{chase2008,\n"
        "  title = {Collective processes of an ensemble of spin-1/2 particles},\n"
        "  author = {Chase, Bradley A. and van der Berg, Jan and King, Jr., Martin Luther and {ATLAS Collaboration}},\n"
        "  journaltitle = {Physical Review A},\n"
        "  shortjournal = {Phys. Rev. A},\n"
        "  volume = {78},\n"
        "  number = {5},\n"
        "  pages = {052101--052110},\n"
        "  date = {2008-11-03},\n"
        "  doi = {10.1103/PhysRevA.78.052101},\n"
        "  url = {https://journals.aps.org/pra/abstract/10.1103/PhysRevA.78.052101}\n"
        "}")
    # Without its journal an article is a @misc, as in BibTeX.
    assert citations.biblatex_entry({"title": "T", "kind": "journalArticle"}, "k").startswith("@misc{k,")


# --- cases where Gamma differed from Zotero (the oracle: public Zotero group
# libraries, each item's own Web API data and Zotero's output for it) --------

def _creator(first, last, creator_type="author"):
    return {"creatorType": creator_type, "firstName": first, "lastName": last}


def test_a_surname_keeps_the_split_zotero_gave_it():
    """2228317-MJAQFMSC and others: a surname of two words or with a
    capitalized particle. Zotero cites "Pereira Campos, J. F. (2021)" and
    "(Den Hengst, 2023)"; a record that joined the two fields as "First
    Last" split again at the last word, "Campos, J. F. P." and "Hengst, F. D."."""
    item = {"itemType": "thesis", "title": "The dynamics of data donation: privacy risk, mobility data, and the smart city",
            "creators": [_creator("Jorge Fernando", "Pereira Campos")], "date": "2021",
            "university": "University of St Andrews"}
    meta = zotero_fields.meta_from_item(item)
    assert meta["authors"] == ["Pereira Campos, Jorge Fernando"]
    assert citations.csl_item(meta, "k")["author"] == [{"family": "Pereira Campos", "given": "Jorge Fernando"}]
    assert ("AU", "Pereira Campos, Jorge Fernando") in _ris(meta)
    assert "author = {Pereira Campos, Jorge Fernando}" in citations.biblatex_entry(meta, "k")
    entry = bibtex.build_entry(meta)  # BibTeX reads "Last, First" itself; the key is the one it always was
    assert entry.startswith("@phdthesis{campos2021,") and "author = {Pereira Campos, Jorge Fernando}" in entry
    # Back to Zotero as it came, and read back the same.
    creators = zotero_fields.fields_from_meta({"kind": "thesis", "authors": meta["authors"]})["creators"]
    assert creators == item["creators"]
    for first, last in (("Floris", "Den Hengst"), ("L.", "Van Waerbeke"), ("D.", "Le Touzé"),
                        ("G.", "Verdoes Kleijn"), ("I.", "Fenech Conti")):
        name = zotero_fields.meta_from_item({"itemType": "journalArticle", "creators": [_creator(first, last)]})
        assert citations._csl_name(name["authors"][0]) == {"family": last, "given": first}
    # A name that splits back by itself stays "First Last", a particle Zotero
    # keeps with the given name too (Gamma cites it under its surname).
    assert [zotero_fields.meta_from_item({"itemType": "book", "creators": [_creator(f, l)]})["authors"][0]
            for f, l in (("Ada", "Lovelace"), ("Frank van", "Harmelen"), ("Martin Luther", "King, Jr."))] == \
        ["Ada Lovelace", "Frank van Harmelen", "Martin Luther King, Jr."]


def test_particles_are_split_off_as_zoteros_csl_json_splits_them():
    """4989916-FPEAFHZP: Zotero's CSL JSON gives "Frank van Harmelen" as
    family "Harmelen" and particle "van" (citeproc-js's parseParticles), so
    Chicago prints "Harmelen, Frank van" and APSA sorts it under H. A
    capitalized "Den" is part of the surname, as citeproc reads it."""
    assert citations._csl_name("Frank van Harmelen") == {"family": "Harmelen", "given": "Frank",
                                                          "non-dropping-particle": "van"}
    assert citations._csl_name("Jean d'Alembert") == {"family": "Alembert", "given": "Jean",
                                                       "non-dropping-particle": "d'"}
    assert citations._csl_name("Busch, Jan Luca van den") == {"family": "Busch", "given": "Jan Luca",
                                                               "dropping-particle": "van den"}
    assert citations._csl_name("Den Hengst, Floris") == {"family": "Den Hengst", "given": "Floris"}
    assert citations._csl_name('"van Gogh", Vincent') == {"family": "van Gogh", "given": "Vincent"}  # quoted: whole
    assert citations._csl_name("Smith, John, Esq.") == {"family": "Smith", "given": "John", "suffix": "Esq."}


def test_an_office_and_a_bureau_are_organisations():
    """2228317-Y6TGL8UN, Zotero's single-field "International Labor Office":
    APA cites "International Labor Office. (2007)", not "Office, I. L."."""
    item = {"itemType": "report", "title": "Equality at work: Tackling the challenge", "date": "2007",
            "creators": [{"creatorType": "author", "name": "International Labor Office"}],
            "institution": "International Labor Organization"}
    meta = zotero_fields.meta_from_item(item)
    assert citations.csl_item(meta, "k")["author"] == [{"literal": "International Labor Office"}]
    assert ("AU", "International Labor Office") in _ris(meta)
    assert "author = {{International Labor Office}}" in citations.biblatex_entry(meta, "k")
    for org in ("U.S. Census Bureau", "Ministry of Health", "European Commission", "Dark Energy Survey",
                "International Monetary Fund"):
        assert citations._csl_name(org) == {"literal": org}


def test_ris_writes_no_m1_and_dates_a_year_alone():
    """4989025-3JQGTYTP and the other arXiv preprints: Zotero's RIS writes
    no M1 (its import reads one back as a journal article's issue,
    "arXiv:2508.06760"), and DA for every dated item, "2021///"."""
    rec = {"title": "T", "authors": ["A B"], "year": "2025", "arxiv_id": "2508.06760", "kind": "preprint"}
    tags = _ris(rec)
    assert "M1" not in dict(tags) and ("DA", "2025///") in tags and ("PY", "2025") in tags
    assert [tag for tag, _ in tags].index("DA") < [tag for tag, _ in tags].index("PY")


def test_biblatex_types_and_fields_as_zotero_writes_them():
    """5098525-PJ36VREA, a talk: Zotero's BibLaTeX type is @unpublished;
    4989916-X2K8TXEJ, a document whose publisher is its only venue;
    4989916-KE8FBP7F and R9UH4NJH, a chapter's volume and a conference
    paper's ISBN, which CSL and RIS carried and BibLaTeX dropped."""
    talk = {"title": "Constraints on Societies Engaged in Relativistic Interstellar Travel", "authors": ["Kevin Knuth"],
            "year": "2019", "venue": "AAPC 2019", "kind": "presentation", "date": "2019-04-22"}
    entry = citations.biblatex_entry(talk, "knuth2019")
    assert entry.startswith("@unpublished{knuth2019,") and "howpublished = {AAPC 2019}" in entry
    doc = {"title": "Knowledge Representation for Clinical Guidelines", "authors": ["Carretta Zamborlini, V."],
           "year": "2017", "publisher": "Vrije Universiteit Amsterdam", "kind": "document"}
    assert "howpublished = {Vrije Universiteit Amsterdam}" in citations.biblatex_entry(doc, "k")
    chapter = {"title": "A Contextualised Semantics for owl: sameAs", "authors": ["W. G. J. Beek"], "year": "2016",
               "venue": "The Semantic Web, ESWC 2016", "volume": "9678", "pages": "405–419",
               "publisher": "Springer/Verlag", "isbn": "9783319341286", "kind": "bookSection"}
    entry = citations.biblatex_entry(chapter, "beek2016")
    assert "title = {A Contextualised Semantics for owl: {sameAs}}" in entry
    assert "booktitle = {The Semantic Web, {ESWC} 2016}" in entry and "volume = {9678}" in entry
    conf = {"title": "Inductive Entity Representations from Text via Link Prediction", "authors": ["Daniel Daza"],
            "year": "2021", "venue": "Proceedings of the Web Conference 2021", "pages": "798–808",
            "publisher": "Association for Computing Machinery", "isbn": "9781450383127", "kind": "conferencePaper"}
    assert "isbn = {9781450383127}" in citations.biblatex_entry(conf, "k")
