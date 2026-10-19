"""Zotero library import: a zip of the "Zotero RDF" export becomes pages with
metadata, collections→folder labels, tags→flat labels, notes→child blocks, and
the annotations embedded in the exported PDFs become highlight blocks."""

import io
import zipfile

import pytest
from conftest import folder_names, guest_name, label_names, make_folder


def _annotated_pdf(text=b"Attention is all you need, says the paper."):
    """Minimal one-page PDF with a text layer and one /Highlight annotation
    (same hand-built construction as test_pdf_proxy's _text_pdf)."""
    stream = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> /Annots [6 0 R] >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Annot /Subtype /Highlight /Rect [70 700 300 735]"
        b" /QuadPoints [70 735 300 735 70 700 300 700]"
        b" /C [1 0.85 0.3] /CA 0.5 /Contents (great point) >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (i, obj))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objs) + 1, xref))
    return out.getvalue()


RDF = """<rdf:RDF
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:z="http://www.zotero.org/namespaces/export#"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:bib="http://purl.org/net/biblio#"
 xmlns:foaf="http://xmlns.com/foaf/0.1/"
 xmlns:link="http://purl.org/rss/1.0/modules/link/"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/">
    <bib:Article rdf:about="https://www.nature.com/articles/s41586-000-00000-0">
        <z:itemType>journalArticle</z:itemType>
        <dcterms:isPartOf rdf:resource="urn:issn:0028-0836"/>
        <bib:authors><rdf:Seq><rdf:li><foaf:Person>
            <foaf:surname>Vaswani</foaf:surname><foaf:givenName>Ashish</foaf:givenName>
        </foaf:Person></rdf:li></rdf:Seq></bib:authors>
        <link:link rdf:resource="#item_3"/>
        <dcterms:isReferencedBy rdf:resource="#item_9"/>
        <dc:title>Attention is all you need</dc:title>
        <dc:description>Citation Key: vaswani2017attention</dc:description>
        <dc:date>2017-06-12</dc:date>
        <dc:subject>transformers</dc:subject>
        <dc:subject><z:AutomaticTag><rdf:value>attention</rdf:value></z:AutomaticTag></dc:subject>
        <bib:pages>1-11</bib:pages>
    </bib:Article>
    <bib:Journal rdf:about="urn:issn:0028-0836">
        <dc:identifier>DOI 10.1038/s41586-000-00000-0</dc:identifier>
        <prism:volume>647</prism:volume>
        <dc:title>Nature</dc:title>
        <dc:identifier>ISSN 0028-0836</dc:identifier>
    </bib:Journal>
    <z:Attachment rdf:about="#item_3">
        <z:itemType>attachment</z:itemType>
        <z:path rdf:resource="files/3/Vaswani - 2017 - Attention.pdf"/>
        <dc:title>PDF</dc:title>
        <link:type>application/pdf</link:type>
    </z:Attachment>
    <bib:Memo rdf:about="#item_9">
        <rdf:value>&lt;p&gt;Read this &lt;strong&gt;twice&lt;/strong&gt;.&lt;/p&gt;</rdf:value>
    </bib:Memo>
    <rdf:Description rdf:about="https://arxiv.org/abs/1707.06347">
        <z:itemType>preprint</z:itemType>
        <dc:title>Proximal Policy Optimization</dc:title>
        <dc:date>2017</dc:date>
        <dc:identifier><dcterms:URI><rdf:value>https://arxiv.org/abs/1707.06347</rdf:value></dcterms:URI></dc:identifier>
    </rdf:Description>
    <z:Collection rdf:about="#collection_1">
        <dc:title>ML</dc:title>
        <dcterms:hasPart rdf:resource="#collection_2"/>
    </z:Collection>
    <z:Collection rdf:about="#collection_2">
        <dc:title>Transformers</dc:title>
        <dcterms:hasPart rdf:resource="https://www.nature.com/articles/s41586-000-00000-0"/>
    </z:Collection>
</rdf:RDF>
"""


def _export_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("zotero_export/zotero_export.rdf", RDF)
        zf.writestr("zotero_export/files/3/Vaswani - 2017 - Attention.pdf", _annotated_pdf())
    buf.seek(0)
    return buf


def _post(guest, **form):
    return guest.post("/api/import/zotero",
                      files={"file": ("lib.zip", _export_zip(), "application/zip")},
                      data=form)


