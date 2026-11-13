"""Where a place on a work folder's PDF was typeset (gamma/feedback/sources.py,
docs/dev/feedback.md "SyncTeX"): a position's rects in SyncTeX's frame at
every rotation and under a crop; where the build's SyncTeX file is looked
for and the gates a candidate passes; a tag's path below the work folder and
the file and lines picked; and ``source_for`` on the paper
(samples/synctex/paper) in a work folder of this computer — a passage, the
figure, the equation —, after a rebuild that moves the passage down three
lines, and the places that get no source."""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from conftest import login, make_user
from gamma.db import connect_pages_db
from gamma.feedback import sources, synctex
from test_vault_routes import _page_of, _props, _put, _sync, _vault, disk  # noqa: F401: the disk fixture

SAMPLES = Path(__file__).parent / "samples" / "synctex"
PAPER = SAMPLES / "paper"
PLACES = json.loads((PAPER / "places.json").read_text(encoding="utf-8"))
LETTER = (0.0, 0.0, 612.0, 792.0)
RECT = (100, 72, 300, 92)
THEN = 1_700_000_000
OWNER = "fbs_owner"


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


# --- the frame -------------------------------------------------------------------------------------

def _pos(width, height, *rects) -> dict:
    return {"pageNumber": 1, "width": width, "height": height,
            "rects": [dict(zip(("x1", "y1", "x2", "y2"), r)) for r in rects]}


@pytest.mark.parametrize("rotation, size, want", [
    (0, (612, 792), (100, 72, 300, 92)),
    (90, (792, 612), (72, 492, 92, 692)),
    (180, (612, 792), (312, 700, 512, 720)),
    (270, (792, 612), (520, 100, 540, 300)),
])
def test_tex_rects_at_every_rotation(rotation, size, want):
    assert sources.tex_rects(_pos(*size, RECT), LETTER, rotation, LETTER) == [pytest.approx(want)]


def test_tex_rects_under_a_crop_and_a_media_box_off_the_origin():
    crop = (50.0, 40.0, 562.0, 752.0)
    assert sources.tex_rects(_pos(512, 712, RECT), crop, 0, LETTER) == [pytest.approx((150, 112, 350, 132))]
    media = (-10.0, -20.0, 602.0, 772.0)
    assert sources.tex_rects(_pos(612, 792, RECT), media, 0, media) == [pytest.approx(RECT)]
    assert sources.tex_rects(_pos(612, 792, RECT, (10, 10, 20, 20)), LETTER, 0, LETTER)[1] == pytest.approx(
        (10, 10, 20, 20))
    assert sources.tex_rects({"pageNumber": 4, "rects": [dict(zip(("x1", "y1", "x2", "y2"), RECT))]},
                             LETTER, 0, LETTER) == []
    assert sources.tex_rects({**_pos(612, 792), "rects": [{"x1": 1}]}, LETTER, 0, LETTER) == []


# --- where the SyncTeX file is ----------------------------------------------------------------------

def _build(where: Path, pdf: str = "main.pdf", stx: str = "main.synctex.gz", at: float = THEN) -> tuple:
    """v1's PDF and SyncTeX file as ``where/pdf`` and ``where/stx`` (the
    ones named), written at ``at``: their paths."""
    where.mkdir(parents=True, exist_ok=True)
    out = []
    for name, source in ((pdf, PAPER / "v1" / "main.pdf"), (stx, PAPER / "v1" / "main.synctex.gz")):
        if name:
            shutil.copyfile(source, where / name)
            os.utime(where / name, (at, at))
            out.append(where / name)
    return tuple(out)


def _stamp(path: Path, at: float) -> Path:
    os.utime(path, (at, at))
    return path


def test_find_takes_the_file_beside_the_pdf_the_newer_of_plain_and_gzipped(tmp_path):
    pdf, stx = _build(tmp_path / "a")
    assert sources.find(pdf) == stx
    plain = tmp_path / "a" / "main.synctex"
    plain.write_bytes(synctex._text(stx.read_bytes()))
    _stamp(plain, THEN + 10)
    assert sources.find(pdf) == plain
    _stamp(stx, THEN + 20)
    assert sources.find(pdf) == stx
    assert sources.find(tmp_path / "a" / "none.pdf") is None
    (tmp_path / "a" / "bad.pdf").write_bytes(b"%PDF-1.4 not a pdf")
    assert sources.find(tmp_path / "a" / "bad.pdf") is None


