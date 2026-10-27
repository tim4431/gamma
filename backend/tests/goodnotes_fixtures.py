"""A test-only writer of GoodNotes ``.goodnotes`` files, built from the
format's public description (docs/research/goodnotes-onenote-import.md):
protobuf records written field by field, stroke geometry as tpl images in
Apple's framed LZ4, the event journal, the page and attachment indexes,
catalogue papers drawn as GoodNotes' svg2pdf draws them, and the zip
around them. It imports no pytest; pictures, audio and plain PDFs come from
notability_fixtures. A ``Notebook`` is built page by page
(``Notebook.page``) from the element helpers below and written by
``Notebook.bytes``; its ids are deterministic, so a notebook built twice
is the same file."""

import hashlib
import io
import re
import struct
import zipfile

PAPERS = {"blank": "B03314A5-9B9F-556B-B373-4678E13D0DAC", "dots": "CC365888-75C9-5DBF-B1D4-A7E8E0AD3B7F",
          "grid": "9FE8F365-4BEE-5057-8573-1A56C77CAC19", "narrow": "238EE861-EE9A-51BD-B010-832CD3C50D2C",
          "ruled": "AA273C17-F440-5125-B34E-F9C3C62F89E2", "cover": "417A3734-B870-5E89-8055-DA029834F25E",
          "cornell": "0C0A0B0D-1111-4222-8333-444455556666"}  # the last: a design the import does not know
PITCH = {"dots": 16.0, "grid": 16.0, "narrow": 17.5, "ruled": 25.0}
STANDARD = (455.04, 588.45)
CANVAS = 11 / 6
RULE = (0.8156863, 0.8235294, 0.827451)


class F64(float):
    """A float written as fixed64 (an event's timestamp)."""


# --- protobuf -------------------------------------------------------------------------------

def varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def msg(*fields) -> bytes:
    """A message of ``(number, value)`` fields: an int a varint, a float a
    fixed32, an F64 a fixed64, a str, bytes or message a length-delimited
    field, a list that field repeated."""
    out = bytearray()
    for number, value in fields:
        for v in value if isinstance(value, list) else [value]:
            if v is None:
                continue
            if isinstance(v, bool) or isinstance(v, int):
                out += varint(number << 3) + varint(int(v))
            elif isinstance(v, F64):
                out += varint(number << 3 | 1) + struct.pack("<d", v)
            elif isinstance(v, float):
                out += varint(number << 3 | 5) + struct.pack("<f", v)
            else:
                raw = v.encode() if isinstance(v, str) else bytes(v)
                out += varint(number << 3 | 2) + varint(len(raw)) + raw
    return bytes(out)


def stream(records) -> bytes:
    return b"".join(varint(len(r)) + r for r in records)


def point(x: float, y: float) -> bytes:
    return msg((1, float(x)), (2, float(y)))


def rect(x, y, w, h) -> bytes:
    return msg((1, point(x, y)), (2, point(w, h)))


def colour(r=0.0, g=0.0, b=0.0, a=1.0) -> bytes:
    """GoodNotes' RGBA: a component of 0 omitted."""
    return msg(*[(i, float(v)) for i, v in enumerate((r, g, b, a), 1) if v])


# --- geometry -------------------------------------------------------------------------------

def lz4_stored(raw: bytes) -> bytes:
    """Apple's frame with one stored block: ``bv4-``, its size, the bytes."""
    return b"bv4-" + struct.pack("<I", len(raw)) + raw + b"bv4$"


def lz4_literals(raw: bytes) -> bytes:
    """Apple's frame with one compressed block that is all literals."""
    n = len(raw)
    if n < 15:
        block = bytes([n << 4]) + raw
    else:
        rest, ext = n - 15, bytearray()
        while rest >= 255:
            ext.append(255)
            rest -= 255
        ext.append(rest)
        block = bytes([0xF0]) + bytes(ext) + raw
    return b"bv41" + struct.pack("<II", n, len(block)) + block + b"bv4$"


def tpl(fmt: str, parts: list) -> bytes:
    """A tpl image of ``fmt`` whose values are given packed already: each
    part ``(struct letters, values)`` or raw bytes."""
    body = b"".join(p if isinstance(p, bytes) else struct.pack("<" + p[0], *p[1]) for p in parts)
    head = fmt.encode() + b"\0"
    return b"tpl\0" + struct.pack("<I", 8 + len(head) + len(body)) + head + body


