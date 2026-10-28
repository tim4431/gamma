"""Zotero library import: a zip of the "Zotero RDF" export becomes pages with
metadata, collections→folder labels, tags→flat labels, notes→block trees,
standalone notes→note pages, and the annotations embedded in the exported
PDFs become highlight blocks."""

import base64
import html
import io
import os
import time
import zipfile

import pytest
from conftest import folder_names, guest_name, label_names, make_folder, make_page


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


def _pdf_object(v):
    """A test value as a PyPDF2 object: a str starting with "/" a name,
    any other str a string, a tuple an array, a dict a dictionary."""
    from PyPDF2.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
    if hasattr(v, "write_to_stream"):
        return v
    if isinstance(v, tuple):
        return ArrayObject(_pdf_object(x) for x in v)
    if isinstance(v, dict):
        return DictionaryObject({NameObject(k): _pdf_object(x) for k, x in v.items()})
    if isinstance(v, str):
        return NameObject(v) if v.startswith("/") else TextStringObject(v)
    return NumberObject(v) if isinstance(v, int) else FloatObject(v)


def _annot(subtype, rect, **keys):
    """An annotation dictionary, ``keys`` named without their slash."""
    return _pdf_object({"/Type": "/Annot", "/Subtype": subtype, "/Rect": rect, "/F": 4,
                        **{f"/{k}": v for k, v in keys.items()}})


def _zotero_annot(subtype, key, rect, **keys):
    """An annotation as Zotero's pdf-worker writes its own: its key as /NM
    and /Zotero:Key."""
    return _annot(subtype, rect, NM=f"Zotero-{key}", **{"Zotero:Key": key}, **keys)


def _with_annotations(*annots):
    """``_annotated_pdf``'s page carrying these annotations in place of its
    own."""
    from PyPDF2 import PdfReader, PdfWriter
    from PyPDF2.generic import NameObject

    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(_annotated_pdf())))
    del w.pages[0][NameObject("/Annots")]
    for a in annots:
        w.add_annotation(page_number=0, annotation=a)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _zotero_annotated_pdf():
    """The paper with one annotation of each kind Zotero has, keyed as its
    pdf-worker writes them into an export with "Include Annotations"
    (annotationToRaw, Zotero 9): a Zotero id on each, no /CA on the markup,
    a text annotation's own box and turn in /Zotero:* keys."""
    from PyPDF2.generic import TextStringObject

    return _with_annotations(
        _zotero_annot("/Highlight", "HLAAAAAA", (72, 716, 300, 732),
                      QuadPoints=(72, 732, 300, 732, 72, 716, 300, 716), C=(1, 0.831, 0), Contents="a highlight"),
        _zotero_annot("/Underline", "ULAAAAAA", (72, 716, 200, 732),
                      QuadPoints=(72, 732, 200, 732, 72, 716, 200, 716), C=(1, 0.4, 0.4), Contents="an underline"),
        _zotero_annot("/Text", "NTAAAAAA", (500, 700, 522, 722), C=(0.373, 0.698, 0.212), CA=1, Contents="a note"),
        _zotero_annot("/Square", "IMAAAAAA", (100, 300, 400, 500), BS={"/W": 2}, C=(0.18, 0.659, 0.898),
                      Contents="an image"),
        _zotero_annot("/Ink", "INAAAAAA", (98, 98, 302, 202), BS={"/S": "/N", "/Type": "/Border", "/W": 2},
                      InkList=((100, 100, 150, 150, 200, 120, 300, 200),), C=(0.635, 0.541, 0.898), CA=1),
        _zotero_annot("/FreeText", "TXAAAAAA", (100, 550, 300, 600), Contents="typed in Zotero", CA=1,
                      DA=TextStringObject("/Helvetica 14 Tf 0.898 0.431 0.933 rg"), **{
                          "Zotero:Rect": (100, 550, 300, 600), "Zotero:Rotation": 0, "Zotero:FontSize": 14,
                          "Zotero:Color": (0.898, 0.431, 0.933)}),
    )


def test_each_kind_of_zotero_annotation_imports(guest):
    """Zotero's highlight and underline are highlights (Gamma has no
    underline), its image an area highlight, its ink handwriting, its text
    and its note text boxes — each with its comment or text, its colour,
    and the key of the Zotero annotation it copies."""
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
    assert {content: props.get("zotero_annotation") for content, props in kids.items()} == {
        "a highlight": "HLAAAAAA", "an underline": "ULAAAAAA", "an image": "IMAAAAAA", "a note": "NTAAAAAA",
        "typed in Zotero": "TXAAAAAA", "": "INAAAAAA"}


