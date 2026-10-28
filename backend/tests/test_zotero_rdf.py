"""Reading a Zotero RDF export (gamma/zotero_import.py) in the shapes Zotero
writes it (its "Zotero RDF" translator, ``generateItem``): records, the
pass-through a later Zotero export writes back (``properties.zotero_rdf``),
notes as block trees, standalone notes, and the plan of an export zip — its
PDFs hashed once, as they stream, and its snapshot folders warned of once."""

import html
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from gamma import zotero_import
from gamma.storage import SPOOL_CHUNK, content_digest
from gamma.zotero_export import standalone_note_html
from gamma.zotero_import import html_note_text, parse_zotero_rdf, plan_zotero_archive, same_work
from gamma.zotero_notes import note_tree

_NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "z": "http://www.zotero.org/namespaces/export#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "bib": "http://purl.org/net/biblio#",
    "prism": "http://prismstandard.org/namespaces/1.2/basic/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "link": "http://purl.org/rss/1.0/modules/link/",
}


def q(name: str) -> str:
    """``"dc:title"`` → ElementTree's ``{namespace}title``."""
    prefix, local = name.split(":")
    return f"{{{_NS[prefix]}}}{local}"


CHILD_NOTE = (
    '<div data-schema-version="9"><h1>Reading notes</h1>'
    '<p>See <a href="https://example.org/a b">the site</a> and <span class="math">$E=mc^2$</span>.</p>'
    "<ul><li>outer<ul><li>inner</li></ul></li></ul>"
    "<ol><li><p>first</p></li><li><p>second</p></li></ol>"
    '<p><img src="data:image/png;base64,iVBORw0KGgo="/></p></div>')
STANDALONE_NOTE = ('<div data-schema-version="9"><h2>Ideas <em>later</em></h2>'
                   "<p>One</p><ul><li>two</li></ul></div>")
ARTICLE = "https://journals.aps.org/prl/abstract/10.1103/PhysRevLett.122.101"


def person(surname, given=None):
    given = f"<foaf:givenName>{given}</foaf:givenName>" if given else ""
    return f"<rdf:li><foaf:Person><foaf:surname>{surname}</foaf:surname>{given}</foaf:Person></rdf:li>"


def rdf_doc(*elements: str) -> str:
    return ("""<rdf:RDF
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:z="http://www.zotero.org/namespaces/export#"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:bib="http://purl.org/net/biblio#"
 xmlns:foaf="http://xmlns.com/foaf/0.1/"
 xmlns:link="http://purl.org/rss/1.0/modules/link/"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:vcard="http://nwalsh.com/rdf/vCard#"
 xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/">
""" + "\n".join(elements) + "\n</rdf:RDF>\n")


