"""Render each study page at 300 dpi in grey (the OCR text is invisible, so
the pixels are the scan alone) and write the existing layer as the "layer"
engine: PyMuPDF's words in stream order, grouped into rows."""

import json
import sys
import time

import pymupdf

import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
from common import DPI, IMG, group_rows, load_pages, select, write

IMG.mkdir(parents=True, exist_ok=True)
for p in select(load_pages(), sys.argv[1:]):
    doc = pymupdf.open(p["path"])
    page = doc[p["page"] - 1]
    t = time.perf_counter()
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY, alpha=False)
    render_s = time.perf_counter() - t
    pix.save(IMG / f"{p['id']}.png")
    r = page.rect
    (IMG / f"{p['id']}.json").write_text(json.dumps({
        "id": p["id"], "path": p["path"], "page": p["page"], "rotation": page.rotation,
        "width": r.width, "height": r.height, "px": [pix.width, pix.height],
        "render_s": round(render_s, 3),
    }), encoding="utf-8")
    words = [{"text": w[4], "bbox": [round(v, 2) for v in w[:4]]}
             for w in page.get_text("words", sort=False)]
    write("layer", p["id"], group_rows(words), 0.0)
    print(p["id"], pix.width, "x", pix.height, f"render {render_s:.2f}s", len(words), "layer words")
