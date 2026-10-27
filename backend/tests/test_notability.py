"""The Notability reader and mapping, without the HTTP layer: the keyed
archive behind its guards, finding notes in an upload, the model a note
reads into, its pages' geometry, the strokes, shapes and stickies as
gamma-ink v2, the typed text, and the helpers the import shares
(docs/dev/import_export.md "Notability notes")."""

import contextlib
import io
import json
import math
import plistlib
import struct
import zipfile
from array import array
from collections import Counter
from pathlib import Path

import pytest

import notability_fixtures as fx
from gamma import ink, notability as nb, notability_map as nm
from gamma.import_zip import DirectoryTooLarge, directory_size, new_walk, open_zip, walk_zip
from gamma.notability_archive import Archive, ArchiveError, load_plist, parse_numbers
from gamma.recording import mp4_duration_ms

PDF = "A1B2C3D4-0000-4000-8000-000000000001.pdf"
WORKED = [(10, 20), (12, 20), (14, 22), (16, 22)]
SHARED = Path(__file__).resolve().parents[2] / "tests" / "shared"


# --- helpers ------------------------------------------------------------------------------

def walked(data: bytes, name: str = "upload.zip"):
    """The upload's walked entries and its walk (the zips stay open with the test)."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    stack = contextlib.ExitStack()
    walk = new_walk(stack)
    entries = []
    walk_zip(zf, "", entries, walk, nested=nb.nested_zip)
    return entries, walk


def note_of(session: bytes, files: dict | None = None, *, ink_too: bool = True, meta: bytes | None = None,
            path: str = "Note.note") -> nb.Note:
    entries, _ = walked(fx.note_zip(session_bytes=session, files=files, meta=meta))
    members = nb.note_members(entries)
    return nb.read_note(members, members.find("Session.plist").read(), path=path, digest="digest", ink=ink_too)


def frame(k=1.0, width=612.0, height=792.0, pdf=PDF, page=1) -> nm.Frame:
    return nm.Frame(0.0, 10_000.0, 0.0, 0.0, k, width, height, pdf, page)


def decoded(stroke: dict) -> list[dict]:
    return ink.decode_stroke(ink.Stroke.model_validate({**stroke, "id": "n0"}))


def xy(stroke: dict) -> list[tuple]:
    return [(s["x"], s["y"]) for s in decoded(stroke)]


# --- the fixture writer ------------------------------------------------------------------

def test_the_fixture_writes_the_specs_worked_curve():
    data = fx.session(curves=[fx.curve(WORKED, fractional=[1.0, 0.5], width=1.5)])
    objects = plistlib.loads(data)["$objects"]
    spatial = next(o for o in objects if isinstance(o, dict) and "curvespoints" in o)
    assert spatial["curvesnumpoints"] == bytes.fromhex("04000000")
    assert spatial["curvespoints"] == bytes.fromhex("00002041" "0000a041" "00004041" "0000a041"
                                                    "00006041" "0000b041" "00008041" "0000b041")
    assert spatial["curvesfractionalwidths"] == bytes.fromhex("0000803f" "0000003f")
    assert spatial["curveswidth"] == bytes.fromhex("0000c03f")
    assert spatial["curvescolors"] == bytes.fromhex("ed3624ff")
    assert spatial["curvesstyles"] == bytes.fromhex("03")


# --- the archive ---------------------------------------------------------------------------

def _rich_session(indexed: bool) -> bytes:
    return fx.session(
        indexed=indexed, layout=[(PDF, 1), None, (PDF, 2)], pdfs={PDF: 2},
        curves=[fx.curve(WORKED, fractional=[1.0, 0.5]), fx.curve([(5, 900)], style=4, rgba=(0, 0, 0, 107))],
        media=[{"kind": "text", "origin": (10, 20), "size": (100, 30), "runs": [{"text": "a"}, {"text": "b"}]},
               {"kind": "sticky", "origin": (30, 40), "size": (50, 50), "color": (0.2, 0.4, 0.6, 1)}],
        flow=[{"text": "one\n"}, {"text": "two", "other": {"indent-decoration-style": 1, "indent-level": 1}}])


def test_both_container_forms_read_alike():
    files = {f"PDFs/{PDF}": fx.pdf(((612, 792), (612, 792)))}
    listed, indexed = note_of(_rich_session(False), files), note_of(_rich_session(True), files)
    assert listed == indexed
    assert len(listed.layout) == 3 and listed.layout[1].pdf is None and len(listed.curves) == 2
    assert [m.kind for m in listed.media] == ["text", "sticky"] and len(listed.flow) == 2


def test_nskeyedarchiver_root_and_glkeyedarchiver_top_both_read():
    for archiver in ("NSKeyedArchiver", "GLKeyedArchiver"):
        note = note_of(fx.session(archiver=archiver, curves=[fx.curve(WORKED)]))
        assert len(note.curves) == 1


def _archive(objects, root=1, archiver="NSKeyedArchiver", top="root") -> Archive:
    return Archive(plistlib.dumps({"$archiver": archiver, "$top": {top: plistlib.UID(root)}, "$objects": objects},
                                  fmt=plistlib.FMT_BINARY), limit=1 << 20)


def test_strings_in_their_three_forms_and_null():
    arch = _archive(["$null", {"plain": "Café", "bytes": plistlib.UID(2), "ns": plistlib.UID(3),
                               "nothing": plistlib.UID(0)},
                     {"NS.bytes": "naïve".encode()}, {"NS.string": "x"}])
    root = arch.root
    assert arch.string(root["plain"]) == "Café"  # NFC
    assert arch.string(root["bytes"]) == "naïve" and arch.string(root["ns"]) == "x"
    assert arch.get(root, "nothing", "default") == "default" and arch.string(root["nothing"]) is None


def test_cycles_and_uid_loops_end():
    loop = _archive(["$null", {"self": plistlib.UID(2), "spin": plistlib.UID(3)},
                     {"NS.objects": [plistlib.UID(2), plistlib.UID(2)]}, plistlib.UID(4), plistlib.UID(3)])
    nested = loop.array(loop.root["self"])
    assert len(nested) == 2 and nested[0] is nested[1]  # its own element: read once, never walked
    assert loop.deref(loop.root["spin"]) is None and loop.string(loop.root["spin"]) is None


def test_plist_guards():
    good = plistlib.dumps({"a": 1}, fmt=plistlib.FMT_BINARY)
    assert load_plist(good, limit=100) == {"a": 1}
    with pytest.raises(ArchiveError):
        load_plist(good, limit=len(good) - 1)
    trailer = bytearray(good)
    trailer[-24:-16] = struct.pack(">Q", 2_000_000)  # the object count
    with pytest.raises(ArchiveError, match="2000000 objects"):
        load_plist(bytes(trailer), limit=1 << 20)
    longer = plistlib.dumps({"a": "x" * 200, "b": [1, 2, 3]}, fmt=plistlib.FMT_BINARY)
    with pytest.raises(ArchiveError):
        load_plist(longer[:20] + longer[-32:], limit=1 << 20)  # the offset table cut away
    with pytest.raises(ArchiveError):
        load_plist(longer[:-5], limit=1 << 20)
    entity = (b'<?xml version="1.0"?><!DOCTYPE plist [<!ENTITY e "x">]>'
              b'<plist version="1.0"><string>&e;</string></plist>')
    with pytest.raises(ArchiveError):
        load_plist(entity, limit=1 << 20)
    with pytest.raises(ArchiveError):
        load_plist(b"no plist at all", limit=1 << 20)


def test_what_is_no_notability_note():
    files = fx.note_zip(session_bytes=fx.session())
    entries, _ = walked(files)
    members = nb.note_members(entries)
    with pytest.raises(nb.NoteError):
        nb.read_note(members, b"<?xml version='1.0'?><plist/>", path="x")
    wrong = plistlib.dumps({"$archiver": "Other", "$top": {"root": plistlib.UID(1)}, "$objects": ["$null", {}]},
                           fmt=plistlib.FMT_BINARY)
    with pytest.raises(nb.NoteError):
        nb.read_note(members, wrong, path="x")
    arch = fx.Archiver()
    with pytest.raises(nb.NoteError, match="not a Notability note"):
        nb.read_note(members, arch.dumps(arch.obj("SomethingElse", {"richText": arch.obj("FormattedString", {})})),
                     path="x")
    arch = fx.Archiver()
    with pytest.raises(nb.NoteError):
        nb.read_note(members, arch.dumps(arch.obj("NoteTakingSession", {"name": "x"})), path="x")


def test_geometry_strings():
    assert parse_numbers("{{1, -2.5}, {3e2, .5}}", 4) == (1.0, -2.5, 300.0, 0.5)
    assert parse_numbers("{1, 2}", 4) is None and parse_numbers("{nan, 2}", 2) is None
    assert parse_numbers("{1e999, 2}", 2) is None and parse_numbers(None, 2) is None


# --- finding notes ------------------------------------------------------------------------

def test_the_upload_shapes():
    one = fx.note_zip("Older name", session_bytes=fx.session())
    entries, _ = walked(one)
    bundles = nb.find_bundles(entries, "Lecture.note")
    assert [b.path for b in bundles] == ["Lecture.note"] and bundles[0].members

    two = fx.zip_of({"Biology/Cells.note": one, "Physics/Waves.note": one})
    bundles = nb.find_bundles(walked(two)[0], "notes.zip")
    assert [(b.path, b.packed is not None) for b in bundles] == [("Biology/Cells.note", True),
                                                                  ("Physics/Waves.note", True)]

    unzipped = fx.zip_of({"Lecture.nbn/Session.plist": fx.session(), "Lecture.nbn/metadata.plist": fx.metadata(),
                          "Lecture.nbn/Images/Image .png": fx.png()})
    bundles = nb.find_bundles(walked(unzipped)[0], "folder.zip")
    assert [b.path for b in bundles] == ["Lecture.nbn"]
    assert set(bundles[0].members.entries) == {"Session.plist", "metadata.plist", "Images/Image .png"}

    inner = fx.zip_of({"Cells.note": one})
    bundles = nb.find_bundles(walked(fx.zip_of({"Export.zip": inner}))[0], "outer.zip")
    assert [b.path for b in bundles] == ["Cells.note"]

    top = fx.zip_of({"Session.plist": fx.session(), "metadata.plist": fx.metadata()})
    assert [b.path for b in nb.find_bundles(walked(top)[0], "Loose.zip")] == ["Loose.zip"]


def test_ntb_files_and_folders_are_notes():
    """An .ntb is a note as a .note is: one uploaded alone is the upload, one
    inside a zip is packed, and a folder holding a noteBundle (an unzipped
    one) is a note of its members."""
    (alone,) = nb.find_bundles(walked(fx.ntb())[0], "Cloud.ntb")
    assert alone.path == "Cloud.ntb" and set(alone.members.entries) == {"version", "manifest.json", "noteBundle"}
    upload = fx.zip_of({"Old.ntb": fx.ntb(), "Unzipped/noteBundle": b"x", "Unzipped/version": b"1"})
    bundles = nb.find_bundles(walked(upload)[0], "mixed.zip")
    assert [(b.path, b.packed is not None) for b in bundles] == [("Old.ntb", True), ("Unzipped", False)]
    assert set(bundles[1].members.entries) == {"noteBundle", "version"}


def test_the_search_cache_is_never_opened_and_the_empty_member_is_skipped():
    cache = fx.zip_of({"PDFIndex.plist": b"x"})
    note = fx.note_zip("N", session_bytes=fx.session(), files={"NBPDFIndex/PDFIndex.zip": cache})
    entries, walk = walked(note)
    assert walk["zips"] == 0 and "N/NBPDFIndex/PDFIndex.zip" in [e.path for e in entries]
    assert "" not in [e.path for e in entries]
    assert nb.nested_zip("a/Part-1.zip") and not nb.nested_zip("N/NBPDFIndex/PDFIndex.zip")


def test_folder_paths_drop_the_shared_top():
    from gamma.note_app_import import note_dirs
    shared = [nb.Bundle("Export/Biology/A.note", 1), nb.Bundle("Export/Physics/B.note", 1)]
    assert note_dirs(shared) == {"Export/Biology/A.note": ["Biology"], "Export/Physics/B.note": ["Physics"]}
    picked = [nb.Bundle("Notability notes/A.note", 1), nb.Bundle("Notability notes/B.note", 1)]
    assert note_dirs(picked) == {"Notability notes/A.note": [], "Notability notes/B.note": []}
    mixed = [nb.Bundle("A.note", 1), nb.Bundle("Sub/B.note", 1)]
    assert note_dirs(mixed) == {"A.note": [], "Sub/B.note": ["Sub"]}


# --- the reader ----------------------------------------------------------------------------

def test_page_width_and_its_fallbacks():
    assert note_of(fx.session(width=600.0)).width == 600.0
    locked = fx.session(reflow="reflowable", paper={"sizing": "lockedWidth:679.0:iPad"})
    assert note_of(locked).width == 679.0
    assert note_of(fx.session(reflow=None, version=None)).width == 768.0
    assert note_of(fx.session(width=float("nan"), paper={"sizing": "lockedWidth:abc:iPad"})).width == 565.0
    assert note_of(fx.session(width=5.0)).width == 565.0


def test_the_worked_curve():
    note = note_of(fx.session(curves=[fx.curve(WORKED, fractional=[1.0, 0.5], width=1.5)]))
    (c,) = note.curves
    assert (c.n, c.bezier, c.anchors, c.base, c.rgba, c.style) == (4, True, 2, 1.5, (237, 54, 36, 255), 3)
    assert isinstance(c.ink.points, array) and c.ink.points.typecode == "f" and c.p == 0 and c.f == 0
    stroke = nm.curve_stroke(c, frame(k=1.0))
    assert stroke["color"] == "#ed3624" and stroke["size"] == 1.5 and stroke["ch"] == "xyw"
    assert "tool" not in stroke and "pen" not in stroke and "id" not in stroke
    samples = decoded(stroke)
    widths = [ink.stroke_width(ink.Stroke.model_validate({**stroke, "id": "n0"}), s) for s in samples]
    assert widths[0] == pytest.approx(1.5) and widths[-1] == pytest.approx(0.75)
    assert (samples[0]["x"], samples[0]["y"], samples[-1]["x"], samples[-1]["y"]) == (10, 20, 16, 22)


def test_styles_dashes_and_what_is_never_drawn():
    curves = [fx.curve(WORKED), fx.curve(WORKED, rgba=(10, 10, 10, 107)), fx.curve(WORKED, style=6),
              fx.curve(WORKED, rgba=(1, 2, 3, 0)), fx.curve(WORKED, rgba=(4, 5, 6, 255))]
    note = note_of(fx.session(curves=curves, styles=[3], dashes={1: 2, 4: 1}))
    # a short curvesstyles: the rest by alpha; style 6 is a pen here (no byte), alpha 0 is dropped
    assert [c.style for c in note.curves] == [3, 4, 3, 3]
    assert [c.dash for c in note.curves] == [None, "dotted", None, "dashed"]
    hidden = note_of(fx.session(curves=curves, dashes={1: 2, 4: 1}))
    assert [c.style for c in hidden.curves] == [3, 3, 3]
    assert [c.dash for c in hidden.curves] == [None, "dotted", "dashed"]  # by each curve's index in its hash


def test_styles_and_dashes_count_the_strokes_the_eraser_cut():
    """The pieces the eraser cut a stroke into are consecutive curves
    sharing a curveUUID, and curvesstyles and the keys of dashStyles count
    strokes: Notability 16's sample note has 596 curves and 149 styles."""
    curves = [fx.curve(WORKED, style=5), fx.curve(WORKED, style=5), fx.curve(WORKED, rgba=(9, 9, 9, 107), style=4),
              fx.curve(WORKED), fx.curve(WORKED), fx.curve(WORKED)]
    strokes = [0, 0, 1, 2, 2, 3]
    note = note_of(fx.session(curves=curves, strokes=strokes, dashes={2: 1, 3: 2}))
    assert [c.style for c in note.curves] == [5, 5, 4, 3, 3, 3]
    assert [c.dash for c in note.curves] == [None, None, None, "dashed", "dashed", "dotted"]
    per_curve = note_of(fx.session(curves=curves, strokes=strokes, styles=[3, 5, 4, 5, 3, 3], dashes={2: 1}))
    assert [c.style for c in per_curve.curves] == [3, 5, 4, 5, 3, 3]  # a byte for every curve: counted by curve
    assert [c.dash for c in per_curve.curves] == [None, None, "dashed", None, None, None]


