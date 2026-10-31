"""The Notability import on real notes (docs/dev/import_export.md
"Notability notes"): every ``.note`` and ``.ntb`` of the samples imports
whole, nothing in it left out for want of reading it. The samples are
``samples/notability/`` (its README says where they come from), and the
files of the folder GAMMA_NOTABILITY_SAMPLES names, when it is set. The
second half of this module pins what the import makes of the example note
of Notability-notes-converter, exported by Notability 16 both as "Note"
and as "NTB": its Note export against the note's own bytes and its
thumbnail, which Notability drew, and its NTB export against its Note
export."""

import contextlib
import functools
import hashlib
import io
import math
import os
import zipfile
from collections import Counter
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from conftest import make_user
from gamma import ink, notability as nb, notability_import as ni, notability_map as nm, notability_ntb as ntb
from gamma import notebook, picture
from gamma.blocks_store import block_to_dict, fetch_subtree
from gamma.db import connect_pages_db, ws_uploads_dir
from gamma.import_zip import new_walk, walk_zip
from gamma.notebook import sheets_of

SAMPLES = Path(__file__).resolve().parent / "samples" / "notability"
EXTRA = os.environ.get("GAMMA_NOTABILITY_SAMPLES")  # a folder of more notes to import
FOLDERS = [SAMPLES] + ([Path(EXTRA)] if EXTRA else [])
NOTES = sorted(path for folder in FOLDERS for path in folder.rglob("*.note"))
NTBS = sorted(path for folder in FOLDERS for path in folder.rglob("*.ntb"))
EXAMPLE = SAMPLES / "NOTE Note Apr 26, 2026.note"
EXAMPLE_NTB = SAMPLES / "NTB Note Apr 26, 2026.ntb"
# What a report says of content the import could not read: the reasons of
# notability.LEFT_OUT's unreadable kinds, the width data's warning, a picture's.
UNREAD = ("could not be read", "does not know", "ends early", "does not match its strokes", "is left out: not a")


def read(path: Path) -> nb.Note:
    """The note of a ``.note`` or ``.ntb`` file, read as the import reads a
    packed one."""
    with contextlib.ExitStack() as stack:
        entries = []
        walk_zip(stack.enter_context(zipfile.ZipFile(path)), "", entries, new_walk(stack), nested=lambda _path: False)
        members = nb.note_members(entries)
        if members.find("Session.plist") is not None:
            return nb.read_note(members, members.find("Session.plist").read(), path=path.name)
        return ntb.read_ntb(members, members.find("noteBundle").read(), path=path.name)


@functools.cache
def imported(path: Path) -> tuple[str, dict]:
    """``(workspace, report)`` of the file imported into a workspace of its
    own, once for the module."""
    ws = make_user("nbs_" + hashlib.sha256(str(path).encode()).hexdigest()[:12], "nbs-password")
    with open(path, "rb") as f, connect_pages_db(ws) as conn:
        return ws, ni.import_notes(ws, f, conn, name=path.name, actor="nbs")


@pytest.mark.parametrize("path", NOTES + NTBS, ids=lambda path: path.name)
def test_every_sample_imports_whole(path):
    """One page, and no stroke, shape, group or object left out because it
    does not read or is of a kind the import does not know. What the import
    only approximates (a turned text box set upright, a template as plain
    paper) may be warned of."""
    _ws, report = imported(path)
    unread = [w["reason"] for w in report["warnings"] if any(u in w["reason"] for u in UNREAD)]
    assert report["pages_created"] == 1 and not report["skipped"] and not unread, (report["skipped"], unread)


# --- the converter's example note ------------------------------------------------------

@pytest.fixture(scope="module")
def example() -> nb.Note:
    return read(EXAMPLE)


