"""A test-only writer of Notability ``.ntb`` files, built from the format's
description (docs/research/notability-format.md ".ntb"): a FlatBuffers
``noteBundle`` of records and the zip around it. ``Table`` and ``Vector``
are laid out each before what it leads to, so every offset leads forward,
and each table's vtable just before it. The record helpers put each value
in the slot gamma/notability_ntb.py reads it from."""

import io
import math
import struct
import zipfile


EMPTY_VTABLE = 2  # an empty table's vtable: its own size alone, as Notability's iPad app writes it (4: its table's too)


class Table:
    """A table: ``{slot: value}``, a value a ``Table``, a ``Vector``, a
    ``str`` or ``bytes`` (each led to by an offset), or ``Inline`` bytes.
    An empty one's vtable is EMPTY_VTABLE bytes long."""

    def __init__(self, **slots):
        self.slots = {int(k[1:]): v for k, v in slots.items()}  # s0=..., s5=...


class Vector:
    """A vector of tables (each led to by an offset), or of ``Inline``
    structs of one width."""

    def __init__(self, items):
        self.items = list(items)


class Inline(bytes):
    """Bytes kept in place: a scalar, a struct."""


def u8(v): return Inline(struct.pack("<B", v))
def u32(v): return Inline(struct.pack("<I", v))
def u64(v): return Inline(struct.pack("<Q", v))
def f32(v): return Inline(struct.pack("<f", v))
def pair(x, y): return Inline(struct.pack("<ff", x, y))
def rect(x, y, w, h): return Inline(struct.pack("<4f", x, y, w, h))
def ident(a, b): return Inline(struct.pack("<II", a, b))
def place(owner, count): return Inline(struct.pack("<III", *owner, count))  # an id and a count past it
def rgba(*v): return Inline(bytes(v))


def dumps(root: Table) -> bytes:
    """The buffer: the root's offset, then each table, its vtable before it
    and what it leads to after it."""
    out = bytearray(4)
    struct.pack_into("<I", out, 0, _emit(out, root))
    return bytes(out)


def _emit(out: bytearray, node) -> int:
    if isinstance(node, Table):
        slots = sorted(node.slots)
        vtable = len(out)
        size = 4 + 2 * (max(slots) + 1) if slots else EMPTY_VTABLE
        out += bytes(size)
        table = len(out)
        out += struct.pack("<i", table - vtable)
        if size == 2:
            struct.pack_into("<H", out, vtable, size)
            return table
        refs, offsets = [], {}
        for slot in slots:
            value = node.slots[slot]
            offsets[slot] = len(out) - table
            if isinstance(value, Inline):
                out += value
            else:
                refs.append((len(out), value))
                out += bytes(4)
        struct.pack_into("<HH", out, vtable, size, len(out) - table)
        for slot, offset in offsets.items():
            struct.pack_into("<H", out, vtable + 4 + 2 * slot, offset)
        for at, value in refs:
            struct.pack_into("<I", out, at, _emit(out, value) - at)
        return table
    at = len(out)
    if isinstance(node, Vector):
        out += struct.pack("<I", len(node.items))
        if node.items and isinstance(node.items[0], Inline):
            out += b"".join(node.items)
            return at
        slots = [len(out) + 4 * i for i in range(len(node.items))]
        out += bytes(4 * len(node.items))
        for slot, item in zip(slots, node.items):
            struct.pack_into("<I", out, slot, _emit(out, item) - slot)
        return at
    raw = node.encode() if isinstance(node, str) else bytes(node)
    out += struct.pack("<I", len(raw)) + raw + (b"\x00" if isinstance(node, str) else b"")
    return at


# --- records ---------------------------------------------------------------------------

PAGES = (0, 1)        # the page list's id, which pages count from
DOCUMENT = (0, 0)


def record(kind: int, key: tuple, payload: Table, time: int = 1_788_159_111_000) -> Table:
    return Table(s0=ident(*key), s1=u64(time), s4=u8(kind), s5=payload)


