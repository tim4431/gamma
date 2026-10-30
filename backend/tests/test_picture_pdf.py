"""Pictures in the PDFs, the exports and the AI (gamma/picture.py,
docs/dev/pictures.md): the /Stamp the annotated export writes (its keys, its
place on turned and cropped pages, the picture drawn through its crop,
flips and turn, checked in MuPDF's pixels, over the highlights and in
document order), its lossless re-import and the image stamps other apps
embed (placed where their appearance draws them), what the import reads of
an untrusted PDF's images and how little, a picture drawn as the browser
shows it (EXIF orientation where Chromium and Firefox apply it), the
notebook PDF and the AI's page pictures with pictures under the boxes and
the ink, the text exports' lines and the files they bundle, and what the
agent is told of a picture. The picture value itself is pinned by
tests/shared/picture.json (test_picture.py)."""

import io
import json
import math
import subprocess
import tracemalloc
import zipfile
import zlib
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image, ImageOps
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject,
                            TextStringObject)

from ai_fixtures import folder, notes, org  # noqa: F401  (fixtures)
from test_pdf_export import _page_text
from test_text_box import CROP, _annots, _kids, _pdf, _put_children, _upload_pdf
from test_text_box import _imported as _records
from test_text_box import _reimported as _rerecords

from gamma import ink as inkmod
from gamma import notebook, picture, text_box
from gamma.ai_context import placed_label
from gamma.ai_tools import run_agent_tool
from gamma.logseq_graph_export import _render_block
from gamma.markdown_export import render_readable
from gamma.obsidian_export import VaultContext, render_vault_page
from gamma.pdf_document import render_document
from gamma.pdf_export import annotate_pdf, highlight_note_text, page_with_ink, zotero_annot_key
from gamma.pdf_image import DecodeBudget, XObjectStore, browser_orientation, image_file
from gamma.routers import imports
from gamma.zotero_export import note_html

RED, GREEN, BLUE, YELLOW, WHITE = (255, 0, 0), (0, 200, 0), (0, 0, 255), (255, 255, 0), (255, 255, 255)
QUADRANTS = {"TL": RED, "TR": GREEN, "BL": BLUE, "BR": YELLOW}


def _quads(w=80, h=40):
    """A picture in four coloured quadrants, so a flip or a turn shows in
    the pixels."""
    image = Image.new("RGB", (w, h))
    for x in range(w):
        for y in range(h):
            image.putpixel((x, y), QUADRANTS[("T" if y < h / 2 else "B") + ("L" if x < w / 2 else "R")])
    return image


def _file(image, fmt="PNG", orientation=None):
    buf = io.BytesIO()
    kw = {"quality": 95} if fmt in ("JPEG", "WEBP") else {}
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kw["exif"] = exif.tobytes()
    image.save(buf, fmt, **kw)
    return buf.getvalue()


def _stored(tmp_path, data, ext="png", stem="3f2a9c"):
    """An uploads directory holding ``data`` → (directory, its url)."""
    (tmp_path / f"{stem}.{ext}").write_bytes(data)
    return tmp_path, f"/api/uploads/{stem}.{ext}"


def _pic(url, **keys):
    return picture.normalize_picture({"url": url, "x": 100, "y": 50, "w": 200, "h": 100, **keys})


def _entry(pic, caption="", bid="pic1"):
    return {"picture": pic, "content": caption, "page": 1, "id": bid, "modified": "2026-10-01T12:00:00.000000Z"}


def _stamps(pdf_bytes):
    return [a for a in _annots(pdf_bytes) if a["/Subtype"] == "/Stamp"]


def _read(records):
    """The import's records with their pictures' files read, as the import
    reads those it adds (``_PictureFiles``); a picture that does not read
    is left out."""
    files = imports._PictureFiles()
    return [r for r in records if r.get("kind") != "picture" or files.read(r)]


def _imported(pdf_bytes):
    return _read(_records(pdf_bytes))


def _reimported(pdf_bytes, edit):
    return _read(_rerecords(pdf_bytes, edit))


