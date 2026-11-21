"""Shared helpers and settings for the Stage 2 OCR study.

Every engine writes $OCR_STUDY_DIR/stage2/out/<engine>/<page id>.json:
  {"engine", "page", "time": seconds of recognition (render excluded),
   "lines": [{"text", "bbox": [x0, y0, x1, y1], "words": [{"text", "bbox"}]}]}
Boxes are PDF points, top-left origin (PyMuPDF page coordinates).

Settings (environment variables):
  OCR_STUDY_DIR             the working directory (default tools/ocr-study/.work);
                            the corpus is corpus/<id>.pdf in it
  OCR_STUDY_TESSDATA_FAST   tessdata_fast folder (github.com/tesseract-ocr/tessdata_fast)
  OCR_STUDY_TESSDATA_BEST   tessdata_best folder (github.com/tesseract-ocr/tessdata_best)
"""

import json
import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
WORK = pathlib.Path(os.environ.get("OCR_STUDY_DIR") or HERE.parent / ".work").resolve()
CORPUS = WORK / "corpus"
ROOT = WORK / "stage2"
IMG = ROOT / "img"
OUT = ROOT / "out"
GT = ROOT / "gt"
PREP = ROOT / "gtprep"
DPI = 300
SCALE = 72 / DPI  # pixels -> points


def tessdata(kind):
    """The tessdata folder for `kind` (fast or best), from OCR_STUDY_TESSDATA_<KIND>."""
    var = f"OCR_STUDY_TESSDATA_{kind.upper()}"
    path = os.environ.get(var)
    if not path:
        raise SystemExit(f"set {var} to the tessdata_{kind} folder")
    return pathlib.Path(path)


def load_pages():
    spec = json.loads((HERE / "pages.json").read_text(encoding="utf-8"))
    for p in spec["pages"]:
        p["path"] = str(CORPUS / f"{p['file']}.pdf")
    return spec["pages"]


def select(pages, argv):
    """Pages named on the command line (ids or file names), else all."""
    want = [a for a in argv if not a.startswith("-")]
    if not want:
        return pages
    return [p for p in pages if p["id"] in want or p["file"] in want]


def image_path(pid):
    return IMG / f"{pid}.png"


def meta(pid):
    return json.loads((IMG / f"{pid}.json").read_text(encoding="utf-8"))


def px_box(box):
    return [round(v * SCALE, 2) for v in box]


def write(engine, pid, lines, seconds, **extra):
    d = OUT / engine
    d.mkdir(parents=True, exist_ok=True)
    rec = {"engine": engine, "page": pid, "time": round(seconds, 3), "lines": lines, **extra}
    (d / f"{pid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=0), encoding="utf-8")


def line_of(words):
    """A line record from its words (each {"text", "bbox"})."""
    xs0, ys0, xs1, ys1 = zip(*(w["bbox"] for w in words))
    return {"text": " ".join(w["text"] for w in words),
            "bbox": [min(xs0), min(ys0), max(xs1), max(ys1)], "words": words}


def group_rows(words, overlap=0.5, gap=3.0):
    """Words in reading/stream order -> lines: a word continues the line when
    it overlaps the line vertically by `overlap` of the smaller height, starts
    right of the previous word, and the gap is under `gap` line heights (the
    rule of gamma/pdf_scan.rows)."""
    lines, cur = [], []
    for w in words:
        if cur:
            p = cur[-1]["bbox"]
            b = w["bbox"]
            lo, hi = max(p[1], b[1]), min(p[3], b[3])
            h = min(p[3] - p[1], b[3] - b[1]) or 1
            same = (hi - lo) / h >= overlap and b[0] >= p[2] - 0.5 and b[0] - p[2] <= gap * h
            if not same:
                lines.append(line_of(cur))
                cur = []
        cur.append(w)
    if cur:
        lines.append(line_of(cur))
    return lines


def threads_env(n):
    """Environment pinning a run to n threads (OpenMP, ONNX Runtime via its own
    option, BLAS)."""
    env = dict(os.environ)
    for k in ("OMP_THREAD_LIMIT", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        env[k] = str(n)
    return env
