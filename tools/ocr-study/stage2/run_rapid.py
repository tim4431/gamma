"""RapidOCR 3.10 (PaddleOCR's PP-OCR models on ONNX Runtime).

  python -I run_rapid.py --version v6 --size small|medium|tiny [--side 2000] [--threads N] [ids...]

`--side` is Global.max_side_len, the longest side the page is shrunk to
before detection (2000 by default; a 300-dpi letter page is 3300). Word boxes
come from return_word_box."""

import argparse
import time

import numpy as np
from PIL import Image
from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
from common import image_path, load_pages, select, write

ap = argparse.ArgumentParser()
ap.add_argument("--version", default="v6")
ap.add_argument("--size", default="small")
ap.add_argument("--side", type=int, default=2000)
ap.add_argument("--threads", type=int, default=-1)
ap.add_argument("ids", nargs="*")
a = ap.parse_args()
ver = {"v6": OCRVersion.PPOCRV6, "v5": OCRVersion.PPOCRV5}[a.version]
size = {"tiny": ModelType.TINY, "small": ModelType.SMALL, "medium": ModelType.MEDIUM,
        "mobile": ModelType.MOBILE, "server": ModelType.SERVER}[a.size]
engine = f"rapid-{a.version}-{a.size}" + ("" if a.side == 2000 else f"-s{a.side}") + (f"-t{a.threads}" if a.threads > 0 else "")
params = {
    "Global.max_side_len": a.side,
    "Global.log_level": "warning",
    "Det.ocr_version": ver, "Det.model_type": size,
    "Rec.ocr_version": ver, "Rec.model_type": size,
    "EngineConfig.onnxruntime.intra_op_num_threads": a.threads,
}
if a.version == "v6":
    # One multilingual model per size (its supported_langs list English,
    # French and German); the default lang_type resolves to it.
    pass
else:
    params["Det.lang_type"] = LangDet.MULTI
    params["Rec.lang_type"] = LangRec.LATIN
ocr = RapidOCR(params=params)

for p in select(load_pages(), a.ids):
    img = np.array(Image.open(image_path(p["id"])).convert("RGB"))
    t = time.perf_counter()
    res = ocr(img, return_word_box=True)
    dt = time.perf_counter() - t
    out = []
    boxes = res.boxes if res.boxes is not None else []
    words_of = res.word_results or [None] * len(boxes)
    for box, txt, wr in zip(boxes, res.txts or [], words_of):
        xs, ys = box[:, 0] * 0.24, box[:, 1] * 0.24
        words = []
        for item in wr or []:
            wt, _score, wb = item
            if wb is None or not str(wt).strip():
                continue
            wb = np.asarray(wb)
            words.append({"text": str(wt), "bbox": [round(float(v), 2) for v in (wb[:, 0].min() * 0.24, wb[:, 1].min() * 0.24, wb[:, 0].max() * 0.24, wb[:, 1].max() * 0.24)]})
        out.append({"text": str(txt), "bbox": [round(float(v), 2) for v in (xs.min(), ys.min(), xs.max(), ys.max())], "words": words})
    write(engine, p["id"], out, dt)
    print(engine, p["id"], f"{dt:.2f}s", len(out), "lines")
