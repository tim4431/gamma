"""Notability's "NTB" export, an ``.ntb`` (docs/research/notability-format.md
".ntb"), read into the ``Note`` a .note reads into (gamma/notability.py):
the same pages, ink, shapes, typed text, pictures and sticky notes, so the
rest of the import (gamma/notability_map.py, gamma/notability_import.py)
treats the two alike.

An .ntb is a zip of ``noteBundle``, a FlatBuffers table of the note's
records, and the pictures and PDFs they name under ``assets/`` by content
hash. The records are a log (``_live``): one is current at its last
version, in the place of its first, and one an erase record names is gone,
with the text it holds. The kinds read here: 1 the document, 3 the page
list (nothing drawn), 7 a text anchor, 8 text, 12 character and 13
paragraph styles, 15 ink, 18 a shape, 20 an ink group's members, 22 a
picture, a sticky note or a text box, 25 erased records. Strokes, shapes
and groups are drawn in the order of their records, a group in the place
of its first member.

A record's coordinates are its page's, x from the paper's left edge. Each
item is placed in the .note's document units by its page's frame
(``notability_map.frames``, through ``_Pages``): x less the inset, y down
the strip of pages, the PDF's pages first and then plain pages as tall as
the document says. ``pdf_pages`` holds the PDF's page sizes, which give a
PDF page its slot; without them its items are placed as on paper, which is
enough for a preview.

Everything read is untrusted: every position is checked before it is read
and every offset before it is followed (``_Flat``), damage reads as a
missing value or a count of what was left out, and the caps of a .note
(gamma/notability.py) bound what is read.
"""

import math
import posixpath
import struct
import unicodedata
from array import array
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import notability as nb
from . import notability_map as nmap

DOCUMENT, PAGES, ANCHOR, TEXT, STYLE, PARAGRAPH = 1, 3, 7, 8, 12, 13
INK, SHAPE, GROUP, MEDIA, ERASE = 15, 18, 20, 22, 25
KNOWN = frozenset({DOCUMENT, PAGES, ANCHOR, TEXT, STYLE, PARAGRAPH, INK, SHAPE, GROUP, MEDIA, ERASE})
MAX_STYLED = 20_000_000          # characters the styles of one note may touch, every range together
_TOOLS = {0: nb.STYLE_PEN, 1: nb.STYLE_PENCIL, 2: nb.STYLE_HIGHLIGHTER}  # a record's tool byte
_DASHES = {2: "dashed", 3: "dotted"}                                    # its line style byte
_RULINGS = {0: "ruled", 1: "dots", 2: "grid"}                           # a paper's pattern byte
_CUBIC, _LINEAR = 3, 7           # an ink path's kinds
_UPRIGHT = (1.0, 1.0, math.pi / 2, 1.0, 0.0)  # a node with nothing measured: width, force, altitude, azimuth x, y
_A4 = 841.89 / 595.28
_DEFAULT_WIDTH = 565.0
_SIZES = {"B": 1, "H": 2, "I": 4, "i": 4, "Q": 8, "e": 2, "f": 4}
_LOOK = ("bold", "italic", "strike", "size", "rgba", "list_style", "level")  # a run's style, as a tuple


