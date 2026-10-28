"""Zotero → Gamma → Zotero → Gamma through the real endpoints. One library
written the way Zotero writes it is imported, exported again as Zotero RDF,
read back, and imported into another library. The library holds:

- a journal article whose record goes beyond Gamma's fields;
- a PDF carrying Zotero's annotations;
- a structured child note and a standalone note;
- a preprint;
- nested collections and both kinds of tag.

Each step keeps what the step before it held; the per-library parts (block
ids, the keys a note travels under) are left out of the comparison."""

import io
import zipfile

from conftest import folder_names, label_names, make_folder
from test_zotero_export import _canon_rdf
from test_zotero_import import (_PNG, _custom_zip, _data_uri, _library, _rdf, _send, _with_annotations,
                                _zotero_annot)

from gamma.zotero_import import parse_zotero_rdf

ARTICLE = """<bib:Article rdf:about="#item_12">
    <z:itemType>journalArticle</z:itemType>
    <dcterms:isPartOf rdf:resource="urn:issn:0028-0836"/>
    <bib:authors><rdf:Seq>
        <rdf:li><foaf:Person><foaf:surname>Berg</foaf:surname><foaf:givenName>Jan van der</foaf:givenName></foaf:Person></rdf:li>
        <rdf:li><foaf:Person><foaf:surname>ATLAS Collaboration</foaf:surname></foaf:Person></rdf:li>
    </rdf:Seq></bib:authors>
    <bib:editors><rdf:Seq>
        <rdf:li><foaf:Person><foaf:surname>Eco</foaf:surname><foaf:givenName>Umberto</foaf:givenName></foaf:Person></rdf:li>
    </rdf:Seq></bib:editors>
    <link:link rdf:resource="#item_13"/>
    <dcterms:isReferencedBy rdf:resource="#item_14"/>
    <dc:subject>optics</dc:subject>
    <dc:subject><z:AutomaticTag><rdf:value>fibres</rdf:value></z:AutomaticTag></dc:subject>
    <dc:subject><dcterms:LCC><rdf:value>QC350</rdf:value></dcterms:LCC></dc:subject>
    <dc:title>Light in fibres</dc:title>
    <dcterms:abstract>We guide light &amp; measure it.</dcterms:abstract>
    <dc:date>2019-03-14</dc:date>
    <z:language>en</z:language>
    <dc:identifier><dcterms:URI><rdf:value>https://www.nature.com/articles/x</rdf:value></dcterms:URI></dc:identifier>
    <dcterms:dateSubmitted>2024-01-02 10:00:00</dcterms:dateSubmitted>
    <dc:description>PMID: 31234
Citation Key: berg2019</dc:description>
    <bib:pages>12-19</bib:pages>
</bib:Article>
<bib:Journal rdf:about="urn:issn:0028-0836">
    <prism:volume>567</prism:volume>
    <dc:title>Nature</dc:title>
    <dcterms:alternative>Nat.</dcterms:alternative>
    <prism:number>7748</prism:number>
    <dc:identifier>DOI 10.1038/s41586-019-0001-1</dc:identifier>
    <dc:identifier>ISSN 0028-0836</dc:identifier>
</bib:Journal>
<z:Attachment rdf:about="#item_13">
    <z:itemType>attachment</z:itemType>
    <z:path rdf:resource="files/13/Berg - 2019 - Light in fibres.pdf"/>
    <dc:title>Full Text PDF</dc:title>
    <link:type>application/pdf</link:type>
</z:Attachment>
<bib:Memo rdf:about="#item_14"><z:itemType>note</z:itemType><rdf:value>{note}</rdf:value></bib:Memo>
<bib:Memo rdf:about="#item_20">
    <z:itemType>note</z:itemType>
    <dc:subject>idea</dc:subject>
    <rdf:value>{standalone}</rdf:value>
</bib:Memo>
<rdf:Description rdf:about="http://arxiv.org/abs/2101.00001">
    <z:itemType>preprint</z:itemType>
    <dc:publisher><foaf:Organization><foaf:name>arXiv</foaf:name></foaf:Organization></dc:publisher>
    <bib:authors><rdf:Seq>
        <rdf:li><foaf:Person><foaf:surname>Lovelace</foaf:surname><foaf:givenName>Ada</foaf:givenName></foaf:Person></rdf:li>
    </rdf:Seq></bib:authors>
    <dc:title>A preprint</dc:title>
    <dc:date>2021-01-01</dc:date>
    <z:archiveID>arXiv:2101.00001</z:archiveID>
    <dc:identifier><dcterms:URI><rdf:value>http://arxiv.org/abs/2101.00001</rdf:value></dcterms:URI></dc:identifier>
    <dc:identifier>DOI 10.48550/arXiv.2101.00001</dc:identifier>
</rdf:Description>
<z:Collection rdf:about="#collection_1">
    <dc:title>Optics</dc:title>
    <dcterms:hasPart rdf:resource="#collection_2"/>
    <dcterms:hasPart rdf:resource="http://arxiv.org/abs/2101.00001"/>
</z:Collection>
<z:Collection rdf:about="#collection_2">
    <dc:title>Fibres</dc:title>
    <dcterms:hasPart rdf:resource="#item_12"/>
</z:Collection>
<z:Collection rdf:about="#collection_3">
    <dc:title>Ideas</dc:title>
    <dcterms:hasPart rdf:resource="#item_20"/>
</z:Collection>"""