def test_a_groups_transform_and_its_nesting():
    inner = {"transform": [1, 0, 0, 1, 0, 0], "index": 0, "curves": [fx.curve(WORKED, width=1.0)]}
    group = {"transform": [0, 0.5, -0.5, 0, 100, 0], "index": 1, "curves": [fx.curve(WORKED, width=2.0)],
             "groups": [inner]}
    note = note_of(fx.session(curves=[fx.curve(WORKED), fx.curve(WORKED)], groups=[group]))
    assert len(note.curves) == 4  # the group at position 1, between the strokes; the one in it at 0, below its curve
    first, nested, grouped, last = note.curves
    assert grouped.ink.scale == pytest.approx(0.5) and grouped.base == pytest.approx(1.0)
    assert nb.curve_points(grouped)[0] == pytest.approx((100 - 0.5 * 20, 0.5 * 10))  # turned a quarter, halved
    assert nested.base == pytest.approx(0.5) and first.ink is last.ink

    deep = {"transform": [1, 0, 0, 1, 0, 0], "index": 0, "curves": [fx.curve(WORKED)]}
    for _ in range(nb.MAX_GROUP_DEPTH):
        deep = {"transform": [1, 0, 0, 1, 0, 0], "index": 0, "curves": [fx.curve(WORKED)], "groups": [deep]}
    note = note_of(fx.session(groups=[deep]))
    assert len(note.curves) == nb.MAX_GROUP_DEPTH and note.left_out["group"] == 1


def test_strokes_shapes_and_groups_share_one_z_order():
    """A shape's index and an ink group's are positions in one z-order and
    the strokes take the other positions in turn; a group's members are
    drawn at its position, one after another. A page draws them so."""
    curves = [fx.curve(WORKED, width=1.0), fx.curve(WORKED, width=2.0), fx.curve(WORKED, width=2.0),
              fx.curve(WORKED, width=4.0)]
    group = {"transform": [1, 0, 0, 1, 0, 0], "index": 4, "curves": [fx.curve(WORKED, width=5.0)],
             "shapes": [("line", fx.shape(startPt=(0, 5), endPt=(10, 5), width=6.0))]}
    shapes = [("line", 3, fx.shape(startPt=(0, 0), endPt=(10, 0), width=3.0)),
              ("line", 1, fx.shape(startPt=(0, 0), endPt=(10, 0), width=1.5))]
    note = note_of(fx.session(curves=curves, strokes=[0, 1, 1, 2], shapes=shapes, groups=[group]))
    drawn = sorted(note.curves + note.shapes, key=lambda item: item.z)
    widths = [1.0, 1.5, 2.0, 2.0, 3.0, 5.0, 6.0, 4.0]  # stroke 0, shape 1, stroke 1, shape 3, the group at 4, stroke 2
    assert [item.base if isinstance(item, nb.Curve) else item.width for item in drawn] == widths
    assert [item.z for item in drawn] == list(range(8))
    strokes = nm.page_strokes(note, nm.plan_note(note, {}).pages[0], Counter())
    assert [round(s["size"] * 565 / 612, 2) for s in strokes] == widths


