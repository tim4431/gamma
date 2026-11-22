"""Score display-formula detection against the marked equations.

  python -I score_detect.py <model> [--min-score 0.5] [--classes a,b]

Truth: $OCR_STUDY_DIR/stage3/eq/index.json (every display equation on the
69 pages, its box snapped to the ink, the equation number left out).
Predictions: det/<model>/<page>.json [{cls, box, score}] in 300-dpi page
pixels.

- Matched at IoU >= 0.5 (greedy, best first): recall, precision, F1.
- Area view (a finder may split a display into rows or join two): truth
  ink area covered by any predicted box (coverage), and predicted area
  that lies on some truth box (purity).
- Number boxes: whether a predicted box swallowed the printed equation
  number (the reader would then read it into the formula)."""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402

DISPLAY = {"display_formula", "formula", "isolate_formula", "isolated_formula", "equation", "interline_equation",
           "interline_formula", "formula_block", "Equation"}

ap = argparse.ArgumentParser()
ap.add_argument("model")
ap.add_argument("--min-score", type=float, default=0.5)
ap.add_argument("--classes", default="")
a = ap.parse_args()
S = ROOT
classes = set(a.classes.split(",")) if a.classes else DISPLAY
truth = json.loads((S / "eq" / "index.json").read_text(encoding="utf-8"))
pages = [p["id"] for p in json.loads((S / "pages.json").read_text(encoding="utf-8"))]


def iou(p, q):
    ix = max(0, min(p[2], q[2]) - max(p[0], q[0]))
    iy = max(0, min(p[3], q[3]) - max(p[1], q[1]))
    inter = ix * iy
    union = (p[2] - p[0]) * (p[3] - p[1]) + (q[2] - q[0]) * (q[3] - q[1]) - inter
    return inter / union if union else 0.0


def area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def covered(box, others):
    """Area of `box` covered by the union of `others` (a raster at 4 px)."""
    import numpy as np
    x0, y0, x1, y1 = (int(v) // 4 for v in box)
    if x1 <= x0 or y1 <= y0:
        return 0
    m = np.zeros((y1 - y0, x1 - x0), bool)
    for o in others:
        ox0, oy0, ox1, oy1 = (int(v) // 4 for v in o)
        m[max(0, oy0 - y0):max(0, oy1 - y0), max(0, ox0 - x0):max(0, ox1 - x0)] = True
    return int(m.sum()) * 16


tp = fp = fn = 0
cov_num = cov_den = pur_num = pur_den = 0
swallowed = numbered = 0
missing_pages = []
for pid in pages:
    f = S / "det" / a.model / f"{pid}.json"
    if not f.exists():
        missing_pages.append(pid)
        continue
    pred_all = json.loads(f.read_text(encoding="utf-8"))
    preds = [p for p in pred_all if p.get("cls") in classes and p.get("score", 1) >= a.min_score]
    gts = [e for e in truth if e["page"] == pid]
    pairs = sorted(((iou(g["box"], p["box"]), gi, pi) for gi, g in enumerate(gts) for pi, p in enumerate(preds)), reverse=True)
    used_g, used_p = set(), set()
    for v, gi, pi in pairs:
        if v < 0.5:
            break
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
    tp += len(used_g)
    fn += len(gts) - len(used_g)
    fp += len(preds) - len(used_p)
    for g in gts:
        cov_num += covered(g["box"], [p["box"] for p in preds])
        cov_den += area(g["box"])
        if g.get("number_box"):
            numbered += 1
            nb = g["number_box"]
            if any(iou(nb, p["box"]) > 0 and covered(nb, [p["box"]]) > 0.5 * area(nb) for p in preds):
                swallowed += 1
    for p in preds:
        pur_num += covered(p["box"], [g["box"] for g in gts])
        pur_den += area(p["box"])

rec = tp / max(1, tp + fn)
prec = tp / max(1, tp + fp)
print(json.dumps({"model": a.model, "classes": sorted(classes), "min_score": a.min_score,
                  "truth": tp + fn, "predicted": tp + fp, "recall": round(rec, 3), "precision": round(prec, 3),
                  "f1": round(2 * rec * prec / max(1e-9, rec + prec), 3),
                  "coverage": round(cov_num / max(1, cov_den), 3), "purity": round(pur_num / max(1, pur_den), 3),
                  "numbers_swallowed": f"{swallowed}/{numbered}", "pages_missing": len(missing_pages)}))