def _imported_annotations(guest, pdf, page=None):
    """Store ``pdf`` and import its embedded annotations into ``page`` (a
    new page holding it when None) → ``(page, the import's answer, the
    page's imported blocks' properties by content)``."""
    up = guest.post("/api/uploads", files={"file": ("a.pdf", pdf, "application/pdf")})
    assert up.status_code == 200, up.text
    if page is None:
        page = make_page(guest, "Embedded annotations",
                         {"doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"]})
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": up.json()["doc_id"]})
    assert r.status_code == 200, r.text
    kids = guest.get(f"/api/blocks/{page['id']}/children").json()["children"]
    return page, r.json(), {c["content"]: c["properties"] for c in kids if c["properties"].get("imported_annot")}


def test_embedded_alpha_is_gamma_alpha_before_ca(guest):
    """A highlight's or area's alpha is Gamma's /GammaAlpha when it has
    one, which a Zotero copy writes in place of /CA, even beside a /CA
    another app added; else its /CA; else the 0.45 of an annotation with
    neither, as Zotero's own are."""
    quads = {"QuadPoints": (72, 732, 300, 732, 72, 716, 300, 716)}
    _, result, kids = _imported_annotations(guest, _with_annotations(
        _annot("/Highlight", (72, 716, 300, 732), C=(1, 0, 0), GammaAlpha=0.3, Contents="gamma alpha", **quads),
        _annot("/Square", (100, 300, 400, 500), C=(0, 0, 1), GammaAlpha=0.6, CA=0.2, Contents="both"),
        _annot("/Highlight", (72, 616, 300, 632), C=(0, 1, 0), CA=0.8, Contents="ca only"),
        _annot("/Highlight", (72, 516, 300, 532), C=(1, 1, 0), Contents="neither")))
    assert result["imported"] == 4
    assert {content: props["color"] for content, props in kids.items()} == {
        "gamma alpha": "#ff00004c", "both": "#0000ff99", "ca only": "#00ff00cc", "neither": "#ffff0073"}


def test_a_gamma_zotero_copy_brings_its_colours_back(guest):
    """The copy a Zotero RDF export writes (``annotate_pdf(zotero=True)``)
    carries no /CA on its highlight and area note, so Zotero shows their
    true colour, and reads back here at the alpha they had. Its area
    note's /NM is shaped like a Zotero key but names no Zotero annotation."""
    from gamma.colors import normalize_color
    from gamma.pdf_export import annotate_pdf

    def at(y):
        rect = {"x1": 72, "y1": y, "x2": 300, "y2": y + 16}
        return {"pageNumber": 1, "width": 612, "height": 792, "boundingRect": rect, "rects": [rect]}

    colors = {"washed": "rgba(255, 212, 0, 0.3)", "figure": "rgba(46, 168, 229, 0.65)"}
    pdf, written = annotate_pdf(_with_annotations(), [
        {"position": at(60), "color": colors["washed"], "note": "washed", "id": "hl-washed"},
        {"position": {**at(200), "area": True}, "color": colors["figure"], "note": "figure", "id": "hl-figure"}],
        zotero=True)
    assert written == 2
    _, _, kids = _imported_annotations(guest, pdf)
    assert {content: props["color"] for content, props in kids.items()} == {
        content: normalize_color(color) for content, color in colors.items()}
    assert not any("zotero_annotation" in props for props in kids.values())


def test_a_zotero_key_names_only_the_blocks_an_import_makes(guest):
    """The key comes with a block made now; a block imported earlier from
    the same annotation without it is left as it is, and a /Zotero:Key
    that is no Zotero key names nothing."""
    quads = {"QuadPoints": (72, 732, 300, 732, 72, 716, 300, 716)}
    earlier = _annot("/Highlight", (72, 716, 300, 732), C=(1, 0.831, 0), Contents="imported before", **quads)
    page, _, kids = _imported_annotations(guest, _with_annotations(earlier))
    assert "zotero_annotation" not in kids["imported before"]
    _, result, kids = _imported_annotations(guest, _with_annotations(
        _zotero_annot("/Highlight", "HLBBBBBB", (72, 716, 300, 732), C=(1, 0.831, 0), Contents="imported before",
                      **quads),
        _zotero_annot("/Square", "IMBBBBBB", (100, 300, 400, 500), C=(0, 0, 1), Contents="new now"),
        _annot("/Square", (100, 100, 200, 200), C=(0, 0, 1), Contents="no key", **{"Zotero:Key": "not a key"})),
        page=page)
    assert result["imported"] == 2
    assert {content: props.get("zotero_annotation") for content, props in kids.items()} == {
        "imported before": None, "new now": "IMBBBBBB", "no key": None}


