"""Uploaded pictures → PDF image XObjects.

Notes can contain ``![](/api/uploads/…)`` refs, and an exported note that
shows the URL instead of the picture is useless. Two paths:

* Verbatim, at any size and no decoding cost: JPEG as ``/DCTDecode`` (only the
  SOF header is parsed for the size and component count), and 8-bit
  gray/RGB/palette PNG without transparency as ``/FlateDecode`` with
  ``/Predictor 15``, which is exactly PNG's row filtering.
* Decoded with Pillow: everything else a browser draws (``BROWSER_FORMATS``).
  That is a PNG with an alpha channel or a ``tRNS`` chunk (the common case
  for pasted screenshots), with 16-bit or sub-byte samples, or interlaced,
  and GIF and WebP. The colour is re-encoded as an 8-bit PNG and embedded
  the verbatim way, and transparency becomes a soft mask (``/SMask``), so
  what is under the picture shows through, as in the app: a text box's
  tint, not a white rectangle. A 25 MP screenshot takes well under a
  second; memory, about 8 bytes a pixel while it is decoded, is what
  ``MAX_DECODED_PIXELS`` bounds.

A picture is shown as the browser shows it, its EXIF orientation applied
where Chromium and Firefox apply one (``browser_orientation``). The decoded
path turns the pixels themselves. A verbatim JPEG or PNG keeps its bytes:
it is wrapped in a form XObject that draws it through the orientation in
the unit square an image fills, so every writer draws it as it draws any
image, at the size as shown.

A picture that can't be embedded (an SVG, a corrupt file) is logged with the
reason, and the writers draw its alt text instead.

``image_file`` is the way back, for the import of a picture embedded in a
PDF: an image XObject as a file to store. The PDF is untrusted, so nothing
is read from it without a bound: a stream is inflated here, never past the
size its image needs (``stream_data``), each image is opened as the format
its filter names, and its pixels count against ``MAX_DECODED_PIXELS`` and
the import's ``DecodeBudget``.
"""

import io
import struct
import zlib

from PIL import Image, UnidentifiedImageError
from PyPDF2.generic import (
    ArrayObject,
    ByteStringObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    StreamObject,
)

from .logbuf import log
from .markdown_export import UPLOAD_RE

# The largest picture the decoded path takes: an 8K screen is 33 MP, and
# 40 MP is about 330 MB while decoded. The verbatim path has no cap.
MAX_DECODED_PIXELS = 40_000_000
# The most one import decodes in all (``DecodeBudget``): a PDF of a few kB
# can draw thousands of large images, each a second or so of work.
MAX_IMPORT_PIXELS = 5 * MAX_DECODED_PIXELS
# The formats an upload is opened as: the ones a browser draws. Image.open
# would try every format Pillow knows, and an EPS hands its bytes to
# Ghostscript.
BROWSER_FORMATS = ("PNG", "JPEG", "GIF", "WEBP", "BMP", "ICO", "AVIF")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_GRAY_MODES = {"1", "L", "LA", "I", "I;16"}
_EXIF_ORIENTATION = 0x0112
# How an EXIF orientation (2 to 8) shows the stored picture, as the
# ``cm`` that maps the unit square an image fills onto itself: the stored
# image's point lands where the browser shows it. A stored point (s, t),
# from the top-left as fractions, shows at (1 - s, t) for 2, (1 - s, 1 - t)
# for 3, (s, 1 - t) for 4, (t, s) for 5, (1 - t, s) for 6 (a quarter turn
# clockwise), (1 - t, 1 - s) for 7 and (t, 1 - s) for 8; the image space
# runs up from its bottom row, (s, 1 - t).
_ORIENTED = {
    2: (-1, 0, 0, 1, 1, 0), 3: (-1, 0, 0, -1, 1, 1), 4: (1, 0, 0, -1, 0, 1),
    5: (0, -1, -1, 0, 1, 1), 6: (0, -1, 1, 0, 0, 1), 7: (0, 1, 1, 0, 0, 0), 8: (0, 1, -1, 0, 1, 0),
}
# The turn that shows stored samples as an EXIF orientation says.
_TRANSPOSE = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
              4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270,
              7: Image.Transpose.TRANSVERSE, 8: Image.Transpose.ROTATE_90}


