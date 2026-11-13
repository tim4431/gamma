"""Where in the sources a place on a work folder's PDF was typeset: a
feedback entry's ``source`` (docs/dev/feedback.md "SyncTeX"), read from the
build's SyncTeX file (gamma/feedback/synctex.py) on this computer.

``source_for`` maps a place on the page's current PDF (an anchor, or the
block's position now: its kind, page and rects as fractions of the page as
shown) to ``{"file", "line", "end"?}``: the source file below the work
folder's directory, with ``/``, its first line and, over several, its last.

- **The PDF** is the page's document on this computer, the version Gamma
  shows (``storage.linked_path``: its size and time are the recorded
  ones). Another server, a rebuild Gamma has not taken yet, a file gone:
  no source. A place measured on another file (its ``doc_id``) is never
  mapped against this one.
- **The SyncTeX file** (``find``) is the first of ``<stem>.synctex.gz`` and
  ``<stem>.synctex`` beside it (the newer first), then in the build
  directories beside it (``BUILD_DIRS``) and the ``$out_dir`` / ``$aux_dir``
  a ``latexmkrc`` or ``.latexmkrc`` beside it or at the work folder's root
  names, then the one ``*.synctex.gz`` of those directories (a PDF copied
  under another name), never a walk, that passes the gates: as many sheets
  as the PDF has pages, written within ``MAX_SKEW_S`` of the PDF, and no
  newer ``(busy)`` file beside it (a build running). It is read, never
  stored.
- **The rects** are put in SyncTeX's frame (``tex_rects``: bp from the
  media box's top-left, y down, through the page's view box and
  rotation), and a passage is asked as text (``synctex.SyncTeX.query``).
- **Each tag's path** is taken below the work folder's directory, else by
  the longest tail of its parts that is a file there (a build made in a
  container or another checkout), else dropped (TeX's own files). The
  source file with the largest share wins, a generated one (``GENERATED``:
  a table of contents, a bibliography) only when there is none, and its
  lines with at least ``LINE_SHARE`` of its share give ``line`` and ``end``.

The answers are kept by the files' stats and the place (``MEMO`` of them),
so a pass that reads the same entries again costs a few stats until the
next build.
"""

import json
import logging
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path, PurePath

from pypdf import PdfReader

from .. import folder_links, highlights, pdf_export, storage
from ..documents.model import is_pdf, page_document
from ..work_folders.library import work_folder_of
from . import synctex

logger = logging.getLogger(__name__)

BUILD_DIRS = ("build", "out", "output", "_build", ".build", "latex.out", "tex.out")
LATEXMKRC = ("latexmkrc", ".latexmkrc")
GENERATED = frozenset((".aux", ".toc", ".lof", ".lot", ".bbl", ".ind", ".gls", ".nav", ".snm", ".vrb", ".out"))
MAX_SKEW_S = 120          # seconds between the PDF's and the SyncTeX file's times
LINE_SHARE = 0.05         # of the file's share, for a line to count
MEMO = 2000               # answers kept
PDFS = 8                  # PDFs whose page frames are kept
MAX_RC = 64 * 1024        # bytes of a latexmkrc read
_DIR_RC = re.compile(r"""\$(out|aux)_dir\s*=\s*['"]([^'"]+)['"]""")

_lock = threading.Lock()
_memo: OrderedDict = OrderedDict()
_frames: OrderedDict = OrderedDict()


def _remember(cache: OrderedDict, key, value, cap: int):
    with _lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > cap:
            cache.popitem(last=False)
    return value


def _recalled(cache: OrderedDict, key):
    with _lock:
        if key not in cache:
            return None
        cache.move_to_end(key)
        return cache[key]


def _stamp(st) -> tuple:
    return st.st_size, st.st_mtime_ns


# --- the frame ----------------------------------------------------------------------------

def tex_rects(pos: dict, view, rotation: int, media) -> list[tuple]:
    """A ``pdf_position``'s rects in SyncTeX's frame: bp from the top-left
    of the media box ``media`` (x0, y0, x1, y1), y down, for a page shown
    as ``view`` turned by ``rotation`` (``pdf_export.page_frame``); none for
    a position without a frame."""
    size = highlights._size(pos)
    if not size:
        return []
    out = []
    for r in pos.get("rects") or ():
        if highlights.placed(r):
            x0, y0, x1, y1 = pdf_export.viewer_rect_to_pdf(r, size, rotation, view)
            out.append((x0 - media[0], media[3] - y1, x1 - media[0], media[3] - y0))
    return out


