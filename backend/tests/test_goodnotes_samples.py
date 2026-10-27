"""The GoodNotes import on real notebooks (docs/dev/import_export.md
"GoodNotes notebooks"): every ``.goodnotes`` of the samples imports whole,
nothing in it left out for want of reading it. The samples are
``samples/goodnotes/`` (its README says where they come from), and the
files of the folder GAMMA_GOODNOTES_SAMPLES names, when it is set. A
notebook with a ``.pdf`` beside it, named as it is, is held to that file
as GoodNotes' own Editable PDF export of it: its pages and their sizes,
and its strokes against the ``/Ink`` annotations GoodNotes wrote for them.
The last tests pin what the import makes of the vendored notebook."""

import contextlib
import functools
import hashlib
import io
import math
import os
import zipfile
from collections import Counter
from pathlib import Path

import pytest
from PyPDF2 import PdfReader

from conftest import make_user
from gamma import goodnotes as gn, goodnotes_import as gi, goodnotes_map as gm, ink, notability_map as nm
from gamma.blocks_store import block_to_dict, fetch_subtree
from gamma.db import connect_pages_db
from gamma.import_zip import new_walk, walk_zip

SAMPLES = Path(__file__).resolve().parent / "samples" / "goodnotes"
EXTRA = os.environ.get("GAMMA_GOODNOTES_SAMPLES")  # a folder of more notebooks, with their exports beside them
FOLDERS = [SAMPLES] + ([Path(EXTRA)] if EXTRA else [])
NOTEBOOKS = sorted(path for folder in FOLDERS for path in folder.rglob("*.goodnotes"))
EXPORTED = [path for path in NOTEBOOKS if path.with_suffix(".pdf").exists()]
FIXTURE = SAMPLES / "gn-mac-mixed-pens.goodnotes"
# What a report says of content the import could not read.
UNREAD = ("could not be read", "does not know", "is damaged", "Not imported", "Missing")


def planned(path: Path) -> tuple[gn.Notebook, gm.NotebookPlan]:
    """The notebook of a ``.goodnotes`` and its plan, its PDFs opened as the
    import opens them."""
    with contextlib.ExitStack() as stack:
        entries = []
        walk_zip(stack.enter_context(zipfile.ZipFile(path)), "", entries, new_walk(stack), nested=lambda _p: False)
        [bundle] = gn.find_notebooks(entries, path.name)
        notebook = gn.read_notebook(bundle.members, path=bundle.path)
        pages, _readers, _sources, colours, missing = gi.notebook_pdfs(bundle.members, notebook)
        assert not missing
        return notebook, gm.plan_notebook(notebook, pages, colours)


@functools.cache
def imported(path: Path) -> tuple[str, dict]:
    """``(workspace, report)`` of the file imported into a workspace of its
    own, once for the module."""
    ws = make_user("gns_" + hashlib.sha256(str(path).encode()).hexdigest()[:12], "gns-password")
    with open(path, "rb") as f, connect_pages_db(ws) as conn:
        return ws, gi.import_goodnotes(ws, f, conn, name=path.name, actor="gns")


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda path: path.name)
def test_every_sample_imports_whole(path):
    _ws, report = imported(path)
    unread = [w["reason"] for w in report["warnings"] if any(u in w["reason"] for u in UNREAD)]
    assert report["pages_created"] == 1 and not report["skipped"] and not unread, (report["skipped"], unread)


# --- against GoodNotes' own export ------------------------------------------------------

def _annotations(page) -> list[tuple]:
    """A PDF page's /Ink annotations as ``(bounds of its /InkList, /BS /W,
    /Rect)``, in points from the page's top-left."""
    height = float(page.mediabox[3]) - float(page.mediabox[1])
    raw = page.get("/Annots")
    out = []
    for annot in raw.get_object() if raw is not None else []:
        annot = annot.get_object()
        if annot.get("/Subtype") != "/Ink":
            continue
        values = [float(v) for path in annot["/InkList"] for v in path]
        xs, ys = values[0::2], [height - y for y in values[1::2]]
        border = annot.get("/BS")
        width = float(border.get_object().get("/W", 1)) if border is not None else 1.0
        r = [float(v) for v in annot["/Rect"]]
        rect = (min(r[0], r[2]), height - max(r[1], r[3]), max(r[0], r[2]), height - min(r[1], r[3]))
        out.append(((min(xs), min(ys), max(xs), max(ys)), width, rect))
    return out


def _bounds(stroke: dict, grown: bool = False) -> tuple:
    """A stroke's samples' bounds, or with ``grown`` its drawn extent."""
    parsed = ink.Stroke.model_validate({**stroke, "id": "n0"})
    samples = ink.decode_stroke(parsed)
    half = [ink.stroke_width(parsed, s) / 2 if grown else 0 for s in samples]
    return (min(s["x"] - h for s, h in zip(samples, half)), min(s["y"] - h for s, h in zip(samples, half)),
            max(s["x"] + h for s, h in zip(samples, half)), max(s["y"] + h for s, h in zip(samples, half)))