def test_the_example_s_strokes_keep_their_tools(example):
    """Its own hash has 596 curves in 149 strokes, the eraser having cut
    some into pieces that share a curveUUID, and 149 styles. Read by
    stroke, its highlighters are its translucent curves, and page 1's only
    dashed and dotted strokes are the green pen's and the blue
    highlighter's at its top, as its thumbnail shows them. Read by curve,
    pieces of the orange scribble below them were dashed, and page 2's
    pencil scribbles were pens."""
    note = example
    assert all((c.style == nb.STYLE_HIGHLIGHTER) == (c.rgba[3] < 255) for c in note.curves)
    assert Counter(c.style for c in note.curves) == {nb.STYLE_PEN: 647, nb.STYLE_PENCIL: 35, nb.STYLE_HIGHLIGHTER: 17}
    first = nm.plan_note(note, {}).pages[0]
    dashed = [note.curves[i] for i in first.curves if note.curves[i].dash]
    assert {c.rgba for c in dashed} == {(169, 255, 23, 255), (20, 199, 222, 107)}
    assert max(y for c in dashed for _x, y in nb.curve_points(c)) < 120  # of a page 891 units tall


def test_the_example_s_shapes_and_groups_come_in_whole(example):
    """Its 88 shapes, 15 of them partly erased, and its 4 ink groups, two of
    them turned copies holding 35 shape lists each: 170 shapes, each of the
    23 partly erased ones a path per piece the eraser left. Page 1's pieces
    are the seven orange dashes of a line under "Say Hi to pumpkin"."""
    note = example
    assert not note.left_out and not note.classes and not note.warnings
    assert (len(note.curves), len(note.shapes), Counter(s.kind for s in note.shapes)["path"]) == (699, 170, 35)
    plan = nm.plan_note(note, {})
    pieces = [note.shapes[i] for i in plan.pages[0].shapes if note.shapes[i].kind == "path"]
    assert len(pieces) == 7 and {tuple(round(v * 255) for v in s.rgba) for s in pieces} == {(250, 157, 0, 255)}
    assert {round(y) for s in pieces for _x, y in s.points} == {389}
    left = Counter()
    assert sum(1 for page in plan.pages for _stroke in nm.page_strokes(note, page, left)) == 871 and not left


def pictures_of(ws: str, blocks: list[dict]) -> list[tuple[dict, Image.Image]]:
    """The picture blocks among ``blocks``: each one's picture and its
    stored file, opened."""
    found = [b["properties"]["picture"] for b in blocks if b["properties"].get("picture")]
    return [(p, Image.open(ws_uploads_dir(ws) / p["url"].rsplit("/", 1)[-1])) for p in found]


def test_the_example_imports_as_notability_shows_it(example):
    """A notebook of two ruled letter sheets with its 871 strokes (699
    curves, 170 shapes, 2 stickies) and 8 pictures, each its file as it
    came with its crop, turn and flips as numbers: the photo whole with the
    crop Notability shows, the turned ones upright with their angles, the
    GIF as its animation; and the text typed on its pages as the page's
    notes: nothing left out."""
    ws, report = imported(EXAMPLE)
    assert (report["pages_created"], report["strokes_imported"], report["pictures_imported"]) == (1, 871, 8)
    assert report["warnings"] == [] and report["skipped"] == []
    page_id = report["pages"][0]["id"]
    with connect_pages_db(ws) as conn:
        blocks = [block_to_dict(row) for row in fetch_subtree(conn, page_id)]
    sheets = sheets_of(blocks, page_id)
    assert [(s["paper"]["width"], s["paper"]["height"], s["paper"]["pattern"], s["paper"]["spacing"]) for s in sheets] == [
        (612, 803.25, "ruled", 18)] * 2
    pictures = [pictures_of(ws, s["blocks"]) for s in sheets]
    assert [len(on_sheet) for on_sheet in pictures] == [4, 4]
    (photo, image), *turned = pictures[0]
    assert (image.format, image.size) == ("JPEG", (2250, 3000))  # the photo, not the part Notability shows
    assert (photo["crop"], photo["angle"]) == ({"x": 0, "y": 0.2658, "w": 1, "h": 0.5168}, 0)  # its FigureCropRectKey
    assert [(p["angle"], p["flip_h"], p["flip_v"], i.format, i.size) for p, i in turned] == [
        (16.22, False, True, "JPEG", (2250, 3000)), (53.15, True, False, "JPEG", (2250, 3000)),
        (-31.41, False, False, "JPEG", (2250, 3000))]
    assert [(p["angle"], i.format, i.size) for p, i in pictures[1]] == [
        (19.2, "PNG", (1877, 3000)), (0, "PNG", (1317, 1298)), (0, "PNG", (1800, 1800)), (0, "GIF", (352, 240))]
    gif, moving = pictures[1][3]
    assert moving.n_frames == 18 and gif["crop"] == {"x": 0, "y": 0, "w": 1, "h": 1}  # its animation, its crop whole
    assert all(p["w"] < 612 and p["x"] + p["w"] <= 612 and p["y"] + p["h"] <= 803.25 for s in pictures for p, _i in s)
    notes = [b["content"] for b in sorted(blocks, key=lambda b: b["position"]) if b["parent_id"] == page_id
             and "sheet" not in b["properties"]]
    assert notes == ["https://github.com/nokcha0/Notability-notes-converter", "DIFFERENT **FONT**",
                     "1. *Large title*", "2. Title 1", "3. Title 2", "4. **Title 3**", "5. Body", "6. Caption"]


