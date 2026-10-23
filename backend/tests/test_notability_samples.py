"""The Notability import on real notes (docs/dev/import_export.md
"Notability notes"): every ``.note`` of the samples imports whole, nothing
in it left out for want of reading it, and every ``.ntb`` is refused with
how to export a note instead. The samples are ``samples/notability/``
(its README says where they come from), and the files of the folder
GAMMA_NOTABILITY_SAMPLES names, when it is set. The second half of this
module pins what the import makes of the example note of
Notability-notes-converter, exported by Notability 16 both as "Note" and as
"NTB", against the note's own bytes and its thumbnail, which Notability
drew."""

import contextlib
import functools
import hashlib
import os
import zipfile
from collections import Counter
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from conftest import make_user
from gamma import notability as nb, notability_import as ni, notability_map as nm
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
# What a report says of content the import could not read: the reasons of
# notability.LEFT_OUT's unreadable kinds, the width data's warning, a picture's.
UNREAD = ("could not be read", "does not know", "ends early", "does not match its strokes", "is left out: not a")


def read(path: Path) -> nb.Note:
    """The note of a ``.note`` file, read as the import reads a packed one."""
    with contextlib.ExitStack() as stack:
        entries = []
        walk_zip(stack.enter_context(zipfile.ZipFile(path)), "", entries, new_walk(stack), nested=lambda _path: False)
        members = nb.note_members(entries)
        return nb.read_note(members, members.find("Session.plist").read(), path=path.name)


@functools.cache
def imported(path: Path) -> tuple[str, dict]:
    """``(workspace, report)`` of the file imported into a workspace of its
    own, once for the module."""
    ws = make_user("nbs_" + hashlib.sha256(str(path).encode()).hexdigest()[:12], "nbs-password")
    with open(path, "rb") as f, connect_pages_db(ws) as conn:
        return ws, ni.import_notes(ws, f, conn, name=path.name, actor="nbs")


@pytest.mark.parametrize("path", NOTES, ids=lambda path: path.name)
def test_every_sample_note_imports_whole(path):
    """One page, and no stroke, shape, group or object left out because it
    does not read or is of a kind the import does not know. What the import
    only approximates (a turned text box set upright, a template as plain
    paper) may be warned of."""
    _ws, report = imported(path)
    unread = [w["reason"] for w in report["warnings"] if any(u in w["reason"] for u in UNREAD)]
    assert report["pages_created"] == 1 and not report["skipped"] and not unread, (report["skipped"], unread)


@pytest.mark.parametrize("path", NTBS, ids=lambda path: path.name)
def test_every_sample_ntb_is_refused_with_how_to_export_a_note(path):
    ws = make_user("nbs_ntb", "nbs-password")
    with open(path, "rb") as f, connect_pages_db(ws) as conn, pytest.raises(HTTPException) as refused:
        ni.import_notes(ws, f, conn, name=path.name, actor="nbs")
    assert (refused.value.status_code, refused.value.detail) == (400, nb.NTB_REASON)


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


def test_the_example_imports_as_notability_shows_it(example):
    """A notebook of two ruled letter sheets with its 871 strokes (699
    curves, 170 shapes, 2 stickies) and 8 pictures, the photo cropped as
    Notability crops it, the turned ones stored turned and the GIF as its
    animation, and the text typed on its pages as the page's notes:
    nothing left out."""
    ws, report = imported(EXAMPLE)
    assert (report["pages_created"], report["strokes_imported"], report["pictures_imported"]) == (1, 871, 8)
    assert report["warnings"] == [] and report["skipped"] == []
    page_id = report["pages"][0]["id"]
    with connect_pages_db(ws) as conn:
        blocks = [block_to_dict(row) for row in fetch_subtree(conn, page_id)]
    sheets = sheets_of(blocks, page_id)
    assert [(s["paper"]["width"], s["paper"]["height"], s["paper"]["pattern"], s["paper"]["spacing"]) for s in sheets] == [
        (612, 803.25, "ruled", 18)] * 2
    pictures = [[Image.open(ws_uploads_dir(ws) / b["content"].rsplit("/", 1)[-1][:-1]) for b in s["blocks"]
                 if b["content"].startswith("![")] for s in sheets]
    assert [len(on_sheet) for on_sheet in pictures] == [4, 4]
    photo, *turned = pictures[0]
    assert (photo.format, photo.size) == ("JPEG", (2250, 1550))  # FigureCropRectKey of a 2250 × 3000 photo
    assert all(p.format == "PNG" and p.mode == "RGBA" and p.getpixel((0, 0))[3] == 0 and max(p.size) <= 2048
               for p in turned)
    (moving,) = [p for p in pictures[1] if p.format == "GIF"]
    assert (moving.size, moving.n_frames) == ((352, 240), 18)  # its animation, not the 220 × 150 still frame
    notes = [b["content"] for b in sorted(blocks, key=lambda b: b["position"]) if b["parent_id"] == page_id
             and "sheet" not in b["properties"]]
    assert notes == ["https://github.com/nokcha0/Notability-notes-converter", "DIFFERENT **FONT**",
                     "1. *Large title*", "2. Title 1", "3. Title 2", "4. **Title 3**", "5. Body", "6. Caption"]