def _position(place: dict) -> dict | None:
    """A place's fraction rects as a position of a 1 × 1 frame."""
    rects = []
    for r in place.get("rects") or ():
        if isinstance(r, (list, tuple)) and len(r) == 4 and all(highlights._number(v) for v in r):
            rects.append(dict(zip(highlights.COORDS, r)))
    return {"pageNumber": place.get("page"), "width": 1, "height": 1, "rects": rects} if rects else None


def _media(page) -> tuple:
    try:
        box = page.mediabox
        x0, y0, x1, y1 = (float(v) for v in (box.left, box.bottom, box.right, box.top))
    except Exception:
        return pdf_export.page_frame(page)[0]
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def page_frames(pdf: Path, st=None) -> list | None:
    """``[(view, rotation, media)]`` of each page of the PDF at ``pdf``,
    kept while its size and time hold; None for a file pypdf cannot read."""
    try:
        st = st or pdf.stat()
    except OSError:
        return None
    key = (str(pdf), _stamp(st))
    hit = _recalled(_frames, key)
    if hit is not None:
        return hit or None
    try:
        reader = PdfReader(str(pdf), strict=False)
        frames = [(*pdf_export.page_frame(page), _media(page)) for page in reader.pages]
    except Exception as e:
        logger.info("no page frames for %s: %s", pdf, e)
        frames = []
    return _remember(_frames, key, frames, PDFS) or None


# --- where the SyncTeX file is --------------------------------------------------------

def _inside(path: Path, bound: Path) -> bool:
    return folder_links.inside(str(path), str(bound))


def _rc_dirs(rc: Path, bound: Path) -> list[Path]:
    """The ``$out_dir`` and ``$aux_dir`` a latexmkrc names, relative to it,
    inside ``bound``."""
    try:
        with rc.open("rb") as f:
            text = f.read(MAX_RC).decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for m in _DIR_RC.finditer(text):
        d = Path(os.path.normpath(rc.parent / m.group(2)))
        if _inside(d, bound):
            out.append(d)
    return out


def _dirs(pdf: Path, root: Path | None) -> list[Path]:
    """The directories a build of ``pdf`` may have written its SyncTeX file
    to besides the PDF's own, in order, without repeats."""
    here, bound = pdf.parent, root or pdf.parent
    out = [here / name for name in BUILD_DIRS]
    for top in dict.fromkeys(p for p in (here, root) if p is not None):
        for name in LATEXMKRC:
            out += _rc_dirs(top / name, bound)
    seen, dirs = {os.path.normcase(str(here))}, []
    for d in out:
        key = os.path.normcase(str(d))
        if key not in seen and d.is_dir():
            seen.add(key)
            dirs.append(d)
    return dirs


def _named(directory: Path, stem: str) -> list[Path]:
    """``<stem>.synctex.gz`` and ``<stem>.synctex`` in ``directory``, the
    newer first."""
    found = []
    for name in (stem + ".synctex.gz", stem + ".synctex"):
        try:
            found.append((os.stat(directory / name).st_mtime_ns, directory / name))
        except OSError:
            continue
    return [p for _t, p in sorted(found, key=lambda e: -e[0])]


def _passes(candidate: Path, pdf_st, pages: int) -> bool:
    """The gates: no newer ``(busy)`` file (a build running), written within
    ``MAX_SKEW_S`` of the PDF, as many sheets as it has pages."""
    try:
        st = candidate.stat()
    except OSError:
        return False
    if abs(st.st_mtime - pdf_st.st_mtime) > MAX_SKEW_S:
        return False
    try:
        if os.stat(candidate.with_name(candidate.name + "(busy)")).st_mtime_ns > st.st_mtime_ns:
            return False
    except OSError:
        pass
    try:
        return synctex.load(candidate).pages == pages
    except (OSError, synctex.SyncTeXError):
        return False


def find(pdf: Path, root: Path | None = None) -> Path | None:
    """The SyncTeX file of the build that made ``pdf``, the work folder's
    directory ``root`` bounding where a latexmkrc may point; None when no
    candidate passes the gates."""
    pdf = Path(pdf)
    try:
        st = pdf.stat()
    except OSError:
        return None
    frames = page_frames(pdf, st)
    if not frames:
        return None
    dirs = _dirs(pdf, root)
    tried = []
    for directory in (pdf.parent, *dirs):
        for candidate in _named(directory, pdf.stem):
            if _passes(candidate, st, len(frames)):
                return candidate
            tried.append(os.path.normcase(str(candidate)))
    others = []
    for directory in (pdf.parent, *dirs):
        try:
            names = sorted(e.name for e in os.scandir(directory) if e.name.lower().endswith(".synctex.gz"))
        except OSError:
            continue
        others += [directory / n for n in names if os.path.normcase(str(directory / n)) not in tried]
    passing = [p for p in others if _passes(p, st, len(frames))]
    return passing[0] if len(passing) == 1 else None