def _spatial(note_bytes: bytes):
    top = plistlib.loads(note_bytes)
    return top, next(o for o in top["$objects"] if isinstance(o, dict) and "curvespoints" in o)


def _tamper(session: bytes, **arrays) -> bytes:
    top, spatial = _spatial(session)
    spatial.update(arrays)
    return plistlib.dumps(top, fmt=plistlib.FMT_BINARY, sort_keys=False)


def test_damaged_arrays_are_bounded_and_counted():
    three = [fx.curve(WORKED), fx.curve(WORKED), fx.curve(WORKED)]
    session = fx.session(curves=three)
    _, spatial = _spatial(session)
    cut = note_of(_tamper(session, curvespoints=spatial["curvespoints"][:40]))  # 5 points of 12
    assert len(cut.curves) == 1 and cut.left_out["cut short"] == 2
    zero = note_of(_tamper(session, curvesnumpoints=struct.pack("<3i", 4, 0, 4)))
    assert len(zero.curves) == 1 and zero.left_out["cut short"] == 2
    mismatch = note_of(_tamper(session, curvesfractionalwidths=spatial["curvesfractionalwidths"][:8]))
    assert len(mismatch.curves) == 3 and mismatch.curves[0].ink.fractional is None
    assert any("width data" in w for w in mismatch.warnings)

    points = list(struct.unpack("<24f", spatial["curvespoints"]))
    points[3], points[13] = float("nan"), 5e6
    bad = note_of(_tamper(session, curvespoints=struct.pack("<24f", *points)))
    plan = nm.plan_note(bad, {})
    assert plan.left_out["stroke"] == 2 and sum(len(p.curves) for p in plan.pages) == 1


def test_a_curve_with_too_many_points_is_left_out():
    long = fx.curve([(i, i % 7) for i in range(nb.MAX_CURVE_POINTS + 3)], polyline=True)
    note = note_of(fx.session(curves=[long, fx.curve(WORKED)]))
    assert len(note.curves) == 1 and note.left_out["stroke"] == 1


def test_the_layout_from_the_paper_model_and_from_rich_text():
    files = {f"PDFs/{PDF}": fx.pdf()}
    model = note_of(fx.session(layout=[(PDF, 1), None, ("Gone.pdf", 1)], pdfs={PDF: 2},
                               paper={"identifier": "Color:#FFF48E"}), files)
    assert [(e.pdf, e.page) for e in model.layout] == [(PDF, 1), (None, None), ("Gone.pdf", 1)]
    assert model.missing == ["PDFs/Gone.pdf"] and model.paper.color == "#fff48e"
    legacy = note_of(fx.session(layout=[(PDF, 1), None], pdfs={PDF: None}, file_keys=True, indexed=True), files)
    assert [(e.pdf, e.page) for e in legacy.layout] == [(PDF, 1), (None, None)]
    assert legacy.pdfs[PDF].version == 1 and legacy.pdfs[PDF].path == f"PDFs/{PDF}"


def test_text_runs():
    runs = [{"text": "Hi 😀 "}, {"text": "bold", "font": "HelveticaNeue-Bold", "size": 22},
            {"text": " and slanted\n", "font": "Avenir-Oblique"},
            {"text": "item\n", "other": {"indent-decoration-style": 2, "indent-decoration-number": 4,
                                         "indent-level": 1}},
            {"text": "done", "other": {"indent-decoration-style": 3, "checklist-checked": 1,
                                       "NSStrikethrough": 1, "highlight": (1, 1, 0, 1)},
             "color": (0.0, 0.435294121503830, 1.0, 1.0)}]
    note = note_of(fx.session(flow=runs))
    texts = [(r.text, r.bold, r.italic) for r in note.flow]
    assert texts == [("Hi 😀 ", False, False), ("bold", True, False), (" and slanted\n", False, True),
                     ("item\n", False, False), ("done", False, False)]
    item, done = note.flow[3], note.flow[4]
    assert (item.list_style, item.number, item.level) == (2, 4, 1)
    assert (done.list_style, done.checked, done.strike, done.mark) == (3, True, True, True)
    assert done.rgba == pytest.approx((0.0, 0.435294, 1.0, 1.0), abs=1e-5) and note.flow[1].size == 22


def test_runs_out_of_order_overlapping_or_past_the_end():
    arch = fx.Archiver()
    text = "abcdef"
    subs = [arch.dictionary({"subRangeRangeKey": arch.string(r), "subRangeFontKey": arch.dictionary(
        {"NSFontNameAttribute": arch.string(f), "NSFontSizeAttribute": 12})})
        for r, f in (("{3, 10}", "X-Bold"), ("{0, 2}", "X-Italic"), ("{1, 3}", "X-Bold"))]
    attributed = arch.dictionary({"stringKey": arch.string(text), "subRangesKey": arch.array(subs)})
    rich = arch.obj("FormattedString", {"attributedString": attributed})
    note_bytes = arch.dumps(arch.obj("NoteTakingSession", {"richText": rich}))
    note = note_of(note_bytes)
    assert [(r.text, r.bold, r.italic) for r in note.flow] == [("ab", False, True), ("cd", True, False),
                                                                ("ef", True, False)]


def test_media_kinds():
    media = [{"kind": "picture", "origin": (10, 10), "size": (40, 30), "path": "Images/Image .png", "z": 3,
              "rotation": 0.5, "flip_h": True, "crop": (1, 2, 30, 20), "caption": "A [cap]|tion"},
             {"kind": "picture", "origin": (10, 10), "size": (40, 30), "path": "Images/Gone.png", "z": 1},
             {"kind": "picture", "origin": (10, 10), "size": (40, 30), "path": "Images/Image .png", "missing": True},
             {"kind": "math", "origin": (5, 5), "size": (50, 20), "latex": "x^2", "color": (1, 0, 0, 1)},
             {"kind": "sticky", "origin": (100, 100), "size": (80, 80)},
             {"kind": "AudioMediaObject", "origin": (1, 1), "size": (2, 2)},
             {"kind": "text", "origin": (1, 1), "size": (0, 0), "runs": [{"text": "x"}]}]
    note = note_of(fx.session(media=media), {"Images/Image .png": fx.png()})
    assert [m.kind for m in note.media] == ["picture", "math", "sticky", "picture", "picture"]
    picture = note.media[-1]
    assert (picture.picture, picture.rotation, picture.flip_h, picture.crop) == ("Images/Image .png", 0.5, True,
                                                                                 (1, 2, 30, 20))
    assert picture.caption == "A [cap]|tion"
    assert note.media[0].picture is None and note.media[3].picture is None
    assert note.missing == ["Images/Gone.png", "Images/Image .png"]
    assert note.media[2].background == pytest.approx(nb.STICKY_COLOR) and note.media[1].latex == "x^2"
    assert note.left_out["media"] == 1 and note.classes == Counter({"AudioMediaObject": 1})
    assert note.left_out["object"] == 1


def test_recordings_with_and_without_a_library():
    files = {"Recordings/library.plist": fx.library([
        {"identifier": 2, "filepath": "Recording 2.m4a", "userFacingName": "Seminar"},
        {"identifier": 1, "filepath": "Recording 1.m4a", "name": "Recording 1"},
        {"identifier": 3, "filepath": "Gone.m4a"}]),
        "Recordings/Recording 1.m4a": fx.m4a(), "Recordings/Recording 2.m4a": fx.m4a()}
    note = note_of(fx.session(), files)
    assert [(r.path, r.name) for r in note.recordings] == [("Recordings/Recording 1.m4a", "Recording 1"),
                                                           ("Recordings/Recording 2.m4a", "Seminar"),
                                                           (None, "Gone")]
    assert note.missing == ["Recordings/Gone.m4a"]
    bare = note_of(fx.session(), {"Recordings/b.m4a": fx.m4a(), "Recordings/a.m4a": fx.m4a()})
    assert [r.name for r in bare.recordings] == ["a", "b"]


def test_title_subject_and_version():
    note = note_of(fx.session(name="From the root", subject="Physics", version=12),
                   meta=fx.metadata("  From metadata  ", "unsortedNotesKey"))
    assert note.title == "From metadata" and note.subject == "Physics"
    assert any("newer Notability (format 12)" in w for w in note.warnings)
    assert note_of(fx.session(name="", subject="Unfiled Notes"), meta=fx.metadata("", "")).title == "Note"
    assert note_of(fx.session(name=""), meta=fx.metadata(""), path="x/Lecture 3.nbn").title == "Lecture 3"


def test_a_preview_reads_no_ink():
    note = note_of(fx.session(curves=[fx.curve(WORKED)],
                              shapes=[("line", 0, fx.shape(startPt=(0, 0), endPt=(10, 0)))]), ink_too=False)
    assert note.curves == [] and note.shapes == []


# --- pages ---------------------------------------------------------------------------------

def _note(**fields) -> nb.Note:
    return nb.Note(title="t", **fields)