def test_zotero_import_full(guest):
    r = _post(guest)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["items"] == 2
    assert d["pages_created"] == 2 and d["pages_merged"] == 0
    assert d["pdfs_stored"] == 1
    assert d["notes_imported"] == 1
    assert d["annotations_imported"] == 1
    assert d["skipped"] == []

    by_title = {p["title"]: p for p in d["pages"]}
    paper = by_title["Attention is all you need"]

    block = guest.get(f"/api/blocks/{paper['id']}").json()
    props = block["properties"]
    assert props["doc_id"] and "source_url" not in props  # the stored copy, derived from doc_id
    assert props["zotero_key"] == "https://www.nature.com/articles/s41586-000-00000-0"
    # nested collection → nested folders; tags (incl. AutomaticTag) → labels by name
    assert [folder_names(guest)[f] for f in props["folders"]] == [["ML", "Transformers"]]
    assert [label_names(guest)[i] for i in props["labels"]] == ["transformers", "attention"]
    assert paper["folders"] == [["ML", "Transformers"]]
    meta = props["meta"]
    assert meta["source"] == "zotero" and meta["year"] == "2017"
    assert meta["authors"] == ["Ashish Vaswani"]
    # the DOI lives on the referenced journal record, the venue is its title
    assert meta["doi"] == "10.1038/s41586-000-00000-0"
    assert meta["venue"] == "Nature" and meta["volume"] == "647"
    assert "Vaswani" in props["bibtex"]

    children = guest.get(f"/api/blocks/{paper['id']}/children").json()["children"]
    notes = [c for c in children if c["properties"].get("zotero_note")]
    assert len(notes) == 1 and notes[0]["content"] == "Read this **twice**."
    annots = [c for c in children if c["properties"].get("imported_annot")]
    assert len(annots) == 1
    hl = annots[0]
    assert hl["content"] == "great point"  # the /Contents comment
    assert "Attention" in hl["properties"]["quote"]
    assert hl["properties"]["pdf_position"]["pageNumber"] == 1

    # the arXiv preprint has no PDF: metadata-only page, id from the URL
    pre = guest.get(f"/api/blocks/{by_title['Proximal Policy Optimization']['id']}").json()
    assert pre["properties"]["meta"]["arxiv_id"] == "1707.06347"
    assert "doc_id" not in pre["properties"]


def test_zotero_import_idempotent(guest):
    first = _post(guest).json()
    again = _post(guest)
    assert again.status_code == 200, again.text
    d = again.json()
    assert d["pages_created"] == 0
    assert d["pages_merged"] == 2
    assert d["notes_imported"] == 0
    assert d["annotations_imported"] == 0
    # same page ids as before — matched by doc_id / zotero_key, not duplicated
    assert {p["id"] for p in d["pages"]} == {p["id"] for p in first["pages"]}
    # and the folders and labels are the same blocks, not made twice
    assert list(folder_names(guest).values()).count(["ML", "Transformers"]) == 1
    assert list(label_names(guest).values()).count("attention") == 1


def test_zotero_import_folder_prefix(guest):
    """Into a destination folder: the collections are made below it, and a
    page merged into keeps its own folders first (the pages exist from the
    tests above, filed in ML/Transformers)."""
    dest = make_folder(guest, "zotero")
    r = _post(guest, folder=dest)
    assert r.status_code == 200, r.text
    d = r.json()
    by_title = {p["title"]: p for p in d["pages"]}
    paper = guest.get(f"/api/blocks/{by_title['Attention is all you need']['id']}").json()
    paths = folder_names(guest)
    assert [paths[f] for f in paper["properties"]["folders"]] == [["ML", "Transformers"],
                                                                  ["zotero", "ML", "Transformers"]]
    assert by_title["Attention is all you need"]["folders"] == [["ML", "Transformers"],
                                                                ["zotero", "ML", "Transformers"]]
    # an item in no collection lands in the destination itself
    pre = guest.get(f"/api/blocks/{by_title['Proximal Policy Optimization']['id']}").json()
    assert pre["properties"]["folders"] == [dest]
    assert _post(guest, folder="no-such-folder").status_code == 400


def test_zotero_import_rejects_junk(guest):
    r = guest.post("/api/import/zotero",
                   files={"file": ("x.zip", io.BytesIO(b"not a zip"), "application/zip")})
    assert r.status_code == 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hi")
    buf.seek(0)
    r = guest.post("/api/import/zotero",
                   files={"file": ("x.zip", buf, "application/zip")})
    assert r.status_code == 400 and ".rdf" in r.json()["detail"]