def test_zotero_key_lookup_uses_its_index(guest):
    """The page an item's Zotero key made is found through
    idx_ub_zotero_key (db.PAGES_SCHEMA), not by reading every page: each
    query the lookup runs on the key is planned on that index."""
    from conftest import workspace_of
    from gamma.db import connect_pages_db
    from gamma.routers.imports import _zotero_existing_page

    page = make_page(guest, "Found by its key", {"zotero_key": "#item_424242",
                                                 "meta": {"title": "Found by its key", "doi": ""}})
    item = {"key": "#item_424242", "digest": "", "title": "Found by its key",
            "meta": {"title": "Found by its key", "doi": ""}}
    ran = []
    with connect_pages_db(workspace_of(guest_name())) as conn:
        conn.set_trace_callback(ran.append)
        try:
            row = _zotero_existing_page(conn, item)
        finally:
            conn.set_trace_callback(None)
        lookups = [sql for sql in ran if "zotero_key" in sql]
        plans = [" ".join(step[-1] for step in conn.execute(f"EXPLAIN QUERY PLAN {sql}")) for sql in lookups]
    assert row[0] == page["id"]
    assert plans and all("USING INDEX idx_ub_zotero_key" in plan for plan in plans), plans


def test_imported_blocks_are_found_in_the_pages_subtree_only(guest):
    """The blocks an import made under a page are found by walking the
    page's subtree, each block read as it is reached, never by reading
    every block of the workspace: a Zotero import looks twice per
    annotated PDF, in a library its note trees make large."""
    from conftest import workspace_of
    from gamma.db import connect_pages_db
    from gamma.routers.imports import _imported_blocks

    square = _annot("/Square", (100, 100, 200, 200), C=(0, 0, 1), Contents="found")
    page, _, kids = _imported_annotations(guest, _with_annotations(square))
    ran = []
    with connect_pages_db(workspace_of(guest_name())) as conn:
        conn.set_trace_callback(ran.append)
        try:
            rows = _imported_blocks(conn, page["id"])
        finally:
            conn.set_trace_callback(None)
        [sql] = [sql for sql in ran if "imported_annot" in sql]
        steps = [step[-1] for step in conn.execute(f"EXPLAIN QUERY PLAN {sql}")]
    assert [key for _, key, _ in rows] == [kids["found"]["imported_annot"]]
    assert all(step == "SCAN sub" for step in steps if step.startswith("SCAN")), steps


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


# --- the commit: local keys, note trees and pictures, note pages, Zotero's fields, stored PDFs ---

def _library():
    """A library of its own (a new guest account's): its client and its
    workspace."""
    from conftest import fresh_client, workspace_of
    c = fresh_client()
    r = c.post("/api/login-guest")
    assert r.status_code == 200, r.text
    return c, workspace_of(r.json()["username"])


def _rdf(*elements):
    """An RDF export of ``elements``, in RDF's namespaces."""
    return RDF[:RDF.index(">") + 1] + "".join(elements) + "</rdf:RDF>"


def _article(about, title, *children):
    return (f'<bib:Article rdf:about="{about}"><z:itemType>journalArticle</z:itemType>'
            f'<dc:title>{title}</dc:title>{"".join(children)}</bib:Article>')


def _memo(about, note, *children):
    """A Zotero note, ``note`` its HTML."""
    return (f'<bib:Memo rdf:about="{about}"><z:itemType>note</z:itemType>{"".join(children)}'
            f'<rdf:value>{html.escape(note, quote=False)}</rdf:value></bib:Memo>')


def _tree(client, block_id):
    """The block and its subtree as ``{content, children}``, in order."""
    def shape(node):
        return {"content": node["content"], "children": [shape(c) for c in node["children"]]}
    r = client.get(f"/api/blocks/{block_id}/subtree")
    assert r.status_code == 200, r.text
    return shape(r.json()["block"])