def document(title="Lecture", width=679.0, height=891.1875, *, pattern=0, spacing=19.97, legacy=13, color=None,
             font_size=12.0) -> Table:
    """The document record's payload: its title, its page and paper."""
    paper = {"s0": u8(pattern)} if pattern is not None else {}
    if spacing is not None:
        paper["s1"] = f32(spacing)
    if color is not None:
        paper["s4"] = rgba(*color)
    if legacy is not None:
        paper["s5"] = u8(legacy)
    layout = Table(s0=Table(**paper), s3=pair(width, height))
    return Table(s0=Table(s0=title), s1=Table(s0=layout), s2=Table(s0="en_US"), s4="Helvetica Neue",
                 s5=f32(font_size))


def _node(width=1.0, force=1.0, altitude=255, azimuth=0) -> bytes:
    return struct.pack("<eeBB", width, force, altitude, azimuth)


def cubic(*pieces, nodes=None, precision: int = 0) -> bytes:
    """A cubic ink path: ``pieces`` are lists of points (an anchor, then two
    controls and an anchor per segment) as offsets from the origin, the
    eraser's breaks between them; ``nodes`` one ``(width, force, altitude
    byte, azimuth byte)`` per anchor of every piece."""
    scalar = "e" if precision == 0 else "f"
    point = lambda p: struct.pack("<" + 2 * scalar, *p)  # noqa: E731
    anchors = [1 + (len(piece) - 1) // 3 for piece in pieces]
    nodes = list(nodes) if nodes is not None else [(1.0, 1.0, 255, 0)] * sum(anchors)
    count = sum(anchors)
    out = bytearray(struct.pack("<BHB", precision, count, 3)) + point(pieces[0][0])
    node = iter(nodes)
    for n, piece in enumerate(pieces):
        if n:
            out += _node(*next(node)) + b"\x03" + point(piece[0])
        for s in range(1, len(piece), 3):
            out += _node(*next(node)) + b"\x00" + b"".join(point(p) for p in piece[s:s + 3])
    return bytes(out + _node(*next(node)))


def linear(*pieces) -> bytes:
    """A linear ink path: each piece a polyline, the eraser's breaks between them."""
    point = lambda p: struct.pack("<ee", *p)  # noqa: E731
    out = bytearray(struct.pack("<BHB", 0, sum(len(p) for p in pieces), 7)) + point(pieces[0][0])
    for n, piece in enumerate(pieces):
        for i, p in enumerate(piece):
            if n or i:
                out += (b"\x07" if i == 0 else b"\x06") + point(p)
    return bytes(out)


def ink(path: bytes, *, page=0, origin=(100.0, 100.0), tool=0, line=1, color=(30, 30, 30, 255), width=2.0,
        scale=None, rotation=None) -> Table:
    slots = {"s0": place(PAGES, page), "s1": pair(*origin), "s4": u8(tool), "s5": u8(line), "s7": rgba(*color),
             "s8": f32(width), "s9": path}
    if scale is not None:
        slots["s3"] = pair(*scale)
    if rotation is not None:
        slots["s2"] = f32(rotation)
    return Table(**slots)


def shape(kind: str, *, page=0, origin=(100.0, 100.0), tool=0, dash=1, color=(0, 0, 0, 255), width=2.0,
          rotation=None, **geometry) -> Table:
    """A shape record's payload: a ``line`` (``end``, ``start``, ``control``),
    a ``polygon`` (``points``) or an ``ellipse`` (``size``), about its origin."""
    if kind == "line":
        parts = {"s3": pair(*geometry["end"])}
        if "start" in geometry:
            parts["s0"] = pair(*geometry["start"])
        if "control" in geometry:
            parts["s1"] = pair(*geometry["control"])
        number, table = 1, Table(**parts)
    elif kind == "polygon":
        number, table = 2, Table(s0=Vector(pair(*p) for p in geometry["points"]))
    else:
        number, table = 3, Table(s1=pair(*geometry["size"]))
    slots = {"s0": place(PAGES, page), "s1": pair(*origin), "s4": u8(number), "s5": table, "s6": u8(tool),
             "s7": u8(dash), "s9": rgba(*color), "s10": f32(width)}
    if rotation is not None:
        slots["s2"] = f32(rotation)
    return Table(**slots)


def picture(digest: bytes, *, page=0, corner=(50.0, 50.0), size=(120.0, 90.0), mime="image/png", logical=None,
            crop=None, rotation=None, key=None, scale=None, flip_h=False, flip_v=False, caption=False) -> Table:
    """A picture's media record payload: its asset by content hash (64
    bytes), its frame by its turned top-left corner and its size before
    ``scale``; its flips and whether its caption shows, each a byte."""
    asset = Table(s0=Inline(digest), s2=mime)
    content = {"s0": asset}
    if logical is not None:
        content["s1"] = pair(*logical)
    slots = {"s2": place(PAGES, page), "s3": pair(*corner), "s6": pair(*size), "s10": Table(**content)}
    if crop is not None:
        slots["s11"] = rect(*crop)
    if rotation is not None:
        slots["s4"] = f32(rotation)
    if scale is not None:
        slots["s5"] = pair(*scale)
    if key is not None:
        slots["s9"] = u64(key)
    for slot, on in ((8, caption), (16, flip_h), (17, flip_v)):
        if on:
            slots[f"s{slot}"] = u8(1)
    return Table(**slots)


def sticky(*, page=0, corner=(300.0, 300.0), size=(150.0, 130.0), color=(255, 244, 142, 255)) -> Table:
    return Table(s2=place(PAGES, page), s3=pair(*corner), s6=pair(*size), s15=Table(s4=rgba(*color)))


def box(*, page=0, corner=(60.0, 400.0), size=(200.0, 40.0), rotation=None) -> Table:
    """A text box's media record payload: its text is the text records it owns."""
    slots = {"s2": place(PAGES, page), "s3": pair(*corner), "s6": pair(*size)}
    if rotation is not None:
        slots["s4"] = f32(rotation)
    return Table(**slots)


def text(string: str, owner=None, *, at=None) -> Table:
    """A text record: ``string``, typed in the block ``owner`` (its text
    field; the document's text without one), inserted ``at`` a position
    ``(anchor, count)`` when given."""
    slots = {"s1": string}
    if owner is not None:
        slots["s2"] = ident(*owner)
    if at is not None:
        slots["s0"] = place(*at)
    return Table(**slots)


def anchor(at: tuple, count: int) -> Table:
    """A text anchor at ``count`` UTF-16 units past the anchor ``at``."""
    return Table(s0=place(at, count))


def style(start: tuple, end: tuple, *, bold=None, italic=None, strike=None, size=None, color=None) -> Table:
    """A character style over the range between two positions, ``(anchor,
    count)`` each."""
    slots = {"s0": place(*start), "s1": place(*end)}
    for slot, value in ((3, bold), (4, italic), (13, strike)):
        if value is not None:
            slots[f"s{slot}"] = Table(s0=u8(int(value)))
    if size is not None:
        slots["s8"] = Table(s0=f32(size))
    if color is not None:
        slots["s9"] = Table(s0=rgba(*color))
    return Table(**slots)


def paragraph(start: tuple, end: tuple, *, decoration=None, level=None) -> Table:
    slots = {"s0": place(*start), "s1": place(*end)}
    if decoration is not None:
        slots["s5"] = Table(s0=u8(decoration))
    if level is not None:
        slots["s2"] = Table(s0=u8(level))
    return Table(**slots)


def ids(*keys) -> Table:
    """A group's members."""
    return Table(s0=Vector(ident(*key) for key in keys))


def _page(n): return place(PAGES, n)
def _xy(p): return pair(*p)
def _rect(r): return rect(*r)
def _paper(color): return Table(s4=rgba(*color))


def erase(*keys, restored=(), pages=(), restored_pages=()) -> Table:
    """An erase record: the records it deletes and those it restores, then
    the pages it deletes and restores, each by its number."""
    slots = {}
    for slot, items in ((0, [ident(*key) for key in keys]), (1, [ident(*key) for key in restored]),
                        (2, [_page(n) for n in pages]), (3, [_page(n) for n in restored_pages])):
        if items:
            slots[f"s{slot}"] = Vector(items)
    return Table(**slots)


def _set(pack):
    """How a table of one value (SetFloat, SetSize, SetRect, SetPaper) is
    packed: ``()`` packs one of none, which clears the value."""
    return lambda value: Table() if value == () else Table(s0=pack(value))


def _table(slots: dict, given: tuple) -> Table:
    """A table of ``slots`` and of what ``given`` sets, ``(slot, value,
    pack)`` each, a value of None left out."""
    for slot, value, pack in given:
        if value is not None:
            slots[f"s{slot}"] = pack(value)
    return Table(**slots)


def edit(*blocks, page=None, corner=None, rotation=None, scale=None, size=None, key=None, crop=None, color=None,
         caption=None, flip_h=None, flip_v=None, rounded=None, locked=None) -> Table:
    """An edit of ``blocks`` (ModifyBlock): what it sets, each in its slot;
    the turn, scale, crop and a sticky's paper (its ``color``) each in a
    table of one value."""
    return _table({"s0": Vector(ident(*block) for block in blocks)}, (
        (1, rounded, u8), (2, page, _page), (3, corner, _xy), (4, rotation, _set(f32)), (5, scale, _set(_xy)),
        (6, size, _xy), (8, caption, u8), (9, key, u64), (12, crop, _set(_rect)), (13, color, _set(_paper)),
        (14, flip_h, u8), (15, flip_v, u8), (17, locked, u8)))


def move(*targets) -> Table:
    """Moves (ModifyPositions), one ``position`` per target."""
    return Table(s0=Vector(targets))


def position(target, *, page=None, origin=None, rotation=None, scale=None, key=None) -> Table:
    """One target's move (a ModifyPosition): what it sets, each in its slot;
    the turn and scale each in a table of one value."""
    return _table({"s0": ident(*target)}, ((1, page, _page), (2, origin, _xy), (3, rotation, _set(f32)),
                                            (4, scale, _set(_xy)), (5, key, u64)))


def bundle(*records, created_ms: int = 1_777_193_637_206) -> bytes:
    """A noteBundle of ``records``: ``(kind, (id), payload)``, after the
    page list (``PAGES``)."""
    tables = [record(3, PAGES, Table(s2=u32(2)))]
    tables += [record(kind, key, payload) for kind, key, payload in records]
    return dumps(Table(s4=u64(created_ms), s6=Vector(tables)))


def ntb_zip(note_bundle: bytes, assets: dict | None = None) -> bytes:
    """An .ntb: version, manifest.json, noteBundle and ``assets`` (``{name:
    bytes}`` under assets/), stored as Notability stores them."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("version", b"1")
        zf.writestr("noteBundle", note_bundle)
        for name, data in (assets or {}).items():
            zf.writestr(f"assets/{name}", data)
        zf.writestr("manifest.json", b'{\n  "appVersion" : "16.10"\n}')
    return out.getvalue()


def turned_corner(x0: float, y0: float, w: float, h: float, turn: float) -> tuple[float, float]:
    """Where a frame's top-left corner lies once the frame is turned about
    its centre: what a media record stores."""
    cx, cy = x0 + w / 2, y0 + h / 2
    return (cx - w / 2 * math.cos(turn) + h / 2 * math.sin(turn), cy - w / 2 * math.sin(turn) - h / 2 * math.cos(turn))
