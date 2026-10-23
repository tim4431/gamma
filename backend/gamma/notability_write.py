"""Writing a Notability note: Notability's "Note" export, a ``.note``, the
inverse of gamma/notability.py (the format and what is known of writing it:
docs/research/notability-format.md "Writing a note").

A ``.note`` is a zip of one folder: ``Session.plist`` (the whole note, a
GLKeyedArchiver keyed archive), ``metadata.plist``,
``Recordings/library.plist``, a thumbnail, and the note's files: its PDF
under ``PDFs/``, its pictures under ``Images/`` (an animated GIF's
animation under ``Assets/<id>/``) and its recordings under ``Recordings/``.
Everything is written in the shape Notability 16.1.5 writes it, key for
key, as the Note export in tests/samples/notability has it: the same
classes, the same keys, each value inline or behind a UID as the app puts
it, so its unarchiver finds every key it decodes where it looks.

The input is a ``Draft``, already in document units (gamma/notability_export.py
makes one of a Gamma page): this module knows the file, not Gamma. A
stroke is written as a C1 Bézier chain whose segments are straight (the
controls at a third and two thirds of each chord), its samples the
anchors, so Notability draws the polyline the samples make and a reader
gets the samples back; one past ``MAX_ANCHORS`` anchors goes in pieces of
one UUID, each starting where the last ended.
"""

import datetime
import io
import math
import plistlib
import posixpath
import shutil
import struct
import unicodedata
import uuid as uuidlib
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .notability import PRIVATE_KEY
from .notability_archive import APPLE_EPOCH

APP_VERSION = "16.1.5"           # the version whose files this writer copies
FORMAT_VERSION = 9               # its sessionFormatVersion
MAX_ANCHORS = 3334               # anchors in one curve: 1 + 3 × 3333 = 10,000 points, the reader's cap
INT64_MAX = 9223372036854775807  # a layout entry's page number for a blank page
FONT = "HelveticaNeue"
BLACK = (0.0, 0.0, 0.0, 1.0)
THUMB_SIZE = (576, 756)          # thumb12x.png, as 16.1.5 writes it
STYLE_PEN, STYLE_HIGHLIGHTER, STYLE_PENCIL = 3, 4, 5
_HALF_PI = math.pi / 2


# --- what is written ------------------------------------------------------------------------

@dataclass
class Run:
    """Typed text of one look. ``font`` is a PostScript name, bold and
    italic its suffix (``HelveticaNeue-BoldItalic``); ``size`` in document
    units; colours 0..1. A list paragraph's runs carry its ``list_style``
    (1 bullet, 2 numbered, 3 checklist), ``level``, ``number`` and the
    ``list_id`` its list shares."""
    text: str
    font: str = FONT
    size: float = 12.0
    rgba: tuple = BLACK
    strike: bool = False
    underline: bool = False
    mark: tuple | None = None
    list_style: int = 0
    level: int = 0
    number: int = 1
    checked: bool = False
    list_id: int = 0


@dataclass
class Stroke:
    """One stroke: its samples as the anchors of a straight Bézier chain,
    in document units, with per-anchor fractional widths, raw forces,
    altitudes (radians) and azimuth unit vectors (None: Notability's
    unknown, 1.0, π/2 and (1, 0)); ``width`` is curveswidth, ``rgba`` the
    colour's bytes, ``dash`` 1 dashed or 2 dotted, ``z`` its place in the
    note's drawing order, which shapes share."""
    anchors: list
    width: float
    rgba: tuple
    widths: list | None = None
    forces: list | None = None
    altitudes: list | None = None
    azimuths: list | None = None
    style: int = STYLE_PEN
    dash: int = 0
    uuid: bytes = b""
    z: int = 0


@dataclass
class Shape:
    """A shape of the shape list: a ``line`` (``points`` its start and end,
    ``arrow`` its head), a ``rectangle``, ``square``, ``triangle`` or
    ``polygon`` (its corners, closed), or a ``circle`` or ``ellipse`` (the
    four corners of the turned rectangle it is drawn in); colours 0..1."""
    kind: str
    points: list
    width: float
    rgba: tuple = BLACK
    fill: tuple | None = None
    arrow: bool = False
    style: int = STYLE_PEN
    dash: int = 0
    uuid: bytes = b""
    z: int = 0


@dataclass
class TextBox:
    x: float
    y: float
    w: float
    h: float
    runs: list
    background: tuple | None = None   # its paper colour, 0..1; None is transparent
    z: int = 0
    uuid: bytes = b""