def _raster(pdf_bytes):
    """The first page as MuPDF shows it (turned, annotations drawn), one
    pixel a point → ``pixel(x, y)``."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        pix = doc[0].get_pixmap(alpha=False)
    finally:
        doc.close()
    return pix.pixel


def _screen(pic, x, y):
    """What the screen draws at display point (x, y) of a picture, by the
    CSS of frontend/src/markup/picture.js (the frame turned about its
    centre, the inner box flipped, the image placed by its crop): the
    quadrant, or "out" outside the frame; None near an edge, where a pixel
    may go either way."""
    a = math.radians(pic["angle"])
    dx, dy = x - (pic["x"] + pic["w"] / 2), y - (pic["y"] + pic["h"] / 2)
    fx, fy = dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
    w, h, crop = pic["w"], pic["h"], pic["crop"]
    edge = min(w / 2 - abs(fx), h / 2 - abs(fy))
    if abs(edge) < 3:
        return None
    if edge < 0:
        return "out"
    fx, fy = -fx if pic["flip_h"] else fx, -fy if pic["flip_v"] else fy
    s, t = crop["x"] + (fx / w + 0.5) * crop["w"], crop["y"] + (fy / h + 0.5) * crop["h"]
    if min(abs(s - 0.5), abs(t - 0.5)) < 0.06:
        return None
    return ("T" if t < 0.5 else "B") + ("L" if s < 0.5 else "R")


def _near(got, want, tol=60):
    return max(abs(g - w) for g, w in zip(got, want)) <= tol


# --- the /Stamp ---------------------------------------------------------------------------


def test_the_stamp_and_its_keys(tmp_path):
    uploads, url = _stored(tmp_path, _file(_quads()))
    pic = _pic(url, angle=30, flip_h=True, crop={"x": 0.25, "w": 0.75})
    caption = "A *caption*\nover  two lines"
    out, written = annotate_pdf(_pdf(), [], author="tester", uploads_dir=uploads, pictures=[_entry(pic, caption)],
                                text_boxes=[{"box": text_box.normalize_text_box({"x": 10, "y": 10}),
                                             "content": "box", "page": 1, "id": "tb", "modified": ""}])
    assert written == 2
    # Under the box, as the page draws it.
    assert [a["/Subtype"] for a in _annots(out)] == ["/Stamp", "/FreeText"]
    [a] = _stamps(out)
    assert a["/IT"] == "/StampImage" and a["/F"] == 4 and a["/T"] == "tester" and a["/M"] == "D:20261001120000Z"
    assert a["/NM"] == f"Zotero-{zotero_annot_key('pic1')}"
    assert a["/Contents"] == "A caption over two lines"              # as a reader sees it
    assert json.loads(a["/GammaPicture"]) == {"v": 1, "picture": pic, "caption": caption,
                                              "text": "A caption over two lines"}
    form = a["/AP"]["/N"].get_object()
    assert form["/Subtype"] == "/Form" and [float(v) for v in form["/Matrix"]] == [1, 0, 0, 1, 0, 0]
    assert [float(v) for v in form["/BBox"]] == [0, 0, *picture.turned_size(pic)]
    [image] = form["/Resources"]["/XObject"].values()
    assert image.get_object()["/Subtype"] == "/Image"
    # No caption, no /Contents; no author, no /T.
    [bare] = _stamps(annotate_pdf(_pdf(), [], uploads_dir=uploads, pictures=[_entry(pic)])[0])
    assert "/Contents" not in bare and "/T" not in bare


@pytest.mark.parametrize("rotation, rect, matrix", [
    (0, (130, 610, 330, 710), (1, 0, 0, 1, 0, 0)),
    (90, (80, 140, 180, 340), (0, 1, -1, 0, 100, 0)),
    (180, (280, 90, 480, 190), (-1, 0, 0, -1, 200, 100)),
    (270, (430, 460, 530, 660), (0, -1, 1, 0, 0, 200)),
])
def test_the_rect_follows_the_crop_box_and_the_rotation(tmp_path, rotation, rect, matrix):
    """The picture (100, 50) 200 × 100 in the display frame of a page
    cropped to 550 × 720 at (30, 40): its user-space /Rect, and a /Matrix
    that turns the appearance back so it stands as the page shows it."""
    uploads, url = _stored(tmp_path, _file(_quads()))
    [a] = _stamps(annotate_pdf(_pdf(rotation, CROP), [], uploads_dir=uploads, pictures=[_entry(_pic(url))])[0])
    assert [float(v) for v in a["/Rect"]] == pytest.approx(rect, abs=0.001)
    form = a["/AP"]["/N"].get_object()
    assert [float(v) for v in form["/BBox"]] == [0, 0, 200, 100]
    assert [float(v) for v in form["/Matrix"]] == list(matrix)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("angle, flip_h, flip_v", [(0, False, False), (30, True, False), (30, False, True),
                                                   (-150, True, True)])
def test_the_stamp_draws_the_picture_as_the_screen_does(tmp_path, rotation, angle, flip_h, flip_v):
    """Rendered as a viewer shows the turned page, every pixel of the frame
    is the quadrant the screen draws there, through the crop, the flips and
    the turn, and the turned box around the frame is paper."""
    uploads, url = _stored(tmp_path, _file(_quads()))
    pic = _pic(url, x=150, y=120, w=200, h=120, angle=angle, flip_h=flip_h, flip_v=flip_v,
               crop={"x": 0.25, "y": 0, "w": 0.75, "h": 1})
    pixel = _raster(annotate_pdf(_pdf(rotation, CROP), [], uploads_dir=uploads, pictures=[_entry(pic)])[0])
    x0, y0, x1, y1 = picture.turned_box(pic)
    inside = outside = 0
    for x in range(int(x0), int(x1), 5):
        for y in range(int(y0), int(y1), 5):
            want = _screen(pic, x + 0.5, y + 0.5)
            if want is None:
                continue
            got = pixel(x, y)
            assert _near(got, WHITE if want == "out" else QUADRANTS[want]), (x, y, want, got)
            inside, outside = inside + (want != "out"), outside + (want == "out")
    assert inside > 200 and (outside > 50 or not angle)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_export_then_import_restores_the_picture(tmp_path, rotation):
    uploads, url = _stored(tmp_path, _file(_quads()))
    pic = _pic(url, x=150.37, y=120.11, angle=30, flip_v=True, crop={"x": 0.1, "y": 0.2, "w": 0.6, "h": 0.5})
    out, _ = annotate_pdf(_pdf(rotation, CROP), [], uploads_dir=uploads, pictures=[_entry(pic, "**Fig. 1**")])
    [found] = _imported(out)
    assert found["kind"] == "picture" and found["page"] == 1 and found["content"] == "**Fig. 1**"
    assert found["original"] == url
    got = found["picture"]
    assert {k: v for k, v in got.items() if k not in ("x", "y", "url")} == \
        {k: v for k, v in pic.items() if k not in ("x", "y", "url")}
    assert (got["x"], got["y"]) == (pytest.approx(pic["x"], abs=0.01), pytest.approx(pic["y"], abs=0.01))
    # The file read back is the picture, its name its content hash.
    data, ext = found["file"]
    assert ext == ".png" and got["url"].endswith(ext)
    assert Image.open(io.BytesIO(data)).convert("RGB").tobytes() == _quads().tobytes()


def test_a_move_or_a_new_caption_in_another_viewer_holds(tmp_path):
    uploads, url = _stored(tmp_path, _file(_quads()))
    pic = _pic(url, angle=-45)
    out, _ = annotate_pdf(_pdf(), [], uploads_dir=uploads, pictures=[_entry(pic, "my *figure*")])

    def moved(annot):
        x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
        annot[NameObject("/Rect")] = ArrayObject(FloatObject(v) for v in (x0 + 20, y0 - 10, x1 + 20, y1 - 10))

    [found] = _reimported(out, moved)
    assert found["content"] == "my *figure*"                       # the caption as written
    assert (found["picture"]["x"], found["picture"]["y"]) == (pytest.approx(120), pytest.approx(60))
    assert found["picture"]["angle"] == -45

    def retyped(annot):
        annot[NameObject("/Contents")] = TextStringObject("costs $5 *now*")

    [found] = _reimported(out, retyped)
    assert found["content"] == text_box.markdown_of("costs $5 *now*")


def test_the_stamps_contents_is_the_caption_as_a_reader_sees_it(tmp_path):
    """/Contents is the caption's plain text, as a text box's is, so another
    viewer shows no Markdown escapes; a re-import keeps the Markdown, its
    references with it, until another viewer edits that text."""
    uploads, url = _stored(tmp_path, _file(_quads()))
    escaped = text_box.escape_markdown("my_photo_2024.jpg")                 # as the Notability import stores it
    out, _ = annotate_pdf(_pdf(), [], uploads_dir=uploads, pictures=[_entry(_pic(url), escaped)])
    [a] = _stamps(out)
    assert escaped != "my_photo_2024.jpg" and a["/Contents"] == "my_photo_2024.jpg"
    assert _imported(out)[0]["content"] == escaped

    def appended(annot):
        annot[NameObject("/Contents")] = TextStringObject(str(annot["/Contents"]) + " (2)")

    [found] = _reimported(out, appended)
    assert found["content"] == text_box.markdown_of("my_photo_2024.jpg (2)") == escaped + " (2)"
    linked = "**Fig.** of [[abc123]]"
    out, _ = annotate_pdf(_pdf(), [], uploads_dir=uploads, pictures=[_entry(_pic(url), linked)],
                          resolve_ref=lambda ref: {"content": "the setup"} if ref == "abc123" else None)
    [a] = _stamps(out)
    assert a["/Contents"] == "Fig. of the setup"
    assert _imported(out)[0]["content"] == linked


def test_a_foreign_image_stamp_is_a_picture_over_its_rect():
    """An image stamp another app wrote (PyMuPDF here, as Acrobat's photo
    stamps are): a picture at its /Rect, upright, the whole image; a rubber
    stamp of text, or one of vectors alone, makes nothing."""
    doc = pymupdf.open(stream=_pdf(), filetype="pdf")
    try:
        doc[0].add_stamp_annot(pymupdf.Rect(100, 120, 300, 220), stamp=_file(_quads()))
        doc[0].add_stamp_annot(pymupdf.Rect(100, 400, 300, 460), stamp=0)        # "Approved", in words
        data = doc.tobytes()
    finally:
        doc.close()
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(data)))
    vectors = DecodedStreamObject()
    vectors.set_data(b"1 0 0 RG 4 w 2 2 96 46 re S")
    vectors.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                    NameObject("/BBox"): ArrayObject(FloatObject(v) for v in (0, 0, 100, 50))})
    writer.add_annotation(page_number=0, annotation=DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Stamp"),
        NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (300, 100, 400, 150)),
        NameObject("/AP"): DictionaryObject({NameObject("/N"): writer._add_object(vectors)})}))
    buf = io.BytesIO()
    writer.write(buf)
    [found] = _imported(buf.getvalue())
    assert found["kind"] == "picture" and found["original"] is None
    assert {k: v for k, v in found["picture"].items() if k != "url"} == {
        "x": 100, "y": 120, "w": 200, "h": 100, "angle": 0, "crop": {"x": 0, "y": 0, "w": 1, "h": 1},
        "flip_h": False, "flip_v": False}
    data, _ext = found["file"]
    shown = Image.open(io.BytesIO(data)).convert("RGB")
    assert shown.size == (80, 40)
    assert [shown.getpixel(p) for p in ((10, 5), (70, 5), (10, 35), (70, 35))] == [RED, GREEN, BLUE, YELLOW]


def _matches_the_pdf(pdf_bytes, found):
    """Whether the imported picture ``found`` shows what MuPDF draws of
    the stamp it came from: the picture's quadrants where the screen draws
    them (``_screen``), sampled every 5 points over its turned box, in the
    file as the browser shows it."""
    pixel = _raster(pdf_bytes)
    pic = found["picture"]
    shown = ImageOps.exif_transpose(Image.open(io.BytesIO(found["file"][0]))).convert("RGB")
    colours = {q: shown.getpixel((int((0.25 if q[1] == "L" else 0.75) * shown.width),
                                  int((0.25 if q[0] == "T" else 0.75) * shown.height))) for q in QUADRANTS}
    x0, y0, x1, y1 = picture.turned_box(pic)
    checked = 0
    for x in range(int(x0), int(x1), 5):
        for y in range(int(y0), int(y1), 5):
            want = _screen(pic, x + 0.5, y + 0.5)
            if want is None or want == "out":
                continue
            if not _near(pixel(x, y), colours[want]):
                return False
            checked += 1
    return checked > 100


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_a_foreign_stamp_on_a_turned_page_turns_with_it(rotation):
    """A stamp that does not counter-turn its appearance turns with the page,
    as viewers draw it: the picture is the image turned so, its aspect
    kept, where MuPDF shows it."""
    doc = pymupdf.open(stream=_pdf(), filetype="pdf")
    try:
        doc[0].set_rotation(rotation)
        doc[0].add_stamp_annot(pymupdf.Rect(100, 120, 300, 220), stamp=_file(_quads()))
        data = doc.tobytes()
    finally:
        doc.close()
    [found] = _imported(data)
    pic = found["picture"]
    assert (pic["w"], pic["h"]) == (pytest.approx(200, abs=0.02), pytest.approx(100, abs=0.02))
    assert pic["angle"] == (rotation if rotation < 180 else rotation - 360) and not pic["flip_h"]
    assert _matches_the_pdf(data, found)


def test_a_no_rotate_stamp_stays_upright_on_a_turned_page():
    """A NoRotate stamp does not turn with the page: it stays upright,
    pinned at its /Rect's top-left, as MuPDF draws it."""
    doc = pymupdf.open(stream=_pdf(), filetype="pdf")
    try:
        doc[0].set_rotation(270)
        stamp = doc[0].add_stamp_annot(pymupdf.Rect(100, 120, 300, 220), stamp=_file(_quads()))
        stamp.set_flags(pymupdf.PDF_ANNOT_IS_NO_ROTATE | pymupdf.PDF_ANNOT_IS_PRINT)
        stamp.update()
        data = doc.tobytes()
    finally:
        doc.close()
    [found] = _imported(data)
    assert {k: found["picture"][k] for k in ("x", "y", "w", "h", "angle")} == \
        {"x": 120, "y": 512, "w": 200, "h": 100, "angle": 0}
    assert _matches_the_pdf(data, found)


