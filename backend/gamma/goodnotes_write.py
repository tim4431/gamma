"""Writing a GoodNotes notebook: the ``.goodnotes`` the GoodNotes export
makes (docs/dev/import_export.md "GoodNotes export"; the format and its
sources: docs/research/goodnotes-onenote-import.md). What goes on its pages
is gamma/goodnotes_export.py's; this module only writes a ``Draft``.

The file is the one goodnotes-codec (MIT, Taylor-Nilsen) builds from
nothing in ``Document.new`` and saw GoodNotes for Mac import: a zip of
``schema.pb`` (schema 24), the event journal ``index.events.pb``, the page
index ``index.notes.pb`` and a ``notes/<N>`` member per page, the
attachment index ``index.attachments.pb`` and ``attachments/<A>``. Its
events are the codec's, event for event, without the fields GoodNotes' own
export (``tests/samples/goodnotes``) lacks: the document made (#30), each
attachment added (#6, a PDF's search index #105 after it), each template
(#2: the PDF page a page is drawn on), each page (#54) with its notes
group's store, index and update (#105, #104, #102), the current page (#10),
and the versioned store and index of every page that holds elements. A page
member is a stream of (metadata, content) records, one pair per element,
in drawing order. Only element kinds the codec saw GoodNotes show are
written: a ball pen or highlighter stroke (#7, the constant-width "flat"
geometry), a picture (#1) and a schema-35 box (#21: a text box, a filled
shape).

Every length here is in GoodNotes' canvas units (11/6 of a point, 1/132
in) from the page's top-left corner, but a stroke's ``width``, which is
GoodNotes' W: the stroke draws W/2 points wide. Nothing here reads Gamma's
data; ids are random UUIDs, as the app's are.
"""

import math
import random
import struct
import time
import uuid as uuidlib
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCHEMA = 24                       # schema.pb, and every record's own schema field
BOX_SCHEMA = 35                   # a #21 box's
DJB2_EMPTY = 5381                 # every metadata record's #14
CANVAS = 11 / 6                   # canvas units per point
FLAT = "vuA(v)A(S(uu))A(S(uuuu))vA(f)"  # a ball pen's geometry
BLOCK = 32768                     # the most raw bytes one LZ4 block of a frame holds, as GoodNotes splits them
LINE_SPACING = 29.33333396911621  # a template's line spacing (16 pt), as the codec and the app write it
INHERIT = -404                    # a run's "the default style's"
ROUND_CAP = 1                     # a line style's lineCap
STANDARD_CANVAS = (834.24, 1078.825)  # GoodNotes' Standard page, for the writing area's margins
PAPER_COLOURS = ((0.8667, 0.8667, 0.8667, 1.0), (1.0, 1.0, 1.0, 1.0))  # a page's canvas colours (dark, light)
# A shape's empty text exactly as GoodNotes writes it, and its content hash
# (goodnotes-codec ``_EMPTY_TEXT``): one run of nothing in the default look.
EMPTY_TEXT = (b"\n]\x12.\x1a\x14\r\xf1\xf0\xf0=\x15\xd9\xd8\xd8=\x1d\xd9\xd8\xd8=%\x00\x00\x80?\xc5\x02"
              b"\x00\x00\x80A\xe0\x03\xec\xfc\xff\xff\xff\xff\xff\xff\xff\x01\xb5\x04\x00\x00\xca\xc3\x1a+"
              b"\n\x02\n\x00\x12\x0b\x08\xff\xff\xff\xff\xff\xff\xff\xff\xff\x01\x1a\x16\x08\xff\xff\xff"
              b"\xff\xff\xff\xff\xff\xff\x01\x10\xff\xff\xff\xff\xff\xff\xff\xff\xff\x01 \x02")
EMPTY_TEXT_HASH = bytes.fromhex("afd2f3d38e516ada")
FONT = "Helvetica Neue"
PNG, JPEG = 0, 1                  # a picture's #6


# --- the draft ---------------------------------------------------------------------------------

@dataclass
class Attachment:
    """A file of the notebook: its bytes, or the path to read them from;
    ``pages`` a PDF's page count (None for a picture)."""
    data: bytes | Path
    pages: int | None = None

    def size(self) -> int:
        return self.data.stat().st_size if isinstance(self.data, Path) else len(self.data)


@dataclass
class Template:
    """The PDF page a page is drawn on: an attachment's id, its 1-based
    page, and the page's size in canvas units."""
    attachment: str
    page: int
    canvas: tuple[float, float]


