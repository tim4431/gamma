"""Cut the inline formulas PP-DocLayoutV3 found (--keep-inline run) out of some pages.

  python -I cut_inline.py <page> [<page> ...]

Writes $OCR_STUDY_DIR/stage3/crops_inline/<page>-i<k>.png (the box with a
6 px margin, grey) and crops_inline/index.json [{id, page, box, score}]."""

import json
import pathlib
import sys

from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402

s3 = ROOT
out = s3 / "crops_inline"
out.mkdir(exist_ok=True)
index = []
for page in sys.argv[1:]:
    im = Image.open(s3 / "img" / f"{page}.png").convert("L")
    boxes = [b for b in json.loads((s3 / "det_inline" / "pp-doclayoutv3" / f"{page}.json").read_text(encoding="utf-8"))
             if b["cls"] == "inline_formula" and b["score"] >= 0.5]
    boxes.sort(key=lambda b: (b["box"][1], b["box"][0]))
    for k, b in enumerate(boxes, 1):
        x0, y0, x1, y1 = b["box"]
        cid = f"{page}-i{k}"
        im.crop((max(0, int(x0) - 6), max(0, int(y0) - 6), min(im.width, int(x1) + 6), min(im.height, int(y1) + 6))).save(out / f"{cid}.png")
        index.append({"id": cid, "page": page, "box": b["box"], "score": b["score"]})
(out / "index.json").write_text(json.dumps(index, indent=0), encoding="utf-8")
print(len(index), "inline crops")