def _stream(data: bytes, width: int, height: int, colorspace, bpc: int,
            filt: str, parms=None) -> DecodedStreamObject:
    img = DecodedStreamObject()
    img.set_data(data)
    img.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(width),
        NameObject("/Height"): NumberObject(height),
        NameObject("/ColorSpace"): colorspace,
        NameObject("/BitsPerComponent"): NumberObject(bpc),
        NameObject("/Filter"): NameObject(filt),
    })
    if parms:
        img[NameObject("/DecodeParms")] = parms
    return img


def _orientation(exif: bytes | None) -> int:
    """The EXIF orientation (1 to 8) a picture's EXIF block names; 1 for
    none, or for one that cannot be read."""
    if not exif:
        return 1
    try:
        found = Image.Exif()
        found.load(exif)
        value = found.get(_EXIF_ORIENTATION, 1)
    except Exception:  # an unreadable block: the picture as stored
        return 1
    return value if value in _ORIENTED else 1


def _oriented(image, width: int, height: int, orientation: int):
    """A verbatim image whose EXIF orientation is not 1, as the browser shows
    it: a form XObject over the unit square drawing ``image`` through the
    orientation (``_ORIENTED``), and the size as shown, the sides swapped
    for a quarter turn. The image is a direct stream in the form's
    resources; ``XObjectStore`` makes it an indirect object. The private
    ``/GammaOrientation`` names the orientation for ``image_file``: a PNG's
    samples go in without the EXIF block that turns them."""
    if orientation not in _ORIENTED:
        return image, width, height
    form = DecodedStreamObject()
    form.set_data(b"q %s cm /Im Do Q" % " ".join(str(v) for v in _ORIENTED[orientation]).encode())
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject(NumberObject(v) for v in (0, 0, 1, 1)),
        NameObject("/Resources"): DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Im"): image})}),
        NameObject("/GammaOrientation"): NumberObject(orientation),
    })
    return (form, height, width) if orientation >= 5 else (form, width, height)


def _jpeg_segments(data: bytes):
    """A JPEG's marker segments before its first scan, as ``(marker, offset,
    body)``, ``offset`` where the body starts in ``data``."""
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:                  # a fill byte
            i += 1
        elif marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
        elif marker in (0xD9, 0xDA):        # the end, or the first scan: the header is over
            return
        else:
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            yield marker, i + 4, data[i + 4:i + 2 + length]
            i += 2 + length


def _jpeg_exif(data: bytes) -> tuple[int, bytes] | None:
    """A JPEG's EXIF block, the first APP1 Exif segment, as the browser and
    Pillow read it: ``(offset, body)``, or None."""
    return next(((at, body) for marker, at, body in _jpeg_segments(data)
                 if marker == 0xE1 and body[:6] == b"Exif\x00\x00"), None)


def browser_orientation(data: bytes) -> int:
    """The EXIF orientation (1 to 8) a browser shows a picture file
    through, as Chromium and Firefox apply one: a JPEG's EXIF block, a
    PNG's eXIf chunk when it comes before the image data. Any other file is
    shown as stored, and so is the EXIF of a WebP, an eXIf after a PNG's
    image data and the text-chunk profile ImageMagick writes ("Raw profile
    type exif"), which those browsers ignore and Pillow's
    ``ImageOps.exif_transpose`` would apply."""
    if data[:8] == _PNG_MAGIC:
        for tag, body in _png_chunks(data):
            if tag == b"eXIf":
                return _orientation(body)
            if tag in (b"IDAT", b"IEND"):
                break
        return 1
    if data[:2] == b"\xff\xd8":
        found = _jpeg_exif(data)
        return _orientation(found[1] if found else None)
    return 1