def test_the_worked_page_examples():
    paper = _note(width=565.0)
    pw, ph, height = nm.paper_page(paper)
    assert (pw, height) == (612, 741.5625) and ph == pytest.approx(803.25)
    frames, _ = nm.frames(paper, {}, [835.0])
    assert len(frames) == 2 and frames[1].k == pytest.approx(1.083186, abs=1e-6)
    i = nm._index([f.top for f in frames], 835.0)
    f = frames[i]
    assert i == 1 and ((100 - f.ox) * f.k, (835 - f.oy) * f.k) == (pytest.approx(124.26, abs=0.005),
                                                                   pytest.approx(101.21, abs=0.005))

    a4 = _note(width=565.0, layout=[nb.LayoutEntry("a.pdf", 1)], pdfs={"a.pdf": nb.PdfFile("a.pdf", "a.pdf", 2)})
    (f,), _ = nm.frames(a4, {"a.pdf": [(595.28, 841.89)]}, [])
    assert (f.bottom - f.top, f.oy - f.top) == (800, pytest.approx(0.934, abs=0.001))
    assert f.k == pytest.approx(1.05359, abs=1e-5) and (f.width, f.height) == (595.28, 841.89)

    old = _note(width=565.0, layout=[nb.LayoutEntry("o.pdf", 1)], pdfs={"o.pdf": nb.PdfFile("o.pdf", "o.pdf", 1)})
    (f,), _ = nm.frames(old, {"o.pdf": [(720, 540)]}, [])
    assert f.bottom - f.top == 424 and f.k == pytest.approx(612 / (0.85 * 565))


def test_paper_sizes():
    a4 = _note(width=565.0, paper=nb.PaperSpec(size="a4"))
    pw, ph, height = nm.paper_page(a4)
    assert (pw, ph) == (595.28, pytest.approx(841.89)) and height == pytest.approx(565 * 841.89 / 595.28)
    static = _note(width=610.0, paper=nb.PaperSpec(size="letter", sizing="staticWidth"))
    assert nm.paper_page(static)[2] == 789  # floor(610 × 11 / 8.5)
    landscape = _note(width=565.0, paper=nb.PaperSpec(landscape=True))
    pw, ph, _height = nm.paper_page(landscape)
    assert pw == 612 and ph == pytest.approx(612 * 612 / 803.25)


def test_implicit_pages_pruning_and_the_title_area():
    note = _note(width=565.0, layout=[nb.LayoutEntry(None, None)] * 3)
    frames, _ = nm.frames(note, {}, [100.0, 2000.0])
    assert len(frames) == 3  # 2000 is on page 3; a trailing empty page would go
    frames, _ = nm.frames(note, {}, [100.0])
    assert len(frames) == 1
    frames, _ = nm.frames(note, {}, [])
    assert len(frames) == 1
    assert nm._index([f.top for f in frames], -40.0) == 0
    frames, _ = nm.frames(_note(width=565.0), {}, [5000.0])
    assert len(frames) == 7 and nm._index([f.top for f in frames], 5000.0) == 6


def test_missing_pdfs_and_pages_become_paper(monkeypatch):
    note = _note(width=565.0, layout=[nb.LayoutEntry("gone.pdf", 1), nb.LayoutEntry("a.pdf", 3)],
                 pdfs={"a.pdf": nb.PdfFile("a.pdf", "a.pdf", 2)})
    frames, warnings = nm.frames(note, {"a.pdf": [(612, 792)]}, [10.0, 900.0])
    assert [f.pdf for f in frames] == [None, None]
    assert warnings == ["Missing PDF gone.pdf: its pages are blank paper", "a.pdf has no page 3: blank paper"]
    monkeypatch.setattr(nm, "MAX_NOTE_PAGES", 3)
    capped = nb.Note(title="t", width=565.0, curves=[], media=[nb.Media("sticky", 0, 0, 10, 10, background=(1, 1, 1, 1)),
                                                         nb.Media("sticky", 0, 9000, 10, 10, background=(1, 1, 1, 1))])
    plan = nm.plan_note(capped, {})
    assert len(plan.pages) == 3 and plan.left_out["page"] == 1


def test_what_a_note_becomes():
    files = {f"PDFs/{PDF}": fx.pdf(((612, 792), (612, 792)))}
    sizes = {PDF: [(612.0, 792.0), (612.0, 792.0)]}
    original = note_of(fx.session(layout=[(PDF, 1), (PDF, 2)], pdfs={PDF: 2}), files)
    plan = nm.plan_note(original, sizes)
    assert (plan.kind, plan.pdf, len(plan.pages)) == ("original", PDF, 2)
    reordered = note_of(fx.session(layout=[(PDF, 2), (PDF, 1)], pdfs={PDF: 2}), files)
    assert nm.plan_note(reordered, sizes).kind == "composed"
    mixed = note_of(fx.session(layout=[None, (PDF, 1), (PDF, 2)], pdfs={PDF: 2}), files)
    assert nm.plan_note(mixed, sizes).kind == "composed"
    drawn = note_of(fx.session(curves=[fx.curve(WORKED)]))
    assert nm.plan_note(drawn, {}).kind == "notebook"
    typed = note_of(fx.session(flow=[{"text": "only typed"}]))
    plan = nm.plan_note(typed, {})
    assert plan.kind == "note" and plan.flow == [{"content": "only typed", "children": []}]


# --- ink -------------------------------------------------------------------------------------

def _bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def _distance_to_polyline(p, line):
    best = math.inf
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        dx, dy = bx - ax, by - ay
        t = 0 if not dx and not dy else max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy))
    return best


def test_flattening_stays_on_the_curve_and_a_point_apart():
    points = [(10, 10), (60, -40), (120, 160), (200, 50), (230, 0), (300, 300), (240, 120)]
    note = note_of(fx.session(curves=[fx.curve(points, fractional=[1, 1.5, 0.5])]))
    samples = nm.flatten(note.curves[0], frame())
    line = [(s["x"], s["y"]) for s in samples]
    for seg in (points[0:4], points[3:7]):
        for i in range(201):
            assert _distance_to_polyline(_bezier(*seg, i / 200), line) <= 0.055
    assert max(math.dist(a, b) for a, b in zip(line, line[1:])) <= nm.STEP_PT + nm.DECIMATE_PT
    straight = note_of(fx.session(curves=[fx.curve([(0, 0), (100, 0), (200, 0), (300, 0)])]))
    line = [(s["x"], s["y"]) for s in nm.flatten(straight.curves[0], frame())]
    assert len(line) >= 300 and max(math.dist(a, b) for a, b in zip(line, line[1:])) <= nm.STEP_PT + 0.01
    vee = note_of(fx.session(curves=[fx.curve([(0, 0), (50, 100), (100, 0)], polyline=True)]))
    line = [(s["x"], s["y"]) for s in nm.flatten(vee.curves[0], frame())]
    assert (50, 100) in line and max(math.dist(a, b) for a, b in zip(line, line[1:])) <= nm.STEP_PT + 0.01


def test_decimation_and_width_interpolation():
    tiny = note_of(fx.session(curves=[fx.curve([(0, 0), (0.01, 0), (0.02, 0), (0.05, 0), (5, 0)], polyline=True,
                                                fractional=[1, 1, 1, 1, 2])]))
    samples = nm.flatten(tiny.curves[0], frame())
    assert (samples[0]["x"], samples[-1]["x"]) == (0, 5)
    assert all(math.dist((a["x"], a["y"]), (b["x"], b["y"])) >= nm.DECIMATE_PT - 1e-9
               for a, b in zip(samples, samples[1:-1]))
    half = next(s for s in samples if abs(s["x"] - 2.5) < 0.6)
    assert half["w"] == pytest.approx(1 + (half["x"] - 0.05) / 4.95, abs=1e-6)


def test_channels_appear_only_for_real_values():
    even = note_of(fx.session(curves=[fx.curve(WORKED, fractional=[0.8, 0.8], width=2.0)]))
    stroke = nm.curve_stroke(even.curves[0], frame(k=2.0))
    assert stroke["pen"] is False and stroke["ch"] == "xy" and stroke["size"] == pytest.approx(3.2)
    pressed = note_of(fx.session(curves=[fx.curve(WORKED, fractional=[1, 0.5], force=[2.0, 4.0])]))
    stroke = nm.curve_stroke(pressed.curves[0], frame())
    assert stroke["ch"] == "xypw" and decoded(stroke)[0]["p"] == pytest.approx(0.48, abs=0.001)
    session = fx.session(curves=[fx.curve(WORKED, fractional=[1, 0.5])])
    _, spatial = _spatial(session)
    tilted = note_of(_tamper(session, curvesaltitudeangles=struct.pack("<2f", 0.5, 1.0),
                             curvesazimuthunitvector=struct.pack("<4f", 0, 1, 0, 1)))
    stroke = nm.curve_stroke(tilted.curves[0], frame())
    first = decoded(stroke)[0]
    assert stroke["ch"] == "xyazw" and first["a"] == 29 and first["z"] == 90
    assert all("t" not in s and "t0" not in stroke for s in decoded(stroke))


def test_width_caps():
    wide = note_of(fx.session(curves=[fx.curve(WORKED, fractional=[1, 8], width=1.0)]))
    stroke = nm.curve_stroke(wide.curves[0], frame())
    assert stroke["size"] == pytest.approx(2.0) and max(s["w"] for s in decoded(stroke)) == pytest.approx(4.0)
    heavy = note_of(fx.session(curves=[fx.curve(WORKED, fractional=[1, 3], width=50.0)]))
    stroke = nm.curve_stroke(heavy.curves[0], frame())
    assert stroke["size"] * max(s["w"] for s in decoded(stroke)) <= 100
    ink.Stroke.model_validate({**stroke, "id": "n0"})


