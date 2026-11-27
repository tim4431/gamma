"""The fields a citation needs beside the ones every record has
(gamma/meta_fields.py: issue, journal abbreviation, date, address), as each
writer of a record takes them from its source: doi.org's CSL JSON, a
Crossref search hit, an arXiv entry, a web page's citation tags."""

import json
import urllib.parse
import xml.etree.ElementTree as ET

from gamma import meta_fields
from gamma.routers import metadata


def test_a_registry_record_keeps_the_issue_the_abbreviation_and_the_day(monkeypatch):
    """doi.org's record names the issue, the journal's short title and the
    day of issue: the record keeps all three, the year as before. A record
    without them (dated by its year, its journal's short title its title)
    has none of the keys, so its entry is the one of before."""
    csl = {"type": "journal-article", "title": "Near-Unitary Spin Squeezing in 171Yb",
           "author": [{"given": "Boris", "family": "Braverman"}], "container-title": "Physical Review Letters",
           "container-title-short": "Phys. Rev. Lett.", "volume": "122", "issue": "22", "page": "223203",
           "issued": {"date-parts": [[2019, 6, 4]]}}
    short = {"type": "journal-article", "title": "A letter", "author": [{"given": "Ann", "family": "Smith"}],
             "container-title": "Nature", "container-title-short": "Nature", "issued": {"date-parts": [[1986]]}}

    def doi_org(record):
        return lambda url, accept="", timeout=20: json.dumps(record).encode() if "csl" in accept else b""

    monkeypatch.setattr(metadata, "_http_get", doi_org(csl))
    meta, _ = metadata._fetch_doi("10.1103/physrevlett.122.223203")
    monkeypatch.setattr(metadata, "_http_get", doi_org(short))
    plain, _ = metadata._fetch_doi("10.1000/letter")
    assert (meta["year"], meta["issue"], meta["journal_abbr"], meta["date"]) == (
        "2019", "22", "Phys. Rev. Lett.", "2019-06-04")
    assert plain["year"] == "1986" and not set(meta_fields.NEW_FIELDS) & set(plain)


def test_a_crossref_hit_asks_for_and_keeps_the_issue_and_month(monkeypatch):
    asked = []

    def crossref(url, accept="", timeout=20):
        asked.append(urllib.parse.unquote(url))
        return json.dumps({"message": {"items": [{
            "DOI": "10.1103/PhysRevA.78.052101", "type": "journal-article", "title": ["A paper"],
            "container-title": ["Physical Review A"], "short-container-title": ["Phys. Rev. A"], "volume": "78",
            "issue": "5", "article-number": "052101", "issued": {"date-parts": [[2008, 11]]}}]}}).encode()
    monkeypatch.setattr(metadata, "_http_get", crossref)
    [hit] = metadata._crossref_search("A paper")
    assert (hit["issue"], hit["journal_abbr"], hit["date"], hit["pages"]) == ("5", "Phys. Rev. A", "2008-11", "052101")
    fields = asked[0].split("&select=")[1].split("&")[0].split(",")
    assert {"issue", "short-container-title"} <= set(fields)


def test_an_arxiv_preprint_is_dated_by_its_first_version():
    """A published paper's date is its journal's, which arXiv does not
    give: one with a journal reference keeps the year alone."""
    atom = ('<entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
            "<id>http://arxiv.org/abs/1706.03762v7</id>"
            "<title>Attention Is All You Need</title><published>2017-06-12T17:57:34Z</published>"
            "<author><name>Ashish Vaswani</name></author>{}</entry>")
    meta = metadata._arxiv_entry_meta(ET.fromstring(atom.format("")))
    assert (meta["arxiv_id"], meta["year"], meta["date"]) == ("1706.03762", "2017", "2017-06-12")
    published = metadata._arxiv_entry_meta(ET.fromstring(atom.format(
        "<arxiv:journal_ref>Adv. Neural Inf. Process. Syst. 30 (2017)</arxiv:journal_ref>")))
    assert (published["kind"], published["year"]) == ("journalArticle", "2017") and "date" not in published


def test_a_web_pages_record_keeps_its_date_issue_and_abbreviation():
    """What the Connector read from a page's Highwire tags (pageRecord.js):
    the full date, the tag as the publisher wrote it, while it is the
    year's, the issue, the journal's abbreviation; the lookup's page record
    carries them, each within its limit. A date of another year is not
    kept; a date with no year beside it gives the year."""
    record = {"title": "A paper", "authors": ["Ann Smith"], "year": "2019", "date": "2019/03/14",
              "venue": "Physical Review A", "issue": "3", "journal_abbr": "Phys. Rev. A", "kind": "journalArticle"}
    web = metadata.web_record(record)
    assert (web["date"], web["issue"], web["journal_abbr"]) == ("2019-03-14", "3", "Phys. Rev. A")
    page = metadata._page_record(web)
    assert (page["year"], page["date"], page["issue"], page["journal_abbr"]) == (
        "2019", "2019-03-14", "3", "Phys. Rev. A")
    assert "date" not in metadata.web_record({**record, "date": "2018-12-01"})
    assert metadata.web_record({**record, "year": "", "date": "2019-03"})["year"] == "2019"
    for raw, date in (("2019/3/14", "2019-03-14"), ("2019/3/14 10:00", "2019-03-14"), ("2019-3", "2019-03"),
                      ("2019-03-14T10:00:00Z", "2019-03-14"), ("2019", None), ("March 2019", "2019-03"),
                      ("Spring 2019", None)):
        assert metadata.web_record({**record, "date": raw}).get("date") == date, raw
    long = metadata._page_record(metadata.web_record({**record, "issue": "9" * 60, "journal_abbr": "J" * 400}))
    assert (len(long["issue"]), len(long["journal_abbr"])) == (40, 300)
    older = metadata.web_record({k: v for k, v in record.items() if k not in meta_fields.NEW_FIELDS})
    assert not set(meta_fields.NEW_FIELDS) & set(metadata._page_record(older))  # an older Connector's record


def test_a_web_pages_last_first_names_keep_the_split_their_tags_gave():
    """Highwire writes citation_author "Last, First": a name whose surname
    "First Last" reads back keeps that form, one whose surname would not
    ("Pereira Campos", "Den Hengst") keeps its comma (names.join_name); a
    name an older Connector turned around, or one of more commas, stays."""
    record = {"title": "A paper", "authors": ["Lovelace, Ada", "Pereira Campos, Jorge Fernando", "Den Hengst, Floris",
                                              "Ann Smith", "King, Jr., Martin Luther", "University of California, Berkeley"]}
    assert metadata.web_record(record)["authors"] == [
        "Ada Lovelace", "Pereira Campos, Jorge Fernando", "Den Hengst, Floris", "Ann Smith", "King, Jr., Martin Luther",
        "University of California, Berkeley"]
    # a comma with no space after it reads as Zotero's Embedded Metadata reads it (``/, ?/``)
    assert metadata.web_record({**record, "authors": ["Lovelace,Ada", "Pereira Campos,Jorge Fernando"]})[
        "authors"] == ["Ada Lovelace", "Pereira Campos, Jorge Fernando"]
