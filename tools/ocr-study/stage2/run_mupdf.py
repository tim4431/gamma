"""MuPDF's built-in Tesseract through PyMuPDF (the server's pinned 1.28.2,
run with a Python that has it, and -I; nothing of gamma is imported).

  python -I run_mupdf.py --data best|fast [--dpi 300] [ids...]

The time covers get_textpage_ocr alone: MuPDF renders the page itself at
`dpi` and recognises it; the words come from that text page. The models are
OCR_STUDY_TESSDATA_FAST or _BEST."""

import argparse
import time

import pymupdf

import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
from common import load_pages, select, tessdata as tessdata_dir, write

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="best")
ap.add_argument("--dpi", type=int, default=300)
ap.add_argument("--name-suffix", default="")
ap.add_argument("ids", nargs="*")
a = ap.parse_args()
engine = f"mupdf-{a.data}" + ("" if a.dpi == 300 else f"-{a.dpi}") + a.name_suffix
tessdata = str(tessdata_dir(a.data))

for p in select(load_pages(), a.ids):
    doc = pymupdf.open(p["path"])
    page = doc[p["page"] - 1]
    t = time.perf_counter()
    tp = page.get_textpage_ocr(language=p["lang"], dpi=a.dpi, full=True, tessdata=tessdata)
    dt = time.perf_counter() - t
    lines, keys = {}, []
    for x0, y0, x1, y1, text, b, ln, _ in page.get_text("words", textpage=tp, sort=False):
        k = (b, ln)
        if k not in lines:
            lines[k] = []
            keys.append(k)
        lines[k].append({"text": text, "bbox": [round(v, 2) for v in (x0, y0, x1, y1)]})
    out = []
    for k in keys:
        ws = lines[k]
        xs0, ys0, xs1, ys1 = zip(*(w["bbox"] for w in ws))
        out.append({"text": " ".join(w["text"] for w in ws), "bbox": [min(xs0), min(ys0), max(xs1), max(ys1)], "words": ws})
    write(engine, p["id"], out, dt, pymupdf=pymupdf.VersionBind)
    print(engine, p["id"], f"{dt:.2f}s", len(out), "lines")