# --- from tags to the source -----------------------------------------------------------

def _relative(path: Path, root: Path) -> str | None:
    if not path.is_file() or not _inside(path, root):
        return None
    rel = os.path.relpath(os.path.normpath(path), os.path.normpath(root))
    return None if rel.startswith("..") else rel.replace(os.sep, "/")


def project_path(tex_path: str, root: Path, base: Path) -> str | None:
    """The file TeX names ``tex_path``, as a path below ``root`` with
    ``/``: itself when it is there, else the longest tail of its parts that
    is a file below ``base`` or a directory above it up to ``root`` (a
    build made in a container or another checkout); None for none (TeX's
    own files)."""
    if not tex_path:
        return None
    norm = os.path.normpath(tex_path)
    parts = [p for p in PurePath(norm).parts if p not in ("", ".")]
    if parts and PurePath(norm).anchor and parts[0] == PurePath(norm).anchor:
        parts = parts[1:]
        whole = _relative(Path(norm), root)
        if whole:
            return whole
    bases, at = [], Path(base)
    while True:
        bases.append(at)
        if not _inside(at, root) or os.path.normcase(str(at)) == os.path.normcase(str(root)) or at.parent == at:
            break
        at = at.parent
    for n in range(len(parts), 0, -1):
        tail = parts[-n:]
        if ".." in tail:
            continue
        for b in bases:
            rel = _relative(b.joinpath(*tail), root)
            if rel:
                return rel
    return None


def _pick(syn: synctex.SyncTeX, ranked, root: Path, base: Path) -> dict | None:
    files: dict[str, dict[int, float]] = {}
    for tag, line, share in ranked:
        rel = project_path(syn.inputs.get(tag, ""), root, base)
        if rel:
            lines = files.setdefault(rel, {})
            lines[line] = lines.get(line, 0.0) + share
    if not files:
        return None
    sources = [f for f in files if os.path.splitext(f)[1].lower() not in GENERATED]
    best = min(sources or files, key=lambda f: (-sum(files[f].values()), f))
    total = sum(files[best].values())
    lines = sorted(ln for ln, share in files[best].items() if share >= LINE_SHARE * total)
    out = {"file": best, "line": lines[0]}
    if lines[-1] != lines[0]:
        out["end"] = lines[-1]
    return out


def source_for(ws: str, conn, page_id: str, props: dict, place: dict) -> dict | None:
    """``{"file", "line", "end"?}`` of ``place`` (``{"kind", "page",
    "rects"}``, the rects fractions of the page as shown, and the anchor's
    ``doc_id`` when it has one) on the page ``page_id`` of ``ws``, a work
    folder's file (its live row in the pages.db ``conn``) whose PDF is on
    this computer; None when there is no answer."""
    view = page_document(props)
    page = place.get("page")
    if not work_folder_of(props) or not is_pdf(view) or not view["hash"] or not highlights._is_page(page):
        return None
    if place.get("doc_id") and place["doc_id"] != view["hash"]:
        return None
    position = _position(place)
    if position is None:
        return None
    row = conn.execute("SELECT work_folder FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                       (page_id,)).fetchone()
    link = folder_links.vault_of_page(ws, page_id) if row and row[0] else None
    pdf = storage.linked_path(ws, view["file"]) if link else None
    stx = find(pdf, Path(link["path"])) if pdf else None
    if stx is None:
        return None
    root = Path(link["path"])
    try:
        pdf_st, stx_st = pdf.stat(), stx.stat()
    except OSError:
        return None
    key = (str(pdf), _stamp(pdf_st), str(stx), _stamp(stx_st), str(root),
           json.dumps([place.get("kind"), page, position["rects"]], sort_keys=True))
    with _lock:
        if key in _memo:
            _memo.move_to_end(key)
            hit = _memo[key]
            return dict(hit) if hit else None
    frames = page_frames(pdf, pdf_st)
    answer = None
    if frames and page <= len(frames):
        view_box, rotation, media = frames[page - 1]
        rects = tex_rects(position, view_box, rotation, media)
        try:
            syn = synctex.load(stx)
            ranked = syn.query(page, rects, text=place.get("kind") == "passage") if rects else []
        except (OSError, synctex.SyncTeXError):
            return None
        answer = _pick(syn, ranked, root, pdf.parent) if ranked else None
    _remember(_memo, key, answer, MEMO)
    return dict(answer) if answer else None
