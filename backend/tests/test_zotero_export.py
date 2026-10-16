"""Zotero RDF export: a page or folder → .rdf + files/ tree in a zip, the
import's exact inverse — verified by round-tripping through
``zotero_import.parse_zotero_rdf`` and the real ``/api/import/zotero``.
What the PDF copies carry follows Zotero 9's pdf-worker (readRawAnnotation):
an /Ink or /FreeText needs a Zotero id, a /FreeText its /Zotero:* keys."""

import io
import json
import zipfile

from conftest import folder_names, fresh_client, make_folder, make_label, make_page
from PyPDF2 import PdfReader

from gamma import ink as inkmod
from gamma.zotero_import import parse_zotero_rdf


def _blank_pdf_bytes():
    from PyPDF2 import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _put_children(guest, page_id, tree):
    r = guest.put(f"/api/blocks/{page_id}/children", json={"blocks": tree})
    assert r.status_code == 200, r.text


def _positioned(hid, quote, note=""):
    rect = {"x1": 50.0, "y1": 60.0, "x2": 250.0, "y2": 160.0}
    return {
        "id": hid, "content": note, "children": [],
        "properties": {
            "quote": quote, "color": "rgba(170, 235, 170, 0.65)",
            "pdf_position": {"pageNumber": 1, "width": 800.0, "height": 1035.0, "boundingRect": rect, "rects": [rect]},
        },
    }


def _zip_of(r):
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(r.content))


def _rdf_items(z):
    rdf_name = next(n for n in z.namelist() if n.endswith(".rdf"))
    text = z.read(rdf_name).decode("utf-8")
    # "rdf:resource" is an RDF/XML syntax term — as an ELEMENT it's invalid
    # RDF/XML (Zotero happens to tolerate it, strict parsers like rdflib
    # don't). Attachment paths travel in z:path only, like Zotero's own export.
    assert "<rdf:resource" not in text
    return parse_zotero_rdf(text)


def _pdf_annots(z, item):
    """The annotations on the first page of an item's PDF copy."""
    base = next(n for n in z.namelist() if n.endswith(".rdf")).rsplit("/", 1)[0]
    page = PdfReader(io.BytesIO(z.read(f"{base}/{item['pdf_paths'][0]}"))).pages[0]
    return [a.get_object() for a in page.get("/Annots") or []]


def _ink_block(guest, bid, caption):
    """A handwriting group on PDF page 1, its strokes uploaded."""
    ink = {"format": "gamma-ink", "version": 1,
           "space": {"kind": "pdf-page", "page": 1, "width": 612, "height": 792},
           "strokes": [{"id": "s1", "tool": "pen", "color": "#1d4ed8", "size": 2, "opacity": 1, "pen": True,
                        "t0": 1757760000000, "ch": "xypt",
                        "pts": inkmod.encode_points([{"x": 100 + 10 * i, "y": 400 + 3 * i, "p": 0.5, "t": 16 * i}
                                                     for i in range(6)], "xypt")}]}
    up = guest.post("/api/upload-ink", content=json.dumps(ink))
    assert up.status_code == 200, up.text
    return {"id": bid, "content": caption, "children": [],
            "properties": {"ink_url": up.json()["url"], "pdf_position": up.json()["pdf_position"], "ink_strokes": 1}}


def _other_library():
    """A client signed in to a guest account of its own: another library."""
    other = fresh_client()
    assert other.post("/api/login-guest").status_code == 200
    return other


def _paper(guest, prefix):
    """Block ids and the guest DB persist across tests in a run, so every id,
    title and folder here is namespaced by ``prefix``."""
    up = guest.post("/api/uploads", files={"file": ("p.pdf", _blank_pdf_bytes(), "application/pdf")})
    assert up.status_code == 200, up.text
    page = make_page(guest, f"Attention {prefix}", properties={
        "doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"],
        "folders": [make_folder(guest, f"{prefix}ML/Transformers")],
        "labels": [make_label(guest, "transformers"), make_label(guest, "attention")],
        "meta": {"title": f"Attention {prefix}",
                 "authors": ["Ashish Vaswani", "Noam Shazeer"],
                 "year": "2017", "venue": "Nature", "volume": "647",
                 "pages": "1-11", "doi": "10.1038/s41586-000-00000-0",
                 "arxiv_id": "1706.03762"},
    })
    _put_children(guest, page["id"], [
        _positioned(f"{prefix}h1", "the quoted passage", note="what I thought"),
        {"id": f"{prefix}n1", "content": "Read this **twice**.", "properties": {}, "children": [
            {"id": f"{prefix}n1a", "content": "sub point", "properties": {}, "children": []},
        ]},
    ])
    return page


