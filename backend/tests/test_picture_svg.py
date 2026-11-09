"""SVG pictures (docs/dev/pictures.md "SVG pictures"): an upload served so
that its scripts never run, its size from its root element, drawn as vector
content in the annotated PDF and read back from it as the SVG it was,
bounded before MuPDF sees it, a PNG of it where an SVG is not taken (the
Notability export, the AI), and the file as it came in the imports and the
text exports. The size rule itself is pinned by tests/shared/picture.json."""

import base64
import io
import zipfile

import pymupdf
import pytest
from PIL import Image

import goodnotes_fixtures as gx
from ai_fixtures import folder, notes, org  # noqa: F401  (fixtures)
from conftest import files_in, guest_name, workspace_of
from test_notability_export import flat
from test_picture_pdf import (QUADRANTS, WHITE, _entry, _imported, _near, _pic, _placed, _raster, _screen,
                              _stamps, _stored, _upload_image)
from test_text_box import CROP, _kids, _pdf, _put_children, _upload_pdf

from gamma import note_app_import, notability_export, pdf_image, picture, storage
from gamma.ai_tools import run_agent_tool
from gamma.pdf_export import annotate_pdf

SVG_HEAD = b'<svg xmlns="http://www.w3.org/2000/svg"'
XLINK = b' xmlns:xlink="http://www.w3.org/1999/xlink"'
# The four quadrants of test_picture_pdf's _quads, as vectors: 300 x 150 as
# the browser shows it (a viewBox alone, fitted in 300 x 150).
QUADS = (SVG_HEAD + b' viewBox="0 0 80 40"><rect width="40" height="20" fill="#f00"/>'
         b'<rect x="40" width="40" height="20" fill="#00c800"/><rect y="20" width="40" height="20" fill="#00f"/>'
         b'<rect x="40" y="20" width="40" height="20" fill="#ff0"/></svg>')
SCRIPTED = (SVG_HEAD + b' width="10" height="10" onload="alert(1)"><script>alert(document.cookie)</script>'
            b'<a href="javascript:alert(2)"><rect width="10" height="10"/></a></svg>')


def _uses(levels: int) -> bytes:
    """An SVG of a few hundred bytes that draws 2^levels squares: each
    group shows the one before it twice."""
    groups = b"".join(b'<g id="g%d"><use href="#g%d"/><use href="#g%d"/></g>' % (i, i - 1, i - 1)
                      for i in range(1, levels + 1))
    return (SVG_HEAD + b' width="10" height="10"><defs><g id="g0"><rect width="1" height="1"/></g>' + groups
            + b'</defs><use href="#g%d"/></svg>' % levels)


def _doubled(link: bytes, ident: bytes = b"g@", head: bytes = b"", ns: bytes = XLINK, levels: int = 30) -> bytes:
    """_uses with the references written another way: group ``ident``
    shows the one before it twice through ``link``, each "@" standing for
    a group's number."""
    def at(template: bytes, n: int) -> bytes:
        return template.replace(b"@", b"%d" % n)
    groups = b"".join(b'<g id="' + at(ident, i) + b'">' + at(link, i - 1) * 2 + b"</g>" for i in range(1, levels + 1))
    return (head + SVG_HEAD + ns + b' width="10" height="10"><defs><g id="e"/><rect id="' + at(ident, 0)
            + b'" width="1" height="1"/>' + groups + b'</defs><use xlink:href="#' + at(ident, levels) + b'"/></svg>')


def _png_b64(width: int, height: int) -> str:
    """A blank PNG of that size, in base64: its header is all a bound reads."""
    out = io.BytesIO()
    Image.new("1", (width, height)).save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode()


# --- served -------------------------------------------------------------------------------------


