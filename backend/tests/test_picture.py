"""Pictures on a page (gamma/picture.py, docs/dev/pictures.md): what makes a
block a picture and which page it is on, the caption as text and as the
Markdown line the exports print (more cases in tests/shared/picture.json
`markdown`), and a picture file's size read from its header alone. Normalization, keeping on a surface, the turned frame and
the merge are pinned by tests/shared/picture.json and picturemerge.json
(test_shared_fixtures.py)."""

import io
import re
import struct
import time
import zlib

import pytest
from PIL import Image, ImageFile

from gamma import picture
from gamma.picture import caption_text, image_size, is_picture, markdown_of, picture_page
from gamma.text_box import escape_markdown

URL = "/api/uploads/3f2a9c.png"


def _file(fmt, size=(40, 20), orientation=None, mode="RGB"):
    """A small picture file made by Pillow, with an EXIF orientation when
    one is given."""
    buf = io.BytesIO()
    kw = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kw["exif"] = exif.tobytes()
    Image.new(mode, size, "red").save(buf, fmt, **kw)
    return buf.getvalue()


def _png_header(width, height):
    """A PNG whose header declares ``width`` × ``height`` and whose image
    data is next to nothing, as a decompression bomb's is."""
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))


def _png_exif_after_idat(orientation):
    """A PNG with an eXIf chunk after its image data, where Chromium and
    Firefox no longer look for one."""
    data = _file("PNG")
    exif = Image.Exif()
    exif[0x0112] = orientation
    body = exif.tobytes()
    body = body[6:] if body[:6] == b"Exif\x00\x00" else body
    chunk = struct.pack(">I", len(body)) + b"eXIf" + body + struct.pack(">I", zlib.crc32(b"eXIf" + body))
    end = data.rindex(b"IEND") - 4
    return data[:end] + chunk + data[end:]


def _jpeg_declaring(width, height):
    """A small JPEG with its frame header (SOF0) rewritten to declare
    ``width`` × ``height``."""
    data = bytearray(_file("JPEG"))
    sof = data.index(b"\xff\xc0")
    data[sof + 5:sof + 9] = struct.pack(">HH", height, width)
    return bytes(data)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "WEBP"])
def test_the_size_of_each_kind_of_picture(fmt):
    assert image_size(_file(fmt, mode="P" if fmt == "GIF" else "RGB")) == (40, 20)


@pytest.mark.parametrize("fmt", ["JPEG", "PNG"])
@pytest.mark.parametrize("orientation, shown", [(1, (40, 20)), (3, (40, 20)), (5, (20, 40)), (6, (20, 40)),
                                                (8, (20, 40))])
def test_an_exif_quarter_turn_swaps_the_sides(fmt, orientation, shown):
    # as the browser shows it: orientations 5 to 8 turn the picture a quarter
    assert image_size(_file(fmt, orientation=orientation)) == shown


@pytest.mark.parametrize("orientation", [1, 3, 5, 6, 7, 8])
def test_exif_the_browser_ignores_keeps_the_stored_size(orientation):
    # the rule is pdf_image.browser_orientation: Chromium and Firefox show
    # a WebP and a PNG whose eXIf comes after its image data as stored
    assert image_size(_file("WEBP", orientation=orientation)) == (40, 20)
    assert image_size(_png_exif_after_idat(orientation)) == (40, 20)


@pytest.mark.parametrize("data", [
    b"", b"not a picture", b"\x89PNG\r\n\x1a\n" + b"garbage" * 10, b"\xff\xd8" + b"\x00" * 50, b"GIF89a",
    b"RIFF\x00\x00\x00\x00WEBPVP8 ", b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',
], ids=["empty", "text", "png garbage", "jpeg garbage", "gif header only", "webp garbage", "svg"])
def test_what_is_no_picture_has_no_size(data):
    assert image_size(data) is None


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "WEBP"])
def test_a_truncated_header_has_no_size(fmt):
    data = _file(fmt, mode="P" if fmt == "GIF" else "RGB")
    assert image_size(data[:20]) is None


def test_only_the_four_formats_count():
    # Pillow reads a BMP or a TIFF, but neither is a picture on the page
    assert image_size(_file("BMP")) is None
    assert image_size(_file("TIFF")) is None


def test_nothing_is_decoded(monkeypatch):
    files = {fmt: _file(fmt, orientation=None if fmt == "GIF" else 6, mode="P" if fmt == "GIF" else "RGB")
             for fmt in ("PNG", "JPEG", "GIF", "WEBP")}

    def refuse(self, *args, **kwargs):
        raise AssertionError("decoded")
    monkeypatch.setattr(ImageFile.ImageFile, "load", refuse)
    monkeypatch.setattr(Image.Image, "load", refuse)
    assert {fmt: image_size(data) for fmt, data in files.items()} == {
        "PNG": (20, 40), "JPEG": (20, 40), "GIF": (40, 20), "WEBP": (40, 20)}