def test_highlighter_and_pencil():
    note = note_of(fx.session(curves=[fx.curve(WORKED, width=10, rgba=(250, 157, 0, 0x6B), style=4),
                                      fx.curve(WORKED, fractional=[1, 0.6], style=5, rgba=(0, 0, 0, 255))]))
    marker = nm.curve_stroke(note.curves[0], frame())
    assert (marker["tool"], marker["opacity"], marker["ch"], marker["size"]) == ("highlighter", 0.42, "xy", 10)
    pencil = nm.curve_stroke(note.curves[1], frame())
    assert pencil["brush"] == "pencil" and "o" not in pencil["ch"] and "w" in pencil["ch"]


def test_files_are_valid_and_split_deterministically(monkeypatch):
    space = {"kind": "canvas", "width": 612, "height": 803.25}
    dots = [{"color": "#000000", "size": 1, "pen": False, "ch": "xy", "pts": [i, i]} for i in range(5003)]
    files = list(ink.group_files(dots, space))
    assert [len(f["strokes"]) for f in files] == [5000, 3]
    assert [s["id"] for s in files[1]["strokes"]] == ["n0", "n1", "n2"]
    for f in files:
        parsed = ink.parse_ink(ink.dumps(f))
        assert ink.dumps(ink.parse_ink(ink.dumps(parsed))) == ink.dumps(parsed)
    assert ink.dumps(next(ink.group_files(dots[:3], space))) == ink.dumps(next(ink.group_files(dots[:3], space)))

    long = {"color": "#000000", "size": 1, "pen": False, "ch": "xy", "pts": [0, 0] * 200_000}
    assert [len(f["strokes"]) for f in ink.group_files([long, long, long], space)] == [2, 1]

    monkeypatch.setattr(ink, "GROUP_SPLIT_BYTES", 600)
    files = list(ink.group_files(dots[:40], space))
    assert len(files) > 1 and all(len(ink.dumps(f)) <= 600 for f in files)
    assert sum(len(f["strokes"]) for f in files) == 40


def test_a_stroke_past_the_sample_cap_is_left_out(monkeypatch):
    monkeypatch.setattr(nm, "MAX_STROKE_SAMPLES", 50)
    note = note_of(fx.session(curves=[fx.curve([(0, 0), (100, 0), (200, 0), (300, 0)]), fx.curve(WORKED)]))
    assert nm.flatten(note.curves[0], frame()) is None
    plan = nm.plan_note(note, {})
    left = Counter()
    assert len(list(nm.page_strokes(note, plan.pages[0], left))) == 1 and left["stroke"] == 1


# --- shapes ----------------------------------------------------------------------------------

def _shapes_note(shapes):
    return note_of(fx.session(shapes=[(kind, i, s) for i, (kind, s) in enumerate(shapes)]))


def test_lines_and_arrows():
    note = _shapes_note([("line", fx.shape(startPt=(10, 10), endPt=(110, 10))),
                         ("line", fx.shape(startPt=(10, 50), endPt=(110, 50), arrow=1)),
                         ("line", fx.shape(startPt=(10, 90), endPt=(110, 90), controlPoint1=(60, 140), arrow=1))])
    line, arrow, curved = (nm.shape_stroke(s, frame()) for s in note.shapes)
    assert line["shape"] == {"kind": "line", "params": {"x1": 10, "y1": 10, "x2": 110, "y2": 10}}
    assert line["pen"] is False and line["ch"] == "xy" and xy(line) == [(10, 10), (110, 10)]  # its two ends
    assert arrow["shape"]["kind"] == "arrow" and "head" not in arrow["shape"]["params"]
    tip = xy(arrow)
    assert tip == ink.shape_samples("arrow", arrow["shape"]["params"])
    assert (110, 50) in tip and tip[-1] == pytest.approx((110 - 24 * math.cos(math.radians(30)),
                                                          50 + 24 * math.sin(math.radians(30))), abs=0.01)
    assert "shape" not in curved and decoded(curved)[0]["x"] == 10
    head = xy(curved)[xy(curved).index((110, 90)):]  # the head after the curve, its legs cut as an arrow's are
    assert len(head) > 5 and max(math.dist(a, b) for a, b in zip(head, head[1:])) <= ink.SHAPE_STEP + 0.01


def test_rectangles_triangles_and_ellipses():
    turn = math.radians(30)
    c, s = math.cos(turn), math.sin(turn)
    corners = [(100 + u * c - v * s, 100 + u * s + v * c) for u, v in ((-40, -20), (40, -20), (40, 20), (-40, 20))]
    skew = [(0, 0), (50, 0), (60, 30), (10, 30)]
    note = _shapes_note([
        ("rectangle", fx.shape(points=corners, fill=(1, 0, 0, 0.5))),
        ("triangle", fx.shape(points=[(0, 0), (40, 0), (20, 30)])),
        ("ellipse", fx.shape(rotatedRect={"corners": [[0, 0], [20, 0], [20, 60], [0, 60]]})),
        ("rectangle", fx.shape(points=skew)),
        ("polygon", fx.shape(points=[(0, 0), (30, 0), (40, 20), (10, 30), (0, 10)], fill=(0, 0, 1, 1))),
        ("partialshape", {"strokePath": b"\x25\xb3\xe5\x48"}),
        ("blob", {})])
    rect, triangle, ellipse, skewed, polygon = (nm.shape_stroke(s, frame()) for s in note.shapes)
    assert rect["shape"]["kind"] == "rect"
    assert rect["shape"]["params"] == {"cx": 100, "cy": 100, "w": 80, "h": 40, "angle": 30}
    assert rect["fill"] == {"color": "#ff0000", "opacity": 0.5}
    assert triangle["shape"] == {"kind": "triangle", "params": {"x1": 0, "y1": 0, "x2": 40, "y2": 0, "x3": 20,
                                                                 "y3": 30}}
    assert ellipse["shape"] == {"kind": "ellipse", "params": {"cx": 10, "cy": 30, "rx": 30, "ry": 10, "angle": 90}}
    h = (20 / 40) ** 2
    assert len(decoded(ellipse)) == math.ceil(math.pi * 40 * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h))) / 2) + 1
    for stroke in (rect, triangle, ellipse):  # the samples shapes.js draws the params as
        assert xy(stroke) == ink.shape_samples(stroke["shape"]["kind"], stroke["shape"]["params"])
    assert "shape" not in skewed and "shape" not in polygon and "fill" not in polygon
    for outline, corners in ((skewed, skew), (polygon, [(0, 0), (30, 0), (40, 20), (10, 30), (0, 10)])):
        pts = xy(outline)  # a plain outline: its corners, samples at most 2 pt apart, closed
        assert set(corners) <= set(pts) and pts[0] == pts[-1]
        assert max(math.dist(a, b) for a, b in zip(pts, pts[1:])) <= ink.SHAPE_STEP + 0.01
    counted = Counter()
    nm.shape_stroke(note.shapes[4], frame(), counted)
    assert counted["polygon fill"] == 1
    assert note.left_out["partial shape"] == 1 and note.left_out["shape"] == 1


def test_grouped_shapes_are_plain_strokes():
    group = {"transform": [2, 0, 0, 2, 0, 0], "index": 0,
             "shapes": [("ellipse", fx.shape(rotatedRect={"corners": [[0, 0], [20, 0], [20, 10], [0, 10]]}, width=1.5)),
                        ("square", fx.shape(points=[(0, 0), (10, 0), (10, 10), (0, 10)]))]}
    note = note_of(fx.session(groups=[group]))
    assert [(s.kind, s.grouped) for s in note.shapes] == [("ellipse", True), ("square", True)]
    ellipse, square = (nm.shape_stroke(s, frame()) for s in note.shapes)
    assert "shape" not in ellipse and ellipse["size"] == 3.0
    xs = [s["x"] for s in decoded(ellipse)]
    assert len(xs) == 50  # as many as a record's: max(48, ceil(96.9 / 2)), and the first again
    assert min(xs) == pytest.approx(0, abs=0.05) and max(xs) == 40
    assert "shape" not in square and len(decoded(square)) == 4 * 10 + 1  # 20 pt sides, a sample every 2 pt


def test_a_partly_erased_shape_is_what_the_eraser_left_of_its_centre_line():
    """A partialshape's outlinePath is what is left of the shape's centre
    line: each piece of it (a move and what follows) is a plain stroke
    along it with the shape's look, a quadratic drawn as its cubic and a
    close back to the piece's start. One that does not read is counted. A
    pencil shape keeps the pencil, and its record."""
    left = fx.path((0, [(0, 0)]), (1, [(30, 0)]),
                   (0, [(50, 0)]), (1, [(80, 0)]), (1, [(80, 20)]),
                   (0, [(0, 40)]), (2, [(20, 60), (40, 40)]), (3, [(50, 30), (60, 30), (70, 40)]), (4, []))
    erased = fx.shape(outlinePath=left, strokePath=fx.path((0, [(0, -1)]), (1, [(30, -1)])), width=1.5,
                      rgba=(1, 0.5, 0, 1), style=5, pattern=1)
    damaged = [fx.shape(strokePath=left), fx.shape(outlinePath=left[4:]), fx.shape(outlinePath=left[:-8]),
               fx.shape(outlinePath=fx.path((1, [(0, 0)]))), fx.shape(outlinePath=fx.path((0, [(0, 0)]), (5, [(1, 1)]))),
               fx.shape(outlinePath=fx.path((0, [(0, 0)]), (1, [(math.nan, 0)])))]
    note = _shapes_note([("partialshape", erased), *(("partialshape", shape) for shape in damaged),
                         ("line", fx.shape(startPt=(0, 0), endPt=(10, 0), style=5))])
    assert note.left_out["partial shape"] == len(damaged)
    one, two, three, pencil = note.shapes
    assert {(s.kind, s.style, s.dash, s.width) for s in (one, two, three)} == {("path", 5, "dashed", 1.5)}
    assert (one.points, one.segments, two.points, two.segments) == ([(0, 0), (30, 0)], (1,),
                                                                    [(50, 0), (80, 0), (80, 20)], (1, 1))
    assert three.segments == (3, 3, 1) and three.points[-1] == (0, 40)
    assert [v for p in three.points[1:3] for v in p] == pytest.approx([40 / 3, 160 / 3, 80 / 3, 160 / 3])
    for shape, ends, corner in ((one, [(0, 0), (30, 0)], (30, 0)), (two, [(50, 0), (80, 20)], (80, 0)),
                                (three, [(0, 40), (0, 40)], (40, 40))):
        stroke = nm.shape_stroke(shape, frame())
        assert "shape" not in stroke and (stroke["pen"], stroke["brush"], stroke["dash"]) == (False, "pencil", "dashed")
        pts = xy(stroke)
        assert [pts[0], pts[-1]] == ends and corner in pts
        assert max(math.dist(a, b) for a, b in zip(pts, pts[1:])) <= ink.SHAPE_STEP + 0.01
    line = nm.shape_stroke(pencil, frame())
    assert line["brush"] == "pencil" and line["shape"]["kind"] == "line"

    group = {"transform": [0, 1, -1, 0, 100, 0], "index": 0, "shapes": [("partialshape", erased)]}
    turned = note_of(fx.session(groups=[group])).shapes
    assert [(s.kind, s.grouped) for s in turned] == [("path", True)] * 3 and turned[0].points == [(100, 0), (100, 30)]