def _custom_zip(rdf, files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("export/library.rdf", rdf)
        for path, data in files.items():
            zf.writestr(f"export/{path}", data)
    return buf.getvalue()


def _send(guest, data, preview=False, **form):
    return guest.post("/api/import/zotero" + ("/preview" if preview else ""),
                      files={"file": ("lib.zip", data, "application/zip")}, data=form)


def _zotero_annotated_pdf():
    """The paper with one annotation of each kind Zotero has, keyed as its
    pdf-worker writes them into an export with "Include Annotations"
    (annotationToRaw, Zotero 9): a Zotero id on each, no /CA on the markup,
    a text annotation's own box and turn in /Zotero:* keys."""
    from PyPDF2 import PdfReader, PdfWriter
    from PyPDF2.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject

    def value(v):
        if hasattr(v, "write_to_stream"):
            return v
        if isinstance(v, tuple):
            return ArrayObject(value(x) for x in v)
        if isinstance(v, dict):
            return DictionaryObject({NameObject(k): value(x) for k, x in v.items()})
        if isinstance(v, str):
            return NameObject(v) if v.startswith("/") else TextStringObject(v)
        return NumberObject(v) if isinstance(v, int) else FloatObject(v)

    def annot(subtype, key, rect, **keys):
        return DictionaryObject({NameObject(k): value(v) for k, v in {
            "/Type": "/Annot", "/Subtype": subtype, "/Rect": rect, "/F": 4,
            "/NM": f"Zotero-{key}", "/Zotero:Key": key, **{f"/{k}": v for k, v in keys.items()}}.items()})

    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(_annotated_pdf())))
    del w.pages[0][NameObject("/Annots")]
    for a in (
        annot("/Highlight", "HLAAAAAA", (72, 716, 300, 732), QuadPoints=(72, 732, 300, 732, 72, 716, 300, 716),
              C=(1, 0.831, 0), Contents="a highlight"),
        annot("/Underline", "ULAAAAAA", (72, 716, 200, 732), QuadPoints=(72, 732, 200, 732, 72, 716, 200, 716),
              C=(1, 0.4, 0.4), Contents="an underline"),
        annot("/Text", "NTAAAAAA", (500, 700, 522, 722), C=(0.373, 0.698, 0.212), CA=1, Contents="a note"),
        annot("/Square", "IMAAAAAA", (100, 300, 400, 500), BS={"/W": 2}, C=(0.18, 0.659, 0.898),
              Contents="an image"),
        annot("/Ink", "INAAAAAA", (98, 98, 302, 202), BS={"/S": "/N", "/Type": "/Border", "/W": 2},
              InkList=((100, 100, 150, 150, 200, 120, 300, 200),), C=(0.635, 0.541, 0.898), CA=1),
        annot("/FreeText", "TXAAAAAA", (100, 550, 300, 600), Contents="typed in Zotero", CA=1,
              DA=TextStringObject("/Helvetica 14 Tf 0.898 0.431 0.933 rg"), **{
                  "Zotero:Rect": (100, 550, 300, 600), "Zotero:Rotation": 0, "Zotero:FontSize": 14,
                  "Zotero:Color": (0.898, 0.431, 0.933)}),
    ):
        w.add_annotation(page_number=0, annotation=a)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def test_each_kind_of_zotero_annotation_imports(guest):
    """Zotero's highlight and underline are highlights (Gamma has no
    underline), its image an area highlight, its ink handwriting, its text
    and its note text boxes — each with its comment or text, its colour."""
    rdf = RDF.replace("s41586-000-00000-0", "every-kind").replace("Attention is all you need", "Every kind")
    d = _send(guest, _custom_zip(rdf, {"files/3/Vaswani - 2017 - Attention.pdf": _zotero_annotated_pdf()})).json()
    assert d["annotations_imported"] == 6, d
    paper = next(p for p in d["pages"] if p["title"] == "Every kind")
    kids = {c["content"]: c["properties"] for c in
            guest.get(f"/api/blocks/{paper['id']}/children").json()["children"] if c["properties"].get("imported_annot")}
    assert "Attention" in kids["a highlight"]["quote"] and kids["a highlight"]["color"] == "#ffd40073"
    assert "Attention" in kids["an underline"]["quote"]
    assert kids["an image"]["pdf_position"]["area"] is True
    assert kids["a note"]["text_box"]["bg"] == "#fff4b8"
    box = kids["typed in Zotero"]["text_box"]
    assert (box["x"], box["y"], box["w"], box["h"], box["size"], box["color"]) == (100, 192, 200, 50, 14, "#e56eee")
    [ink] = [p for p in kids.values() if p.get("ink_url")]
    assert ink["color"] == "#a28ae5" and ink["ink_strokes"] == 1


