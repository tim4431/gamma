"""Can a reader tell its good LaTeX from its bad without the truth?

  python -I guard.py [--set real|synth]

For every engine scored in $OCR_STUDY_DIR/stage3/scores/<set>-<engine>.json
(score_latex --json rows: ned to the truth, compiles), signals a product
could compute:
- compiles: the prediction renders (render_latex);
- crop: ink IoU of the prediction's render against the scanned crop itself
  (score_latex.visual with the crop as reference, so the render is scaled
  to the crop's ink box), 1 is identical;
- aspect: |log(width/height)| difference of the two ink boxes;
- agree: 1 - ned between this engine's tokens and the other engines' (the
  best agreement with any other engine);
- loop: the share of the prediction's tokens in a repeated 4-gram (a reader
  stuck in a loop).
Per signal: the AUC for telling good (ned <= 0.10) from bad (ned > 0.25),
and the kept/flagged split at the threshold that flags 20% of predictions.
Writes scores/guard-<set>.json."""

import argparse
import collections
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402
from score_latex import edit, ink, tokens, visual  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--set", default="real", choices=["real", "synth"])
a = ap.parse_args()
S = ROOT
SC = S / "scores"
CROPS = S / "crops_real" if a.set == "real" else S / "synth" / "scan"
REC = S / "rec" if a.set == "real" else S / "rec_synth"
CACHE = S / "render_cache"

engines = {}
for f in sorted(SC.glob(f"{a.set}-*.json")):
    eng = f.stem[len(a.set) + 1:]
    engines[eng] = {r["id"]: r for r in json.loads(f.read_text(encoding="utf-8"))["rows"]}
preds = {e: {i: (json.loads((REC / e / f"{i}.json").read_text(encoding="utf-8")).get("latex", "")
                 if (REC / e / f"{i}.json").exists() else "") for i in rows} for e, rows in engines.items()}
toks = {e: {i: tokens(p) for i, p in d.items()} for e, d in preds.items()}


def aspect(png):
    m = ink(png)
    return None if m is None else math.log(m.shape[1] / max(1, m.shape[0]))


def loop_share(t):
    if len(t) < 8:
        return 0.0
    grams = collections.Counter(tuple(t[k:k + 4]) for k in range(len(t) - 3))
    rep = set()
    for k in range(len(t) - 3):
        if grams[tuple(t[k:k + 4])] >= 3:
            rep.update(range(k, k + 4))
    return len(rep) / len(t)


def auc(pos, neg):
    """P(signal of a good one > signal of a bad one); ties count half."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


out = {}
for e, rows in engines.items():
    sig = []
    for i, r in rows.items():
        crop = CROPS / f"{i}.png"
        pp = CACHE / f"{e}-{i}.png"
        ok = bool(r.get("compiles")) and pp.exists()
        crop_iou = visual(crop, pp) if ok else 0.0
        ac, ap_ = aspect(crop), (aspect(pp) if ok else None)
        asp = -abs(ac - ap_) if (ac is not None and ap_ is not None) else -9.0
        others = [1 - min(1.0, edit(toks[o][i], toks[e][i]) / max(1, len(toks[o][i]))) for o in engines if o != e and i in toks[o]]
        sig.append({"id": i, "ned": r["ned"], "compiles": float(ok), "crop": round(crop_iou, 4), "aspect": round(asp, 4),
                    "agree": round(max(others), 4) if others else 0.0, "loop": -loop_share(toks[e][i])})
    good = [s for s in sig if s["ned"] <= 0.10]
    bad = [s for s in sig if s["ned"] > 0.25]
    res = {"n": len(sig), "good": len(good), "bad": len(bad), "ned_mean": round(sum(s["ned"] for s in sig) / max(1, len(sig)), 4)}
    for k in ("compiles", "crop", "aspect", "agree", "loop"):
        res[f"auc_{k}"] = auc([s[k] for s in good], [s[k] for s in bad])
        order = sorted(sig, key=lambda s: s[k])
        cut = max(1, round(0.2 * len(order)))
        flagged, kept = order[:cut], order[cut:]
        res[f"flag20_{k}"] = {"kept_ned": round(sum(s["ned"] for s in kept) / max(1, len(kept)), 4),
                              "flagged_ned": round(sum(s["ned"] for s in flagged) / max(1, len(flagged)), 4),
                              "bad_caught": sum(s["ned"] > 0.25 for s in flagged)}
    out[e] = {"summary": res, "rows": sig}
    print(e, json.dumps(res))
(SC / f"guard-{a.set}.json").write_text(json.dumps(out, indent=0), encoding="utf-8")