def test_a_sticky_is_a_filled_rect_under_the_ink():
    note = note_of(fx.session(curves=[fx.curve(WORKED)],
                              media=[{"kind": "sticky", "origin": (100, 100), "size": (80, 40),
                                      "rotation": math.radians(30), "color": (1, 0.8, 0.2, 1)}]))
    plan = nm.plan_note(note, {})
    strokes = list(nm.page_strokes(note, plan.pages[0], Counter()))
    sticky = strokes[0]
    k = 612 / 565
    assert (sticky["band"], sticky["pen"], sticky["fill"], sticky["size"]) == (0, False, {"color": "#ffcc33",
                                                                                         "opacity": 1}, 0.5)
    params = sticky["shape"]["params"]
    assert sticky["shape"]["kind"] == "rect" and params["angle"] == 30
    assert (params["w"], params["h"]) == (pytest.approx(80 * k, abs=0.01), pytest.approx(40 * k, abs=0.01))
    assert "tool" not in sticky and "opacity" not in sticky


def _shape_for(kind: str, p: dict) -> tuple:
    """The Notability shape the import maps onto a shape record's params: a
    line's or an arrow's ends, a triangle's corners, a rectangle's corners
    or those of the box an ellipse is inscribed in, from the top left."""
    if kind in ("line", "arrow"):
        return "line", fx.shape(startPt=(p["x1"], p["y1"]), endPt=(p["x2"], p["y2"]), arrow=int(kind == "arrow"))
    if kind == "triangle":
        return "triangle", fx.shape(points=[(p["x1"], p["y1"]), (p["x2"], p["y2"]), (p["x3"], p["y3"])])
    a = math.radians(p["angle"])
    c, s = math.cos(a), math.sin(a)
    hw, hh = (p["w"] / 2, p["h"] / 2) if kind == "rect" else (p["rx"], p["ry"])
    corners = [(p["cx"] + u * hw * c - v * hh * s, p["cy"] + u * hw * s + v * hh * c)
               for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    if kind == "rect":
        return "rectangle", fx.shape(points=corners)
    return "ellipse", fx.shape(rotatedRect={"corners": [list(corner) for corner in corners]})


def test_shapes_are_drawn_as_the_web_draws_them():
    """tests/shared/inkshapes.json ``samples``, each case of a kind the
    import writes: the Notability shape of its params becomes that record
    and the very samples shapes.js draws it as, and so does a sticky of a
    rectangle's."""
    shared = json.loads((SHARED / "inkshapes.json").read_text(encoding="utf-8"))["samples"]
    cases = [c for c in shared if c["kind"] in ("line", "rect", "triangle", "ellipse")
             or (c["kind"] == "arrow" and "head" not in c["params"])]  # the import writes no arrow head
    assert {c["kind"] for c in cases} == {"line", "arrow", "rect", "triangle", "ellipse"}
    note = _shapes_note([_shape_for(c["kind"], c["params"]) for c in cases])
    for case, shape in zip(cases, note.shapes, strict=True):
        stroke = nm.shape_stroke(shape, frame())
        assert stroke["shape"] == {"kind": case["kind"], "params": case["params"]}, case["note"]
        assert [list(p) for p in xy(stroke)] == case["pts"], case["note"]
        if case["kind"] == "rect":
            p = case["params"]
            sticky = nb.Media(kind="sticky", x=p["cx"] - p["w"] / 2, y=p["cy"] - p["h"] / 2, w=p["w"], h=p["h"],
                              rotation=math.radians(p["angle"]), background=(1, 1, 0, 1))
            stroke = nm.sticky_stroke(sticky, frame())
            assert stroke["shape"]["params"] == p and [list(q) for q in xy(stroke)] == case["pts"], case["note"]


# --- text --------------------------------------------------------------------------------------

def run(text, **style):
    return nb.Run(text, **style)


def test_flow_text_as_notes():
    runs = [run("Title\n\n\n"), run("$5 and # x\n"), run("1. not a list\n"),
            run("point\n", list_style=1), run("deep\n", list_style=1, level=3), run("next\n", list_style=2, number=7),
            run("todo\n", list_style=3), run("done", list_style=3, level=1, checked=True)]
    blocks = nm.flow_blocks(runs)
    assert [b["content"] for b in blocks] == ["Title", "\\$5 and # x", "1\\. not a list", "point", "7. next",
                                             "- [ ] todo"]
    assert blocks[3]["children"] == [{"content": "deep", "children": []}]
    assert blocks[5]["children"] == [{"content": "- [x] done", "children": []}]
    styled = nm.flow_blocks([run("a "), run("bold", bold=True), run(" and "), run(" it ", italic=True, strike=True)])
    assert styled[0]["content"] == "a **bold** and  ~~*it*~~"


def test_a_box_s_markdown():
    runs = [run("Plan\n"), run("first\n", list_style=1), run("sub\n", list_style=2, level=4, number=2),
            run("check\n", list_style=3, checked=True), run("after")]
    assert nm.box_markdown(runs) == "Plan\n- first\n    2. sub\n- [x] check\n\nafter"


def test_text_boxes_math_and_pictures():
    k = 2.0
    page = frame(k=k, width=600, height=800)
    text = nb.Media("text", 10, 20, 100, 30, rotation=0.3, runs=[run("Big ", size=20, rgba=(1, 0, 0, 1)),
                                                                   run("words here", size=20, rgba=(1, 0, 0, 1)),
                                                                   run("x", size=8)], background=(0, 0, 1, 0.5))
    left = Counter()
    box = nm.text_box(text, page, left)
    assert box["content"] == "Big words herex" and left["rotated box"] == 1
    tb = box["text_box"]
    assert (tb["x"], tb["y"], tb["w"], tb["h"], tb["auto"]) == (20, 40, 200, 60, False)
    assert (tb["size"], tb["color"], tb["bg"]) == (40, "#ff0000", "#0000ff")
    assert nm.text_box(nb.Media("text", 0, 0, 10, 10, runs=[run("  ")]), page) is None
    math_box = nm.text_box(nb.Media("math", 0, 0, 10, 10, latex="x^2\n\n+ 1", rgba=(0, 1, 0, 1)), page)
    assert math_box["content"] == "$$\nx^2\n+ 1\n$$" and math_box["text_box"]["color"] == "#00ff00"
    assert nm.text_box(nb.Media("math", 0, 0, 10, 10, latex="a $$ b"), page)["content"] == "a \\$\\$ b"
    turned = nm.picture_box(nb.Media("picture", 100, 100, 40, 20, rotation=math.pi / 2), page)
    assert (turned["w"], turned["h"]) == (pytest.approx(40 + 8), pytest.approx(80 + 8))
    assert turned["x"] == pytest.approx(240 - 20 - 4) and turned["auto"] is False


def test_paper():
    assert nm.paper(_note(width=565.0, paper=nb.PaperSpec(color="#ffeedd")))["color"] == "#ffeedd"
    assert nm.paper(_note(width=565.0, paper=nb.PaperSpec(legacy=0)))["color"] == nm.CREAM
    ruled = nm.paper(_note(width=565.0, paper=nb.PaperSpec(lines=0.25)))
    assert (ruled["pattern"], ruled["spacing"]) == ("ruled", 18)
    grid = nm.paper(_note(width=565.0, paper=nb.PaperSpec(grid=True)))
    assert (grid["pattern"], grid["spacing"]) == ("grid", 18)
    template = _note(width=565.0, paper=nb.PaperSpec(template=True), curves=[], media=[
        nb.Media("sticky", 0, 0, 10, 10, background=(1, 1, 1, 1))])
    assert "The paper template is not imported: plain paper instead" in nm.plan_note(template, {}).warnings


# --- helpers the import shares ---------------------------------------------------------------

def test_mp4_duration():
    assert mp4_duration_ms(io.BytesIO(fx.m4a(3500))) == 3500
    assert mp4_duration_ms(io.BytesIO(fx.m4a(61_000, version=1))) == 61_000
    assert mp4_duration_ms(io.BytesIO(fx.m4a(2000, moov_last=True))) == 2000
    assert mp4_duration_ms(io.BytesIO(fx.m4a(2000)[:40])) is None
    no_moov = struct.pack(">I4s", 16, b"ftyp") + b"M4A \0\0\0\0" + struct.pack(">I4s", 8, b"mdat")
    assert mp4_duration_ms(io.BytesIO(no_moov)) is None
    assert mp4_duration_ms(io.BytesIO(fx.m4a(49 * 3600 * 1000, version=1))) is None
    zero = fx.m4a(1000).replace(struct.pack(">I", 44100), struct.pack(">I", 0), 1)
    assert mp4_duration_ms(io.BytesIO(zero)) is None


def test_the_central_directory_guard():
    small = fx.zip_of({f"f{i}.txt": b"x" for i in range(5)})
    size = directory_size(io.BytesIO(small))
    assert 5 * 46 < size < 5 * 46 + 100
    with pytest.raises(DirectoryTooLarge):
        open_zip(io.BytesIO(small), max_directory=size - 1)
    assert open_zip(io.BytesIO(small), max_directory=size).namelist()
    assert directory_size(io.BytesIO(b"no zip here")) is None

    # the same zip with a ZIP64 end record: the classic record says 0xFFFFFFFF
    eocd = small.rfind(b"PK\x05\x06")
    count, cd_size, cd_offset = struct.unpack("<10xHII", small[eocd:eocd + 20])
    record = struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, cd_size, cd_offset)
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, eocd, 1)
    classic = bytearray(small[eocd:])
    classic[12:16] = struct.pack("<I", 0xFFFFFFFF)
    zip64 = small[:eocd] + record + locator + bytes(classic)
    assert directory_size(io.BytesIO(zip64)) == cd_size
    assert zipfile.ZipFile(io.BytesIO(zip64)).namelist() == [f"f{i}.txt" for i in range(5)]