def test_the_example_s_pictures_lie_where_notability_drew_them():
    """Sheet 1's pictures as imported and as Gamma draws them
    (``notebook.notebook_pdf``, rendered by MuPDF at the scale of the
    note's thumb12x.png, which Notability drew) match that thumbnail
    inside each one's visible area (its turned frame less the frames drawn
    over it, 3 px in from its edges): the crop, the flips about the frame's
    own axes, then the turn, a higher zIndex on top. The other order, the
    turn and then the flips about the page's axes, which is the turn the
    other way round (F·R(θ) = R(−θ)·F), lies far off on both flipped
    pictures."""
    ws, report = imported(EXAMPLE)
    page_id = report["pages"][0]["id"]
    with connect_pages_db(ws) as conn:
        sheet = sheets_of([block_to_dict(row) for row in fetch_subtree(conn, page_id)], page_id)[0]
    pictures = [b["properties"]["picture"] for b in sheet["blocks"] if b["properties"].get("picture")]
    with zipfile.ZipFile(EXAMPLE) as zf:
        drawn = Image.open(io.BytesIO(zf.read("TEST Note Apr 26, 2026/thumb12x.png"))).convert("RGBA")
    thumb = Image.new("RGBA", drawn.size, "white")
    thumb.alpha_composite(drawn)
    thumb = thumb.convert("RGB")
    k = thumb.width / sheet["paper"]["width"]
    paper = notebook.normalize_paper({"width": sheet["paper"]["width"], "height": sheet["paper"]["height"]})

    def render(pics: list[dict]) -> Image.Image:
        pdf = notebook.notebook_pdf([(paper, [], [])], pictures=[pics], uploads_dir=ws_uploads_dir(ws))
        pix = pymupdf.open(stream=pdf, filetype="pdf")[0].get_pixmap(matrix=pymupdf.Matrix(k, k), alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    def frame(pic: dict) -> Image.Image:
        mask = Image.new("L", thumb.size, 0)
        ImageDraw.Draw(mask).polygon([(x * k, y * k) for x, y in picture.turned_corners(pic)], fill=255)
        return mask

    visible = []
    for i, pic in enumerate(pictures):
        mask = frame(pic)
        for above in pictures[i + 1:]:
            mask = ImageChops.subtract(mask, frame(above))
        visible.append(mask.filter(ImageFilter.MinFilter(7)))

    def error(image: Image.Image, i: int) -> float:
        return sum(ImageStat.Stat(ImageChops.difference(image, thumb), visible[i]).mean) / 3

    shown = render(pictures)
    right = [error(shown, i) for i in range(len(pictures))]
    assert len(pictures) == 4 and max(right) < 10, right
    flipped = [i for i, pic in enumerate(pictures) if pic["flip_h"] or pic["flip_v"]]
    assert len(flipped) == 2
    for i in flipped:
        wrong = error(render([{**pic, "angle": -pic["angle"]} if j == i else pic for j, pic in enumerate(pictures)]), i)
        assert wrong > max(25, 4 * right[i]), (i, right[i], wrong)


# --- its NTB export against its Note export --------------------------------------------

def strokes_of(note: nb.Note) -> list[list[tuple]]:
    """Each page's strokes as the import makes them: what each looks like
    (colour, tool, brush, dash, size) and the centre of its samples' box,
    in points."""
    pages = []
    for page in nm.plan_note(note, {}).pages:
        found = []
        for stroke in nm.page_strokes(note, page, Counter()):
            samples = ink.decode_stroke(ink.Stroke.model_validate({**stroke, "id": "n0"}))
            xs, ys = [s["x"] for s in samples], [s["y"] for s in samples]
            look = (stroke["color"], stroke.get("tool"), stroke.get("brush"), stroke.get("dash"), round(stroke["size"], 1))
            found.append((look, ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)))
        pages.append(found)
    return pages


