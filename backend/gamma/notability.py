"""Notability notes: finding them among an upload's files, and reading one
into the ``Note`` model (the format: docs/research/notability-format.md;
what the import makes of a note: docs/dev/import_export.md "Notability
notes").

A note is Notability's "Note" export, a zip holding one folder with a
``Session.plist`` (the whole document, a keyed archive), a
``metadata.plist``, its PDFs, images and recordings. This module reads
plists only: no PDF parsing, no image decoding, no storage and no database.
Everything it reads is untrusted, so the reading is bounded (the caps
below), every value is type-checked where it is read
(gamma/notability_archive.py), and damage reads as a missing value, a
warning or a count of what was left out, never as a crash. An archive may
name one object many times, so what a note costs is counted where it is
spent, not per object: the plists inside it (MAX_SESSION_BYTES in all), its
typed text (MAX_NOTE_TEXT) and the formatting runs on it (MAX_NOTE_RUNS), its
objects (MAX_MEDIA), shapes (MAX_SHAPES), ink groups (MAX_GROUP_ARCHIVES) and
pages (MAX_NOTE_PAGES).

Document units are Notability's: y points down, and x = 0 is the paper's
left edge plus an inset (gamma/notability_map.py places them on a page).
The ink stays in ``array.array`` over the file's bytes and a ``Curve`` is
offsets into it, so a note of a million curves holds no Python object per
point.
"""

import bisect
import math
import posixpath
import sys
import unicodedata
from array import array
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import accumulate, islice

from .import_zip import READ_ERRORS, ZipEntry
from .notability_archive import (MAX_NAME, Archive, ArchiveError, color_string, finite, load_plist, parse_numbers,
                                 utc, whole)

MAX_SESSION_BYTES = 256 << 20   # Session.plist, the whole note; refused unread past this (gnnote's per-member cap),
                                # and the plists inside it (groups, their archives, shapes, dashes) in all
MAX_SMALL_PLIST = 4 << 20       # metadata.plist and Recordings/library.plist
MAX_CURVES = 1_000_000          # curves in one note, every ink hash together
MAX_CURVE_POINTS = 10_000       # points in one curve (Notability writes a few hundred at most)
MAX_GROUP_DEPTH = 8             # ink groups inside ink groups
MAX_GROUP_ARCHIVES = 20_000     # ink groups read in one note: their plists and the archives inside them
MAX_SHAPES = 100_000            # shapes in one note, every hash and group together
MAX_NOTE_PAGES = 5_000          # pages of one note: its layout is read no further
MAX_MEDIA = 10_000              # text boxes, pictures, formulas and stickies in one note
MAX_NOTE_TEXT = 5_000_000       # characters of typed text in one note, every string together
MAX_NOTE_RUNS = 200_000         # formatting runs read in one note, every text together
COORD_LIMIT = 1e6               # an item with a |coordinate| past this, in document units, is damage
UNFILED = frozenset({"unsortedNotesKey", "Unfiled Notes", ""})
NTB_REASON = ("This is a Notability .ntb file, the format of Notability Cloud and of the Mac app's "
              "Save As, which Gamma cannot read reliably yet. In Notability, export the note again "
              "with File Format “Note” instead of “NTB” (in the note: the options icon → Export "
              "Options; in the library: press and hold the note → Export Options), then import the "
              ".note file.")
SUPERNOTE_REASON = "This .note file is from a Supernote, not from Notability."
NO_NOTES_REASON = ("No Notability notes in this file. Gamma reads Notability's “Note” export: a .note "
                   "file, or a .zip of them. A “PDF + Recording” backup holds a PDF and its audio: "
                   "add the PDF to the library instead.")
SUPERNOTE_MAGIC = b"noteSN_FILE_VER_"
NEWEST_FORMAT = 9               # sessionFormatVersion of Notability 15 and 16
TITLE_CHARS = 500
STICKY_COLOR = (1.0, 244 / 255, 142 / 255, 1.0)  # #FFF48E, a sticky without a colour of its own
STYLE_PEN, STYLE_HIGHLIGHTER, STYLE_PENCIL, STYLE_HIDDEN = 3, 4, 5, 6
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
PAPER_SIZES = ("letter", "a4", "legal")
SIZING = ("lockedWidth", "staticWidth", "deviceBasedWidth")
SHAPE_KINDS = ("line", "rectangle", "square", "triangle", "polygon", "circle", "ellipse")
_WIDTH_RANGE = (16.0, 10_000.0)  # a page width W outside it is damage
_MAX_LINE_SPACING = 10.0         # inches between ruled lines
_MAX_PAGE_NUMBER = 1_000_000     # past it a layout entry is a blank page (Notability writes INT64_MAX)

# What a note left out, by kind, as its warning says it once per note
# (``left_out_reasons``); ``{n}`` is the count.
LEFT_OUT = {
    "stroke": ("{n} stroke could not be read and was left out", "{n} strokes could not be read and were left out"),
    "cut short": ("The handwriting data ends early: {n} stroke is left out",
                  "The handwriting data ends early: {n} strokes are left out"),
    "curves": (f"Past {MAX_CURVES:,} strokes the rest is left out ({{n}} stroke)",
               f"Past {MAX_CURVES:,} strokes the rest is left out ({{n}} strokes)"),
    "group": ("{n} group of handwriting could not be read and was left out",
              "{n} groups of handwriting could not be read and were left out"),
    "partial shape": ("{n} partly erased shape is left out", "{n} partly erased shapes are left out"),
    "shape": ("{n} shape of a kind this import does not know is left out",
              "{n} shapes of a kind this import does not know are left out"),
    "shapes limit": (f"Past {MAX_SHAPES:,} shapes the rest is left out ({{n}} shape)",
                     f"Past {MAX_SHAPES:,} shapes the rest is left out ({{n}} shapes)"),
    "object": ("{n} object could not be read and was left out", "{n} objects could not be read and were left out"),
    "media": ("{n} object of a kind this import does not know is left out ({classes})",
              "{n} objects of a kind this import does not know are left out ({classes})"),
    "rotated box": ("{n} rotated text box is placed upright", "{n} rotated text boxes are placed upright"),
    "polygon fill": ("{n} filled polygon keeps its outline only", "{n} filled polygons keep their outline only"),
    "page": ("Past {pages:,} pages the rest is left out ({n} item)",
             "Past {pages:,} pages the rest is left out ({n} items)"),
    "samples": ("The note's handwriting is too long: {n} stroke is left out",
                "The note's handwriting is too long: {n} strokes are left out"),
    "long text": ("{n} paragraph of typed text was cut short", "{n} paragraphs of typed text were cut short"),
    "typed text": ("The note's typed text is too long: {n} text is cut short or left out",
                   "The note's typed text is too long: {n} texts are cut short or left out"),
    "formatting": ("The note's typed text has too much formatting: {n} text keeps its words only",
                   "The note's typed text has too much formatting: {n} texts keep their words only"),
    "media limit": (f"Past {MAX_MEDIA:,} objects the rest is left out ({{n}} object)",
                    f"Past {MAX_MEDIA:,} objects the rest is left out ({{n}} objects)"),
    "text": ("{n} paragraph of typed text past the import's limit is left out",
             "{n} paragraphs of typed text past the import's limit are left out"),
}