def test_an_svg_upload_is_served_so_that_its_scripts_never_run(guest):
    """Opened at its URL, an SVG downloads, and a browser that drew it
    anyway would run no script and fetch nothing, in an origin of its
    own; nosniff keeps its type. The bytes are stored as they came: the
    headers defuse every script an SVG can hold at once. An <img> draws an
    SVG with no script, so the page and the notes draw it as they draw any
    picture. A file chip of an SVG is served the same way."""
    for route in ("/api/upload-image", "/api/upload-file"):
        r = guest.post(route, files={"file": ("x.svg", SCRIPTED, "image/svg+xml")})
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        assert url.endswith(".svg") and picture.normalize_picture({"url": url}) is not None
        for method in ("get", "head"):
            served = getattr(guest, method)(url)
            assert served.status_code == 200, (route, method)
            assert served.headers["content-type"] == "image/svg+xml"
            assert served.headers["content-disposition"].startswith("attachment")
            assert served.headers["x-content-type-options"] == "nosniff"
            policy = [p.strip() for p in served.headers["content-security-policy"].split(";")]
            assert "sandbox" in policy and "default-src 'none'" in policy
            assert not any(p.startswith(("script-src", "img-src", "connect-src")) for p in policy)
        assert guest.get(url).content == SCRIPTED


# --- the size -----------------------------------------------------------------------------------


def test_the_copy_mupdf_reads_is_sized_as_the_browser_shows_it():
    """MuPDF reads no viewBox for a missing side and puts an SVG of no size
    on a Letter page; the copy it reads names the browser's size. Without a
    viewBox, one over the size the attributes give is added with
    preserveAspectRatio none, so a drawing the bound scaled down is drawn
    whole."""
    def page(data):
        doc = pymupdf.open(stream=pdf_image.svg_pdf(data), filetype="pdf")
        try:
            return tuple(doc[0].rect)[2:]
        finally:
            doc.close()

    assert page(SVG_HEAD + b' width="80" viewBox="0 0 40 20"/>') == (80, 40)
    assert page(SVG_HEAD + b' viewBox="0 0 20 40"/>') == (75, 150)
    assert page(SVG_HEAD + b'><rect width="300" height="150"/></svg>') == (300, 150)
    huge = SVG_HEAD + b' width="100000" height="50000" viewBox="junk">'
    sized = picture.svg_sized(huge)
    assert sized == (SVG_HEAD + b' width="14400" height="7200" viewBox="0 0 100000 50000" '
                     b'preserveAspectRatio="none">')
    assert picture.svg_sized(b"<svg/>") is None


# --- the annotated PDF --------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", [0, 90])
@pytest.mark.parametrize("angle, flip_h", [(0, False), (30, True)])
def test_the_stamp_draws_an_svg_as_vector_content_as_the_screen_does(tmp_path, rotation, angle, flip_h):
    """An SVG picture's stamp draws MuPDF's page of it as a form, through
    the crop, the flips and the turn as any picture: every pixel of the
    frame is the quadrant the screen draws there. The form holds the file
    (/GammaSVG), and no image: it is vectors."""
    uploads, url = _stored(tmp_path, QUADS, ext="svg")
    pic = _pic(url, x=150, y=120, w=200, h=120, angle=angle, flip_h=flip_h, crop={"x": 0.25, "y": 0, "w": 0.75, "h": 1})
    out, written = annotate_pdf(_pdf(rotation, CROP), [], stored=files_in(uploads), pictures=[_entry(pic)])
    assert written == 1
    [stamp] = _stamps(out)
    [drawn] = stamp["/AP"]["/N"].get_object()["/Resources"]["/XObject"].values()
    drawn = drawn.get_object()
    assert drawn["/Subtype"] == "/Form" and drawn["/GammaSVG"].get_object().get_data() == QUADS
    assert "/XObject" not in drawn["/Resources"]
    pixel = _raster(out)
    x0, y0, x1, y1 = picture.turned_box(pic)
    inside = 0
    for x in range(int(x0), int(x1), 5):
        for y in range(int(y0), int(y1), 5):
            want = _screen(pic, x + 0.5, y + 0.5)
            if want is not None:
                assert _near(pixel(x, y), WHITE if want == "out" else QUADRANTS[want]), (x, y, want)
                inside += want != "out"
    assert inside > 200


