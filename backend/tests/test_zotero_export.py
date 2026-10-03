"""Zotero RDF export: a page or folder → .rdf + files/ tree in a zip, the
import's exact inverse — verified by round-tripping through
``zotero_import.parse_zotero_rdf`` and the real ``/api/import/zotero``."""

import io
import zipfile

from conftest import folder_names, make_folder, make_label, make_page

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


def test_zotero_export_writes_a_text_box_as_a_note(guest):
    """A text box is the user's writing: a Zotero note headed by its page
    (the highlight memo's convention), never an annotation burned into the
    PDF copy, and gone with the Notes switch like any note."""
    page = _paper(guest, "zxt")
    _put_children(guest, page["id"], [
        _positioned("zxth", "the quoted passage"),
        {"id": "zxtb", "content": "typed on the **paper**", "children": [],
         "properties": {"text_box": {"x": 40, "y": 60, "w": 180}, "pdf_page": 1}},
    ])
    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf"}))
    it = _rdf_items(z)[0]
    notes = [n["text"] for n in it["notes"]]
    assert len(notes) == 1 and "Text box on p.1" in notes[0] and "typed on the **paper**" in notes[0]
    base = next(n for n in z.namelist() if n.endswith(".rdf")).rsplit("/", 1)[0]
    pdf = z.read(f"{base}/{it['pdf_paths'][0]}")
    assert pdf.count(b"/Highlight") == 1 and b"/Square" not in pdf and b"/FreeText" not in pdf

    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf", "notes": 0}))
    assert _rdf_items(z)[0]["notes"] == []


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

    imp = guest.post(
        "/api/import/zotero",
        files={"file": ("lib.zip", io.BytesIO(r.content), "application/zip")},
        data={"folder": make_folder(guest, "zimported")},
    )
    assert imp.status_code == 200, imp.text
    d = imp.json()
    assert d["items"] == 2 and d["skipped"] == []
    assert d["pages_created"] == 2  # fresh pages: nothing to merge into
    by_title = {p["title"]: p for p in d["pages"]}
    one = guest.get(f"/api/blocks/{by_title['Paper one']['id']}").json()["properties"]
    assert one["meta"]["authors"] == ["Ada Lovelace"]
    # the pinned key went out as Better BibTeX's "Citation Key:" line and came back
    assert one["cite_key"] == "lovelace:notes" and "@article{lovelace:notes," in one["bibtex"]
    assert one["meta"]["doi"] == "10.1000/rt1" and one["meta"]["venue"] == "Notes"
    paths = folder_names(guest)
    assert [paths[f] for f in one["folders"]] == [["zimported"]]
    two = guest.get(f"/api/blocks/{by_title['Paper two']['id']}").json()["properties"]
    assert [paths[f] for f in two["folders"]] == [["zimported", "deep"]]
    assert by_title["Paper two"]["folders"] == [["zimported", "deep"]]