# --- what a hostile note may cost ----------------------------------------------------------
# A keyed archive may name one object a million times for a few bytes each,
# so every cost is counted where it is spent, never per object.

def _same_object(monkeypatch, maker: str) -> None:
    """The fixture writer's ``maker`` (``media_object``) gives every call
    the first call's object: an archive naming one object many times."""
    real, made = getattr(fx, maker), []

    def once(*args):
        if not made:
            made.append(real(*args))
        return made[0]

    monkeypatch.setattr(fx, maker, once)


def test_an_object_named_many_times_is_read_once():
    arch = _archive(["$null", {"a": plistlib.UID(2), "b": plistlib.UID(2), "s": plistlib.UID(3),
                               "t": plistlib.UID(3), "$class": plistlib.UID(4)},
                     {"NS.objects": [plistlib.UID(3)]}, "text", {"$classname": "X" * (nb.MAX_NAME + 1)}])
    root = arch.root
    assert arch.array(root["a"]) is arch.array(root["b"]) and arch.mapping(root["a"]) is arch.mapping(root["b"])
    assert arch.string(root["s"]) is arch.string(root["t"]) == "text"
    assert arch.class_name(root) == ""  # past MAX_NAME: no class


def _group_naming_one_archive(times: int) -> tuple[bytes, int]:
    """An ink group whose objects name one archive ``times`` times (the
    binary plist holds it once), and the size of that archive."""
    inner = fx.Archiver("NSKeyedArchiver", "root")
    archive = inner.dumps(fx.ink_hash(inner, [fx.curve(WORKED), fx.curve([(i, i % 9) for i in range(2000)],
                                                                         polyline=True)]))
    raw = plistlib.dumps({"inkGroup": {"inkGroupObjects": [{"type": 1, "object": archive}] * times,
                                       "transform": [1, 0, 0, 1, 0, 0]}, "index": 0}, fmt=plistlib.FMT_BINARY)
    return raw, len(archive)


def test_the_plists_inside_a_note_are_read_within_its_budget(monkeypatch):
    raw, archive = _group_naming_one_archive(50)
    monkeypatch.setattr(fx, "group_bytes", lambda g: g)  # the group as it is
    session = fx.session(curves=[fx.curve(WORKED)], groups=[raw])
    monkeypatch.setattr(nb, "MAX_SESSION_BYTES", 4 * len(session))
    note = note_of(session)
    read = (4 * len(session) - len(raw)) // archive  # the group's own plist first, then an archive a time
    assert 1 <= read < 50 and len(note.curves) == 1 + 2 * read and note.left_out["group"] == 50 - read


def test_typed_text_is_read_within_its_budget(monkeypatch):
    monkeypatch.setattr(nb, "MAX_NOTE_TEXT", 10)
    _same_object(monkeypatch, "media_object")
    box = {"kind": "text", "origin": (10, 10), "size": (100, 30), "runs": [{"text": "abcdef"}]}
    note = note_of(fx.session(media=[box] * 3, flow=[{"text": "never read"}]))
    assert ["".join(r.text for r in m.runs) for m in note.media] == ["abcdef", "abcd", ""]
    assert note.flow == [] and note.left_out["typed text"] == 3
    assert "typed text is too long: 3 texts" in nb.left_out_reasons(note.left_out)[0]


def test_no_more_runs_are_read_than_the_text_has_units():
    arch = fx.Archiver()
    empty = [arch.dictionary({"subRangeRangeKey": arch.string("{1, 0}")}) for _ in range(4)]
    bold = arch.dictionary({"subRangeRangeKey": arch.string("{0, 3}"), "subRangeFontKey": arch.dictionary(
        {"NSFontNameAttribute": arch.string("X-Bold")})})
    attributed = arch.dictionary({"stringKey": arch.string("abc"), "subRangesKey": arch.array(empty + [bold])})
    note = note_of(arch.dumps(arch.obj("NoteTakingSession", {"richText": arch.obj(
        "FormattedString", {"attributedString": attributed})})))
    assert [(r.text, r.bold) for r in note.flow] == [("abc", False)]  # the fifth run of three units: not read
    astral = note_of(fx.session(flow=[{"text": "\U0001F600"}, {"text": "b", "font": "X-Bold"}]))
    assert [(r.text, r.bold) for r in astral.flow] == [("\U0001F600", False), ("b", True)]


def test_names_longer_than_a_file_name_are_not_names():
    long = "x" * (nb.MAX_NAME + 1)
    note = note_of(fx.session(layout=[(long + ".pdf", 1)], pdfs={long + ".pdf": 2}, media=[
        {"kind": "picture", "origin": (1, 1), "size": (5, 5), "path": long},
        {"kind": "text", "origin": (1, 1), "size": (5, 5), "runs": [{"text": "a", "font": long + "-Bold"}]}]))
    assert [(e.pdf, e.page) for e in note.layout] == [(None, None)] and note.pdfs == {}
    assert note.missing == [] and note.left_out["object"] == 1  # the picture names no file
    assert [m.kind for m in note.media] == ["text"] and not note.media[0].runs[0].bold
    assert nb.Members({long: None}).resolve(long) is None


def test_past_max_media_the_rest_is_left_out(monkeypatch):
    monkeypatch.setattr(nb, "MAX_MEDIA", 2)
    sticky = {"kind": "sticky", "origin": (10, 10), "size": (20, 20)}
    note = note_of(fx.session(media=[sticky] * 5))
    assert len(note.media) == 2 and note.left_out["media limit"] == 3
    assert nb.left_out_reasons(note.left_out) == ["Past 10,000 objects the rest is left out (3 objects)"]


def test_a_missing_file_is_named_once_and_a_recording_file_listed_twice_is_one():
    note = note_of(fx.session(layout=[("Gone.pdf", 1), ("Gone.pdf", 2)], media=[
        {"kind": "picture", "origin": (1, 1), "size": (5, 5), "path": "Images/Gone.png"},
        {"kind": "picture", "origin": (9, 9), "size": (5, 5), "path": "Images/Gone.png"}]),
        {"Recordings/library.plist": fx.library([{"identifier": 1, "filepath": "a.m4a", "name": "First"},
                                                 {"identifier": 2, "filepath": "a.m4a", "name": "Again"}]),
         "Recordings/a.m4a": fx.m4a()})
    assert note.missing == ["PDFs/Gone.pdf", "Images/Gone.png"]
    assert [(r.path, r.name) for r in note.recordings] == [("Recordings/a.m4a", "First")]


def test_finding_the_notes_of_a_large_upload_stays_linear():
    import time
    many = fx.zip_of({f"f{i}/{name}": b"" for i in range(4000) for name in ("Session.plist", "x.png")})
    entries, _ = walked(many)
    started = time.perf_counter()
    bundles = nb.find_bundles(entries, "many.zip")
    assert len(bundles) == 4000 and set(bundles[0].members.entries) == {"Session.plist", "x.png"}
    assert time.perf_counter() - started < 2  # each file against each folder took about 9 s


def test_a_transform_composed_past_a_float_s_range_leaves_its_stroke_out():
    inner = {"transform": [1e-200, 0, 0, 1e-200, 1e200, -1e200], "index": 0,
             "curves": [fx.curve([(1, 1), (2, 2)], polyline=True)]}
    outer = {"transform": [1e200, 0, 1e200, 1e200, 0, 0], "index": 0, "groups": [inner]}  # inf - inf: NaN
    note = note_of(fx.session(curves=[fx.curve(WORKED)], groups=[outer]))
    assert len(note.curves) == 2 and math.isnan(nb.curve_points(note.curves[0])[0][0])
    plan = nm.plan_note(note, {})
    assert plan.left_out["stroke"] == 1 and len(list(nm.page_strokes(note, plan.pages[0], plan.left_out))) == 1