@pytest.mark.parametrize("rotation", [0, 90])
def test_a_foreign_stamp_is_placed_where_its_appearance_draws_the_image(tmp_path, rotation):
    """Read from the appearance alone (a Gamma export with its record taken
    off): the image's matrix in the form's content, the form's /Matrix and
    the page's turn give back the picture as it was placed, turned and
    mirrored."""
    uploads, url = _stored(tmp_path, _file(_quads()))
    pic = _pic(url, x=150, y=120, w=200, h=120, angle=30, flip_h=True)
    out, _ = annotate_pdf(_pdf(rotation, CROP), [], uploads_dir=uploads, pictures=[_entry(pic)])

    def anonymous(annot):
        del annot["/GammaPicture"]

    [found] = _reimported(out, anonymous)
    assert found["original"] is None
    assert {k: v for k, v in found["picture"].items() if k not in ("x", "y", "url")} == \
        {k: v for k, v in pic.items() if k not in ("x", "y", "url")}
    assert (found["picture"]["x"], found["picture"]["y"]) == (pytest.approx(150, abs=0.02),
                                                              pytest.approx(120, abs=0.02))


def test_a_foreign_jpeg_stamp_comes_in_as_the_pdf_draws_it():
    """A camera JPEG another app embedded as it is, its EXIF orientation 6
    with it, is drawn unturned by PDF viewers: the file the import stores
    carries orientation 1, so the browser shows it unturned too, and the
    rest of its bytes are the JPEG's own."""
    jpeg = _file(_quads(), "JPEG", 6)
    assert browser_orientation(jpeg) == 6
    doc = pymupdf.open(stream=_pdf(), filetype="pdf")
    try:
        doc[0].add_stamp_annot(pymupdf.Rect(100, 120, 300, 220), stamp=jpeg)
        data = doc.tobytes()
    finally:
        doc.close()
    [found] = _imported(data)
    stored, ext = found["file"]
    assert ext == ".jpg" and browser_orientation(stored) == 1 and len(stored) == len(jpeg)
    assert Image.open(io.BytesIO(stored)).tobytes() == Image.open(io.BytesIO(jpeg)).tobytes()
    assert (found["picture"]["w"], found["picture"]["h"]) == (200, 100)
    assert _matches_the_pdf(data, found)


# --- as the browser shows it ----------------------------------------------------------------


def _shows_as(pdf_bytes, shown, scale):
    """Whether the first page draws ``shown`` (a Pillow image) at its
    top-left, ``scale`` points a pixel: its quadrants' centres sampled."""
    pixel = _raster(pdf_bytes)
    w, h = shown.size
    return all(_near(pixel(int(fx * w * scale), int(fy * h * scale)), shown.getpixel((int(fx * w), int(fy * h))))
               for fx in (0.25, 0.75) for fy in (0.25, 0.75))


def _png_with(image, chunk_after_idat=None, text=None):
    """A PNG of ``image`` with an eXIf chunk after its image data, or a
    "Raw profile type exif" text chunk as ImageMagick writes one: EXIF
    Chromium and Firefox ignore."""
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    if text is not None:
        exif = Image.Exif()
        exif[0x0112] = text
        raw = b"Exif\x00\x00" + exif.tobytes()
        info.add_text("Raw profile type exif", f"\nexif\n{len(raw):8d}\n{raw.hex()}\n", zip=True)
    buf = io.BytesIO()
    image.save(buf, "PNG", pnginfo=info)
    data = buf.getvalue()
    if chunk_after_idat is not None:
        exif = Image.Exif()
        exif[0x0112] = chunk_after_idat
        body = exif.tobytes()[6:] if exif.tobytes()[:6] == b"Exif\x00\x00" else exif.tobytes()
        chunk = len(body).to_bytes(4, "big") + b"eXIf" + body + zlib.crc32(b"eXIf" + body).to_bytes(4, "big")
        end = data.rindex(b"IEND") - 4
        data = data[:end] + chunk + data[end:]
    return data


@pytest.mark.parametrize("fmt, orientation, turned", [("JPEG", n, True) for n in range(1, 9)]
                         + [("PNG", 6, True), ("PNG", 7, True), ("WEBP", 6, False), ("WEBP", 3, False),
                            ("PNG-late", 6, False), ("PNG-text", 6, False), ("PNGA-text", 6, False)])