def notes_of(blocks: list[dict]) -> list[str]:
    return [line for b in blocks for line in [b["content"], *notes_of(b["children"])]]


def test_the_example_s_ntb_reads_as_its_note_does(example):
    """The same note exported both ways reads alike: each page's strokes
    look the same and lie within a point of each other (the bundle's half
    floats aside), the partly erased shapes the .note keeps as such and the
    .ntb as ink the eraser cut among them; its typed text is the same notes,
    but for a bold the .ntb keeps where the .note names its font alone
    (Impact)."""
    twin = read(EXAMPLE_NTB)
    for ours, theirs in zip(strokes_of(twin), strokes_of(example), strict=True):
        assert len(ours) == len(theirs)
        left: dict = {}
        for look, centre in theirs:
            left.setdefault(look, []).append(centre)
        for look, centre in ours:
            near = min(left.get(look) or [(math.inf, math.inf)], key=lambda c: math.dist(c, centre))
            assert math.dist(near, centre) <= 1.0, (look, centre)
            left[look].remove(near)
    ours, theirs = notes_of(nm.flow_blocks(twin.flow)), notes_of(nm.flow_blocks(example.flow))
    assert len(ours) == len(theirs) and [a for a, b in zip(ours, theirs) if a != b] == ["3. **Title 2**"]
    assert (twin.title, twin.width, twin.paper.lines) == (example.title, example.width, pytest.approx(0.25, abs=1e-6))


def test_the_example_s_ntb_imports_as_its_note_does():
    """Its NTB export becomes the page its Note export does: as many
    strokes, pictures and pages, nothing left out, each picture an image
    of the same kind and size at the same place, size, turn and crop (the
    bundle's floats aside), and the GIF moving, its crop given in the
    asset's logical size the whole GIF. Their flips differ: the bundle holds
    no flipped picture (Notability 16.10 drew both of the Note's flipped
    ones unflipped in its thumbnail.png), though its format has the flips
    (CreateBlock fields 16 and 17), which the .ntb reader reads."""
    ws, report = imported(EXAMPLE_NTB)
    note_ws, note_report = imported(EXAMPLE)
    counts = ("pages_created", "strokes_imported", "pictures_imported", "text_boxes_imported", "recordings_imported")
    assert [report[k] for k in counts] == [note_report[k] for k in counts] == [1, 871, 8, 0, 0]
    assert report["warnings"] == [] and report["skipped"] == []
    pages = []
    for workspace, made in ((ws, report), (note_ws, note_report)):
        with connect_pages_db(workspace) as conn:
            blocks = [block_to_dict(row) for row in fetch_subtree(conn, made["pages"][0]["id"])]
        pages.append([pictures_of(workspace, s["blocks"]) for s in sheets_of(blocks, made["pages"][0]["id"])])
    for ours, theirs in zip(*pages, strict=True):
        assert len(ours) == len(theirs) == 4
        for (mine, image), (other, their_image) in zip(ours, theirs):
            assert (image.format, image.size) == (their_image.format, their_image.size)
            assert all(abs(mine[k] - other[k]) <= 0.05 for k in ("x", "y", "w", "h", "angle")), (mine, other)
            assert all(abs(mine["crop"][k] - other["crop"][k]) <= 0.0002 for k in ("x", "y", "w", "h")), (mine, other)
    ((gif, moving),) = [(p, i) for p, i in pages[0][1] if i.format == "GIF"]
    assert (moving.size, moving.n_frames, gif["crop"]) == ((352, 240), 18, {"x": 0, "y": 0, "w": 1, "h": 1})