def test_find_looks_in_the_build_directory_a_latexmkrc_names_inside_the_work_folder(tmp_path):
    pdf, = _build(tmp_path / "b", stx="")
    _build(tmp_path / "b" / "made", pdf="")
    assert sources.find(pdf) is None
    (tmp_path / "b" / "latexmkrc").write_text("$out_dir = 'made';\n", encoding="utf-8")
    assert sources.find(pdf) == tmp_path / "b" / "made" / "main.synctex.gz"
    # one at the work folder's root, relative to it; never outside the work folder
    root = tmp_path / "w"
    deep, = _build(root / "paper", stx="")
    _build(root / "paper" / "aux_out", pdf="")
    _build(tmp_path / "elsewhere", pdf="")
    (root / ".latexmkrc").write_text('$aux_dir = "paper/aux_out";\n', encoding="utf-8")
    assert sources.find(deep, root) == root / "paper" / "aux_out" / "main.synctex.gz"
    (root / ".latexmkrc").write_text("$aux_dir = '../elsewhere';\n", encoding="utf-8")
    assert sources.find(deep, root) is None


def test_find_takes_the_one_file_of_a_build_directory_for_a_pdf_copied_under_another_name(tmp_path):
    pdf, = _build(tmp_path / "c", pdf="paper.pdf", stx="")
    _build(tmp_path / "c" / "build", pdf="")
    assert sources.find(pdf) == tmp_path / "c" / "build" / "main.synctex.gz"
    _build(tmp_path / "c" / "build", pdf="", stx="other.synctex.gz")
    assert sources.find(pdf) is None                               # two that pass: no guess


def test_the_gates(tmp_path):
    # as many sheets as the PDF has pages
    figure = tmp_path / "d" / "fig1.pdf"
    figure.parent.mkdir()
    shutil.copyfile(PAPER / "v1" / "figures" / "fig1.pdf", figure)
    _build(tmp_path / "d", pdf="", stx="fig1.synctex.gz")
    _stamp(figure, THEN)
    assert sources.find(figure) is None
    # written within MAX_SKEW_S of the PDF
    pdf, stx = _build(tmp_path / "e")
    _stamp(stx, THEN + 600)
    assert sources.find(pdf) is None
    _stamp(stx, THEN + 100)
    assert sources.find(pdf) == stx
    # no newer (busy) file: a build is running
    busy, = _build(tmp_path / "e", pdf="", stx="main.synctex.gz(busy)", at=THEN + 110)
    assert sources.find(pdf) is None
    _stamp(busy, THEN)
    assert sources.find(pdf) == stx


# --- from tags to the source -------------------------------------------------------------------------

def test_a_tags_path_is_taken_below_the_work_folder(tmp_path):
    root = tmp_path / "W"
    (root / "sections").mkdir(parents=True)
    (root / "main.tex").write_text("x", encoding="utf-8")
    (root / "sections" / "method.tex").write_text("x", encoding="utf-8")
    assert sources.project_path(str(root / "main.tex"), root, root) == "main.tex"
    assert sources.project_path(str(root / "sections" / ".." / "main.tex"), root, root) == "main.tex"
    # a build made elsewhere: the longest tail that is a file here
    assert sources.project_path("/work/paper/./sections/method.tex", root, root) == "sections/method.tex"
    assert sources.project_path("/work/paper/./main.tex", root, root / "paper" / "out") == "main.tex"
    assert sources.project_path("C:/other/checkout/sections/method.tex", root, root) == "sections/method.tex"
    # TeX's own files, a name no file here ends in, and nothing
    assert sources.project_path("/texlive/2024/texmf-dist/tex/latex/base/article.cls", root, root) is None
    assert sources.project_path("/work/paper/./sections/results.tex", root, root) is None
    assert sources.project_path("../nothing.tex", root, root) is None
    assert sources.project_path("", root, root) is None