def test_preview_is_read_only_and_predicts_upgrade(guest):
    from conftest import workspace_of
    from gamma.db import connect_pages_db, ws_uploads_dir

    rdf = RDF.replace("s41586-000-00000-0", "preview-upgrade").replace("Attention is all you need", "Preview upgrade")
    missing = _custom_zip(rdf, {"files/3/": b""})
    ws = workspace_of(guest_name())
    research = make_folder(guest, "Research")
    with connect_pages_db(ws) as conn:
        before = conn.execute("SELECT * FROM unified_blocks ORDER BY id").fetchall()
    uploads_before = set(ws_uploads_dir(ws).glob("*"))
    response = _send(guest, missing, preview=True, folder=research)
    assert response.status_code == 200, response.text
    plan = response.json()
    page = next(p for p in plan["pages"] if p["title"] == "Preview upgrade")
    assert page["kind"] == "page" and page["action"] == "create"
    assert page["folders"] == [["Research", "ML", "Transformers"]]  # no folder made
    assert plan["folder"] == research
    assert any("PDF missing" in w["reason"] for w in plan["warnings"])
    assert any(e["directory"] for e in plan["entries"])
    with connect_pages_db(ws) as conn:
        assert conn.execute("SELECT * FROM unified_blocks ORDER BY id").fetchall() == before
    assert set(ws_uploads_dir(ws).glob("*")) == uploads_before
    first = _send(guest, missing).json()
    page_id = next(p["id"] for p in first["pages"] if p["title"] == "Preview upgrade")
    complete = _custom_zip(rdf, {"files/3/Vaswani - 2017 - Attention.pdf": _annotated_pdf(b"Recovered preview PDF")})
    plan = _send(guest, complete, preview=True).json()
    page = next(p for p in plan["pages"] if p["title"] == "Preview upgrade")
    assert page["action"] == "merge" and page["kind"] == "pdf" and page["existing_id"] == page_id
    result = _send(guest, complete).json()
    assert next(p for p in result["pages"] if p["id"] == page_id)["kind"] == "pdf"
    assert guest.get(f"/api/blocks/{page_id}").json()["properties"]["doc_id"]


@pytest.mark.parametrize("path,entry", [
    ("./files/3/paper%20name.pdf", "files/3/paper name.pdf"),
    ("files\\3\\paper.pdf", "files/3/paper.pdf"),
    ("files/3/caf\u00e9.pdf", "files/3/cafe\u0301.pdf"),
    ("files/3/a-long-original-name.pdf", "files/3/renamed.pdf"),
])
def test_pdf_path_variations(path, entry):
    from gamma.zotero_import import plan_zotero_archive
    rdf = RDF.replace("files/3/Vaswani - 2017 - Attention.pdf", path)
    with zipfile.ZipFile(io.BytesIO(_custom_zip(rdf, {entry: _annotated_pdf()}))) as zf:
        plan = plan_zotero_archive(zf)
    assert plan["items"][0]["pdf_entry"] == f"export/{entry}"


@pytest.mark.parametrize("files", [
    {"files/3/": b""},  # zipped: the empty folder is in the zip
    {"files/7/other.pdf": _annotated_pdf()},  # a picked folder lists no empty folders
])
def test_unwritten_pdf_says_zotero_was_still_writing(files):
    """Zotero writes a files/<id>/ path only for a file it is copying, and
    copies after its export reports done; an export taken too early lacks it."""
    from gamma.zotero_import import plan_zotero_archive
    with zipfile.ZipFile(io.BytesIO(_custom_zip(RDF, files))) as zf:
        plan = plan_zotero_archive(zf)
    assert plan["warnings"][0]["title"] == "Zotero export not finished"
    assert plan["warnings"][0]["reason"].startswith("1 PDF was not in the export folder yet.")
    reason = next(w["reason"] for w in plan["items"][0]["warnings"] if "PDF missing" in w["reason"])
    assert reason.endswith("(Zotero had not written it yet)")


def test_export_without_its_files_tree_is_not_an_unwritten_one():
    """Only the .rdf zipped: nothing says Zotero was still writing."""
    from gamma.zotero_import import plan_zotero_archive
    with zipfile.ZipFile(io.BytesIO(_custom_zip(RDF, {}))) as zf:
        plan = plan_zotero_archive(zf)
    assert any("PDF missing" in w["reason"] for w in plan["warnings"])
    assert not any("not finished" in w["title"] or "not written" in w["reason"] for w in plan["warnings"])