class NoteError(ValueError):
    pass


# --- the model --------------------------------------------------------------------------

@dataclass
class Members:
    """A note's files by their path below the note's folder (the one
    holding Session.plist)."""
    entries: dict[str, ZipEntry]
    _folded: dict | None = None

    def resolve(self, rel: str) -> str | None:
        """The member key ``rel`` names: an exact match, else one NFC and
        case-insensitive; None when the note has no such file. ``rel`` is
        only a lookup key, never a path that is opened."""
        if not isinstance(rel, str) or not rel or len(rel) > MAX_NAME:
            return None
        if rel in self.entries:
            return rel
        if self._folded is None:
            self._folded = {}
            for key in self.entries:
                self._folded.setdefault(_fold(key), key)
        return self._folded.get(_fold(rel))

    def find(self, rel: str) -> ZipEntry | None:
        key = self.resolve(rel)
        return self.entries[key] if key is not None else None


def _fold(path: str) -> str:
    return unicodedata.normalize("NFC", path.replace("\\", "/")).casefold()


@dataclass
class Bundle:
    path: str                       # its selection id and source path (find_bundles)
    size: int                       # the .note file's size, or its folder's members summed
    packed: ZipEntry | None = None  # a .note file in the upload: opened only while it is read
    members: Members | None = None  # an unzipped note's files, found in the upload's own walk


@dataclass
class PaperSpec:
    size: str = ""            # "letter" | "a4" | "legal" | "" (legacy)
    sizing: str = ""          # "lockedWidth" | "staticWidth" | "deviceBasedWidth" | ""
    landscape: bool = False
    color: str | None = None  # "#rrggbb" from "Color:#…" or "TemplatePDF:<UUID>:#…"; None means white
    legacy: int | None = None  # "Legacy:<n>", or the root's paperIndex (11.x and earlier)
    lines: float | None = None  # the spacing in inches of lineStyle2 "Lines:<b>:<b>:<spacing>"
    grid: bool = False        # legacy paperLineStyle 5 (a small grid)
    template: bool = False    # a TemplatePDF paper


@dataclass
class LayoutEntry:
    pdf: str | None           # a PDF's file name; None for a paper page
    page: int | None          # the 1-based page in that PDF


@dataclass
class PdfFile:
    name: str
    path: str                 # below the note's folder
    version: int              # PDFFile.version: 2 for 10.x and later, 1 when the key is absent


@dataclass(slots=True)
class InkHash:
    """One InkedSpatialHash's per-point and per-anchor arrays, unpacked
    little-endian into array.array (no Python object per point), with the
    transform of the ink groups around it."""
    points: array                  # 'f': x, y per point
    fractional: array | None       # 'f': per anchor; None when the counts disagree (widths read as 1.0)
    force: array | None            # 'f': per anchor, raw UITouch force
    altitude: array | None         # 'f': per anchor, radians
    azimuth: array | None          # 'f': x, y per anchor
    transform: tuple               # (a, b, c, d, tx, ty): x' = a·x + c·y + tx, y' = b·x + d·y + ty
    scale: float                   # the transform's width scale, (hypot(a, b) + hypot(c, d)) / 2


@dataclass(slots=True)
class Curve:
    ink: InkHash
    p: int                         # its first point: ink.points[2p : 2(p + n)]
    n: int
    f: int                         # its first anchor in ink's per-anchor arrays
    bezier: bool                   # n = 1 + 3k: a Bézier chain; else a polyline (every point an anchor)
    base: float                    # curveswidth × ink.scale
    rgba: tuple                    # the curvescolors bytes
    style: int                     # 3 pen, 4 highlighter, 5 pencil; any other value reads as a pen
    dash: str | None               # "dashed" | "dotted"

    @property
    def anchors(self) -> int:
        return (self.n - 1) // 3 + 1 if self.bezier else self.n


@dataclass
class Shape:
    kind: str                      # "line" "rectangle" "square" "triangle" "polygon" "circle" "ellipse"
    points: list                   # transformed. line: start, end; rectangle to polygon: the vertices;
                                   # circle and ellipse: the rotated rect's 4 corners
    control: tuple | None          # a curved line's quadratic control point
    arrow: bool
    width: float
    rgba: tuple
    fill: tuple | None
    highlighter: bool
    dash: str | None
    grouped: bool                  # from an ink group: drawn as a plain stroke


@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    mark: bool = False
    size: float | None = None      # NSFontSizeAttribute, document units
    rgba: tuple | None = None
    list_style: int = 0            # indent-decoration-style: 0 none, 1 bullet, 2 numbered, 3 checklist
    level: int = 0                 # indent-level
    number: int = 1                # indent-decoration-number
    checked: bool = False          # checklist-checked


@dataclass
class Media:
    kind: str                      # "text" | "sticky" | "math" | "picture"
    x: float                       # documentOrigin and unscaledContentSize, before rotation
    y: float
    w: float
    h: float
    rotation: float = 0.0          # rotationDegrees, which holds radians; the turn is about the centre
    flip_h: bool = False           # pictures only
    flip_v: bool = False
    z: float = 0.0                 # zIndex
    runs: list = field(default_factory=list)  # text
    background: tuple | None = None  # text: its paper colour, None is transparent; sticky: its colour
    latex: str = ""                # math
    rgba: tuple | None = None
    picture: str | None = None     # picture: its image below the note's folder; None when missing
    crop: tuple | None = None      # picture: FigureCropRectKey, in source pixels
    caption: str = ""              # picture: captionFieldText when isCaptionEnabled


@dataclass
class RecordingRef:
    path: str | None               # the audio file below the note's folder; None when missing
    name: str
    created: datetime | None