@pytest.mark.parametrize("path", EXPORTED, ids=lambda path: path.name)
def test_a_sample_reads_as_goodnotes_exported_it(path):
    """Its pages are the export's, at their sizes. Each highlighter and
    ball pen stroke has an annotation of its width, its samples' bounds
    within 2 pt of that annotation's (the export draws a quadratic as its
    cubic, the import samples it), tape aside (the export fills it); so has
    each straight shape (a curved line or an ellipse is listed by its
    anchors alone). Each piece of a variable-width stroke lies, as drawn,
    within 0.5 pt inside the rect of its record's annotation, most of them
    exactly on it."""
    notebook, plan = planned(path)
    export = PdfReader(str(path.with_suffix(".pdf")), strict=False)
    assert [(round(p.frame.width, 2), round(p.frame.height, 2)) for p in plan.pages] == [
        (round(float(p.mediabox[2]) - float(p.mediabox[0]), 2), round(float(p.mediabox[3]) - float(p.mediabox[1]), 2))
        for p in export.pages]
    exact, ribbons = 0, 0
    for page, exported in zip(plan.pages, export.pages):
        theirs = _annotations(exported)
        for shape in page.page.shapes:
            stroke = nm.shape_stroke(shape, page.frame)
            if not theirs or shape.control is not None or shape.kind == "ellipse":
                continue
            box = _bounds(stroke)
            near = min(theirs, key=lambda t: sum(abs(a - b) for a, b in zip(box, t[0])))
            assert max(abs(a - b) for a, b in zip(box, near[0])) <= 2.0, (path.name, shape.kind, box, near)
            assert abs(stroke["size"] - near[1]) < 0.01, (path.name, shape.kind, stroke["size"], near[1])
        for item in page.page.ink:
            stroke = gm.ink_stroke(item, page.frame)
            assert stroke is not None
            if not theirs:
                continue  # a page the export flattened
            if item.kind == "flat":
                box = _bounds(stroke)
                near = min(theirs, key=lambda t: sum(abs(a - b) for a, b in zip(box, t[0])))
                assert max(abs(a - b) for a, b in zip(box, near[0])) <= 2.0, (path.name, box, near)
                assert abs(stroke["size"] - near[1]) < 0.01, (path.name, stroke["size"], near[1])
            elif item.kind == "ribbon" and not item.tape:
                box = _bounds(stroke, grown=True)
                inside = [t for t in theirs if all(box[i] >= t[2][i] - 0.5 for i in (0, 1))
                          and all(box[i] <= t[2][i] + 0.5 for i in (2, 3))]
                assert inside, (path.name, box)
                ribbons += 1
                exact += any(max(abs(a - b) for a, b in zip(box, t[2])) <= 0.5 for t in inside)
    assert exact >= 0.9 * ribbons


# --- the vendored notebook --------------------------------------------------------------------

def test_the_fixture_becomes_a_notebook_on_its_paper():
    notebook, plan = planned(FIXTURE)
    assert (notebook.title, plan.kind, len(plan.pages)) == ("Untitled Notebook", "notebook", 1)
    assert plan.papers == [{"width": 455.04, "height": 588.45, "color": "#f8f7e9", "pattern": "grid", "spacing": 16,
                            "line": "#d0d2d3"}]
    assert notebook.left_out == Counter({"tape": 1})


def test_the_fixture_s_strokes_keep_their_pens():
    """Two variable-width strokes keep their widths, the tape is an opaque
    band, the highlighter keeps its alpha, the ball pen and the pencil
    drawn with a mouse (no force) are even, and the recognised ellipse is
    one, with its translucent fill."""
    _notebook, plan = planned(FIXTURE)
    strokes = list(gm.page_strokes(plan.pages[0], Counter()))
    looks = [(s.get("tool", "pen"), s.get("brush"), s.get("pen", True), s["ch"], s["size"], s.get("opacity"))
             for s in strokes]
    assert looks == [("pen", "monoline", False, "xy", 19.636, None),
                     ("pen", None, True, "xyw", 1.275, None),
                     ("highlighter", None, True, "xy", 12.0, 0.5),
                     ("pen", "monoline", False, "xy", 0.78, None),
                     ("pen", None, False, "xy", 0.78, None),
                     ("pen", None, True, "xyw", 2.697, None),
                     ("pen", "pencil", False, "xy", 0.78, None)]
    ellipse = strokes[4]
    assert ellipse["shape"] == {"kind": "ellipse", "params": {"cx": 143.1, "cy": 398.51, "rx": 87.66, "ry": 55.98,
                                                              "angle": 18.34}}
    assert ellipse["fill"] == {"color": "#1e1b1b", "opacity": 0.1}


def test_the_fixture_imports_as_a_sheet_with_its_ink():
    ws, report = imported(FIXTURE)
    assert [report[k] for k in ("pages_created", "strokes_imported", "pictures_imported", "text_boxes_imported")] == [
        1, 7, 0, 0]
    assert [w["reason"] for w in report["warnings"]] == [
        "1 strip of tape is opaque ink: it no longer hides and reveals"]
    with connect_pages_db(ws) as conn:
        blocks = [block_to_dict(row) for row in fetch_subtree(conn, report["pages"][0]["id"])]
    sheets = [b for b in blocks if (b.get("properties") or {}).get("sheet")]
    assert len(sheets) == 1 and sheets[0]["properties"]["sheet"]["pattern"] == "grid"
    assert report["pages"][0]["kind"] == "page" and not report["pages"][0]["missing"]
    assert math.isclose(sheets[0]["properties"]["sheet"]["width"], 455.04)


def test_the_fixture_imported_again_is_skipped():
    ws, report = imported(FIXTURE)
    with open(FIXTURE, "rb") as f, connect_pages_db(ws) as conn:
        again = gi.import_goodnotes(ws, io.BytesIO(f.read()), conn, name=FIXTURE.name, actor="gns")
    assert (again["pages_created"], again["pages_skipped"]) == (0, 1)
    assert again["pages"][0]["id"] == report["pages"][0]["id"] and again["pages"][0]["action"] == "skip"