def test_pdf_matching_never_guesses_across_items_or_ambiguous_folders():
    from gamma.zotero_import import plan_zotero_archive
    files = {"files/3/one.pdf": _annotated_pdf(), "files/3/two.pdf": _annotated_pdf(),
             "files/99/Vaswani - 2017 - Attention.pdf": _annotated_pdf()}
    with zipfile.ZipFile(io.BytesIO(_custom_zip(RDF, files))) as zf:
        plan = plan_zotero_archive(zf)
    assert plan["items"][0]["pdf_entry"] is None
    assert any("PDF missing" in w["reason"] for w in plan["warnings"])
    assert sum(e["status"] == "not_imported" for e in plan["entries"]) == 3


def _with_attachments(rdf, links, top=""):
    """``rdf`` with more link:link attachments on its article (``links``,
    inline z:Attachment elements) and more top-level elements (``top``)."""
    rdf = rdf.replace('<link:link rdf:resource="#item_3"/>',
                      '<link:link rdf:resource="#item_3"/>' + "".join(
                          f"<link:link>{link}</link:link>" for link in links))
    return rdf.replace("</rdf:RDF>", top + "</rdf:RDF>")


def _file_att(about, path, mime, title=""):
    return (f'<z:Attachment rdf:about="{about}"><z:itemType>attachment</z:itemType>'
            f'<dc:title>{title}</dc:title><z:path rdf:resource="{path}"/>'
            f'<link:type>{mime}</link:type></z:Attachment>')


def _link_att(about, url, title):
    return (f'<z:Attachment rdf:about="{about}"><z:itemType>attachment</z:itemType>'
            f'<dc:identifier><dcterms:URI><rdf:value>{url}</rdf:value></dcterms:URI></dc:identifier>'
            f'<dc:title>{title}</dc:title><z:linkMode>3</z:linkMode></z:Attachment>')


def _attachments_of(guest, page_id):
    """The page's "Attachments" block and the contents under it, in order."""
    children = guest.get(f"/api/blocks/{page_id}/children").json()["children"]
    headings = [c for c in children if c["properties"].get("zotero_attachments")]
    if not headings:
        return None, []
    [heading] = headings
    under = guest.get(f"/api/blocks/{heading['id']}/children").json()["children"]
    return heading, under