def test_a_stroke_past_what_a_stroke_file_holds_is_left_out_and_the_page_comes_in():
    """A coordinate under COORD_LIMIT can still land past ink.MAX_XY (±10^7
    pt, what a stroke file holds) on its page: a narrow note maps a unit to
    612 / W points (38.25 at W 16). That stroke is left out and counted,
    as one past the sample caps is, and the page's other strokes make a
    file that parses."""
    far = 900_000
    note = note_of(fx.session(width=16.0, curves=[fx.curve([(2, 2), (4, 2), (6, 4), (8, 4)]),
                                                  fx.curve([(far, 2), (far + 2, 4)], polyline=True)]))
    plan = nm.plan_note(note, {})
    page = plan.pages[0]
    assert far < nb.COORD_LIMIT and plan.left_out["stroke"] == 0 and page.curves == [0, 1]
    assert far * page.frame.k * ink.COORD_UNIT > ink.MAX_XY
    left = Counter()
    strokes = list(nm.page_strokes(note, page, left))
    assert len(strokes) == 1 and left["stroke"] == 1
    space = {"kind": "canvas", "width": page.frame.width, "height": page.frame.height}
    files = list(ink.group_files(strokes, space))
    assert [len(ink.parse_ink(ink.dumps(f)).strokes) for f in files] == [1]


def test_samples_are_counted_before_they_are_made(monkeypatch):
    note = note_of(fx.session(curves=[fx.curve([(0, 0), (60_000, 0)], polyline=True)]))

    def never(*_args, **_kwargs):
        raise AssertionError("sampled")

    monkeypatch.setattr(nm, "_edge", never)
    assert nm.flatten(note.curves[0], frame()) is None  # 60,000 samples past MAX_STROKE_SAMPLES, none made
    shapes = _shapes_note([("line", fx.shape(startPt=(0, 0), endPt=(120_000, 0), arrow=1)),
                           ("polygon", fx.shape(points=[(0, 0), (60_000, 0), (0, 300), (60_000, 300)])),
                           ("ellipse", fx.shape(rotatedRect={"corners": [[0, 0], [60_000, 0], [60_000, 18_000],
                                                                         [0, 18_000]]})),
                           ("ellipse", fx.shape(rotatedRect={"corners": [[0, 0], [60_000, 0], [60_000, 18_000],
                                                                         [10, 18_000]]})),
                           ("line", fx.shape(startPt=(0, 0), endPt=(60_000, 0)))])
    *past, line = (nm.shape_stroke(s, frame()) for s in shapes.shapes)
    assert past == [None, None, None, None]  # past 50,000 samples at 2 pt
    assert xy(line) == [(0, 0), (60_000, 0)]  # a line is its two ends, however long
    sticky = nb.Media(kind="sticky", x=0, y=0, w=30_000, h=30_000, background=(1, 1, 0, 1))
    assert nm.sticky_stroke(sticky, frame()) is None


def test_the_note_s_samples_are_spent_before_a_stroke_is_made(monkeypatch):
    note = note_of(fx.session(curves=[fx.curve(WORKED)] * 5))
    plan = nm.plan_note(note, {})
    made, real = [], nm.curve_stroke
    monkeypatch.setattr(nm, "curve_stroke", lambda curve, at: made.append(curve) or real(curve, at))
    left, counts = [25], Counter()
    strokes = list(nm.page_strokes(note, plan.pages[0], counts, left))
    assert len(strokes) == 2 and len(made) == 3 and left == [0]  # the third goes past: it and the rest go
    assert counts["samples"] == 3


def test_a_varying_stroke_wider_than_100_pt_is_drawn_at_100():
    for base in (150.0, 400.0):
        note = note_of(fx.session(curves=[fx.curve(WORKED, fractional=[1.0, 0.5], width=base)]))
        stroke = ink.Stroke.model_validate({**nm.curve_stroke(note.curves[0], frame()), "id": "n0"})
        widths = [w for _x, _y, w in ink.stroke_polyline(stroke)]
        assert max(widths) == pytest.approx(100, abs=0.2) and min(widths) == pytest.approx(50, abs=0.2)


def test_a_box_s_text_is_cut_at_what_the_editor_saves(monkeypatch):
    monkeypatch.setattr(nm, "MAX_CONTENT", 10)
    left = Counter()
    box = nb.Media(kind="text", x=0, y=0, w=50, h=20, runs=[nb.Run("a long typed text")])
    assert nm.text_box(box, frame(), left)["content"] == "a long typ" and left["long text"] == 1
    formula = nb.Media(kind="math", x=0, y=0, w=50, h=20, latex="x^2 + y^2 = z^2")
    assert len(nm.text_box(formula, frame(), left)["content"]) == 10 and left["long text"] == 2


def test_formatting_runs_are_read_within_the_note_s_budget(monkeypatch):
    arch = fx.Archiver()
    font = arch.dictionary({"NSFontNameAttribute": arch.string("X-Bold")})
    one = arch.dictionary({"subRangeRangeKey": arch.string("{0, 1}"), "subRangeFontKey": font})
    others = [arch.dictionary({"subRangeRangeKey": arch.string(f"{{{i}, 1}}"), "subRangeFontKey": font})
              for i in (1, 2, 3)]
    attributed = arch.dictionary({"stringKey": arch.string("abcd"), "subRangesKey": arch.array([one] * 4 + others)})
    session = arch.dumps(arch.obj("NoteTakingSession", {"richText": arch.obj(
        "FormattedString", {"attributedString": attributed})}))
    read = []
    real = nb._Reader._style
    monkeypatch.setattr(nb._Reader, "_style", lambda self, m: read.append(1) or real(self, m))
    note = note_of(session)  # the run named four times is read once; the array holds 5 runs a 4-unit text can use
    assert len(read) == 2 and [(r.text, r.bold) for r in note.flow] == [("a", True), ("b", True), ("cd", False)]
    monkeypatch.setattr(nb, "MAX_NOTE_RUNS", 1)
    read.clear()
    note = note_of(session)
    assert len(read) == 1 and [(r.text, r.bold) for r in note.flow] == [("a", True), ("bcd", False)]
    assert note.left_out["formatting"] == 1
    assert nb.left_out_reasons(note.left_out) == [
        "The note's typed text has too much formatting: 1 text keeps its words only"]


def test_references_are_counted_before_plistlib_reads_them(monkeypatch):
    from gamma import notability_archive as na
    monkeypatch.setattr(na, "MAX_PLIST_REFS", 10)
    assert load_plist(plistlib.dumps([1] * 10, fmt=plistlib.FMT_BINARY), limit=1 << 20) == [1] * 10
    with pytest.raises(ArchiveError, match="more than 10 references"):
        load_plist(plistlib.dumps([1] * 11, fmt=plistlib.FMT_BINARY), limit=1 << 20)
    with pytest.raises(ArchiveError, match="references"):  # a key and a value each
        load_plist(plistlib.dumps({f"k{i}": i for i in range(6)}, fmt=plistlib.FMT_BINARY), limit=1 << 20)
    long = plistlib.dumps([[1] * 6, [2] * 6], fmt=plistlib.FMT_BINARY)  # 2 + 6 + 6 in all
    with pytest.raises(ArchiveError, match="references"):
        load_plist(long, limit=1 << 20)
    # a count past 14 follows the marker as an int: 0xAF, then 0x10 and one byte
    body = b"bplist00" + b"\xaf\x10\x0b" + b"\x01" * 11 + b"\x10\x01"
    table = len(body)
    data = body + bytes([8, 8 + 3 + 11]) + struct.pack(">6xBBQQQ", 1, 1, 2, 0, table)
    with pytest.raises(ArchiveError, match="more than 10 references"):
        load_plist(data, limit=1 << 20)
    outside = bytearray(plistlib.dumps([1, 2], fmt=plistlib.FMT_BINARY))
    table = struct.unpack(">Q", outside[-8:])[0]
    outside[table] = 0xF0  # the first object's offset, past the end
    with pytest.raises(ArchiveError, match="outside"):
        load_plist(bytes(outside), limit=1 << 20)


def test_the_layout_shapes_and_groups_a_note_may_hold(monkeypatch):
    monkeypatch.setattr(nb, "MAX_NOTE_PAGES", 2)
    assert len(note_of(fx.session(layout=[None, None, None])).layout) == 2  # read no further than the pages

    monkeypatch.setattr(nb, "MAX_SHAPES", 2)
    pieces = fx.path(*((kind, [(x + 5 * kind, 0)]) for x in (0, 10, 20) for kind in (0, 1)))
    note = _shapes_note([("partialshape", fx.shape(outlinePath=pieces))])
    assert len(note.shapes) == 2 and note.left_out["shapes limit"] == 1  # a piece is a shape
    monkeypatch.setattr(nb, "MAX_CURVE_POINTS", 3)
    line = fx.shape(startPt=(0, 0), endPt=(10, 0))
    note = _shapes_note([("line", line), ("polygon", fx.shape(points=[(0, 0), (5, 0), (5, 5), (0, 5)])),
                         ("line", line), ("line", line)])
    assert [s.kind for s in note.shapes] == ["line"]  # the polygon of 4 points is past a curve's 3
    assert note.left_out["stroke"] == 1 and note.left_out["shapes limit"] == 2
    assert "Past 100,000 shapes the rest is left out (2 shapes)" in nb.left_out_reasons(note.left_out)

    monkeypatch.setattr(nb, "MAX_GROUP_ARCHIVES", 3)  # a group's plist and its archive each count
    groups = [{"transform": [1, 0, 0, 1, 0, 0], "index": 0, "curves": [fx.curve([(10, 20)])]} for _ in range(3)]
    note = note_of(fx.session(groups=groups))
    assert len(note.curves) == 1 and note.left_out["group"] == 2