def test_page_zotero_export_roundtrips_through_parser(guest):
    page = _paper(guest, "zxa")
    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf"}))
    items = _rdf_items(z)
    assert len(items) == 1
    it = items[0]
    assert it["title"] == "Attention zxa"
    meta = it["meta"]
    assert meta["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert meta["year"] == "2017"
    # DOI travels on the journal record, venue/volume with it — like Zotero's own export
    assert meta["venue"] == "Nature" and meta["volume"] == "647"
    assert meta["doi"] == "10.1038/s41586-000-00000-0"
    assert meta["arxiv_id"] == "1706.03762"
    assert meta["pages"] == "1-11"
    assert it["tags"] == ["transformers", "attention"]
    assert it["folders"] == [["zxaML", "Transformers"]]  # one page: its folder's whole path
    # the free note (with its child) came back as one note, markdown intact
    assert len(it["notes"]) == 1
    assert "Read this **twice**." in it["notes"][0]["text"]
    assert "- sub point" in it["notes"][0]["text"]

    # the PDF is in the zip under the path the RDF points at, highlights embedded
    assert len(it["pdf_paths"]) == 1
    base = next(n for n in z.namelist() if n.endswith(".rdf")).rsplit("/", 1)[0]
    pdf = z.read(f"{base}/{it['pdf_paths'][0]}")
    assert pdf.startswith(b"%PDF") and b"/Highlight" in pdf


_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c626001000000ffff03000006000557"
    "bfabd40000000049454e44ae426082")


def test_zotero_export_note_images(guest):
    """Pasted images: embedded as data URIs inside the Zotero note, attached
    to the item as an image attachment, and replaced by a plain placeholder in
    the annotation comment (comments can't hold pictures)."""
    from PyPDF2 import PdfReader

    page = _paper(guest, "zxi")
    img = guest.post("/api/upload-image", files={"file": ("fig.png", _PNG, "image/png")})
    assert img.status_code == 200, img.text
    img_url = img.json()["url"]  # /api/uploads/<sha>.png
    img_name = img_url.rsplit("/", 1)[-1]
    _put_children(guest, page["id"], [
        _positioned("zxih", "quoted", note=f"see ![fig]({img_url})"),
        {"id": "zxin", "content": f"figure: ![fig]({img_url})", "properties": {}, "children": []},
    ])

    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf"}))
    base = next(n for n in z.namelist() if n.endswith(".rdf")).rsplit("/", 1)[0]
    rdf = z.read(f"{base}/{base}.rdf").decode("utf-8")

    # image file rides in the zip and as an item attachment in the RDF
    assert z.read(f"{base}/files/1/{img_name}") == _PNG
    assert f"files/1/{img_name}" in rdf and "image/png" in rdf
    # the note embeds it as a data URI (Zotero keeps those in notes)
    assert "data:image/png;base64," in rdf
    # the annotation comment gets the plain placeholder, not raw markdown
    it = _rdf_items(z)[0]
    pdf = z.read(f"{base}/{it['pdf_paths'][0]}")
    annots = PdfReader(io.BytesIO(pdf)).pages[0]["/Annots"]
    contents = [str(a.get_object().get("/Contents") or "") for a in annots]
    assert any(f"(image: {img_name} — see item notes)" in c for c in contents)
    assert not any("![fig]" in c for c in contents)
    # ...and, since comments can't hold pictures, the highlight's writing ALSO
    # became a Zotero note with a page+quote header (the memo the placeholder
    # points at). Top-level note + highlight note = 2 memos.
    notes = [n["text"] for n in it["notes"]]
    assert len(notes) == 2
    hl_note = next(n for n in notes if "p.1" in n)
    assert "“quoted”" in hl_note and "see" in hl_note