def test_other_attachments_sit_on_the_items_page(guest):
    """As in Zotero's item tree: the item's first PDF is its page's
    document, its other attachments — another PDF, a snapshot, a web link —
    file chips and links under the page's "Attachments" block, in the
    export's order. A standalone attachment is an item of its own."""
    rdf = RDF.replace("s41586-000-00000-0", "all-attachments").replace("Attention is all you need", "All attachments")
    rdf = _with_attachments(rdf, [
        '<z:Attachment rdf:about="#inline"><z:path>files/4/supplement</z:path>'
        '<dcterms:type>application/pdf</dcterms:type></z:Attachment>',
        _file_att("#snapshot", "files/6/Snapshot [1].html", "text/html", "Snapshot"),
        _link_att("#link", "https://example.org/a page (v2)", "Publisher [page]"),
        _file_att("#same-bytes", "files/8/copy.pdf", "application/pdf"),
    ], top='''<z:Attachment rdf:about="#standalone">
        <z:itemType>attachment</z:itemType><dc:title>Standalone paper</dc:title>
        <z:path rdf:resource="files/5/standalone.pdf"/><dcterms:type>application/pdf</dcterms:type>
        </z:Attachment>''' + _file_att("#data", "files/7/data.csv", "text/csv"))
    primary = _annotated_pdf(b"Primary")
    data = _custom_zip(rdf, {"files/3/Vaswani - 2017 - Attention.pdf": primary,
                            "files/4/supplement": _annotated_pdf(b"Supplement"),
                            "files/6/Snapshot [1].html": b"<html>snapshot</html>",
                            "files/8/copy.pdf": primary,  # the document's bytes again: no chip
                            "files/5/standalone.pdf": _annotated_pdf(b"Standalone"),
                            "files/7/data.csv": b"a,b\n1,2\n"})
    plan = _send(guest, data, preview=True).json()
    assert sum(p["kind"] == "pdf" for p in plan["pages"]) == 2
    item = next(p for p in plan["pages"] if p["title"] == "All attachments")
    assert item["attachments"] == 3 and item["folders"] == [["ML", "Transformers"]]
    assert "export/files/4/supplement" in item["source_paths"]
    assert "export/files/6/Snapshot [1].html" in item["source_paths"]
    assert next(p for p in plan["pages"] if p["title"] == "data.csv")["attachments"] == 1
    assert {e["path"]: e["status"] for e in plan["entries"]}["export/files/6/Snapshot [1].html"] == "imported"
    assert not any("separate page" in w["reason"] or "not imported" in w["reason"] for w in plan["warnings"])

    result = _send(guest, data).json()
    assert result["skipped"] == [] and result["attachments_imported"] == 4
    assert sum(p["kind"] == "pdf" for p in result["pages"]) == 2
    page_id = next(p["id"] for p in result["pages"] if p["title"] == "All attachments")
    heading, under = _attachments_of(guest, page_id)
    assert heading["content"] == "## Attachments"
    children = guest.get(f"/api/blocks/{page_id}/children").json()["children"]
    order = [c["id"] for c in children]
    note = next(c for c in children if c["properties"].get("zotero_note"))
    assert order.index(heading["id"]) > order.index(note["id"])  # after the notes
    assert [c["properties"]["zotero_attachment"] for c in under] == ["#inline", "#snapshot", "#link"]
    supplement, snapshot, link = (c["content"] for c in under)
    assert supplement.startswith("[supplement](/api/uploads/") and supplement.endswith(".pdf)")
    assert snapshot.startswith("[Snapshot 1.html](/api/uploads/") and snapshot.endswith(".html)")
    assert link == "[Publisher page](https://example.org/a%20page%20%28v2%29)"
    stored = guest.get(snapshot[snapshot.index("(") + 1:-1])
    assert stored.status_code == 200 and stored.content == b"<html>snapshot</html>"
    standalone = next(p for p in result["pages"] if p["title"] == "data.csv")
    assert [c["content"].split("](")[0] for c in _attachments_of(guest, standalone["id"])[1]] == ["[data.csv"]

    again = _send(guest, data).json()
    assert again["pages_created"] == 0 and again["annotations_imported"] == 0
    assert again["attachments_imported"] == 0
    assert [c["id"] for c in _attachments_of(guest, page_id)[1]] == [c["id"] for c in under]


def test_attachments_added_later_join_the_pages_attachments(guest):
    """A merge adds only the attachments the page lacks — by key, else by
    the file a block of it names — under its "Attachments" block, which is
    made the first time only."""
    rdf = RDF.replace("s41586-000-00000-0", "later-attachments").replace("Attention is all you need", "Later")
    files = {"files/3/Vaswani - 2017 - Attention.pdf": _annotated_pdf(b"Later primary")}
    first = _send(guest, _custom_zip(rdf, files)).json()
    page_id = next(p["id"] for p in first["pages"] if p["title"] == "Later")
    assert first["attachments_imported"] == 0 and _attachments_of(guest, page_id) == (None, [])

    files["files/4/one.txt"] = b"one"
    one = _with_attachments(rdf, [_file_att("#one", "files/4/one.txt", "text/plain"),
                                  _link_att("#site", "https://example.org/a b", "Site")])
    assert _send(guest, _custom_zip(one, files)).json()["attachments_imported"] == 2
    heading, under = _attachments_of(guest, page_id)
    assert [c["content"].split("](")[0] for c in under] == ["[one.txt", "[Site"]

    # Zotero renumbered both (new keys, the same file and URL) and added one
    files["files/9/two.txt"] = b"two"
    two = _with_attachments(rdf, [_file_att("#renumbered", "files/4/one.txt", "text/plain"),
                                  _link_att("#site-again", "https://example.org/a b", "Site"),
                                  _file_att("#two", "files/9/two.txt", "text/plain")])
    assert _send(guest, _custom_zip(two, files)).json()["attachments_imported"] == 1
    again, under = _attachments_of(guest, page_id)
    assert again["id"] == heading["id"]
    assert [c["content"].split("](")[0] for c in under] == ["[one.txt", "[Site", "[two.txt"]


