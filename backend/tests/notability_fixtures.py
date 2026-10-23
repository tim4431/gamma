"""A test-only writer of Notability ``.note`` files, built from the format's
public description (docs/research/notability-format.md) with ``plistlib``:
keyed archives (``$objects`` and UIDs), the ink hash's little-endian
arrays, plain plists inside them, metadata, recordings, and the zip around
them as the app writes it. It imports no pytest, since the browser suite
builds its note with it too (``sample_note``), and it checks its own
invariants: every curve has 1 + 3k points unless it is a polyline, F is
the sum of the curves' anchors, and 3F − 2N = P for a hash of Bézier
curves only."""

import io
import plistlib
import random
import struct
import zipfile

APPLE_EPOCH = 978307200
INT64_MAX = 9223372036854775807


class Archiver:
    """An NSKeyedArchiver ``$objects`` graph, written by plistlib
    (FMT_BINARY, plistlib.UID). ``indexed`` writes containers in the
    ``NS.object.N`` / ``NS.key.N`` form (7.2.5, 8.4.8, 10.2.4) instead of
    lists, their keys in shuffled order, as the app writes them."""

    def __init__(self, archiver: str = "GLKeyedArchiver", top_key: str = "$0", indexed: bool = False):
        self.objects = ["$null"]
        self.archiver, self.top_key, self.indexed = archiver, top_key, indexed
        self._classes = {}
        self._shuffle = random.Random(7)

    def add(self, value) -> plistlib.UID:
        self.objects.append(value)
        return plistlib.UID(len(self.objects) - 1)

    def _class(self, classname: str, classes: tuple) -> plistlib.UID:
        if classname not in self._classes:
            self._classes[classname] = self.add({"$classname": classname,
                                                 "$classes": [classname, *classes, "NSObject"]})
        return self._classes[classname]

    def obj(self, classname: str, fields: dict, classes: tuple = ()) -> plistlib.UID:
        return self.add({**fields, "$class": self._class(classname, classes)})

    def _keyed(self, pairs: list) -> dict:
        pairs = list(pairs)
        self._shuffle.shuffle(pairs)
        return dict(pairs)

    def array(self, items: list) -> plistlib.UID:
        items = list(items)
        if self.indexed:
            return self.add({**self._keyed([(f"NS.object.{i}", v) for i, v in enumerate(items)]),
                             "$class": self._class("NSMutableArray", ("NSArray",))})
        return self.obj("NSMutableArray", {"NS.objects": items}, ("NSArray",))

    def dictionary(self, mapping: dict) -> plistlib.UID:
        keys = [self.add(k) for k in mapping]
        values = list(mapping.values())
        if self.indexed:
            pairs = [(f"NS.key.{i}", k) for i, k in enumerate(keys)] + [(f"NS.object.{i}", v)
                                                                       for i, v in enumerate(values)]
            return self.add({**self._keyed(pairs), "$class": self._class("NSMutableDictionary", ("NSDictionary",))})
        return self.obj("NSMutableDictionary", {"NS.keys": keys, "NS.objects": values}, ("NSDictionary",))

    def string(self, text: str, mutable: bool = False) -> plistlib.UID:
        if mutable:
            return self.obj("NSMutableString", {"NS.bytes": text.encode("utf-8")}, ("NSString",))
        return self.add(text)

    def date(self, unix: float) -> plistlib.UID:
        return self.obj("NSDate", {"NS.time": unix - APPLE_EPOCH})

    def color(self, r: float, g: float, b: float, a: float = 1.0) -> plistlib.UID:
        return self.obj("UIColor", {"UIColorComponentCount": 4, "UIRed": r, "UIGreen": g, "UIBlue": b, "UIAlpha": a,
                                    "NSColorSpace": 2, "NSRGB": f"{r} {g} {b}".encode("ascii")})

    def dumps(self, root: plistlib.UID) -> bytes:
        return plistlib.dumps({"$version": 100000, "$archiver": self.archiver, "$top": {self.top_key: root},
                               "$objects": self.objects}, fmt=plistlib.FMT_BINARY, sort_keys=False)


# --- ink ---------------------------------------------------------------------------------