@dataclass
class Picture:
    """A picture shown in its frame: ``image`` is its file below the note's
    folder, ``pixels`` that file's size; ``gif`` the asset id whose
    ``Assets/<id>/media.gif`` is its animation (``image`` then holds the
    still frame)."""
    x: float
    y: float
    w: float
    h: float
    image: str
    pixels: tuple
    jpeg: bool = False
    gif: str | None = None
    z: int = 0
    uuid: bytes = b""


@dataclass
class Sticky:
    x: float
    y: float
    w: float
    h: float
    rgba: tuple
    rotation: float = 0.0              # radians, about its centre
    z: int = 0
    uuid: bytes = b""


@dataclass
class Recording:
    identifier: int
    name: str
    filepath: str                      # below Recordings/
    created: datetime.datetime
    duration: float                    # seconds
    size: int


@dataclass
class Paper:
    """The note's paper: its size (``letter``, ``a4``, ``legal``) and
    orientation, how its width is held (``lockedWidth`` at the note's W,
    or ``staticWidth``), its identifier (``Color:#RRGGBB``) and, for ruled
    paper, the inches between lines."""
    size: str = "letter"
    landscape: bool = False
    sizing: str = "lockedWidth"
    identifier: str = "Color:#FFFFFF"
    lines: float | None = None


@dataclass
class Draft:
    title: str
    width: float                       # W, the page width in document units
    paper: Paper = field(default_factory=Paper)
    created: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.UTC))
    modified: datetime.datetime | None = None
    layout: list = field(default_factory=list)     # [(PDF file name, 1-based page)], (None, None) a blank page
    pdfs: list = field(default_factory=list)       # the PDFs' file names, below PDFs/
    strokes: list = field(default_factory=list)    # [Stroke]
    shapes: list = field(default_factory=list)     # [Shape]
    media: list = field(default_factory=list)      # [TextBox | Picture | Sticky]
    flow: list = field(default_factory=list)       # [Run]: the text typed on the pages
    recordings: list = field(default_factory=list)  # [Recording]
    files: dict = field(default_factory=dict)      # path below the note's folder → bytes, or a Path to copy
    thumbnail: bytes | None = None                 # thumb12x.png; None writes a blank page of the paper
    private: dict | None = None                    # Gamma's own copy of what the note holds (PRIVATE_KEY)


def new_uuid() -> bytes:
    return uuidlib.uuid4().bytes


def note_folder(title: str) -> str:
    """The note's folder in the zip: its title as a single path part (the
    characters a file name cannot hold taken out), "Untitled note" when
    nothing is left."""
    name = "".join(" " if c in '/\\:*?"<>|' or ord(c) < 32 else c for c in unicodedata.normalize("NFC", title))
    name = " ".join(name.split()).strip(". ")[:200]
    return name or "Untitled note"


# --- the keyed archive ------------------------------------------------------------------------

