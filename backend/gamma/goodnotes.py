"""GoodNotes notebooks: finding them among an upload's files, and reading
one into the ``Notebook`` model (the format and its sources:
docs/research/goodnotes-onenote-import.md; what the import makes of a
notebook: docs/dev/import_export.md "GoodNotes notebooks").

A ``.goodnotes`` is a zip: ``index.events.pb``, the document's event
journal (its title, its paper templates, its pages, their order and the
pages deleted, its recordings); ``index.notes.pb``, the pages' content
members, ``notes/<N>``; ``index.attachments.pb`` and ``attachments/``, the
PDFs every page is bound to (a built-in paper is a one-page PDF too), the
pictures and the audio. A page's member is a stream of (metadata,
content) record pairs, one per element, in drawing order: ink, a shape's
fill, a picture, a text box. Every record is protobuf
(gamma/goodnotes_codec.py); a stroke's geometry is a tpl image inside
Apple's framed LZ4.

This module reads members only: no PDF parsing, no image decoding, no
storage and no database. Everything it reads is untrusted, so the reading
is bounded (the caps below), every value is type-checked where it is read,
and damage reads as a missing value, a warning or a count of what was left
out (``Notebook.left_out``, worded by ``left_out_reasons``), never as a
crash.

Geometry stays in GoodNotes' canvas units: points × 11/6 (1/132 in), from
the page's top-left corner, y down, each element's lasso offset already
added; gamma/goodnotes_map.py places it on a Gamma page.
"""

import hashlib
import itertools
import math
import posixpath
import re
import struct
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from . import goodnotes_codec as gc
from . import notability as nb
from . import rtf
from .import_zip import READ_ERRORS, ZipEntry

CANVAS_PT = 6 / 11               # points per canvas unit: GoodNotes' unit is 1/132 in
MAX_INDEX_BYTES = 64 << 20       # index.events.pb, index.notes.pb, index.attachments.pb, each
MAX_PAGE_BYTES = 256 << 20       # one page's notes/ member
MAX_PAGES = nb.MAX_NOTE_PAGES    # pages of one notebook
MAX_INDEX = 10 * MAX_PAGES       # entries of the page index read (a deleted page stays listed)
MAX_ELEMENTS = 1_000_000         # elements of one notebook, every page together
MAX_GEOMETRY = 8 << 20           # one stroke's geometry unpacked (the largest seen is 133,538 bytes)
MAX_NOTEBOOK_GEOMETRY = 1 << 30  # every stroke's geometry of one notebook, unpacked
MAX_NOTEBOOK_TEXT = 64 << 20     # every text box's RTF or runs of one notebook, unpacked
MAX_POINTS = nb.MAX_CURVE_POINTS * 10  # points in one stroke (the longest seen has 1,699)
MAX_PAGE_POINTS = 2_000_000      # points in one page's ink and shapes (a dense page of ex3's holds ~75,000)
MAX_SHAPE_POINTS = 10_000        # corners of one recognised shape
MAX_RUNS = 10_000                # runs of one text element
MAX_TEXT = 200_000               # characters of one text element
HIGHLIGHT_BLEND = 1              # a stroke's blendMode: multiply, the highlighter
PENCIL = 5                       # a stroke's type (#3) that is a pencil: its tpl format says the rest
INK, FILL, IMAGE, RTF_TEXT, TEXT = 7, 9, 1, 8, 21  # element kinds
KINDS = {11: "math", 20: "sticky note", 22: "line"}  # kinds known, not imported
COORD_LIMIT = 1e7                # canvas units: past this a value is damage
MAX_MS = 253_402_300_799_999     # ms since 1970: the last of year 9999, the latest a date may be
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
INHERIT = -404                   # a #21 style's "the default style's"

# The catalogue papers: the template name's leading UUID is the design.
# Each ruling's (pattern, pitch in points); a cover is a plain sheet of
# its colour, its band left out.
PAPERS = {"B03314A5-9B9F-556B-B373-4678E13D0DAC": ("blank", None),
          "CC365888-75C9-5DBF-B1D4-A7E8E0AD3B7F": ("dots", 16.0),
          "9FE8F365-4BEE-5057-8573-1A56C77CAC19": ("grid", 16.0),
          "238EE861-EE9A-51BD-B010-832CD3C50D2C": ("ruled", 17.5),
          "AA273C17-F440-5125-B34E-F9C3C62F89E2": ("ruled", 25.0)}
COVERS = frozenset(("417A3734-B870-5E89-8055-DA029834F25E", "BF443295-F2FD-55E0-975E-3E718F509B91"))
_CATALOGUE = re.compile(r"^([0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12})_[a-z0-9]+_\d+_\d+ - .+$")

FLAT_FORMATS = ("vuA(v)A(S(uu))A(S(uuuu))vA(f)", "vuA(v)A(S(uu))A(S(uuuu))")
RIBBON_FORMATS = ("vA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)", "vuA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)")
PENCIL_FORMAT = "vuA(v)A(S(uuuuu))A(S(uuuuuuuuuuu))A(S(uu))A(v)A(S(uu))A(S(uuuu))A(u)"
KNOWN_FORMATS = frozenset((*FLAT_FORMATS, *RIBBON_FORMATS, PENCIL_FORMAT))

NO_NOTEBOOKS_REASON = ("no GoodNotes notebooks found: upload one or more .goodnotes files, or a .zip of them "
                       "(GoodNotes on an iPad, iPhone or Mac: a document's ⋯ menu → Export → GoodNotes)")