def test_a_stretched_svg_is_stretched_as_the_screen_draws_it(tmp_path):
    """Twice as wide as its own aspect, an SVG with a viewBox fills its
    frame, as the screen draws it (picture.js svgFill) and as any picture
    stretches: no paper at either end."""
    uploads, url = _stored(tmp_path, QUADS, ext="svg")
    pic = _pic(url, x=100, y=100, w=400, h=100)
    pixel = _raster(annotate_pdf(_pdf(), [], stored=files_in(uploads), pictures=[_entry(pic)])[0])
    assert _near(pixel(110, 120), QUADRANTS["TL"]) and _near(pixel(490, 180), QUADRANTS["BR"])


def test_export_then_import_brings_the_svg_back(tmp_path):
    """The import reads an SVG stamp's file from its form (/GammaSVG): the
    picture comes back with the SVG it was drawn from, in any workspace,
    its crop, turn and flips with it."""
    uploads, url = _stored(tmp_path, QUADS, ext="svg")
    pic = _pic(url, angle=30, flip_v=True, crop={"x": 0.1, "y": 0.2, "w": 0.6, "h": 0.5})
    out, _ = annotate_pdf(_pdf(90, CROP), [], stored=files_in(uploads), pictures=[_entry(pic, "a *plot*")])
    [found] = _imported(out)
    assert (found["kind"], found["content"], found["original"]) == ("picture", "a *plot*", url)
    assert found["file"] == (QUADS, ".svg") and found["picture"]["url"].endswith(".svg")
    got = found["picture"]
    assert {k: v for k, v in got.items() if k not in ("x", "y", "url")} == \
        {k: v for k, v in pic.items() if k not in ("x", "y", "url")}


def test_an_svg_drawing_text_and_a_picture_still_comes_back(tmp_path):
    """The form of an SVG drawing an embedded picture and text names an
    image and a font of its own; the import counts the form as the one
    image, so the stamp is still a picture, the SVG as it was."""
    red = io.BytesIO()
    Image.new("RGB", (4, 2), (255, 0, 0)).save(red, "PNG")
    data = (SVG_HEAD + b' width="40" height="20"><image width="40" height="20" href="data:image/png;base64,'
            + base64.b64encode(red.getvalue()) + b'"/><text x="2" y="15" font-size="10">Hi</text></svg>')
    uploads, url = _stored(tmp_path, data, ext="svg")
    out, _ = annotate_pdf(_pdf(), [], stored=files_in(uploads), pictures=[_entry(_pic(url))])
    assert _near(_raster(out)(250, 70), (255, 0, 0))
    [found] = _imported(out)
    assert found["file"] == (data, ".svg")


def test_an_svg_picture_round_trips_through_the_endpoints(guest):
    """Exported and imported back into its workspace, an SVG picture keeps
    its own file, nothing is left over to warn of, and the original stamp
    is stripped."""
    url = _upload_image(guest, QUADS, "plot.svg", "image/svg+xml")
    source, _doc = _upload_pdf(guest, _pdf(), "SVG source")
    _put_children(guest, source["id"], [_placed("svgRound", url, content="vector", angle=12.5)])
    exported = guest.get(f"/api/pages/{source['id']}/export-pdf")
    assert exported.headers["x-annotations-written"] == "1"
    page, doc_id = _upload_pdf(guest, exported.content, "SVG import")
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id, "strip": True})
    assert r.json() == {"ok": True, "found": 1, "imported": 1, "stripped": 1}
    [block] = _kids(guest, page["id"])
    assert block["content"] == "vector" and block["properties"]["picture"]["url"] == url