def test_zotero_export_puts_a_text_box_on_the_pdf_copy(guest):
    """A text box on a PDF page rides in the PDF copy as the /FreeText the
    annotated PDF writes, which Zotero 7+ imports as its text annotation —
    with either switch, as there. It is a note where no copy carries it, and
    as well when notes sit under it, which a text annotation can't hold."""
    page = _paper(guest, "zxt")
    _put_children(guest, page["id"], [
        _positioned("zxth", "the quoted passage"),
        {"id": "zxtb", "content": "typed on the **paper**", "children": [],
         "properties": {"text_box": {"x": 40, "y": 60, "w": 180}, "pdf_page": 1}},
        {"id": "zxtc", "content": "boxed with a thought", "properties": {
            "text_box": {"x": 40, "y": 300, "w": 180}, "pdf_page": 1}, "children": [
            {"id": "zxtcn", "content": "the thought", "properties": {}, "children": []}]},
    ])

    def export(**params):
        z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf", **params}))
        it = _rdf_items(z)[0]
        return it, ([str(a["/Subtype"]) for a in _pdf_annots(z, it)] if it["pdf_paths"] else None), z

    it, kinds, z = export()
    assert sorted(kinds) == ["/FreeText", "/FreeText", "/Highlight"]
    boxes = [a for a in _pdf_annots(z, it) if a["/Subtype"] == "/FreeText"]
    assert sorted(a["/Contents"] for a in boxes) == ["boxed with a thought", "typed on the paper"]
    assert all("/Zotero:Rect" in a and a["/NM"].startswith("Zotero-") for a in boxes)
    [note] = [n["text"] for n in it["notes"]]
    assert "Text box on p.1" in note and "boxed with a thought" in note and "- the thought" in note

    it, kinds, _z = export(notes=0)
    assert it["notes"] == [] and kinds.count("/FreeText") == 2
    assert export(notes=0, highlights=0)[1] == []                 # both off: the stored file
    # no copy to carry them: each box is a note headed by its page
    notes = [n["text"] for n in export(pdf=0)[0]["notes"]]
    assert any("Text box on p.1" in n and "typed on the **paper**" in n for n in notes) and len(notes) == 2


def test_zotero_export_carries_handwriting_in_the_pdf_copy(guest):
    """Handwriting rides in the PDF copy as /Ink with a Zotero id, which
    Zotero imports as its ink annotation, the caption its comment rather
    than a note as well. Without the highlights switch the copy carries no
    ink, and the caption stays a note."""
    page = _paper(guest, "zxk")
    _put_children(guest, page["id"], [_ink_block(guest, "zxkink", "my sketch")])
    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf"}))
    it = _rdf_items(z)[0]
    [ink] = _pdf_annots(z, it)
    assert ink["/Subtype"] == "/Ink" and ink["/NM"].startswith("Zotero-") and ink["/Contents"] == "my sketch"
    assert float(ink["/BS"]["/W"]) > 0 and len(ink["/InkList"][0]) % 2 == 0
    assert it["notes"] == []

    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf", "highlights": 0}))
    it = _rdf_items(z)[0]
    assert _pdf_annots(z, it) == [] and [n["text"] for n in it["notes"]] == ["my sketch"]


def test_zotero_export_switches(guest):
    page = _paper(guest, "zxb")
    # notes=0: no Memo; highlights=0: bare PDF copy; pdf=0: no files at all
    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export",
                          params={"mode": "zotero-rdf", "notes": 0, "highlights": 0}))
    it = _rdf_items(z)[0]
    assert it["notes"] == []
    base = next(n for n in z.namelist() if n.endswith(".rdf")).rsplit("/", 1)[0]
    assert b"/Highlight" not in z.read(f"{base}/{it['pdf_paths'][0]}")

    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export",
                          params={"mode": "zotero-rdf", "pdf": 0}))
    assert _rdf_items(z)[0]["pdf_paths"] == []
    assert not any("/files/" in n for n in z.namelist())


