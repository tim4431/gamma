"""A display-equation finder from Tesseract's lines alone (no model), as a
baseline for the layout models.

  python -I detect_tess.py

A line is display math when
- it ends in an equation number — a last word like (12), (3.16), (4.5a),
  (17') — after a gap of at least 3 line heights, or alone at the right
  margin with the line's ink well left of it; or
- it is indented from both column edges (centred: at least 6% of the
  column on each side) and short of the column's width, and its words read
  poorly (mean confidence < 70) or hold math characters (= + − / ^ _ ∑ ∫ ≈
  or Greek).
Lines judged math that touch vertically (gap < 0.8 line heights) and
overlap horizontally merge into one display. The number is left out of the
box. Writes $OCR_STUDY_DIR/stage3/det/tess-rules/<page>.json
[{cls: "display_formula", box, score: 1}] in the same frame as the layout
models."""

import json
import pathlib
import re
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402

S = ROOT
OUT = S / "det" / "tess-rules"
OUT.mkdir(parents=True, exist_ok=True)
NUM = re.compile(r"^\(?\d{1,3}(\.\d{1,3})?[a-z]?'?\)?[.,]?$")
MATH = set("=+−-/^_∑∫≈≃∝∞±×·<>|ΓΔΘΛΞΠΣΦΨΩαβγδεζηθικλμνξπρστυφχψω∂∇")
TWO = {"holstein", "kitagawa", "naca", "jdp"}


def columns(lines, width):
    """Column bounds from the text lines' left and right edges."""
    lefts = sorted(l["box"][0] for l in lines if len(l["words"]) > 4)
    rights = sorted(l["box"][2] for l in lines if len(l["words"]) > 4)
    if not lefts:
        return [(0, width)]
    return [(lefts[len(lefts) // 10], rights[-max(1, len(rights) // 10)])]


pages = json.loads((S / "pages.json").read_text(encoding="utf-8"))
for p in pages:
    pid = p["id"]
    lines = json.loads((S / "lines" / f"{pid}.json").read_text(encoding="utf-8"))
    width = p["px"][0]
    if p["file"] in TWO:
        mid = width / 2
        cols = [(min(l["box"][0] for l in lines), mid), (mid, max(l["box"][2] for l in lines))] if lines else []
    else:
        cols = columns(lines, width)
    hs = [l["box"][3] - l["box"][1] for l in lines if l["words"]]
    H = statistics.median(hs) if hs else 40
    math_lines = []
    for l in lines:
        x0, y0, x1, y1 = l["box"]
        col = next(((a, b) for a, b in cols if a - 20 <= (x0 + x1) / 2 <= b + 20), cols[0] if cols else (0, width))
        cw = col[1] - col[0]
        words = l["words"]
        body = words
        numbered = False
        last = words[-1]["text"].strip()
        if NUM.match(last) and len(words) > 1:
            gap = words[-1]["box"][0] - words[-2]["box"][2]
            if gap >= 3 * H:
                numbered, body = True, words[:-1]
        if not body:
            continue
        bx0 = min(w["box"][0] for w in body)
        bx1 = max(w["box"][2] for w in body)
        conf = statistics.mean(w["conf"] for w in body)
        text = "".join(w["text"] for w in body)
        mathy = sum(ch in MATH for ch in text) >= 1
        centred = bx0 - col[0] >= 0.06 * cw and col[1] - bx1 >= 0.06 * cw
        if numbered or (centred and (conf < 70 or mathy) and bx1 - bx0 < 0.85 * cw):
            math_lines.append([bx0, y0, bx1, y1])
    # merge touching lines into displays
    math_lines.sort(key=lambda b: b[1])
    displays = []
    for b in math_lines:
        if displays:
            d = displays[-1]
            if b[1] - d[3] < 0.8 * H and min(b[2], d[2]) > max(b[0], d[0]):
                displays[-1] = [min(d[0], b[0]), min(d[1], b[1]), max(d[2], b[2]), max(d[3], b[3])]
                continue
        displays.append(b)
    (OUT / f"{pid}.json").write_text(json.dumps([{"cls": "display_formula", "box": d, "score": 1.0} for d in displays]),
                                     encoding="utf-8")
print(len(pages), "pages")