def _notes_of(client, page_id):
    return [c for c in client.get(f"/api/blocks/{page_id}/children").json()["children"]
            if c["properties"].get("zotero_note")]


def test_a_local_item_key_merges_only_into_the_same_work():
    """Zotero names an item #item_<itemID>, a number local to one Zotero
    database, so two libraries both hold an #item_1: the second library's,
    another work, makes a page of its own instead of landing on the first's.
    The same work merges, by its DOI though retitled, else by its title, in
    the preview as in the import. A URL key names the work itself and
    merges whatever the title says, as before."""
    c, _ = _library()
    alpha_doi = "<dc:identifier>DOI 10.1000/alpha</dc:identifier>"
    [alpha] = _send(c, _custom_zip(_rdf(_article("#item_1", "Alpha paper", alpha_doi)), {})).json()["pages"]
    beta = _custom_zip(_rdf(_article("#item_1", "Beta paper")), {})
    assert [p["action"] for p in _send(c, beta, preview=True).json()["pages"]] == ["create"]
    d = _send(c, beta).json()
    [beta_page] = d["pages"]
    assert d["pages_created"] == 1 and beta_page["id"] != alpha["id"]

    retitled = _rdf(_article("#item_1", "Alpha, revised", "<dc:identifier>DOI 10.1000/ALPHA</dc:identifier>"))
    for rdf, page in ((retitled, alpha), (_rdf(_article("#item_1", "BETA paper.")), beta_page)):
        data = _custom_zip(rdf, {})
        [planned] = _send(c, data, preview=True).json()["pages"]
        assert (planned["action"], planned["existing_id"]) == ("merge", page["id"])
        d = _send(c, data).json()
        assert d["pages_merged"] == 1 and [p["id"] for p in d["pages"]] == [page["id"]]
    other_doi = _rdf(_article("#item_1", "Alpha paper", "<dc:identifier>DOI 10.1000/other</dc:identifier>"))
    assert _send(c, _custom_zip(other_doi, {})).json()["pages_created"] == 1

    url = "https://example.org/a-work-named-by-its-url"
    [page] = _send(c, _custom_zip(_rdf(_article(url, "Named by its URL")), {})).json()["pages"]
    d = _send(c, _custom_zip(_rdf(_article(url, "Renamed in Zotero")), {})).json()
    assert d["pages_merged"] == 1 and d["pages"][0]["id"] == page["id"]


_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9a"
                        "wAAAABJRU5ErkJggg==")


def _data_uri(data, mime="image/png"):
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


TREE_NOTE = ('<div data-schema-version="9"><h1>Reading notes</h1><p>Read <strong>twice</strong>.</p>'
             "<ul><li>outer<ul><li>inner</li></ul></li><li>second</li></ul>"
             f'<p>A figure: <img src="{_data_uri(_PNG)}"/></p></div>')


def _tree_note(image_url):
    """TREE_NOTE as its block tree, its picture stored at ``image_url``."""
    return {"content": "# Reading notes", "children": [
        {"content": "Read **twice**.", "children": []},
        {"content": "outer", "children": [{"content": "inner", "children": []}]},
        {"content": "second", "children": []},
        {"content": f"A figure: ![]({image_url})", "children": []}]}


