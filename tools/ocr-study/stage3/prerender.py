"""Fill $OCR_STUDY_DIR/stage3/render_cache in parallel, so score_latex finds every render cached.

  python -I prerender.py [--jobs 6]

Renders each truth (truth-<id>.png) of gt_latex and gt_synth, and each
prediction under rec/<engine> and rec_synth/<engine> (<engine>-<id>.png)
whose id has a truth, skipping what is cached. A prediction that does not
compile leaves no file, and score_latex then tries it again (once, fast to
fail), so failures are recorded in render_cache/_failed.txt and score_latex
treats them as non-compiling through the same missing file."""

import argparse
import json
import pathlib
import sys
from multiprocessing import Pool

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402
from render_latex import render  # noqa: E402


def job(t):
    latex, out = t
    return out, bool(latex) and render(latex, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    S = ROOT
    cache = S / "render_cache"
    cache.mkdir(exist_ok=True)
    failed_f = cache / "_failed.txt"
    failed = set(failed_f.read_text(encoding="utf-8").split()) if failed_f.exists() else set()
    todo = []
    for gt, rec in (("gt_latex", "rec"), ("gt_synth", "rec_synth")):
        ids = [p.stem for p in (S / gt).glob("*.tex")]
        for i in ids:
            out = cache / f"truth-{i}.png"
            if not out.exists():
                todo.append(((S / gt / f"{i}.tex").read_text(encoding="utf-8"), str(out)))
        for d in sorted((S / rec).iterdir()):
            if not d.is_dir() or d.name.endswith(("-page", "-cpu")):
                continue
            for i in ids:
                pf, out = d / f"{i}.json", cache / f"{d.name}-{i}.png"
                if pf.exists() and not out.exists() and out.name not in failed:
                    todo.append((json.loads(pf.read_text(encoding="utf-8")).get("latex", ""), str(out)))
    print(len(todo), "to render", flush=True)
    bad = 0
    with Pool(a.jobs) as pool:
        for k, (out, ok) in enumerate(pool.imap_unordered(job, todo, chunksize=4), 1):
            if not ok:
                bad += 1
                failed.add(pathlib.Path(out).name)
            if k % 200 == 0:
                print(k, "done", bad, "failed", flush=True)
    failed_f.write_text("\n".join(sorted(failed)), encoding="utf-8")
    print("done", len(todo), "rendered,", bad, "failed")