def curve(points, *, fractional=None, width=1.5, rgba=(237, 54, 36, 255), style=3, force=None,
          polyline=False) -> dict:
    """One curve as ``session`` writes it: its points (a Bézier chain of
    1 + 3k, or a polyline), one fractional width per anchor (1.0 each by
    default), its base width, RGBA bytes and style byte; ``force`` one raw
    UITouch force per anchor."""
    n = len(points)
    assert n >= 1
    assert polyline or (n - 1) % 3 == 0, "a Bézier chain has 1 + 3k points"
    anchors = n if polyline else (n - 1) // 3 + 1
    fractional = list(fractional) if fractional is not None else [1.0] * anchors
    assert len(fractional) == anchors
    if force is not None:
        assert len(force) == anchors
    return {"points": [tuple(p) for p in points], "fractional": fractional, "width": width, "rgba": tuple(rgba),
            "style": style, "force": force, "anchors": anchors, "polyline": polyline}


def _f32(values) -> bytes:
    values = list(values)
    return struct.pack(f"<{len(values)}f", *values)


def ink_hash(arch: Archiver, curves=(), *, shapes=None, dashes=None, groups=None, styles=True,
             channels=True, strokes=None) -> plistlib.UID:
    """An InkedSpatialHash: the counts as UIDs to ints and the arrays
    inline, as GLKeyedArchiver writes them. ``strokes`` numbers each
    curve's stroke, the curves of one stroke (the pieces the eraser cut it
    into) one after another: they share a curveUUID, and ``curvesstyles``
    has a byte per stroke, its first curve's; without it there are no
    UUIDs and a byte per curve. ``styles=False`` leaves ``curvesstyles``
    out; a list of style bytes writes those. ``channels`` writes force,
    altitude and azimuth (the defaults where a curve has none). ``shapes``
    is ``[(kind, index, shape dict)]``, ``dashes`` ``{stroke number: 1 |
    2}``, ``groups`` ``[{transform, index, curves, strokes?, shapes?:
    [(kind, shape dict)], groups?}]``."""
    curves = list(curves)
    n_points = sum(len(c["points"]) for c in curves)
    n_anchors = sum(c["anchors"] for c in curves)
    if all(not c["polyline"] for c in curves):
        assert 3 * n_anchors - 2 * len(curves) == n_points
    fields = {
        "numcurves": arch.add(len(curves)),
        "numpoints": arch.add(n_points),
        "numfractionalwidths": arch.add(n_anchors),
        "curvesnumpoints": struct.pack(f"<{len(curves)}i", *(len(c["points"]) for c in curves)),
        "curvespoints": _f32(v for c in curves for p in c["points"] for v in p),
        "curvesfractionalwidths": _f32(w for c in curves for w in c["fractional"]),
        "curveswidth": _f32(c["width"] for c in curves),
        "curvescolors": bytes(v for c in curves for v in c["rgba"]),
        "eventTokens": b"\xff\xff\xff\xff" * len(curves),
    }
    if strokes is not None:
        strokes = list(strokes)
        assert len(strokes) == len(curves) and strokes == sorted(strokes)
        fields["curveUUIDs"] = b"".join((1 + s).to_bytes(16, "big") for s in strokes)
    if styles is True:
        fields["curvesstyles"] = bytes(c["style"] for i, c in enumerate(curves)
                                       if strokes is None or not i or strokes[i] != strokes[i - 1])
    elif styles:
        fields["curvesstyles"] = bytes(styles)
    if channels:
        fields["curvesforces"] = _f32(f for c in curves for f in (c["force"] or [1.0] * c["anchors"]))
        fields["curvesaltitudeangles"] = _f32([1.5707963705062866] * n_anchors)
        fields["curvesazimuthunitvector"] = _f32([1.0, 0.0] * n_anchors)
    if shapes:
        fields["shapes"] = plistlib.dumps({"shapes": [s for _k, _i, s in shapes], "kinds": [k for k, _i, _s in shapes],
                                           "indices": [i for _k, i, _s in shapes]}, fmt=plistlib.FMT_BINARY)
    if dashes:
        fields["dashStyles"] = plistlib.dumps({"objectPatterns": {str(i): {"pattern": p} for i, p in dashes.items()}},
                                              fmt=plistlib.FMT_BINARY)
    fields["groupsArrays"] = arch.array([group_bytes(g) for g in groups or ()])
    fields["bezierPathsDataDictionary"] = arch.dictionary({})
    return arch.obj("InkedSpatialHash", fields)


