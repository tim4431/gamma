"""CPU seconds per page for the small models' page OCR, estimated from Stage 3's idle-machine CPU timings.

  python -I cpu_estimate.py

Rates per model on llama.cpp's default 12 threads, from the Stage 3 crop records ($OCR_STUDY_DIR/stage3/timing/rec*/<model>-cpu/*.json,
stage3/rec/<model>-cpu): llama-server's own split, prefill = sum prompt_n / sum prompt_ms (the image's tokens included),
decode = sum predicted_n / sum predicted_ms, plus the request overhead (seconds - prompt_ms - predicted_ms).
One thread: the 12-thread estimate times the median ratio seconds(1 thread) / seconds(12) on the same crops.
Applied to the GPU page runs' token counts ($OCR_STUDY_DIR/stage2/vlm_raw/<engine>/<page>.json): a whole page is one request, a region
run one request per region plus PP-DocLayoutV3 on the CPU (0.53 s a page on 12 threads, 1.18 s on one; stage3/timing/det).
The crops carry at most ~600 tokens in and a whole page 1-4k, so whole-page figures are extrapolated.
"""
import glob
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT, WORK  # noqa: E402

S3 = ROOT
S2 = WORK / "stage2"
MODEL = {"paddle-regions": "paddleocr-vl-1.6", "paddle-page": "paddleocr-vl-1.6", "lighton-page": "lightonocr-2",
         "glm-regions": "glm-ocr", "glm-page": "glm-ocr", "granite-page": "granite-docling"}
DET = {"default": 0.53, "1": 1.18}


def crop_records(model):
    for d in ("timing/rec", "timing/rec_inline", "rec"):
        for f in glob.glob(str(S3 / d /f"{model}-cpu" / "*.json")):
            if not pathlib.Path(f).name.startswith("_"):
                r = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
                if r.get("server") and r.get("seconds_by_threads"):
                    yield r


rates = {}
for model in sorted(set(MODEL.values())):
    recs = list(crop_records(model))
    pn = sum(r["server"]["prompt_n"] for r in recs)
    pms = sum(r["server"]["prompt_ms"] for r in recs)
    dn = sum(r["server"]["predicted_n"] for r in recs)
    dms = sum(r["server"]["predicted_ms"] for r in recs)
    over = statistics.median(r["seconds_by_threads"]["default"] - (r["server"]["prompt_ms"] + r["server"]["predicted_ms"]) / 1000 for r in recs)
    ratio = [r["seconds_by_threads"]["1"] / r["seconds_by_threads"]["default"] for r in recs if "1" in r["seconds_by_threads"]]
    rates[model] = (pn / pms * 1000, dn / dms * 1000, max(over, 0), statistics.median(ratio))
    print(f"{model:17s} crops={len(recs):3d}  12 threads: prefill {rates[model][0]:4.0f} tok/s, decode {rates[model][1]:4.1f} tok/s,"
          f" overhead {rates[model][2]:.2f} s;  1 thread / 12 = {rates[model][3]:.2f} (range {min(ratio):.1f}-{max(ratio):.1f});"
          f"  tokens in per crop <= {max(r['server']['prompt_n'] for r in recs)}")

print()
for eng, model in MODEL.items():
    pre, dec, over, ratio = rates[model]
    per, tins, touts = [], [], []
    for f in sorted(glob.glob(str(S2 / "vlm_raw" / eng / "*.json"))):
        r = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        reqs = [(x["tokens_in"], x["tokens_out"]) for x in r["regions"]] if r.get("regions") else [(r["tokens_in"], r["tokens_out"])]
        per.append(sum(over + i / pre + o / dec for i, o in reqs) + (DET["default"] if r.get("regions") else 0))
        tins.append(sum(i for i, _ in reqs))
        touts.append(sum(o for _, o in reqs))
    det1 = DET["1"] if "regions" in eng else 0
    one = [(p - (DET["default"] if "regions" in eng else 0)) * ratio + det1 for p in per]
    print(f"{eng:15s} pages={len(per)}  tokens in/out {statistics.mean(tins):5.0f}/{statistics.mean(touts):4.0f}"
          f"  12 threads: mean {statistics.mean(per):5.1f} s (max {max(per):5.1f})"
          f"  1 thread: mean {statistics.mean(one):5.0f} s (max {max(one):4.0f})")