# A library as Zotero 7 exports it: a journal article on an ISSN record (its
# issue, ISSN, abbreviation and series there), a book in a series, a web
# page, a preprint, a bare article, a standalone note in a collection.
LIBRARY = rdf_doc(f"""
    <bib:Article rdf:about="{ARTICLE}">
        <z:itemType>journalArticle</z:itemType>
        <dcterms:isPartOf rdf:resource="urn:issn:0031-9007"/>
        <bib:authors>
            <rdf:Seq>{person("van der Berg", "Jan")}{person("ATLAS Collaboration")}</rdf:Seq>
        </bib:authors>
        <bib:editors>
            <rdf:Seq>{person("Editor", "Erin")}</rdf:Seq>
        </bib:editors>
        <link:link rdf:resource="#item_11"/>
        <dcterms:isReferencedBy rdf:resource="#item_12"/>
        <dc:subject>quantum</dc:subject>
        <dc:subject>
           <z:AutomaticTag>
              <rdf:value>Physics - Optics</rdf:value>
           </z:AutomaticTag>
        </dc:subject>
        <dc:subject>
           <dcterms:LCC>
              <rdf:value>QC350 .B47</rdf:value>
           </dcterms:LCC>
        </dc:subject>
        <dc:title>Light in a box</dc:title>
        <dcterms:abstract>We put light in a box.</dcterms:abstract>
        <dc:date>2019-03-14</dc:date>
        <z:language>en</z:language>
        <z:shortTitle>Light</z:shortTitle>
        <z:libraryCatalog>APS</z:libraryCatalog>
        <dc:identifier>
            <dcterms:URI>
               <rdf:value>{ARTICLE}</rdf:value>
            </dcterms:URI>
        </dc:identifier>
        <dcterms:dateSubmitted>2024-01-02 03:04:05</dcterms:dateSubmitted>
        <dc:description>Citation Key: berg2019light
arXiv: 1903.01234
PMID: 12345678</dc:description>
        <bib:pages>101-105</bib:pages>
    </bib:Article>
    <bib:Journal rdf:about="urn:issn:0031-9007">
        <dcterms:isPartOf>
            <bib:Series>
                <dc:title>Letters series</dc:title>
            </bib:Series>
        </dcterms:isPartOf>
        <prism:volume>122</prism:volume>
        <dc:title>Physical Review Letters</dc:title>
        <dc:identifier>DOI 10.1103/PhysRevLett.122.101</dc:identifier>
        <prism:number>10</prism:number>
        <dcterms:alternative>Phys. Rev. Lett.</dcterms:alternative>
        <dc:identifier>ISSN 0031-9007</dc:identifier>
    </bib:Journal>
    <z:Attachment rdf:about="#item_11">
        <z:itemType>attachment</z:itemType>
        <rdf:resource rdf:resource="files/11/Berg - 2019 - Light in a box.pdf"/>
        <dc:title>Full Text PDF</dc:title>
        <link:type>application/pdf</link:type>
    </z:Attachment>
    <bib:Memo rdf:about="#item_12">
        <rdf:value>{html.escape(CHILD_NOTE, quote=False)}</rdf:value>
    </bib:Memo>
    <bib:Book rdf:about="urn:isbn:978-3-16-148410-0">
        <z:itemType>book</z:itemType>
        <dcterms:isPartOf>
            <bib:Series>
                <dc:title>Lecture Notes in Physics</dc:title>
                <dc:identifier>42</dc:identifier>
            </bib:Series>
        </dcterms:isPartOf>
        <dc:publisher>
            <foaf:Organization>
                <vcard:adr><vcard:Address><vcard:locality>Berlin</vcard:locality></vcard:Address></vcard:adr>
                <foaf:name>Springer</foaf:name>
            </foaf:Organization>
        </dc:publisher>
        <bib:authors><rdf:Seq>{person("Optiker", "Olga")}</rdf:Seq></bib:authors>
        <dc:title>Optics for all</dc:title>
        <dc:date>2010</dc:date>
        <dc:identifier>ISBN 978-3-16-148410-0</dc:identifier>
        <prism:edition>2</prism:edition>
        <z:numPages>300</z:numPages>
    </bib:Book>
    <bib:Document rdf:about="https://example.org/post">
        <z:itemType>webpage</z:itemType>
        <dcterms:isPartOf>
           <z:Website>
              <dc:title>Example Blog</dc:title>
           </z:Website>
        </dcterms:isPartOf>
        <link:link rdf:resource="#item_21"/>
        <dc:title>A post</dc:title>
        <dc:date>2023</dc:date>
        <dc:identifier>
            <dcterms:URI>
               <rdf:value>https://example.org/post</rdf:value>
            </dcterms:URI>
        </dc:identifier>
        <dcterms:dateSubmitted>2024-05-06 07:08:09</dcterms:dateSubmitted>
    </bib:Document>
    <z:Attachment rdf:about="#item_21">
        <z:itemType>attachment</z:itemType>
        <z:path rdf:resource="files/21/post.html"/>
        <dc:identifier>
            <dcterms:URI>
               <rdf:value>https://example.org/post</rdf:value>
            </dcterms:URI>
        </dc:identifier>
        <dc:title>Snapshot</dc:title>
        <z:linkMode>1</z:linkMode>
        <link:type>text/html</link:type>
        <link:charset>utf-8</link:charset>
    </z:Attachment>
    <rdf:Description rdf:about="#item_31">
        <z:itemType>preprint</z:itemType>
        <dc:publisher>
            <foaf:Organization>
               <foaf:name>arXiv</foaf:name>
            </foaf:Organization>
        </dc:publisher>
        <dc:title>A preprint</dc:title>
        <dc:date>2021</dc:date>
        <z:archiveID>arXiv:2101.01234</z:archiveID>
    </rdf:Description>
    <bib:Article rdf:about="#item_51">
        <z:itemType>journalArticle</z:itemType>
        <dc:title>Bare</dc:title>
        <dc:date>2020</dc:date>
        <dc:description>Citation Key: bare2020</dc:description>
    </bib:Article>
    <bib:Memo rdf:about="#item_41">
        <z:itemType>note</z:itemType>
        <dc:subject>todo</dc:subject>
        <dc:subject>
           <z:AutomaticTag>
              <rdf:value>auto</rdf:value>
           </z:AutomaticTag>
        </dc:subject>
        <rdf:value>{html.escape(STANDALONE_NOTE, quote=False)}</rdf:value>
    </bib:Memo>
    <z:Collection rdf:about="#collection_1">
        <dc:title>Lab</dc:title>
        <dcterms:hasPart rdf:resource="#collection_2"/>
        <dcterms:hasPart rdf:resource="#item_41"/>
    </z:Collection>
    <z:Collection rdf:about="#collection_2">
        <dc:title>Optics</dc:title>
        <dcterms:hasPart rdf:resource="{ARTICLE}"/>
    </z:Collection>""")


def by_key(text=LIBRARY):
    return {item["key"]: item for item in parse_zotero_rdf(text)}


def elements(rdf, key):
    """The passed-through elements under ``key``, parsed back."""
    return [ET.fromstring(s) for s in rdf.get(key, [])]


def tags_of(els):
    return [el.tag for el in els]


def _pdf(text=b"a page"):
    """A file that starts as a PDF does: all the plan reads of one."""
    return b"%PDF-1.4\n" + text + b"\n%%EOF\n"


