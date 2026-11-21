"""Stage 2: compile a seeded sample of candidates, keep a width-stratified set,
render clean + degraded PNGs and write ground-truth JSON (all under
$OCR_STUDY_DIR/stage3/synth)."""
import json, sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent)]
import render, degrade
from common import SYNTH

BASE = SYNTH
SEED = 20261119            # sampling seed
DEGRADE_SEED = 1950        # per-equation degradation seed = DEGRADE_SEED + n
GUARD_STEP = 100000        # redraw k uses DEGRADE_SEED + n + GUARD_STEP * k
COMPILE_PER_PAPER = 45
KEEP_PER_PAPER = 24
MAX_W = int(1.03 * render.TEXTWIDTH_IN * render.DPI) + 2 * render.MARGIN


def main():
    cands = json.loads((BASE / "work" / "candidates.json").read_text(encoding="utf-8"))
    papers = json.loads((BASE / "meta" / "papers.json").read_text(encoding="utf-8"))
    rng = np.random.default_rng(SEED)
    by = defaultdict(list)
    for c in cands:
        by[c["paper"]].append(c)
    sample = []
    for p in papers:
        lst = by[p["id"]]
        idx = rng.permutation(len(lst))[:COMPILE_PER_PAPER]
        sample += [lst[i] for i in sorted(idx)]

    def job(c):
        a, err = render.render(c["latex"], BASE / "work" / "tex" / c["cid"])
        return c, a, err

    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(job, sample))

    failures, ok = [], defaultdict(list)
    for c, a, err in results:
        if a is None:
            failures.append({"cid": c["cid"], "paper": c["paper"], "latex": c["latex"], "error": err})
        elif a.shape[1] > MAX_W:
            failures.append({"cid": c["cid"], "paper": c["paper"], "latex": c["latex"],
                             "error": "wider than the 3.4in column (%d px)" % a.shape[1]})
        else:
            ok[c["paper"]].append((c, a))

    # width-stratified selection per paper: evenly spaced through the width ranking
    kept = []
    for p in papers:
        lst = sorted(ok[p["id"]], key=lambda t: t[1].shape[1])
        if len(lst) > KEEP_PER_PAPER:
            pick = sorted(set(np.round(np.linspace(0, len(lst) - 1, KEEP_PER_PAPER)).astype(int)))
            lst = [lst[i] for i in pick]
        kept += lst

    for d in ("clean", "scan", "gt"):
        (BASE / d).mkdir(exist_ok=True)
    index = []
    for n, (c, a) in enumerate(kept, 1):
        eid = "eq%04d" % n
        render.save_png(a, BASE / "clean" / (eid + ".png"))
        # legibility guard: redraw (next seed) if the scan keeps <75% or >220%
        # of the clean ink; the seed actually used is recorded
        clean_ink = int((a < 128).sum())
        for k in range(20):
            s, params = degrade.degrade(a, DEGRADE_SEED + n + GUARD_STEP * k)
            ratio = int((s < 128).sum()) / clean_ink
            if 0.75 <= ratio <= 2.2:
                break
        params["ink_ratio_vs_clean"] = round(ratio, 3)
        params["redraws"] = k
        degrade.save_bilevel(s, BASE / "scan" / (eid + ".png"))
        rec = {"id": eid, "paper": c["paper"], "latex": c["latex"],
               "clean_png": "clean/%s.png" % eid, "scan_png": "scan/%s.png" % eid,
               "width_px": int(a.shape[1]), "height_px": int(a.shape[0]),
               "scan_width_px": int(s.shape[1]), "scan_height_px": int(s.shape[0]),
               "source_env": c["kind"], "source_latex": c["source"],
               "candidate_id": c["cid"], "degradation": params}
        (BASE / "gt" / (eid + ".json")).write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
        index.append({k: rec[k] for k in ("id", "paper", "latex", "clean_png", "scan_png",
                                         "width_px", "height_px")} | {"gt": "gt/%s.json" % eid})
    (BASE / "index.json").write_text(json.dumps({"equations": index, "papers": papers}, indent=1,
                                                ensure_ascii=False), encoding="utf-8")
    (BASE / "logs").mkdir(exist_ok=True)
    (BASE / "logs" / "compile_failures.json").write_text(json.dumps(failures, indent=1), encoding="utf-8")

    stats = {"candidates_extracted": len(cands), "sampled_for_compile": len(sample),
             "compiled_ok": sum(1 for _, a, _ in results if a is not None),
             "failed": len(failures), "kept": len(kept),
             "per_paper": {p["id"]: {"candidates": p["candidates"],
                                     "sampled": sum(1 for c in sample if c["paper"] == p["id"]),
                                     "ok_in_width": len(ok[p["id"]]),
                                     "kept": sum(1 for c, _ in kept if c["paper"] == p["id"])}
                           for p in papers},
             "failure_kinds": Counter(f["error"].split(" ")[0] + " " + " ".join(f["error"].split(" ")[1:3])
                                      for f in failures).most_common(),
             "kept_width_px": [int(min(a.shape[1] for _, a in kept)), int(np.median([a.shape[1] for _, a in kept])),
                               int(max(a.shape[1] for _, a in kept))]}
    (BASE / "logs" / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
