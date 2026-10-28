"""Zotero RDF export: a page or folder → .rdf + files/ tree in a zip, the
import's exact inverse — verified by round-tripping through
``zotero_import.parse_zotero_rdf`` and the real ``/api/import/zotero``.
What the PDF copies carry follows Zotero 9's pdf-worker (readRawAnnotation):
an /Ink or /FreeText needs a Zotero id, a /FreeText its /Zotero:* keys."""

import copy
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile

from conftest import folder_names, fresh_client, make_folder, make_label, make_page
from PyPDF2 import PdfReader

from gamma import ink as inkmod
from gamma import zotero_notes
from gamma.zotero_export import build_rdf, split_name
from gamma.zotero_import import PASS_THROUGH_BYTES, parse_zotero_rdf


def _blank_pdf_bytes(width=612):
    from PyPDF2 import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=width, height=792)
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


def _note_lines(note):
    """A parsed note's text and the content of every block under it, in
    reading order."""
    lines = [note["text"]]

    def walk(nodes):
        for node in nodes:
            lines.append(node["content"])
            walk(node.get("children") or [])
    walk(note.get("children") or [])
    return lines


def _pdf_annots(z, item):
    """The annotations on the first page of an item's PDF copy."""
    base = next(n for n in z.namelist() if n.endswith(".rdf")).rsplit("/", 1)[0]
    page = PdfReader(io.BytesIO(z.read(f"{base}/{item['pdf_paths'][0]}"))).pages[0]
    return [a.get_object() for a in page.get("/Annots") or []]