def group_bytes(group: dict) -> bytes:
    """One ``groupsArrays`` element: a plain plist ``{inkGroup:
    {inkGroupObjects, transform}, index}``, its curves a complete
    NSKeyedArchiver archive of another hash (type 1), each shape a shape
    list of its own, ``{shapes, kinds, indices}`` (type 2)."""
    items = []
    if group.get("curves") or group.get("groups"):
        inner = Archiver("NSKeyedArchiver", "root")
        root = ink_hash(inner, group.get("curves", ()), groups=group.get("groups"), strokes=group.get("strokes"))
        items.append({"type": 1, "object": inner.dumps(root)})
    items += [{"type": 2, "object": {"shapes": [shape], "kinds": [kind], "indices": [0]}}
              for kind, shape in group.get("shapes", ())]
    return plistlib.dumps({"inkGroup": {"inkGroupObjects": items, "transform": list(group["transform"]),
                                        "isCustomSticker": False, "options": 0,
                                        "appearance": {"style": 3, "strokeWidth": 1.0,
                                                       "strokeColor": {"rgba": [0.0, 0.0, 0.0, 1.0]}}},
                           "index": group.get("index", 0)}, fmt=plistlib.FMT_BINARY)


def shape(**fields) -> dict:
    """A shape dict: ``appearance`` (style, strokeColor, strokeWidth,
    fillColor, dashStyle) and the kind's geometry."""
    look = {"style": fields.pop("style", 3), "strokeColor": {"rgba": list(fields.pop("rgba", (0.0, 0.0, 0.0, 1.0)))},
            "strokeWidth": fields.pop("width", 2.0)}
    if "fill" in fields:
        look["fillColor"] = {"rgba": list(fields.pop("fill"))}
    if "pattern" in fields or "arrow" in fields:
        look["dashStyle"] = {"pattern": fields.pop("pattern", 0), "arrowHead": fields.pop("arrow", 0)}
    return {"appearance": look, **{k: [list(p) for p in v] if k == "points" else list(v) if isinstance(v, tuple)
                                   else v for k, v in fields.items()}}


def path(*elements) -> bytes:
    """A CGPath as Notability archives one, as a partly erased shape's
    ``outlinePath`` (what is left of its centre line) and ``strokePath``
    (the outline it fills) hold it: the magic, the element count (uint32),
    a byte per element, then the points as float64 pairs. ``elements`` are
    ``(type, points)``: 0 move, 1 line, 2 quadratic, 3 cubic, 4 close."""
    kinds = bytes(kind for kind, _points in elements)
    values = [v for _kind, points in elements for point in points for v in point]
    return b"\x25\xb3\xe5\x48" + struct.pack("<I", len(kinds)) + kinds + struct.pack(f"<{len(values)}d", *values)


# --- text ----------------------------------------------------------------------------------

def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def text_store(arch: Archiver, runs, align: int = 0) -> plistlib.UID:
    """A FormattedString holding an attributed string: ``runs`` are
    ``{text, font?, size?, color?, other?}`` (``other`` the
    subRangeOtherAttributesKey dict), their ranges in UTF-16 units."""
    return arch.obj("FormattedString", {"attributedString": attributed(arch, runs),
                                        "reflowState": arch.obj("NBReflowStateReflowable", {}),
                                        "formattedStringTextAlignmentKey": align, "formatVersion": 5})


def attributed(arch: Archiver, runs) -> plistlib.UID:
    subranges, at = [], 0
    for run in runs:
        length = _utf16(run["text"])
        fields = {"subRangeRangeKey": arch.string(f"{{{at}, {length}}}"),
                  "subRangeFontKey": arch.dictionary({"NSFontNameAttribute": arch.string(run.get("font", "HelveticaNeue")),
                                                      "NSFontSizeAttribute": run.get("size", 12)})}
        if run.get("color"):
            fields["subRangeColorCrossPlatformKey"] = arch.string(",".join(f"{v:.15f}" for v in run["color"]))
        other = dict(run.get("other") or {})
        if "highlight" in other:
            other["highlight"] = arch.color(*other["highlight"])
        fields["subRangeOtherAttributesKey"] = arch.dictionary(other)
        subranges.append(arch.dictionary(fields))
        at += length
    return arch.dictionary({"stringKey": arch.string("".join(r["text"] for r in runs), mutable=True),
                            "subRangesKey": arch.array(subranges)})


# --- the session --------------------------------------------------------------------------

def _geometry(arch: Archiver, item: dict) -> dict:
    x, y = item["origin"]
    w, h = item["size"]
    return {"documentOrigin": arch.string(f"{{{x}, {y}}}"), "documentContentOrigin": arch.string(f"{{{x}, {y}}}"),
            "unscaledContentSize": arch.string(f"{{{w}, {h}}}"), "rotationDegrees": item.get("rotation", 0.0),
            "zIndex": item.get("z", 0), "cornerMode": 0, "handwritingZIndecesKey": arch.array([])}


