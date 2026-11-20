"""A primary reader checked by a second one: keep the primary's LaTeX where the two agree.

  python -I pair_guard.py

Over both sets (real crops against gt_latex, synthetic scans against gt_synth), for each
primary P and checker C, and each threshold t on the token edit distance between P's and C's
readings (over P's length): the share kept, the mean NED (to the truth) of the kept and of the
flagged, and how many bad readings (NED > 0.25) were kept. A reading is the same in both sets
only by its engine name: real ones are under rec/, synthetic under rec_synth/ (both in
$OCR_STUDY_DIR/stage3)."""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402
from score_latex import edit, tokens  # noqa: E402

S = ROOT
SETS = {"real": ("rec", "real"), "synth": ("rec_synth", "synth")}
PRIMARY = ["glm-ocr-gpu", "paddleocr-vl-1.6-gpu", "lightonocr-2-gpu", "pp-formulanet-plus-m"]
CHECK = ["glm-ocr-gpu", "paddleocr-vl-1.6-gpu", "lightonocr-2-gpu", "dots-ocr-gpu", "pp-formulanet-plus-m",
         "pix2text-mfr-1.5", "pp-formulanet-plus-s", "granite-docling-gpu", "texteller"]


def load(set_name, eng):
    rec, tag = SETS[set_name]
    f = S / "scores" / f"{tag}-{eng}.json"
    if not f.exists():
        return None
    rows = {r["id"]: r["ned"] for r in json.loads(f.read_text(encoding="utf-8"))["rows"]}
    toks = {i: tokens(json.loads((S / rec / eng / f"{i}.json").read_text(encoding="utf-8")).get("latex", "")) for i in rows}
    return rows, toks


data = {(s, e): load(s, e) for s in SETS for e in set(PRIMARY + CHECK)}
for p in PRIMARY:
    for c in CHECK:
        if c == p:
            continue
        items = []
        for s in SETS:
            if not data[(s, p)] or not data[(s, c)]:
                continue
            (pn, pt), (_, ct) = data[(s, p)], data[(s, c)]
            for i in pn:
                d = edit(pt[i], ct[i]) / max(1, len(pt[i]))
                items.append((d, pn[i]))
        if not items:
            continue
        n = len(items)
        bad = sum(v > 0.25 for _, v in items)
        line = []
        for t in (0.0, 0.05, 0.1, 0.2):
            kept = [v for d, v in items if d <= t]
            flag = [v for d, v in items if d > t]
            line.append(f"t={t:.2f}: kept {len(kept) / n:.0%} ned {sum(kept) / max(1, len(kept)):.3f}"
                        f" bad {sum(v > 0.25 for v in kept)}/{bad} | flagged ned {sum(flag) / max(1, len(flag)):.3f}")
        print(f"{p} checked by {c} (n={n}, all ned {sum(v for _, v in items) / n:.3f})")
        for x in line:
            print("   ", x)
