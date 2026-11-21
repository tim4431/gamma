"""Cut the smoke-test equation crops from lounis-p8.png.

Each rough box (page pixels at 300 dpi) is tightened to the ink inside it and padded
with white, so the crop holds the equation without its number. Output: grey PNGs in
$OCR_STUDY_DIR/stage3/engines/crops/smoke/<crop_id>.png and a crops.json with the final boxes.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

HERE = common.ENGINES  # $OCR_STUDY_DIR/stage3/engines
S3 = common.S3
OUT = HERE / "crops" / "smoke"
PAD = 12
INK = 140  # grey level below which a pixel counts as ink

# crop_id: (page, rough box x0, y0, x1, y1) -- x1 stops before the equation number (x ~ 1800)
ROUGH = {
    "lounis-p8-e3_16": ("lounis-p8", 300, 390, 1760, 580),   # 2x2 matrix = (1/hbar) inverse matrix
    "lounis-p8-e3_17": ("lounis-p8", 400, 712, 1760, 842),   # T_fi = hbar Omega1 Omega / 4 * (d1-d2)/D
    "lounis-p8-e3_19": ("lounis-p8", 400, 1122, 1760, 1176), # D = (d1 - zI)(d1 - zII)
    "lounis-p8-e3_20": ("lounis-p8", 400, 1280, 1760, 1390), # long two-fraction product with brackets
    "lounis-p8-e3_21": ("lounis-p8", 400, 2058, 1760, 2214), # |T_fi|^2 with nested fraction in brackets
}


def tighten(a: np.ndarray, x0, y0, x1, y1):
    sub = a[y0:y1, x0:x1] < INK
    ys, xs = np.nonzero(sub)
    return x0 + xs.min(), y0 + ys.min(), x0 + xs.max() + 1, y0 + ys.max() + 1


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {}
    for cid, (page, x0, y0, x1, y1) in ROUGH.items():
        im = Image.open(S3 / "img" / f"{page}.png").convert("L")
        a = np.array(im)
        bx0, by0, bx1, by1 = tighten(a, x0, y0, x1, y1)
        box = [int(max(0, bx0 - PAD)), int(max(0, by0 - PAD)), int(min(im.width, bx1 + PAD)), int(min(im.height, by1 + PAD))]
        im.crop(box).save(OUT / f"{cid}.png", dpi=(300, 300))
        meta[cid] = {"page": page, "box": [int(v) for v in box], "size": [box[2] - box[0], box[3] - box[1]]}
        print(cid, meta[cid])
    (OUT / "crops.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
