"""Tesseract 5.5.4 through the tesserocr 2.13.0 wheel (libtesseract bundled),
lines and words from Tesseract's own result iterator, in its reading order.

  OMP_THREAD_LIMIT=1 python -I run_tesserocr.py --data fast [--lang eng+fra+deu] [--name ENGINE] [ids...]
"""

import argparse
import pathlib
import sys
import time

from PIL import Image
from tesserocr import PSM, RIL, PyTessBaseAPI, iterate_level

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import image_path, load_pages, px_box, select, tessdata as tessdata_dir, write  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="fast")
ap.add_argument("--lang", default=None)
ap.add_argument("--name", default=None)
ap.add_argument("ids", nargs="*")
a = ap.parse_args()
engine = a.name or f"tesserocr-{a.data}" + ("-multi" if a.lang else "")
tessdata = str(tessdata_dir(a.data))
apis = {}

for p in select(load_pages(), a.ids):
    lang = a.lang or p["lang"]
    if lang not in apis:
        apis[lang] = PyTessBaseAPI(path=tessdata, lang=lang, psm=PSM.AUTO)
    api = apis[lang]
    img = Image.open(image_path(p["id"]))
    t = time.perf_counter()
    api.SetImage(img)
    api.Recognize()
    lines, cur = [], None
    for w in iterate_level(api.GetIterator(), RIL.WORD):
        text = w.GetUTF8Text(RIL.WORD)
        if text is None:
            continue
        if cur is None or w.IsAtBeginningOf(RIL.TEXTLINE):
            cur = []
            lines.append(cur)
        if text.strip():
            cur.append({"text": text, "bbox": px_box(w.BoundingBox(RIL.WORD)), "conf": round(w.Confidence(RIL.WORD), 1)})
    dt = time.perf_counter() - t
    out = []
    for ws in lines:
        if not ws:
            continue
        xs0, ys0, xs1, ys1 = zip(*(w["bbox"] for w in ws))
        out.append({"text": " ".join(w["text"] for w in ws), "bbox": [min(xs0), min(ys0), max(xs1), max(ys1)], "words": ws})
    write(engine, p["id"], out, dt)
    print(engine, p["id"], f"{dt:.2f}s", len(out), "lines")
for api in apis.values():
    api.End()