def test_an_attachment_that_cannot_be_stored_is_a_warning(guest, monkeypatch):
    """A file that is not what its extension says, or one past the storage
    limits, is left out with a warning; the item and its other attachments
    go on. An executable is listed as not imported."""
    from fastapi import HTTPException
    from conftest import workspace_of
    from gamma import storage

    rdf = RDF.replace("s41586-000-00000-0", "refused-attachments").replace("Attention is all you need", "Refused")
    rdf = _with_attachments(rdf, [_file_att("#audio", "files/4/talk.m4a", "audio/mp4"),
                                  _file_att("#exe", "files/5/setup.exe", "application/octet-stream"),
                                  _link_att("#link", "https://example.org/kept", "Kept")])
    pdf = _annotated_pdf(b"Refused primary")
    data = _custom_zip(rdf, {"files/3/Vaswani - 2017 - Attention.pdf": pdf,
                            "files/4/talk.m4a": b"not audio at all",
                            "files/5/setup.exe": b"MZ"})
    plan = _send(guest, data, preview=True).json()
    assert next(p for p in plan["pages"] if p["title"] == "Refused")["attachments"] == 2
    assert {e["path"]: e["status"] for e in plan["entries"]}["export/files/5/setup.exe"] == "not_imported"
    result = _send(guest, data).json()
    assert result["skipped"] == [] and result["attachments_imported"] == 1
    assert any(w["reason"] == "Attachment not imported: not a valid audio file" for w in result["warnings"])
    page_id = next(p["id"] for p in result["pages"] if p["title"] == "Refused")
    assert [c["content"] for c in _attachments_of(guest, page_id)[1]] == ["[Kept](https://example.org/kept)"]

    def full(ws, nbytes):
        raise HTTPException(status_code=507, detail="storage quota exceeded")

    other = _annotated_pdf(b"Over quota primary")
    storage.store_pdf(workspace_of(guest_name()), other)  # stored before: only the new file meets the limit
    monkeypatch.setattr(storage, "check_upload_allowed", full)
    over = _with_attachments(RDF.replace("s41586-000-00000-0", "over-quota"),
                             [_file_att("#big", "files/6/big.csv", "text/csv")])
    data = _custom_zip(over, {"files/3/Vaswani - 2017 - Attention.pdf": other, "files/6/big.csv": b"x,y\n"})
    result = _send(guest, data).json()
    assert result["skipped"] == [] and result["pages_created"] + result["pages_merged"] == 2
    assert any(w["reason"] == "Attachment not imported: storage quota exceeded" for w in result["warnings"])


def test_invalid_pdf_and_unsupported_file_are_visible(guest):
    data = _custom_zip(RDF, {"files/3/Vaswani - 2017 - Attention.pdf": b"not a pdf",
                            "files/4/snapshot.html": b"<html>snapshot</html>"})
    preview = _send(guest, data, preview=True).json()
    assert any("not a PDF" in w["reason"] for w in preview["warnings"])
    assert any("snapshot.html" == w["title"] for w in preview["warnings"])
    result = _send(guest, data).json()
    assert result["warnings"] == preview["warnings"]


def test_preview_reports_existing_pdf_preservation(guest):
    rdf = RDF.replace("s41586-000-00000-0", "keep-existing-pdf")
    path = "files/3/Vaswani - 2017 - Attention.pdf"
    original = _custom_zip(rdf, {path: _annotated_pdf(b"Original PDF")})
    changed = _custom_zip(rdf, {path: _annotated_pdf(b"New PDF")})
    first = _send(guest, original).json()
    page_id = first["pages"][0]["id"]
    digest = guest.get(f"/api/blocks/{page_id}").json()["properties"]["doc_id"]
    plan = _send(guest, changed, preview=True).json()
    assert any("keeps its current PDF" in w["reason"] for w in plan["warnings"])
    result = _send(guest, changed).json()
    assert result["warnings"] == plan["warnings"]
    assert guest.get(f"/api/blocks/{page_id}").json()["properties"]["doc_id"] == digest


def test_preview_collapses_same_pdf_into_one_destination(guest):
    rdf = RDF.replace("s41586-000-00000-0", "same-pdf-preview")
    rdf = rdf.replace('</rdf:RDF>', '''<z:Attachment rdf:about="#same-pdf-standalone">
      <dc:title>Duplicate PDF</dc:title><z:path rdf:resource="files/5/duplicate.pdf"/>
      </z:Attachment><z:Collection rdf:about="#duplicates"><dc:title>Copies</dc:title>
      <dcterms:hasPart rdf:resource="#same-pdf-standalone"/></z:Collection></rdf:RDF>''')
    pdf = _annotated_pdf(b"Same PDF in two items")
    data = _custom_zip(rdf, {"files/3/Vaswani - 2017 - Attention.pdf": pdf, "files/5/duplicate.pdf": pdf})
    preview = _send(guest, data, preview=True).json()
    pdf_pages = [p for p in preview["pages"] if p["kind"] == "pdf"]
    assert len(pdf_pages) == 1
    assert pdf_pages[0]["folders"] == [["ML", "Transformers"], ["Copies"]]
    assert pdf_pages[0]["action"] == "create"
    result = _send(guest, data).json()
    assert len({p["id"] for p in result["pages"] if p["kind"] == "pdf"}) == 1
    again = _send(guest, data, preview=True).json()
    assert len([p for p in again["pages"] if p["kind"] == "pdf"]) == 1
    assert all(p["action"] == "merge" for p in again["pages"])


