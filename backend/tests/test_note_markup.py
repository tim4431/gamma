"""LaTeX → drawable spans (the fallback when the math renderer is out), and
uploads → PDF image XObjects."""

import io
import zlib


from conftest import require_math_renderer
from gamma.note_markup import MATH, SUB, SUP, SYMBOLS, TEXT, latex_spans
from gamma.pdf_image import image_xobject
from gamma.pdf_typeset import CID, font_of


def flat(spans):
    """Spans → the text they draw; inline math shows as ⟪tex⟫."""
    return "".join(f"⟪{p}⟫" if k == MATH else p for k, p, _ in spans)


def levels(spans, level):
    return [p for k, p, lv in spans if k == TEXT and lv == level]


def test_every_symbol_is_drawable():
    """A LaTeX command that maps to a character none of the PDF fonts carry
    would print as a blank box — the mapping must stay in sync with
    pdf_typeset.SYMBOL."""
    undrawable = {name: ch for name, ch in SYMBOLS.items()
                  if any(font_of(c) == CID for c in ch)}
    assert undrawable == {}


def test_latex_becomes_symbols_and_scripts():
    spans = latex_spans(r"F_1=\frac{1}{2}+\sum_{i=1}^N \sin\left( nx \right)^2")
    assert flat(spans) == "F1=1/2+∑i=1N sin( nx )2"
    # The parts that must not be drawn on the baseline.
    assert levels(spans, SUP) == ["N", "2"]
    assert levels(spans, SUB) == ["1", "i=1"]

    assert flat(latex_spans(r"\ket{\phi_j}")) == "|φj〉"
    assert flat(latex_spans(r"\nabla g \geq \alpha \cdot \beta")) == "∇ g ≥ α ⋅ β"
    assert flat(latex_spans(r"\sqrt{2}\times\mathbf{A}")) == "√2×A"
    # An unknown command keeps its name — that is what \sin, \log, \max need.
    assert flat(latex_spans(r"\argmax_x")) == "argmaxx"


def _png(width, height, ctype, depth=8, filt=0):
    chans = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    stride = width * chans * depth // 8
    raw = b"".join(bytes([filt]) + bytes(range(1, stride + 1)) for _ in range(height))

    def chunk(tag, data):
        body = tag + data
        import struct
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    import struct
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def test_png_rgb_is_embedded_verbatim(tmp_path):
    """8-bit RGB needs no decoding: PDF's /Predictor 15 is PNG row filtering."""
    path = tmp_path / "a.png"
    path.write_bytes(_png(4, 3, ctype=2))
    stream, w, h = image_xobject(path)
    assert (w, h) == (4, 3)
    assert str(stream["/Filter"]) == "/FlateDecode"
    assert int(stream["/DecodeParms"]["/Predictor"]) == 15
    assert str(stream["/ColorSpace"]) == "/DeviceRGB"


def _chunk(tag, data):
    import struct
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def _unpredicted(stream):
    """A gray or RGB /Predictor 15 stream's samples, read back by Pillow
    from the PNG its data is the image data of."""
    import struct

    from PIL import Image

    w, h = int(stream["/Width"]), int(stream["/Height"])
    ctype = 2 if str(stream["/ColorSpace"]) == "/DeviceRGB" else 0
    png = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
           + _chunk(b"IDAT", stream.get_data()) + _chunk(b"IEND", b""))
    return Image.open(io.BytesIO(png)).tobytes()