@dataclass
class Note:
    title: str
    subject: str = ""              # "" when unfiled
    digest: str = ""               # storage.content_digest of Session.plist: the re-import key
    created: datetime | None = None
    version: int | None = None     # sessionFormatVersion
    width: float = 565.0           # W
    paper: PaperSpec = field(default_factory=PaperSpec)
    layout: list = field(default_factory=list)       # [LayoutEntry]
    pdfs: dict = field(default_factory=dict)         # the bundled PDFs the layout can use, by file name
    curves: list = field(default_factory=list)       # drawing order, ink groups merged in at their index
    shapes: list = field(default_factory=list)       # sorted by their indices
    media: list = field(default_factory=list)        # by zIndex, then file order
    flow: list = field(default_factory=list)         # the text typed on the pages, in order
    recordings: list = field(default_factory=list)   # by identifier
    missing: list = field(default_factory=list)      # files the note names that it lacks
    warnings: list = field(default_factory=list)
    left_out: Counter = field(default_factory=Counter)  # what was left out, counted by kind (LEFT_OUT)
    classes: Counter = field(default_factory=Counter)   # the classes of the media objects left out


def left_out_reasons(counts: Counter, classes: Counter | None = None, pages: int = 0) -> list[str]:
    """One warning per kind of content left out (``LEFT_OUT``)."""
    out = []
    for kind, n in counts.items():
        if n <= 0 or kind not in LEFT_OUT:
            continue
        one, many = LEFT_OUT[kind]
        names = ", ".join(sorted(classes or ())) or "unnamed"
        out.append((one if n == 1 else many).format(n=n, classes=names, pages=pages))
    return out


def curve_points(curve: Curve) -> list[tuple[float, float]]:
    """The curve's points through its hash's transform, in document units."""
    a, b, c, d, tx, ty = curve.ink.transform
    pts = curve.ink.points[2 * curve.p: 2 * (curve.p + curve.n)]
    return [(a * x + c * y + tx, b * x + d * y + ty) for x, y in zip(pts[0::2], pts[1::2])]


# --- finding notes ------------------------------------------------------------------------