def _jpeg_oriented(data: bytes, orientation: int) -> bytes | None:
    """A JPEG with the orientation tag of its EXIF block set to
    ``orientation``, every other byte as it is; None when it has no such
    tag to set."""
    found = _jpeg_exif(data)
    if found is None:
        return None
    at, body = found
    tiff = body[6:]
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if order is None or len(tiff) < 8:
        return None
    ifd = struct.unpack(order + "I", tiff[4:8])[0]
    if ifd + 2 > len(tiff):
        return None
    for k in range(struct.unpack(order + "H", tiff[ifd:ifd + 2])[0]):
        entry = ifd + 2 + 12 * k
        if entry + 12 > len(tiff):
            break
        tag, kind, count = struct.unpack(order + "HHI", tiff[entry:entry + 8])
        if tag == _EXIF_ORIENTATION and kind == 3 and count == 1:  # one SHORT, held in the entry
            spot = at + 6 + entry + 8
            out = data[:spot] + struct.pack(order + "H", orientation) + data[spot + 2:]
            return out if browser_orientation(out) == orientation else None
    return None


def _jpeg(data: bytes):
    size = None
    for marker, _at, body in _jpeg_segments(data):
        if marker in _SOF and len(body) >= 6:
            size = struct.unpack(">HH", body[1:5]) + (body[5],)
            break
    if size is None:
        return None
    height, width, comps = size
    space = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(comps)
    if not space:
        return None
    return _oriented(_stream(data, width, height, NameObject(space), 8, "/DCTDecode"),
                     width, height, browser_orientation(data))


def _png_chunks(data: bytes):
    i = 8
    while i + 8 <= len(data):
        length, tag = struct.unpack(">I4s", data[i:i + 8])
        yield tag, data[i + 8:i + 8 + length]
        i += 8 + length + 4


def _flate_png(idat: bytes, width: int, height: int, space, colors: int) -> DecodedStreamObject:
    """8-bit PNG image data as it is: /Predictor 15 makes the viewer undo
    the PNG row filters."""
    parms = DictionaryObject({
        NameObject("/Predictor"): NumberObject(15),
        NameObject("/Colors"): NumberObject(colors),
        NameObject("/BitsPerComponent"): NumberObject(8),
        NameObject("/Columns"): NumberObject(width),
    })
    return _stream(idat, width, height, space, 8, "/FlateDecode", parms)


