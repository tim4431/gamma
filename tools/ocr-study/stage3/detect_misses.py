"""Why a finder misses: per paper, and each unmatched truth/prediction sorted.

  python -I detect_misses.py <model> [--min-score 0.5]

A truth equation left unmatched at IoU 0.5 is
- split    when two or more predictions each cover part of it (>= 80% of its
           area together),
- merged   when one prediction covers it and another truth too,
- loose    when one prediction covers >= 80% of it but is too large/small
           for IoU 0.5 alone (padding, a swallowed number or text line),
- missed   otherwise (coverage < 50%), or partial.
An unmatched prediction is
- part     when it lies mostly (>= 60%) on truth (the other half of a split
           or a merge),
- spurious otherwise (prints its box, for a look)."""

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("model")
ap.add_argument("--min-score", type=float, default=0.5)
a = ap.parse_args()
S = ROOT
DISPLAY = {"display_formula", "formula", "isolate_formula"}
truth = json.loads((S / "eq" / "index.json").read_text(encoding="utf-8"))
pages = [p["id"] for p in json.loads((S / "pages.json").read_text(encoding="utf-8"))]


def inter(p, q):
    return max(0, min(p[2], q[2]) - max(p[0], q[0])) * max(0, min(p[3], q[3]) - max(p[1], q[1]))


def area(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def iou(p, q):
    i = inter(p, q)
    return i / (area(p) + area(q) - i) if i else 0.0


per = collections.defaultdict(lambda: collections.Counter())
kinds = collections.Counter()
examples = collections.defaultdict(list)
for pid in pages:
    f = pid.split("-")[0]
    preds = [p for p in json.loads((S / "det" / a.model / f"{pid}.json").read_text(encoding="utf-8"))
             if p["cls"] in DISPLAY and p["score"] >= a.min_score]
    gts = [e for e in truth if e["page"] == pid]
    pairs = sorted(((iou(g["box"], p["box"]), gi, pi) for gi, g in enumerate(gts) for pi, p in enumerate(preds)), reverse=True)
    ug, up = set(), set()
    for v, gi, pi in pairs:
        if v >= 0.5 and gi not in ug and pi not in up:
            ug.add(gi)
            up.add(pi)
    per[f]["truth"] += len(gts)
    per[f]["tp"] += len(ug)
    per[f]["pred"] += len(preds)
    for gi, g in enumerate(gts):
        if gi in ug:
            continue
        gb = g["box"]
        over = [(inter(gb, p["box"]) / area(gb), pi) for pi, p in enumerate(preds) if inter(gb, p["box"])]
        big = [pi for c, pi in over if c >= 0.8]
        tot = sum(c for c, _ in over)
        if big:
            pb = preds[big[0]]["box"]
            others = [o for o in gts if o is not g and inter(o["box"], pb) > 0.5 * area(o["box"])]
            k = "merged" if others else "loose"
        elif len(over) >= 2 and tot >= 0.8:
            k = "split"
        elif tot < 0.5:
            k = "missed"
        else:
            k = "partial"
        kinds["truth:" + k] += 1
        examples["truth:" + k].append(f"{g['id']} rows={g.get('rows', 1)} box={gb} preds={[preds[pi]['box'] for _, pi in over][:3]}")
    for pi, p in enumerate(preds):
        if pi in up:
            continue
        on = sum(inter(p["box"], g["box"]) for g in gts) / max(1, area(p["box"]))
        k = "part" if on >= 0.6 else "spurious"
        kinds["pred:" + k] += 1
        examples["pred:" + k].append(f"{pid} {p['box']} score={p['score']:.2f} on_truth={on:.2f}")

print(a.model, "min_score", a.min_score)
for f, c in sorted(per.items()):
    r = c["tp"] / max(1, c["truth"])
    pr = c["tp"] / max(1, c["pred"])
    print(f"  {f:10s} truth {c['truth']:4d}  pred {c['pred']:4d}  recall {r:.3f}  precision {pr:.3f}")
print(dict(kinds))
for k, v in examples.items():
    print(f"-- {k} ({len(v)})")
    for e in v[:12]:
        print("   ", e)