def test_a_note_is_its_block_tree_and_comes_back_as_it_went(monkeypatch):
    """A Zotero note is a block subtree on its page: its first block the
    root, named by the note's key, the rest under it in order, however the
    merge's op batch is cut. Its picture is stored once, and an attachment
    of the same picture makes no chip of its own. The same export again,
    or the Zotero RDF export Gamma makes of the page, adds nothing to the
    library; into another library the export brings the same tree, the
    stored files it names claimed from the upload GC's orphans."""
    from gamma.db import connect_pages_db, page_now
    from gamma.routers import imports
    from gamma.storage import content_digest

    c, _ = _library()
    url, pdf = "https://example.org/a-tree-note", _annotated_pdf(b"A paper with a tree note")
    files = {"files/3/paper.pdf": pdf, "files/7/figure.png": _PNG}
    pdf_att = _file_att("#item_3", "files/3/paper.pdf", "application/pdf")
    first = _rdf(_article(url, "Tree note", '<link:link rdf:resource="#item_3"/>'), pdf_att)
    page_id = _send(c, _custom_zip(first, files)).json()["pages"][0]["id"]
    with_note = _custom_zip(_rdf(
        _article(url, "Tree note", '<link:link rdf:resource="#item_3"/>', '<link:link rdf:resource="#item_7"/>',
                 '<dcterms:isReferencedBy rdf:resource="#item_9"/>'),
        pdf_att, _file_att("#item_7", "files/7/figure.png", "image/png"), _memo("#item_9", TREE_NOTE)), files)
    monkeypatch.setattr(imports, "MAX_OPS", 2)  # the merge's op batch, cut into pieces
    d = _send(c, with_note).json()
    monkeypatch.undo()
    assert (d["pages_merged"], d["notes_imported"], d["attachments_imported"], d["skipped"]) == (1, 1, 0, []), d
    name = f"{content_digest(_PNG)}.png"
    image_url = f"/api/uploads/{name}"
    [note] = _notes_of(c, page_id)
    assert note["properties"] == {"zotero_note": "#item_9"}
    assert _tree(c, note["id"]) == _tree_note(image_url)
    assert c.get(image_url).content == _PNG
    assert _attachments_of(c, page_id) == (None, [])

    before = _tree(c, page_id)
    exported = c.get(f"/api/pages/{page_id}/export", params={"mode": "zotero-rdf"})
    assert exported.status_code == 200, exported.text
    for data in (with_note, exported.content):
        d = _send(c, data).json()
        assert (d["pages_created"], d["pages_merged"], d["notes_imported"], d["attachments_imported"]) == (0, 1, 0, 0)
        assert _tree(c, page_id) == before

    other, other_ws = _library()
    with connect_pages_db(other_ws) as conn:  # the picture was stored there once and let go of
        conn.execute("INSERT INTO upload_orphans (name, since) VALUES (?, ?)", (name, page_now()))
        conn.commit()
    d = _send(other, exported.content).json()
    assert (d["pages_created"], d["notes_imported"], d["attachments_imported"], d["skipped"]) == (1, 1, 0, []), d
    [copy] = _notes_of(other, d["pages"][0]["id"])
    assert copy["properties"] == {"zotero_note": f"#gamma_note_{note['id']}"}  # the export's key for it
    assert _tree(other, copy["id"]) == _tree_note(image_url)
    assert other.get(image_url).content == _PNG
    with connect_pages_db(other_ws) as conn:
        assert conn.execute("SELECT COUNT(*) FROM upload_orphans WHERE name = ?", (name,)).fetchone()[0] == 0


def test_a_note_picture_that_is_not_stored_is_a_placeholder_and_a_warning(monkeypatch):
    """A note's picture of a type Gamma does not store, one past
    NOTE_IMAGE_MAX_BYTES, or one past the storage quota is "(image)" in
    the note, with a warning; the note comes in all the same."""
    from fastapi import HTTPException
    from gamma import storage
    from gamma.routers import imports

    def full(ws, nbytes):
        raise HTTPException(status_code=507, detail="storage quota exceeded")

    c, _ = _library()
    note = (f'<p>tiff <img src="{_data_uri(_PNG, "image/tiff")}"/></p>'
            f'<p>big <img src="{_data_uri(_PNG + bytes(1000))}"/></p>'
            f'<p>full <img src="{_data_uri(_PNG)}"/></p>')
    data = _custom_zip(_rdf(_article("https://example.org/unstored-pictures", "Unstored pictures",
                                     '<dcterms:isReferencedBy rdf:resource="#item_9"/>'), _memo("#item_9", note)), {})
    monkeypatch.setattr(imports, "NOTE_IMAGE_MAX_BYTES", 500)
    monkeypatch.setattr(storage, "check_upload_allowed", full)
    d = _send(c, data).json()
    assert (d["pages_created"], d["notes_imported"], d["skipped"]) == (1, 1, [])
    [block] = _notes_of(c, d["pages"][0]["id"])
    assert _tree(c, block["id"]) == {"content": "tiff (image)", "children": [
        {"content": "big (image)", "children": []}, {"content": "full (image)", "children": []}]}
    reasons = sorted(w["reason"] for w in d["warnings"] if w["reason"].startswith("Note image"))
    assert reasons[:1] + reasons[2:] == ["Note image not imported: image/tiff is not a picture Gamma stores",
                                         "Note image not imported: storage quota exceeded"]
    assert reasons[1].startswith("Note image not imported: larger than")