def test_a_picture_is_drawn_as_the_browser_shows_it(tmp_path, fmt, orientation, turned):
    """A phone photo's EXIF orientation applied where Chromium and Firefox
    apply it, a JPEG's and a PNG's eXIf before its image data, and the
    picture drawn as stored where they ignore it: a WebP's EXIF, a PNG's
    eXIf after its image data, the text-chunk profile ImageMagick writes
    (verbatim or decoded, an RGBA PNG). At the size as shown."""
    image = _quads(60, 30)
    if fmt == "PNG-late":
        data = _png_with(image, chunk_after_idat=orientation)
    elif fmt.endswith("-text"):
        data = _png_with(image.convert("RGBA") if fmt == "PNGA-text" else image, text=orientation)
    else:
        data = _file(image, fmt, orientation)
    opened = Image.open(io.BytesIO(data))
    assert fmt == "PNG-late" or ImageOps.exif_transpose(opened).size != opened.size or orientation in (1, 2, 3, 4)
    shown = (ImageOps.exif_transpose(opened) if turned else opened).convert("RGB")
    assert browser_orientation(data) == (orientation if turned else 1)
    ext = "jpg" if fmt == "JPEG" else fmt.lower()[:4] if fmt == "WEBP" else "png"
    uploads, url = _stored(tmp_path, data, ext)
    store = XObjectStore(PdfWriter(), uploads)
    assert store.resolve(url)[1:] == shown.size
    pic = picture.normalize_picture({"url": url, "w": shown.width * 4, "h": shown.height * 4})
    pdf = notebook.notebook_pdf([(notebook.normalize_paper({"width": 300, "height": 300}), [], [])],
                                pictures=[[pic]], uploads_dir=uploads)
    assert _shows_as(pdf, shown, 4)


def test_an_oriented_jpeg_goes_in_and_comes_back_as_it_is(tmp_path):
    """A JPEG keeps its bytes in the PDF, drawn through a form that applies
    its orientation, and the import reads back the same file; a PNG's
    orientation, which its embedded rows lose, is applied to the PNG read
    back."""
    jpeg = _file(_quads(60, 30), "JPEG", 6)
    uploads, url = _stored(tmp_path, jpeg, "jpg")
    out, _ = annotate_pdf(_pdf(), [], uploads_dir=uploads, pictures=[_entry(_pic(url, w=60, h=120))])
    [stamp] = _stamps(out)
    [turned] = stamp["/AP"]["/N"]["/Resources"]["/XObject"].values()
    turned = turned.get_object()
    assert turned["/Subtype"] == "/Form" and turned["/GammaOrientation"] == 6
    [image] = turned["/Resources"]["/XObject"].values()
    assert image.get_object()["/Filter"] == "/DCTDecode" and image.get_object().get_data() == jpeg
    [found] = _imported(out)
    assert found["file"] == (jpeg, ".jpg")
    png = _file(_quads(60, 30), "PNG", 8)
    uploads, url = _stored(tmp_path, png, "png", stem="0b8d")
    [found] = _imported(annotate_pdf(_pdf(), [], uploads_dir=uploads, pictures=[_entry(_pic(url))])[0])
    shown = ImageOps.exif_transpose(Image.open(io.BytesIO(png))).convert("RGB")
    assert Image.open(io.BytesIO(found["file"][0])).convert("RGB").tobytes() == shown.tobytes()


def test_a_translucent_picture_comes_back_with_its_alpha(tmp_path):
    """A PNG with an alpha channel goes in as its colour and a soft mask,
    and comes back as a PNG with that alpha."""
    image = _quads().convert("RGBA")
    image.putalpha(Image.linear_gradient("L").resize(image.size))
    uploads, url = _stored(tmp_path, _file(image))
    [found] = _imported(annotate_pdf(_pdf(), [], uploads_dir=uploads, pictures=[_entry(_pic(url))])[0])
    back = Image.open(io.BytesIO(found["file"][0]))
    assert found["file"][1] == ".png" and back.mode == "RGBA" and back.tobytes() == image.tobytes()


def test_an_image_xobject_comes_back_as_a_file():
    """``image_file`` reads what other writers embed too: a gray image with
    a soft mask becomes a PNG with that alpha; an image mask is no picture."""
    def image(data, w, h, space, **extra):
        stream = DecodedStreamObject()
        stream.set_data(data)
        stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                       NameObject("/Width"): FloatObject(w), NameObject("/Height"): FloatObject(h),
                       NameObject("/ColorSpace"): NameObject(space),
                       NameObject("/BitsPerComponent"): FloatObject(8), **extra})
        return stream

    gray = image(bytes([0, 255, 128, 64]), 2, 2, "/DeviceGray",
                 **{NameObject("/SMask"): image(bytes([255, 0, 255, 0]), 2, 2, "/DeviceGray")})
    data, ext = image_file(gray)
    shown = Image.open(io.BytesIO(data))
    assert ext == ".png" and shown.mode == "LA"
    assert [shown.getpixel(p) for p in ((0, 0), (1, 0), (0, 1), (1, 1))] == [(0, 255), (255, 0), (128, 255), (64, 0)]
    mask = image(b"\x00", 1, 1, "/DeviceGray", **{NameObject("/ImageMask"): FloatObject(1)})
    assert image_file(mask) is None


# --- reading an untrusted PDF's images ------------------------------------------------------


def _image(data, w, h, filt=None, space="/DeviceGray", **extra):
    """An image XObject over ``data`` as a writer stores it, ``filt`` its
    filter (the data as encoded)."""
    stream = DecodedStreamObject()
    stream.set_data(data)
    stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                   NameObject("/Width"): FloatObject(w), NameObject("/Height"): FloatObject(h),
                   NameObject("/ColorSpace"): NameObject(space), NameObject("/BitsPerComponent"): FloatObject(8),
                   **({NameObject("/Filter"): NameObject(filt)} if filt else {}), **extra})
    return stream


def test_an_image_is_opened_as_the_format_its_filter_names(tmp_path, monkeypatch):
    """PostScript in an image's data never reaches Pillow's EPS reader
    (which runs Ghostscript on it): not as JPEG 2000, not as a JPEG with a
    mask, not as an upload the PDF writers embed."""
    from PIL import EpsImagePlugin

    opened = []
    monkeypatch.setattr(EpsImagePlugin.EpsImageFile, "_open", lambda self: opened.append(1) or 1 / 0)
    monkeypatch.setattr(subprocess, "check_call", lambda *a, **k: opened.append(2) or 1 / 0)
    eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 2 2\n{} loop\n%%EOF\n"
    mask = _image(bytes([255] * 4), 2, 2)
    assert image_file(_image(eps, 2, 2, "/JPXDecode", "/DeviceRGB")) is None
    assert image_file(_image(eps, 2, 2, "/DCTDecode", "/DeviceRGB", **{NameObject("/SMask"): mask})) is None
    uploads, _url = _stored(tmp_path, eps)
    assert XObjectStore(PdfWriter(), uploads).resolve(_url) is None
    assert opened == []


def test_an_images_size_is_its_dictionarys():
    """The caps count the declared /Width and /Height, so the data must be
    that size: a JPEG or JPEG 2000 that is larger is refused before it is
    decoded."""
    def encoded(size, fmt):
        buf = io.BytesIO()
        Image.new("RGB", size, RED).save(buf, fmt)
        return buf.getvalue()

    mask = _image(bytes([255]), 1, 1)
    jpeg = encoded((64, 64), "JPEG")
    assert image_file(_image(jpeg, 1, 1, "/DCTDecode", "/DeviceRGB", **{NameObject("/SMask"): mask})) is None
    assert image_file(_image(encoded((64, 64), "JPEG2000"), 1, 1, "/JPXDecode", "/DeviceRGB")) is None
    data, ext = image_file(_image(jpeg, 64, 64, "/DCTDecode", "/DeviceRGB", **{NameObject("/SMask"): mask}))
    assert ext == ".png" and Image.open(io.BytesIO(data)).size == (64, 64)