@dataclass
class Stroke:
    """A ball pen stroke (a highlighter's when ``highlighter``) through
    ``points``, W ``width``; ``dash`` its dash lengths in multiples of its
    drawn width, round-capped."""
    points: list
    width: float
    rgba: tuple
    highlighter: bool = False
    dash: tuple | None = None


@dataclass
class Run:
    """Typed text of one look; ``size`` in canvas units."""
    text: str
    size: float
    rgba: tuple
    bold: bool = False
    italic: bool = False
    font: str | None = None      # a family; None: the box's (Helvetica Neue)


@dataclass
class Box:
    """A schema-35 box at (x, y), w × h: a text box when ``runs`` is not
    None, else a shape, a rectangle or the polygon of ``vertices`` (in the
    unit square of the box); its ``fill`` and its ``outline`` (width,
    rgba) when it has them."""
    x: float
    y: float
    w: float
    h: float
    runs: list | None = None
    fill: tuple | None = None
    outline: tuple | None = None
    vertices: list | None = None


@dataclass
class Picture:
    """An attachment's picture, its frame's centre and size, turned
    ``angle`` radians (clockwise, y down) about its centre."""
    attachment: str
    cx: float
    cy: float
    w: float
    h: float
    angle: float = 0.0
    kind: int = PNG


@dataclass
class Page:
    template: Template
    elements: list = field(default_factory=list)  # Stroke, Box, Picture, in drawing order


@dataclass
class Draft:
    title: str
    created: datetime | None = None
    pages: list = field(default_factory=list)
    attachments: dict = field(default_factory=dict)  # id → Attachment

    def attach(self, data: bytes | Path, pages: int | None = None) -> str:
        ident = new_uuid()
        self.attachments[ident] = Attachment(data, pages)
        return ident


def new_uuid() -> str:
    return str(uuidlib.uuid4()).upper()


def page_uuid() -> str:
    """A page's UUID P whose notes group N = P + 1 changes only its last
    hex digit (its last is 0 to E), as gnnote writes them."""
    while True:
        ident = new_uuid()
        if ident[-1] != "F":
            return ident


def plus_one(ident: str) -> str:
    """The notes group N of a page P: P + 1 as one 128-bit number."""
    return str(uuidlib.UUID(int=(uuidlib.UUID(ident).int + 1) % (1 << 128))).upper()


def order_key(index: int) -> str:
    """A page's order key: fixed-width base 36, so the keys compared as
    ASCII strings, as GoodNotes compares them, are the pages in order."""
    digits = ""
    for _ in range(5):
        index, d = divmod(index, 36)
        digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[d] + digits
    return "43" + digits


# --- the encodings ---------------------------------------------------------------------------

class F64(float):
    """A float written as fixed64: a time, a line spacing."""


def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1   # a negative number as its 64-bit two's complement, as protobuf writes int64
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
    """A protobuf message of ``(number, value)`` fields in the order given:
    a bool or an int a varint, an F64 a fixed64, any other float a fixed32,
    a str or bytes a length-delimited field, a list that field repeated;
    None leaves the field out."""
    out = bytearray()
    for number, value in fields:
        for v in value if isinstance(value, list) else [value]:
            if v is None:
                continue
            if isinstance(v, (bool, int)):
                out += _varint(number << 3) + _varint(int(v))
            elif isinstance(v, F64):
                out += _varint(number << 3 | 1) + struct.pack("<d", v)
            elif isinstance(v, float):
                out += _varint(number << 3 | 5) + struct.pack("<f", v)
            else:
                raw = v.encode() if isinstance(v, str) else bytes(v)
                out += _varint(number << 3 | 2) + _varint(len(raw)) + raw
    return bytes(out)


def stream(records) -> bytes:
    """A member's records, each behind its varint length."""
    return b"".join(_varint(len(r)) + r for r in records)