STANDALONE_NOTE = ('<div data-schema-version="9"><h2>Ideas <em>later</em></h2>'
                   "<p>One</p><ul><li>two</li></ul></div>")


def test_a_standalone_note_is_a_note_page():
    """A note no item holds (Zotero's standalone note) is a page with no
    PDF: titled by its first line, its body the rest of the note, filed in
    its collections, labelled with its tags, ``zotero_key`` and
    ``zotero_note`` its key. The review lists it as a page that misses
    nothing. Imported again, and as the standalone note Gamma's Zotero RDF
    export makes of it, it is that page, refiled and relabelled only."""
    c, _ = _library()

    def library(*collections, tags=("idea",)):
        subjects = "".join(f"<dc:subject>{tag}</dc:subject>" for tag in tags)
        return _custom_zip(_rdf(_memo("#item_20", STANDALONE_NOTE, subjects), *(
            f'<z:Collection rdf:about="#collection_{i}"><dc:title>{name}</dc:title>'
            '<dcterms:hasPart rdf:resource="#item_20"/></z:Collection>' for i, name in enumerate(collections))), {})

    [planned] = _send(c, library("Ideas"), preview=True).json()["pages"]
    assert (planned["title"], planned["kind"], planned["action"], planned["missing"]) == (
        "Ideas later", "page", "create", False)
    assert planned["folders"] == [["Ideas"]] and planned["warnings"] == []
    d = _send(c, library("Ideas")).json()
    assert (d["pages_created"], d["notes_imported"], d["skipped"], d["warnings"]) == (1, 0, [], [])
    page_id = d["pages"][0]["id"]
    body = {"content": "Ideas later", "children": [{"content": "One", "children": []},
                                                   {"content": "two", "children": []}]}
    assert _tree(c, page_id) == body

    def filing():
        props = c.get(f"/api/blocks/{page_id}").json()["properties"]
        assert props["zotero_key"] == props["zotero_note"] == "#item_20" and "doc_id" not in props
        return (sorted(folder_names(c)[f] for f in props["folders"]),
                [label_names(c)[label] for label in props["labels"]])

    assert filing() == ([["Ideas"]], ["idea"])
    d = _send(c, library("Ideas", "Later", tags=("idea", "soon"))).json()
    assert (d["pages_created"], d["pages_merged"], d["notes_imported"]) == (0, 1, 0)
    assert filing() == ([["Ideas"], ["Later"]], ["idea", "soon"]) and _tree(c, page_id) == body

    exported = c.get(f"/api/pages/{page_id}/export", params={"mode": "zotero-rdf"})
    assert exported.status_code == 200, exported.text
    d = _send(c, exported.content).json()
    assert (d["pages_created"], d["pages_merged"]) == (0, 1)
    assert filing() == ([["Ideas"], ["Later"]], ["idea", "soon"]) and _tree(c, page_id) == body


def test_what_zotero_holds_beyond_the_record_stays_with_it():
    """The item's ``rdf`` (the fields Gamma's record has no place for) is
    the page's ``zotero_rdf`` when the import sets the page's record, and
    later on a page of the same work that keeps none; never on a page whose
    record is another work's now."""
    from gamma.zotero_import import plan_zotero_archive

    c, _ = _library()
    url = "https://example.org/kept-zotero-fields"
    data = _custom_zip(_rdf(_article(url, "Kept Zotero fields", "<dc:date>2017-06-12</dc:date>",
                                     "<dcterms:abstract>What it found.</dcterms:abstract>")), {})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        [item] = plan_zotero_archive(zf)["items"]
    rdf = item["rdf"]
    assert rdf["date"] == "2017-06-12" and any("What it found." in el for el in rdf["item"])
    page_id = _send(c, data).json()["pages"][0]["id"]

    def props():
        return c.get(f"/api/blocks/{page_id}").json()["properties"]

    assert props()["zotero_rdf"] == rdf

    def imported_onto(meta):
        edited = c.put(f"/api/blocks/{page_id}", json={"properties": {"zotero_rdf": None, "meta": meta}})
        assert edited.status_code == 200, edited.text
        assert _send(c, data).json()["pages_merged"] == 1
        return props().get("zotero_rdf")

    meta = props()["meta"]
    assert imported_onto({"title": "Another work entirely", "doi": ""}) is None
    assert imported_onto({**meta, "title": "KEPT zotero fields!"}) == rdf


