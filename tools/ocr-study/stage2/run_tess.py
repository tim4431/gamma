"""Native Tesseract 5.5.4 (conda-forge) over the rendered pages.

  python -I run_tess.py --data best|fast [--threads N] [--psm 3] [--name ENGINE] [ids...]

TSV output; words grouped by (block, par, line). The time is the process's
wall time (model load included, ~0.2 s). The executable is OCR_STUDY_TESSERACT
(default `tesseract` on PATH), the models OCR_STUDY_TESSDATA_FAST or _BEST."""

import argparse
import csv
import io
import os
import subprocess
import sys
import time

import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
from common import image_path, load_pages, px_box, select, tessdata as tessdata_dir, threads_env, write

EXE = os.environ.get("OCR_STUDY_TESSERACT") or "tesseract"

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="best")
ap.add_argument("--threads", type=int, default=0)
ap.add_argument("--psm", default="3")
ap.add_argument("--name", default=None)
ap.add_argument("--lang", default=None, help="override every page's language")
ap.add_argument("ids", nargs="*")
a = ap.parse_args()
engine = a.name or f"tess-{a.data}" + (f"-t{a.threads}" if a.threads else "")
tessdata = tessdata_dir(a.data)
env = threads_env(a.threads) if a.threads else None

for p in select(load_pages(), a.ids):
    cmd = [str(EXE), str(image_path(p["id"])), "stdout", "-l", a.lang or p["lang"], "--psm", a.psm,
           "--tessdata-dir", str(tessdata), "-c", "tessedit_create_tsv=1", "-c", "tessedit_pageseg_mode=" + a.psm]
    t = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True, env=env)
    dt = time.perf_counter() - t
    if r.returncode:
        print(p["id"], "FAILED", r.stderr.decode("utf-8", "replace")[:300], file=sys.stderr)
        continue
    rows = csv.DictReader(io.StringIO(r.stdout.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE)
    lines, keys = {}, []
    for row in rows:
        if row["level"] != "5" or not (row.get("text") or "").strip():
            continue
        k = (row["block_num"], row["par_num"], row["line_num"])
        if k not in lines:
            lines[k] = []
            keys.append(k)
        x, y, w, h = (int(row[c]) for c in ("left", "top", "width", "height"))
        lines[k].append({"text": row["text"], "bbox": px_box([x, y, x + w, y + h]), "conf": float(row["conf"])})
    out = []
    for k in keys:
        ws = lines[k]
        xs0, ys0, xs1, ys1 = zip(*(w["bbox"] for w in ws))
        out.append({"text": " ".join(w["text"] for w in ws), "bbox": [min(xs0), min(ys0), max(xs1), max(ys1)], "words": ws})
    write(engine, p["id"], out, dt)
    print(engine, p["id"], f"{dt:.2f}s", len(out), "lines")