class _Flat:
    """A FlatBuffers buffer read behind bounds checks: a value outside it, a
    table without a whole vtable, an offset that leads outside it, all read
    as None. An offset only leads forward, so no chain of them loops."""

    def __init__(self, data: bytes):
        self.data, self.size = data, len(data)

    def number(self, letter: str, pos):
        """The little-endian value (a ``struct`` letter) at ``pos``; None
        outside the buffer, and for a float that is not finite."""
        if pos is None or pos < 0 or pos + _SIZES[letter] > self.size:
            return None
        value = struct.unpack_from("<" + letter, self.data, pos)[0]
        return value if not isinstance(value, float) or math.isfinite(value) else None

    def table(self, pos) -> int | None:
        """``pos`` when a table lies there: its vtable, at ``pos`` less the
        int32 there, inside the buffer, even and at least 4 bytes long."""
        back = self.number("i", pos)
        size = self.number("H", None if back is None else pos - back)
        if size is None or size < 4 or size % 2 or pos - back + size > self.size:
            return None
        return pos

    def field(self, table, slot: int) -> int | None:
        """Where field ``slot`` of a checked table lies; None when absent."""
        if table is None:
            return None
        vtable = table - self.number("i", table)
        if 6 + 2 * slot > self.number("H", vtable):
            return None
        offset = self.number("H", vtable + 4 + 2 * slot)
        return table + offset if offset and table + offset < self.size else None

    def follow(self, pos) -> int | None:
        """Where the offset at ``pos`` leads."""
        offset = self.number("I", pos)
        return pos + offset if offset is not None and pos + offset < self.size else None

    def sub(self, table, slot: int) -> int | None:
        """The table field ``slot`` leads to."""
        return self.table(self.follow(self.field(table, slot)))

    def length(self, pos, width: int) -> int | None:
        """The length of the vector at ``pos``, when its ``width``-byte
        elements all lie inside the buffer."""
        n = self.number("I", pos)
        return n if n is not None and pos + 4 + n * width <= self.size else None

    def blob(self, table, slot: int) -> bytes | None:
        """A byte vector, or a string's bytes."""
        pos = self.follow(self.field(table, slot))
        n = self.length(pos, 1)
        return self.data[pos + 4:pos + 4 + n] if n is not None else None

    def name(self, table, slot: int) -> str | None:
        """A short string (at most MAX_NAME bytes) that decodes."""
        raw = self.blob(table, slot)
        if raw is None or len(raw) > nb.MAX_NAME:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def tables(self, table, slot: int, limit: int) -> tuple[list[int], int]:
        """The tables a vector of offsets leads to, at most ``limit`` of them,
        and how many it lists past those."""
        pos = self.follow(self.field(table, slot))
        n = self.length(pos, 4) or 0
        found = (self.table(self.follow(pos + 4 + 4 * i)) for i in range(min(n, limit)))
        return [t for t in found if t is not None], max(0, n - limit)

    def pairs(self, table, slot: int, limit: int) -> list[tuple[float, float]] | None:
        """A vector of (float32, float32) structs; None when it does not read,
        holds a value that is not finite or more than ``limit`` pairs."""
        pos = self.follow(self.field(table, slot))
        n = self.length(pos, 8)
        if n is None or n > limit:
            return None
        values = struct.unpack_from(f"<{2 * n}f", self.data, pos + 4)
        return list(zip(values[0::2], values[1::2])) if all(math.isfinite(v) for v in values) else None

    def pair(self, table, slot: int) -> tuple[float, float] | None:
        pos = self.field(table, slot)
        x, y = self.number("f", pos), self.number("f", None if pos is None else pos + 4)
        return (x, y) if x is not None and y is not None else None

    def ident(self, table, slot: int) -> tuple[int, int] | None:
        """A record's id: an inline struct of two uint32."""
        pos = self.field(table, slot)
        a, b = self.number("I", pos), self.number("I", None if pos is None else pos + 4)
        return (a, b) if a is not None and b is not None else None

    def idents(self, table, slot: int) -> list[tuple[int, int]]:
        """A vector of ids, at most MAX_RECORDS of them."""
        pos = self.follow(self.field(table, slot))
        n = min(self.length(pos, 8) or 0, nb.MAX_RECORDS)
        values = struct.unpack_from(f"<{2 * n}I", self.data, pos + 4) if n else ()
        return list(zip(values[0::2], values[1::2]))

    def place(self, table, slot: int) -> tuple[tuple[int, int] | None, int | None]:
        """A position: an inline struct of an id, the record it counts from
        (the page list, a text), and a uint32 count (a page, an offset)."""
        pos = self.field(table, slot)
        return self.ident(table, slot), self.number("I", None if pos is None else pos + 8)

    def rgba(self, pos) -> tuple | None:
        """Four colour bytes, as a .note's curvescolors hold them."""
        return tuple(self.data[pos:pos + 4]) if pos is not None and pos + 4 <= self.size else None


@dataclass(slots=True)
class _Record:
    order: int                     # its place among the bundle's records
    ident: tuple | None
    kind: int
    payload: int


@dataclass
class _Store:
    """One text: the document's or a text box's."""
    parts: list = field(default_factory=list)
    units: int = 0                 # its length in UTF-16 units
    styles: list = field(default_factory=list)      # (start, end, patch), in UTF-16 units
    paragraphs: list = field(default_factory=list)


def pdf_assets(members: nb.Members) -> list[str]:
    """The bundle's PDFs as member keys: those under ``assets/``, and
    top-level ones named by a 64-digit hash, as some app versions keep
    them."""
    out = []
    for key in members.entries:
        stem, ext = posixpath.splitext(key.lower())
        if ext == ".pdf" and ((stem.startswith("assets/") and stem.count("/") == 1)
                              or (len(stem) == 64 and all(c in "0123456789abcdef" for c in stem))):
            out.append(key)
    return sorted(out)