def _leaf(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _inside(path: str, folder: str) -> bool:
    return not folder or path == folder or path.startswith(folder + "/")


def nested_zip(name: str) -> bool:
    """walk_zip's ``nested`` for this source: a ``*.zip`` opens in place,
    except under an ``NBPDFIndex/`` folder (Notability's own search cache,
    never read). A .note is not opened by the walk: the import opens each
    one when it reads it."""
    parts = name.lower().split("/")
    return parts[-1].endswith(".zip") and "nbpdfindex" not in parts[:-1]


def _sessions(entries: list[ZipEntry]) -> list[ZipEntry]:
    """The Session.plist members, shallowest first."""
    return sorted((e for e in entries if _leaf(e.path).lower() == "session.plist"),
                  key=lambda e: (e.path.count("/"), e.path))


def _members_below(entries: list[ZipEntry], folder: str) -> Members:
    cut = len(folder) + 1 if folder else 0
    return Members({e.path[cut:]: e for e in entries if _inside(e.path, folder) and e.path != folder})


def _owner(path: str, folders) -> str | None:
    """The folder of ``folders`` that ``path`` is or lies in, None when
    none: the path and each directory above it looked up, so finding the
    notes of an upload stays linear in its files."""
    if "" in folders:
        return ""
    while path not in folders:
        if "/" not in path:
            return None
        path = path.rsplit("/", 1)[0]
    return path


def note_members(entries: list[ZipEntry]) -> Members | None:
    """The files below the shallowest Session.plist's folder; None without
    one. ``find_bundles`` uses it, and so does the import for a packed note
    once it is opened."""
    sessions = _sessions(entries)
    return _members_below(entries, posixpath.dirname(sessions[0].path)) if sessions else None


def find_bundles(entries: list[ZipEntry], upload_name: str) -> tuple[list[Bundle], list[tuple[str, str]]]:
    """The notes among the walked entries, sorted by path, plus what else is
    worth a word (``[path, reason]``).

    - A member named ``*.note`` (any case) is a packed note: a Bundle with
      ``packed``.
    - A member named Session.plist (any case) that lies in no other note
      marks an unzipped note at its folder (the shallowest one when they
      nest); its members are the entries below that folder.
    - A packed note's path is its member's path. An unzipped note's path
      is its folder, except that it is the upload's name when the folder is
      the upload's top, or when the upload is named ``*.note`` and the
      folder is its single top folder (Notability's own export of one note,
      whose folder may carry an older name).
    - A folder holding a member named ``noteBundle`` (and no Session.plist)
      is left out with NTB_REASON, and so is a member named ``*.ntb``."""
    folders: dict[str, dict] = {}  # each note's folder → its members by their path below it
    for session in _sessions(entries):
        folder = posixpath.dirname(session.path)
        if _owner(folder, folders) is None:
            folders[folder] = {}
    outside = []
    for e in entries:
        folder = _owner(e.path, folders)
        if folder is None:
            outside.append(e)
        elif e.path != folder:
            folders[folder][e.path[len(folder) + 1 if folder else 0:]] = e
    tops = {e.path.split("/", 1)[0] if "/" in e.path else "" for e in entries}
    bundles, reasons, seen = [], [], set()
    for folder, found in folders.items():
        members = Members(found)
        single = upload_name.lower().endswith(".note") and "/" not in folder and tops == {folder}
        path = (upload_name or "Untitled.note") if not folder or single else folder
        bundles.append(Bundle(path=path, size=sum(e.size for e in members.entries.values()), members=members))
    for e in outside:
        leaf = _leaf(e.path)
        if leaf.lower().endswith(".note"):
            bundles.append(Bundle(path=e.path, size=e.size, packed=e))
        elif leaf.lower().endswith(".ntb") or leaf == "noteBundle":
            path = e.path if leaf != "noteBundle" else (posixpath.dirname(e.path) or upload_name or e.path)
            if path not in seen:
                seen.add(path)
                reasons.append((path, NTB_REASON))
    bundles.sort(key=lambda b: b.path)
    return bundles, reasons


# --- reading a note -------------------------------------------------------------------

def read_meta(members: Members) -> tuple[str, str, datetime | None]:
    """``(title, subject, created)`` from metadata.plist (SessionInfo:
    noteName, noteSubject, noteCreationDateKey); ``("", "", None)`` when
    the file is missing or unreadable."""
    entry = members.find("metadata.plist")
    if entry is None or entry.size > MAX_SMALL_PLIST:
        return "", "", None
    try:
        arch = Archive(entry.read(), limit=MAX_SMALL_PLIST)
    except (ArchiveError, *READ_ERRORS):
        return "", "", None
    root = arch.root
    return (arch.string(root.get("noteName")) or "", arch.string(root.get("noteSubject")) or "",
            arch.date(root.get("noteCreationDateKey")))


def read_note(members: Members, session: bytes, *, path: str, digest: str = "", ink: bool = True) -> Note:
    """The note whose files are ``members``. ``session`` is its
    Session.plist, which the caller read and hashed (``digest``); ``path``
    is its bundle path (the title's last fallback). With ``ink`` False (a
    preview), the ink hash, groups and shapes are not read: ``curves`` and
    ``shapes`` stay empty. NoteError when it is not a Notability note: no
    bplist00, over MAX_SESSION_BYTES, no keyed archive, a root that is no
    NoteTakingSession, or no richText."""
    if session[:8] != b"bplist00":
        raise NoteError("its Session.plist is no binary plist")
    if len(session) > MAX_SESSION_BYTES:
        raise NoteError(f"its Session.plist is over {MAX_SESSION_BYTES >> 20} MB")
    try:
        arch = Archive(session, limit=MAX_SESSION_BYTES)
    except ArchiveError as exc:
        raise NoteError(f"its Session.plist is unreadable ({exc})") from None
    if not arch.is_a(arch.root, "NoteTakingSession"):
        raise NoteError("not a Notability note")
    rich = arch.get(arch.root, "richText")
    if not isinstance(rich, dict):
        raise NoteError("the note has no text store")
    return _Reader(arch, rich, members, path, digest).read(ink)


class _Reader:
    """One note's reading: the archive, its rich text, its files, and the
    counts of what was read and left out."""

    def __init__(self, arch: Archive, rich: dict, members: Members, path: str, digest: str):
        self.arch, self.rich, self.members = arch, rich, members
        self.note = Note(title="", digest=digest)
        self.path = path
        self.curve_count = 0
        self.archives = 0
        self.shapes: list = []  # (z key, order, Shape)
        self.inner = MAX_SESSION_BYTES  # bytes the plists inside the note may still take (``_spend``)
        self.text_left = MAX_NOTE_TEXT  # characters of typed text still to read (``_text``)
        self.runs_left = MAX_NOTE_RUNS  # formatting runs still to read (``_runs``)
        self.named_missing: set[str] = set()

    def read(self, ink: bool) -> Note:
        arch, root, note = self.arch, self.arch.root, self.note
        name, subject, created = read_meta(self.members)
        note.title = _title(name, arch.string(root.get("name")), arch.string(root.get("packagePath")), self.path)
        subject = next((s.strip() for s in (subject, arch.string(root.get("subject")))
                        if s and s.strip() not in UNFILED), "")
        note.subject = subject[:TITLE_CHARS]
        note.created = arch.date(root.get("creationDate")) or created
        note.version = arch.integer(root.get("sessionFormatVersion"))
        if note.version is not None and note.version > NEWEST_FORMAT:
            note.warnings.append(f"Written by a newer Notability (format {note.version}): some content may be missing")
        model = arch.get(root, "NBNoteTakingSessionDocumentPaperLayoutModelKey")
        sizing = self._paper(model)
        note.width = self._width(sizing)
        self._pdfs()
        self._layout(model)
        if ink:
            self._ink()
        self._media()
        note.flow = self._runs(arch.get(self.rich, "attributedString"))
        self._recordings()
        return note

    def _spend(self, raw: bytes) -> None:
        """Spend ``raw``'s size from what the plists inside the note (its ink
        groups, their archives, its shape lists and dash styles) may take
        in all, MAX_SESSION_BYTES: a blob the archive names many times is
        read again each time, but never past that. ArchiveError once it is
        spent."""
        if len(raw) > self.inner:
            raise ArchiveError("past what the plists inside a note may take")
        self.inner -= len(raw)

    def _text(self, value) -> str:
        """A string of typed text (a text's, a formula's, a caption's)
        without its NUL characters, cut to what is left of the note's
        MAX_NOTE_TEXT characters, which it spends; one cut short or left out
        is counted ("typed text")."""
        text = self.arch.string(value) or ""
        if len(text) > self.text_left:
            self.note.left_out["typed text"] += 1
            text = text[:self.text_left]
        text = text.replace("\x00", "")
        self.text_left -= len(text)
        return text

    def _name(self, value) -> str | None:
        """A file's name or path, or a font's: a string of at most MAX_NAME
        characters (no member of a zip has a longer one); None otherwise."""
        name = self.arch.string(value)
        return name if name and len(name) <= MAX_NAME else None

    def _missing(self, path: str) -> None:
        """Name a file the note lacks, once."""
        if path not in self.named_missing:
            self.named_missing.add(path)
            self.note.missing.append(path)

    # --- page width, paper, PDFs, layout ---------------------------------------------

    def _paper(self, model) -> str:
        """The note's paper (``note.paper``); returns the raw
        paperSizingBehavior, where a locked width may be read."""
        arch, root = self.arch, self.arch.root
        attrs = arch.get(model, "documentPaperAttributes")
        if not (isinstance(attrs, dict) and arch.is_a(attrs, "PaperAttributes")):
            legacy = arch.integer(root.get("paperIndex"))
            self.note.paper = PaperSpec(legacy=legacy, grid=arch.integer(root.get("paperLineStyle")) == 5)
            return ""
        spec = PaperSpec()
        ident = arch.string(attrs.get("paperIdentifier")) or ""
        if ident.startswith("Color:"):
            spec.color = _hex(ident[len("Color:"):])
        elif ident.startswith("TemplatePDF:"):
            spec.template = True
            spec.color = _hex(ident.rsplit(":", 1)[-1])
        elif ident.startswith("Legacy:"):
            tail = ident[len("Legacy:"):]
            spec.legacy = int(tail) if tail.isascii() and tail.isdigit() and len(tail) < 6 else None
        spec.landscape = (arch.string(attrs.get("paperOrientation")) or "").lower() == "landscape"
        size = (arch.string(attrs.get("paperSize")) or "").lower()
        spec.size = size if size in PAPER_SIZES else ""
        sizing = arch.string(attrs.get("paperSizingBehavior")) or ""
        spec.sizing = sizing.split(":", 1)[0] if sizing.split(":", 1)[0] in SIZING else ""
        lines = arch.string(attrs.get("lineStyle2")) or ""
        if lines.startswith("Lines:"):
            spacing = parse_numbers(lines.rsplit(":", 1)[-1], 1)
            if spacing and 0 < spacing[0] <= _MAX_LINE_SPACING:
                spec.lines = spacing[0]
        self.note.paper = spec
        return sizing

    def _width(self, sizing: str) -> float:
        """W: the locked reflow state's page width, else the width in a
        ``lockedWidth:<W>:<device>`` sizing, else 768 for a note from before
        reflow states (2013–14), else 565. A value that is not finite or
        lies outside 16..10,000 falls through."""
        arch = self.arch
        reflow = arch.get(self.rich, "reflowState")
        if isinstance(reflow, dict) and arch.is_a(reflow, "NBReflowStateLocked"):
            width = arch.number(reflow.get("pageWidthInDocumentCoordsKey"))
            if width is not None and _WIDTH_RANGE[0] <= width <= _WIDTH_RANGE[1]:
                return width
        parts = sizing.split(":")
        if parts[0] == "lockedWidth" and len(parts) > 1:
            width = parse_numbers(parts[1], 1)
            if width and _WIDTH_RANGE[0] <= width[0] <= _WIDTH_RANGE[1]:
                return width[0]
        return 768.0 if reflow is None else 565.0

    def _pdfs(self) -> None:
        arch = self.arch
        for pdf in arch.array(self.rich.get("pdfFiles")):
            name = self._name(arch.get(pdf, "pdfFileName"))
            if not name or name in self.note.pdfs:
                continue
            key = self.members.resolve(f"PDFs/{name}") or self.members.resolve(name)
            if key is not None:
                version = arch.integer(arch.get(pdf, "version"))
                self.note.pdfs[name] = PdfFile(name, key, version if version and version > 0 else 1)

    def _layout(self, model) -> None:
        arch, note = self.arch, self.note
        entries = arch.array(arch.get(model, "pageLayoutArray")) or arch.array(self.rich.get("pageLayoutArray"))
        for entry in entries[:MAX_NOTE_PAGES]:
            m = arch.mapping(entry)
            page = arch.integer(m.get("kPageLayoutPDFPageNumberKey"))
            name = self._name(m.get("kPageLayoutPDFFileNameKey")) or self._name(
                arch.get(m.get("kPageLayoutPDFFileKey"), "pdfFileName"))
            if page is None or not 1 <= page <= _MAX_PAGE_NUMBER or not name:
                note.layout.append(LayoutEntry(None, None))
                continue
            note.layout.append(LayoutEntry(name, page))
            if name not in note.pdfs:
                self._missing(f"PDFs/{name}")

    # --- ink ----------------------------------------------------------------------------

    def _ink(self) -> None:
        arch = self.arch
        overlay = arch.get(self.rich, "Handwriting Overlay")
        spatial = arch.get(overlay, "SpatialHash")
        if isinstance(spatial, dict):
            self.note.curves = self._hash(arch, spatial, IDENTITY, 0, None)
        self.shapes.sort(key=lambda item: (item[0], item[1]))
        self.note.shapes = [shape for _key, _order, shape in self.shapes]

    def _hash(self, arch: Archive, h: dict, transform: tuple, depth: int, zkey) -> list[Curve]:
        """One InkedSpatialHash's curves in drawing order, its ink groups'
        merged in before the curve whose index is the group's; its shapes
        go to ``self.shapes``, keyed by their index (``zkey``, a group's
        index, for the shapes inside a group)."""
        note = self.note
        n_curves = max(0, arch.integer(h.get("numcurves")) or 0)
        n_points = max(0, arch.integer(h.get("numpoints")) or 0)
        n_anchors = max(0, arch.integer(h.get("numfractionalwidths")) or 0)
        counts = _unpack(arch.data(h.get("curvesnumpoints")), "i", n_curves)
        points = _unpack(arch.data(h.get("curvespoints")), "f", 2 * n_points)
        widths = _unpack(arch.data(h.get("curveswidth")), "f", n_curves)
        colors = arch.data(h.get("curvescolors")) or b""
        styles = arch.data(h.get("curvesstyles"))
        fractional = _unpack(arch.data(h.get("curvesfractionalwidths")), "f", n_anchors)
        scale = (math.hypot(transform[0], transform[1]) + math.hypot(transform[2], transform[3])) / 2
        ink = InkHash(points, fractional,
                      _unpack(arch.data(h.get("curvesforces")), "f", n_anchors, exact=True),
                      _unpack(arch.data(h.get("curvesaltitudeangles")), "f", n_anchors, exact=True),
                      _unpack(arch.data(h.get("curvesazimuthunitvector")), "f", 2 * n_anchors, exact=True),
                      transform, scale)
        dashes = self._dashes(arch.data(h.get("dashStyles")))
        kept: list[tuple[int, Curve]] = []
        p = f = 0
        complete = True
        for i in range(n_curves):
            n = counts[i] if i < len(counts) else 0
            if i >= len(widths) or 4 * i + 4 > len(colors) or n < 1 or 2 * (p + n) > len(points):
                note.left_out["cut short"] += n_curves - i  # the offsets after it cannot be trusted
                complete = False
                break
            if self.curve_count >= MAX_CURVES:
                note.left_out["curves"] += n_curves - i
                complete = False
                break
            k, r = divmod(n - 1, 3)
            anchors = k + 1 if r == 0 else n
            rgba = tuple(colors[4 * i:4 * i + 4])
            style = styles[i] if styles is not None and i < len(styles) else (
                STYLE_HIGHLIGHTER if rgba[3] < 255 else STYLE_PEN)
            if n > MAX_CURVE_POINTS:
                note.left_out["stroke"] += 1
            elif style != STYLE_HIDDEN and rgba[3]:  # style 6 Notability does not draw; alpha 0 shows nothing
                kept.append((i, Curve(ink, p, n, f, r == 0, widths[i] * scale, rgba, style, dashes.get(i))))
                self.curve_count += 1
            p += n
            f += anchors
        if (complete and f != n_anchors) or len(fractional) < f:
            ink.fractional = ink.force = ink.altitude = ink.azimuth = None
            message = "The handwriting's width data does not match its strokes: they are drawn at an even width"
            if (n_anchors or fractional) and message not in note.warnings:
                note.warnings.append(message)
        self._shape_list(arch.data(h.get("shapes")), transform, scale, zkey)
        groups = self._groups(arch, h, transform, depth, n_curves)
        out: list[Curve] = []
        g = 0
        for i, curve in kept:
            while g < len(groups) and groups[g][0] <= i:
                out += groups[g][2]
                g += 1
            out.append(curve)
        for _index, _order, curves in groups[g:]:
            out += curves
        return out

    def _dashes(self, data: bytes | None) -> dict[int, str]:
        """``dashStyles``: a plain plist ``{objectPatterns: {"<curve index>":
        {pattern: 1 dashed | 2 dotted}}}``, keyed by the curve's index in its
        hash."""
        if not data:
            return {}
        try:
            self._spend(data)
            plist = load_plist(data, limit=MAX_SESSION_BYTES)
        except ArchiveError:
            return {}
        patterns = plist.get("objectPatterns") if isinstance(plist, dict) else None
        out = {}
        for key, value in (patterns.items() if isinstance(patterns, dict) else ()):
            if isinstance(key, str) and key.isascii() and key.isdigit() and len(key) < 10 and isinstance(value, dict):
                dash = {1: "dashed", 2: "dotted"}.get(whole(value.get("pattern")))
                if dash:
                    out[int(key)] = dash
        return out

    def _groups(self, arch: Archive, h: dict, transform: tuple, depth: int, n_curves: int) -> list:
        """The hash's ink groups as ``[(index, order, curves)]`` sorted by
        their place among its curves. A group's transform is composed with
        the one around it; a type 1 item is a whole archive of another
        hash, a type 2 item a shape."""
        note = self.note
        out = []
        for order, element in enumerate(arch.array(h.get("groupsArrays"))):
            raw = arch.data(element)
            if raw is None:
                continue
            if depth >= MAX_GROUP_DEPTH or self.archives >= MAX_GROUP_ARCHIVES:
                note.left_out["group"] += 1
                continue
            self.archives += 1
            try:
                self._spend(raw)
                plist = load_plist(raw, limit=MAX_SESSION_BYTES)
            except ArchiveError:
                note.left_out["group"] += 1
                continue
            group = plist.get("inkGroup") if isinstance(plist, dict) else None
            own = _transform(group.get("transform")) if isinstance(group, dict) else None
            if own is None:
                note.left_out["group"] += 1
                continue
            index = whole(plist.get("index"))
            index = n_curves if index is None else index
            composed = _compose(transform, own)
            scale = (math.hypot(composed[0], composed[1]) + math.hypot(composed[2], composed[3])) / 2
            look = group.get("appearance") if isinstance(group.get("appearance"), dict) else {}
            curves: list[Curve] = []
            items = group.get("inkGroupObjects")
            for item in items if isinstance(items, list) else ():
                if not isinstance(item, dict):
                    continue
                kind, obj = whole(item.get("type")), item.get("object")
                if kind == 1 and isinstance(obj, (bytes, bytearray)):
                    if self.archives >= MAX_GROUP_ARCHIVES:
                        note.left_out["group"] += 1
                        continue
                    self.archives += 1
                    try:
                        self._spend(obj)
                        inner = Archive(bytes(obj), limit=MAX_SESSION_BYTES)
                    except ArchiveError:
                        note.left_out["group"] += 1
                        continue
                    curves += self._hash(inner, inner.root, composed, depth + 1, index)
                elif kind == 2 and isinstance(obj, dict):
                    if len(self.shapes) >= MAX_SHAPES:
                        note.left_out["shapes limit"] += 1
                        continue
                    shape = self._shape(_grouped_kind(obj), obj, composed, scale, look, grouped=True)
                    if shape is not None:
                        self.shapes.append((index, len(self.shapes), shape))
            out.append((index, order, curves))
        out.sort(key=lambda item: (item[0], item[1]))
        return out

    def _shape_list(self, raw: bytes | None, transform: tuple, scale: float, zkey) -> None:
        """A hash's ``shapes`` plist: ``shapes``, ``kinds`` and ``indices``
        side by side (the shortest wins), up to the note's MAX_SHAPES."""
        if not raw:
            return
        try:
            self._spend(raw)
            plist = load_plist(raw, limit=MAX_SESSION_BYTES)
        except ArchiveError:
            self.note.left_out["shape"] += 1
            return
        if not isinstance(plist, dict):
            return
        shapes, kinds, indices = (plist.get(k) for k in ("shapes", "kinds", "indices"))
        if not all(isinstance(v, list) for v in (shapes, kinds, indices)):
            return
        listed = min(len(shapes), len(kinds), len(indices))
        room = max(0, MAX_SHAPES - len(self.shapes))
        if listed > room:
            self.note.left_out["shapes limit"] += listed - room
        for shape_dict, kind, index in islice(zip(shapes, kinds, indices), room):
            shape = self._shape(kind, shape_dict, transform, scale, None, grouped=False)
            if shape is not None:
                key = zkey if zkey is not None else (whole(index) or 0)
                self.shapes.append((key, len(self.shapes), shape))

    def _shape(self, kind, d, transform: tuple, scale: float, group_look, *, grouped: bool) -> Shape | None:
        note = self.note
        if kind == "partialshape":
            note.left_out["partial shape"] += 1
            return None
        if kind not in SHAPE_KINDS or not isinstance(d, dict):
            note.left_out["shape"] += 1
            return None
        look = d.get("appearance") if isinstance(d.get("appearance"), dict) else (group_look or d)
        width = finite(look.get("strokeWidth"))
        if width is None and group_look:
            width = finite(group_look.get("strokeWidth"))
        dash_style = look.get("dashStyle") if isinstance(look.get("dashStyle"), dict) else {}
        pattern = whole(dash_style.get("pattern"))
        control = None
        if kind == "line":
            raw = [d.get("startPt"), d.get("endPt")]
            if d.get("controlPoint1") is not None:
                control = _point(d.get("controlPoint1"), transform)
                if control is None:
                    raw.append(None)  # a control point that does not read: the shape does not
        elif kind in ("circle", "ellipse"):
            rect = d.get("rotatedRect")
            raw = rect.get("corners") if isinstance(rect, dict) else None
            raw = raw if isinstance(raw, list) and len(raw) == 4 else [None]
        else:
            raw = d.get("points") if isinstance(d.get("points"), list) else [None]
            raw = raw if len(raw) <= MAX_CURVE_POINTS else [None]  # a curve's cap: more is damage
        points = [_point(v, transform) for v in raw]
        if not points or any(v is None for v in points) or width is None or width <= 0:
            note.left_out["stroke"] += 1
            return None
        color = _rgba(look.get("strokeColor")) or (group_look and _rgba(group_look.get("strokeColor"))) or (0, 0, 0, 1)
        return Shape(kind=kind, points=points, control=control, arrow=whole(dash_style.get("arrowHead")) == 1,
                     width=width * scale, rgba=color, fill=_rgba(look.get("fillColor")),
                     highlighter=whole(look.get("style")) == STYLE_HIGHLIGHTER,
                     dash={1: "dashed", 2: "dotted"}.get(pattern), grouped=grouped)

    # --- media ---------------------------------------------------------------------------

    def _media(self) -> None:
        arch, note = self.arch, self.note
        found = []
        objects = arch.array(self.rich.get("mediaObjects"))
        if len(objects) > MAX_MEDIA:
            note.left_out["media limit"] += len(objects) - MAX_MEDIA
            objects = objects[:MAX_MEDIA]
        for order, obj in enumerate(objects):
            if not isinstance(obj, dict):
                continue
            if arch.is_a(obj, "TextBlockMediaObject"):
                kind = "text"
            elif arch.is_a(obj, "MathMediaObject"):
                kind = "math"
            elif arch.is_a(obj, "ImageMediaObject", "GifMediaObject"):
                kind = "picture"
            elif arch.is_a(obj, "CanvasMediaObject"):
                kind = "sticky"
            else:
                note.left_out["media"] += 1
                note.classes[arch.class_name(obj).rsplit(".", 1)[-1] or "unnamed"] += 1
                continue
            origin = parse_numbers(arch.string(obj.get("documentOrigin")), 2) or parse_numbers(
                arch.string(obj.get("documentContentOrigin")), 2)
            size = parse_numbers(arch.string(obj.get("unscaledContentSize")), 2)
            if (origin is None or size is None or size[0] <= 0 or size[1] <= 0
                    or max(abs(v) for v in (*origin, *size)) > COORD_LIMIT):
                note.left_out["object"] += 1
                continue
            media = Media(kind=kind, x=origin[0], y=origin[1], w=size[0], h=size[1],
                          rotation=arch.number(obj.get("rotationDegrees")) or 0.0,
                          z=arch.number(obj.get("zIndex")) or 0.0)
            paper = arch.get(arch.get(obj, "paperStyleObject"), "paperColor")
            if kind == "text":
                media.runs = self._runs(arch.get(arch.get(obj, "textStore"), "attributedString"))
                media.background = arch.color(paper)
            elif kind == "math":
                media.latex = self._text(obj.get("latex"))
                media.rgba = arch.color(obj.get("color"))
            elif kind == "sticky":
                media.background = arch.color(paper) or STICKY_COLOR
            elif not self._picture(obj, media):
                continue
            found.append((media.z, order, media))
        found.sort(key=lambda item: (item[0], item[1]))
        note.media = [media for _z, _order, media in found]

    def _picture(self, obj: dict, media: Media) -> bool:
        """A picture's image, crop, flips and caption; False when it names
        no image at all (counted as unreadable)."""
        arch, note = self.arch, self.note
        figure = arch.get(obj, "figure")
        snapshot = arch.get(arch.get(figure, "FigureBackgroundObjectKey"), "kImageObjectSnapshotKey")
        rel = self._name(arch.get(snapshot, "relativePath"))
        if not rel:
            snapshot = arch.get(obj, "contentSnapshot")
            rel = self._name(arch.get(snapshot, "relativePath"))
        if not rel:
            note.left_out["object"] += 1
            return False
        key = None if arch.flag(arch.get(snapshot, "imageIsMissing")) else self.members.resolve(rel)
        if key is None:
            self._missing(rel)
        media.picture = key
        media.crop = parse_numbers(arch.string(arch.get(figure, "FigureCropRectKey")), 4)
        media.flip_h = arch.flag(obj.get("isFlippedHorizontal"))
        media.flip_v = arch.flag(obj.get("isFlippedVertical"))
        if arch.flag(obj.get("isCaptionEnabled")):
            media.caption = self._text(obj.get("captionFieldText")).strip()
        return True

    # --- text -----------------------------------------------------------------------------

    def _runs(self, attributed) -> list[Run]:
        """An attributed string (``stringKey`` and its ``subRangesKey``
        runs, an NSDictionary or an object holding them) as runs: the
        ranges are UTF-16 offsets, clamped to the string; an overlap keeps
        the first run and a gap is plain text. The text is typed text
        (``_text``); a run covers a UTF-16 unit at least, so no more runs
        are read than the text has units, and one more. A run object named
        again in the text is read once, and every run read spends one of
        the note's MAX_NOTE_RUNS: past them a text keeps its words only
        (counted, "formatting")."""
        arch = self.arch
        attributed = arch.mapping(attributed) or arch.deref(attributed)
        text = self._text(arch.get(attributed, "stringKey"))
        if not text:
            return []
        if max(text) <= "\uffff":  # every character one UTF-16 unit: an offset is an index
            bounds, units = None, len(text)
        else:  # the UTF-16 offset where each character starts, and the end
            bounds = array("q", accumulate((2 if ord(ch) > 0xFFFF else 1 for ch in text), initial=0))
            units = bounds[-1]
        ranged, read = [], set()
        for order, sub in enumerate(arch.array(arch.get(attributed, "subRangesKey"))[:units + 1]):
            if id(sub) in read:
                continue
            if self.runs_left <= 0:
                self.note.left_out["formatting"] += 1
                break
            read.add(id(sub))
            self.runs_left -= 1
            m = arch.mapping(sub)
            span = parse_numbers(arch.string(m.get("subRangeRangeKey")), 2)
            if span is None:
                continue
            start, end = _char_at(bounds, span[0], len(text)), _char_at(bounds, span[0] + span[1], len(text))
            if end > start:
                ranged.append((start, order, end, self._style(m)))
        ranged.sort(key=lambda item: (item[0], item[1]))
        runs, cursor = [], 0
        for start, _order, end, style in ranged:
            start = max(start, cursor)
            if end <= start:
                continue
            if start > cursor:
                runs.append(Run(text[cursor:start]))
            runs.append(Run(text[start:end], **style))
            cursor = end
        if cursor < len(text):
            runs.append(Run(text[cursor:]))
        return runs

    def _style(self, m: dict) -> dict:
        arch = self.arch
        font = arch.mapping(m.get("subRangeFontKey"))
        face = (self._name(font.get("NSFontNameAttribute")) or "").rsplit("-", 1)
        traits = face[1] if len(face) == 2 else ""
        size = arch.number(font.get("NSFontSizeAttribute"))
        rgba = color_string(arch.string(m.get("subRangeColorCrossPlatformKey"))) or arch.color(m.get("subRangeColorKey"))
        other = arch.mapping(m.get("subRangeOtherAttributesKey"))
        mark = arch.color(other.get("highlight"))
        list_style = arch.integer(other.get("indent-decoration-style")) or 0
        number = arch.integer(other.get("indent-decoration-number"))
        return {"bold": "Bold" in traits, "italic": "Italic" in traits or "Oblique" in traits,
                "strike": arch.flag(other.get("NSStrikethrough")), "mark": bool(mark and mark[3] > 0),
                "size": size if size and size > 0 else None, "rgba": rgba,
                "list_style": list_style if list_style in (1, 2, 3) else 0,
                "level": min(max(arch.integer(other.get("indent-level")) or 0, 0), 32),
                "number": number if number and 0 < number < 1_000_000 else 1,
                "checked": arch.flag(other.get("checklist-checked"))}

    # --- recordings ------------------------------------------------------------------------

    def _recordings(self) -> None:
        """``Recordings/library.plist`` (XML) lists the recordings by
        identifier; without a readable one, every ``Recordings/*.m4a`` of
        the note is a recording, by name."""
        note, members = self.note, self.members
        listed = None
        library = members.find("Recordings/library.plist")
        if library is not None and library.size <= MAX_SMALL_PLIST:
            try:
                plist = load_plist(library.read(), limit=MAX_SMALL_PLIST)
                listed = plist.get("recordings") if isinstance(plist, dict) else None
            except (ArchiveError, *READ_ERRORS):
                listed = None
        if isinstance(listed, dict):
            found = []
            for key, rec in listed.items():
                if not isinstance(rec, dict) or not isinstance(rec.get("filepath"), str) or not rec["filepath"]:
                    continue
                ident = whole(rec.get("identifier"))
                if ident is None and isinstance(key, str) and key.isdigit() and len(key) < 10:
                    ident = int(key)
                rel = f"Recordings/{rec['filepath']}"
                name = next((v.strip() for v in (rec.get("userFacingName"), rec.get("name"))
                             if isinstance(v, str) and v.strip()), "") or _stem(rec["filepath"])
                created = utc(rec["createddate"]) if isinstance(rec.get("createddate"), datetime) else None
                found.append((ident if ident is not None else 1 << 62, len(found), rel, name, created))
            found.sort(key=lambda item: (item[0], item[1]))
            refs = [(rel, name, created) for _i, _o, rel, name, created in found]
        else:
            refs = sorted(((key, _stem(key), None) for key in members.entries
                           if key.lower().startswith("recordings/") and key.lower().endswith(".m4a")
                           and "/" not in key[len("recordings/"):]), key=lambda item: item[0].lower())
        taken = set()
        for rel, name, created in refs:
            key = members.resolve(rel)
            if key is None:
                self._missing(rel)
            elif key in taken:  # a file listed twice is one recording
                continue
            taken.add(key)
            note.recordings.append(RecordingRef(key, unicodedata.normalize("NFC", name)[:TITLE_CHARS], created))


# --- small readers -------------------------------------------------------------------------

def _title(meta: str, name: str | None, package: str | None, path: str) -> str:
    """Metadata noteName, else the root's name, else packagePath, else the
    bundle path's last part without ``.note``/``.nbn``; NFC, stripped, at
    most TITLE_CHARS characters, "Untitled note" when empty."""
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    for ext in (".note", ".nbn"):
        if leaf.lower().endswith(ext):
            leaf = leaf[:-len(ext)]
    for value in (meta, name, package, leaf):
        value = unicodedata.normalize("NFC", (value or "").replace("\x00", "")).strip()
        if value:
            return value[:TITLE_CHARS]
    return "Untitled note"


def _stem(path: str) -> str:
    leaf = path.rsplit("/", 1)[-1]
    dot = leaf.rfind(".")
    return leaf[:dot] if dot > 0 else leaf


def _hex(text: str) -> str | None:
    text = text.strip()
    if len(text) == 7 and text[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in text[1:]):
        return text.lower()
    return None


def _char_at(bounds: array | None, offset: float, n: int) -> int:
    """The character index a UTF-16 offset falls at in a text of ``n``
    characters, clamped to it; an offset inside a surrogate pair moves to
    the character after it. ``bounds`` holds where each character starts
    and the end, None when every character is one unit."""
    offset = max(0.0, offset)
    if bounds is None:
        return min(math.ceil(offset), n)
    return min(bisect.bisect_left(bounds, offset), n)


def _unpack(data: bytes | None, code: str, count: int, exact: bool = False) -> array | None:
    """Up to ``count`` little-endian values of type ``code`` from ``data``
    (all of them, or None, with ``exact``): checked against the count
    before anything is unpacked, and never a list of Python numbers."""
    out = array(code)
    size = out.itemsize
    if exact:
        if data is None or len(data) != count * size:
            return None
    elif not data:
        return out
    out.frombytes(memoryview(data)[:min(len(data) // size, count) * size])  # no copy of the slice
    if sys.byteorder == "big":
        out.byteswap()
    return out


def _transform(value) -> tuple | None:
    """A CGAffineTransform ``[a, b, c, d, tx, ty]``; None unless all six
    numbers are finite."""
    if not isinstance(value, list) or len(value) != 6:
        return None
    values = [finite(v) for v in value]
    return None if None in values else tuple(values)


def _compose(outer: tuple, inner: tuple) -> tuple:
    """``outer`` after ``inner``: a point is moved by ``inner`` first."""
    a1, b1, c1, d1, x1, y1 = outer
    a2, b2, c2, d2, x2, y2 = inner
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * x2 + c1 * y2 + x1, b1 * x2 + d1 * y2 + y1)


def _point(value, transform: tuple) -> tuple[float, float] | None:
    """A shape's point (``[x, y]``, or a "{x, y}" string) through the
    transform; None unless finite and within COORD_LIMIT."""
    if isinstance(value, str):
        xy = parse_numbers(value, 2)
    elif isinstance(value, list) and len(value) == 2:
        xy = tuple(finite(v) for v in value)
    else:
        return None
    if not xy or None in xy:
        return None
    a, b, c, d, tx, ty = transform
    x, y = a * xy[0] + c * xy[1] + tx, b * xy[0] + d * xy[1] + ty
    return (x, y) if abs(x) <= COORD_LIMIT and abs(y) <= COORD_LIMIT else None


def _rgba(value) -> tuple | None:
    """A shape's colour, ``{rgba: [r, g, b, a]}`` in 0..1 (alpha 1 when
    missing)."""
    rgba = value.get("rgba") if isinstance(value, dict) else None
    if not isinstance(rgba, list) or len(rgba) not in (3, 4):
        return None
    values = [finite(v) for v in rgba]
    if None in values:
        return None
    if len(values) == 3:
        values.append(1.0)
    return tuple(min(1.0, max(0.0, v)) for v in values)


def _grouped_kind(shape: dict) -> str:
    """A shape inside an ink group carries no kind of its own: what its keys
    hold says what it is."""
    if "rotatedRect" in shape:
        return "ellipse"
    if "startPt" in shape:
        return "line"
    return "polygon" if "points" in shape else ""