def test_an_image_stream_is_never_inflated_past_its_size():
    """A 1 × 1 image whose data inflates to 50 MB is read for its one byte:
    nothing near that is held, and nothing is left on the reader's object."""
    zeros = _image(zlib.compress(bytes(50_000_000), 9), 1, 1, "/FlateDecode")
    doc = PdfWriter()
    doc.add_blank_page(width=10, height=10)
    doc.pages[0][NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject(
        {NameObject("/Im"): doc._add_object(zeros)})})
    buf = io.BytesIO()
    doc.write(buf)
    image = PdfReader(io.BytesIO(buf.getvalue())).pages[0]["/Resources"]["/XObject"]["/Im"].get_object()
    tracemalloc.start()
    try:
        data, ext = image_file(image)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert ext == ".png" and Image.open(io.BytesIO(data)).getpixel((0, 0)) == 0
    assert peak < 5_000_000 and getattr(image, "decoded_self", None) is None


# --- the notebook PDF and the AI's page pictures ----------------------------------------------


def _ink(color="#000000", points=((150, 30), (150, 270)), size=8, width=300, height=300, kind="canvas"):
    space = {"kind": kind, "width": width, "height": height, **({"page": 1} if kind == "pdf-page" else {})}
    return inkmod.parse_ink({"format": "gamma-ink", "version": 2, "space": space,
                             "strokes": [{"id": "s1", "color": color, "size": size, "ch": "xy",
                                          "pts": inkmod.encode_points([{"x": x, "y": y} for x, y in points], "xy")}]})


def test_the_notebook_pdf_draws_a_sheets_pictures_under_its_boxes_and_ink(tmp_path):
    uploads, url = _stored(tmp_path, _file(_quads()))
    pic = picture.normalize_picture({"url": url, "x": 50, "y": 50, "w": 200, "h": 200})
    box = text_box.normalize_text_box({"x": 60, "y": 60, "w": 60, "h": 40, "auto": False, "bg": "#ff00ff"})
    paper = notebook.normalize_paper({"width": 300, "height": 300})
    pixel = _raster(notebook.notebook_pdf([(paper, [("", box)], [_ink()])], pictures=[[pic]], uploads_dir=uploads))
    assert pixel(80, 90) == (255, 0, 255)                   # the box over the picture
    assert _near(pixel(100, 200), BLUE) and _near(pixel(200, 200), YELLOW)
    assert pixel(150, 150) == (0, 0, 0)                     # the ink over both
    assert pixel(20, 20) == WHITE                           # the paper
    # The sheet's pictures, as notebook_pdf takes them: drawable ones, in order.
    blocks = [{"id": "a", "content": "", "properties": {"picture": {"url": url, "x": 5}}},
              {"id": "b", "content": "", "properties": {"picture": {"url": "nope"}}},
              {"id": "c", "content": "", "properties": {"text_box": {}, "picture": {"url": url}}}]
    assert notebook.sheet_pictures(blocks) == [picture.normalize_picture({"url": url, "x": 5})]


def test_page_with_ink_draws_the_pictures_and_boxes_under_the_ink(tmp_path):
    """The page as the user sees it: its pictures, then its text boxes,
    then the handwriting; a picture or box on another page is not drawn."""
    uploads, url = _stored(tmp_path, _file(_quads()))
    pictures = [{**_entry(picture.normalize_picture({"url": url, "x": 50, "y": 50, "w": 200, "h": 200})), "page": 1},
                {**_entry(picture.normalize_picture({"url": url, "x": 0, "y": 400, "w": 100, "h": 100})), "page": 2}]
    boxes = [{"box": text_box.normalize_text_box({"x": 60, "y": 60, "w": 60, "h": 40, "auto": False, "bg": "#ff00ff"}),
              "content": "", "page": 1, "id": "tb", "modified": ""}]
    ink = _ink(kind="pdf-page", width=612, height=792)
    data, pages = page_with_ink(_pdf(), 1, [ink], pictures=pictures, text_boxes=boxes, uploads_dir=uploads)
    pixel = _raster(data)
    assert pages == 1 and pixel(80, 90) == (255, 0, 255)
    assert _near(pixel(100, 200), BLUE) and _near(pixel(200, 100), GREEN)
    assert pixel(150, 150) == (0, 0, 0) and pixel(50, 450) == WHITE
    assert page_with_ink(_pdf(), 1, [], pictures=pictures[1:], uploads_dir=uploads)[0]   # nothing on p. 1


def test_page_with_ink_draws_an_embedded_picture_once(tmp_path):
    """A picture imported from the PDF and still embedded in it is drawn as
    Gamma has it, its original taken off the copy, as the export does."""
    uploads, url = _stored(tmp_path, _file(_quads()))
    original = annotate_pdf(_pdf(), [], uploads_dir=uploads, pictures=[_entry(_pic(url))])[0]
    [found] = _imported(original)
    moved = {**_entry({**found["picture"], "url": url, "x": 300, "y": 400})}
    data, _ = page_with_ink(original, 1, [], pictures=[moved], replaced={found["key"]}, uploads_dir=uploads)
    pixel = _raster(data)
    assert pixel(150, 75) == WHITE and _near(pixel(350, 425), RED)
    assert _annots(data) == []


# --- through the endpoints ----------------------------------------------------------------------


def _upload_image(guest, data, name="p.png", kind="image/png"):
    r = guest.post("/api/upload-image", files={"file": (name, data, kind)})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _placed(bid, url, page=1, content="", **keys):
    return {"id": bid, "content": content, "children": [],
            "properties": {"pdf_page": page, "picture": {"url": url, "x": 100, "y": 100, "w": 120, "h": 60, **keys}}}


def test_the_export_writes_the_pictures_on_the_pdf(guest):
    """Every picture on a PDF page, one with no caption included; none on a
    sheet, none whose file is gone; with either switch on."""
    url = _upload_image(guest, _file(_quads()))
    page, _doc = _upload_pdf(guest, _pdf(), "Picture export")
    _put_children(guest, page["id"], [
        _placed("picA", url, content="On the page"),
        _placed("picBare", url, y=300),
        _placed("picGone", "/api/uploads/0123456789abcdef01234567.png"),
        {"id": "picSheet", "content": "", "properties": {"sheet": notebook.normalize_paper(None)},
         "children": [_placed("picOnSheet", url, content="On the sheet")]},
    ])
    for query in ("", "?highlights=0&notes=1"):
        r = guest.get(f"/api/pages/{page['id']}/export-pdf{query}")
        assert r.status_code == 200, r.text
        assert r.headers["x-annotations-written"] == "2"
        stamps = _stamps(r.content)
        assert sorted(str(a.get("/Contents", "")) for a in stamps) == ["", "On the page"]
    assert guest.get(f"/api/pages/{page['id']}/export-pdf?highlights=0").headers["x-annotations-written"] == "0"


def test_a_highlight_is_under_the_pictures(tmp_path):
    """Annotations paint in the order they are written, so a highlight is
    written under the picture over it, as the page stacks them."""
    uploads, url = _stored(tmp_path, _file(Image.new("RGB", (40, 40), BLUE)))
    band = {"position": {"pageNumber": 1, "width": 612, "height": 792,
                         "rects": [{"x1": 50, "y1": 180, "x2": 400, "y2": 220}]}, "color": "#ffeb3b", "note": ""}
    out, written = annotate_pdf(_pdf(), [band], uploads_dir=uploads,
                                pictures=[_entry(_pic(url, x=100, y=100, w=200, h=200))])
    assert written == 2 and [a["/Subtype"] for a in _annots(out)] == ["/Highlight", "/Stamp"]
    pixel = _raster(out)
    assert _near(pixel(150, 200), BLUE, 10) and _near(pixel(150, 150), BLUE, 10)
    assert not _near(pixel(350, 200), WHITE, 10)                      # the highlight, off the picture