def _ink_block(guest, bid, caption):
    """A handwriting group on PDF page 1, its strokes uploaded."""
    ink = {"format": "gamma-ink", "version": 2,
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
    # the free note (with its child) came back as one note tree, markdown intact
    [note] = it["notes"]
    assert note["text"] == "Read this **twice**."
    assert note["children"] == [{"content": "sub point", "children": []}]

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
    notes = ["\n".join(_note_lines(n)) for n in it["notes"]]
    assert len(notes) == 2
    hl_note = next(n for n in notes if "p.1" in n)
    assert "“quoted”" in hl_note and "see" in hl_note


def test_exported_note_images_come_back_once(guest):
    """A note's image rides the export as an item attachment. Brought back
    into its own library it is in the notes already and adds nothing; into
    another library it comes back inside its note, alt text and all, and the
    attachment holding the same bytes makes no chip."""
    page = _paper(guest, "zxm")
    img_url = guest.post("/api/upload-image", files={"file": ("fig.png", _PNG, "image/png")}).json()["url"]
    _put_children(guest, page["id"], [
        {"id": "zxmn", "content": f"figure: ![fig]({img_url})", "properties": {}, "children": []}])
    r = guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf"})
    kids = [c["id"] for c in guest.get(f"/api/blocks/{page['id']}/children").json()["children"]]
    d = guest.post("/api/import/zotero", files={"file": ("lib.zip", io.BytesIO(r.content), "application/zip")}).json()
    assert (d["pages_merged"], d["attachments_imported"]) == (1, 0)
    assert [c["id"] for c in guest.get(f"/api/blocks/{page['id']}/children").json()["children"]] == kids

    other = _other_library()
    d = other.post("/api/import/zotero", files={"file": ("lib.zip", io.BytesIO(r.content), "application/zip")}).json()
    assert (d["pages_created"], d["attachments_imported"]) == (1, 0)  # the picture comes back in its note
    children = other.get(f"/api/blocks/{d['pages'][0]['id']}/children").json()["children"]
    assert not any(c["properties"].get("zotero_attachments") for c in children)
    [note] = [c for c in children if c["properties"].get("zotero_note")]
    assert note["content"] == f"figure: ![fig]({img_url})"
    assert other.get(img_url).content == _PNG


# An item with a PDF, two more files and a web link, as Zotero exports it.
_ATTACHED = """<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:z="http://www.zotero.org/namespaces/export#" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/" xmlns:bib="http://purl.org/net/biblio#"
 xmlns:link="http://purl.org/rss/1.0/modules/link/">
<bib:Article rdf:about="#item_1"><z:itemType>journalArticle</z:itemType><dc:title>Zxat paper</dc:title>
 <link:link rdf:resource="#item_2"/><link:link rdf:resource="#item_3"/><link:link rdf:resource="#item_4"/>
 <link:link rdf:resource="#item_5"/></bib:Article>
<z:Attachment rdf:about="#item_2"><z:itemType>attachment</z:itemType><z:path rdf:resource="files/2/paper.pdf"/>
 <dc:title>Full Text PDF</dc:title><link:type>application/pdf</link:type></z:Attachment>
<z:Attachment rdf:about="#item_3"><z:itemType>attachment</z:itemType><z:path rdf:resource="files/3/data.csv"/>
 <dc:title>data.csv</dc:title><link:type>text/csv</link:type></z:Attachment>
<z:Attachment rdf:about="#item_4"><z:itemType>attachment</z:itemType><z:path rdf:resource="files/4/supplement.pdf"/>
 <dc:title>Supplement</dc:title><link:type>application/pdf</link:type></z:Attachment>
<z:Attachment rdf:about="#item_5"><z:itemType>attachment</z:itemType>
 <dc:identifier><dcterms:URI><rdf:value>https://example.com/zxat</rdf:value></dcterms:URI></dc:identifier>
 <dc:title>Project page</dc:title></z:Attachment>
</rdf:RDF>"""


def _attachment_chips(client, page_id):
    """The labels of the chips under a page's "Attachments" block."""
    children = client.get(f"/api/blocks/{page_id}/subtree").json()["block"]["children"]
    [block] = [c for c in children if c["properties"].get("zotero_attachments")]
    return sorted(re.match(r"\[([^\]]*)\]", c["content"]).group(1) for c in block["children"])


def test_other_attachments_go_back_as_attachments():
    """An item's other files and web links, which the import made the chips
    of its page's "Attachments" block, go back to Zotero as attachments of
    the item, each file under its own name, and not as a note of links into
    Gamma's storage. In their own library they add nothing; in another they
    come back as the chips they were."""
    lib = _other_library()
    pdf, csv, supplement = _blank_pdf_bytes(), b"a,b\n1,2\n", _blank_pdf_bytes(width=300)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("lib/lib.rdf", _ATTACHED)
        for path, data in {"2/paper.pdf": pdf, "3/data.csv": csv, "4/supplement.pdf": supplement}.items():
            zf.writestr(f"lib/files/{path}", data)
    buf.seek(0)
    d = lib.post("/api/import/zotero", files={"file": ("lib.zip", buf, "application/zip")}).json()
    page_id = d["pages"][0]["id"]
    chips = _attachment_chips(lib, page_id)
    assert chips == ["Project page", "data.csv", "supplement.pdf"]

    r = lib.get(f"/api/pages/{page_id}/export", params={"mode": "zotero-rdf"})
    z = _zip_of(r)
    base = next(n for n in z.namelist() if n.endswith(".rdf")).rsplit("/", 1)[0]
    assert "/api/uploads/" not in z.read(f"{base}/{base}.rdf").decode("utf-8")
    [item] = _rdf_items(z)
    assert item["notes"] == []
    assert {a["title"]: z.read(f"{base}/{a['path']}") for a in item["attachments"] if a["path"]} == {
        "Full Text PDF": pdf, "data.csv": csv, "supplement.pdf": supplement}
    assert [(a["title"], a["url"]) for a in item["attachments"] if not a["path"]] == [
        ("Project page", "https://example.com/zxat")]

    d = lib.post("/api/import/zotero", files={"file": ("lib.zip", io.BytesIO(r.content), "application/zip")}).json()
    assert (d["pages_created"], d["pages_merged"], d["attachments_imported"], d["notes_imported"]) == (0, 1, 0, 0)
    assert _attachment_chips(lib, page_id) == chips
    other = _other_library()
    d = other.post("/api/import/zotero", files={"file": ("lib.zip", io.BytesIO(r.content), "application/zip")}).json()
    assert _attachment_chips(other, d["pages"][0]["id"]) == chips


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
    [note] = it["notes"]
    assert _note_lines(note) == ["**Text box on p.1**", "boxed with a thought", "the thought"]

    it, kinds, _z = export(notes=0)
    assert it["notes"] == [] and kinds.count("/FreeText") == 2
    assert export(notes=0, highlights=0)[1] == []                 # both off: the stored file
    # no copy to carry them: each box is a note headed by its page
    notes = [_note_lines(n) for n in export(pdf=0)[0]["notes"]]
    assert ["**Text box on p.1**", "typed on the **paper**"] in notes and len(notes) == 2


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
    base = {"authors": ["Kaiming He"], "year": "2016", "doi": "10.1/x"}
    metas = {
        "journalArticle": {**base, "kind": "journalArticle", "venue": "Nature", "volume": "521"},
        "conferencePaper": {**base, "kind": "conferencePaper", "venue": "CVPR", "publisher": "IEEE"},
        "bookSection": {**base, "kind": "bookSection", "venue": "Handbook of X", "isbn": "9780000000002",
                        "publisher": "Springer"},
        "book": {**base, "kind": "book", "publisher": "Wiley", "isbn": "9780471878766", "volume": "3"},
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
            # arXiv is the repository a preprint with no publisher names, as Zotero's own do
            want = "arXiv" if (kind, field) == ("preprint", "publisher") else meta.get(field, "")
            assert got.get(field, "") == want, (kind, field)
    assert back["#item_bare"]["kind"] == "document"


# --- names, preprints, URLs ---------------------------------------------------

_NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "z": "http://www.zotero.org/namespaces/export#",
    "dc": "http://purl.org/dc/elements/1.1/", "dcterms": "http://purl.org/dc/terms/",
    "bib": "http://purl.org/net/biblio#", "foaf": "http://xmlns.com/foaf/0.1/",
    "prism": "http://prismstandard.org/namespaces/1.2/basic/", "link": "http://purl.org/rss/1.0/modules/link/",
}
_ABOUT = f"{{{_NS['rdf']}}}about"


def _tag(prefixed):
    prefix, name = prefixed.split(":")
    return f"{{{_NS[prefix]}}}{name}"


def _record(text, key):
    """The top-level RDF record ``key`` names."""
    return next(el for el in ET.fromstring(text) if el.get(_ABOUT) == key)


def _url_of(el):
    return [v.text for v in el.findall("dc:identifier/dcterms:URI/rdf:value", _NS)]


def _x(fragment):
    """One element as the import serializes it (``ET.tostring``)."""
    decl = " ".join(f'xmlns:{p}="{u}"' for p, u in _NS.items())
    head, rest = fragment.split(">", 1)
    return ET.tostring(ET.fromstring(f"{head} {decl}>{rest}"), encoding="unicode")


def _canon(xml):
    """An element's serialization with the layout whitespace dropped."""
    el = copy.deepcopy(xml) if isinstance(xml, ET.Element) else ET.fromstring(xml)
    for e in el.iter():
        if e.text is not None and not e.text.strip():
            e.text = None
        e.tail = None
    return ET.tostring(el, encoding="unicode")


def test_names_split_into_zoteros_two_fields():
    """Gamma keeps an author as one string; Zotero has a surname and a given
    name, or a surname alone for a single-field name (an organisation)."""
    cases = {
        "Ashish Vaswani": ("Vaswani", "Ashish"),
        "  Ada   Lovelace ": ("Lovelace", "Ada"),
        "Vaswani, Ashish": ("Vaswani", "Ashish"),
        "Jan van der Berg": ("van der Berg", "Jan"),
        "Ludwig van Beethoven": ("van Beethoven", "Ludwig"),
        "Charles de Gaulle": ("de Gaulle", "Charles"),
        "Ibn bin Laden": ("bin Laden", "Ibn"),
        "de la Cruz": ("de la Cruz", ""),
        "J. R. R. Tolkien": ("Tolkien", "J. R. R."),
        "Martin Luther King Jr.": ("King Jr.", "Martin Luther"),
        "Martin Luther King, Jr.": ("King, Jr.", "Martin Luther"),
        "Henry Ford II": ("Ford II", "Henry"),
        "Plato": ("Plato", ""),
        "ATLAS Collaboration": ("ATLAS Collaboration", ""),
        "World Health Organization": ("World Health Organization", ""),
        "Max Planck Institute for Physics": ("Max Planck Institute for Physics", ""),
        "University of California, Berkeley": ("University of California, Berkeley", ""),
        "Acme Inc.": ("Acme Inc.", ""),
    }
    assert {name: split_name(name) for name in cases} == cases


def test_authors_go_out_split_and_come_back_as_they_were():
    names = ["Jan van der Berg", "ATLAS Collaboration", "Martin Luther King, Jr.", "Plato", "Ada Lovelace"]
    text = build_rdf([{"key": "#item_n", "title": "Names", "meta": {"title": "Names", "authors": names}}])
    people = [(p.findtext("foaf:surname", None, _NS), p.findtext("foaf:givenName", None, _NS))
              for p in _record(text, "#item_n").iterfind("bib:authors/rdf:Seq/rdf:li/foaf:Person", _NS)]
    assert people == [("van der Berg", "Jan"), ("ATLAS Collaboration", None), ("King, Jr.", "Martin Luther"),
                      ("Plato", None), ("Lovelace", "Ada")]
    assert parse_zotero_rdf(text)[0]["meta"]["authors"] == names


def test_a_preprint_names_its_archive_and_repository():
    """A preprint goes out as Zotero's own arXiv records are: archive id,
    arXiv as its repository, the abstract page as its URL. A published paper
    with an arXiv id keeps the abstract URL only."""
    items = [
        {"key": "#item_pre", "title": "Pre", "meta": {"title": "Pre", "kind": "preprint", "arxiv_id": "2101.00001"}},
        {"key": "#item_bio", "title": "Bio", "meta": {"title": "Bio", "kind": "preprint", "publisher": "bioRxiv"}},
        {"key": "#item_art", "title": "Art", "meta": {"title": "Art", "kind": "journalArticle", "venue": "Nature",
                                                      "arxiv_id": "2101.00002"}},
    ]
    text = build_rdf(items)
    pre, bio, art = (_record(text, i["key"]) for i in items)
    assert pre.findtext("z:archiveID", None, _NS) == "arXiv:2101.00001"
    assert pre.findtext("dc:publisher/foaf:Organization/foaf:name", None, _NS) == "arXiv"
    assert _url_of(pre) == ["https://arxiv.org/abs/2101.00001"]
    assert bio.find("z:archiveID", _NS) is None and _url_of(bio) == []
    assert bio.findtext("dc:publisher/foaf:Organization/foaf:name", None, _NS) == "bioRxiv"
    assert art.find("z:archiveID", _NS) is None and art.find("dc:publisher", _NS) is None
    assert _url_of(art) == ["https://arxiv.org/abs/2101.00002"]
    back = {it["key"]: it["meta"] for it in parse_zotero_rdf(text)}
    assert back["#item_pre"]["arxiv_id"] == "2101.00001" and back["#item_pre"]["publisher"] == "arXiv"
    assert back["#item_art"]["arxiv_id"] == "2101.00002"


def test_an_item_carries_one_url():
    """The URL is the original record's, else the arXiv abstract's, else the
    web page the page was clipped from."""
    meta = {"title": "Web", "kind": "document"}
    clipped = {"key": "#item_web", "title": "Web", "meta": meta, "url": "https://example.com/post"}
    rdf = {"v": 1, "work": {"title": "Web", "doi": ""},
           "item": [_x("<dc:identifier><dcterms:URI><rdf:value>https://example.com/original</rdf:value>"
                       "</dcterms:URI></dc:identifier>")]}
    kept = {**clipped, "key": "#item_kept", "rdf": rdf}
    arxiv = {**clipped, "key": "#item_arxiv", "meta": {**meta, "arxiv_id": "2101.00003"}}
    text = build_rdf([clipped, kept, arxiv])
    assert _url_of(_record(text, "#item_web")) == ["https://example.com/post"]
    assert _url_of(_record(text, "#item_kept")) == ["https://example.com/original"]
    assert _url_of(_record(text, "#item_arxiv")) == ["https://arxiv.org/abs/2101.00003"]


# --- the pass-through ---------------------------------------------------------

def _magazine_rdf():
    """What the import keeps of a Zotero magazine article that ``meta`` does
    not model."""
    return {
        "v": 1,
        "work": {"title": "Zxp gadgets", "doi": "10.1/zxp"},
        "type": "magazineArticle",
        "class": f"{{{_NS['bib']}}}Article",
        "container_class": f"{{{_NS['bib']}}}Periodical",
        "date": "2017-06-12",
        "authors": {"names": ["Jan van der Berg"], "xml": _x(
            "<bib:authors><rdf:Seq><rdf:li><foaf:Person><foaf:surname>Berg</foaf:surname>"
            "<foaf:givenName>Jan van der</foaf:givenName></foaf:Person></rdf:li></rdf:Seq></bib:authors>")},
        "extra": "PMID: 123\nCitation Key: old:key\ntex.howpublished: online",
        "pdf_title": "Full Text PDF",
        "item": [
            _x("<dcterms:abstract>We study gadgets &amp; things.</dcterms:abstract>"),
            _x("<z:language>en</z:language>"),
            _x("<z:shortTitle>Gadgets</z:shortTitle>"),
            _x("<bib:editors><rdf:Seq><rdf:li><foaf:Person><foaf:surname>Eco</foaf:surname>"
               "<foaf:givenName>Umberto</foaf:givenName></foaf:Person></rdf:li></rdf:Seq></bib:editors>"),
            _x("<dc:subject><dcterms:LCC><rdf:value>QA76.9</rdf:value></dcterms:LCC></dc:subject>"),
        ],
        "container": [
            _x("<dc:identifier>ISSN 1059-1028</dc:identifier>"),
            _x("<dcterms:alternative>Wired Mag.</dcterms:alternative>"),
            _x("<prism:number>6</prism:number>"),
        ],
    }


def _magazine(**meta):
    return {"key": "#item_zxp", "title": "Zxp gadgets", "cite_key": "berg:zxp", "pdf_path": "files/1/zxp.pdf",
            "meta": {"title": "Zxp gadgets", "doi": "10.1/zxp", "kind": "document", "venue": "Wired",
                     "volume": "25", "year": "2017", "authors": ["Jan van der Berg"], **meta},
            "rdf": _magazine_rdf()}


def test_what_zotero_held_goes_back_verbatim():
    """A page that came from Zotero goes back with what its record held
    beyond ``meta``: the original type and RDF classes, the full date, the
    authors as Zotero split them, the Extra lines, the attachment's title,
    and every element the import did not map, on the item or its container."""
    rdf = _magazine_rdf()
    text = build_rdf([_magazine()])
    item = _record(text, "#item_zxp")
    assert item.tag == _tag("bib:Article") and item.findtext("z:itemType", None, _NS) == "magazineArticle"
    assert item.findtext("dc:date", None, _NS) == "2017-06-12"
    assert [_canon(a) for a in item.findall("bib:authors", _NS)] == [_canon(rdf["authors"]["xml"])]
    # the pinned key replaces the old one, where it was
    assert item.findtext("dc:description", None, _NS) == (
        "PMID: 123\nCitation Key: berg:zxp\ntex.howpublished: online")
    passed = {_canon(e) for e in item}
    assert {_canon(x) for x in rdf["item"]} <= passed
    [part] = item.findall("dcterms:isPartOf", _NS)
    container = _record(text, part.get(f"{{{_NS['rdf']}}}resource"))
    assert container.tag == _tag("bib:Periodical") and container.findtext("dc:title", None, _NS) == "Wired"
    assert container.findtext("prism:volume", None, _NS) == "25"
    assert {_canon(x) for x in rdf["container"]} <= {_canon(e) for e in container}
    [att] = [el for el in ET.fromstring(text) if el.tag == _tag("z:Attachment")]
    assert att.findtext("dc:title", None, _NS) == "Full Text PDF"

    # ...and the import keeps them again, so another round trip changes nothing
    [back] = parse_zotero_rdf(text)
    assert back["meta"]["kind"] == "document" and back["meta"]["authors"] == ["Jan van der Berg"]
    assert back["cite_key"] == "berg:zxp"
    again = back["rdf"]
    assert {k: again.get(k) for k in ("type", "class", "container_class", "date", "extra")} == {
        "type": "magazineArticle", "class": rdf["class"], "container_class": rdf["container_class"],
        "date": "2017-06-12", "extra": "PMID: 123\nCitation Key: berg:zxp\ntex.howpublished: online"}
    assert again["authors"]["names"] == ["Jan van der Berg"]
    assert _canon(again["authors"]["xml"]) == _canon(rdf["authors"]["xml"])
    assert sorted(map(_canon, again["item"])) == sorted(map(_canon, rdf["item"]))
    assert sorted(map(_canon, again["container"])) == sorted(map(_canon, rdf["container"]))


def test_a_pass_through_follows_the_record():
    """Each part goes out only while the record still agrees with it; none
    of it once the record names another work."""
    rdf = _magazine_rdf()
    # another work: nothing of the old one leaves
    text = build_rdf([_magazine(title="Something else", doi="10.1/other")])
    item = _record(text, "#item_zxp")
    assert item.tag == _tag("rdf:Description") and item.findtext("z:itemType", None, _NS) == "document"
    assert item.findtext("dc:date", None, _NS) == "2017"
    assert item.findtext("dc:description", None, _NS) == "Citation Key: berg:zxp"
    assert item.find("dcterms:abstract", _NS) is None and item.find("bib:editors", _NS) is None
    assert item.find("dcterms:isPartOf", _NS) is None
    assert item.findtext("bib:authors/rdf:Seq/rdf:li/foaf:Person/foaf:surname", None, _NS) == "van der Berg"
    assert "Full Text PDF" not in text and "ISSN" not in text

    # the same work, its record edited: what no longer agrees is the export's own
    text = build_rdf([{**_magazine(year="2018", authors=["Jan van der Berg", "Ada Lovelace"], kind="book"),
                       "cite_key": ""}])
    item = _record(text, "#item_zxp")
    assert item.tag == _tag("bib:Book") and item.findtext("z:itemType", None, _NS) == "book"
    assert item.findtext("dc:date", None, _NS) == "2018"
    assert [p.findtext("foaf:surname", None, _NS) for p in item.iterfind("bib:authors//foaf:Person", _NS)] == [
        "van der Berg", "Lovelace"]
    assert item.findtext("dc:description", None, _NS) == "PMID: 123\ntex.howpublished: online"
    assert {_canon(x) for x in rdf["item"]} <= {_canon(e) for e in item}
    assert "Wired Mag." not in text  # a book has no venue record to hold it

    # a key pinned on a page whose Extra had none goes first
    plain = {**_magazine(), "rdf": {**rdf, "extra": "PMID: 123"}}
    assert _record(build_rdf([plain]), "#item_zxp").findtext("dc:description", None, _NS) == (
        "Citation Key: berg:zxp\nPMID: 123")
    # a date naming no year goes back while the record names none either
    undated = {**_magazine(year=""), "rdf": {**rdf, "date": "Spring term"}}
    assert _record(build_rdf([undated]), "#item_zxp").findtext("dc:date", None, _NS) == "Spring term"
    dated = {**_magazine(), "rdf": {**rdf, "date": "Spring term"}}
    assert _record(build_rdf([dated]), "#item_zxp").findtext("dc:date", None, _NS) == "2017"


def test_a_stored_pass_through_goes_out_only_as_fields():
    """``zotero_rdf`` is a page property any editor of the workspace can
    write, so the export writes back only what the import keeps there,
    fields: no XML whose entities would expand, no element the import maps
    itself, no attachment (Zotero would take its path as a file of the
    importing machine), note or link to one, and no more of it than the
    import keeps."""
    bomb = ('<!DOCTYPE z:language [<!ENTITY a "zxbomb"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            f'<z:language xmlns:z="{_NS["z"]}">&b;&b;&b;&b;</z:language>')
    rdf = {"v": 1, "work": {"title": "Zxv", "doi": ""},
           "authors": {"names": ["Ann Lee"], "xml": _x("<dcterms:abstract>zxauthors</dcterms:abstract>")},
           "item": [bomb,
                    _x('<dc:relation><z:Attachment rdf:about="#zxinline"><z:itemType>attachment</z:itemType>'
                       '<z:path rdf:resource="/home/someone/private.pdf"/></z:Attachment></dc:relation>'),
                    _x('<link:link><z:Attachment rdf:about="#zxlinked"><z:path rdf:resource="/etc/hosts"/>'
                       "</z:Attachment></link:link>"),
                    _x('<dcterms:isReferencedBy><bib:Memo rdf:about="#zxmemo"></bib:Memo></dcterms:isReferencedBy>'),
                    _x("<dc:title>Another work</dc:title>"),
                    _x("<dcterms:abstract>Kept.</dcterms:abstract>")],
           "container": [_x("<dc:title>Another journal</dc:title>"), _x("<prism:number>6</prism:number>")]}
    meta = {"title": "Zxv", "authors": ["Ann Lee"], "kind": "journalArticle", "venue": "Zxv Journal"}
    text = build_rdf([{"key": "#item_zxv", "title": "Zxv", "meta": meta, "rdf": rdf}])
    for planted in ("zxbomb", "#zxinline", "#zxlinked", "#zxmemo", "zxauthors", "z:path", "Another"):
        assert planted not in text
    item = _record(text, "#item_zxv")
    assert item.findtext("dcterms:abstract", None, _NS) == "Kept."
    assert [p.findtext("foaf:surname", None, _NS) for p in item.iterfind("bib:authors//foaf:Person", _NS)] == [
        "Lee"]
    container = _record(text, item.find("dcterms:isPartOf", _NS).get(f"{{{_NS['rdf']}}}resource"))
    assert (container.findtext("dc:title", None, _NS), container.findtext("prism:number", None, _NS)) == (
        "Zxv Journal", "6")

    field = _x(f"<dcterms:abstract>{'x' * 1000}</dcterms:abstract>")
    text = build_rdf([{"key": "#item_zxv", "title": "Zxv", "meta": meta, "rdf": {**rdf, "item": [field] * 200}}])
    kept = _record(text, "#item_zxv").findall("dcterms:abstract", _NS)
    assert kept and len(kept) * len(field) <= PASS_THROUGH_BYTES


def test_a_webpage_goes_out_a_webpage():
    """Zotero's webpage is a document here; the pass-through takes it back
    to Zotero as the webpage it was, its site as a z:Website record."""
    meta = {"title": "A post", "kind": "document", "venue": "Example Blog"}
    rdf = {"v": 1, "work": {"title": "A post", "doi": ""}, "type": "webpage",
           "class": f"{{{_NS['bib']}}}Document", "container_class": f"{{{_NS['z']}}}Website"}
    text = build_rdf([{"key": "#item_w", "title": "A post", "meta": meta, "rdf": rdf}])
    item = _record(text, "#item_w")
    assert item.tag == _tag("bib:Document") and item.findtext("z:itemType", None, _NS) == "webpage"
    site = _record(text, item.find("dcterms:isPartOf", _NS).get(f"{{{_NS['rdf']}}}resource"))
    assert site.tag == _tag("z:Website") and site.findtext("dc:title", None, _NS) == "Example Blog"
    # a page whose kind was set to another type goes out as that type
    text = build_rdf([{"key": "#item_w", "title": "A post", "meta": {**meta, "kind": "report"}, "rdf": rdf}])
    assert _record(text, "#item_w").findtext("z:itemType", None, _NS) == "report"


def test_a_page_sends_its_zotero_fields_and_web_address(guest):
    """The export sends what the page keeps: its ``zotero_rdf`` and the web
    page it was clipped from."""
    page = make_page(guest, "Zxp gadgets", properties={
        "meta": _magazine()["meta"], "zotero_rdf": _magazine_rdf(), "zotero_key": "#item_zxp",
        "cite_key": "berg:zxp"})
    clipped = make_page(guest, "Zxw clipped", properties={"web_url": "https://example.com/zxw"})
    for p, key in ((page, "#item_zxp"), (clipped, f"#gamma_page_{clipped['id']}")):
        z = _zip_of(guest.get(f"/api/pages/{p['id']}/export", params={"mode": "zotero-rdf"}))
        text = z.read(next(n for n in z.namelist() if n.endswith(".rdf"))).decode("utf-8")
        item = _record(text, key)
        if p is page:
            assert item.findtext("z:itemType", None, _NS) == "magazineArticle"
            assert item.findtext("dcterms:abstract", None, _NS) == "We study gadgets & things."
        else:
            assert _url_of(item) == ["https://example.com/zxw"]


# --- notes ----------------------------------------------------------------------

def test_notes_go_out_as_their_block_trees(guest):
    """A note is its block subtree in Zotero's note HTML: headings, lists,
    links, code. The import's note tree reads it back as it was; a [[ref]]
    goes out as the text it names."""
    page = make_page(guest, "Zxq paper")
    tree = {"id": "zxqn", "content": "## Method", "properties": {}, "children": [
        {"id": "zxqa", "content": "**Key** idea, see [the paper](https://example.com/p) and [[zxqr]]",
         "properties": {}, "children": []},
        {"id": "zxqb", "content": "1. first", "properties": {}, "children": [
            {"id": "zxqc", "content": "nested `code` and $x^2$", "properties": {}, "children": []}]},
        {"id": "zxqd", "content": "2. second", "properties": {}, "children": []},
    ]}
    _put_children(guest, page["id"], [tree, {"id": "zxqr", "content": "the referenced idea", "properties": {},
                                             "children": []}])
    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf"}))
    text = z.read(next(n for n in z.namelist() if n.endswith(".rdf"))).decode("utf-8")
    html = _record(text, "#gamma_note_zxqn").findtext("rdf:value", None, _NS)
    assert html.startswith("<h2>Method</h2>") and '<a href="https://example.com/p">the paper</a>' in html

    def plain(node):
        return {"content": node["content"], "children": [plain(c) for c in node["children"]]}
    want = plain(tree)
    want["children"][0]["content"] = "**Key** idea, see [the paper](https://example.com/p) and the referenced idea"
    assert zotero_notes.note_tree(html) == want
    [note] = [n for n in _rdf_items(z)[0]["notes"] if n["key"] == "#gamma_note_zxqn"]
    assert {"content": note["text"], "children": note["children"]} == want


def test_a_note_page_goes_out_as_a_standalone_note(guest):
    """A page a Zotero standalone note made (``zotero_note`` is its
    ``zotero_key``) goes back as that note: no item around it, its title the
    note's heading, its blocks the note, filed and tagged like the page."""
    folder = make_folder(guest, "zxsn/notes")
    page = make_page(guest, "Zxsn reading list", properties={
        "zotero_key": "#item_9", "zotero_note": "#item_9", "folders": [folder],
        "labels": [make_label(guest, "todo")]})
    children = [
        {"content": "First point", "children": [{"content": "sub", "children": []}]},
        {"content": "## Later", "children": []},
    ]
    _put_children(guest, page["id"], [
        {"id": "zxsn1", "content": "First point", "properties": {}, "children": [
            {"id": "zxsn2", "content": "sub", "properties": {}, "children": []}]},
        {"id": "zxsn3", "content": "## Later", "properties": {}, "children": []},
    ])
    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf"}))
    text = z.read(next(n for n in z.namelist() if n.endswith(".rdf"))).decode("utf-8")
    memo = _record(text, "#item_9")
    assert memo.tag == _tag("bib:Memo") and memo.findtext("z:itemType", None, _NS) == "note"
    assert [s.text for s in memo.findall("dc:subject", _NS)] == ["todo"]
    html = memo.findtext("rdf:value", None, _NS)
    assert zotero_notes.note_tree(html) == {"content": "# Zxsn reading list", "children": children}
    assert [el.tag for el in ET.fromstring(text)] == [_tag("bib:Memo"), _tag("z:Collection"), _tag("z:Collection")]
    [item] = _rdf_items(z)
    assert item["standalone_note"] and item["title"] == "Zxsn reading list"
    assert item["note"] == children and item["tags"] == ["todo"] and item["folders"] == [["zxsn", "notes"]]

    # with the notes off, nothing of it is left to export
    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf", "notes": 0}))
    assert "#item_9" not in z.read(next(n for n in z.namelist() if n.endswith(".rdf"))).decode("utf-8")


# --- the PDF copies and the zip ---------------------------------------------------

def test_a_pdf_with_nothing_to_draw_goes_in_as_stored(guest, monkeypatch):
    """A page with nothing to draw on its PDF sends the stored file itself:
    neither annotated nor spooled, the same bytes."""
    from gamma.routers import export as export_router

    def refuse(*args, **kwargs):
        raise AssertionError("the stored PDF was rewritten")
    monkeypatch.setattr(export_router._Builder, "spool", refuse)
    monkeypatch.setattr(export_router, "annotate_pdf", refuse)
    pdf = _blank_pdf_bytes()
    up = guest.post("/api/uploads", files={"file": ("p.pdf", pdf, "application/pdf")}).json()
    page = make_page(guest, "Zxs plain", properties={"doc_id": up["doc_id"], "source_url": up["source_url"]})
    _put_children(guest, page["id"], [{"id": "zxsp", "content": "a plain note", "properties": {}, "children": []}])
    for params in ({}, {"highlights": 0, "notes": 0}):
        z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf", **params}))
        base = next(n for n in z.namelist() if n.endswith(".rdf")).rsplit("/", 1)[0]
        assert z.read(f"{base}/{_rdf_items(z)[0]['pdf_paths'][0]}") == pdf


def test_a_pdf_that_cannot_be_drawn_on_goes_in_as_stored(guest, monkeypatch):
    """A PDF whose copy cannot be drawn (one PyPDF2 cannot open, as an
    AES-encrypted publisher PDF) goes in as it is stored, and the export
    goes on with its notes."""
    from gamma.routers import export as export_router

    def fail(*args, **kwargs):
        raise ValueError("PyCryptodome is required for AES algorithm")
    monkeypatch.setattr(export_router, "annotate_pdf", fail)
    pdf = _blank_pdf_bytes()
    up = guest.post("/api/uploads", files={"file": ("p.pdf", pdf, "application/pdf")}).json()
    page = make_page(guest, "Zxl locked", properties={"doc_id": up["doc_id"], "source_url": up["source_url"]})
    _put_children(guest, page["id"], [
        _positioned("zxlh", "a passage", note="mine"),
        {"id": "zxln", "content": "a plain note", "properties": {}, "children": []}])
    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf"}))
    [item] = _rdf_items(z)
    base = next(n for n in z.namelist() if n.endswith(".rdf")).rsplit("/", 1)[0]
    assert z.read(f"{base}/{item['pdf_paths'][0]}") == pdf
    assert [n["text"] for n in item["notes"]] == ["a plain note"]


def test_the_zip_stores_pdfs_and_compressed_media(guest):
    page = _paper(guest, "zxz")
    img_url = guest.post("/api/upload-image", files={"file": ("fig.png", _PNG, "image/png")}).json()["url"]
    _put_children(guest, page["id"], [
        {"id": "zxzn", "content": f"figure: ![fig]({img_url})", "properties": {}, "children": []}])
    z = _zip_of(guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf"}))
    packing = {i.filename.rsplit(".", 1)[-1]: i.compress_type for i in z.infolist()}
    assert packing == {"pdf": zipfile.ZIP_STORED, "png": zipfile.ZIP_STORED,
                       "rdf": zipfile.ZIP_DEFLATED, "txt": zipfile.ZIP_DEFLATED}


def test_zotero_copies_carry_true_colours(guest):
    """Zotero pales a highlight's colour by its /CA, so the copies for
    Zotero carry none on highlights; Gamma's own alpha rides in
    /GammaAlpha, which brings the highlight back as it was drawn."""
    page = _paper(guest, "zxc")
    r = guest.get(f"/api/pages/{page['id']}/export", params={"mode": "zotero-rdf"})
    z = _zip_of(r)
    [hl] = [a for a in _pdf_annots(z, _rdf_items(z)[0]) if a["/Subtype"] == "/Highlight"]
    assert "/CA" not in hl and abs(float(hl["/GammaAlpha"]) - 0.65) < 1e-3

    other = _other_library()
    d = other.post("/api/import/zotero", files={"file": ("lib.zip", io.BytesIO(r.content), "application/zip")}).json()
    children = other.get(f"/api/blocks/{d['pages'][0]['id']}/children").json()["children"]
    assert [c["properties"]["color"] for c in children if c["properties"].get("pdf_position")] == ["#aaebaaa6"]


# A library as Zotero's "Zotero RDF" translator writes it (generateItem):
# a journal article on an ISSN record with its issue, abbreviation and
# series, editors, a full date, Extra lines, a call number, an automatic
# tag, the access date and a URL; a webpage on its site, by an
# organisation; an arXiv preprint with its archive id and DataCite DOI.
_ZOTERO_LIBRARY = """<rdf:RDF
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:z="http://www.zotero.org/namespaces/export#"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:bib="http://purl.org/net/biblio#"
 xmlns:foaf="http://xmlns.com/foaf/0.1/"
 xmlns:link="http://purl.org/rss/1.0/modules/link/"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/">
    <bib:Article rdf:about="#item_12">
        <z:itemType>journalArticle</z:itemType>
        <dcterms:isPartOf rdf:resource="urn:issn:0028-0836"/>
        <bib:authors>
            <rdf:Seq>
                <rdf:li><foaf:Person><foaf:surname>Berg</foaf:surname><foaf:givenName>Jan van der</foaf:givenName></foaf:Person></rdf:li>
                <rdf:li><foaf:Person><foaf:surname>ATLAS Collaboration</foaf:surname></foaf:Person></rdf:li>
            </rdf:Seq>
        </bib:authors>
        <bib:editors>
            <rdf:Seq>
                <rdf:li><foaf:Person><foaf:surname>Eco</foaf:surname><foaf:givenName>Umberto</foaf:givenName></foaf:Person></rdf:li>
            </rdf:Seq>
        </bib:editors>
        <dc:subject>optics</dc:subject>
        <dc:subject><z:AutomaticTag><rdf:value>fibres</rdf:value></z:AutomaticTag></dc:subject>
        <dc:subject><dcterms:LCC><rdf:value>QC350</rdf:value></dcterms:LCC></dc:subject>
        <dc:title>Light in fibres</dc:title>
        <dcterms:abstract>We guide light &amp; measure it.</dcterms:abstract>
        <dc:date>2019-03-14</dc:date>
        <z:language>en</z:language>
        <z:libraryCatalog>Crossref</z:libraryCatalog>
        <dc:identifier><dcterms:URI><rdf:value>https://www.nature.com/articles/x</rdf:value></dcterms:URI></dc:identifier>
        <dcterms:dateSubmitted>2024-01-02 10:00:00</dcterms:dateSubmitted>
        <dc:description>PMID: 31234
Citation Key: berg2019</dc:description>
        <bib:pages>12-19</bib:pages>
    </bib:Article>
    <bib:Journal rdf:about="urn:issn:0028-0836">
        <dcterms:isPartOf><bib:Series><dc:title>Nature Series</dc:title></bib:Series></dcterms:isPartOf>
        <prism:volume>567</prism:volume>
        <dc:title>Nature</dc:title>
        <dcterms:alternative>Nat.</dcterms:alternative>
        <prism:number>7748</prism:number>
        <dc:identifier>DOI 10.1038/s41586-019-0001-1</dc:identifier>
        <dc:identifier>ISSN 0028-0836</dc:identifier>
    </bib:Journal>
    <bib:Document rdf:about="https://example.com/post">
        <z:itemType>webpage</z:itemType>
        <dcterms:isPartOf><z:Website><dc:title>Example Blog</dc:title></z:Website></dcterms:isPartOf>
        <bib:authors>
            <rdf:Seq>
                <rdf:li><foaf:Person><foaf:surname>Example Team</foaf:surname></foaf:Person></rdf:li>
            </rdf:Seq>
        </bib:authors>
        <dc:title>A post</dc:title>
        <dc:date>2021-05</dc:date>
        <dc:identifier><dcterms:URI><rdf:value>https://example.com/post</rdf:value></dcterms:URI></dc:identifier>
        <dc:type>Blog post</dc:type>
    </bib:Document>
    <rdf:Description rdf:about="http://arxiv.org/abs/2101.00001">
        <z:itemType>preprint</z:itemType>
        <dc:publisher><foaf:Organization><foaf:name>arXiv</foaf:name></foaf:Organization></dc:publisher>
        <bib:authors>
            <rdf:Seq>
                <rdf:li><foaf:Person><foaf:surname>Lovelace</foaf:surname><foaf:givenName>Ada</foaf:givenName></foaf:Person></rdf:li>
            </rdf:Seq>
        </bib:authors>
        <dc:title>A preprint</dc:title>
        <dcterms:abstract>Abstract.</dcterms:abstract>
        <dc:date>2021-01-01</dc:date>
        <z:archiveID>arXiv:2101.00001</z:archiveID>
        <dc:identifier><dcterms:URI><rdf:value>http://arxiv.org/abs/2101.00001</rdf:value></dcterms:URI></dc:identifier>
        <dc:identifier>DOI 10.48550/arXiv.2101.00001</dc:identifier>
        <dc:description>arXiv:2101.00001 [physics]</dc:description>
    </rdf:Description>
</rdf:RDF>"""


def _canon_rdf(rdf):
    """A pass-through with its serialized elements compared as elements."""
    if rdf is None:
        return None
    out = {**rdf}
    for key in ("item", "container"):
        if key in out:
            out[key] = sorted(map(_canon, out[key]))
    if "authors" in out:
        out["authors"] = {**out["authors"], "xml": _canon(out["authors"]["xml"])}
    return out


def test_a_zotero_library_goes_back_as_it_came():
    """Zotero → Gamma → Zotero → Gamma: what the first import read, record
    and pass-through, is what the second reads from Gamma's export."""
    first = parse_zotero_rdf(_ZOTERO_LIBRARY)
    assert all(item["rdf"] for item in first)
    text = build_rdf([{key: item[key] for key in ("key", "title", "meta", "cite_key", "tags", "folders", "rdf")}
                      for item in first])
    again = {item["key"]: item for item in parse_zotero_rdf(text)}
    for item in first:
        back = again[item["key"]]
        assert (back["title"], back["meta"], back["cite_key"], back["tags"]) == (
            item["title"], item["meta"], item["cite_key"], item["tags"]), item["key"]
        assert _canon_rdf(back["rdf"]) == _canon_rdf(item["rdf"]), item["key"]
    # what Zotero reads from Gamma's copy: its own shapes again
    article = _record(text, "#item_12")
    assert article.findtext("dcterms:abstract", None, _NS) == "We guide light & measure it."
    assert [p.findtext("foaf:surname", None, _NS) for p in article.iterfind("bib:authors//foaf:Person", _NS)] == [
        "Berg", "ATLAS Collaboration"]
    post = _record(text, "https://example.com/post")
    assert post.tag == _tag("bib:Document") and post.findtext("z:itemType", None, _NS) == "webpage"
    assert _url_of(post) == ["https://example.com/post"]
    preprint = _record(text, "http://arxiv.org/abs/2101.00001")
    assert [e.text for e in preprint.findall("z:archiveID", _NS)] == ["arXiv:2101.00001"]
    assert _url_of(preprint) == ["http://arxiv.org/abs/2101.00001"]
