"""Uploaded pictures → PDF image XObjects.

Note boxes can contain ``![](/api/uploads/…)`` refs, and an exported note that
shows the URL instead of the picture is useless. Two paths:

* Verbatim, at any size and no decoding cost: JPEG as ``/DCTDecode`` (only the
  SOF header is parsed for the size and component count), and 8-bit
  gray/RGB/palette PNG without transparency as ``/FlateDecode`` with
  ``/Predictor 15``, which is exactly PNG's row filtering.
* Decoded with Pillow: everything else. That is a PNG with an alpha channel
  or a ``tRNS`` chunk (the common case for pasted screenshots), with 16-bit
  or sub-byte samples, or interlaced, and GIF and WebP. The colour is
  re-encoded as an 8-bit PNG and embedded the verbatim way, and transparency
  becomes a soft mask (``/SMask``), so what is under the picture shows
  through, as in the app: a note box's tint, not a white rectangle. A 25 MP
  screenshot takes well under a second; memory, about 8 bytes a pixel while
  it is decoded, is what ``MAX_DECODED_PIXELS`` bounds.

A picture that can't be embedded (an SVG, a corrupt file) is logged with the
reason, and the writers draw its alt text instead.
"""

import io
import struct

from PIL import Image, UnidentifiedImageError
from PyPDF2.generic import (
    ArrayObject,
    ByteStringObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from .logbuf import log
from .markdown_export import UPLOAD_RE

# The largest picture the decoded path takes: an 8K screen is 33 MP, and
# 40 MP is about 330 MB while decoded. The verbatim path has no cap.
MAX_DECODED_PIXELS = 40_000_000
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_GRAY_MODES = {"1", "L", "LA", "I", "I;16"}


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


def _jpeg(data: bytes):
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in _SOF:
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            comps = data[i + 9]
            space = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(comps)
            if not space:
                return None
            return _stream(data, width, height, NameObject(space), 8, "/DCTDecode"), width, height
        i += 2 + length
    return None


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
    return _flate_png(bytes(idat), width, height, space, 3 if ctype == 2 else 1), width, height


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
    """Any picture Pillow reads: its colour as 8-bit gray or RGB, and its
    transparency as an /SMask."""
    image = Image.open(io.BytesIO(data))
    width, height = image.size              # read from the header, nothing decoded yet
    if width * height > MAX_DECODED_PIXELS:
        raise ValueError(f"{width}x{height} px is past MAX_DECODED_PIXELS")
    image.load()
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
    """Image file → (XObject stream, pixel width, pixel height), or None when
    it can't be embedded, which is logged with the reason. Never raises: a
    broken image must not take the whole export down with it. A soft mask
    is a direct stream in the result's ``/SMask``; ``XObjectStore`` makes it
    an indirect object, as a PDF needs."""
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


class XObjectStore:
    """Uploaded images referenced by notes → PDF XObjects, one per file.

    Both PDF writers embed the same pasted pictures, so both register them
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
            if path.is_file():
                built = image_xobject(path)
                if built:
                    stream, px_w, px_h = built
                    if "/SMask" in stream:
                        stream[NameObject("/SMask")] = self.writer._add_object(stream["/SMask"])
                    name = f"GmIm{len(self.refs)}"
                    self.refs[name] = self.writer._add_object(stream)
                    info = (name, px_w, px_h)
        self.by_src[src] = info
        return info