def test_one_export_imported_twice_at_once_makes_each_page_once(guest, monkeypatch):
    """A double-clicked import, or two tabs: each item's page is looked for
    again under the write lock before it is made, so the later import merges
    into it, and the PDF's annotations land once."""
    from fastapi.testclient import TestClient
    from conftest import at_once, slowed, workspace_of
    from gamma.app import app
    from gamma.db import connect_pages_db
    from gamma.routers import imports
    from gamma.storage import store_pdf

    ws = workspace_of(guest_name())
    about, preprint = "https://example.org/raced-import", "https://arxiv.org/abs/1707.99999"
    rdf = (RDF.replace("https://www.nature.com/articles/s41586-000-00000-0", about)
           .replace("https://arxiv.org/abs/1707.06347", preprint))
    pdf = _annotated_pdf(b"Imported twice at once")
    data = _custom_zip(rdf, {"files/3/Vaswani - 2017 - Attention.pdf": pdf})
    store_pdf(ws, pdf)  # stored before: the race under test is the page's, not the file's
    dest = make_folder(guest, "raced")
    slowed(monkeypatch, imports, "existing_in", 0.3)  # between the lookup and the insert
    results = at_once([lambda c=c: _send(c, data, folder=dest)
                       for c in (TestClient(app, cookies=guest.cookies) for _ in range(2))])
    assert [r.status_code for r in results] == [200, 200], results
    bodies = [r.json() for r in results]
    assert sum(b["pages_created"] for b in bodies) == 2 and sum(b["pages_merged"] for b in bodies) == 2
    assert sum(b["annotations_imported"] for b in bodies) == 1
    with connect_pages_db(ws) as conn:
        keys = [r[0] for r in conn.execute(
            "SELECT json_extract(properties, '$.zotero_key') FROM unified_blocks WHERE parent_id = 'root' "
            "AND json_extract(properties, '$.zotero_key') IN (?, ?)", (about, preprint))]
    assert sorted(keys) == sorted([about, preprint])


def test_better_bibtex_citation_key_is_kept(guest):
    """A Better BibTeX key travels in Zotero's Extra field (RDF
    dc:description). Keeping it means the .tex files that already cite the
    paper go on citing it after the import."""
    r = _post(guest)
    assert r.status_code == 200, r.text
    paper = {p["title"]: p for p in r.json()["pages"]}["Attention is all you need"]
    props = guest.get(f"/api/blocks/{paper['id']}").json()["properties"]
    assert props["cite_key"] == "vaswani2017attention"
    assert "@article{vaswani2017attention," in props["bibtex"]
    # And the bibliography export cites it under that key.
    r = guest.get(f"/api/pages/{paper['id']}/export", params={"mode": "bibtex"})
    assert r.status_code == 200, r.text
    assert "@article{vaswani2017attention," in r.text


def test_zotero_filing_refines_like_any_refiling(guest):
    """A merged page filed into a subfolder of one of its own folders leaves
    the folder above (blocks_store.refiled), in the preview and for real; a
    folder elsewhere stays."""
    ml, elsewhere = make_folder(guest, "ML"), make_folder(guest, "Elsewhere")
    pages = guest.get("/api/blocks/root/children").json()["children"]
    paper = next(p for p in pages if p["content"] == "Attention is all you need")
    guest.put(f"/api/blocks/{paper['id']}", json={"properties": {"folders": [ml, elsewhere]}})
    preview = _send(guest, _export_zip(), preview=True).json()
    planned = next(p for p in preview["pages"] if p["title"] == "Attention is all you need")
    assert planned["folders"] == [["Elsewhere"], ["ML", "Transformers"]]
    assert _send(guest, _export_zip()).status_code == 200
    props = guest.get(f"/api/blocks/{paper['id']}").json()["properties"]
    paths = folder_names(guest)
    assert [paths[f] for f in props["folders"]] == [["Elsewhere"], ["ML", "Transformers"]]