def read_ntb(members: nb.Members, data: bytes, *, path: str, digest: str = "", ink: bool = True,
             pdf_pages: dict | None = None) -> nb.Note:
    """The note whose files are ``members``. ``data`` is its noteBundle,
    which the caller read and hashed (``digest``); ``path`` is its bundle
    path (the title's fallback); ``pdf_pages`` the display sizes of its PDF's
    pages by file name, as the import reads them. With ``ink`` False (a
    preview), its ink and shapes are not read. NoteError when it is no
    note: past MAX_SESSION_BYTES, or no root table holding its records."""
    if len(data) > nb.MAX_SESSION_BYTES:
        raise nb.NoteError(f"its noteBundle is over {nb.MAX_SESSION_BYTES >> 20} MB")
    flat = _Flat(data)
    root = flat.table(flat.number("I", 0))
    if root is None or flat.field(root, 6) is None:
        raise nb.NoteError("its noteBundle holds no Notability note")
    return _Reader(flat, root, members, path, digest, pdf_pages or {}).read(ink)


class _Reader:
    """One bundle's reading: its buffer, its files, the note made of it and
    the counts of what was read and left out."""

    def __init__(self, flat: _Flat, root: int, members: nb.Members, path: str, digest: str, pdf_pages: dict):
        self.flat, self.root, self.members, self.path, self.pdf_pages = flat, root, members, path, pdf_pages
        self.note = nb.Note(title="", digest=digest)
        self.curve_count = self.shape_count = 0
        self.text_left = nb.MAX_NOTE_TEXT   # characters of typed text still to read
        self.runs_left = nb.MAX_NOTE_RUNS   # formatting runs still to make
        self.styled_left = MAX_STYLED       # characters the styles may still touch
        self.font_size = None               # the document's, the default of its text
        self.pages = None                   # _Pages, once the document's paper is read
        self.drawn: list = []               # (z key, Curve | Shape), as gamma/notability.py's
        self.assets: dict | None = None     # members by their name's stem, once a picture asks

    def read(self, ink: bool) -> nb.Note:
        flat, note = self.flat, self.note
        tables, past = flat.tables(self.root, 6, nb.MAX_RECORDS)
        if past:
            note.left_out["records"] += past
        records = []
        for order, table in enumerate(tables):
            kind, payload = flat.number("B", flat.field(table, 4)), flat.sub(table, 5)
            if kind is not None and payload is not None:
                records.append(_Record(order, flat.ident(table, 0), kind, payload))
        live = _live(flat, records)
        unknown = sum(1 for r in live if r.kind not in KNOWN)
        if unknown:
            note.left_out["record"] += unknown
        document = next((r for r in reversed(live) if r.kind == DOCUMENT), None)
        document_id = document.ident if document is not None and document.ident is not None else (0, 0)
        title = self._document(document.payload) if document is not None else ""
        note.title = nb.note_title(title, None, None, self.path)
        created = flat.number("Q", flat.field(self.root, 4))
        if created and created < 10 ** 14:  # milliseconds, before the year 5000
            note.created = datetime.fromtimestamp(created / 1000, UTC)
        self._pdfs()
        self.pages = _Pages(note, self.pdf_pages)
        if ink:
            self._ink(live)
        stores = self._texts(live, document_id)
        boxes = self._media(live, stores)
        main = stores.pop(document_id, None)
        note.flow = self._runs(main) if main is not None else []
        lost = sum(1 for owner, store in stores.items() if owner not in boxes and store.units)
        if lost:
            note.left_out["lost text"] += lost
        return note

    # --- the document: its page, paper and PDF ------------------------------------

    def _document(self, payload: int) -> str:
        """The page width W and height, the paper and the default font size
        from the document record; returns its title."""
        flat, note = self.flat, self.note
        layout = flat.sub(flat.sub(payload, 1), 0)
        size = flat.pair(layout, 3)
        width = size[0] if size and 16 <= size[0] <= 10_000 else _DEFAULT_WIDTH
        height = size[1] if size and 0.1 * width <= size[1] <= 10 * width else None
        note.width, note.page_height = width, height
        ratio = (height or width * 1.3125) / width
        spec = nb.PaperSpec(size="a4" if min(abs(ratio - _A4), abs(1 / ratio - _A4)) < 0.005 else "letter",
                            landscape=ratio < 1)
        style = flat.sub(layout, 0)
        legacy, color = flat.number("B", flat.field(style, 5)), flat.rgba(flat.field(style, 4))
        if legacy is not None:
            spec.legacy = legacy
        elif color is not None:
            spec.color = "#%02x%02x%02x" % color[:3]
        pattern = flat.number("B", flat.field(style, 0))
        if pattern in _RULINGS:
            spacing = flat.number("f", flat.field(style, 1))
            inches = spacing / (width / 8.5) if spacing and spacing > 0 else 0.25  # in inches of an 8.5 in page
            if inches <= 10:
                spec.lines, spec.ruling = inches, _RULINGS[pattern]
        note.paper = spec
        font = flat.number("f", flat.field(payload, 5))
        self.font_size = font if font and font > 0 else None
        return flat.name(flat.sub(payload, 0), 0) or ""

    def _pdfs(self) -> None:
        """The PDF whose pages, all of them in order, are the note's first:
        the one under ``assets/``. Several are left out with a warning, since
        the bundle does not say which page is which."""
        note = self.note
        found = pdf_assets(self.members)
        if len(found) > 1:
            note.warnings.append(f"The note holds {len(found)} PDFs and not where their pages go: "
                                 "its pages are blank paper")
            return
        if found:
            name = posixpath.basename(found[0])
            note.pdfs[name] = nb.PdfFile(name, found[0], 2)
            count = len(self.pdf_pages.get(name) or ()) or 1
            note.layout = [nb.LayoutEntry(name, k) for k in range(1, min(count, nb.MAX_NOTE_PAGES) + 1)]

    def _page(self, payload: int, slot: int) -> int | None:
        """A record's page; None when it does not read."""
        return self.flat.place(payload, slot)[1]

    def _far(self, page: int | None) -> bool:
        """Whether a page lies past MAX_NOTE_PAGES, which leaves its record
        out (counted, "page")."""
        if page is not None and page >= nb.MAX_NOTE_PAGES:
            self.note.left_out["page"] += 1
            return True
        return False

    def _placed(self, points: list, origin: tuple, turn: float, page: int) -> list | None:
        """Page points turned ``turn`` radians about ``origin``, then in
        document units; None when one is not finite or lies past
        COORD_LIMIT."""
        c, s = math.cos(turn), math.sin(turn)
        ox, oy = origin
        left, top = self.pages.inset, self.pages.top(page)
        out = []
        for x, y in points:
            dx, dy = x - ox, y - oy
            px, py = ox + dx * c - dy * s - left, top + oy + dx * s + dy * c
            if not (math.isfinite(px) and math.isfinite(py)) or max(abs(px), abs(py)) > nb.COORD_LIMIT:
                return None
            out.append((px, py))
        return out

    # --- ink and shapes -------------------------------------------------------------

    def _ink(self, live: list) -> None:
        """Every ink and shape record into the note in their order, a
        group's members together at its first member's place: the curves
        in one hash, the shapes, each with its place (``z``)."""
        flat, note = self.flat, self.note
        group_of = {}
        for r in live:
            if r.kind == GROUP:
                for member in flat.idents(r.payload, 0):
                    group_of.setdefault(member, r.order)
        self.hash = nb.InkHash(array("f"), array("f"), array("f"), array("f"), array("f"), nb.IDENTITY, 1.0)
        place, groups, members = 0, {}, {}
        for r in live:
            if r.kind not in (INK, SHAPE):
                continue
            group = group_of.get(r.ident)
            if group is None:
                key, place = (place,), place + 1
            else:
                if group not in groups:
                    groups[group], place = place, place + 1
                members[group] = members.get(group, -1) + 1
                key = (groups[group], members[group])
            if r.kind == INK:
                self._stroke(r.payload, key)
            else:
                self._shape(r.payload, key, grouped=group is not None)
        nb.place_drawn(note, self.drawn)  # stable: one record's curves stay in order
        self.drawn = []

    def _stroke(self, payload: int, key: tuple) -> None:
        """An ink record's pieces as curves: its path (``_decode``) scaled
        about its origin, turned about it and placed on its page, its width
        scaled as its points are."""
        flat, note, ink = self.flat, self.note, self.hash
        page, origin, width = self._page(payload, 0), flat.pair(payload, 1), flat.number("f", flat.field(payload, 8))
        if self._far(page):
            return
        scale = flat.pair(payload, 3) or (1.0, 1.0)
        if not (abs(scale[0]) > 1e-9 and abs(scale[1]) > 1e-9):
            scale = (1.0, 1.0)
        data = flat.blob(payload, 9)
        pieces = _decode(data, origin, scale) if origin is not None and data is not None else None
        if page is None or pieces is None or width is None or width <= 0:
            note.left_out["stroke"] += 1
            return
        turn = flat.number("f", flat.field(payload, 2)) or 0.0
        rgba = flat.rgba(flat.field(payload, 7)) or (0, 0, 0, 255)
        style = _TOOLS.get(flat.number("B", flat.field(payload, 4)), nb.STYLE_PEN)
        dash = _DASHES.get(flat.number("B", flat.field(payload, 5)))
        base = width * (abs(scale[0]) + abs(scale[1])) / 2
        for points, nodes, bezier in pieces:
            if self.curve_count >= nb.MAX_CURVES:
                note.left_out["curves"] += 1
                continue
            placed = self._placed(points, origin, turn, page)
            if placed is None or len(placed) > nb.MAX_CURVE_POINTS:
                note.left_out["stroke"] += 1
                continue
            p, f = len(ink.points) // 2, len(ink.fractional)
            for x, y in placed:
                ink.points.extend((x, y))
            for fraction, force, altitude, ax, ay in nodes or [_UPRIGHT] * len(placed):
                ink.fractional.append(fraction)
                ink.force.append(force)
                ink.altitude.append(altitude)
                ink.azimuth.extend((ax, ay))
            self.drawn.append((key, nb.Curve(ink, p, len(placed), f, bezier, base, rgba, style, dash)))
            self.curve_count += 1

    def _shape(self, payload: int, key: tuple, *, grouped: bool) -> None:
        """A shape record about its origin: a line (with an optional
        quadratic control point), a polygon named by its corners as
        Notability names one, or an ellipse in its box."""
        flat, note = self.flat, self.note
        if self.shape_count >= nb.MAX_SHAPES:
            note.left_out["shapes limit"] += 1
            return
        page, origin, width = self._page(payload, 0), flat.pair(payload, 1), flat.number("f", flat.field(payload, 10))
        if self._far(page):
            return
        geometry, kind = flat.sub(payload, 5), flat.number("B", flat.field(payload, 4))
        bend = None
        if kind == 1:
            end = flat.pair(geometry, 3)
            relative = [flat.pair(geometry, 0) or (0.0, 0.0), end] if end is not None else None
            bend, name = flat.pair(geometry, 1), "line"
        elif kind == 2:
            relative = flat.pairs(geometry, 0, nb.MAX_CURVE_POINTS)
            if relative and len(relative) > 3 and relative[0] == relative[-1]:
                relative = relative[:-1]
            name = {3: "triangle", 4: "rectangle"}.get(len(relative or ()), "polygon")
        elif kind == 3:
            box = flat.pair(geometry, 1)
            relative = [(0.0, box[1]), box, (box[0], 0.0), (0.0, 0.0)] if box else None  # as a .note's corners
            name = "ellipse"
        else:
            note.left_out["shape"] += 1
            return
        if page is None or origin is None or not relative or width is None or width <= 0:
            note.left_out["stroke"] += 1
            return
        turn = flat.number("f", flat.field(payload, 2)) or 0.0
        points = self._placed([(origin[0] + x, origin[1] + y) for x, y in relative], origin, turn, page)
        control = None
        if bend is not None:
            control = (self._placed([(origin[0] + bend[0], origin[1] + bend[1])], origin, turn, page) or [None])[0]
        if points is None or (bend is not None and control is None):
            note.left_out["stroke"] += 1
            return
        shape = nb.Shape(kind=name, points=points, control=control, arrow=False, width=width,
                         rgba=_unit(flat.rgba(flat.field(payload, 9)) or (0, 0, 0, 255)), fill=None,
                         style=_TOOLS.get(flat.number("B", flat.field(payload, 6)), nb.STYLE_PEN),
                         dash=_DASHES.get(flat.number("B", flat.field(payload, 7))), grouped=grouped)
        self.drawn.append((key, shape))
        self.shape_count += 1

    # --- pictures, sticky notes, text boxes --------------------------------------------

    def _asset(self, digest: bytes, mime: str) -> str | None:
        """The member a picture names by its content hash: the file named by
        the hash, or by its first 64 hex digits, whatever its extension."""
        if self.assets is None:
            self.assets = {}
            for key in self.members.entries:
                self.assets.setdefault(posixpath.splitext(posixpath.basename(key))[0].lower(), key)
        name = digest.hex()
        key = self.assets.get(name) or self.assets.get(name[:64])
        if key is None:
            self.note.name_missing(f"assets/{name}.{mime.rsplit('/', 1)[-1]}")
        return key

    def _media(self, live: list, stores: dict) -> set:
        """Pictures, sticky notes and text boxes (a media record whose id
        holds a text) by their sort keys; returns the text boxes' ids. A
        frame is stored by its turned top-left corner: its unturned one is
        found from it."""
        flat, note = self.flat, self.note
        found, boxes = [], set()
        for r in live:
            if r.kind != MEDIA:
                continue
            if len(found) >= nb.MAX_MEDIA:
                note.left_out["media limit"] += 1
                continue
            p = r.payload
            page, corner, size = self._page(p, 2), flat.pair(p, 3), flat.pair(p, 6)
            if self._far(page):
                continue
            if page is None or corner is None or size is None or not (size[0] > 0 and size[1] > 0):
                note.left_out["object"] += 1
                continue
            turn = flat.number("f", flat.field(p, 4)) or 0.0
            (w, h), c, s = size, math.cos(turn), math.sin(turn)
            x = corner[0] - w / 2 + w / 2 * c - h / 2 * s - self.pages.inset
            y = self.pages.top(page) + corner[1] - h / 2 + w / 2 * s + h / 2 * c
            if max(abs(v) for v in (x, y, w, h)) > nb.COORD_LIMIT:
                note.left_out["object"] += 1
                continue
            media = nb.Media(kind="text", x=x, y=y, w=w, h=h, rotation=turn,
                             z=flat.number("Q", flat.field(p, 9)) or r.order)
            content = flat.sub(p, 10)
            asset, paper = flat.sub(content, 0), flat.sub(p, 15)
            if r.ident in stores:
                boxes.add(r.ident)
                media.runs = self._runs(stores[r.ident])
            elif asset is not None:
                mime = flat.name(asset, 2) or "image/jpeg"
                digest = flat.field(asset, 0)
                if not mime.startswith("image/") or digest is None or digest + 64 > flat.size:
                    note.left_out["media"] += 1
                    note.classes[mime] += 1
                    continue
                media.kind, media.picture = "picture", self._asset(flat.data[digest:digest + 64], mime)
                at = flat.field(p, 11)
                crop = [flat.number("f", at + 4 * i) for i in range(4)] if at is not None else None
                media.crop = tuple(crop) if crop and None not in crop and crop[2] > 0 and crop[3] > 0 else None
                logical = flat.pair(content, 1)
                media.crop_size = logical if logical and logical[0] > 0 and logical[1] > 0 else None
            elif paper is not None:
                color = flat.rgba(flat.field(paper, 4))
                media.kind = "sticky"
                media.background = _unit(color) if color else nb.STICKY_COLOR
            else:
                note.left_out["media"] += 1
                note.classes["unknown"] += 1
                continue
            found.append(media)
        note.media = sorted(found, key=lambda media: media.z)  # stable: one key's stay in order
        return boxes

    # --- text -----------------------------------------------------------------------------

    def _texts(self, live: list, document: tuple) -> dict:
        """The note's texts by their owner's id, the document's or a text
        box's: each text record appended to its owner's, an anchor where it
        starts; then the anchors that mark a text's last character; then the
        styles of the ranges between anchors."""
        flat, note = self.flat, self.note
        stores, anchors, ends = {}, {document: (document, 0)}, set()
        for r in live:
            if r.kind != TEXT or r.ident is None:
                continue
            raw = flat.blob(r.payload, 1)
            if raw is None:
                continue
            text = raw.decode("utf-8", "replace") if len(raw) <= 4 * self.text_left else ""
            if len(text) > self.text_left or (raw and not text):
                note.left_out["typed text"] += 1
                text = text[:self.text_left]
            owner = flat.ident(r.payload, 0) or document
            store = stores.setdefault(owner, _Store())
            anchors[r.ident] = (owner, store.units)
            store.parts.append(text)
            store.units += nb.utf16_units(text)
            self.text_left -= len(text)
        for r in live:
            if r.kind == ANCHOR and r.ident is not None:
                at = self._position(r.payload, 0, anchors)
                if at is not None:
                    anchors[r.ident] = at
                    ends.add(r.ident)
        for r in live:
            if r.kind not in (STYLE, PARAGRAPH):
                continue
            start, end = self._position(r.payload, 0, anchors), self._position(r.payload, 1, anchors)
            if end is not None and flat.ident(r.payload, 1) in ends:
                end = (end[0], end[1] + 1)  # an end anchor marks the last character, which the range holds
            owner = (end or start or (None,))[0]
            store = stores.get(owner)
            if store is None or (start is not None and start[0] not in (owner, document)):
                continue
            patch = _style(flat, r.payload) if r.kind == STYLE else _paragraph(flat, r.payload)
            begin = start[1] if start is not None and start[0] == owner else 0  # the document's start opens a box's text
            (store.styles if r.kind == STYLE else store.paragraphs).append(
                (begin, end[1] if end is not None else math.inf, patch))
        return stores

    def _position(self, payload: int, slot: int, anchors: dict) -> tuple | None:
        """``(owner, UTF-16 offset)`` of a position: an anchor and a count
        past it."""
        ident, count = self.flat.place(payload, slot)
        anchor = anchors.get(ident)
        return (anchor[0], anchor[1] + count) if anchor is not None and count is not None else None

    def _runs(self, store: _Store) -> list[nb.Run]:
        """A text as runs: its character styles over the default (the
        document's size), its paragraph styles from after the newline that
        opens each range, its lists numbered as Notability numbers them, the
        characters of one style merged into a run. The styles touch at most
        the note's MAX_STYLED characters, and every run spends one of its
        MAX_NOTE_RUNS: past either, the rest keeps its words only."""
        text = "".join(store.parts)
        if not text:
            return []
        n = len(text)
        bounds = nb.utf16_bounds(text)
        at = lambda units: nb.char_at(bounds, min(units, store.units), n)  # noqa: E731
        base = (False, False, False, self.font_size, None, 0, 0)
        looks, index = [base], {base: 0}
        ids = array("I", [0]) * n

        def apply(begin: int, finish: int, patch: dict) -> bool:
            if finish - begin > self.styled_left:
                return False
            self.styled_left -= max(0, finish - begin)
            changed = {}
            for i in range(begin, finish):
                old = ids[i]
                new = changed.get(old)
                if new is None:
                    look = tuple(patch.get(k, v) for k, v in zip(_LOOK, looks[old]))
                    new = changed[old] = index.setdefault(look, len(looks))
                    if new == len(looks):
                        looks.append(look)
                ids[i] = new
            return True

        complete = all(apply(at(start), at(end), patch) for start, end, patch in store.styles)
        complete = complete and all(apply(_past_newline(text, at(start)), _past_newline(text, at(end)), patch)
                                    for start, end, patch in store.paragraphs)
        if not complete:
            self.note.left_out["formatting"] += 1
        numbers = _numbers(text, ids, looks)
        runs, start = [], 0
        for i in range(1, n + 1):
            if i < n and ids[i] == ids[start] and numbers[i] == numbers[start]:
                continue
            if self.runs_left <= 0:
                self.note.left_out["formatting"] += 1
                runs.append(nb.Run(_clean(text[start:])))
                break
            self.runs_left -= 1
            look = dict(zip(_LOOK, looks[ids[start]]))
            runs.append(nb.Run(_clean(text[start:i]), number=numbers[start] or 1, **look))
            start = i
        return [run for run in runs if run.text]


