"""Draw a page's detection boxes: python -I overlay.py <det dir> <page> <out.png> [cls,...]"""
import json, pathlib, sys
from PIL import Image, ImageDraw
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402
s3, det, page, out = ROOT, pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
keep = set(sys.argv[4].split(",")) if len(sys.argv) > 4 else None
im = Image.open(s3 / "img" / f"{page}.png").convert("RGB")
d = ImageDraw.Draw(im)
col = {"inline_formula": (230, 0, 0), "display_formula": (0, 90, 230), "formula_number": (0, 160, 0)}
n = 0
for b in json.loads((det / f"{page}.json").read_text(encoding="utf-8")):
    if keep and b["cls"] not in keep:
        continue
    d.rectangle(b["box"], outline=col.get(b["cls"], (150, 150, 150)), width=4); n += 1
im.resize((im.width // 2, im.height // 2)).save(out)
print(n, "boxes")
