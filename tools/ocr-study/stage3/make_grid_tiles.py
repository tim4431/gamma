"""Tiles of each Stage 3 page with a coordinate grid (page pixels at 300 dpi)
for marking display equations by their boxes."""
import json, shutil
from PIL import Image, ImageDraw, ImageFont
import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
from common import ROOT
TWO = {"holstein", "kitagawa", "naca", "jdp"}
TILE_H, OVER, STEP = 900, 80, 100
try:
    font = ImageFont.truetype("arial.ttf", 18)
except OSError:
    font = ImageFont.load_default()
pages = json.loads((ROOT / "pages.json").read_text(encoding="utf-8"))
root = ROOT / "grid"
if root.exists():
    shutil.rmtree(root)
index = {}
for p in pages:
    pid = p["id"]
    img = Image.open(ROOT / "img" / f"{pid}.png").convert("RGB")
    w, h = img.size
    lines = json.loads((ROOT / "lines" / f"{pid}.json").read_text(encoding="utf-8"))
    if p["file"] in TWO:
        best = min(range(int(w * 0.35), int(w * 0.65), 4),
                   key=lambda x: sum(1 for l in lines if l["box"][0] < x < l["box"][2]))
        cols = [("L", 0, best + 60), ("R", best - 60, w)]
    else:
        cols = [("", 0, w)]
    out = root / pid
    out.mkdir(parents=True)
    tiles = []
    for name, x0, x1 in cols:
        y, n = 0, 1
        while y < h:
            y1 = min(h, y + TILE_H)
            tile = img.crop((x0, y, x1, y1))
            d = ImageDraw.Draw(tile, "RGBA")
            for gx in range((x0 // STEP + 1) * STEP, x1, STEP):
                d.line([(gx - x0, 0), (gx - x0, y1 - y)], fill=(0, 160, 0, 70), width=1)
                d.text((gx - x0 + 2, 2), str(gx), fill=(0, 140, 0, 255), font=font)
            for gy in range((y // STEP + 1) * STEP, y1, STEP):
                d.line([(0, gy - y), (x1 - x0, gy - y)], fill=(0, 160, 0, 70), width=1)
                d.text((2, gy - y + 2), str(gy), fill=(0, 140, 0, 255), font=font)
            tid = f"{name}{n}"
            tile.save(out / f"{tid}.png")
            tiles.append({"tile": tid, "box": [x0, y, x1, y1]})
            if y1 >= h:
                break
            y, n = y1 - OVER, n + 1
    index[pid] = {"size": [w, h], "tiles": tiles}
(root / "index.json").write_text(json.dumps(index, indent=0), encoding="utf-8")
print(len(index), "pages,", sum(len(v["tiles"]) for v in index.values()), "tiles")