def _unit(color: tuple) -> tuple:
    """Four colour bytes as 0..1, a .note's floats."""
    return tuple(v / 255 for v in color)


def _style(flat: _Flat, payload: int) -> dict:
    """A character style record's patch: what it sets, each in a table of
    one value (a flag whose table holds none is off)."""
    patch = {}
    for slot, key in ((3, "bold"), (4, "italic"), (11, "strike")):
        holder = flat.sub(payload, slot)
        if holder is not None:
            patch[key] = bool(flat.number("B", flat.field(holder, 0)))
    size = flat.number("f", flat.field(flat.sub(payload, 8), 0))
    if size is not None and size > 0:
        patch["size"] = size
    color = flat.rgba(flat.field(flat.sub(payload, 9), 0))
    if color is not None:
        patch["rgba"] = _unit(color)
    return patch


def _paragraph(flat: _Flat, payload: int) -> dict:
    """A paragraph style record's patch: its list (1 bullet, 2 numbered, 3
    checklist) and its indent level."""
    patch = {}
    style = flat.number("B", flat.field(flat.sub(payload, 5), 0))
    if style is not None:
        patch["list_style"] = style if style in (1, 2, 3) else 0
    level = flat.number("B", flat.field(flat.sub(payload, 2), 0))
    if level is not None:
        patch["level"] = min(level, 32)
    return patch