def _paper_style(arch: Archiver, color) -> plistlib.UID:
    return arch.obj("GLModel.NBPaperStyle", {"paperColor": arch.color(*color) if color else plistlib.UID(0),
                                             "lineStyle": 0, "paperImageIndex": -1})


def media_object(arch: Archiver, item: dict) -> plistlib.UID:
    """One ``mediaObjects`` entry: ``{kind: text|math|picture|sticky|<a
    class name>, origin, size, rotation?, z?, …}``; a picture's ``gif`` is
    the asset id of an animated GIF's animation."""
    kind = item["kind"]
    fields = _geometry(arch, item)
    if kind == "text":
        fields["textStore"] = text_store(arch, item.get("runs", ()))
        fields["paperStyleObject"] = _paper_style(arch, item.get("background"))
        return arch.obj("TextBlockMediaObject", fields, ("CanvasMediaObject", "GenericMediaObject"))
    if kind == "math":
        fields.update(latex=arch.string(item["latex"]), color=arch.color(*item.get("color", (0, 0, 0, 1))))
        return arch.obj("Notability.MathMediaObject", fields, ("GenericMediaObject",))
    if kind == "sticky":
        fields["paperStyleObject"] = _paper_style(arch, item.get("color"))
        return arch.obj("CanvasMediaObject", fields, ("GenericMediaObject",))
    if kind == "picture":
        snapshot = arch.obj("GLSnapshot", {"relativePath": arch.string(item["path"]), "saveAsJPEG": False,
                                           "imageIsMissing": item.get("missing", False)})
        crop = item.get("crop")
        figure = arch.obj("Figure", {
            "FigureObjectTypeKey": 1,
            "FigureCropRectKey": arch.string("{{%s, %s}, {%s, %s}}" % tuple(crop)) if crop else plistlib.UID(0),
            "FigureBackgroundObjectKey": arch.obj("ImageObject", {"kImageObjectSnapshotKey": snapshot}, ("DrawObject",))})
        fields.update(figure=figure, isFlippedHorizontal=item.get("flip_h", False),
                      isFlippedVertical=item.get("flip_v", False), isCaptionEnabled=bool(item.get("caption")),
                      captionFieldText=arch.string(item["caption"]) if item.get("caption") else plistlib.UID(0))
        if item.get("gif"):  # an animated GIF: its animation is Assets/<gif>/media.gif
            fields.update(isGif=True, assetsIdKey=arch.string(item["gif"], mutable=True))
        return arch.obj("ImageMediaObject", fields, ("GenericMediaObject",))
    return arch.obj(kind, fields, ("GenericMediaObject",))


def _pdf_file(arch: Archiver, name: str, version) -> plistlib.UID:
    fields = {"pdfFileName": arch.string(name), "contentBoxVersion": 1, "highlights": arch.array([])}
    if version is not None:
        fields["version"] = version
    return arch.obj("PDFFile", fields)