def test_an_svg_past_its_bounds_is_left_out_before_mupdf_sees_it(tmp_path, monkeypatch):
    """A <use> of a group of two doubles what is drawn at each step, so a
    few hundred bytes would hold MuPDF for ever; such a file, an embedded
    picture past MAX_DECODED_PIXELS, an SVG embedded in an SVG and a file
    past MAX_SVG_BYTES are refused before MuPDF reads them, and the export
    leaves the picture out as it leaves out a missing file."""
    nested = base64.b64encode(QUADS).decode()
    refused = {
        "uses": (_uses(40), "MAX_SVG_ELEMENTS"),
        "pixels": (SVG_HEAD + f'><image href="data:image/png;base64,{_png_b64(8000, 6000)}"/></svg>'.encode(),
                   "MAX_DECODED_PIXELS"),
        "nested": (SVG_HEAD + f'><image href="data:image/svg+xml;base64,{nested}"/></svg>'.encode(), "no PNG"),
        "junk": (SVG_HEAD + b"><rect></svg>", "not well-formed"),
        "big": (SVG_HEAD + b">" + b" " * 100_000 + b"</svg>", "MAX_SVG_BYTES"),
    }
    monkeypatch.setattr(pdf_image, "MAX_SVG_BYTES", 100_000)
    for what, (data, reason) in refused.items():
        with pytest.raises(ValueError, match=reason):
            pdf_image.svg_pdf(data)
        uploads, url = _stored(tmp_path, data, ext="svg", stem=what)
        assert annotate_pdf(_pdf(), [], stored=files_in(uploads), pictures=[_entry(_pic(url))])[1] == 0, what
    pdf_image.svg_pdf(_uses(12))                                      # 4,096 squares draw


def test_every_reference_mupdf_follows_is_weighed():
    """MuPDF reads the literal xlink:href before href, whatever its prefix
    is bound to. It keeps a line break or an entity in an id as written,
    and takes any one of the elements a repeated id names. Each file here
    is a few kB that MuPDF reads as 2^30 squares, or as a picture past
    MAX_DECODED_PIXELS, and each is refused. So is a loop of <use>s, which
    MuPDF walks along every path. A <use> naming its group both ways
    weighs it once, so 4,096 squares still draw."""
    loop = b"".join(b'<g id="n%d">' % i + b"".join(b'<use href="#n%d"/>' % j for j in range(12) if j != i) + b"</g>"
                    for i in range(12))
    repeated = b"".join(b'<g id="g%d"><use href="#g%d"/><use href="#g%d"/></g><g id="g%d"/>' % (i, i - 1, i - 1, i)
                        for i in range(30, 0, -1))
    refused = {
        "xlink first": (_doubled(b'<use href="#e" xlink:href="#g@"/>'), "MAX_SVG_ELEMENTS"),
        "other prefix": (_doubled(b'<use xlink:href="#g@"/>', ns=b' xmlns:xlink="http://example.com/other"'),
                         "MAX_SVG_ELEMENTS"),
        "line break": (_doubled(b'<use href="#g\n@"/>', ident=b"g&#10;@"), "MAX_SVG_ELEMENTS"),
        "entity": (_doubled(b'<use href="#g@&e;"/>', ident=b"g@&amp;e;", head=b'<!DOCTYPE svg [<!ENTITY e "x">]>'),
                   "entity"),
        "repeated id": (SVG_HEAD + b' width="10" height="10"><defs><rect id="g0" width="1" height="1"/>' + repeated
                        + b'</defs><use href="#g30"/></svg>', "MAX_SVG_ELEMENTS"),
        "loop": (SVG_HEAD + b' width="10" height="10"><defs>' + loop + b'</defs><use href="#n0"/></svg>', "holds it"),
        "xlink picture": (SVG_HEAD + XLINK + f'><image href="data:image/png;base64,{_png_b64(4, 4)}" xlink:href='
                          f'"data:image/png;base64,{_png_b64(8000, 6000)}"/></svg>'.encode(), "MAX_DECODED_PIXELS"),
    }
    for what, (data, reason) in refused.items():
        assert len(data) < 10_000, what
        with pytest.raises(ValueError, match=reason):
            pdf_image._check_svg(data)
    pdf_image.svg_pdf(_doubled(b'<use href="#g@" xlink:href="#g@"/>', levels=12))