def _past_newline(text: str, i: int) -> int:
    """A paragraph range runs from the newline before the paragraph: the
    paragraph starts after it."""
    return i + 1 if i < len(text) and text[i] == "\n" else i


def _numbers(text: str, ids: array, looks: list) -> list[int]:
    """Each character's list number, as Notability numbers a list's
    paragraphs, bullets too: per indent level, a level's deeper ones from 1
    again under each of its paragraphs, and a list of another kind, or after
    a paragraph of none, from 1."""
    out = [0] * len(text)
    counters, current, start = [0] * 33, None, 0
    while start < len(text):
        end = text.find("\n", start)
        end = len(text) if end < 0 else end + 1
        list_style, level = looks[ids[start]][5:7]
        number = 0
        if list_style:
            if list_style != current:
                counters, current = [0] * 33, list_style
            counters[level] += 1
            counters[level + 1:] = [0] * (32 - level)
            number = counters[level]
        else:
            current = None
        out[start:end] = [number] * (end - start)
        start = end
    return out


def _clean(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\x00", ""))


def _live(flat: _Flat, records: list) -> list[_Record]:
    """The records still part of the note: those an erase record lists are
    gone, and the text records they own with them; a record listed again is
    current at its last version, in the place of its first."""
    erased = {ident for r in records if r.kind == ERASE for ident in flat.idents(r.payload, 0)}
    erased |= {r.ident for r in records if r.kind == TEXT and flat.ident(r.payload, 0) in erased}
    latest = {r.ident: r for r in records if r.ident is not None}
    out, seen = [], set()
    for r in records:
        if r.ident is None:
            out.append(r)
        elif r.ident not in erased and r.ident not in seen:
            seen.add(r.ident)
            out.append(latest[r.ident])
    return out


def _decode(data: bytes, origin: tuple, scale: tuple) -> list | None:
    """An ink path's pieces as ``(page points, nodes, bezier)``, the
    eraser's breaks between them. The path: a precision byte (0 half
    floats, 1 floats), the point count (uint16) and its kind (3 a cubic
    chain, 7 a polyline), then the first point; each point an offset from
    ``origin`` times ``scale``. A cubic chain's anchors each have a node,
    the width multiplier and force as half floats, then the altitude (a
    byte of π/2) and the azimuth (a byte of a turn): a node, then a command,
    0 a cubic segment's three points or 3 a move to the next piece's first,
    and the last anchor's node at the end. A polyline's commands are 6 a
    line and 7 a move, with no nodes. None when it does not read: an
    unknown kind or command, or cut short."""
    if len(data) < 4:
        return None
    precision, count, kind = data[0], int.from_bytes(data[1:3], "little"), data[3]
    if precision not in (0, 1) or not count or kind not in (_CUBIC, _LINEAR):
        return None
    width = 2 if precision == 0 else 4
    pair, size = struct.Struct("<ee" if precision == 0 else "<ff"), len(data)
    (ox, oy), (sx, sy) = origin, scale

    def point(at):
        x, y = pair.unpack_from(data, at)
        return ox + x * sx, oy + y * sy

    def node(at):
        fraction, force = struct.unpack_from("<ee", data, at)
        turn = data[at + 5] * math.tau / 256
        return (fraction if math.isfinite(fraction) and fraction >= 0 else 1.0,
                force if math.isfinite(force) else 1.0, data[at + 4] / 255 * math.pi / 2, math.cos(turn), math.sin(turn))

    at = 4
    if at + 2 * width > size:
        return None
    points, nodes, pieces = [point(at)], [], []
    at += 2 * width
    for _ in range(count - 1):
        if kind == _LINEAR:
            if at + 1 + 2 * width > size or data[at] not in (6, 7):
                return None
            if data[at] == 7:
                pieces.append((points, None, False))
                points = []
            points.append(point(at + 1))
            at += 1 + 2 * width
            continue
        if at + 7 > size or data[at + 6] not in (0, 3):
            return None
        nodes.append(node(at))
        steps = 3 if data[at + 6] == 0 else 1
        if at + 7 + 2 * width * steps > size:
            return None
        if steps == 1:
            pieces.append((points, nodes, True))
            points, nodes = [], []
        points += [point(at + 7 + 2 * width * i) for i in range(steps)]
        at += 7 + 2 * width * steps
    if kind == _CUBIC:
        if at + 6 > size:
            return None
        nodes.append(node(at))
    pieces.append((points, nodes if kind == _CUBIC else None, kind == _CUBIC))
    return pieces


class _Pages:
    """Where each page's coordinates land in document units: its frame's
    (``notability_map.frames``; a PDF page's top that of the PDF in its
    slot), and past the frames plain pages as tall as the paper."""

    def __init__(self, note: nb.Note, pdf_pages: dict):
        frames, _warnings = nmap.frames(note, pdf_pages, [])
        self.inset = note.width * nmap.INSET
        self.tops = [frame.oy for frame in frames]
        self.end = frames[-1].bottom
        self.height = nmap.paper_page(note)[2]

    def top(self, page: int) -> float:
        if page < len(self.tops):
            return self.tops[page]
        return self.end + (page - len(self.tops)) * self.height