def test_a_pdf_the_library_stores_already_is_not_streamed_again(monkeypatch):
    """An item's PDF the workspace stores already (a page of it deleted, a
    file chip of it) is not copied from the zip again, only re-dated, as
    storing it again would, so the upload GC keeps it; a PDF it lacks is
    streamed in."""
    from gamma import storage
    from gamma.routers import imports

    c, ws = _library()
    stored, new = _annotated_pdf(b"Stored before the import"), _annotated_pdf(b"New with the import")
    doc_id, _ = storage.store_pdf(ws, stored)
    path = storage.open_path(ws, f"{doc_id}.pdf")
    os.utime(path, (time.time() - 86400, time.time() - 86400))
    streamed, real = [], imports.store_pdf_stream
    monkeypatch.setattr(imports, "store_pdf_stream", lambda ws_, src: streamed.append(src.name) or real(ws_, src))
    rdf = _rdf(_article("https://example.org/stored-before", "Stored before", '<link:link rdf:resource="#item_3"/>'),
               _file_att("#item_3", "files/3/stored.pdf", "application/pdf"),
               _article("https://example.org/new-with-it", "New with it", '<link:link rdf:resource="#item_4"/>'),
               _file_att("#item_4", "files/4/new.pdf", "application/pdf"))
    d = _send(c, _custom_zip(rdf, {"files/3/stored.pdf": stored, "files/4/new.pdf": new})).json()
    assert (d["pages_created"], d["pdfs_stored"], d["annotations_imported"]) == (2, 1, 2)
    assert streamed == ["export/files/4/new.pdf"]
    by_title = {p["title"]: p["id"] for p in d["pages"]}
    assert c.get(f"/api/blocks/{by_title['Stored before']}").json()["properties"]["doc_id"] == doc_id
    assert time.time() - path.stat().st_mtime < 600


def test_a_merge_that_fails_leaves_its_page_as_it_was(monkeypatch):
    """A merge longer than one op batch (MAX_OPS) is written in one
    transaction: a failure after its first piece leaves the page as it
    was, so a later import brings the note in whole, in order. A note
    root holding only its first blocks would stay so, as every later
    import takes a note found under its key for the whole note."""
    from gamma import ops

    c, _ = _library()
    url = "https://example.org/a-long-merge"
    page_id = _send(c, _custom_zip(_rdf(_article(url, "A long merge")), {})).json()["pages"][0]["id"]
    before = c.get(f"/api/blocks/{page_id}").json()["properties"]
    items = [f"item {i}" for i in range(ops.MAX_OPS + 100)]
    data = _custom_zip(_rdf(
        _article(url, "A long merge", "<dc:subject>later</dc:subject>",
                 '<dcterms:isReferencedBy rdf:resource="#item_9"/>'),
        _memo("#item_9", "<p>root</p><ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")), {})

    pieces, real = [], ops._apply

    def second_piece_fails(conn, target, batch, **kwargs):
        if target == page_id:
            pieces.append(len(batch))
            if len(pieces) == 2:
                raise RuntimeError("stopped between pieces")
        return real(conn, target, batch, **kwargs)

    monkeypatch.setattr(ops, "_apply", second_piece_fails)
    d = _send(c, data).json()
    assert pieces == [ops.MAX_OPS, 102]  # the page's labels and the note's root first
    assert d["pages_merged"] == 0 and [s["reason"] for s in d["skipped"]] == ["stopped between pieces"]
    assert _notes_of(c, page_id) == []
    assert c.get(f"/api/blocks/{page_id}").json()["properties"] == before

    monkeypatch.setattr(ops, "_apply", real)
    d = _send(c, data).json()
    assert (d["pages_merged"], d["notes_imported"], d["skipped"]) == (1, 1, [])
    [note] = _notes_of(c, page_id)
    tree = _tree(c, note["id"])
    assert tree["content"] == "root" and [child["content"] for child in tree["children"]] == items


