"""Tiles of each Stage 3 page with every Tesseract line boxed and numbered,
for marking which lines are display equations."""
import json
from PIL import Image, ImageDraw, ImageFont
import sys as _s, pathlib as _p; _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
from common import ROOT
TWO = {"holstein", "kitagawa", "naca", "jdp"}
TILE_H, OVER = 900, 80
try:
    font = ImageFont.truetype("arialbd.ttf", 26)
except OSError:
    font = ImageFont.load_default()
pages = json.loads((ROOT / "pages.json").read_text(encoding="utf-8"))
for p in pages:
    pid = p["id"]
    img = Image.open(ROOT / "img" / f"{pid}.png").convert("RGB")
    lines = json.loads((ROOT / "lines" / f"{pid}.json").read_text(encoding="utf-8"))
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        x0, y0, x1, y1 = l["box"]
        color = (220, 30, 30) if i % 2 else (30, 90, 220)
        d.rectangle([x0 - 3, y0 - 3, x1 + 3, y1 + 3], outline=color, width=2)
        d.text((max(0, x0 - 46), y0 - 4), str(i), fill=color, font=font)
    w, h = img.size
    if p["file"] in TWO:
        # the gutter: the x crossed by fewest line boxes in the middle third
        best = min(range(int(w * 0.35), int(w * 0.65), 4),
                   key=lambda x: sum(1 for l in lines if l["box"][0] < x < l["box"][2]))
        cols = [("L", 0, best + 60), ("R", best - 60, w)]
    else:
        cols = [("", 0, w)]
    out = ROOT / "tiles" / pid
    out.mkdir(parents=True, exist_ok=True)
    k = 0
    for name, x0, x1 in cols:
        y, n = 0, 1
        while y < h:
            y1 = min(h, y + TILE_H)
            img.crop((x0, y, x1, y1)).save(out / f"{name}{n}.png")
            k += 1
            if y1 >= h:
                break
            y, n = y1 - OVER, n + 1
    (out / "lines.txt").write_text("\n".join(f"{i}\t{l['box']}\t{l['text']}" for i, l in enumerate(lines)), encoding="utf-8")
print(len(pages), "pages tiled")