def test_a_huge_declared_size_answers_at_once():
    # Image.open would refuse these as decompression bombs; nothing is
    # decoded here, so the size is read as declared
    with pytest.raises(Image.DecompressionBombError):
        Image.open(io.BytesIO(_png_header(100_000, 100_000)))
    start = time.monotonic()
    assert image_size(_png_header(100_000, 100_000)) == (100_000, 100_000)
    assert image_size(_jpeg_declaring(60_000, 50_000)) == (60_000, 50_000)
    assert time.monotonic() - start < 1


def test_what_is_a_picture_block():
    pic = {"url": URL}
    assert is_picture({"picture": pic})
    assert is_picture({"picture": {}}), "a picture whose file is missing is still one"
    assert is_picture({"picture": {"url": "/api/uploads/a.svg"}})
    assert is_picture({"picture": pic, "text_box": "no"}), "a text_box that is no object"
    assert not is_picture({"picture": pic, "ink_url": "/api/uploads/a.ink"}), "ink first"
    assert not is_picture({"picture": pic, "ink_url": None}), "an ink_url key, whatever its value"
    assert not is_picture({"picture": pic, "text_box": {}}), "a text box next"
    for value in (None, "pic", 3, True, [pic]):
        assert not is_picture({"picture": value}), value
    for props in (None, {}, "picture", [{"picture": pic}]):
        assert not is_picture(props), props


def test_the_page_a_picture_is_on():
    props = {"picture": {"url": URL}, "pdf_page": 3}
    assert picture_page(props) == 3
    assert picture_page(props, on_sheet=True) is None, "the nearest sheet wins"
    for page in (0, -1, True, "3", 3.0, None):
        assert picture_page({**props, "pdf_page": page}) is None, page
    assert picture_page({"picture": {"url": URL}}) is None
    assert picture_page({"pdf_page": 3}) is None, "no picture"
    assert picture_page({**props, "text_box": {}}) is None, "a text box carrying a picture"
    assert picture_page({**props, "ink_url": "/api/uploads/a.ink"}) is None
    assert picture_page(None) is None


def test_the_caption_on_one_line():
    assert caption_text("  A cat\n\ton  the\r\nmat  ") == "A cat on the mat"
    assert caption_text("") == ""
    assert caption_text(None) == ""
    assert caption_text(" \n ") == ""


def test_the_markdown_line_of_a_picture():
    pic = picture.normalize_picture({"url": URL, "x": 10})
    assert markdown_of("A cat", pic) == f"![A cat]({URL})"
    assert markdown_of("", pic) == f"![]({URL})"
    assert markdown_of(None, pic) == f"![]({URL})"
    # brackets and pipes would end the alt text or read as an Obsidian size
    assert markdown_of("A [cat] | 300", pic) == f"![A cat 300]({URL})"
    assert markdown_of("[]|", pic) == f"![]({URL})"
    # the caption is Markdown: its marks are read, the words they show
    # escaped once for the alt text, the start of the line too, on one line
    assert markdown_of("*bold* _x_ `c` $m$ <b>", pic) == rf"![bold x c \$m\$ \<b>]({URL})"
    assert markdown_of("# Figure 1", pic) == rf"![Figure 1]({URL})"
    assert markdown_of("1\\. first\n  second", pic) == rf"![1\. first second]({URL})"
    assert markdown_of("A cat", {"url": URL}) == f"![A cat]({URL})", "a stored value is read as normalized"


def _alt_text(line: str) -> str:
    """The alt text a CommonMark reader shows for an ``![alt](url)`` line
    whose alt text holds no marks but escapes."""
    return re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", line[2:line.rindex("](")])


@pytest.mark.parametrize("caption", ["my_photo_2024.jpg", "A cat on the mat", "*starred* and 1. numbered", "# 5 > 3 + 2",
                                     "costs $5 <now>", "C:\\photos\\cat.png", "a_b*c`d~e=f&g;"])
def test_an_imported_caption_reads_as_it_came(caption):
    # the imports store a caption that is text as escaped Markdown
    # (notability_map.picture_block, the PDF stamp import): the line's alt
    # text shows it as it came, no backslash and no mark more
    assert _alt_text(markdown_of(escape_markdown(caption), {"url": URL})) == caption


def test_no_markdown_without_a_picture():
    assert markdown_of("A cat", None) == ""
    assert markdown_of("A cat", {"url": "/api/uploads/a.svg"}) == ""
    assert markdown_of("A cat", "picture") == ""