def test_a_note_block_longer_than_the_editor_saves_is_cut():
    """A note's block past ops.MAX_CONTENT characters, the most the editor
    saves, is cut there with a warning, on a merged page as on a new one,
    where the merge's op batch would refuse it and the item with it."""
    from gamma.ops import MAX_CONTENT

    c, _ = _library()
    url = "https://example.org/a-long-note"
    _send(c, _custom_zip(_rdf(_article(url, "A long note")), {}))
    data = _custom_zip(_rdf(_article(url, "A long note", '<dcterms:isReferencedBy rdf:resource="#item_9"/>'),
                            _memo("#item_9", f"<p>root</p><p>{'y' * (MAX_CONTENT + 10)}</p>")), {})
    for client, made in ((c, (0, 1)), (_library()[0], (1, 0))):
        d = _send(client, data).json()
        assert (d["pages_created"], d["pages_merged"], d["notes_imported"], d["skipped"]) == (*made, 1, [])
        assert f"Note text cut: a block longer than {MAX_CONTENT:,} characters" in [w["reason"] for w in d["warnings"]]
        [note] = _notes_of(client, d["pages"][0]["id"])
        assert _tree(client, note["id"])["children"] == [{"content": "y" * MAX_CONTENT, "children": []}]


def test_a_note_given_as_its_text_alone_comes_in(monkeypatch):
    """A plan whose note has its text and no tree (zotero-cloud's Web API
    items, ``{key, text, …}``) brings the note in as one block."""
    from gamma.routers import imports

    key = "http://zotero.org/users/1/items/ABCD2345"
    item = {"key": key, "title": "A cloud paper", "meta": {"title": "A cloud paper", "doi": "", "authors": []},
            "cite_key": "", "tags": [], "folders": [], "warnings": [], "pdf_entry": None, "digest": None,
            "extras": [], "selection_id": "zotero:ABCD2345",
            "notes": [{"key": "http://zotero.org/users/1/items/NOTE2345", "text": "my note"}]}
    monkeypatch.setattr(imports, "_zotero_plan", lambda zf, digests=None: {"items": [item], "warnings": []})
    c, _ = _library()
    d = _send(c, _custom_zip(_rdf(), {})).json()
    assert (d["pages_created"], d["notes_imported"], d["skipped"]) == (1, 1, [])
    [note] = _notes_of(c, d["pages"][0]["id"])
    assert _tree(c, note["id"]) == {"content": "my note", "children": []}


def test_a_note_too_deep_for_its_readers_still_brings_its_item():
    """A note nested past NOTE_DEPTH_MAX comes in as its text with a warning,
    and its item, and a standalone note as deep, are made all the same."""
    from gamma.zotero_import import NOTE_DEPTH_MAX

    depth = NOTE_DEPTH_MAX + 100
    deep = "<p>top</p>" + "<ul><li>x" * depth + "</li></ul>" * depth
    c, _ = _library()
    data = _custom_zip(_rdf(
        _article("https://example.org/deep-note", "Deep note", '<dcterms:isReferencedBy rdf:resource="#item_9"/>'),
        _memo("#item_9", deep), _memo("#item_20", deep)), {})
    d = _send(c, data).json()
    assert (d["pages_created"], d["notes_imported"], d["skipped"]) == (2, 1, []), d
    reasons = [w["reason"] for w in d["warnings"]]
    assert reasons.count("A note nested too deeply to keep its structure came in as plain text") == 2


def test_a_record_retitled_in_gamma_still_knows_its_zotero_item():
    """A page whose record was edited in Gamma is still the item its Zotero
    record (``zotero_rdf``'s ``work``) names, so the item merges into it;
    a page of another work under the same #item key still does not."""
    c, _ = _library()
    book = _rdf(_article("#item_5", "Optics of fibres", "<dc:date>2019-03-14</dc:date>"))
    [page] = _send(c, _custom_zip(book, {})).json()["pages"]
    props = c.get(f"/api/blocks/{page['id']}").json()["properties"]
    assert props["zotero_rdf"]["work"]["title"] == "Optics of fibres"
    edited = c.put(f"/api/blocks/{page['id']}", json={"properties": {"meta": {**props["meta"], "title": "Fibre optics"}}})
    assert edited.status_code == 200, edited.text
    data = _custom_zip(book, {})
    [planned] = _send(c, data, preview=True).json()["pages"]
    assert (planned["action"], planned["existing_id"]) == ("merge", page["id"])
    d = _send(c, data).json()
    assert (d["pages_created"], d["pages_merged"]) == (0, 1) and d["pages"][0]["id"] == page["id"]
    other = _rdf(_article("#item_5", "Something else entirely", "<dc:date>2001</dc:date>"))
    assert _send(c, _custom_zip(other, {})).json()["pages_created"] == 1