LEFT_OUT = {
    "stroke": ("{n} stroke could not be read and was left out", "{n} strokes could not be read and were left out"),
    "shape": ("{n} shape could not be read and was left out", "{n} shapes could not be read and were left out"),
    "element": ("{n} element could not be read and was left out",
                "{n} elements could not be read and were left out"),
    "kind": ("{n} element of a kind this import does not know is left out ({classes})",
             "{n} elements of a kind this import does not know are left out ({classes})"),
    "shape box": ("{n} shape from the Shape tool is left out", "{n} shapes from the Shape tool are left out"),
    "elements": (f"Past {MAX_ELEMENTS:,} elements the rest is left out ({{n}} element)",
                 f"Past {MAX_ELEMENTS:,} elements the rest is left out ({{n}} elements)"),
    "pages": (f"Past {MAX_PAGES:,} pages the rest is left out ({{n}} page)",
              f"Past {MAX_PAGES:,} pages the rest is left out ({{n}} pages)"),
    "index": (f"The page index lists more than {MAX_INDEX:,} pages: the rest is left out",
              f"The page index lists more than {MAX_INDEX:,} pages: the rest is left out"),
    "page points": (f"Past {MAX_PAGE_POINTS:,} points on a page the rest of its handwriting is left out "
                    "({n} stroke)",
                    f"Past {MAX_PAGE_POINTS:,} points on a page the rest of its handwriting is left out "
                    "({n} strokes)"),
    "geometry": ("The notebook's handwriting is too large: {n} stroke is left out",
                 "The notebook's handwriting is too large: {n} strokes are left out"),
    "typed text": ("The notebook's typed text is too long: {n} text box is left out",
                   "The notebook's typed text is too long: {n} text boxes are left out"),
    "page": ("{n} page could not be read: it is blank", "{n} pages could not be read: they are blank"),
    "fill": ("{n} shape fill without its shape is left out", "{n} shape fills without their shapes are left out"),
    "picture": ("{n} picture without its image is left out", "{n} pictures without their images are left out"),
    "cropped picture": ("{n} cropped picture is shown whole", "{n} cropped pictures are shown whole"),
    "rotated box": ("{n} rotated text box is placed upright", "{n} rotated text boxes are placed upright"),
    "polygon fill": ("{n} filled polygon keeps its outline only", "{n} filled polygons keep their outline only"),
    "tape": ("{n} strip of tape is opaque ink: it no longer hides and reveals",
             "{n} strips of tape are opaque ink: they no longer hide and reveal"),
    "long text": ("{n} paragraph of typed text was cut short", "{n} paragraphs of typed text were cut short"),
    "samples": ("The notebook's handwriting is too long: {n} stroke is left out",
                "The notebook's handwriting is too long: {n} strokes are left out"),
    "rotated page": ("{n} turned page is imported upright", "{n} turned pages are imported upright"),
}


class NotebookError(ValueError):
    pass


# --- the model --------------------------------------------------------------------------

@dataclass
class Template:
    """A paper template (event #2): the PDF page a page is bound to."""
    id: str
    attachment: str | None       # its PDF's attachment id
    page: int = 1                # the 1-based page of that PDF
    canvas: tuple | None = None  # (w, h), canvas units: the PDF page × 11/6
    name: str = ""               # a catalogue paper's "<design>_<size>_<n>_<m> - <Colour>"
    pitch: float | None = None   # pt: the writing guide's (#18.#2 + #18.#3) × 6/11, when it has rules

    @property
    def design(self) -> str | None:
        """The catalogue design's UUID, None for a user's PDF."""
        m = _CATALOGUE.match(self.name)
        return m.group(1) if m else None


@dataclass
class Ink:
    """One sub-path of a stroke, in canvas units. ``points``: a flat pen's
    start, then each quadratic's control and end; a ribbon's or a pencil's
    samples. ``radii``: a ribbon's half width at each sample. ``force``: a
    pencil's force at each sample (0 without an Apple Pencil). ``width``:
    a flat pen's or a pencil's W, drawn W/2 pt wide."""
    kind: str                    # "flat" | "ribbon" | "pencil"
    points: list
    rgba: tuple                  # 0..1
    width: float = 0.0
    radii: list | None = None
    force: list | None = None
    highlighter: bool = False
    tape: bool = False
    dash: str | None = None      # "dashed" | "dotted"
    z: int = 0


@dataclass
class Page:
    id: str                      # P, the events' page UUID (the notes member's N less one)
    member: str                  # its notes/ member
    template: Template | None
    key: bytes = b""             # its order key
    turns: int = 0               # quarter turns (event #63)
    ink: list = field(default_factory=list)     # [Ink], with shapes in one drawing order (z)
    shapes: list = field(default_factory=list)  # [nb.Shape]
    media: list = field(default_factory=list)   # [nb.Media]: pictures (``picture`` their member) and text
    read: bool = False           # its member was read


@dataclass
class Notebook:
    title: str
    digest: str = ""             # the re-import key (``notebook_digest``)
    created: datetime | None = None
    pages: list = field(default_factory=list)        # [Page], in display order
    attachments: dict = field(default_factory=dict)  # attachment id → its member's path
    recordings: list = field(default_factory=list)   # [nb.RecordingRef], by start
    warnings: list = field(default_factory=list)
    missing: list = field(default_factory=list)      # members the notebook names that it lacks
    left_out: Counter = field(default_factory=Counter)
    classes: Counter = field(default_factory=Counter)  # the element kinds left out, by name

    def name_missing(self, path: str) -> None:
        if path not in self.missing:
            self.missing.append(path)


@dataclass
class Budget:
    """What a notebook's pages may still cost as they are read, every page
    together: its elements, its strokes' geometry and its text boxes' text,
    unpacked."""
    elements: int = MAX_ELEMENTS
    geometry: int = MAX_NOTEBOOK_GEOMETRY
    text: int = MAX_NOTEBOOK_TEXT