def test_folder_zotero_export_scopes_collections(guest):
    optics, cooking = make_folder(guest, "zxresearch/optics"), make_folder(guest, "zxcooking")
    make_page(guest, "Zx in folder A", properties={
        "folders": [optics, cooking],
        "meta": {"title": "Zx in folder A", "arxiv_id": "2101.00001"},
    })
    make_page(guest, "Zx in subfolder", properties={"folders": [make_folder(guest, "zxresearch/optics/lasers/blue")]})
    make_page(guest, "Zx elsewhere", properties={"folders": [cooking]})

    r = guest.get(f"/api/folders/{optics}/export", params={"mode": "zotero-rdf"})
    items = _rdf_items(_zip_of(r))
    by_title = {i["title"]: i for i in items}
    assert set(by_title) == {"Zx in folder A", "Zx in subfolder"}  # not "elsewhere"
    # the exported folder is the library's top: folders outside it
    # ("zxcooking") don't leak, the folder itself is no collection
    assert by_title["Zx in folder A"]["folders"] == []
    # the nested collection chain reassembles the path below the folder
    assert by_title["Zx in subfolder"]["folders"] == [["lasers", "blue"]]
    assert by_title["Zx in folder A"]["meta"]["arxiv_id"] == "2101.00001"


def test_zotero_export_reimports_via_the_real_endpoint(guest):
    roundtrip = make_folder(guest, "roundtrip")
    make_page(guest, "Paper one", properties={
        "folders": [roundtrip], "cite_key": "lovelace:notes",
        "meta": {"title": "Paper one", "authors": ["Ada Lovelace"], "year": "1843",
                 "venue": "Notes", "doi": "10.1000/rt1"},
    })
    make_page(guest, "Paper two", properties={"folders": [make_folder(guest, "roundtrip/deep")]})
    r = guest.get(f"/api/folders/{roundtrip}/export", params={"mode": "zotero-rdf"})
    assert r.status_code == 200, r.text

    other = _other_library()
    imp = other.post(
        "/api/import/zotero",
        files={"file": ("lib.zip", io.BytesIO(r.content), "application/zip")},
        data={"folder": make_folder(other, "zimported")},
    )
    assert imp.status_code == 200, imp.text
    d = imp.json()
    assert d["items"] == 2 and d["skipped"] == []
    assert d["pages_created"] == 2  # another library: nothing to merge into
    by_title = {p["title"]: p for p in d["pages"]}
    one = other.get(f"/api/blocks/{by_title['Paper one']['id']}").json()["properties"]
    assert one["meta"]["authors"] == ["Ada Lovelace"]
    # the pinned key went out as Better BibTeX's "Citation Key:" line and came back
    assert one["cite_key"] == "lovelace:notes" and "@article{lovelace:notes," in one["bibtex"]
    assert one["meta"]["doi"] == "10.1000/rt1" and one["meta"]["venue"] == "Notes"
    paths = folder_names(other)
    assert [paths[f] for f in one["folders"]] == [["zimported"]]
    two = other.get(f"/api/blocks/{by_title['Paper two']['id']}").json()["properties"]
    assert [paths[f] for f in two["folders"]] == [["zimported", "deep"]]
    assert by_title["Paper two"]["folders"] == [["zimported", "deep"]]


def test_zotero_keys_name_one_page_in_every_export(guest):
    """An item's key is the same whatever else the export holds, and no two
    items share one: two pages exported one at a time were both
    "#gamma_item_1" once, and the import merged the second into the first."""
    folder = make_folder(guest, "zxkeys")
    a, b = (make_page(guest, f"Zxkeys {t}", properties={"folders": [folder]}) for t in "AB")
    # a page and its copy, which kept the Zotero key it was imported with
    c, d = (make_page(guest, f"Zxkeys {t}", properties={"folders": [folder], "zotero_key": "#item_7"})
            for t in "CD")
    alone = {}
    for p in (a, b):
        [it] = _rdf_items(_zip_of(guest.get(f"/api/pages/{p['id']}/export", params={"mode": "zotero-rdf"})))
        alone[p["id"]] = it["key"]
    assert alone == {a["id"]: f"#gamma_page_{a['id']}", b["id"]: f"#gamma_page_{b['id']}"}
    other = _other_library()
    for p in (a, b):
        r = guest.get(f"/api/pages/{p['id']}/export", params={"mode": "zotero-rdf"})
        imp = other.post("/api/import/zotero", files={"file": ("lib.zip", io.BytesIO(r.content), "application/zip")})
        assert imp.json()["pages_created"] == 1, imp.text

    keys = {i["title"]: i["key"] for i in _rdf_items(_zip_of(
        guest.get(f"/api/folders/{folder}/export", params={"mode": "zotero-rdf"})))}
    assert keys["Zxkeys A"] == alone[a["id"]]
    # one Zotero key on two pages: the key once, then the other page's id
    assert {keys["Zxkeys C"], keys["Zxkeys D"]} in (
        {"#item_7", f"#gamma_page_{c['id']}"}, {"#item_7", f"#gamma_page_{d['id']}"})