class _Archive:
    """An NSKeyedArchiver ``$objects`` graph as GLKeyedArchiver writes one:
    every object an entry the others point at by UID, its ``$class`` a
    shared ``{$classname, $classes}``; strings, numbers and data that are
    the same value share one entry, as the app's constants do (only values
    nothing can change: a mutable string, array or dictionary is always
    its own)."""

    def __init__(self, archiver: str = "GLKeyedArchiver", top_key: str = "$0"):
        self.objects: list = ["$null", None]  # 1 is the root's, filled by ``dumps``
        self.archiver, self.top_key = archiver, top_key
        self._classes: dict = {}
        self._values: dict = {}

    def add(self, value) -> plistlib.UID:
        self.objects.append(value)
        return plistlib.UID(len(self.objects) - 1)

    def value(self, value) -> plistlib.UID:
        """A string, number, bool or data as a shared entry (bool, int and
        float kept apart: True, 1 and 1.0 are three)."""
        key = (type(value), value)
        uid = self._values.get(key)
        if uid is None:
            uid = self._values[key] = self.add(value)
        return uid

    def _class(self, name: str, chain) -> plistlib.UID:
        uid = self._classes.get(name)
        if uid is None:
            entry = {"$classname": name}
            if chain is not None:
                entry["$classes"] = list(chain)
            if name == "UIColor":
                entry["$classhints"] = ["NSColor"]
            uid = self._classes[name] = self.add(entry)
        return uid

    def obj(self, name: str, fields: dict, chain=()) -> plistlib.UID:
        """An object of class ``name``, its ``$classes`` the name, ``chain``
        (its superclasses below NSObject) and NSObject; None writes no
        ``$classes``, as the app writes InkedSpatialHash. A class of the
        app's own Swift module is named with it and listed without it
        (``Notability.NoteDocumentPaperLayoutModel``); GLModel's keep it in
        both."""
        own = name[len("Notability."):] if name.startswith("Notability.") else name
        return self.add({**fields, "$class": self._class(name, None if chain is None else [own, *chain, "NSObject"])})

    def array(self, items, mutable: bool = True) -> plistlib.UID:
        name = "NSMutableArray" if mutable else "NSArray"
        return self.obj(name, {"NS.objects": list(items)}, ("NSArray",) if mutable else ())

    def dictionary(self, pairs) -> plistlib.UID:
        """An NSMutableDictionary of string keys; each value a UID already,
        or a plain value stored as a shared entry."""
        keys, values = [], []
        for k, v in pairs:
            keys.append(self.value(k))
            values.append(v if isinstance(v, plistlib.UID) else self.value(v))
        return self.obj("NSMutableDictionary", {"NS.keys": keys, "NS.objects": values}, ("NSDictionary",))

    def string(self, text: str) -> plistlib.UID:
        return self.value(text)

    def mutable_string(self, text: str) -> plistlib.UID:
        return self.obj("NSMutableString", {"NS.bytes": text.encode("utf-8")}, ("NSString",))

    def date(self, when: datetime.datetime) -> plistlib.UID:
        return self.obj("NSDate", {"NS.time": when.timestamp() - APPLE_EPOCH})

    def uuid(self, raw: bytes) -> plistlib.UID:
        return self.obj("NSUUID", {"NS.uuidbytes": raw})

    def color(self, rgba) -> plistlib.UID:
        """A UIColor as the app writes an RGB one: each component as a
        float, then ``-Double`` with the exact value when the float is not
        it, and NSRGB its ASCII form, the alpha only when it is not 1."""
        fields = {"UIColorComponentCount": 4}
        for key, v in zip(("UIRed", "UIGreen", "UIBlue", "UIAlpha"), rgba):
            single = struct.unpack("<f", struct.pack("<f", v))[0]
            fields[key] = single
            if single != v:
                fields[f"{key}-Double"] = v
        shown = list(rgba[:3]) + ([rgba[3]] if rgba[3] != 1 else [])
        fields["NSRGB"] = " ".join(_short(v) for v in shown).encode("ascii")
        fields["NSColorSpace"] = 2
        return self.obj("UIColor", fields)

    def gray(self, white: float, alpha: float) -> plistlib.UID:
        shown = [white] + ([alpha] if alpha != 1 else [])
        return self.obj("UIColor", {"UIColorComponentCount": 2, "UIWhite": white, "UIAlpha": alpha,
                                    "NSWhite": " ".join(_short(v) for v in shown).encode("ascii"),
                                    "NSColorSpace": 4})

    def dumps(self, root: dict, root_class: str) -> bytes:
        self.objects[1] = {**root, "$class": self._class(root_class, [root_class, "NSObject"])}
        return plistlib.dumps({"$version": 100000, "$archiver": self.archiver, "$top": {self.top_key: plistlib.UID(1)},
                               "$objects": self.objects}, fmt=plistlib.FMT_BINARY, sort_keys=False)


def _short(v: float) -> str:
    """A colour component as NSRGB spells it: three significant digits."""
    return "%.3g" % v


def _num(v: float) -> str:
    """A number in a geometry string, as the app writes it (17 significant
    digits; a whole number without a point)."""
    return "%.17g" % v


def point(x: float, y: float) -> str:
    return "{%s, %s}" % (_num(x), _num(y))


def rect(x: float, y: float, w: float, h: float) -> str:
    return "{{%s, %s}, {%s, %s}}" % (_num(x), _num(y), _num(w), _num(h))


def cross_platform(rgba) -> str:
    return ",".join("%.15f" % v for v in rgba)


def _f32(values) -> bytes:
    values = list(values)
    return struct.pack(f"<{len(values)}f", *values)


# --- Session.plist ------------------------------------------------------------------------------