def test_overlapping_pictures_and_boxes_stack_in_document_order(guest):
    """Bring to front moves a picture after its siblings: the export, and
    the agent's picture of the page, draw it on top, whatever order the
    rows were stored in. Text boxes stack so too."""
    red = _upload_image(guest, _file(Image.new("RGB", (40, 20), RED)), "red.png")
    blue = _upload_image(guest, _file(Image.new("RGB", (40, 20), BLUE)), "blue.png")
    page, _doc = _upload_pdf(guest, _pdf(), "Pictures in a stack")

    def box(bid, bg, y):
        return {"id": bid, "content": bid, "children": [], "properties": {"pdf_page": 1, "text_box": {
            "x": 100, "y": y, "w": 120, "h": 40, "auto": False, "bg": bg}}}

    _put_children(guest, page["id"], [_placed("picStackA", red), _placed("picStackB", blue, x=130),
                                      box("tbStackA", "#ff0000", 400), box("tbStackB", "#0000ff", 420)])
    kids = {k["id"]: k["position"] for k in _kids(guest, page["id"])}
    for moved, over in (("picStackA", "picStackB"), ("tbStackA", "tbStackB")):   # Bring to front
        r = guest.post(f"/api/blocks/{moved}/reorder", json={"before": kids[over]})
        assert r.status_code == 200, r.text
    assert [k["id"] for k in _kids(guest, page["id"])] == ["picStackB", "picStackA", "tbStackB", "tbStackA"]
    out = guest.get(f"/api/pages/{page['id']}/export-pdf").content
    keys = {f"Zotero-{zotero_annot_key(bid)}": bid for bid in kids}
    assert [keys[str(a["/NM"])] for a in _annots(out)] == ["picStackB", "picStackA", "tbStackB", "tbStackA"]
    pixel = _raster(out)
    assert _near(pixel(160, 130), RED, 10)                            # where the two pictures overlap
    r = guest.get(f"/api/ai/page-image/{page['id']}?page=1&ink=1")
    assert r.status_code == 200, r.text
    image = Image.open(io.BytesIO(r.content)).convert("RGB")
    assert _near(image.getpixel((int(160 / 612 * image.width), int(130 / 792 * image.height))), RED, 30)