def _array(letters: str, rows: list) -> list:
    return [("I", [len(rows)])] + [(letters, row) for row in rows]


def flat(pieces: list, width: float = 1.559, short: bool = False) -> bytes:
    """A constant-width stroke's geometry: each piece a start point, then
    (control, end) pairs."""
    flags, starts, quads = [], [], []
    for start, *segments in pieces:
        flags.append(0)
        starts.append(start)
        for control, end in segments:
            flags.append(1)
            quads.append((*control, *end))
    fmt = "vuA(v)A(S(uu))A(S(uuuu))" if short else "vuA(v)A(S(uu))A(S(uuuu))vA(f)"
    parts = [("H", [2]), ("f", [width])] + _array("H", [[f] for f in flags]) + _array("ff", starts) \
        + _array("ffff", quads)
    if not short:
        parts += [("H", [1]), ("I", [0])]
    return tpl(fmt, parts)


def ribbon(pieces: list, *, width_word: float | None = None, turned: bool = False) -> bytes:
    """A variable-width stroke's geometry: each piece (x, y, r) samples, an
    odd count of them (a start, then panels of two). ``width_word``: the
    layout with a width word, its panels (x1, y1, x2, y2, r1, r2);
    ``turned``: the layout of a nib's angle (flags 4 and 5), a float more
    per start and three more per panel."""
    flags, starts, panels = [], [], []
    for samples in pieces:
        first, rest = samples[0], samples[1:]
        flags.append(4 if turned else 0)
        starts += [*first] + ([0.0] if turned else [])
        for i in range(0, len(rest) - 1, 2):
            (x1, y1, r1), (x2, y2, r2) = rest[i], rest[i + 1]
            flags.append(5 if turned else 1)
            panel = [x1, y1, x2, y2, r1, r2] if width_word is not None else [x1, y1, r1, x2, y2, r2]
            panels += panel + ([0.0, 0.0, 0.1] if turned else [])
    fmt = ("vu" if width_word is not None else "v") + "A(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)"
    parts = [("H", [2])] + ([("f", [width_word])] if width_word is not None else [])
    parts += _array("H", [[f] for f in flags]) + [("I", [len(starts)]), ("%df" % len(starts), starts)]
    parts += [("I", [len(panels)]), ("%df" % len(panels), panels)]
    parts += [("I", [0])] * 7
    return tpl(fmt, parts)