def session(*, width=565.0, layout=(), pdfs=None, curves=(), strokes=None, shapes=None, dashes=None, groups=None,
            media=(), flow=None, paper=None, version=9, indexed=False, archiver="GLKeyedArchiver", styles=True,
            channels=True, reflow="locked", file_keys=False, name="Note", subject="unsortedNotesKey",
            created=1_700_000_000.0) -> bytes:
    """A Session.plist. ``curves`` to ``groups`` are the ink (``ink_hash``);
    ``layout`` is a list of ``(pdf name, page)`` or None
    for a blank page; ``pdfs`` ``{name: PDFFile.version or None}``;
    ``paper`` the PaperAttributes as ``{identifier, orientation, size,
    sizing, lines}`` (with it, the layout is the paper model's, as 11.x and
    later write it; without it, richText's); ``reflow`` "locked",
    "reflowable" or None (2013–14 notes have none); ``file_keys`` names a
    layout entry's PDF by its PDFFile object (10.x and earlier)."""
    arch = Archiver(archiver, "$0" if archiver == "GLKeyedArchiver" else "root", indexed)
    pdf_objects = {n: _pdf_file(arch, n, v) for n, v in (pdfs or {}).items()}
    entries = []
    for i, entry in enumerate(layout):
        if entry is None:
            fields = {"kPageLayoutPDFPageNumberKey": INT64_MAX}
        else:
            file, page = entry
            fields = {"kPageLayoutPDFPageNumberKey": page, "kPageLayoutDocumentPageNumberKey": i + 1}
            if file_keys:
                fields["kPageLayoutPDFFileKey"] = pdf_objects.get(file) or _pdf_file(arch, file, 2)
            else:
                fields["kPageLayoutPDFFileNameKey"] = arch.string(file)
        entries.append(arch.dictionary(fields))
    rich = {
        "formatVersion": 5,
        "Handwriting Overlay": arch.obj("HandwritingObject", {"SpatialHash": ink_hash(
            arch, curves, shapes=shapes, dashes=dashes, groups=groups, styles=styles, channels=channels,
            strokes=strokes)}),
        "pdfFiles": arch.array(list(pdf_objects.values())),
        "pageLayoutArray": arch.array([] if paper is not None else entries),
        "mediaObjects": arch.array([media_object(arch, m) for m in media]),
        "attributedString": attributed(arch, flow or ()),
        "didBecomeReflowable": True,
    }
    if reflow == "locked":
        rich["reflowState"] = arch.obj("NBReflowStateLocked", {"pageWidthInDocumentCoordsKey": width,
                                                               "nativeLayoutDeviceStringKey": arch.string("iPad")})
    elif reflow == "reflowable":
        rich["reflowState"] = arch.obj("NBReflowStateReflowable", {})
    root = {"richText": arch.obj("FormattedString", rich), "name": arch.string(name),
            "packagePath": arch.string(name, mutable=True), "subject": arch.string(subject),
            "creationDate": arch.date(created), "isReadOnly": False}
    if version is not None:
        root["sessionFormatVersion"] = version
    if paper is not None:
        attrs = {"paperIdentifier": arch.string(paper.get("identifier", "Color:#FFFFFF")),
                 "paperOrientation": arch.string(paper.get("orientation", "portrait")),
                 "paperSize": arch.string(paper.get("size", "letter")),
                 "paperSizingBehavior": arch.string(paper.get("sizing", f"lockedWidth:{width}:iPad")),
                 "lineStyle2": arch.string(paper.get("lines", "No Lines")), "drawRotatedForOrientation": True}
        root["NBNoteTakingSessionDocumentPaperLayoutModelKey"] = arch.obj("NoteDocumentPaperLayoutModel", {
            "documentPaperAttributes": arch.obj("GLModel.PaperAttributes", attrs), "pageLayoutArray": arch.array(entries)})
    return arch.dumps(arch.obj("NoteTakingSession", root))


def metadata(name: str = "Note", subject: str = "unsortedNotesKey", created: float | None = None) -> bytes:
    arch = Archiver("NSKeyedArchiver", "root")
    fields = {"noteName": arch.string(name), "noteSubject": arch.string(subject), "noteTags": ""}
    if created is not None:
        fields["noteCreationDateKey"] = arch.date(created)
    return arch.dumps(arch.obj("SessionInfo", fields))


def library(recordings: list[dict]) -> bytes:
    """Recordings/library.plist (XML): ``recordings`` are ``{identifier,
    filepath, name?, userFacingName?, createddate?, duration?}``."""
    return plistlib.dumps({"application version": "6594", "library-format-version": "1.0",
                           "recordings": {str(r["identifier"]): {"name": "", "userFacingName": "", "filetype": "m4a",
                                                                 "filesize": 0, "duration": 0.0, **r}
                                          for r in recordings}}, fmt=plistlib.FMT_XML)


# --- files ---------------------------------------------------------------------------------

def pdf(sizes=((612, 792),)) -> bytes:
    """Blank PyPDF2 pages of these sizes."""
    from PyPDF2 import PdfWriter
    writer = PdfWriter()
    for w, h in sizes:
        writer.add_blank_page(w, h)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def png(w: int = 40, h: int = 30, rgba=(200, 30, 30, 255)) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGBA", (w, h), rgba).save(out, "PNG")
    return out.getvalue()


def jpeg(w: int = 40, h: int = 30) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 200)).save(out, "JPEG")
    return out.getvalue()


def gif(w: int = 48, h: int = 32, frames: int = 3) -> bytes:
    """An animated GIF, each frame a colour of its own."""
    from PIL import Image
    out = io.BytesIO()
    first, *rest = [Image.new("RGB", (w, h), (60 * i, 200 - 40 * i, 90)) for i in range(frames)]
    first.save(out, "GIF", save_all=True, append_images=rest, duration=100, loop=0)
    return out.getvalue()


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + kind + body