def _stamp_pdf(tmp_path, images, per_image=1, note=False):
    """A PDF whose page holds ``per_image`` /Stamps for each of ``images``
    (Pillow images, embedded with their alpha as a soft mask), all the
    stamps of one image drawing the same appearance, and a /Text note when
    ``note``."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    store = XObjectStore(writer, tmp_path)
    for n, image in enumerate(images):
        _stored(tmp_path, _file(image), stem=f"5{n}")
        name, _w, _h = store.resolve(f"/api/uploads/5{n}.png")
        form = DecodedStreamObject()
        form.set_data(b"/Im Do")
        form.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                     NameObject("/BBox"): ArrayObject(FloatObject(v) for v in (0, 0, 1, 1)),
                     NameObject("/Resources"): DictionaryObject({NameObject("/XObject"): DictionaryObject(
                         {NameObject("/Im"): store.refs[name]})})})
        ref = writer._add_object(form)
        for k in range(per_image):
            x, y = 20 + 5 * k, 20 + 60 * n
            writer.add_annotation(page_number=0, annotation=DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Stamp"),
                NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (x, y, x + 40, y + 20)),
                NameObject("/AP"): DictionaryObject({NameObject("/N"): ref})}))
    if note:
        writer.add_annotation(page_number=0, annotation=DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Text"),
            NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (300, 700, 320, 720)),
            NameObject("/Contents"): TextStringObject("a note")}))
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _translucent(colour):
    image = Image.new("RGBA", (40, 20), colour + (255,))
    image.putpixel((0, 0), colour + (0,))
    return image


def _import(guest, page, doc_id, strip=False):
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id, "strip": strip})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_import_reads_an_image_once_and_only_for_the_stamps_it_adds(guest, tmp_path, monkeypatch):
    """Thirty stamps of one image decode it once, and read where their
    shared appearance draws it once; an import that adds nothing, and the
    strip, decode nothing."""
    pdf = _stamp_pdf(tmp_path, [_translucent(RED)], per_image=30)
    walks = []
    walk = imports._Drawn._walk
    monkeypatch.setattr(imports._Drawn, "_walk", lambda *a: walks.append(1) or walk(*a))
    assert len(_records(pdf)) == 30 and len(walks) == 1
    calls = []
    real = imports.image_file
    monkeypatch.setattr(imports, "image_file", lambda *a, **k: calls.append(1) or real(*a, **k))
    page, doc_id = _upload_pdf(guest, pdf, "Many stamps")
    assert _import(guest, page, doc_id) == {"ok": True, "found": 30, "imported": 30, "stripped": 0}
    assert len(calls) == 1
    urls = {k["properties"]["picture"]["url"] for k in _kids(guest, page["id"])}
    assert len(urls) == 1 and guest.get(urls.pop()).status_code == 200
    assert _import(guest, page, doc_id, strip=True) == {"ok": True, "found": 30, "imported": 0, "stripped": 30}
    assert len(calls) == 1


def test_an_import_decodes_within_its_budget(guest, tmp_path, monkeypatch):
    """Past the import's decode budget a stamp is left in the file, for the
    next import to take."""
    monkeypatch.setattr(imports, "DecodeBudget", lambda: DecodeBudget(2000))   # one 40 × 20 image and its mask
    page, doc_id = _upload_pdf(guest, _stamp_pdf(tmp_path, [_translucent(RED), _translucent(BLUE)]), "Budget")
    assert _import(guest, page, doc_id, strip=True) == {"ok": True, "found": 1, "imported": 1, "stripped": 1}
    assert _import(guest, page, doc_id, strip=True) == {"ok": True, "found": 1, "imported": 1, "stripped": 1}
    assert len(_kids(guest, page["id"])) == 2


def test_a_picture_past_the_quota_stops_the_import(tmp_path, monkeypatch):
    """The storage quota is the one refusal storing a picture meets: past
    it (507) the import stops, as the handwriting's files stop it, adding
    nothing and stripping nothing, and an import with room takes it all."""
    from fastapi import HTTPException
    from gamma import storage
    from test_zotero_import import _library

    def full(ws, nbytes):
        raise HTTPException(status_code=507, detail="storage quota exceeded")

    c, _ = _library()  # one that stores none of the picture's bytes yet
    page, doc_id = _upload_pdf(c, _stamp_pdf(tmp_path, [_translucent(RED)], note=True), "Past the quota")
    monkeypatch.setattr(storage, "check_upload_allowed", full)
    r = c.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id, "strip": True})
    assert r.status_code == 507, r.text
    assert _kids(c, page["id"]) == [] and len(_annots(c.get(f"/api/uploads/{doc_id}.pdf").content)) == 2
    monkeypatch.undo()
    assert _import(c, page, doc_id, strip=True) == {"ok": True, "found": 2, "imported": 2, "stripped": 2}
    assert sorted(k["kind"] for k in _kids(c, page["id"])) == ["picture", "text_box"]


def test_import_then_export_keeps_the_file(guest):
    """A Gamma export imported back into the workspace keeps the picture's
    own file (a GIF keeps its animation so), strips the original, and the
    export writes the picture again."""
    gif = io.BytesIO()
    frames = [_quads(), _quads().transpose(Image.Transpose.FLIP_LEFT_RIGHT)]
    frames[0].save(gif, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    url = _upload_image(guest, gif.getvalue(), "anim.gif", "image/gif")
    source, _doc = _upload_pdf(guest, _pdf(90), "Picture source")
    _put_children(guest, source["id"], [_placed("picRound", url, content="Round *trip*", angle=12.5)])
    exported = guest.get(f"/api/pages/{source['id']}/export-pdf").content
    page, doc_id = _upload_pdf(guest, exported, "Picture import")
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id, "strip": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "found": 1, "imported": 1, "stripped": 1}
    [block] = _kids(guest, page["id"])
    props = block["properties"]
    assert block["content"] == "Round *trip*" and props["pdf_page"] == 1
    assert props["picture"] == picture.normalize_picture({"url": url, "x": 100, "y": 100, "w": 120, "h": 60,
                                                          "angle": 12.5})
    assert props["imported_annot"].startswith("1:/Stamp:") and props["annot_stripped"] is True
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id})
    assert r.json()["imported"] == 0                                 # idempotent
    r = guest.get(f"/api/pages/{page['id']}/export-pdf")
    assert r.headers["x-annotations-written"] == "1" and len(_stamps(r.content)) == 1


def test_a_sheets_pictures_are_drawn_in_the_notebook_pdf(guest):
    url = _upload_image(guest, _file(_quads()))
    page = guest.post("/api/pages", json={"title": "Picture notebook"}).json()["id"]
    _put_children(guest, page, [{"id": "picNb", "content": "", "properties": {
        "sheet": notebook.normalize_paper({"width": 300, "height": 300})}, "children": [
        {"id": "picNbPic", "content": "", "children": [],
         "properties": {"picture": {"url": url, "x": 50, "y": 50, "w": 200, "h": 100}}}]}])
    r = guest.get(f"/api/pages/{page}/export-pdf")
    assert r.status_code == 200, r.text
    assert r.headers["x-annotations-written"] == "1"
    pixel = _raster(r.content)
    assert _near(pixel(100, 70), RED) and _near(pixel(200, 130), YELLOW) and pixel(20, 20) == WHITE


def test_the_agents_pictures_of_a_page_show_its_pictures(guest):
    """What the agent and the chat see of a page with its handwriting is
    the page as the user sees it: the PDF page with its pictures drawn
    (``page-image`` with ``ink=1``; the bare page without), and a sheet with
    its pictures (view_ink's picture of the sheet)."""
    def at(response, x, y, w, h):
        assert response.status_code == 200, response.text
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
        return image.getpixel((int(x / w * image.width), int(y / h * image.height)))

    url = _upload_image(guest, _file(_quads()))
    page, _doc = _upload_pdf(guest, _pdf(), "Picture for the agent")
    _put_children(guest, page["id"], [_placed("picAi", url, w=200, h=100)])
    assert _near(at(guest.get(f"/api/ai/page-image/{page['id']}?page=1&ink=1"), 150, 125, 612, 792), RED)
    assert at(guest.get(f"/api/ai/page-image/{page['id']}?page=1"), 150, 125, 612, 792) == WHITE
    sheet = guest.post("/api/pages", json={"title": "Picture sheet for the agent"}).json()["id"]
    _put_children(guest, sheet, [{"id": "picAiSheet", "content": "", "properties": {
        "sheet": notebook.normalize_paper({"width": 300, "height": 300})}, "children": [
        {"id": "picAiOnSheet", "content": "", "children": [],
         "properties": {"picture": {"url": url, "x": 50, "y": 50, "w": 200, "h": 100}}}]}])
    assert _near(at(guest.get("/api/ai/ink-image/picAiSheet?area=page"), 200, 130, 300, 300), YELLOW)


# --- the text exports ---------------------------------------------------------------------------


URL = "/api/uploads/3f2a9c.png"


def _tree():
    """A page with a picture on PDF page 2, one on a sheet whatever its
    pdf_page says, and one on no page."""
    def node(bid, content="", props=None, children=()):
        return {"id": bid, "content": content, "properties": props or {}, "position": bid,
                "children": list(children)}
    return node("page", "Pictures", {}, [
        node("p1", "Fig. [1] | the *setup*", {"picture": {"url": URL}, "pdf_page": 2},
             [node("n1", "about the figure")]),
        node("s", "", {"sheet": {}}, [node("p2", "", {"picture": {"url": URL}, "pdf_page": 5})]),
        node("p3", "loose", {"picture": {"url": URL}}),
    ])


def test_the_markdown_export_writes_the_image_line_then_the_page():
    md = render_readable(_tree())
    assert "- ![Fig. 1 the setup](/api/uploads/3f2a9c.png)\n  `p.2`\n  - about the figure" in md
    assert "- ![](/api/uploads/3f2a9c.png)\n" in md and "p.5" not in md
    assert "- ![loose](/api/uploads/3f2a9c.png)\n" in md
    assert "3f2a9c.png" in render_readable(_tree(), highlights=False)
    assert "3f2a9c.png" not in render_readable(_tree(), notes=False)


def test_the_obsidian_export_writes_the_image_line_then_the_page_link():
    md = render_vault_page(_tree(), VaultContext(None))
    assert "![Fig. 1 the setup](/api/uploads/3f2a9c.png)\n\np. 2\n\n- about the figure\n" in md
    assert "\n![](/api/uploads/3f2a9c.png)\n" in md and "p. 5" not in md


def test_the_logseq_graph_writes_the_image_line_with_no_page():
    lines = []
    for child in _tree()["children"]:
        _render_block(child, 0, lines, has_pdf=False)
    assert lines[:2] == ["- ![Fig. 1 the setup](/api/uploads/3f2a9c.png)", "\t- about the figure"]
    assert "- ![](/api/uploads/3f2a9c.png)" in lines and not any("p.2" in line for line in lines)


def test_a_zotero_note_embeds_the_picture_with_its_page_and_caption():
    """The caption is the image's alt and, for a reader in Zotero, the line
    under it."""
    html = note_html(_tree()["children"][0], resolve_image=lambda name: ("image/png", "QUJD"))
    assert html == ('<p><strong>Picture on p.2</strong></p>'
                    '<p><img src="data:image/png;base64,QUJD" alt="Fig. 1 the setup"/><br/>'
                    "Fig. [1] | the <em>setup</em></p><ul><li>about the figure</li></ul>")


def test_a_picture_under_a_highlight_is_its_own_stamp():
    kids = {"h": [{"id": "p", "content": "on the page", "properties": {"picture": {"url": URL}, "pdf_page": 1}},
                  {"id": "s", "content": "Page 1", "properties": {"sheet": {}}}],
            "s": [{"id": "q", "content": "", "properties": {"picture": {"url": URL}, "pdf_page": 1}}]}
    assert highlight_note_text({"id": "h", "content": ""}, kids) == "- Page 1\n  - ![](/api/uploads/3f2a9c.png)"


def test_the_markdown_and_obsidian_zips_bundle_the_picture(guest):
    data = _file(_quads())
    url = _upload_image(guest, data)
    page = guest.post("/api/pages", json={"title": "Picture bundle"}).json()["id"]
    _put_children(guest, page, [_placed("picMd", url, content="bundled")])
    name = url.rsplit("/", 1)[1]
    for mode, folder_name in (("readable", "assets"), ("obsidian", "attachments"), ("logseq-graph", "assets")):
        r = guest.get(f"/api/pages/{page}/export?mode={mode}")
        assert r.status_code == 200, r.text
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            assert z.read(f"{folder_name}/{name}") == data, mode
            text = "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".md"))
        assert f"({'../' if mode == 'logseq-graph' else ''}{folder_name}/{name})" in text, mode


def test_the_zotero_export_carries_the_picture_in_the_copy_and_as_an_attachment(guest):
    url = _upload_image(guest, _file(_quads()))
    name = url.rsplit("/", 1)[1]
    page, _doc = _upload_pdf(guest, _pdf(), "Picture zotero")
    _put_children(guest, page["id"], [_placed("picZ", url, content="in the copy"),
                                      {**_placed("picZNoted", url, content="with a note", y=300), "children": [
                                          {"id": "picZNote", "content": "the thought", "children": [],
                                           "properties": {}}]}])
    r = guest.get(f"/api/pages/{page['id']}/export?mode=zotero-rdf")
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        rdf = next(z.read(n).decode() for n in z.namelist() if n.endswith(".rdf"))
        pdf = next(z.read(n) for n in z.namelist() if n.endswith(".pdf"))
        assert any(n.endswith(f"/{name}") for n in z.namelist())        # an item attachment
    assert len(_stamps(pdf)) == 2
    assert "with a note" in rdf and "the thought" in rdf and "in the copy" not in rdf


def test_a_zotero_export_brought_back_makes_no_chip_of_a_picture():
    """The attachment the Zotero export makes of a picture's file is the
    picture's, so the export brought back adds no "Attachments" chip for
    it: not into the page it came from, which shows the picture, nor into a
    page it makes, in this library or another, whose picture the copy's
    stamp brings in with that file."""
    from test_zotero_import import _attachments_of, _library, _send

    c, _ = _library()
    data = _file(_quads())
    url = _upload_image(c, data)
    page, _doc = _upload_pdf(c, _pdf(), "Picture brought back")
    _put_children(c, page["id"], [_placed("picBack", url, content="Figure 1")])
    exported = c.get(f"/api/pages/{page['id']}/export?mode=zotero-rdf")
    assert exported.status_code == 200, exported.text

    def brought_back(client, created):
        d = _send(client, exported.content).json()
        assert (d["pages_created"], d["pages_merged"], d["attachments_imported"], d["skipped"]) == (
            created, 1 - created, 0, []), d
        [made] = d["pages"]
        [pic] = [k for k in _kids(client, made["id"]) if k["properties"].get("picture")]
        assert pic["content"] == "Figure 1" and pic["properties"]["picture"]["url"] == url
        assert _attachments_of(client, made["id"]) == (None, [])
        return made["id"]

    assert brought_back(c, 0) == page["id"]
    assert c.delete(f"/api/blocks/{page['id']}").status_code == 200
    brought_back(c, 1)
    other, _ = _library()
    brought_back(other, 1)
    brought_back(other, 0)                                            # and again: nothing more
    assert other.get(url).content == data


def test_the_notes_pdf_draws_the_picture_with_its_page_line(tmp_path):
    uploads, url = _stored(tmp_path, _file(_quads()))
    tree = {"id": "page", "content": "Notes", "properties": {}, "children": [
        {"id": "p", "content": "the caption", "properties": {"picture": {"url": url, "w": 160, "h": 80},
                                                             "pdf_page": 3}, "children": []}]}
    pdf = render_document([tree], uploads_dir=uploads)
    text = _page_text(pdf)
    assert "picture, p. 3" in text and "the caption" in text
    assert text.index("picture, p. 3") < text.index("the caption")
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        [info] = doc[0].get_image_info()
        assert info["bbox"][2] - info["bbox"][0] == pytest.approx(160, abs=0.5)
    finally:
        doc.close()
    assert "picture, p. 3" not in _page_text(render_document([tree], uploads_dir=uploads, notes=False))
    gone = render_document([tree], uploads_dir=tmp_path / "missing")
    assert "picture, p. 3" in _page_text(gone)                       # named even when it cannot be drawn


# --- the AI -----------------------------------------------------------------------------------


def test_the_placed_labels():
    """A picture and a text box are named by one rule, each by its kind."""
    for kind, props in (("picture", {"picture": {"url": URL}}), ("text box", {"text_box": {}})):
        on_pdf = {**props, "pdf_page": 4}
        assert placed_label(on_pdf) == f"({kind} on p. 4)"
        assert placed_label(on_pdf, on_sheet=True) == f"({kind} on a page of paper)"
        assert placed_label(on_pdf, on_sheet=True, above=True) == f"({kind} on the page of paper above)"
        assert placed_label(props) == f"({kind}, not placed on a page)"
    assert placed_label({"text_box": {}, "picture": {"url": URL}}) == "(text box, not placed on a page)"
    assert placed_label({"ink_url": "x", "picture": {"url": URL}}) == "" and placed_label({}) == ""


def _block(c, parent, content, properties):
    r = c.post("/api/blocks", json={"parent_id": parent, "content": content, "properties": properties})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_the_agent_reads_and_looks_at_a_picture(notes):  # noqa: F811
    c, ids = notes
    scope = folder(ids["sandbox"])
    url = _upload_image(c, _file(_quads()))
    page = _block(c, "root", "pictures playground", {"folders": [ids["sandbox"]]})
    placed = {"url": url, "x": 10, "y": 10, "w": 160, "h": 80, "angle": 90, "flip_h": True,
              "crop": {"x": 0.5, "w": 0.5}}
    on_pdf = _block(c, page, "the [setup]", {"picture": placed, "pdf_page": 3})
    sheet = _block(c, page, "", {"sheet": {}})
    on_sheet = _block(c, sheet, "", {"picture": {"url": url}})
    text, _ = run_agent_tool(ids["ws"], scope, "read_block", {"block_id": page})
    assert f"[{on_pdf}] (picture on p. 3) ![the setup]({url})" in text
    assert f"[{on_sheet}] (picture on the page of paper above) ![]({url})" in text
    text, chip = run_agent_tool(ids["ws"], scope, "view_image", {"block_id": on_pdf})
    assert f'Picture 1 of 1, alt text "the [setup]", 80×40 px, {url}: attached.' in text
    assert ("This is the whole file; the page shows it cropped to 50%–100% across and 0%–100% down, "
            "turned 90° clockwise, flipped left to right.") in text
    assert chip["kind"] == "image" and len(chip["images"]) == 1
    text, _ = run_agent_tool(ids["ws"], scope, "view_image", {"block_id": on_sheet})
    assert "Picture 1 of 1, 80×40 px" in text and "whole file" not in text
    c.delete(f"/api/blocks/{page}")


def test_the_page_notes_and_the_focus_name_a_picture_by_its_place(notes):  # noqa: F811
    from gamma.ai_context import notes_focus_section

    c, ids = notes
    page = _block(c, "root", "pictures in focus", {"folders": [ids["sandbox"]]})
    on_pdf = _block(c, page, "", {"picture": {"url": URL}, "pdf_page": 2})
    loose = _block(c, page, "a loose one", {"picture": {"url": URL}})
    text, _ = run_agent_tool(ids["ws"], folder(ids["sandbox"]), "read_page", {"page_id": page})
    assert f"User's notes:\n- (picture on p. 2) ![]({URL})\n- (picture, not placed on a page) ![a loose one]({URL})" \
        in text
    section = notes_focus_section(ids["ws"], SimpleNamespace(focus_block_id=on_pdf, page_id=page, pages=[],
                                                             context_blocks=[loose]))
    assert f"- [{on_pdf}] (picture on p. 2) ![]({URL})" in section
    assert f"- [{loose}] (picture, not placed on a page) ![a loose one]({URL})" in section
    c.delete(f"/api/blocks/{page}")


def test_move_block_keeps_pictures_on_their_page(notes):  # noqa: F811
    c, ids = notes
    scope = folder(ids["sandbox"])
    page = _block(c, "root", "pictures to move", {"folders": [ids["sandbox"]]})
    other = _block(c, "root", "another picture page", {"folders": [ids["sandbox"]]})
    on_pdf = _block(c, page, "", {"picture": {"url": URL}, "pdf_page": 1})
    sheet = _block(c, page, "", {"sheet": {}})
    _block(c, sheet, "", {"picture": {"url": URL}})
    text, _ = run_agent_tool(ids["ws"], scope, "move_block", {"block_id": on_pdf, "parent_id": other})
    assert text.startswith("error: pictures are placed on their page"), text
    text, _ = run_agent_tool(ids["ws"], scope, "move_block", {"block_id": sheet, "parent_id": other})
    assert text.startswith("ok"), text
    c.delete(f"/api/blocks/{page}")
    c.delete(f"/api/blocks/{other}")