def pencil(samples: list, width: float = 1.559, tilt=(0.5236, 1.0472)) -> bytes:
    """A pencil stroke's geometry: (x, y, force) samples, an odd count."""
    a, b = tilt
    (x, y, f), rest = samples[0], samples[1:]
    flags = [0] + [1] * (len(rest) // 2)
    segments = [(0.0, rest[i][0], rest[i][1], a, b, rest[i][2], rest[i + 1][0], rest[i + 1][1], a, b, rest[i + 1][2])
                for i in range(0, len(rest) - 1, 2)]
    parts = [("H", [1]), ("f", [width])] + _array("H", [[g] for g in flags]) + _array("fffff", [(x, y, a, b, f)])
    parts += _array("fffffffffff", segments) + [("I", [0])] * 5
    return tpl("vuA(v)A(S(uuuuu))A(S(uuuuuuuuuuu))A(S(uu))A(v)A(S(uu))A(S(uuuu))A(u)", parts)


# --- shapes (a stroke's #9) -----------------------------------------------------------------

def polyline_shape(points: list, width: float = 1.559) -> bytes:
    return msg((1, msg((1, [point(*p) for p in points]))), (5, msg((2, 1))), (15, float(width)))


def curve_shape(p0, control, p1, width: float = 1.559) -> bytes:
    return msg((2, msg((1, point(*p0)), (2, point(*control)), (3, point(*p1)))), (15, float(width)))


def rect_shape(centre, size, width: float = 1.559) -> bytes:
    return msg((3, msg((1, point(*centre)), (2, point(*size)))), (15, float(width)))


def ellipse_shape(centre, axes, theta: float = 0.0, width: float = 1.559) -> bytes:
    return msg((4, msg((1, point(*centre)), (2, point(*axes)), (3, float(theta)))), (15, float(width)))


# --- elements -------------------------------------------------------------------------------

_UUID = re.compile(r"^[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}$")


def as_uuid(name: str) -> str:
    """An element's id: a UUID as it is, any other name the UUID it hashes to."""
    if _UUID.match(name):
        return name
    h = hashlib.sha256(name.encode()).hexdigest().upper()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def element(ident: str, kind: int, body: bytes, *, deleted: bool = False, attachment: str | None = None) -> list:
    """An element's (metadata, content) records; ``ident`` any name
    (``as_uuid``)."""
    meta = msg((1, as_uuid(ident)), (2, msg((1, 2), (2, 1234))), (3, 1 if deleted else None), (4, attachment),
               (8, 7401413043201209088), (9, 100), (14, 5381), (16, 24))
    return [meta, msg((kind, body))]


def ink(ident: str, blob: bytes | None = None, *, rgba=(0.0, 0.0, 0.0, 1.0), kind: int | None = None,
        highlighter: bool = False, offset=None, shape: bytes | None = None, tape: bool = False,
        dash: list | None = None, deleted: bool = False, erased: bool = False, lz4=lz4_literals) -> list:
    """A stroke: its geometry ``blob`` (a tpl image, framed here by
    ``lz4``), or a recognised ``shape``'s #9; ``erased`` its content's
    #14."""
    body = msg((1, as_uuid(ident)), (2, lz4(blob) if blob is not None else b""), (3, kind), (4, colour(*rgba)),
               (5, 1 if highlighter else None), (6, point(*offset) if offset else b""), (7, msg((1, 1), (2, 5))),
               (9, shape if shape is not None else b""),
               (11, msg((2, msg((1, struct.pack(f"<{len(dash)}f", *dash)), (2, 1)))) if dash else None),
               (14, 1 if erased else None), (15, msg((1, 2), (2, 1234))),
               (20, msg((1, "")) if tape else b""), (21, 24))
    return element(ident, 7, body, deleted=deleted)


def fill(ident: str, outline: str, rgba=(0.0, 0.0, 0.0, 0.1), *, erased: bool = False) -> list:
    nan = float("nan")
    box = rect(nan, nan, nan, nan) if erased else rect(0, 0, 10, 10)
    body = msg((1, as_uuid(ident)), (2, box), (5, as_uuid(outline)), (7, colour(*rgba)),
               (14, 1 if erased else None))
    return element(ident, 9, body)


def image(ident: str, attachment: str, box, *, shown=None, turn: float = 0.0, kind: int | None = None) -> list:
    """A picture at ``box`` (x, y, w, h), shown at ``shown`` (centre and
    size; the box's own by default) turned ``turn`` radians."""
    x, y, w, h = box
    cx, cy, sw, sh = shown or (x + w / 2, y + h / 2, w, h)
    visible = msg((1, point(cx, cy)), (2, point(sw, sh)), (3, float(turn) if turn else None))
    body = msg((1, as_uuid(ident)), (2, rect(*box)), (3, visible), (4, attachment), (6, kind))
    return element(ident, 1, body, attachment=attachment)


def rtf_text(ident: str, outer, text_rtf: bytes, *, rgba=None) -> list:
    """A legacy text box: its outer rect, its text frame 10 units inside."""
    x, y, w, h = outer
    body = msg((1, as_uuid(ident)), (2, rect(*outer)), (3, rect(x + 10, y + 10, w - 20, h - 20)), (6, text_rtf),
               (7, colour(*rgba) if rgba else None), (10, 10.0))
    return element(ident, 8, body)


def text(ident: str, origin, size, runs: list, *, turn: float = 0.0, scale: float = 1.0, kind: int = 1,
         default_size: float = 24.0, fill=None, lz4=lz4_literals) -> list:
    """A schema-35 element: a text box (``kind`` 1) or a Shape tool object
    (0), its box ``size`` at ``origin`` with insets of 10, its ``runs``
    ``(text, size or None, rgba or None, bold)``, its ``fill`` an RGBA."""
    listed = msg((1, [msg((1, t), (2, msg((3, colour(*c) if c else None), (30, "Helvetica Neue"),
                                         (40, float(s) if s else -404.0), (60, -30 if bold else -404))))
                      for t, s, c, bold in runs]))
    content = msg((1, msg((2, lz4(listed)), (4, 1))), (2, point(size[0] - 20, size[1] - 20)),
                  (5, msg((1, msg((3, colour()), (30, "Helvetica Neue"), (40, float(default_size)), (60, -60))))),
                  (10, msg((1, 10.0), (2, 10.0), (3, 10.0), (4, 10.0))))
    body = msg((1, as_uuid(ident)), (2, 35), (9, kind), (20, msg((1, point(*origin)), (2, float(turn) if turn else None),
                                                       (3, float(scale)))),
               (21, msg((2, msg((1, float(size[0])), (2, float(size[1])), (3, float("inf")))))),
               (30, msg((1, msg((1, colour(*fill))))) if fill else None), (32, content))
    return element(ident, 21, body)


def unknown(ident: str, kind: int) -> list:
    return element(ident, kind, msg((1, as_uuid(ident))))


COCOA = (rb"{\rtf1\ansi\ansicpg1252\cocoartf2709" b"\n"
         rb"\cocoatextscaling1\cocoaplatform1{\fonttbl\f0\fnil\fcharset0 HelveticaNeue;\f1\fnil\fcharset0 "
         rb"HelveticaNeue-Bold;}" b"\n"
         rb"{\colortbl;\red255\green255\blue255;\red200\green0\blue0;}" b"\n"
         rb"{\*\expandedcolortbl;;\cssrgb\c0\c0\c0;}" b"\n"
         rb"\pard\tx560\tx1120\sl-559\partightenfactor0" b"\n\n"
         rb"\f0\fs48 \cf2 Plain and " rb"\f1\b bold}")


# --- papers ---------------------------------------------------------------------------------

def _pdf(objects: list[bytes]) -> bytes:
    """A PDF of numbered objects (1 the catalog), its xref written."""
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def _stream(data: bytes, extra: bytes = b"") -> bytes:
    return b"<< /Length %d %s>>\nstream\n" % (len(data), extra) + data + b"\nendstream"


def paper_pdf(size=STANDARD, fill=(1.0, 1.0, 1.0), rules: list | None = None, inline: bool = False) -> bytes:
    """A one-page catalogue paper as svg2pdf writes one: a fill of the
    paper's colour, then a 0.5 pt rule at each of ``rules`` (points from
    the top), in a Form XObject, or in the page itself when ``inline``."""
    w, h = size
    fill_ops = b"q\n/srgb cs\n%.7f %.7f %.7f scn\n0 %.2f m\n%.2f %.2f l\n%.2f 0 l\n0 0 l\nh\nf\nQ\n" % (
        *fill, h, w, h, w)
    rule_ops = b"".join(b"q\n/srgb cs\n%.7f %.7f %.7f scn\n0 %.2f %.2f 0.5 re\nf\nQ\n" % (*RULE, h - y, w)
                        for y in rules or [])
    if inline or not rules:
        page_ops, xobject = fill_ops + rule_ops, b""
        objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                   b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents 4 0 R "
                   b"/Resources << /ColorSpace << /srgb /DeviceRGB >> >> >>" % (w, h), _stream(page_ops)]
    else:
        page_ops = fill_ops + b"q\n/xo0 Do\nQ\n"
        xobject = _stream(rule_ops, b"/Type /XObject /Subtype /Form /BBox [0 0 %.2f %.2f] "
                                    b"/Resources << /ColorSpace << /srgb /DeviceRGB >> >> " % (w, h))
        objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                   b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents 4 0 R "
                   b"/Resources << /ColorSpace << /srgb /DeviceRGB >> /XObject << /xo0 5 0 R >> >> >>" % (w, h),
                   _stream(page_ops), xobject]
    return _pdf(objects)