NOTE = ('<div data-schema-version="9"><h2>Reading notes</h2>'
        '<p>Light is <strong>guided</strong> by <a href="https://en.wikipedia.org/wiki/Total_internal_reflection">'
        "total internal reflection</a>, <em>mostly</em>.</p>"
        "<ol><li>first</li><li>second<ul><li>nested</li></ul></li></ol>"
        "<blockquote><p>a quote</p></blockquote><pre>n = 1.45</pre>"
        f'<p>A figure: <img src="{_data_uri(_PNG)}" alt="fig" width="300"/></p></div>')
STANDALONE = '<div data-schema-version="9"><h1>Ideas for later</h1><p>One</p><ul><li>two</li></ul></div>'


def _library_zip():
    """The library as Zotero exports it, the PDF carrying a highlight and an
    image annotation in Zotero's colours (no /CA on either)."""
    import html
    pdf = _with_annotations(
        _zotero_annot("/Highlight", "HLAAAAAA", (72, 716, 300, 732),
                      QuadPoints=(72, 732, 300, 732, 72, 716, 300, 716), C=(1, 0.831, 0), Contents="a highlight"),
        _zotero_annot("/Square", "IMAAAAAA", (100, 300, 400, 500), BS={"/W": 2}, C=(0.18, 0.659, 0.898),
                      Contents="an image"))
    rdf = _rdf(ARTICLE.format(note=html.escape(NOTE, quote=False), standalone=html.escape(STANDALONE, quote=False)))
    return rdf, _custom_zip(rdf, {"files/13/Berg - 2019 - Light in fibres.pdf": pdf})


def _block(node):
    """A block as both libraries should hold it: its text, what it draws
    on the PDF and what kind of block it is, and its subtree."""
    props = node.get("properties") or {}
    pos = props.get("pdf_position")
    at = {"at": (pos["pageNumber"], bool(pos.get("area")),
                 *(round(pos["boundingRect"][k]) for k in ("x1", "y1", "x2", "y2")))} if pos else {}
    return {"content": node["content"], **at,
            **{key: props[key] for key in ("color", "quote") if key in props},
            "note": bool(props.get("zotero_note")), "attachments": bool(props.get("zotero_attachments")),
            "children": [_block(c) for c in node.get("children") or []]}


def _pages(client, ids):
    """{title: what the page holds} for the pages an import reported."""
    folders, labels = folder_names(client), label_names(client)
    out = {}
    for page_id in ids:
        tree = client.get(f"/api/blocks/{page_id}/subtree")
        assert tree.status_code == 200, tree.text
        page = tree.json()["block"]
        props = page["properties"]
        out[page["content"]] = {
            "meta": props.get("meta"), "zotero_rdf": _canon_rdf(props.get("zotero_rdf")),
            "zotero_key": props.get("zotero_key"), "has_pdf": bool(props.get("doc_id")),
            "note_page": bool(props.get("zotero_note")) and props.get("zotero_note") == props.get("zotero_key"),
            "folders": sorted(folders[f] for f in props.get("folders") or []),
            "labels": sorted(labels[label] for label in props.get("labels") or []),
            "blocks": [_block(c) for c in page["children"]],
        }
    return out


