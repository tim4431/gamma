"""Share of marked displays covered (>= 50% and >= 80% of their area) by a finder's boxes, joined or split.
python -I region_cover.py <model> [min_score]"""
import json, pathlib, sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402
s3, model = ROOT, sys.argv[1]
ms = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
DISPLAY = {"display_formula", "formula", "isolate_formula"}
truth = json.loads((s3 / "eq" / "index.json").read_text(encoding="utf-8"))
c50 = c80 = 0
for g in truth:
    preds = [p["box"] for p in json.loads((s3 / "det" / model / f"{g['page']}.json").read_text(encoding="utf-8")) if p["cls"] in DISPLAY and p["score"] >= ms]
    x0, y0, x1, y1 = (int(v) for v in g["box"])
    m = np.zeros((y1 - y0, x1 - x0), bool)
    for b in preds:
        m[max(0, int(b[1]) - y0):max(0, int(b[3]) - y0), max(0, int(b[0]) - x0):max(0, int(b[2]) - x0)] = True
    c = m.mean() if m.size else 0
    c50 += c >= 0.5
    c80 += c >= 0.8
print(model, f"covered >=50%: {c50}/{len(truth)}  >=80%: {c80}/{len(truth)}")