def _png(data: bytes):
    """A PNG embedded verbatim, or None when it needs the decoded path."""
    header, idat, plte = None, bytearray(), None
    for tag, body in _png_chunks(data):
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body[:13])
        elif tag == b"IDAT":
            idat += body
        elif tag == b"PLTE":
            plte = body
        elif tag == b"tRNS":
            return None             # transparency: the decoded path's soft mask
        elif tag == b"IEND":
            break
    if not header or not idat:
        return None
    width, height, depth, ctype, _comp, _filt, interlace = header
    if interlace or depth != 8 or ctype not in (0, 2, 3) or (ctype == 3 and not plte):
        return None
    if ctype == 3:
        # ByteStringObject, NOT create_string_object: a text string gets
        # re-encoded (UTF-16 once a byte isn't PDFDocEncodable), which
        # scrambles the palette and paints the image a solid colour.
        space = ArrayObject([NameObject("/Indexed"), NameObject("/DeviceRGB"),
                             NumberObject(len(plte) // 3 - 1),
                             ByteStringObject(plte)])
    else:
        space = NameObject("/DeviceGray" if ctype == 0 else "/DeviceRGB")
    return _oriented(_flate_png(bytes(idat), width, height, space, 3 if ctype == 2 else 1),
                     width, height, browser_orientation(data))


def _idat(image) -> bytes:
    """An "L" or "RGB" image's rows as a PNG stores them: filtered and deflated."""
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return b"".join(body for tag, body in _png_chunks(buf.getvalue()) if tag == b"IDAT")


def _soft_mask(image):
    """The alpha channel as an /SMask image, or None when the picture is
    opaque everywhere."""
    if "A" not in image.getbands():
        return None
    alpha = image.getchannel("A")
    if alpha.getextrema()[0] == 255:
        return None
    return _flate_png(_idat(alpha), alpha.width, alpha.height, NameObject("/DeviceGray"), 1)


def _decoded(data: bytes):
    """Any picture Pillow reads in a format the browser draws: its colour
    as 8-bit gray or RGB, turned as the browser turns it
    (``browser_orientation``), and its transparency as an /SMask."""
    image = Image.open(io.BytesIO(data), formats=BROWSER_FORMATS)
    width, height = image.size              # read from the header, nothing decoded yet
    if width * height > MAX_DECODED_PIXELS:
        raise ValueError(f"{width}x{height} px is past MAX_DECODED_PIXELS")
    image.load()
    if turn := _TRANSPOSE.get(browser_orientation(data)):
        image = image.transpose(turn)
    width, height = image.size
    gray = image.mode in _GRAY_MODES
    if image.mode in ("I", "I;16"):         # 16-bit gray: convert() would clip it, not scale it
        image = image.point(lambda v: v / 257)
    if "transparency" in image.info:        # a tRNS colour key or palette entry
        image = image.convert("LA" if gray else "RGBA")
    mask = _soft_mask(image)
    # Rebound so the decoded RGBA is freed before the colour is encoded.
    image = image.convert("L" if gray else "RGB")
    stream = _flate_png(_idat(image), width, height,
                        NameObject("/DeviceGray" if gray else "/DeviceRGB"), 1 if gray else 3)
    if mask is not None:
        stream[NameObject("/SMask")] = mask
    return stream, width, height


def image_xobject(path):
    """Image file → (XObject stream, pixel width, pixel height) as the
    browser shows it, or None when it can't be embedded, which is logged
    with the reason. Never raises: a broken image must not take the whole
    export down with it. A soft mask, and the image an oriented picture's
    form draws, are direct streams in the result; ``XObjectStore`` makes
    them indirect objects, as a PDF needs."""
    name = getattr(path, "name", path)
    try:
        data = path.read_bytes()
        verbatim = (_png(data) if data[:8] == _PNG_MAGIC
                    else _jpeg(data) if data[:2] == b"\xff\xd8" else None)
        return verbatim or _decoded(data)
    except UnidentifiedImageError:
        log.info(f"[pdf-image] unsupported image format: {name}")
    except Exception as e:
        log.warning(f"[pdf-image] could not embed {name}: {e}")
    return None


def _indirect(writer, stream):
    """``stream`` added to ``writer`` with the streams it holds made
    indirect objects first: an image's /SMask, the image an oriented
    picture's form draws."""
    if "/SMask" in stream:
        stream[NameObject("/SMask")] = writer._add_object(stream["/SMask"])
    inner = (stream.get("/Resources") or {}).get("/XObject") or {}
    for key in list(inner):
        if isinstance(inner[key], StreamObject):
            inner[NameObject(key)] = _indirect(writer, inner[key])
    return writer._add_object(stream)


class XObjectStore:
    """Uploaded images referenced by notes or placed on a page → PDF
    XObjects, one per file.

    Every PDF writer embeds the same uploaded pictures, so all register them
    here: ``resolve`` maps an ``/api/uploads/…`` reference to the
    ``(resource name, pixel width, pixel height)`` the caller draws with, and
    ``refs`` holds the indirect objects for the page's /XObject dictionary.
    Unresolvable refs are remembered as ``None`` so a broken image is looked
    up (and logged) once.
    """

    def __init__(self, writer, uploads_dir):
        self.writer, self.dir = writer, uploads_dir
        self.by_src = {}        # src → (name, px_w, px_h) or None
        self.refs = {}          # name → indirect object

    def resolve(self, src: str):
        if src in self.by_src:
            return self.by_src[src]
        info = None
        match = UPLOAD_RE.search(src or "")
        if match and self.dir is not None:
            path = self.dir / match.group(1)
            if not path.is_file():
                log.info(f"[pdf-image] missing upload: {match.group(1)}")
            elif built := image_xobject(path):
                stream, px_w, px_h = built
                name = f"GmIm{len(self.refs)}"
                self.refs[name] = _indirect(self.writer, stream)
                info = (name, px_w, px_h)
        self.by_src[src] = info
        return info


# --- the way back: an image XObject as a file ---------------------------------------

_SPACE_MODES = {"/DeviceGray": "L", "/CalGray": "L", "/G": "L", "/DeviceRGB": "RGB", "/CalRGB": "RGB",
                "/RGB": "RGB", "/DeviceCMYK": "CMYK", "/CMYK": "CMYK"}
_ICC_MODES = {1: "L", 3: "RGB", 4: "CMYK"}
_CHANNELS = {"L": 1, "P": 1, "RGB": 3, "CMYK": 4}
_SUB_BYTE = {"L": {1: "1", 2: "L;2", 4: "L;4", 8: "L"}, "P": {1: "P;1", 2: "P;2", 4: "P;4", 8: "P"}}
_FLATE = ("/FlateDecode", "/Fl")
# An image codec as the one Pillow format its data is opened as.
_CODECS = {"/DCTDecode": "JPEG", "/DCT": "JPEG", "/JPXDecode": "JPEG2000"}


class DecodeBudget:
    """The pixels ``image_file`` may still decode for one import, across
    all its images: one image is capped at ``MAX_DECODED_PIXELS``, and this
    caps them together, since a small PDF can draw many. An image past it
    is refused as one past the cap is."""

    def __init__(self, pixels: int = MAX_IMPORT_PIXELS):
        self.left = pixels

    def take(self, pixels: int) -> None:
        if pixels > self.left:
            raise ValueError(f"{pixels} px is past what is left of the import's decode budget")
        self.left -= pixels


def _filters(stream) -> list[str]:
    found = stream.get("/Filter")
    found = found.get_object() if hasattr(found, "get_object") else found
    if found is None:
        return []
    return [str(f) for f in found] if isinstance(found, ArrayObject) else [str(found)]


def stream_data(stream, limit: int) -> bytes:
    """A PDF stream's data with a /FlateDecode at the front of its filters
    undone, ``limit`` bytes of it at most; an image codec after that (DCT,
    JPX) stays for Pillow, and any other filter is a ValueError. Inflated
    here and never past ``limit``: PyPDF2's ``get_data`` inflates a stream
    whole however large it grows (a few kB can hold gigabytes of zeros), by
    a byte at a time when it is cut short, and keeps the result on the
    object for as long as the reader lives."""
    filters, data = _filters(stream), stream._data
    if filters[:1] and filters[0] in _FLATE:
        data, filters = zlib.decompressobj().decompress(data, max(1, limit)), filters[1:]
    if len(filters) > 1 or (filters and filters[0] not in _CODECS):
        raise ValueError(f"an image filter it does not undo: {filters}")
    return data[:limit]


def _codestream_limit(width: int, height: int) -> int:
    """The most bytes a JPEG or JPEG 2000 codestream of a ``width`` ×
    ``height`` image is read for: more than any encoding of it needs, its
    headers and profiles with it."""
    return 8 * width * height + (1 << 20)


def _predictor(stream) -> int:
    parms = stream.get("/DecodeParms")
    parms = parms.get_object() if hasattr(parms, "get_object") else parms
    if isinstance(parms, ArrayObject):
        parms = parms[0].get_object() if parms else None
    return int(parms.get("/Predictor", 1)) if hasattr(parms, "get") else 1


def _space_mode(space):
    """A colour space → ``(Pillow mode, palette)``: the palette (RGB bytes)
    of an /Indexed space, else None; ``(None, None)`` for a space Pillow
    has no mode for (Lab, Separation, DeviceN, a pattern)."""
    space = space.get_object() if hasattr(space, "get_object") else space
    if not isinstance(space, ArrayObject):
        return _SPACE_MODES.get(str(space)), None
    kind = str(space[0]) if space else ""
    if kind == "/ICCBased" and len(space) > 1:
        profile = space[1].get_object()
        n = profile.get("/N")
        if n is None:  # the profile's alternate space says as much (PyPDF2's writer can drop /N)
            return _space_mode(profile.get("/Alternate", NameObject("/DeviceRGB")))
        return _ICC_MODES.get(int(n)), None
    if kind in ("/Indexed", "/I") and len(space) > 3:
        base, _palette = _space_mode(space[1])
        lookup = space[3].get_object() if hasattr(space[3], "get_object") else space[3]
        table = stream_data(lookup, 256 * 4) if isinstance(lookup, StreamObject) else bytes(lookup)
        if base == "L":
            table = bytes(v for g in table for v in (g, g, g))
        elif base == "CMYK":
            table = Image.frombytes("CMYK", (len(table) // 4, 1), table[:len(table) // 4 * 4]).convert("RGB").tobytes()
        elif base != "RGB":
            return None, None
        return "P", table
    if kind in _SPACE_MODES:
        return _SPACE_MODES[kind], None
    return None, None


def _as_png(stream) -> bytes | None:
    """A Flate image with PNG row filters (``/Predictor`` 10 to 15) as the
    PNG file it was made from, its compressed rows as they are: the
    inverse of the verbatim path, with nothing decoded. None for any other
    image, or one PNG has no colour type for (CMYK, 16-bit colour). PyPDF2
    cannot be asked for such an image's samples: it undoes the row filters
    as though every pixel were one byte."""
    parms = stream.get("/DecodeParms")
    parms = parms.get_object() if hasattr(parms, "get_object") else parms
    if _filters(stream) not in (["/FlateDecode"], ["/Fl"]) or not hasattr(parms, "get") \
            or int(parms.get("/Predictor", 1)) < 10:
        return None
    width, height = int(stream["/Width"]), int(stream["/Height"])
    bpc, colors = int(stream.get("/BitsPerComponent", 8)), int(parms.get("/Colors", 1))
    mode, palette = _space_mode(stream.get("/ColorSpace", NameObject("/DeviceGray")))
    ctype = {("L", 1): 0, ("RGB", 3): 2, ("P", 1): 3}.get((mode, colors))
    if ctype is None or int(parms.get("/Columns", 1)) != width or bpc not in ((8,) if ctype == 2 else (1, 2, 4, 8)):
        return None

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    return b"".join([_PNG_MAGIC, chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bpc, ctype, 0, 0, 0)),
                     chunk(b"PLTE", palette[:768]) if ctype == 3 else b"", chunk(b"IDAT", stream._data),
                     chunk(b"IEND", b"")])


def _samples(stream, budget=None):
    """An image XObject's samples as a Pillow image (its own colours, no
    mask), or None for one it cannot read. Its declared size is what the
    caps and ``budget`` (a ``DecodeBudget``) count, and encoded data must
    have that size: it is opened as the format its filter names (Pillow
    would try every format it knows) and refused when its header says
    otherwise, before anything is decoded."""
    width, height = int(stream["/Width"]), int(stream["/Height"])
    if width * height > MAX_DECODED_PIXELS:
        raise ValueError(f"{width}x{height} px is past MAX_DECODED_PIXELS")
    if budget is not None:
        budget.take(width * height)
    png = _as_png(stream)
    codec = _CODECS.get((_filters(stream) or [""])[-1])
    if png is not None or codec:
        data = png or stream_data(stream, _codestream_limit(width, height))
        image = Image.open(io.BytesIO(data), formats=["PNG" if png is not None else codec])
        if image.size != (width, height):
            raise ValueError(f"its data is {image.width}x{image.height} px, its dictionary says {width}x{height}")
        image.load()
        return image
    mode, palette = _space_mode(stream.get("/ColorSpace", NameObject("/DeviceGray")))
    bpc = int(stream.get("/BitsPerComponent", 8))
    raw = _SUB_BYTE.get(mode, {}).get(bpc) if mode in _SUB_BYTE else mode if bpc == 8 else None
    if mode is None or raw is None:
        return None
    if _predictor(stream) > 1:  # PNG row filters _as_png has no rebuild for, or TIFF's
        raise ValueError("a predictor it does not undo")
    data = stream_data(stream, height * ((width * _CHANNELS[mode] * bpc + 7) // 8))
    image = Image.frombytes("1" if raw == "1" else mode, (width, height), data, "raw", raw)
    if palette is not None:
        image.putpalette(palette[:768])
    return image


def image_file(stream, orientation: int = 1, budget=None) -> tuple[bytes, str] | None:
    """An image XObject read back as a file to store, ``(bytes, extension)``,
    that the browser shows as the PDF draws it, ``orientation`` applied
    (the ``/GammaOrientation`` of the form an export drew it through,
    ``_oriented``; 1 for an image drawn as stored). A /DCTDecode image
    without a soft mask is the JPEG it holds, its bytes as they are when
    their EXIF orientation is that one (a Gamma export's), else with the
    tag set to it (a camera's JPEG another app embedded as stored, which
    PDF viewers draw unturned). Anything else is decoded with Pillow and
    written as a PNG, its /SMask the alpha channel, its samples turned as
    ``orientation`` says; ``budget``, a ``DecodeBudget``, counts what is
    decoded. None, logged, for one it cannot read: an image mask, a filter
    or colour space Pillow has no reading of, one past
    ``MAX_DECODED_PIXELS`` or the budget."""
    try:
        if stream.get("/ImageMask"):
            return None
        orientation = orientation if orientation in _TRANSPOSE else 1
        mask = stream.get("/SMask")
        mask = mask.get_object() if hasattr(mask, "get_object") else mask
        if _filters(stream)[-1:] in (["/DCTDecode"], ["/DCT"]) and not isinstance(mask, StreamObject):
            data = stream_data(stream, _codestream_limit(int(stream["/Width"]), int(stream["/Height"])))
            if data[:2] != b"\xff\xd8":
                return None
            if browser_orientation(data) == orientation:
                return data, ".jpg"
            if oriented := _jpeg_oriented(data, orientation):
                return oriented, ".jpg"
            # no orientation tag to set: decoded below, turned as the PDF drew it
        if not isinstance(mask, StreamObject) and orientation == 1 and (png := _as_png(stream)):
            return png, ".png"
        image = _samples(stream, budget)
        if image is None:
            log.info("[pdf-image] an embedded image in a colour space or depth it cannot read")
            return None
        alpha = _samples(mask, budget) if isinstance(mask, StreamObject) else None
        if image.mode == "CMYK" or (image.mode == "P" and alpha is not None):
            image = image.convert("RGB")
        if alpha is not None:
            alpha = alpha.convert("L")
            if alpha.size != image.size:
                alpha = alpha.resize(image.size)
            image = image.convert("LA" if image.mode in _GRAY_MODES else "RGBA")
            image.putalpha(alpha)
        if orientation in _TRANSPOSE:
            image = image.transpose(_TRANSPOSE[orientation])
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue(), ".png"
    except Exception as e:
        log.warning(f"[pdf-image] could not read an embedded image: {e}")
        return None