def test_an_svg_reads_nothing_outside_itself(tmp_path):
    """An <image> of a file beside it, or of the server's own files, draws
    nothing and puts nothing of them in the PDF."""
    secret = b"SECRET-OF-THE-SERVER"
    (tmp_path / "secret.png").write_bytes(secret)
    data = (SVG_HEAD + b' xmlns:xlink="http://www.w3.org/1999/xlink" width="20" height="20">'
            b'<image width="20" height="20" xlink:href="secret.png"/>'
            b'<image width="20" height="20" href="file:///etc/hostname"/></svg>')
    document = pdf_image.svg_pdf(data)
    assert secret not in document
    doc = pymupdf.open(stream=document, filetype="pdf")
    try:
        assert doc[0].get_images() == []
    finally:
        doc.close()


# --- where an SVG is not taken ------------------------------------------------------------------


def test_an_svg_picture_goes_into_a_note_as_a_png(guest):
    """Notability takes no SVG: the picture goes in as a PNG of it,
    SVG_PNG_SIDE pixels on its longer side, its crop in that PNG's pixels;
    one that does not draw is left out and said."""
    url = _upload_image(guest, QUADS, "plot.svg", "image/svg+xml")
    bad = _upload_image(guest, _uses(40), "bomb.svg", "image/svg+xml")
    page, _doc = _upload_pdf(guest, _pdf(), "SVG to Notability")
    _put_children(guest, page["id"], [_placed("svgNote", url, crop={"x": 0.5, "w": 0.5}), _placed("svgBomb", bad)])
    assert guest.get(f"/api/pages/{page['id']}/export", params={"mode": "notability"}).status_code == 200
    root = guest.get(f"/api/blocks/{page['id']}/subtree").json()["block"]
    draft, warnings = notability_export.page_draft([{**root, "parent_id": "root"}, *flat(root)], page["id"],
                                                   storage.files(workspace_of(guest_name())))
    assert warnings == ["1 SVG picture could not be drawn for the note and is left out"]
    [written] = draft.media
    shown = Image.open(io.BytesIO(draft.files[written.image])).convert("RGB")
    side = notability_export.SVG_PNG_SIDE
    assert written.image.endswith(".png") and written.pixels == shown.size == (side, side // 2)
    assert written.crop == (side / 2, 0, side / 2, side // 2)
    assert _near(shown.getpixel((10, 10)), QUADRANTS["TL"]) and _near(shown.getpixel((side - 10, 10)), QUADRANTS["TR"])


def test_a_box_holding_an_svg_goes_into_a_note_as_a_png(guest):
    """A text box holding one SVG, as imports made a picture before it was
    a block of its own, goes in as a picture block's SVG does: a PNG of it,
    its alt text the caption. One that does not draw is said, and its box
    goes in as text."""
    url = _upload_image(guest, QUADS, "plot.svg", "image/svg+xml")
    bad = _upload_image(guest, _uses(40), "bomb.svg", "image/svg+xml")
    page, _doc = _upload_pdf(guest, _pdf(), "Boxed SVG to Notability")
    box = {"x": 60, "y": 300, "w": 128, "h": 88, "auto": False}
    _put_children(guest, page["id"], [
        {"id": f"svgBox{i}", "content": f"![a plot]({src})", "children": [],
         "properties": {"pdf_page": 1, "text_box": {**box, "y": 300 + 100 * i}}} for i, src in enumerate((url, bad))])
    root = guest.get(f"/api/blocks/{page['id']}/subtree").json()["block"]
    draft, warnings = notability_export.page_draft([{**root, "parent_id": "root"}, *flat(root)], page["id"],
                                                   storage.files(workspace_of(guest_name())))
    assert warnings == ["1 SVG picture could not be drawn for the note and is left out"]
    written, text = draft.media
    side = notability_export.SVG_PNG_SIDE
    assert written.image.endswith(".png") and written.pixels == (side, side // 2) and written.caption == "a plot"
    assert Image.open(io.BytesIO(draft.files[written.image])).size == (side, side // 2)
    assert draft.private["boxes"][text.uuid.hex()]["content"] == f"![a plot]({bad})"


def test_the_agent_looks_at_an_svg_picture_as_a_png(notes):  # noqa: F811
    """A vision model takes no SVG: view_image hands it a PNG of one. One
    that cannot be drawn is said to be so, not to be missing."""
    c, ids = notes
    url = _upload_image(c, QUADS, "plot.svg", "image/svg+xml")
    bad = _upload_image(c, _uses(40), "bomb.svg", "image/svg+xml")
    r = c.post("/api/blocks", json={"parent_id": "root", "content": "svg playground",
                                    "properties": {"folders": [ids["sandbox"]]}})
    page = r.json()["id"]
    r = c.post("/api/blocks", json={"parent_id": page, "content": "a plot",
                                    "properties": {"picture": {"url": url}, "pdf_page": 1}})
    text, chip = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "view_image", {"block_id": r.json()["id"]})
    assert f'Picture 1 of 1, alt text "a plot", 1568×784 px, {url}: attached.' in text
    [(media, _data)] = chip["images"]
    assert media == "image/png"
    r = c.post("/api/blocks", json={"parent_id": page, "content": "a bomb",
                                    "properties": {"picture": {"url": bad}, "pdf_page": 1}})
    text, _ = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "view_image", {"block_id": r.json()["id"]})
    assert text.startswith(f"error: Picture 1 ({bad}): its file does not read as a picture (an SVG that cannot")
    c.delete(f"/api/blocks/{page}")


# --- as it came ---------------------------------------------------------------------------------


def test_the_imports_keep_an_svg_as_it_came(guest):
    """The Notability and GoodNotes imports store an SVG picture as it came
    (note_app_import.picture_file), its type told by its root element. A
    GoodNotes notebook's SVG lands as a picture of that file, nothing said;
    test_notability_import has the Notability one."""
    assert note_app_import.picture_file(QUADS) == QUADS
    assert picture.picture_type(QUADS) == "image/svg+xml" and picture.image_size(QUADS) == (300, 150)
    n = gx.Notebook("SVG picture", seed=31)
    n.page(n.paper(), [gx.image("V", n.attach(QUADS), (110, 220, 160, 80))])
    r = guest.post("/api/import/goodnotes", files={"file": ("SVG.goodnotes", n.bytes(), "application/zip")},
                   data={"folder": ""})
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["pictures_imported"] == 1 and report["warnings"] == [] and report["skipped"] == [], report
    [sheet] = guest.get(f"/api/blocks/{report['pages'][0]['id']}/subtree").json()["block"]["children"]
    [shown] = sheet["children"]
    placed = shown["properties"]["picture"]
    assert placed["url"].endswith(".svg") and guest.get(placed["url"]).content == QUADS
    assert placed["crop"] == {"x": 0, "y": 0, "w": 1, "h": 1}


def test_the_text_exports_bundle_the_svg_as_it_is(guest):
    url = _upload_image(guest, QUADS, "plot.svg", "image/svg+xml")
    page = guest.post("/api/pages", json={"title": "SVG bundle"}).json()["id"]
    _put_children(guest, page, [_placed("svgMd", url, content="bundled")])
    name = url.rsplit("/", 1)[1]
    for mode, folder_name in (("readable", "assets"), ("obsidian", "attachments"), ("logseq-graph", "assets"),
                              ("notion", "SVG bundle")):
        r = guest.get(f"/api/pages/{page}/export?mode={mode}")
        assert r.status_code == 200, r.text
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            assert z.read(f"{folder_name}/{name}") == QUADS, mode