def test_png_with_alpha_keeps_it_as_a_soft_mask(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(_png(2, 2, ctype=6))
    stream, w, h = image_xobject(path)
    assert (w, h) == (2, 2)
    assert str(stream["/ColorSpace"]) == "/DeviceRGB"
    mask = stream["/SMask"]
    assert str(mask["/ColorSpace"]) == "/DeviceGray"
    # Re-encoded as 8-bit PNG data, so both go in as /Predictor 15 streams.
    assert int(stream["/DecodeParms"]["/Predictor"]) == 15
    assert _unpredicted(mask) == bytes([4, 8]) * 2            # each pixel's alpha, as stored
    assert _unpredicted(stream) == bytes([1, 2, 3, 5, 6, 7]) * 2

    from PIL import Image
    Image.new("RGBA", (3, 2), (10, 20, 30, 255)).save(path)
    stream, _, _ = image_xobject(path)
    assert "/SMask" not in stream, "an alpha channel that is opaque everywhere needs no mask"


def test_a_large_png_embeds_verbatim(tmp_path):
    """The verbatim path costs nothing at any size: a 12 MP RGB PNG goes in
    as its own IDAT bytes."""
    from PIL import Image

    from gamma.pdf_image import _png_chunks

    path = tmp_path / "big.png"
    Image.new("RGB", (4000, 3000), (10, 120, 200)).save(path)
    stream, w, h = image_xobject(path)
    assert (w, h) == (4000, 3000)
    assert int(stream["/DecodeParms"]["/Predictor"]) == 15
    assert stream.get_data() == b"".join(b for tag, b in _png_chunks(path.read_bytes()) if tag == b"IDAT")


def _drawn_over_blue(path, scale):
    """The picture at ``path`` through ``XObjectStore``, drawn to fill a page
    painted blue, rendered back by PyMuPDF."""
    import pymupdf
    from PyPDF2 import PdfWriter
    from PyPDF2.generic import DecodedStreamObject, DictionaryObject, IndirectObject, NameObject

    from gamma.pdf_image import XObjectStore

    writer = PdfWriter()
    store = XObjectStore(writer, path.parent)
    name, w, h = store.resolve(f"/api/uploads/{path.name}")
    mask = store.refs[name].get_object().get("/SMask")
    assert mask is None or isinstance(mask, IndirectObject), "a stream is only valid as an indirect object"
    writer.add_blank_page(w, h)
    page = writer.pages[0]                       # the writer's copy, not the one add_blank_page returns
    content = DecodedStreamObject()
    content.set_data(b"0 0 1 rg 0 0 %d %d re f %d 0 0 %d 0 0 cm /%s Do" % (w, h, w, h, name.encode()))
    page[NameObject("/Contents")] = writer._add_object(content)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({
        NameObject(f"/{name}"): store.refs[name]})})
    out = io.BytesIO()
    writer.write(out)
    doc = pymupdf.open(stream=out.getvalue(), filetype="pdf")
    try:
        return doc[0].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).pil_image().convert("RGB")
    finally:
        doc.close()


def test_a_large_screenshot_with_alpha_embeds_with_its_transparency(tmp_path):
    """A pasted screenshot is RGBA and often past the old 2.5 MP cap: a
    6 MP one embeds, and what is under it shows through where it is
    transparent (a note box's tint), not a white rectangle."""
    from PIL import Image

    path = tmp_path / "ab12.png"
    image = Image.new("RGBA", (3000, 2000), (0, 0, 0, 0))   # transparent right third
    image.paste((220, 30, 30, 255), (0, 0, 1000, 2000))      # opaque red
    image.paste((30, 200, 30, 128), (1000, 0, 2000, 2000))   # half-transparent green
    image.save(path)

    shown = _drawn_over_blue(path, scale=0.1)                # 300×200 px
    def near(px, want):
        return all(abs(a - b) <= 4 for a, b in zip(px, want))
    assert near(shown.getpixel((50, 100)), (220, 30, 30))
    assert near(shown.getpixel((150, 100)), (15, 100, 142))  # half green, half the blue page
    assert near(shown.getpixel((250, 100)), (0, 0, 255))


