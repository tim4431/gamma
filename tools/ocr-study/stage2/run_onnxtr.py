"""OnnxTR 0.9 (docTR's models on ONNX Runtime).

  python -I run_onnxtr.py --det db_resnet50 --reco parseq [--threads N] [ids...]
"""

import argparse
import time

from onnxtr.io import DocumentFile
from onnxtr.models import EngineConfig, ocr_predictor

import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
from common import image_path, load_pages, meta, select, write

ap = argparse.ArgumentParser()
ap.add_argument("--det", default="db_resnet50")
ap.add_argument("--reco", default="parseq")
ap.add_argument("--threads", type=int, default=0)
ap.add_argument("ids", nargs="*")
a = ap.parse_args()
engine = f"onnxtr-{a.det}-{a.reco}" + (f"-t{a.threads}" if a.threads else "")

cfg = None
if a.threads:
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = a.threads
    so.inter_op_num_threads = 1
    cfg = EngineConfig(session_options=so)
kw = {"det_engine_cfg": cfg, "reco_engine_cfg": cfg, "clf_engine_cfg": cfg} if cfg else {}
model = ocr_predictor(det_arch=a.det, reco_arch=a.reco, assume_straight_pages=True,
                      detect_orientation=False, straighten_pages=False, **kw)

for p in select(load_pages(), a.ids):
    m = meta(p["id"])
    W, H = m["width"], m["height"]
    doc = DocumentFile.from_images([str(image_path(p["id"]))])
    t = time.perf_counter()
    res = model(doc)
    dt = time.perf_counter() - t
    out = []
    for block in res.export()["pages"][0]["blocks"]:
        for line in block["lines"]:
            words = []
            for w in line["words"]:
                (x0, y0), (x1, y1) = w["geometry"]
                words.append({"text": w["value"], "bbox": [round(x0 * W, 2), round(y0 * H, 2), round(x1 * W, 2), round(y1 * H, 2)]})
            if not words:
                continue
            xs0, ys0, xs1, ys1 = zip(*(w["bbox"] for w in words))
            out.append({"text": " ".join(w["text"] for w in words), "bbox": [min(xs0), min(ys0), max(xs1), max(ys1)], "words": words})
    write(engine, p["id"], out, dt)
    print(engine, p["id"], f"{dt:.2f}s", len(out), "lines")