def lz4_frame(raw: bytes) -> bytes:
    """Apple's framed LZ4 of ``raw``: a ``bv41`` block of literals per
    BLOCK bytes, then ``bv4$`` (any LZ4 reader reads a block that is all
    literals; the codec writes these)."""
    out = bytearray()
    for i in range(0, max(len(raw), 1), BLOCK):
        chunk = raw[i:i + BLOCK]
        n = len(chunk)
        if n < 15:
            block = bytes([n << 4]) + chunk
        else:
            rest = n - 15
            block = b"\xf0" + b"\xff" * (rest // 255) + bytes([rest % 255]) + chunk
        out += b"bv41" + struct.pack("<II", n, len(block)) + block
    return bytes(out) + b"bv4$"


def flat_geometry(points: list, width: float) -> bytes:
    """A ball pen's geometry, the tpl image of FLAT: its version 2, W, a
    move to the first point and a quadratic to each next one whose control
    is the middle of the segment (a straight line), 1, no widths. A lone
    point is a dash of 0.3 units, as gnnote writes one."""
    if len(points) == 1:
        points = [points[0], (points[0][0] + 0.3, points[0][1])]
    quads = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        quads.append(((x0 + x1) / 2, (y0 + y1) / 2, x1, y1))
    body = bytearray(struct.pack("<Hf", 2, width))
    body += struct.pack(f"<I{1 + len(quads)}H", 1 + len(quads), 0, *([1] * len(quads)))
    body += struct.pack("<Iff", 1, *points[0])
    body += struct.pack("<I", len(quads)) + b"".join(struct.pack("<ffff", *q) for q in quads)
    body += struct.pack("<HI", 1, 0)
    head = FLAT.encode() + b"\0"
    return b"tpl\0" + struct.pack("<I", 8 + len(head) + len(body)) + head + bytes(body)


def colour(rgba) -> bytes:
    """``{#1 r, #2 g, #3 b, #4 a}``, a component of 0 left out."""
    return msg(*[(i, float(c)) for i, c in enumerate(rgba, 1) if c])


def point(x: float, y: float) -> bytes:
    return msg((1, float(x) if x else None), (2, float(y) if y else None))


def rect(x: float, y: float, w: float, h: float) -> bytes:
    return msg((1, point(x, y)), (2, point(w, h)))


def _nonce() -> int:
    return random.getrandbits(32)


def stamp(version: int = 1, nonce: int | None = None) -> bytes:
    """A version register: ``{#1 edit counter, #2 random tag}``."""
    return msg((1, version or None), (2, _nonce() if nonce is None else nonce))


def lww(value) -> bytes:
    """A last-writer-wins register: ``{#1 value, #2 stamp}``."""
    return msg((1, value), (2, stamp()))


# --- the elements ------------------------------------------------------------------------------

def _runs(runs: list[Run]) -> bytes:
    """A text box's runs, the message its Apple LZ4 blob holds."""
    para = msg((1, msg((1, ""))), (2, msg((1, -1))), (3, msg((1, -1), (2, -1))))
    return msg((1, [msg((1, r.text),
                        (2, msg((3, colour(r.rgba)), (30, r.font), (40, float(r.size)), (50, 1 if r.italic else None),
                                (60, -30 if r.bold else INHERIT), (70, float(INHERIT)))),
                        (3, para)) for r in runs]))


def _default_style(size: float = 24.0) -> bytes:
    return msg((1, msg((3, msg((4, 1.0))), (30, FONT), (40, float(size)), (60, -60), (70, -20.0))),
               (2, msg((1, msg((1, ""))), (2, ""), (3, msg((1, -1), (2, -1))))))


def _style(width: float, rgba) -> bytes:
    """An outline: ``{#1 width, #2 {#1 solid}, #3 {#1 colour}}``."""
    return msg((1, float(width)), (2, msg((1, ""))), (3, msg((1, colour(rgba)))))


class _Elements:
    """A notebook's element records: one device, one creation counter."""

    def __init__(self, device: int):
        self.device = device
        self.seq = 0

    def _meta(self, ident: str, version: bytes, schema: int = SCHEMA, attachment: str | None = None) -> bytes:
        self.seq += 1
        return msg((1, ident), (2, version), (4, attachment), (8, self.device), (9, self.seq), (14, DJB2_EMPTY),
                   (16, schema))

    def stroke(self, s: Stroke, z: int) -> list[bytes]:
        ident, version = new_uuid(), stamp(2)
        dash = (msg((2, msg((1, struct.pack(f"<{len(s.dash)}f", *s.dash)), (2, ROUND_CAP))))
                if s.dash else None)
        body = msg((1, ident), (2, lz4_frame(flat_geometry(s.points, s.width))), (4, colour(s.rgba)),
                   (5, 1 if s.highlighter else None), (6, ""), (7, msg((1, msg((1, z or None), (2, _nonce()))))),
                   (9, ""), (11, dash), (15, version), (20, ""), (21, SCHEMA))
        return [self._meta(ident, version), msg((7, body))]

    def picture(self, p: Picture) -> list[bytes]:
        """``#2`` the turned frame's bounding box, ``#3`` its centre, size
        and turn, as the codec writes a placed picture."""
        ident, version = new_uuid(), stamp(2)
        c, s = abs(math.cos(p.angle)), abs(math.sin(p.angle))
        bw, bh = p.w * c + p.h * s, p.w * s + p.h * c
        shown = msg((1, point(p.cx, p.cy)), (2, point(p.w, p.h)), (3, float(p.angle) if p.angle else None))
        body = msg((1, ident), (2, rect(p.cx - bw / 2, p.cy - bh / 2, bw, bh)), (3, shown), (4, p.attachment),
                   (5, msg((1, stamp()))), (6, p.kind or None), (15, version), (18, SCHEMA))
        return [self._meta(ident, version, attachment=p.attachment), msg((1, body))]

    def box(self, b: Box) -> list[bytes]:
        """A text box (``#9`` 1, as all of GoodNotes' own text elements),
        or a shape (``#9`` absent), fixed at its size."""
        ident, version = new_uuid(), stamp(2)
        if b.vertices:
            poly = msg((1, [msg((1, point(x, y)), (2, 1)) for x, y in b.vertices]), (2, 1))
            geometry = msg((3, msg((1, poly))))
        else:
            geometry = msg((1, ""))
        fill = msg((1, msg((1, colour(b.fill))) if b.fill else ""))
        outline = _style(*b.outline) if b.outline else msg((2, msg((1, ""))))
        if b.runs is not None:
            text, digest = _runs(b.runs), random.getrandbits(64).to_bytes(8, "little")
        else:
            text, digest = EMPTY_TEXT, EMPTY_TEXT_HASH
        content = msg((1, msg((2, lz4_frame(text)), (3, digest), (4, 1))), (5, _default_style()),
                      (10, msg((1, 10.0), (2, 10.0), (3, 10.0), (4, 10.0))))
        body = msg((1, ident), (2, BOX_SCHEMA), (3, version), (7, msg((1, stamp()))),
                   (9, 1 if b.runs is not None else None), (20, msg((1, point(b.x, b.y)), (3, 1.0))),
                   (21, msg((2, msg((1, float(b.w)), (2, float(b.h)), (3, math.inf))))), (22, geometry),
                   (30, fill), (31, outline), (32, content))
        return [self._meta(ident, version, BOX_SCHEMA), msg((21, body))]


# --- the notebook ------------------------------------------------------------------------------

class _Journal:
    """The event journal: every event's time, id, device and clock (a
    hybrid logical clock, one tick per event)."""

    def __init__(self, device: int, now_ms: float):
        self.device, self.now = device, now_ms
        self.clock = int(now_ms)
        self.events: list[bytes] = []

    def tick(self) -> int:
        self.clock += 1
        return self.clock

    def add(self, target: str, kind: int, body: bytes) -> None:
        self.events.append(msg((1, target), (kind, body)))

    def when(self) -> list:
        """An event body's time (#10) and id (#11)."""
        return [(10, F64(self.now)), (11, new_uuid())]

    def tail(self) -> list:
        """``when``, then the device (#13) and the clock (#14)."""
        return [*self.when(), (13, self.device), (14, self.tick())]


def write_notebook(draft: Draft, out) -> None:
    """Write ``draft`` as a .goodnotes into ``out``, a path or a binary
    file. A draft of no page is refused (ValueError): GoodNotes opens no
    notebook without one."""
    if not draft.pages:
        raise ValueError("a notebook needs a page")
    device = random.getrandbits(63) or 1
    now = time.time() * 1000
    journal = _Journal(device, now)
    elements = _Elements(device)
    doc, folder = new_uuid(), new_uuid()
    created = draft.created.timestamp() * 1000 if draft.created is not None else now
    w, h = draft.pages[0].template.canvas
    journal.add(doc, 30, msg((1, doc), (2, lww(draft.title)), (3, lww(folder)), (6, lww("L" if w > h else "P")),
                             (7, lww(folder)), (9, "auto"), (10, F64(min(created, now))), (11, new_uuid()),
                             (13, device), (14, journal.tick()), (17, ""), (18, ""), (19, msg((2, stamp()))),
                             (20, SCHEMA)))
    for ident, a in draft.attachments.items():
        page_range = msg((1, 1), (2, a.pages)) if a.pages else ""
        journal.add(ident, 6, msg((1, ident), (2, ident), (5, a.size()), (6, doc), *journal.when(),
                                  (12, page_range), (14, device), (15, journal.tick()), (16, SCHEMA)))
        if a.pages:  # GoodNotes indexes a PDF for search
            journal.add(ident, 105, msg((2, doc), (3, ident), *journal.tail(), (15, SCHEMA)))
    templates: dict[tuple, str] = {}
    for page in draft.pages:
        t = page.template
        key = (t.attachment, t.page, t.canvas)
        if key in templates:
            continue
        templates[key] = ident = new_uuid()
        cw, ch = t.canvas
        mx, my = 44.0 * cw / STANDARD_CANVAS[0], 58.6667 * ch / STANDARD_CANVAS[1]
        area = msg((1, rect(mx, my, cw - 2 * mx, ch - 2 * my)), (2, 28.416668), (3, 0.9166667), (5, 1))
        journal.add(ident, 2, msg((1, doc), (2, ident), (4, t.attachment), (5, t.page), (6, 1), (7, F64(LINE_SPACING)),
                                  (8, point(cw, ch)), *journal.when(), (12, msg((1, F64(LINE_SPACING)), (2, stamp()))),
                                  (13, lww(True)), (15, device), (16, journal.tick()), (17, lww(1)), (18, area),
                                  (19, msg((2, stamp()))), (21, SCHEMA)))
    ids, notes, members = [], [], {}
    canvas = msg((1, msg((2, msg((1, colour(PAPER_COLOURS[0])), (2, colour(PAPER_COLOURS[1])))))), (2, stamp()))
    for n, page in enumerate(draft.pages):
        t = page.template
        pid = page_uuid()
        nid = plus_one(pid)
        ids.append((pid, nid))
        journal.add(pid, 54, msg((1, doc), (2, pid), (3, lww(templates[(t.attachment, t.page, t.canvas)])),
                                 (4, lww(order_key(n))), *journal.tail(), (15, SCHEMA), (17, canvas)))
        _notes_events(journal, doc, nid)
        records = []
        for z, element in enumerate(page.elements):
            if isinstance(element, Stroke):
                records += elements.stroke(element, z)
            elif isinstance(element, Picture):
                records += elements.picture(element)
            else:
                records += elements.box(element)
        notes.append(msg((1, nid), (2, f"notes/{nid}")))
        members[f"notes/{nid}"] = stream(records)
    journal.add(doc, 10, msg((1, doc), (2, ids[0][0]), (3, f"PagingViewServiceUpdater:{new_uuid()}"), *journal.tail(),
                             (15, SCHEMA)))
    for (_pid, nid), page in zip(ids, draft.pages):
        if page.elements:  # a page's notes hold items: the store and index again, at a version of their own
            _notes_events(journal, doc, nid, random.getrandbits(63) or 1)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("schema.pb", msg((1, SCHEMA)))
        zf.writestr("document.info.pb", b"")
        zf.writestr("index.notes.pb", stream(notes))
        zf.writestr("index.attachments.pb", stream([msg((1, i), (2, f"attachments/{i}")) for i in draft.attachments]))
        zf.writestr("index.events.pb", stream(journal.events))
        for name, data in members.items():
            zf.writestr(name, data)
        for ident, a in draft.attachments.items():
            if isinstance(a.data, Path):
                zf.write(a.data, f"attachments/{ident}")
            else:
                zf.writestr(f"attachments/{ident}", a.data)


def _notes_events(journal: _Journal, doc: str, nid: str, version: int = 0) -> None:
    """A page's notes group stored (#105), indexed (#104) and updated
    (#102); ``version`` the random 63-bit id GoodNotes stamps on the store
    and the index once the group holds items."""
    journal.add(nid, 105, msg((1, 1), (2, doc), (4, nid), (5, version or None), (6, "auto"), *journal.tail(),
                              (15, SCHEMA)))
    journal.add(nid, 104, msg((1, doc), (2, nid), *journal.when(), (12, version or None),
                              (13, "auto"), (14, 1), (15, new_uuid()), (17, journal.device), (18, journal.tick()),
                              (19, SCHEMA)))
    journal.add(nid, 102, msg((1, nid), *journal.tail(), (15, SCHEMA), (16, doc)))