class _Session:
    """One note's Session.plist being made, its archive's objects added as
    each part of the draft is written."""

    def __init__(self, draft: Draft):
        self.draft = draft
        self.arch = _Archive()

    def dumps(self) -> bytes:
        a, d = self.arch, self.draft
        rich = self._rich(d.flow, main=True)
        model = a.obj("Notability.NoteDocumentPaperLayoutModel", {
            "documentPaperAttributes": self._paper_attributes(d.paper, d.width),
            "pageLayoutArray": self._layout()})
        events = a.obj("NBCPEventManager", {
            "NBCPTimeManagerSOAEventUUIDsKey": b"", "NBCPTimeManagerSOATimestampsKey": b"",
            "NBCPTimeManagerSOANumEventsKey": 0, "NBCPTimeManagerSOARecordingIDsKey": b"",
            "NBCPTimeManagerSOADurationsKey": b""})
        root = {
            "subject": a.string("unsortedNotesKey"),
            "NBNoteTakingSessionBundleVersionNumberKey": a.string(APP_VERSION),
            "NBNoteTakingSessionHandwritingLanguageKey": a.string("en_US"),
            "contentPlaybackEventManager": events,
            "NBPaperMinorVersionNumber": 0,
            "NBNoteTakingSessionLastUsedFontKey": self._font_descriptor(),
            "NBNoteTakingSessionMinorVersionNumberKey": 0,
            "packagePath": a.string(note_folder(d.title)),
            "tags": a.string(""),
            "NBNoteTakingSessionIsHighlighterBehindTextKey": True,
            "sessionFormatVersion": FORMAT_VERSION,
            "NBNoteTakingSessionDocumentPaperLayoutModelKey": model,
            "NBNoteTakingSessionAllowHighlighterConversionKey": False,
            "NBPaperMajorVersionNumber": 3,
            "richText": rich,
            "isReadOnly": False,
            "creationDate": a.date(d.created),
            "name": a.string(d.title),
        }
        if d.private is not None:
            root[PRIVATE_KEY] = a.value(zlib.compress(_private_json(d.private), 6))
        return a.dumps(root, "NoteTakingSession")

    # --- the paper and the pages ---------------------------------------------------------

    def _paper_attributes(self, paper: Paper, width: float) -> plistlib.UID:
        a = self.arch
        sizing = f"lockedWidth:{float(width)!r}:iPad" if paper.sizing == "lockedWidth" else paper.sizing
        lines = f"Lines:false:false:{paper.lines!r}" if paper.lines else "No Lines"
        return a.obj("GLModel.PaperAttributes", {
            "paperIdentifier": a.string(paper.identifier),
            "drawRotatedForOrientation": True,
            "paperOrientation": a.string("landscape" if paper.landscape else "portrait"),
            "paperSize": a.string(paper.size),
            "paperSizingBehavior": a.string(sizing),
            "alignsTextToLines": True,
            "lineStyle2": a.string(lines)})

    def _layout(self) -> plistlib.UID:
        """The page list, as 15.3 and later write it: a dictionary per page,
        a PDF page by its file and number, a blank page by INT64_MAX."""
        a = self.arch
        entries = []
        for n, (name, page) in enumerate(self.draft.layout, 1):
            pairs = [("kPageLayoutPDFPageNumberKey", page if name else INT64_MAX)]
            if name:
                pairs.append(("kPageLayoutPDFFileNameKey", name))
            pairs += [("kPageLayoutDocumentPageNumberKey", n), ("kPageLayoutPDFIsOriginalPageKey", bool(name)),
                      ("kPageLayoutPageIsBookmarkedKey", False)]
            entries.append(a.dictionary(pairs))
        return a.array(entries)

    def _font_descriptor(self) -> plistlib.UID:
        a = self.arch
        return a.obj("GLFontDescriptor", {
            "GLFontDescriptorExpanded": False, "GLFontDescriptorShadowOffset": a.string("{0, 0}"),
            "GLFontDescriptorLineSpacing": 1.0, "GLFontDescriptorItalic": False, "GLFontDescriptorSubscript": False,
            "GLFontDescriptorSize": 12.0, "GLFontDescriptorSuperscript": False, "GLFontDescriptorBold": False,
            "GLFontDescriptorIndentDecorationStyle": 0, "GLFontDescriptorShadowColor": plistlib.UID(0),
            "GLFontDescriptorName": a.string(FONT), "GLFontDescriptorFamily": a.string("Helvetica Neue"),
            "GLFontDescriptorShadowOpacity": 0.0, "GLFontDescriptorColor": a.color(BLACK),
            "GLFontDescriptorIndentLevel": 0, "GLFontDescriptorStrikethrough": False,
            "GLFontDescriptorCondensed": False, "GLFontDescriptorUnderlined": False,
            "GLFontDescriptorShadowRadius": 0.0})

    # --- a FormattedString --------------------------------------------------------------

    def _rich(self, runs: list, *, main: bool) -> plistlib.UID:
        """A FormattedString: the note's own (``main``: its ink, PDFs, media,
        page list and locked width) or a text box's store (empty but for
        its text, reflowable)."""
        a, d = self.arch, self.draft
        text = "".join(r.text for r in runs)
        backing = a.obj("NBAttributedString", {
            "NBAttributedBackingStringCodingKey": self._attributed(runs, mutable=True),
            "NBAttributedLayoutStringCodingKey": self._attributed(runs, mutable=True)})
        stamps = a.dictionary([("stringKey", a.mutable_string(text)), ("subRangesKey", a.array(
            [a.dictionary([("subRangeOtherAttributesKey", a.dictionary([])),
                           ("subRangeRangeKey", a.string("{0, %d}" % _units(text)))])] if text else []))])
        if main:
            reflow = a.obj("NBReflowStateLocked", {"pageWidthInDocumentCoordsKey": float(d.width),
                                                   "nativeLayoutDeviceStringKey": a.string("iPad")},
                           ("NBReflowState",))
            ink = self._hash(d.strokes, d.shapes)
            pdfs = [a.obj("PDFFile", {"pdfFileName": a.string(name), "contentBoxVersion": 1,
                                      "highlights": a.array([]), "version": 2, "type": 0})
                    for name in d.pdfs]
            media = [self._media(m) for m in d.media]
        else:
            reflow = a.obj("NBReflowStateReflowable", {}, ("NBReflowState",))
            ink, pdfs, media = self._hash([], []), [], []
        return a.obj("FormattedString", {
            "formatVersion": 5,
            "formattedStringTextAlignmentKey": 0,
            "attributedString": self._attributed(runs, mutable=False),
            "NBAttributedBackingString": backing,
            "Handwriting Overlay": a.obj("HandwritingObject", {"SpatialHash": ink}),
            "reflowState": reflow,
            "didBecomeReflowable": main,
            "pdfFiles": a.array(pdfs, mutable=False),
            "mediaObjects": a.array(media, mutable=False),
            "recordingTimestampString": stamps,
            "pageLayoutArray": self._layout() if main else a.array([])})

    def _attributed(self, runs: list, *, mutable: bool) -> plistlib.UID:
        """An attributed string: its text and a dictionary per run, the
        ranges in UTF-16 units."""
        a = self.arch
        text = "".join(r.text for r in runs)
        subs, at = [], 0
        for run in runs:
            n = _units(run.text)
            if not n:
                continue
            other = [("NSStrikethrough", int(run.strike)), ("line-spacing", 1.0), ("NSUnderline", int(run.underline)),
                     ("NSBaselineOffset", 0.0)]
            if run.list_style:
                other += [("indent-range-id", run.list_id), ("indent-level", run.level),
                          ("indent-decoration-style", run.list_style), ("indent-decoration-number", run.number),
                          ("checklist-checked", int(run.checked))]
            if run.mark is not None:
                other.append(("highlight", a.color(run.mark)))
            subs.append(a.dictionary([
                ("subRangeColorCrossPlatformKey", a.string(cross_platform(run.rgba))),
                ("subRangeRangeKey", a.string("{%d, %d}" % (at, n))),
                ("subRangeFontKey", a.dictionary([("NSFontSizeAttribute", float(run.size)),
                                                  ("NSFontNameAttribute", run.font)])),
                ("subRangeOtherAttributesKey", a.dictionary(other)),
                ("subRangeColorKey", a.color(run.rgba))]))
            at += n
        return a.dictionary([("stringKey", a.mutable_string(text) if mutable else a.string(text)),
                             ("subRangesKey", a.array(subs))])

    # --- ink -------------------------------------------------------------------------------

    def _hash(self, strokes: list, shapes: list) -> plistlib.UID:
        """An InkedSpatialHash: the strokes' parallel arrays in their order
        of ``z`` (they take the places the shapes leave), a long stroke in
        pieces of one UUID; the per-stroke arrays (curvesstyles, options,
        the keys of dashStyles) count strokes; the shapes in a shape list
        at their places."""
        a = self.arch
        counts, points, widths, colors, uuids = [], [], [], bytearray(), bytearray()
        fractional, forces, altitudes, azimuths = [], [], [], []
        styles, dashes = bytearray(), {}
        for n, stroke in enumerate(sorted(strokes, key=lambda s: s.z)):
            styles.append(stroke.style)
            if stroke.dash:
                dashes[str(n)] = {"pattern": stroke.dash}
            ident = stroke.uuid or new_uuid()
            for lo, hi in _pieces(len(stroke.anchors)):
                anchors = stroke.anchors[lo:hi]
                counts.append(3 * len(anchors) - 2)
                points += _chain(anchors)
                fractional += stroke.widths[lo:hi] if stroke.widths is not None else [1.0] * len(anchors)
                forces += stroke.forces[lo:hi] if stroke.forces is not None else [1.0] * len(anchors)
                altitudes += stroke.altitudes[lo:hi] if stroke.altitudes is not None else [_HALF_PI] * len(anchors)
                for v in stroke.azimuths[lo:hi] if stroke.azimuths is not None else [(1.0, 0.0)] * len(anchors):
                    azimuths += v
                widths.append(stroke.width)
                colors += bytes(stroke.rgba)
                uuids += ident
        n_points = sum(counts)
        shape_list = sorted(shapes, key=lambda s: s.z)
        return a.obj("InkedSpatialHash", {
            "numpoints": a.value(n_points),
            "options": bytes(8 * len(styles)),
            "numfractionalwidths": a.value(len(fractional)),
            "groupsArrays": a.array([]),
            "curvesaltitudeangles": _f32(altitudes),
            "curvespoints": _f32(points),
            "curveUUIDs": bytes(uuids),
            "curvescolors": bytes(colors),
            "shapes": a.add(plistlib.dumps({"shapes": [_shape_dict(s) for s in shape_list],
                                            "kinds": [s.kind for s in shape_list],
                                            "indices": [s.z for s in shape_list]}, fmt=plistlib.FMT_BINARY)),
            "curvesfractionalwidths": _f32(fractional),
            "curvesnumpoints": struct.pack(f"<{len(counts)}i", *counts),
            "curvesforces": _f32(forces),
            "dashStyles": a.add(plistlib.dumps({"objectPatterns": dashes}, fmt=plistlib.FMT_BINARY)),
            "curvesazimuthunitvector": _f32(azimuths),
            "curveswidth": _f32(widths),
            "curvesstyles": bytes(styles),
            "bezierPathsDataDictionary": a.dictionary([]),
            "numcurves": a.value(len(counts)),
        }, chain=None)

    # --- media ----------------------------------------------------------------------------

    def _common(self, item, *, canvas: bool) -> dict:
        """The keys every media object has (GenericMediaObject's), for a
        CanvasMediaObject (a text box, a sticky) or an ImageMediaObject."""
        a = self.arch
        fields = {
            "unscaledContentSize": a.string(point(item.w, item.h)),
            "locked": False,
            "captionFontColorCrossPlatform": a.string(cross_platform(BLACK)),
            "documentOrigin": a.string(point(item.x, item.y)),
            "rotationDegrees": float(getattr(item, "rotation", 0.0)),
            "captionIsUnderlined": False,
            "documentContentOrigin": a.string(point(item.x, item.y)),
            "captionFieldText": plistlib.UID(0),
            "minDimension": 8.0,
            "textWrapMode": 1,
            "cornerMode": 1,
            "UUID": a.uuid(item.uuid or new_uuid()),
            "captionFontName": a.string(FONT),
            "assetsIdKey": a.mutable_string(str(uuidlib.uuid4()).upper()),
            "handwritingZIndecesKey": a.array([]),
            "captionFontSize": 12.0,
            "zIndex": int(item.z),
            "captionFontColor": a.color(BLACK),
            "maxDimension": 1024.0,
        }
        if not canvas:
            fields.update(isFlippedHorizontal=False, isCaptionEnabled=False, isFlippedVertical=False, indexable=False)
        return fields

    def _media(self, item) -> plistlib.UID:
        a = self.arch
        if isinstance(item, Picture):
            fields = self._common(item, canvas=False)
            snapshot = a.obj("GLSnapshot", {"saveAsJPEG": item.jpeg, "imageIsMissing": False,
                                            "relativePath": a.string(item.image)})
            image = a.obj("ImageObject", {
                "kImageObjectSnapshotKey": snapshot,
                "fillColorCrossPlatform": a.string(cross_platform((0.0, 0.0, 0.0, 0.0))),
                "fillColor": a.gray(0.0, 0.0), "strokeAlpha": 1.0, "strokeWidth": 1.0,
                "strokeColor": a.gray(0.0, 1.0), "fillAlpha": 1.0,
                "strokeColorCrossPlatform": a.string(cross_platform(BLACK)),
                "rect": a.string(rect(0, 0, 0, 0))}, ("DrawObject",))
            fields["figure"] = a.obj("Figure", {
                "kFigurePrimitiveObjectsArrayKey": a.array([]), "FigureObjectTypeKey": 1,
                "FigureCanvasSizeKey": a.string(point(0, 0)),
                "FigureCropRectKey": a.string(rect(0, 0, *item.pixels)),
                "$0": plistlib.UID(0), "FigureBackgroundObjectKey": image})
            if item.gif:
                fields["isGif"] = True
                fields["assetsIdKey"] = a.mutable_string(item.gif)
            return a.obj("ImageMediaObject", fields, ("GenericMediaObject",))
        fields = self._common(item, canvas=True)
        paper = item.rgba if isinstance(item, Sticky) else item.background
        fields.update(paperIndex=-1, lineStyle=0, paperStyleObject=a.obj("GLModel.NBPaperStyle", {
            "paperColor": a.color(paper) if paper is not None else plistlib.UID(0),
            "lineStyle": 0, "paperImageIndex": -1}))
        if paper is not None:
            fields["kCanvasMediaObjectPaperAttributes"] = a.obj("GLModel.PaperAttributes", {
                "paperIdentifier": a.string("Color:#%02X%02X%02X" % tuple(round(v * 255) for v in paper[:3])),
                "drawRotatedForOrientation": True, "paperOrientation": a.string("portrait"),
                "paperSize": a.string("letter"), "paperSizingBehavior": a.string("deviceBasedWidth"),
                "alignsTextToLines": True, "lineStyle2": a.string("No Lines")})
        if isinstance(item, Sticky):
            return a.obj("CanvasMediaObject", fields, ("GenericMediaObject",))
        fields["textStore"] = self._rich(item.runs, main=False)
        return a.obj("TextBlockMediaObject", fields, ("CanvasMediaObject", "GenericMediaObject"))