def _items(rdf):
    """{key: what Zotero reads of the item}, each note's own key left out."""
    return {item["key"]: {
        "title": item["title"], "meta": item["meta"], "cite_key": item["cite_key"], "tags": item["tags"],
        "folders": sorted(item["folders"]), "standalone_note": item["standalone_note"], "note": item.get("note"),
        "notes": [(note["text"], note["children"]) for note in item["notes"]],
        "rdf": _canon_rdf(item["rdf"]),
    } for item in parse_zotero_rdf(rdf)}


def _into(client, data):
    """Import ``data`` into the library's "RT" folder; the pages it names."""
    dest = make_folder(client, "RT")
    d = _send(client, data, folder=dest).json()
    assert d["skipped"] == [], d
    return dest, d


def test_a_library_goes_round_zotero_and_gamma_and_back():
    rdf, data = _library_zip()
    a, _ = _library()
    dest, d = _into(a, data)
    assert (d["pages_created"], d["notes_imported"]) == (3, 1), d
    ids = [p["id"] for p in d["pages"]]
    first = _pages(a, ids)
    assert set(first) == {"Light in fibres", "Ideas for later", "A preprint"}

    article = first["Light in fibres"]
    assert article["folders"] == [["RT", "Optics", "Fibres"]] and article["labels"] == ["fibres", "optics"]
    assert article["meta"]["authors"] == ["Jan van der Berg", "ATLAS Collaboration"]
    [highlight, area] = [b for b in article["blocks"] if "at" in b]
    assert (highlight["content"], highlight["at"][:2], area["content"], area["at"][:2]) == (
        "a highlight", (1, False), "an image", (1, True))
    [note] = [b for b in article["blocks"] if b["note"]]
    stored = next(b for b in note["children"] if b["content"].startswith("A figure"))["content"]
    assert stored.startswith("A figure: ![fig|300](/api/uploads/")
    assert note == {"content": "## Reading notes", "note": True, "attachments": False, "children": [
        {"content": "Light is **guided** by [total internal reflection]"
                    "(https://en.wikipedia.org/wiki/Total_internal_reflection), *mostly*.",
         "note": False, "attachments": False, "children": []},
        {"content": "1. first", "note": False, "attachments": False, "children": []},
        {"content": "2. second", "note": False, "attachments": False, "children": [
            {"content": "nested", "note": False, "attachments": False, "children": []}]},
        {"content": "> a quote", "note": False, "attachments": False, "children": []},
        {"content": "```\nn = 1.45\n```", "note": False, "attachments": False, "children": []},
        {"content": stored, "note": False, "attachments": False, "children": []}]}
    ideas = first["Ideas for later"]
    assert (ideas["note_page"], ideas["has_pdf"], ideas["folders"], ideas["labels"]) == (
        True, False, [["RT", "Ideas"]], ["idea"])
    assert [b["content"] for b in ideas["blocks"]] == ["One", "two"]

    # Gamma → Zotero: what Zotero reads back is what it wrote, the notes
    # under keys of Gamma's own.
    exported = a.get(f"/api/folders/{dest}/export", params={"mode": "zotero-rdf"})
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
        rdf_again = z.read(next(n for n in z.namelist() if n.endswith(".rdf"))).decode("utf-8")
    original, again = _items(rdf), _items(rdf_again)
    for key in ("#item_12", "#item_20", "http://arxiv.org/abs/2101.00001"):
        assert again[key] == original[key], key

    # Zotero → Gamma again, into another library: the same pages.
    b, _ = _library()
    _, d = _into(b, exported.content)
    assert (d["pages_created"], d["notes_imported"]) == (3, 1), d
    assert _pages(b, [p["id"] for p in d["pages"]]) == first

    # and into the library it came from, nothing new
    d = _send(a, exported.content, folder=dest).json()
    assert (d["pages_created"], d["pages_merged"], d["notes_imported"], d["attachments_imported"]) == (0, 3, 0, 0), d
    assert _pages(a, ids) == first