def test_reimporting_an_export_into_its_own_library_adds_nothing(guest):
    """Each item names the page it was exported from and each note the block
    it was written from, so the export brought back into the same library
    merges into those pages and adds nothing — whether its PDF copy carries
    the highlights or is the stored file itself."""
    page = _paper(guest, "zxr")
    kids = [c["id"] for c in guest.get(f"/api/blocks/{page['id']}/subtree").json()["block"]["children"]]
    for params in ({}, {"highlights": 0}):
        r = guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf", **params})
        upload = {"file": ("lib.zip", io.BytesIO(r.content), "application/zip")}
        [preview] = guest.post("/api/import/zotero/preview", files=upload).json()["pages"]
        assert (preview["action"], preview["existing_id"]) == ("merge", page["id"])
        upload["file"][1].seek(0)
        d = guest.post("/api/import/zotero", files=upload).json()
        assert (d["pages_created"], d["pages_merged"], d["notes_imported"], d["annotations_imported"]) == (0, 1, 0, 0)
        assert d["pages"][0]["id"] == page["id"]
    after = guest.get(f"/api/blocks/{page['id']}/subtree").json()["block"]["children"]
    assert [c["id"] for c in after] == kids


def test_each_item_type_goes_out_as_zotero_writes_it_and_comes_back():
    """Zotero's own "Zotero RDF" translator decides the shapes: the item
    carries z:itemType, a journal article's and a conference paper's venue
    sit on a bib:Journal, a chapter's book title and ISBN on a bib:Book, and
    a publisher is a dc:publisher foaf:Organization."""
    from gamma.zotero_export import build_rdf

    base = {"authors": ["Kaiming He"], "year": "2016", "doi": "10.1/x"}
    metas = {
        "journalArticle": {**base, "kind": "journalArticle", "venue": "Nature", "volume": "521"},
        "conferencePaper": {**base, "kind": "conferencePaper", "venue": "CVPR", "publisher": "IEEE"},
        "bookSection": {**base, "kind": "bookSection", "venue": "Handbook of X", "isbn": "9780000000002",
                        "publisher": "Springer"},
        "book": {**base, "kind": "book", "publisher": "Wiley", "isbn": "9780471878766"},
        "thesis": {**base, "kind": "thesis", "publisher": "MIT"},
        "report": {**base, "kind": "report", "publisher": "NIST"},
        "preprint": {**base, "kind": "preprint", "arxiv_id": "1706.03762", "venue": "arXiv:1706.03762"},
        "presentation": {**base, "kind": "presentation"},
        "document": {**base, "kind": "document"},
    }
    items = [{"key": f"#item_{kind}", "title": f"A {kind}", "meta": meta} for kind, meta in metas.items()]
    items.append({"key": "#item_bare", "title": "No record", "meta": {}})
    text = build_rdf(items)
    assert '<bib:BookSection rdf:about="#item_bookSection">' in text
    assert '<bib:Book rdf:about="#book_3">' in text and "<dc:identifier>ISBN 9780000000002</dc:identifier>" in text
    assert "<foaf:Organization>" in text and "<foaf:name>IEEE</foaf:name>" in text

    back = {it["key"]: it["meta"] for it in parse_zotero_rdf(text)}
    for kind, meta in metas.items():
        got = back[f"#item_{kind}"]
        assert got["kind"] == kind
        for field in ("venue", "volume", "publisher", "isbn", "doi"):
            if kind == "preprint" and field == "venue":
                continue  # an arXiv id travels as its URL; the "arXiv:<id>" venue is the default
            assert got.get(field, "") == meta.get(field, ""), (kind, field)
    assert back["#item_bare"]["kind"] == "document"