def test_the_source_file_with_the_largest_share_and_its_lines(tmp_path):
    root = tmp_path / "W"
    root.mkdir()
    for name in ("main.tex", "chapter.tex", "main.aux"):
        (root / name).write_text("x", encoding="utf-8")
    syn = synctex.load(SAMPLES / "edges" / "input_inside.synctex")     # 1 main.tex, 2 chapter.tex, 3 main.aux
    pick = sources._pick
    assert pick(syn, [(1, 3, 0.6), (1, 5, 0.4)], root, root) == {"file": "main.tex", "line": 3, "end": 5}
    assert pick(syn, [(1, 3, 0.96), (1, 9, 0.04)], root, root) == {"file": "main.tex", "line": 3}
    assert pick(syn, [(2, 4, 0.3), (3, 2, 0.7)], root, root) == {"file": "chapter.tex", "line": 4}  # a source first
    assert pick(syn, [(3, 2, 1.0)], root, root) == {"file": "main.aux", "line": 2}
    assert pick(syn, [(7, 2, 1.0)], root, root) is None
    # two tags for one file are one file
    two = synctex.load(SAMPLES / "edges" / "two_tags.synctex")
    assert pick(two, [(2, 5, 0.6), (1, 3, 0.4)], root, root) == {"file": "main.tex", "line": 3, "end": 5}


# --- a place on the paper in a work folder -------------------------------------------------------------

def _paper(root: Path, version: str) -> None:
    for rel in ("main.pdf", "main.synctex.gz", "main.tex", "sections/method.tex", "figures/fig1.pdf"):
        _put(root / rel, (PAPER / version / rel).read_bytes())


def _place(kind: str, rects, page: int = 1, **more) -> dict:
    return {"kind": kind, "page": page, "rects": rects, **more}


def test_a_place_on_the_papers_pdf_names_its_source_and_follows_a_rebuild(owner, disk):
    root = disk / "Paper"
    _paper(root, "v1")
    link = _vault(owner, root)
    ws, page = link["workspace_id"], _page_of(link, "main.pdf")
    props = _props(link, page)
    assert props["doc"]["at"] == "path" and props["work_folder"] == link["folder_id"]
    v1, v2 = PLACES["v1"], PLACES["v2"]
    with connect_pages_db(ws) as conn:
        assert sources.source_for(ws, conn, page, props, _place("passage", v1["passage"]["rects"])) == {
            "file": "sections/method.tex", "line": 3, "end": 5}
        assert sources.source_for(ws, conn, page, props, _place("area", [v1["figure"]["rect"]])) == {
            "file": "main.tex", "line": 22}                                         # its \includegraphics
        assert sources.source_for(ws, conn, page, props, _place("area", [v1["equation"]["rect"]])) == {
            "file": "sections/method.tex", "line": 11}                              # its \end{equation}
        # measured on another file: never mapped against this build; a page past the end; no rects
        assert sources.source_for(ws, conn, page, props, _place("passage", v1["passage"]["rects"],
                                                                doc_id="b" * 24)) is None
        assert sources.source_for(ws, conn, page, props, _place("passage", v1["passage"]["rects"], page=3)) is None
        assert sources.source_for(ws, conn, page, props, _place("note", [])) is None
        # the figure's own file was built by nothing
        figure = _page_of(link, "figures/fig1.pdf")
        assert sources.source_for(ws, conn, figure, _props(link, figure), _place("area", [[0.1, 0.1, 0.9, 0.9]])) is None
    # v2 inserts three lines above the passage; the round takes the new build
    _paper(root, "v2")
    assert _sync(owner, link["id"])["last_error"] == ""
    before, props = props, _props(link, page)
    assert props["doc"]["hash"] != before["doc"]["hash"]
    with connect_pages_db(ws) as conn:
        assert sources.source_for(ws, conn, page, props, _place("passage", v2["passage"]["rects"])) == {
            "file": "sections/method.tex", "line": 6, "end": 8}
        assert sources.source_for(ws, conn, page, props, _place("area", [v2["equation"]["rect"]])) == {
            "file": "sections/method.tex", "line": 14}
        assert sources.source_for(ws, conn, page, props, _place("passage", v1["passage"]["rects"],
                                                                doc_id=before["doc"]["hash"])) is None
        # the disk moved on since Gamma read it: nothing until the next round takes the build
        now = time.time()
        os.utime(root / "main.pdf", (now, now))
        assert sources.source_for(ws, conn, page, props, _place("passage", v2["passage"]["rects"])) is None
        # a page that is no work folder's file
        assert sources.source_for(ws, conn, page, {**props, "work_folder": ""}, _place("passage", v2["passage"]["rects"])) is None