def test_the_decoded_path_takes_what_the_verbatim_one_cannot(tmp_path):
    """16-bit samples are scaled (not clipped), a palette's tRNS entry
    becomes a soft mask, and interlaced PNGs, GIF and WebP embed; an SVG and
    a corrupt file are left for the alt text."""
    import struct

    from PIL import Image

    def png(ctype, depth, rows, extra=b"", interlace=0):
        """A 2×1 PNG; ``rows`` its filter-0 image data."""
        return (b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, depth, ctype, 0, 0, interlace)) + extra
                + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b""))

    path = tmp_path / "a.png"
    path.write_bytes(png(0, 16, b"\x00" + struct.pack(">HH", 0x8000, 0xFFFF)))
    assert _unpredicted(image_xobject(path)[0]) == bytes([127, 255])

    path.write_bytes(png(3, 8, b"\x00\x00\x01",
                         _chunk(b"PLTE", bytes([255, 0, 0, 0, 0, 255])) + _chunk(b"tRNS", b"\x00")))
    stream, _, _ = image_xobject(path)
    assert _unpredicted(stream) == bytes([255, 0, 0, 0, 0, 255])
    assert _unpredicted(stream["/SMask"]) == bytes([0, 255])

    # Adam7 puts a 2×1 image's pixels in passes 1 and 6, a row each.
    path.write_bytes(png(0, 8, b"\x00\x0a" + b"\x00\x14", interlace=1))
    assert _unpredicted(image_xobject(path)[0]) == bytes([10, 20])

    for name, kwargs in (("a.gif", {}), ("a.webp", {"lossless": True})):
        Image.new("RGB", (5, 4), (10, 120, 200)).save(tmp_path / name, **kwargs)
        assert image_xobject(tmp_path / name)[1:] == (5, 4), name

    (tmp_path / "a.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    assert image_xobject(tmp_path / "a.svg") is None
    path.write_bytes(png(6, 8, b"")[:33] + _chunk(b"IDAT", b"not zlib") + _chunk(b"IEND", b""))
    assert image_xobject(path) is None


def test_unsupported_image_is_skipped(tmp_path):
    path = tmp_path / "a.gif"
    path.write_bytes(b"GIF89a nope")
    assert image_xobject(path) is None


def _palette_png(width, height, palette):
    import struct

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(bytes([0]) + bytes(range(width)) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", palette)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def test_palette_survives_as_raw_bytes(tmp_path):
    """The /Indexed lookup table must be a byte string. As a *text* string
    PyPDF2 re-encodes it (UTF-16 as soon as a byte isn't PDFDocEncodable),
    which scrambles the palette and paints the picture one flat colour."""
    from PyPDF2.generic import ByteStringObject

    palette = bytes([255, 0, 0, 0, 200, 0, 32, 64, 255, 250, 250, 180])
    path = tmp_path / "p.png"
    path.write_bytes(_palette_png(4, 2, palette))
    stream, w, h = image_xobject(path)
    space = stream["/ColorSpace"]
    assert str(space[0]) == "/Indexed" and int(space[2]) == 3
    assert isinstance(space[3], ByteStringObject)
    assert bytes(space[3]) == palette


def test_boxed_math_is_stroked_not_filled():
    """`\\boxed{}` is a stroked, unfilled rect. Painting it solid (ignoring the
    SVG's fill/stroke) turned every boxed equation into a black slab."""
    import xml.etree.ElementTree as ET

    from gamma.vector_text import _shape_ops, math

    boxed = math(r"\boxed{w = \frac{a}{b}}", 8)
    plain = math(r"w = \frac{a}{b}", 8)
    if boxed is None or plain is None:
        require_math_renderer()  # fails with the cause, never a silent skip
    assert b"re\nS" in boxed[0].shapes, "the box outline must be stroked"
    assert b"re\nS" not in plain[0].shapes, "…and only where there is a box"
    assert b"re\nf" in plain[0].shapes, "the fraction bar stays a filled rect"
    # The letters are glyph placements, never part of the shapes.
    assert {p.char for p in plain[0].glyphs} == {"w", "=", "a", "b"}

    # The fill/stroke attributes drive it, not the element type.
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -8 10 10">'
           '<rect x="0" y="-8" width="10" height="10" fill="none" stroke="black"'
           ' stroke-width="0.5"/></svg>')
    assert b"0.5 w" in _shape_ops(ET.fromstring(svg))


def test_vector_text_refuses_rescaled_svg():
    """A <symbol>/<use> or a group transform rescales what follows; drawing
    those children as-is is silently wrong (ziafont's default output makes
    glyphs ~1.5x too big), so the shape converter must refuse them — as it
    does an <ellipse>, which it can't draw."""
    import xml.etree.ElementTree as ET

    from gamma.vector_text import _shape_ops

    flat = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -8 10 10">'
            '<path d="M 0 0 L 5 0 L 5 -5 Z"/></svg>')
    assert _shape_ops(ET.fromstring(flat)) is not None
    for bad in ('<symbol id="g" viewBox="0 -20 20 20"><path d="M 0 0 L 5 0 Z"/></symbol>',
                '<g transform="scale(2)"><path d="M 0 0 L 5 0 Z"/></g>',
                '<ellipse cx="1" cy="1" rx="2" ry="1"/>'):
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -8 10 10">{bad}</svg>'
        assert _shape_ops(ET.fromstring(svg)) is None