def export_zip(rdf=LIBRARY, files=None, top="Exported Items"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{top}/{top}.rdf", rdf)
        for path, data in (files or {}).items():
            zf.writestr(f"{top}/{path}", data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


# --- records ----------------------------------------------------------------------

def test_same_work_goes_by_doi_when_both_have_one_else_by_title():
    assert same_work({"doi": "10.1/ABC", "title": "One"}, {"doi": "10.1/abc", "title": "Other"})
    assert not same_work({"doi": "10.1/a", "title": "Same"}, {"doi": "10.1/b", "title": "Same"})
    assert same_work({"doi": "10.1/a", "title": "Café — the  Paper!"}, {"doi": "", "title": "CAFÉ: The paper"})
    assert same_work({"title": "Ｆｕｌｌ width"}, {"title": "full WIDTH"})  # NFKC
    assert not same_work({"title": ""}, {"title": ""})
    assert not same_work({"title": "?!"}, {"title": "..."})  # nothing left to compare
    assert not same_work({"title": "Light in a box"}, {"title": "Light in a bag"})
    assert not same_work(None, {"title": "x"})


def test_a_journal_article_reads_its_record_from_the_issn_record():
    """Venue, volume and DOI come from the journal record the article names;
    the series that record is in is no venue. Names read as given, a
    particle and an organisation alike; the arXiv id from an Extra line."""
    item = by_key()[ARTICLE]
    meta = item["meta"]
    assert meta["venue"] == "Physical Review Letters" and meta["volume"] == "122"
    assert meta["doi"] == "10.1103/PhysRevLett.122.101"
    assert meta["authors"] == ["Jan van der Berg", "ATLAS Collaboration"]
    assert meta["year"] == "2019" and meta["pages"] == "101-105"
    assert meta["arxiv_id"] == "1903.01234"
    assert item["cite_key"] == "berg2019light"
    assert item["folders"] == [["Lab", "Optics"]]
    assert item["standalone_note"] is False


def test_a_call_number_is_no_tag():
    """A dc:subject holding a typed node other than an automatic tag (Zotero
    writes the call number as a dcterms:LCC) is no tag; it is kept for the
    next Zotero export instead."""
    item = by_key()[ARTICLE]
    assert item["tags"] == ["quantum", "Physics - Optics"]
    [subject] = [el for el in elements(item["rdf"], "item") if el.tag == q("dc:subject")]
    assert subject.findtext(f"{q('dcterms:LCC')}/{q('rdf:value')}") == "QC350 .B47"


def test_what_meta_does_not_hold_passes_through():
    """Each element of the item and of its journal record that no field
    reads and the export does not write back is kept, in order and whole:
    editors, abstract, language, short title, catalogue, URL, access date,
    call number; the record's series, issue, abbreviation and ISSN. With
    them the full date, the names as Zotero split them and the Extra text."""
    rdf = by_key()[ARTICLE]["rdf"]
    assert rdf["v"] == 1 and rdf["work"] == {"title": "Light in a box", "doi": "10.1103/PhysRevLett.122.101"}
    assert tags_of(elements(rdf, "item")) == [q(t) for t in (
        "bib:editors", "dc:subject", "dcterms:abstract", "z:language", "z:shortTitle", "z:libraryCatalog",
        "dc:identifier", "dcterms:dateSubmitted")]
    editors, *_ = elements(rdf, "item")
    assert [p.findtext(q("foaf:surname")) for p in editors.iter(q("foaf:Person"))] == ["Editor"]
    url = elements(rdf, "item")[6]
    assert url.findtext(f"{q('dcterms:URI')}/{q('rdf:value')}").strip() == ARTICLE
    container = elements(rdf, "container")
    assert tags_of(container) == [q(t) for t in ("dcterms:isPartOf", "prism:number", "dcterms:alternative",
                                                 "dc:identifier")]
    assert container[0].findtext(f"{q('bib:Series')}/{q('dc:title')}").strip() == "Letters series"
    assert [el.text for el in container[1:]] == ["10", "Phys. Rev. Lett.", "ISSN 0031-9007"]
    assert rdf["date"] == "2019-03-14"
    assert rdf["extra"] == "Citation Key: berg2019light\narXiv: 1903.01234\nPMID: 12345678"
    assert rdf["authors"]["names"] == ["Jan van der Berg", "ATLAS Collaboration"]
    people = ET.fromstring(rdf["authors"]["xml"]).iter(q("foaf:Person"))
    assert [(p.findtext(q("foaf:surname")), p.findtext(q("foaf:givenName"))) for p in people] == [
        ("van der Berg", "Jan"), ("ATLAS Collaboration", None)]
    # a Gamma type: the export writes Zotero's own classes for it
    assert not {"type", "class", "container_class"} & set(rdf)
    assert json.loads(json.dumps(rdf)) == rdf


def test_mapped_elements_are_not_passed_through():
    """What the record, tags, attachments and notes hold is not kept twice:
    the item's type, title, authors, date, pages, Extra, tags, links and
    notes, the journal it is part of, and the record's title, volume and
    DOI."""
    rdf = by_key()[ARTICLE]["rdf"]
    kept = tags_of(elements(rdf, "item")) + tags_of(elements(rdf, "container"))
    for name in ("z:itemType", "dc:title", "bib:authors", "dc:date", "bib:pages", "dc:description",
                 "link:link", "dcterms:isReferencedBy", "prism:volume"):
        assert q(name) not in kept, name
    assert [el for el in elements(rdf, "item") if el.tag == q("dcterms:isPartOf")] == []
    texts = [el.text for el in elements(rdf, "container") + elements(rdf, "item")]
    assert "DOI 10.1103/PhysRevLett.122.101" not in texts


def test_a_publishers_place_goes_back_while_the_publisher_does():
    """Zotero writes a book's place in its publisher element; the pass-through
    keeps the element, and the export writes it back while the record names
    the same publisher, else a publisher of its own."""
    from gamma.zotero_export import build_rdf

    text = rdf_doc("""<bib:Book rdf:about="urn:isbn:9780262033848"><z:itemType>book</z:itemType>
        <dc:publisher><foaf:Organization>
            <vcard:adr><vcard:Address><vcard:locality>Cambridge, MA</vcard:locality></vcard:Address></vcard:adr>
            <foaf:name>MIT Press</foaf:name>
        </foaf:Organization></dc:publisher>
        <dc:title>Algorithms</dc:title><dc:identifier>ISBN 978-0-262-03384-8</dc:identifier></bib:Book>""")
    [item] = parse_zotero_rdf(text)
    assert item["meta"]["publisher"] == "MIT Press" and item["rdf"]["publisher"]["name"] == "MIT Press"

    def place(meta):
        out = build_rdf([{"key": item["key"], "title": item["title"], "meta": meta, "rdf": item["rdf"],
                          "cite_key": "", "tags": [], "folders": []}])
        [back] = parse_zotero_rdf(out)
        return ET.fromstring(out).findtext(".//{http://nwalsh.com/rdf/vCard#}locality"), back["meta"]["publisher"]

    assert place(item["meta"]) == ("Cambridge, MA", "MIT Press")
    assert place({**item["meta"], "publisher": "Penguin"}) == (None, "Penguin")
    [plain] = parse_zotero_rdf(rdf_doc("""<bib:Book rdf:about="#b"><z:itemType>book</z:itemType><dc:title>B</dc:title>
        <dc:publisher><foaf:Organization><foaf:name>Penguin</foaf:name></foaf:Organization></dc:publisher></bib:Book>"""))
    assert "publisher" not in (plain["rdf"] or {})  # the name alone: the export writes it anyway


def test_a_series_is_no_container():
    """A book names its series with dcterms:isPartOf, as an article names
    its journal: the series is no venue and goes through whole, as do the
    book's edition and page count. Its ISBN is the record's; the
    publisher's place, inside the publisher Zotero writes, is not kept."""
    item = by_key()["urn:isbn:978-3-16-148410-0"]
    assert item["meta"]["venue"] == "" and item["meta"]["isbn"] == "9783161484100"
    assert item["meta"]["publisher"] == "Springer" and item["meta"]["kind"] == "book"
    rdf = item["rdf"]
    assert tags_of(elements(rdf, "item")) == [q("dcterms:isPartOf"), q("prism:edition"), q("z:numPages")]
    series = elements(rdf, "item")[0]
    assert series.findtext(f"{q('bib:Series')}/{q('dc:title')}").strip() == "Lecture Notes in Physics"
    assert "date" not in rdf and "container" not in rdf


def test_a_web_page_keeps_its_type_classes_and_url():
    """Gamma reads a web page as a document; its own type, its RDF class and
    its website's class are kept for the export, and its URL."""
    item = by_key()["https://example.org/post"]
    assert item["meta"]["kind"] == "document" and item["meta"]["venue"] == "Example Blog"
    rdf = item["rdf"]
    assert rdf["type"] == "webpage"
    assert rdf["class"] == q("bib:Document") and rdf["container_class"] == q("z:Website")
    assert tags_of(elements(rdf, "item")) == [q("dc:identifier"), q("dcterms:dateSubmitted")]
    assert "date" not in rdf and "container" not in rdf


def test_a_preprint_reads_its_arxiv_id_from_its_archive_id():
    item = by_key()["#item_31"]
    assert item["meta"]["arxiv_id"] == "2101.01234" and item["meta"]["publisher"] == "arXiv"
    assert tags_of(elements(item["rdf"], "item")) == [q("z:archiveID")]  # Zotero's, kept as written
    assert "type" not in item["rdf"] and "class" not in item["rdf"]


@pytest.mark.parametrize("extra,arxiv", [
    ("arXiv: hep-th/9901001", "hep-th/9901001"),
    ("Some note\narXiv:2101.01234v2\n", "2101.01234"),
    ("see arXiv: 2101.01234 for the preprint", ""),  # a line of its own only
])
def test_an_arxiv_line_in_extra_names_the_preprint(extra, arxiv):
    rdf = rdf_doc(f"""<bib:Article rdf:about="#item_1"><z:itemType>journalArticle</z:itemType>
        <dc:title>T</dc:title><dc:description>{extra}</dc:description></bib:Article>""")
    [item] = parse_zotero_rdf(rdf)
    assert item["meta"]["arxiv_id"] == arxiv


def test_the_arxiv_abstract_url_is_written_back_not_kept():
    """The export writes a preprint's abstract URL from its arXiv id, so
    that one is not kept; another URL is."""
    rdf = rdf_doc("""<rdf:Description rdf:about="#item_1"><z:itemType>preprint</z:itemType>
        <dc:title>P</dc:title>
        <dc:identifier><dcterms:URI><rdf:value>https://arxiv.org/abs/2101.01234</rdf:value></dcterms:URI></dc:identifier>
        <dc:identifier><dcterms:URI><rdf:value>https://example.org/mirror</rdf:value></dcterms:URI></dc:identifier>
        <dc:identifier>DOI 10.48550/arXiv.2101.01234</dc:identifier>
        </rdf:Description>""")
    [item] = parse_zotero_rdf(rdf)
    assert item["meta"]["arxiv_id"] == "2101.01234" and item["meta"]["doi"] == "10.48550/arXiv.2101.01234"
    [kept] = elements(item["rdf"], "item")
    assert kept.findtext(f"{q('dcterms:URI')}/{q('rdf:value')}") == "https://example.org/mirror"


def test_a_record_the_export_writes_whole_has_no_pass_through():
    """Only ``v`` and ``work`` would remain: a year for a date, Extra that
    is the citation key alone, no creators, nothing else."""
    assert by_key()["#item_51"]["rdf"] is None


def test_an_items_own_volume_is_kept_only_when_its_record_has_one():
    rdf = rdf_doc("""<bib:Article rdf:about="#a"><z:itemType>journalArticle</z:itemType>
        <dc:title>A</dc:title><prism:volume>7</prism:volume></bib:Article>
        <bib:Article rdf:about="#b"><z:itemType>journalArticle</z:itemType>
        <dcterms:isPartOf><bib:Journal><dc:title>J</dc:title><prism:volume>8</prism:volume></bib:Journal></dcterms:isPartOf>
        <dc:title>B</dc:title><prism:volume>9</prism:volume></bib:Article>""")
    items = by_key(rdf)
    assert items["#a"]["meta"]["volume"] == "7" and items["#a"]["rdf"] is None
    assert items["#b"]["meta"]["volume"] == "8"
    assert [el.text for el in elements(items["#b"]["rdf"], "item")] == ["9"]


def test_a_pass_through_past_its_cap_drops_its_largest_fields():
    """Over 64 KB as JSON, the largest kept fields go first (here a pasted
    abstract and three thousand authors) until it fits, and the item says
    so; what is small stays."""
    authors = "".join(person(f"Physicist{n}", "A.") for n in range(3000))
    rdf = rdf_doc(f"""<bib:Article rdf:about="#big"><z:itemType>journalArticle</z:itemType>
        <bib:authors><rdf:Seq>{authors}</rdf:Seq></bib:authors>
        <dc:title>Big</dc:title><dcterms:abstract>{"x" * 70000}</dcterms:abstract>
        <z:language>en</z:language></bib:Article>""")
    [item] = parse_zotero_rdf(rdf)
    kept = item["rdf"]
    assert len(json.dumps(kept)) <= 64 * 1024
    assert "authors" not in kept and tags_of(elements(kept, "item")) == [q("z:language")]
    assert len(item["meta"]["authors"]) == 3000  # the record itself is whole
    assert item["warnings"] == [{"title": "Big", "reason": "Some Zotero fields are too large to keep for a later Zotero export"}]


# --- attachments ------------------------------------------------------------------

def test_an_old_rdf_resource_path_names_the_file():
    """Older Zotero wrote an attachment's file as an rdf:resource element,
    which Zotero's own importer still reads."""
    [att] = by_key()[ARTICLE]["attachments"]
    assert att["path"] == "files/11/Berg - 2019 - Light in a box.pdf" and att["pdf"]
    text = rdf_doc("""<z:Attachment rdf:about="#a"><z:itemType>attachment</z:itemType>
        <rdf:resource>files/2/text path.pdf</rdf:resource><dc:title>T</dc:title></z:Attachment>""")
    [item] = parse_zotero_rdf(text)
    assert item["pdf_paths"] == ["files/2/text path.pdf"]


def test_a_standalone_attachment_carries_no_pass_through():
    text = rdf_doc("""<z:Attachment rdf:about="#a"><z:itemType>attachment</z:itemType>
        <z:path rdf:resource="files/2/paper.pdf"/><dc:title>Paper</dc:title><z:linkMode>0</z:linkMode>
        <link:type>application/pdf</link:type></z:Attachment>""")
    [item] = parse_zotero_rdf(text)
    assert item["title"] == "Paper" and item["rdf"] is None


# --- notes ------------------------------------------------------------------------

def test_a_child_note_is_a_block_tree():
    """Its first element the root, every other one a child, a list's items
    children with their nested lists under them; links keep their targets,
    math its dollars, a pasted picture its data."""
    [note] = by_key()[ARTICLE]["notes"]
    tree = note_tree(CHILD_NOTE)
    assert note == {"key": "#item_12", "text": tree["content"], "children": tree["children"]}
    assert note["text"] == "# Reading notes"
    contents = [child["content"] for child in note["children"]]
    assert contents == ["See [the site](https://example.org/a%20b) and $E=mc^2$.", "outer",
                        "1. first", "2. second", "![](data:image/png;base64,iVBORw0KGgo=)"]
    assert note["children"][1]["children"] == [{"content": "inner", "children": []}]


def test_an_empty_note_is_none():
    text = rdf_doc("""<bib:Article rdf:about="#a"><z:itemType>journalArticle</z:itemType><dc:title>A</dc:title>
        <dcterms:isReferencedBy rdf:resource="#n"/></bib:Article>
        <bib:Memo rdf:about="#n"><rdf:value>&lt;p&gt;&lt;br&gt;&lt;/p&gt;</rdf:value></bib:Memo>""")
    [item] = parse_zotero_rdf(text)
    assert item["notes"] == []  # and, referenced, no standalone note either


def test_a_note_past_the_depth_the_readers_take_comes_in_as_its_text():
    """A tree the reader can build but later readers could not walk (the
    import's copy, the export) is text too: past NOTE_DEPTH_MAX levels."""
    depth = zotero_import.NOTE_DEPTH_MAX + 100
    deep = "<p>top</p>" + "<ul><li>x" * depth + "</li></ul>" * depth
    text = rdf_doc(f'<bib:Memo rdf:about="#n"><z:itemType>note</z:itemType>'
                   f"<rdf:value>{html.escape(deep, quote=False)}</rdf:value></bib:Memo>")
    [item] = parse_zotero_rdf(text)
    assert item["title"] == "top" and [c["children"] for c in item["note"]] == [[]]
    assert [w["reason"] for w in item["warnings"]] == [
        "A note nested too deeply to keep its structure came in as plain text"]
    shallow = "<p>top</p>" + "<ul><li>x" * 50 + "</li></ul>" * 50
    [item] = parse_zotero_rdf(text.replace(html.escape(deep, quote=False), html.escape(shallow, quote=False)))
    assert item["warnings"] == [] and item["note"][0]["children"]


def test_a_note_too_deep_to_walk_comes_in_as_its_text():
    """A note nested past what the tree reader can walk (thousands of open
    lists, which other tools than Zotero's editor may write) is its text in
    one block with a warning; the library's other notes keep their trees."""
    deep = "<ul><li>" * 3000 + "bottom" + "</li></ul>" * 3000
    memo = '<bib:Memo rdf:about="#{key}"><rdf:value>{note}</rdf:value></bib:Memo>'
    text = rdf_doc(f"""<bib:Article rdf:about="#a"><z:itemType>journalArticle</z:itemType><dc:title>A</dc:title>
        <dcterms:isReferencedBy rdf:resource="#deep"/><dcterms:isReferencedBy rdf:resource="#fine"/></bib:Article>
        {memo.format(key="deep", note=html.escape(deep, quote=False))}
        {memo.format(key="fine", note=html.escape("<h1>Fine</h1><p>kept</p>", quote=False))}""")
    [item] = parse_zotero_rdf(text)
    deep_note, fine = item["notes"]
    assert (deep_note["key"], deep_note["children"], deep_note["text"].endswith("- bottom")) == ("#deep", [], True)
    assert (fine["key"], fine["text"], fine["children"]) == ("#fine", "# Fine", [{"content": "kept", "children": []}])
    assert item["warnings"] == [
        {"reason": "A note nested too deeply to keep its structure came in as plain text", "title": "A"}]


def test_a_standalone_note_is_an_item_of_its_own():
    """A note no item references, filed in a collection, comes in as an item
    with no record: titled by its first block as text, the rest of its tree
    its note, its tags and collections its own."""
    item = by_key()["#item_41"]
    assert item["standalone_note"] is True
    assert item["title"] == "Ideas later"
    assert item["note"] == [{"content": "One", "children": []}, {"content": "two", "children": []}]
    assert item["tags"] == ["todo", "auto"] and item["folders"] == [["Lab"]]
    assert (item["meta"], item["cite_key"], item["attachments"], item["notes"], item["rdf"]) == ({}, "", [], [], None)
    # the child note is no standalone one
    assert [i["key"] for i in parse_zotero_rdf(LIBRARY) if i["standalone_note"]] == ["#item_41"]


def test_a_standalone_note_keeps_a_picture_in_its_first_block():
    """The title is text; a first block holding a picture stays in the note."""
    note = '<p>Figure <img src="data:image/png;base64,AAAA"/></p><p>after</p>'
    text = rdf_doc(f'<bib:Memo rdf:about="#n"><z:itemType>note</z:itemType>'
                   f"<rdf:value>{html.escape(note, quote=False)}</rdf:value></bib:Memo>")
    [item] = parse_zotero_rdf(text)
    assert item["title"] == "Figure"
    assert [c["content"] for c in item["note"]] == ["Figure ![](data:image/png;base64,AAAA)", "after"]


@pytest.mark.parametrize("note, title, body", [
    ('<p>Meeting notes<br>Attendees: <a href="https://lab.org/ann">Ann</a><br><strong>Decision</strong>: ship'
     "</p><p>Follow-ups</p>",
     "Meeting notes", ["Attendees: [Ann](https://lab.org/ann)\n**Decision**: ship", "Follow-ups"]),
    ('<ul><li><p>Reading list</p><p>See <a href="https://x.org/p">the preprint</a></p></li><li>second</li></ul>',
     "Reading list", ["See [the preprint](https://x.org/p)", "second"]),
    ("<table><tr><th>Sample</th><th>Yield</th></tr><tr><td>A</td><td>12%</td></tr></table><p>Run on 3 May</p>",
     "Sample Yield", ["| Sample | Yield |\n| --- | --- |\n| A | 12% |", "Run on 3 May"]),
    ("<pre>import numpy as np\nx = np.zeros(3)</pre><p>works</p>",
     "import numpy as np", ["```\nimport numpy as np\nx = np.zeros(3)\n```", "works"]),
    ("<blockquote><p>To be<br>or not</p></blockquote>", "To be", ["> To be\n> or not"]),
    ('<p><img src="data:image/png;base64,AAAA"/><br>Figure 1</p>', "Figure 1",
     ["![](data:image/png;base64,AAAA)\nFigure 1"]),
    ("<p><strong>Meeting<br>notes</strong></p><p>after</p>", "Meeting", ["**notes**", "after"]),
    # a link on the first line: the title is its text, the block keeps its address
    ('<p>Notes on <a href="https://doi.org/10.1/x">Smith 2020</a></p><p>Body.</p>', "Notes on Smith 2020",
     ["Notes on [Smith 2020](https://doi.org/10.1/x)", "Body."]),
    ('<h1>Reading <a href="https://example.org/list">list</a></h1><ul><li>a</li></ul>', "Reading list",
     ["# Reading [list](https://example.org/list)", "a"]),
])
def test_a_standalone_note_is_titled_by_its_first_line(note, title, body):
    """The page's title is the note's first line as text and the rest of
    the note is the page: a first paragraph's other lines, their marks and
    links kept, and the whole of a first block that is no line of text (a
    table, code, a quote) or opens with a picture. The export writes that
    page back as the note the import reads the same page from again."""
    def memo(note_html):
        return rdf_doc(f'<bib:Memo rdf:about="#n"><z:itemType>note</z:itemType>'
                       f"<rdf:value>{html.escape(note_html, quote=False)}</rdf:value></bib:Memo>")
    [item] = parse_zotero_rdf(memo(note))
    assert (item["title"], [c["content"] for c in item["note"]]) == (title, body)
    [again] = parse_zotero_rdf(memo(standalone_note_html(item["title"], item["note"])))
    assert (again["title"], again["note"]) == (item["title"], item["note"])


def test_html_note_text_puts_a_nested_list_on_lines_of_its_own_and_keeps_links():
    assert html_note_text("<ul><li>outer<ul><li>inner</li></ul></li><li>next</li></ul>") == "- outer\n- inner\n- next"
    assert html_note_text("<p>intro</p><ul><li>a</li></ul>") == "intro\n- a"
    assert html_note_text('<p>See <a href="https://example.org/a b?x=1&amp;y=(2)">the <b>site</b></a>.</p>') == (
        "See [the **site**](https://example.org/a%20b?x=1&y=%282%29).")
    assert html_note_text('<a href="https://example.org">  </a>') == "https://example.org"
    assert html_note_text('<a href="javascript:alert(1)">click</a>') == "click"
    assert html_note_text("<p>Read this <strong>twice</strong>.</p>") == "Read this **twice**."


# --- the plan of an export zip ----------------------------------------------------

def _library_zip(pdf=None):
    return export_zip(files={
        "files/11/Berg - 2019 - Light in a box.pdf": pdf or _pdf(b"light"),
        "files/11/.zotero-ft-cache": b"light text",
        "files/21/post.html": b"<html>post</html>",
        "files/21/post_files/style.css": b"body{}",
        "files/21/post_files/logo.png": b"\x89PNG",
        "files/21/.zotero-ft-info": b"info",
        "files/99/stray.txt": b"nobody's",
    })


def test_the_plan_keeps_the_document_title_and_skips_the_no_pdf_warning_for_a_note():
    plan = plan_zotero_archive(_library_zip())
    items = {item["key"]: item for item in plan["items"]}
    article = items[ARTICLE]
    assert article["pdf_entry"] == "Exported Items/files/11/Berg - 2019 - Light in a box.pdf"
    assert article["digest"] == content_digest(_pdf(b"light"))
    assert article["rdf"]["pdf_title"] == "Full Text PDF"
    assert items["#item_41"]["warnings"] == [] and items["#item_41"]["pdf_entry"] is None
    assert any(w["reason"].startswith("No PDF available") for w in items["#item_31"]["warnings"])
    assert items["#item_51"]["rdf"] is None  # no PDF, nothing else to keep


def test_the_document_title_alone_makes_a_pass_through():
    """A record the export writes whole still keeps its document's title,
    unless that is the "PDF" the export writes; a standalone file's title
    is its item's own."""
    def article(key, att_title):
        return (f'<bib:Article rdf:about="#{key}"><z:itemType>journalArticle</z:itemType><dc:title>{key}</dc:title>'
                f'<dc:identifier>DOI 10.1/{key}</dc:identifier><link:link rdf:resource="#{key}-pdf"/></bib:Article>'
                f'<z:Attachment rdf:about="#{key}-pdf"><z:itemType>attachment</z:itemType>'
                f'<z:path rdf:resource="files/{key}/a.pdf"/><dc:title>{att_title}</dc:title>'
                f"<link:type>application/pdf</link:type></z:Attachment>")

    text = rdf_doc(article("plain", "PDF"), article("titled", "Accepted Version"),
                   """<z:Attachment rdf:about="#s"><z:itemType>attachment</z:itemType><z:path rdf:resource="files/2/s.pdf"/>
                   <dc:title>Standalone scan</dc:title><link:type>application/pdf</link:type></z:Attachment>""")
    plan = plan_zotero_archive(export_zip(text, {"files/plain/a.pdf": _pdf(b"a"), "files/titled/a.pdf": _pdf(b"t"),
                                                 "files/2/s.pdf": _pdf(b"s")}))
    assert [(item["title"], item["rdf"]) for item in plan["items"]] == [
        ("plain", None),
        ("titled", {"v": 1, "work": {"title": "titled", "doi": "10.1/titled"}, "pdf_title": "Accepted Version"}),
        ("Standalone scan", None)]


def test_a_web_snapshots_folder_is_one_warning():
    """The files beside a snapshot in its attachment folder are one warning;
    Zotero's full-text index files none; a file in a folder no attachment
    points into still warns by itself. All are listed as not imported."""
    plan = plan_zotero_archive(_library_zip())
    folded = [w for w in plan["warnings"] if "beside" in w["reason"]]
    assert folded == [{"title": "A post", "path": "Exported Items/files/21",
                       "reason": "2 files beside post.html are not imported (a web snapshot's page resources)"}]
    singles = [w["path"] for w in plan["warnings"] if w["reason"].startswith("File is not imported")]
    assert singles == ["Exported Items/files/99/stray.txt"]
    status = {e["path"]: e["status"] for e in plan["entries"]}
    for path in ("files/11/.zotero-ft-cache", "files/21/post_files/style.css", "files/21/post_files/logo.png",
                 "files/21/.zotero-ft-info", "files/99/stray.txt"):
        assert status[f"Exported Items/{path}"] == "not_imported", path
    assert status["Exported Items/files/21/post.html"] == "imported"


def test_one_file_beside_an_attachment_is_said_in_the_singular():
    text = rdf_doc("""<z:Attachment rdf:about="#a"><z:itemType>attachment</z:itemType>
        <z:path rdf:resource="files/3/page.html"/><dc:title>Page</dc:title></z:Attachment>""")
    plan = plan_zotero_archive(export_zip(text, {"files/3/page.html": b"<p>", "files/3/img.png": b"\x89PNG"}))
    assert [w["reason"] for w in plan["warnings"] if "beside" in w["reason"]] == [
        "1 file beside page.html is not imported (a web snapshot's page resources)"]


def test_an_attachments_own_file_keeps_its_own_warning():
    """Not a PDF after all: its own warning, and its file warns by itself
    rather than as a snapshot's resource."""
    plan = plan_zotero_archive(_library_zip(pdf=b"<html>not a pdf</html>"))
    article = next(item for item in plan["items"] if item["key"] == ARTICLE)
    assert any(w["reason"] == "Cannot import PDF: file is not a PDF" for w in article["warnings"])
    assert article["pdf_entry"] is None
    assert "Exported Items/files/11/Berg - 2019 - Light in a box.pdf" in [
        w.get("path") for w in plan["warnings"] if w["reason"].startswith("File is not imported")]


class _NoWholeReads(zipfile.ZipFile):
    """A zip whose PDFs can only be streamed."""

    def read(self, name, pwd=None):
        assert not str(name).endswith(".pdf"), f"{name} read whole"
        return super().read(name, pwd)


def test_a_pdf_is_hashed_as_it_streams():
    """A PDF many chunks long is hashed as it is inflated, never read whole,
    to the digest its stored copy is named by."""
    big = _pdf(bytes(range(256)) * (3 * SPOOL_CHUNK // 256 + 7))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("x/x.rdf", LIBRARY)
        zf.writestr("x/files/11/Berg - 2019 - Light in a box.pdf", big)
    with _NoWholeReads(io.BytesIO(buf.getvalue())) as zf:
        plan = plan_zotero_archive(zf)
    article = next(item for item in plan["items"] if item["key"] == ARTICLE)
    assert article["digest"] == content_digest(big)


def test_a_preview_s_digests_spare_the_commit_a_second_hashing(monkeypatch):
    """The plan names every PDF's size, CRC and digest; a plan of the same
    archive given them hashes none of its PDFs again, while an entry whose
    size or CRC changed is hashed anew. One PDF two attachments name is
    hashed once."""
    calls = []
    real_digest = zotero_import._pdf_digest
    monkeypatch.setattr(zotero_import, "_pdf_digest", lambda zf, name: calls.append(name) or real_digest(zf, name))
    text = LIBRARY.replace("</rdf:RDF>", """<z:Attachment rdf:about="#again"><z:itemType>attachment</z:itemType>
        <z:path rdf:resource="files/11/Berg - 2019 - Light in a box.pdf"/><dc:title>Again</dc:title>
        <link:type>application/pdf</link:type></z:Attachment></rdf:RDF>""")
    files = {"files/11/Berg - 2019 - Light in a box.pdf": _pdf(b"light")}
    entry = "Exported Items/files/11/Berg - 2019 - Light in a box.pdf"

    first = plan_zotero_archive(export_zip(text, files))
    info = export_zip(text, files).getinfo(entry)
    assert first["digests"] == {entry: [info.file_size, info.CRC, content_digest(_pdf(b"light"))]}
    assert calls == [entry]

    stored = json.loads(json.dumps(first["digests"]))  # as a staged review keeps it
    again = plan_zotero_archive(export_zip(text, files), digests=stored)
    assert calls == [entry] and again["digests"] == first["digests"]
    assert [i["digest"] for i in again["items"]] == [i["digest"] for i in first["items"]]

    stale = {entry: [info.file_size, info.CRC ^ 1, "0" * 24]}
    changed = plan_zotero_archive(export_zip(text, files), digests=stale)
    assert calls == [entry, entry] and changed["digests"] == first["digests"]