def m4a(duration_ms: int = 3500, *, version: int = 0, moov_last: bool = False) -> bytes:
    """An MP4 audio file's boxes: ``ftyp``, ``moov`` with an ``mvhd`` stating
    the length, and an empty ``mdat`` (after ``moov``, or before it)."""
    timescale = 44100
    duration = duration_ms * timescale // 1000
    if version == 0:
        mvhd = _box(b"mvhd", struct.pack(">B3xIIII", 0, 0, 0, timescale, duration) + bytes(80))
    else:
        mvhd = _box(b"mvhd", struct.pack(">B3xQQIQ", 1, 0, 0, timescale, duration) + bytes(80))
    ftyp = _box(b"ftyp", b"M4A \x00\x00\x00\x00M4A mp42isom")
    moov = _box(b"moov", mvhd)
    mdat = _box(b"mdat", bytes(64))
    return ftyp + (mdat + moov if moov_last else moov + mdat)


def note_zip(folder: str = "Note", *, session_bytes: bytes, meta: bytes | None = None, files: dict | None = None,
             compression: int = zipfile.ZIP_STORED) -> bytes:
    """A ``.note``: one folder holding Session.plist, metadata.plist and
    ``files`` (``{path below the folder: bytes}``), with the directory
    entries and the empty-named member Notability 15.3 and later write."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as zf:
        zf.writestr(f"{folder}/", b"")
        zf.writestr(zipfile.ZipInfo(""), b"")
        zf.writestr(f"{folder}/Session.plist", session_bytes)
        zf.writestr(f"{folder}/metadata.plist", meta if meta is not None else metadata(folder))
        for path, data in (files or {}).items():
            zf.writestr(f"{folder}/{path}", data)
    return out.getvalue()


def zip_of(files: dict, compression: int = zipfile.ZIP_STORED) -> bytes:
    """A plain zip of ``{path: bytes}``: an upload of several notes."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return out.getvalue()


def sample_note() -> bytes:
    """The browser suite's note: W 565, a 612 × 792 PDF page, then a paper
    page. Page 1 holds a pen curve whose width varies, a highlighter, a
    text box "Typed **box**" and a picture; page 2 a dot. Flow text, one
    3.5 s recording, and the metadata name "E2E Notability note"."""
    pdf_name = "0C6E6D2E-1111-4222-8333-944455556666.pdf"
    pen = curve([(40, 60), (80, 40), (120, 80), (160, 60), (200, 40), (240, 80), (280, 60)],
                fractional=[1.0, 1.6, 0.6], width=1.8, force=[1.5, 3.0, 0.8])
    highlight = curve([(40, 200), (160, 200), (280, 200), (400, 200)], width=12.0, rgba=(250, 157, 0, 0x6B), style=4)
    dot = curve([(300, 900)], width=3.0, rgba=(0, 111, 255, 255))
    media = [
        {"kind": "text", "origin": (60, 300), "size": (220, 40), "z": 1,
         "runs": [{"text": "Typed ", "size": 14}, {"text": "box", "font": "HelveticaNeue-Bold", "size": 14}]},
        {"kind": "picture", "origin": (300, 300), "size": (120, 90), "z": 2, "path": "Images/Image .png"},
    ]
    flow = [{"text": "Lecture notes\n", "size": 12},
            {"text": "First point", "other": {"indent-decoration-style": 1, "indent-level": 0}}]
    data = session(layout=[(pdf_name, 1), None], pdfs={pdf_name: 2}, curves=[pen, highlight, dot], media=media,
                   flow=flow, name="E2E Notability note",
                   paper={"identifier": "Color:#FFFFFF", "size": "letter", "lines": "No Lines"})
    files = {f"PDFs/{pdf_name}": pdf(((612, 792),)), "Images/Image .png": png(80, 60),
             "Recordings/library.plist": library([{"identifier": 1, "filepath": "Recording 1.m4a",
                                                   "name": "Recording 1"}]),
             "Recordings/Recording 1.m4a": m4a(3500)}
    return note_zip("E2E Notability note", session_bytes=data, meta=metadata("E2E Notability note"), files=files)


def ntb() -> bytes:
    """A Notability Cloud ``.ntb``: version, manifest.json and noteBundle."""
    return zip_of({"version": b"1", "manifest.json": b'{"appVersion": "16.5.3"}', "noteBundle": b"\x10\x00\x00\x00"})