def sticker_pdf(size=(100, 60)) -> bytes:
    """A die-cut sticker: a one-page vector PDF of a filled shape."""
    w, h = size
    ops = b"0.9 0.2 0.2 rg\n5 5 %.2f %.2f re\nf\n" % (w - 10, h - 10)
    return _pdf([b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents 4 0 R >>" % (w, h),
                 _stream(ops)])


# --- a notebook -----------------------------------------------------------------------------

def uuid_of(n: int, seed: int = 0) -> str:
    h = f"{seed:08X}{n:024X}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def plus_one(ident: str) -> str:
    """The notes member N of a page P: P + 1 as one 128-bit number."""
    h = f"{(int(ident.replace('-', ''), 16) + 1) % (1 << 128):032X}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


class Notebook:
    """A notebook made page by page. ``paper`` and ``pdf`` give a template's
    id; ``page`` adds a page bound to one, holding elements (each a list of
    records, from the helpers above); ``bytes`` writes the file."""

    def __init__(self, title: str = "Notebook", seed: int = 1, created_ms: float = 1790000000000.0):
        self.seed, self.n = seed, 0
        self.doc = self.ident()
        self.events = [msg((1, self.doc), (30, msg((1, self.doc), (2, msg((1, title))), (9, "auto"),
                                                   (10, F64(created_ms)))))]
        self.attachments: dict[str, tuple[str, bytes]] = {}  # id → (member, bytes)
        self.pages: list[tuple[str, str]] = []                # (P, member) in index order
        self.contents: dict[str, bytes] = {}                  # member → its records
        self.extra: dict[str, bytes] = {}                     # more members, as they are

    def ident(self) -> str:
        self.n += 1
        return uuid_of(self.n, self.seed)

    def event(self, kind: int, *fields) -> None:
        entity = fields[0][1] if fields and fields[0][0] == 2 else self.doc
        self.events.append(msg((1, entity), (kind, msg(*fields))))

    def attach(self, data: bytes, *, storage: str | None = None) -> str:
        ident = self.ident()
        stored = storage or ident
        self.attachments[ident] = (f"attachments/{stored}", data)
        self.event(6, (1, ident), (2, stored), (5, len(data)), (6, self.doc))
        return ident

    def template(self, attachment: str, *, name: str = "", canvas=None, page: int = 1, pitch: float | None = None,
                 rules: bool = False) -> str:
        ident = self.ident()
        width, height = canvas or STANDARD
        guide = msg((2, float(pitch * CANVAS - 0.9166667)), (3, 0.9166667), (5, 1)) if rules and pitch else None
        self.event(2, (1, self.doc), (2, ident), (4, attachment), (5, page),
                   (8, msg((1, float(width * CANVAS)), (2, float(height * CANVAS)))),
                   (9, name or None), (18, guide))
        return ident

    def paper(self, kind: str = "blank", *, size=STANDARD, colour: str = "White", fill=(1.0, 1.0, 1.0),
              inline: bool = False) -> str:
        """A catalogue paper's template: its PDF drawn, its name and writing guide."""
        pitch = PITCH.get(kind)
        rules = [pitch * k for k in range(1, int(size[1] // pitch))] if pitch else None
        data = paper_pdf(size, fill, rules, inline)
        attachment = self.attach(data)
        token = "a4" if abs(size[0] - 595.28) < 0.01 else "standard"
        return self.template(attachment, name=f"{PAPERS[kind]}_{token}_1_2 - {colour}", canvas=size, pitch=pitch,
                             rules=bool(pitch))

    def pdf(self, data: bytes, page: int = 1, size=(612, 792), attachment: str | None = None) -> str:
        """A template of a page of a user's PDF (attached once, then named again)."""
        attachment = attachment or self.attach(data)
        return self.template(attachment, canvas=size, page=page)

    def page(self, template: str | None, elements=(), *, key: str | None = None) -> str:
        ident = self.ident()
        member = f"notes/{plus_one(ident)}"
        fields = [(2, ident), (3, msg((1, template)) if template else None),
                  (4, msg((1, key or f"K{len(self.pages):04d}")))]
        self.event(54, *fields)
        self.pages.append((ident, member))
        self.contents[member] = stream([r for e in elements for r in e])
        return ident

    def recording(self, data: bytes, when_ms: float = 1790000100000.0) -> str:
        attachment = self.attach(data)
        ident = self.ident()
        self.event(160, (1, ident), (2, attachment), (3, self.doc), (10, F64(when_ms)))
        return attachment

    def bytes(self, *, drop: tuple = ()) -> bytes:
        members = {
            "index.notes.pb": stream([msg((1, plus_one(p)), (2, m)) for p, m in self.pages]),
            "index.events.pb": stream(self.events),
            "index.attachments.pb": stream([msg((1, i), (2, m)) for i, (m, _d) in self.attachments.items()]),
            "schema.pb": msg((1, 24)),
            "thumbnail.jpg": b"\xff\xd8\xff\xd9",
            **self.contents,
            **{m: d for m, d in self.attachments.values()},
            **self.extra,
        }
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in members.items():
                if name not in drop:
                    zf.writestr(name, data)
        return out.getvalue()


def zip_of(files: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()