def _units(text: str) -> int:
    """The text's length in UTF-16 units, which an attributed string's
    ranges count."""
    return len(text) + sum(1 for c in text if ord(c) > 0xFFFF)


def _pieces(n: int) -> list[tuple[int, int]]:
    """A stroke of ``n`` anchors cut into curves of at most MAX_ANCHORS,
    each starting at the anchor the last ended on: ``[(lo, hi)]``."""
    if n <= MAX_ANCHORS:
        return [(0, n)]
    out, lo = [], 0
    while lo < n - 1:
        hi = min(n, lo + MAX_ANCHORS)
        out.append((lo, hi))
        lo = hi - 1
    return out


def _chain(anchors: list) -> list[float]:
    """Anchors as a C1 Bézier chain of straight segments: anchor, then per
    segment the points at a third and two thirds of the chord and the next
    anchor; x, y flat."""
    out = list(anchors[0])
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        dx, dy = x1 - x0, y1 - y0
        out += (x0 + dx / 3, y0 + dy / 3, x0 + 2 * dx / 3, y0 + 2 * dy / 3, x1, y1)
    return out


def _shape_dict(shape: Shape) -> dict:
    """One shape of the shape list as 16.1.5 writes it: its geometry, the
    box it takes (``rect``, its outline's bounds grown by half the width),
    its ``appearance``, ``options`` and ``uuid``."""
    pts = [[float(x), float(y)] for x, y in shape.points]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    half = shape.width / 2
    look = {"style": shape.style, "strokeColor": {"rgba": [float(v) for v in shape.rgba]},
            "strokeWidth": float(shape.width)}
    if shape.fill is not None:
        look["fillColor"] = {"rgba": [float(v) for v in shape.fill]}
    if shape.dash or shape.arrow:
        look["dashStyle"] = {"pattern": shape.dash, **({"arrowHead": 1} if shape.arrow else {})}
    box = [[min(xs) - half, min(ys) - half], [max(xs) - min(xs) + shape.width, max(ys) - min(ys) + shape.width]]
    out = {"rect": box, "appearance": look, "options": 0, "uuid": shape.uuid or new_uuid()}
    if shape.kind == "line":
        out.update(startPt=pts[0], endPt=pts[1])
    elif shape.kind in ("circle", "ellipse"):
        out["rotatedRect"] = {"corners": pts}
    else:
        out.update(points=pts, isClosed=True)
    return out


