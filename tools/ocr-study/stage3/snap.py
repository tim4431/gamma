"""Snap the marked display-equation boxes to their ink and cut the crops.

  python -I snap.py

Reads $OCR_STUDY_DIR/stage3/marks/<page>.json, writes
$OCR_STUDY_DIR/stage3/eq/<page>-e<k>.png (grey, the ink box plus an 8 px
margin) and eq/index.json: [{id, page, box (snapped, page px), marked (as
marked), number, number_box, rows}]. The snap trims white rows and columns
from the marked box (a box marked generously around the equation), never
growing it."""

import json
import pathlib
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402

MARKS, IMG, OUT = ROOT / "marks", ROOT / "img", ROOT / "eq"
OUT.mkdir(exist_ok=True)
MARGIN = 8


def snap(gray, box):
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    h, w = gray.shape
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    region = gray[y0:y1, x0:x1]
    if region.size == 0:
        return None
    ink = region < 140
    rows, cols = np.where(ink.any(axis=1))[0], np.where(ink.any(axis=0))[0]
    if not len(rows) or not len(cols):
        return None
    return [x0 + int(cols[0]), y0 + int(rows[0]), x0 + int(cols[-1]) + 1, y0 + int(rows[-1]) + 1]


index, missing = [], []
for f in sorted(MARKS.glob("*.json")):
    mark = json.loads(f.read_text(encoding="utf-8"))
    pid = mark.get("page") or f.stem
    gray = np.asarray(Image.open(IMG / f"{pid}.png").convert("L"))
    for k, e in enumerate(mark.get("equations") or [], 1):
        box = snap(gray, e["box"])
        if box is None:
            missing.append(f"{pid} #{k}")
            continue
        h, w = gray.shape
        crop = (max(0, box[0] - MARGIN), max(0, box[1] - MARGIN), min(w, box[2] + MARGIN), min(h, box[3] + MARGIN))
        eid = f"{pid}-e{k}"
        Image.fromarray(gray[crop[1]:crop[3], crop[0]:crop[2]]).save(OUT / f"{eid}.png")
        index.append({"id": eid, "page": pid, "box": box, "marked": e["box"], "number": e.get("number"),
                      "number_box": e.get("number_box"), "rows": e.get("rows", 1), "note": e.get("note", "")})
(OUT / "index.json").write_text(json.dumps(index, indent=0, ensure_ascii=False), encoding="utf-8")
print(len(index), "equations cut;", len(missing), "with no ink:", missing[:10])