def left_out_reasons(counts: Counter, classes: Counter | None = None) -> list[str]:
    """One warning per kind of content left out (``LEFT_OUT``)."""
    out = []
    for kind, n in counts.items():
        if n <= 0 or kind not in LEFT_OUT:
            continue
        one, many = LEFT_OUT[kind]
        out.append((one if n == 1 else many).format(n=n, classes=", ".join(sorted(classes or ())) or "unnamed"))
    return out


# --- finding notebooks ----------------------------------------------------------------------

def _leaf(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def nested_zip(name: str) -> bool:
    """walk_zip's ``nested``: a ``*.zip`` opens in place (a folder's export,
    a ``.goodnotes.zip`` backup); a ``.goodnotes`` is opened by the import
    when it reads it."""
    return name.lower().endswith(".zip")


def notebook_members(entries: list[ZipEntry]) -> nb.Members | None:
    """The files below the folder of the shallowest ``index.notes.pb``;
    None without one."""
    roots = sorted((e for e in entries if _leaf(e.path).lower() == "index.notes.pb"),
                   key=lambda e: (e.path.count("/"), e.path))
    if not roots:
        return None
    folder = posixpath.dirname(roots[0].path)
    cut = len(folder) + 1 if folder else 0
    return nb.Members({e.path[cut:]: e for e in entries if not folder or e.path.startswith(folder + "/")})


def find_notebooks(entries: list[ZipEntry], upload_name: str) -> list[nb.Bundle]:
    """The notebooks among the walked entries, sorted by path: each member
    named ``*.goodnotes`` (any case) is one (``packed``); a folder holding
    an ``index.notes.pb`` and lying in no other is an unzipped one, whose
    path is its folder, or the upload's name when it is the upload's top
    (the upload is one .goodnotes)."""
    folders = sorted({posixpath.dirname(e.path) for e in entries if _leaf(e.path).lower() == "index.notes.pb"},
                     key=lambda f: (f.count("/"), f))
    kept: list[str] = []
    for folder in folders:
        if not any(not k or folder == k or folder.startswith(k + "/") for k in kept):
            kept.append(folder)
    bundles = []
    for folder in kept:
        found = {e.path[len(folder) + 1 if folder else 0:]: e for e in entries
                 if not folder or e.path.startswith(folder + "/")}
        members = nb.Members(found)
        path = (upload_name or "Untitled.goodnotes") if not folder else folder
        bundles.append(nb.Bundle(path=path, size=sum(e.size for e in found.values()), members=members))

    def inside(path):
        return any(not k or path.startswith(k + "/") for k in kept)

    bundles += [nb.Bundle(path=e.path, size=e.size, packed=e) for e in entries
                if _leaf(e.path).lower().endswith(".goodnotes") and not inside(e.path)]
    bundles.sort(key=lambda b: b.path)
    return bundles


def notebook_digest(members: nb.Members) -> str:
    """The re-import key: a SHA-256 of every member's path, size and CRC-32
    as the zip's directory states them, but for what GoodNotes derives
    (the search index, the thumbnail), so it changes with any edit and is
    computed without reading a byte."""
    h = hashlib.sha256()
    for path in sorted(members.entries):
        lower = path.lower()
        if lower.startswith("search/") or lower in ("index.search.pb", "thumbnail.jpg", "document.info.pb"):
            continue
        entry = members.entries[path]
        h.update(f"{path}\0{entry.size}\0{entry.crc:08x}\n".encode())
    return h.hexdigest()


# --- reading values -------------------------------------------------------------------------

def _point(m: gc.Message | None) -> tuple[float, float] | None:
    """``{#1 x, #2 y}``: a missing coordinate is 0 (protobuf omits a zero)."""
    if m is None:
        return None
    x, y = m.f32(1), m.f32(2)
    if (x is None and m.has(1)) or (y is None and m.has(2)):
        return None  # not finite
    x, y = x or 0.0, y or 0.0
    return (x, y) if abs(x) <= COORD_LIMIT and abs(y) <= COORD_LIMIT else None


def _rect(m: gc.Message | None) -> tuple[float, float, float, float] | None:
    """``{#1 {x, y}, #2 {w, h}}`` → (x, y, w, h)."""
    origin = _point(m.msg(1)) if m is not None else None
    size = _point(m.msg(2)) if m is not None else None
    return (*origin, *size) if origin is not None and size is not None else None


def rgba(m: gc.Message | None, default=(0.0, 0.0, 0.0, 1.0)) -> tuple:
    """``{#1 r, #2 g, #3 b, #4 a}`` floats in 0..1, a component omitted
    when it is 0 (opaque black is ``{#4 1}`` alone)."""
    if m is None:
        return default
    return tuple(min(1.0, max(0.0, m.f32(i) or 0.0)) for i in (1, 2, 3, 4))


def _uuid_minus_one(n: str) -> str:
    """The page UUID P of a notes member N: N − 1 as one 128-bit number."""
    value = (int(n.replace("-", ""), 16) - 1) % (1 << 128)
    h = f"{value:032X}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _floats_ok(values) -> bool:
    return all(math.isfinite(v) and abs(v) <= COORD_LIMIT for v in values)


def _dash(m: gc.Message | None) -> str | None:
    """A stroke's line style (#11 ``{#2 {#1 packed dash lengths, in
    widths}}``): dashed when its first dash is longer than a tenth of the
    width, else dotted; None when solid."""
    raw = m.msg(2).bytes(1) if m is not None and m.msg(2) is not None else None
    if not raw or len(raw) % 4:
        return None
    lengths = struct.unpack(f"<{len(raw) // 4}f", raw)
    if not lengths or not all(math.isfinite(v) and v >= 0 for v in lengths):
        return None
    return "dashed" if lengths[0] > 0.1 else "dotted"


def _when(ms: float | None) -> datetime | None:
    """A time in ms since 1970 as a UTC datetime, None past year 9999 or
    before 1970: counted from the epoch, so no platform's limits apply."""
    return _EPOCH + timedelta(milliseconds=ms) if ms is not None and 0 < ms <= MAX_MS else None


# --- reading a notebook ---------------------------------------------------------------------

def _member(members: nb.Members, rel: str, limit: int) -> bytes | None:
    entry = members.find(rel)
    if entry is None:
        return None
    if entry.size > limit:
        raise NotebookError(f"its {rel} is over {limit >> 20} MB")
    try:
        return entry.read()
    except READ_ERRORS:
        raise NotebookError(f"its {rel} could not be read") from None


def _stream(data: bytes | None, what: str, notebook: Notebook):
    """A member's records as messages, one at a time; a damaged tail is a
    warning, the records before it read."""
    if not data:
        return
    try:
        for raw in gc.iter_records(data):
            message = gc.Message.read(raw)
            if message is not None:
                yield message
    except gc.CodecError:
        notebook.warnings.append(f"{what} is damaged: what could not be read is left out")


def read_notebook(members: nb.Members, *, path: str = "", digest: str = "", ink: bool = True) -> Notebook:
    """Read a notebook's files into a ``Notebook``: its journal and indexes,
    and with ``ink`` every page's elements too (``read_page``; the import
    reads a page at a time instead, so it holds one page's ink, and a
    preview none). NotebookError when it is no notebook at all or an
    index is past MAX_INDEX_BYTES."""
    events = _member(members, "index.events.pb", MAX_INDEX_BYTES)
    notes = _member(members, "index.notes.pb", MAX_INDEX_BYTES)
    if notes is None:
        raise NotebookError("not a GoodNotes notebook")
    name = posixpath.basename(path)
    for suffix in (".goodnotes", ".zip"):
        if name.lower().endswith(suffix):
            name = name[:-len(suffix)]
    notebook = Notebook(title=name or "Untitled", digest=digest or notebook_digest(members))
    attachments = _attachments(members, _member(members, "index.attachments.pb", MAX_INDEX_BYTES), notebook)
    journal = _Journal(notebook, attachments)
    if events is None:
        notebook.warnings.append("The notebook has no event journal: its pages are blank paper in the order stored")
    for record in _stream(events, "The event journal", notebook):
        journal.event(record)
    notebook.attachments = journal.attachments
    notebook.title = (journal.title or notebook.title).strip()[:nb.MAX_NAME] or "Untitled"
    notebook.created = journal.created
    entries = list(itertools.islice(_stream(notes, "The page index", notebook), MAX_INDEX + 1))
    if len(entries) > MAX_INDEX:
        notebook.left_out["index"] += 1
        entries.pop()
    notebook.pages = journal.pages(entries)
    notebook.recordings = journal.recordings(members)
    if ink:
        budget = Budget()
        for page in notebook.pages:
            read_page(notebook, page, members, budget)
    return notebook


def read_page(notebook: Notebook, page: Page, members: nb.Members, budget: Budget) -> None:
    """Read one page's elements into it (``Page.ink``, ``shapes``,
    ``media``), within what ``budget`` leaves of the notebook's."""
    _PageReader(notebook, page, members, budget).run()


def _attachments(members: nb.Members, data: bytes | None, notebook: Notebook) -> dict:
    """Attachment id → member path, from the index and then any
    ``attachments/<id>`` it does not list."""
    out = {}
    for record in _stream(data, "The attachment index", notebook):
        ident, member = record.uuid(1), record.str(2)
        if ident and member and len(member) <= nb.MAX_NAME:
            out[ident] = member
    for rel in members.entries:
        if rel.lower().startswith("attachments/"):
            ident = rel.split("/", 1)[1].upper()
            out.setdefault(ident, rel)
    return out


class _Journal:
    """The event journal, read event by event in file order: what the
    pages, their templates and the recordings are once it ends."""

    def __init__(self, notebook: Notebook, attachments: dict):
        self.notebook = notebook
        self.attachments = dict(attachments)
        self.title, self.created = "", None
        self.templates: dict[str, Template] = {}
        self.bound: dict[str, str] = {}      # page P → template id (#54, then the last #3)
        self.keys: dict[str, bytes] = {}     # page P → order key (#54, then the last #55)
        self.turns: dict[str, int] = {}      # page P → quarter turns (#63)
        self.deleted: set[str] = set()
        self.audio: dict[str, tuple] = {}    # recording id → (attachment, started ms)

    def event(self, record: gc.Message) -> None:
        for number, wire, value in record.fields:
            if number == 1 or wire != 2:
                continue
            body = gc.Message.read(value)
            if body is not None:
                self._event(number, body)
            return

    def _event(self, kind: int, body: gc.Message) -> None:
        if kind in (30, 31):  # created, renamed
            title = body.msg(2).str(1) if body.msg(2) is not None else None
            if title:
                self.title = title
            if kind == 30:
                self.created = _when(body.f64(10))
        elif kind == 2:  # a template
            ident = body.uuid(2)
            if ident is None:
                return
            canvas = _point(body.msg(8))
            guide = body.msg(18)
            pitch = None
            if guide is not None and guide.has(3):  # a rule's width: the paper draws rules, a grid or dots
                gap, rule = guide.f32(2) or guide.f64(2), guide.f32(3) or guide.f64(3)
                if gap and rule and 0 < gap + rule < 1e4:
                    pitch = (gap + rule) * CANVAS_PT
            page = body.int(5)
            self.templates[ident] = Template(ident, body.uuid(4), page if page and page < 1 << 31 else 1,
                                             canvas if canvas and canvas[0] > 0 and canvas[1] > 0 else None,
                                             (body.str(9) or "")[:nb.MAX_NAME], pitch)
        elif kind in (54, 3):  # a page made, a page bound to another template
            page = body.uuid(2)
            bound = body.msg(3)
            if page is None:
                return
            if bound is not None and bound.uuid(1):
                self.bound[page] = bound.uuid(1)
            key = body.msg(4).bytes(1) if kind == 54 and body.msg(4) is not None else None
            if key:
                self.keys.setdefault(page, key[:256])
        elif kind == 55:  # a page moved
            page, key = body.uuid(2), body.msg(3).bytes(1) if body.msg(3) is not None else None
            if page and key:
                self.keys[page] = key[:256]
        elif kind == 56:
            if body.uuid(2):
                self.deleted.add(body.uuid(2))
        elif kind == 63:  # a page turned
            page, turn = body.uuid(2), body.msg(3).int(1) if body.msg(3) is not None else None
            if page and turn is not None:
                self.turns[page] = turn % 4
        elif kind == 6:  # an attachment, stored under another id when it is a PDF inserted as a page
            ident, storage = body.uuid(1), body.uuid(2)
            if ident and storage and storage != ident and ident not in self.attachments:
                member = self.attachments.get(storage) or f"attachments/{storage}"
                self.attachments[ident] = member
        elif kind == 160:  # a recording made
            ident, attachment, when = body.uuid(1), body.uuid(2), body.f64(10)
            if ident and attachment:
                self.audio[ident] = (attachment, when)

    def pages(self, entries: list[gc.Message]) -> list[Page]:
        """The live pages, in display order: by their order keys compared
        byte for byte, a page without one after the rest in index order."""
        found, seen = [], set()
        for i, entry in enumerate(entries):
            n, member = entry.uuid(1), entry.str(2)
            if n is None:
                continue
            member = member if member and len(member) <= nb.MAX_NAME else f"notes/{n}"
            page = _uuid_minus_one(n)
            if page in self.deleted or page in seen:
                continue
            seen.add(page)
            template = self.templates.get(self.bound.get(page, ""))
            key = self.keys.get(page)
            found.append(((0, key, i) if key is not None else (1, b"", i),
                          Page(page, member, template, key or b"", self.turns.get(page, 0))))
        found.sort(key=lambda item: item[0])
        pages = [page for _key, page in found]
        if len(pages) > MAX_PAGES:
            self.notebook.left_out["pages"] += len(pages) - MAX_PAGES
            pages = pages[:MAX_PAGES]
        return pages

    def recordings(self, members: nb.Members) -> list[nb.RecordingRef]:
        """The recordings whose audio the notebook holds, by start; each
        named "Recording", numbered when there are several."""
        found = []
        for attachment, when in self.audio.values():
            member = self.attachments.get(attachment, f"attachments/{attachment}")
            if members.find(member) is None:
                continue  # a recording deleted, whose event stays
            started = _when(when)
            found.append((started or datetime.max.replace(tzinfo=UTC), member, started))
        found.sort(key=lambda item: item[0])
        many = len(found) > 1
        return [nb.RecordingRef(member, f"Recording {i}" if many else "Recording", started)
                for i, (_order, member, started) in enumerate(found, 1)]


# --- reading a page -------------------------------------------------------------------------

class _PageReader:
    """One page's elements, in drawing order: each (metadata, content)
    pair read, a tombstone (metadata #3, content #14) passed by, a shape's
    fill joined to its shape once the page is read (a fill may come before
    it)."""

    def __init__(self, notebook: Notebook, page: Page, members: nb.Members, budget: Budget):
        self.notebook, self.page, self.members, self.budget = notebook, page, members, budget
        self.z = 0
        self.points = MAX_PAGE_POINTS  # what the page's ink and shapes may still hold
        self.shapes: dict[str, nb.Shape] = {}  # a stroke's UUID → its shape, for fills
        self.fills: list[tuple[str, tuple]] = []

    def run(self) -> None:
        notebook, page = self.notebook, self.page
        try:
            data = _member(self.members, page.member, MAX_PAGE_BYTES)
        except NotebookError as exc:
            notebook.warnings.append(f"A page is blank: {exc}")
            notebook.left_out["page"] += 1
            return
        if data is None:
            if self.members.find(page.member) is None and page.member:
                notebook.name_missing(page.member)
            return
        page.read = True
        deleted = False
        for record in _stream(data, "A page", notebook):
            if _is_metadata(record):
                deleted = record.int(3) == 1  # the next record is its element's content
                continue
            content = record.only()
            gone, deleted = deleted, False
            if content is None:
                notebook.left_out["element"] += 1
                continue
            kind, body = content
            if gone or body.int(14) == 1:
                continue
            if self.budget.elements <= 0:
                notebook.left_out["elements"] += 1
                continue
            self.budget.elements -= 1
            try:
                self._element(kind, body)
            except (gc.CodecError, ValueError, TypeError, OverflowError, IndexError, struct.error):
                notebook.left_out["element"] += 1  # an element that does not read is that element's
            self.z += 1
        for outline, color in self.fills:
            shape = self.shapes.get(outline)
            if shape is None:
                notebook.left_out["fill"] += 1
            else:
                shape.fill = color

    def _element(self, kind: int, body: gc.Message) -> None:
        if kind == INK:
            self._ink(body)
        elif kind == FILL:
            outline, color = body.uuid(5), rgba(body.msg(7), None)
            box = body.msg(2)
            if box is not None and any(p is not None and (p.nan(1) or p.nan(2)) for p in (box.msg(1), box.msg(2))):
                return  # an erased fill keeps NaN bounds
            if outline and color is not None:
                self.fills.append((outline, color))
        elif kind == IMAGE:
            self._image(body)
        elif kind == RTF_TEXT:
            self._rtf_text(body)
        elif kind == TEXT:
            self._text(body)
        else:
            self.notebook.left_out["kind"] += 1
            self.notebook.classes[KINDS.get(kind, f"kind {kind}")] += 1

    # --- ink ------------------------------------------------------------------------------

    def _ink(self, body: gc.Message) -> None:
        notebook = self.notebook
        color = rgba(body.msg(4))
        highlighter = body.int(5) == HIGHLIGHT_BLEND
        tape = body.msg(20) is not None and body.msg(20).has(1)
        offset = _point(body.msg(6)) or (0.0, 0.0)
        dash = _dash(body.msg(11))
        kind = body.int(3)
        style = nb.STYLE_HIGHLIGHTER if highlighter else nb.STYLE_PENCIL if kind == PENCIL else nb.STYLE_PEN
        shape = _shape(body.msg(9), offset)
        if shape is not None:
            if len(shape.points) > self.points:
                notebook.left_out["page points"] += 1
                return
            self.points -= len(shape.points)
            shape.rgba, shape.style, shape.dash, shape.z = color, style, dash, self.z
            self.page.shapes.append(shape)
            ident = body.uuid(1)
            if ident:
                self.shapes[ident] = shape
            return
        blob = body.bytes(2)
        if not blob:
            return  # no geometry and no shape: nothing to draw
        if self.budget.geometry <= 0 or self.points <= 0:
            notebook.left_out["geometry" if self.budget.geometry <= 0 else "page points"] += 1
            return
        limit = min(MAX_GEOMETRY, self.budget.geometry)
        try:
            raw = gc.lz4_frame(blob, limit)
        except gc.TooLarge:
            if limit == MAX_GEOMETRY:
                raise  # one stroke past what one may unpack to: damage
            self.budget.geometry = 0  # past what the notebook's strokes may unpack to
            notebook.left_out["geometry"] += 1
            return
        self.budget.geometry -= len(raw)
        fmt, values = gc.tpl(raw, KNOWN_FORMATS)
        if fmt in FLAT_FORMATS:
            pieces = _flat(values)
            ink = [Ink("flat", pts, color, width=values[1]) for pts in pieces]
        elif fmt in RIBBON_FORMATS:
            ink = [Ink("ribbon", pts, color, radii=radii) for pts, radii in _ribbon(values, fmt == RIBBON_FORMATS[1])]
        elif fmt == PENCIL_FORMAT:
            ink = [Ink("pencil", pts, color, width=values[1], force=force) for pts, force in _pencil(values)]
        else:
            notebook.left_out["stroke"] += 1
            return
        for piece in ink:
            if not piece.points or len(piece.points) > MAX_POINTS or not math.isfinite(piece.width):
                notebook.left_out["stroke"] += 1
                continue
            if len(piece.points) > self.points:
                notebook.left_out["page points"] += 1
                continue
            self.points -= len(piece.points)
            dx, dy = offset
            piece.points = [(x + dx, y + dy) for x, y in piece.points]
            if not _floats_ok(v for p in piece.points for v in p) or not _floats_ok(piece.radii or ()):
                notebook.left_out["stroke"] += 1
                continue
            piece.highlighter, piece.tape, piece.dash, piece.z = highlighter, tape, dash, self.z
            if tape:
                notebook.left_out["tape"] += 1
            self.page.ink.append(piece)

    # --- pictures and text -----------------------------------------------------------------

    def _image(self, body: gc.Message) -> None:
        notebook = self.notebook
        box = _rect(body.msg(2))
        ident = body.uuid(4)
        member = notebook.attachments.get(ident, f"attachments/{ident}") if ident else None
        if box is None or box[2] <= 0 or box[3] <= 0:
            notebook.left_out["element"] += 1
            return
        if member is None or self.members.find(member) is None:
            if member:
                notebook.name_missing(member)
            notebook.left_out["picture"] += 1
            return
        shown = body.msg(3)  # the visible rect {centre, size} and its turn
        turn = (shown.f32(3) or 0.0) if shown is not None else 0.0
        visible = _rect(shown)
        if visible is not None and (abs(visible[2] - box[2]) > 0.5 or abs(visible[3] - box[3]) > 0.5):
            notebook.left_out["cropped picture"] += 1
        self.page.media.append(nb.Media("picture", *box, rotation=turn if abs(turn) > 1e-4 else 0.0,
                                        z=self.z, picture=member))

    def _rtf_text(self, body: gc.Message) -> None:
        """A legacy text box (#8): its text frame (#3, else the outer rect
        less its padding), its RTF, its fill (#7)."""
        outer, inner = _rect(body.msg(2)), _rect(body.msg(3))
        pad = body.f32(10) or 10.0
        if inner is None and outer is not None:
            inner = (outer[0] + pad, outer[1] + pad, max(0.0, outer[2] - 2 * pad), max(0.0, outer[3] - 2 * pad))
        if inner is None:
            self.notebook.left_out["element"] += 1
            return
        data = body.bytes(6) or b""
        if len(data) > self.budget.text:
            self.notebook.left_out["typed text"] += 1
            return
        self.budget.text -= len(data)
        runs, length = [], 0
        for span in rtf.read_rtf(data)[:MAX_RUNS]:
            text = span.text[:MAX_TEXT - length]
            length += len(text)
            runs.append(nb.Run(text, bold=span.bold, italic=span.italic, strike=span.strike, size=span.size,
                               rgba=(*(c / 255 for c in span.color), 1.0) if span.color else None))
        fill = rgba(body.msg(7), None)
        self.page.media.append(nb.Media("text", *inner, z=self.z, runs=runs,
                                        background=fill if fill and fill[3] > 0 else None))

    def _text(self, body: gc.Message) -> None:
        """A schema-35 element (#21): a text box (#9 1) or a Shape tool
        object (#9 0), placed by its transform (#20: origin, turn about it,
        scale), its text a run list in Apple LZ4 (#32)."""
        notebook = self.notebook
        if body.int(9) != 1:
            notebook.left_out["shape box"] += 1
            return
        transform, content = body.msg(20), body.msg(32)
        origin = _point(transform.msg(1)) if transform is not None else None
        if origin is None or content is None:
            notebook.left_out["element"] += 1
            return
        turn = transform.f32(2) or 0.0
        scale = transform.f32(3) or 1.0
        scale = scale if 0.01 < scale < 100 else 1.0
        insets = content.msg(10)
        left, top, right, bottom = ((insets.f32(i) if insets is not None and insets.has(i) else 10.0) or 0.0
                                    for i in (1, 2, 3, 4))
        fixed = body.msg(21).msg(2) if body.msg(21) is not None else None
        size = _point(fixed) if fixed is not None else None
        if size is None:
            inside = _point(content.msg(2))
            size = (inside[0] + left + right, inside[1] + top + bottom) if inside else (left + right, top + bottom)
        default = content.msg(5).msg(1) if content.msg(5) is not None else None
        if self.budget.text <= 0:
            notebook.left_out["typed text"] += 1
            return
        runs = _runs(content, default, scale, self.budget)
        if runs is None:
            notebook.left_out["element"] += 1
            return
        w, h = max(0.0, size[0] - left - right) * scale, max(0.0, size[1] - top - bottom) * scale
        x, y = origin[0] + left * scale, origin[1] + top * scale
        if abs(turn) > 1e-6:  # GoodNotes turns the box about its origin: keep the centre it is turned to
            cx, cy = x + w / 2 - origin[0], y + h / 2 - origin[1]
            c, s = math.cos(turn), math.sin(turn)
            x, y = origin[0] + c * cx - s * cy - w / 2, origin[1] + s * cx + c * cy - h / 2
        fill_box = body.msg(30).msg(1) if body.msg(30) is not None else None  # {#1 {#1 colour}}
        fill = rgba(fill_box.msg(1) if fill_box is not None else None, None)
        self.page.media.append(nb.Media("text", x, y, w, h, rotation=turn if abs(turn) > math.radians(0.5) else 0.0,
                                        z=self.z, runs=runs, background=fill if fill and fill[3] > 0 else None))


def _is_metadata(record: gc.Message) -> bool:
    """A metadata record: its element's UUID at #1 and the device and
    counter varints (#8, #9) a content record never has at its top."""
    return record.uuid(1) is not None and record.int(8) is not None


def _runs(content: gc.Message, default: gc.Message | None, scale: float, budget: Budget) -> list[nb.Run] | None:
    """A #21 element's runs (``#32.#1.#2``, a run list, Apple LZ4 or plain):
    each its text, colour, size (canvas units, ``scale`` applied) and bold
    (#60, a weight, −30 bold), a style value of −404 the default style's."""
    blob = content.msg(1).bytes(2) if content.msg(1) is not None else None
    if blob is None:
        return None
    if gc.is_lz4_frame(blob):
        blob = gc.lz4_frame(blob, min(MAX_TEXT * 8, budget.text))
    budget.text -= len(blob)
    runs_msg = gc.Message.read(blob)
    if runs_msg is None:
        return None
    base_size = default.f32(40) if default is not None else None
    base_size = base_size if base_size and base_size > 0 else 24.0
    base_color = rgba(default.msg(3), (0.0, 0.0, 0.0, 1.0)) if default is not None and default.msg(3) else None
    base_weight = default.signed(60) if default is not None else None
    out, length = [], 0
    for run in runs_msg.msgs(1)[:MAX_RUNS]:
        text = (run.str(1) or "")[:MAX_TEXT - length]
        length += len(text)
        style = run.msg(2)
        size = style.f32(40) if style is not None else None
        size = size if size and size > 0 and size != INHERIT else base_size
        color = rgba(style.msg(3), None) if style is not None and style.msg(3) is not None else None
        weight = style.signed(60) if style is not None else None
        weight = base_weight if weight in (None, INHERIT) else weight
        out.append(nb.Run(text, bold=weight == -30, size=size * scale, rgba=color or base_color))
    return out


def _shape(m: gc.Message | None, offset: tuple) -> nb.Shape | None:
    """A recognised shape's geometry (#9), its lasso offset added, as the
    planner's Shape: two points a line, more a closed polygon when the
    last is the first (three corners a triangle, four a rectangle) or else
    an open polyline; a quadratic a curved line; an axis-aligned rectangle
    its corners; an ellipse the corners of the turned rectangle it is
    inscribed in. Its width is #15, drawn #15/2 pt wide. None when it holds
    no geometry."""
    if m is None:
        return None
    dx, dy = offset

    def at(p):
        return (p[0] + dx, p[1] + dy)

    width = m.f32(15) or 0.0
    width = width / 2 / CANVAS_PT if width > 0 else 1 / CANVAS_PT  # the planner draws width × 6/11
    ellipse, rect, curve, line = m.msg(4), m.msg(3), m.msg(2), m.msg(1)
    if line is not None and sum(1 for n, _w, _v in line.fields if n == 1) > MAX_SHAPE_POINTS:
        raise gc.CodecError("a shape of too many corners")
    points = [p for p in (_point(q) for q in line.msgs(1)) if p is not None] if line is not None else []
    kind, pts, control, segments = None, [], None, ()
    if ellipse is not None:
        centre, axes = _point(ellipse.msg(1)), _point(ellipse.msg(2))
        if centre is None or axes is None:
            return None
        theta = ellipse.f32(3) or 0.0
        c, s = math.cos(theta), math.sin(theta)
        corners = [(-axes[0], -axes[1]), (axes[0], -axes[1]), (axes[0], axes[1]), (-axes[0], axes[1])]
        kind, pts = "ellipse", [at((centre[0] + c * u - s * v, centre[1] + s * u + c * v)) for u, v in corners]
    elif rect is not None:
        centre, size = _point(rect.msg(1)), _point(rect.msg(2))
        if centre is None or size is None:
            return None
        hw, hh = size[0] / 2, size[1] / 2
        kind = "rectangle"
        pts = [at((centre[0] + u, centre[1] + v)) for u, v in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]
    elif curve is not None:
        p0, q, p1 = (_point(curve.msg(i)) for i in (1, 2, 3))
        if p0 is None or q is None or p1 is None:
            return None
        kind, pts, control = "line", [at(p0), at(p1)], at(q)
    elif len(points) >= 2:
        closed = len(points) > 3 and points[0] == points[-1]
        corners = points[:-1] if closed else points
        if len(points) == 2:
            kind = "line"
        elif closed:
            kind = {3: "triangle", 4: "rectangle"}.get(len(corners), "polygon")
        else:
            kind, segments = "path", (1,) * (len(corners) - 1)
        pts = [at(p) for p in corners]
    if kind is None:
        return None
    return nb.Shape(kind, pts, control, False, width, (0.0, 0.0, 0.0, 1.0), None, nb.STYLE_PEN, None, False,
                    segments)


def _flat(values: list) -> list[list]:
    """A constant-width stroke's sub-paths: each flag 0 starts one at the
    next start point, each flag 1 adds the next quadratic (control, end)."""
    flags, starts, quads = values[2], values[3], values[4]
    pieces, si, qi = [], 0, 0
    for flag in flags:
        if flag == 0:
            if si >= len(starts):
                raise gc.CodecError("more sub-paths than start points")
            pieces.append([starts[si]])
            si += 1
        else:
            if qi >= len(quads) or not pieces:
                raise gc.CodecError("a segment without its sub-path")
            cx, cy, x, y = quads[qi]
            pieces[-1] += [(cx, cy), (x, y)]
            qi += 1
    return pieces


def _ribbon(values: list, with_width: bool) -> list[tuple[list, list]]:
    """A variable-width stroke's sub-paths as (samples, half widths): an
    even flag starts one at the next start tuple (x, y, r, …), an odd one
    adds the next panel's two samples, (x1, y1, r1, x2, y2, r2, …), or
    (x1, y1, x2, y2, r1, r2) in the layout with a width word."""
    off = 1 if with_width else 0
    flags, starts, panels = values[1 + off], values[2 + off], values[3 + off]
    n_start = sum(1 for f in flags if f % 2 == 0)
    n_panel = len(flags) - n_start
    if (n_start and len(starts) % n_start) or (n_panel and len(panels) % n_panel):
        raise gc.CodecError("a ribbon's pools do not match its flags")
    s_stride = len(starts) // n_start if n_start else 0
    p_stride = len(panels) // n_panel if n_panel else 0
    if (n_start and s_stride < 3) or (n_panel and p_stride < 6):
        raise gc.CodecError("a ribbon's tuples are too short")
    pieces, si, pi = [], 0, 0
    for flag in flags:
        if flag % 2 == 0:
            t = starts[si * s_stride:(si + 1) * s_stride]
            si += 1
            pieces.append(([(t[0], t[1])], [t[2]]))
        else:
            if not pieces:
                raise gc.CodecError("a panel without its sub-path")
            t = panels[pi * p_stride:(pi + 1) * p_stride]
            pi += 1
            pts, radii = pieces[-1]
            if with_width:
                pts += [(t[0], t[1]), (t[2], t[3])]
                radii += [t[4], t[5]]
            else:
                pts += [(t[0], t[1]), (t[3], t[4])]
                radii += [t[2], t[5]]
    for _pts, radii in pieces:
        if not all(math.isfinite(r) and 0 <= r <= COORD_LIMIT for r in radii):
            raise gc.CodecError("a ribbon's width is not finite")
    return pieces


def _pencil(values: list) -> list[tuple[list, list]]:
    """A pencil stroke's sub-paths as (samples, forces): an even flag starts
    one at the next start (x, y, a, b, force), an odd one adds the next
    segment's two samples (seed, x1, y1, a, b, force, x2, y2, a, b, force)."""
    flags, starts, segments = values[2], values[3], values[4]
    pieces, si, gi = [], 0, 0
    for flag in flags:
        if flag % 2 == 0:
            if si >= len(starts):
                raise gc.CodecError("more sub-paths than start points")
            t = starts[si]
            si += 1
            pieces.append(([(t[0], t[1])], [t[4]]))
        else:
            if gi >= len(segments) or not pieces:
                raise gc.CodecError("a segment without its sub-path")
            t = segments[gi]
            gi += 1
            pts, force = pieces[-1]
            pts += [(t[1], t[2]), (t[6], t[7])]
            force += [t[5], t[10]]
    for _pts, force in pieces:
        for i, f in enumerate(force):
            force[i] = f if math.isfinite(f) and f > 0 else 0.0
    return pieces