def _private_json(private: dict) -> bytes:
    import json
    return json.dumps(private, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


# --- the other files ---------------------------------------------------------------------------

def metadata_plist(draft: Draft) -> bytes:
    """metadata.plist, a SessionInfo archived by NSKeyedArchiver."""
    a = _Archive("NSKeyedArchiver", "root")
    modified = draft.modified or draft.created
    changed = a.obj("NSMutableDictionary", {"NS.keys": [a.value(k) for k in (4, 2, 16, 1, 8)],
                                            "NS.objects": [a.date(modified) for _ in range(5)]}, ("NSDictionary",))
    nothing = a.array([], mutable=False)
    return a.dumps({
        "uuidKey": a.string(str(uuidlib.uuid4()).upper()),
        "associatedProductsKey": nothing,
        "notePackagePath": a.string(note_folder(draft.title)),
        "noteSubject": a.string("unsortedNotesKey"),
        "noteName": a.string(draft.title),
        "noteTags": a.string(""),
        "noteModifiedDateKey": a.date(modified),
        "noteCreationDateKey": a.date(draft.created),
        "noteLastChangeDatePerTypeKey": changed,
        "galleryPublishHistoryKey": nothing,
        "noteHasRecordingKey": bool(draft.recordings),
    }, "SessionInfo")


def library_plist(recordings: list) -> bytes:
    """Recordings/library.plist (XML): each recording by its identifier."""
    entries = {str(r.identifier): {
        "createddate": r.created.astimezone(datetime.UTC).replace(tzinfo=None),
        "duration": float(r.duration), "filepath": r.filepath, "filesize": int(r.size), "filetype": "m4a",
        "identifier": int(r.identifier), "name": r.name, "userFacingName": r.name} for r in recordings}
    return plistlib.dumps({"application version": "6852", "library-format-version": "1.0", "recordings": entries},
                          fmt=plistlib.FMT_XML)


def blank_png(width: int, height: int, rgb=(255, 255, 255)) -> bytes:
    """A PNG of one colour: a blank page for the thumbnail."""
    row = b"\x00" + bytes(rgb) * width
    raw = zlib.compress(row * height, 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


# --- the zip ------------------------------------------------------------------------------------

def session_plist(draft: Draft) -> bytes:
    return _Session(draft).dumps()


def write_note(draft: Draft, out) -> None:
    """The ``.note`` of ``draft`` into ``out`` (a path or a binary file): a
    stored zip with Unix attributes and a directory entry for every
    folder, as the app writes one (zipfile marks a name UTF-8 when it is
    not ASCII). ``draft.files`` are copied in as they are."""
    folder = note_folder(draft.title)
    members = {"Session.plist": session_plist(draft), "metadata.plist": metadata_plist(draft),
               "thumb12x.png": draft.thumbnail or blank_png(*THUMB_SIZE, _paper_rgb(draft.paper)),
               "Recordings/library.plist": library_plist(draft.recordings), **draft.files}
    dirs = {"", "Images", "PDFs", "Recordings", "HandwritingIndex", "Assets"}
    for path in members:
        parts = path.split("/")[:-1]
        dirs.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    stamp = datetime.datetime.now().timetuple()[:6]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        for d in sorted(dirs):
            info = zipfile.ZipInfo(posixpath.join(folder, d) + "/" if d else folder + "/", stamp)
            info.create_system, info.external_attr = 3, (0o40755 << 16) | 0x10
            zf.writestr(info, b"")
        for path, data in members.items():
            info = zipfile.ZipInfo(f"{folder}/{path}", stamp)
            info.create_system, info.external_attr = 3, 0o100644 << 16
            if isinstance(data, Path):
                with data.open("rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
            else:
                zf.writestr(info, data)


def _paper_rgb(paper: Paper) -> tuple[int, int, int]:
    ident = paper.identifier
    if ident.startswith("Color:#") and len(ident) == 13:
        try:
            return tuple(int(ident[7 + 2 * i:9 + 2 * i], 16) for i in range(3))
        except ValueError:
            pass
    return 255, 255, 255


def note_bytes(draft: Draft) -> bytes:
    out = io.BytesIO()
    write_note(draft, out)
    return out.getvalue()
