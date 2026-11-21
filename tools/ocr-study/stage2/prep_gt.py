"""Prepare ground-truth work for one draft engine, under $OCR_STUDY_DIR/stage2:
  gtprep/<page>/tiles/<tile>.png   full-resolution pieces of the 300-dpi page
  gtprep/<page>/draft_<tag>.txt    the draft engine's lines, by tile, with
                                   other engines' differing readings

  python -I prep_gt.py --draft tess-best --tag A --alts rapid-v6-small,onnxtr-db_resnet50-parseq [ids...]
"""

import argparse
import json
import pathlib
import sys

from PIL import Image
from rapidfuzz import fuzz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import IMG, OUT, PREP, SCALE, load_pages, select  # noqa: E402

COLUMNS = {"lounis": 1, "hansch": 2, "holstein": 2, "kitagawa": 2, "feshbach": 1, "naca": 2, "kollin": 2,
           "jdp": 2, "physrev": 1, "annalen": 1, "siegman": 1, "shannon": 1}
TILE_H = 760  # px of the 300-dpi page per tile
OVERLAP = 70

ap = argparse.ArgumentParser()
ap.add_argument("--draft", required=True)
ap.add_argument("--tag", required=True)
ap.add_argument("--alts", default="")
ap.add_argument("ids", nargs="*")
a = ap.parse_args()
alts = [e for e in a.alts.split(",") if e]


def lines_of(engine, pid):
    f = OUT / engine / f"{pid}.json"
    return json.loads(f.read_text(encoding="utf-8"))["lines"] if f.exists() else []


def gutter(lines, W):
    """x (pt) between 0.35 and 0.65 of the width crossed by fewest lines."""
    best, at = None, W / 2
    for i in range(70):
        x = W * (0.35 + 0.3 * i / 69)
        n = sum(1 for l in lines if l["bbox"][0] < x - 2 and l["bbox"][2] > x + 2)
        if best is None or n < best:
            best, at = n, x
    return at


for p in select(load_pages(), a.ids):
    pid = p["id"]
    meta = json.loads((IMG / f"{pid}.json").read_text(encoding="utf-8"))
    W, H = meta["width"], meta["height"]
    img = Image.open(IMG / f"{pid}.png")
    pw, ph = img.size
    draft = lines_of(a.draft, pid)
    base = PREP / pid
    tiles_dir = base / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    cols = COLUMNS[p["file"]]
    if cols == 2:
        g = gutter(draft + lines_of("layer", pid), W) / SCALE
        xs = [("L", 0, int(g) + 40), ("R", int(g) - 40, pw)]
    else:
        xs = [("", 0, pw)]
    tiles = []
    if not (tiles_dir / "index.json").exists():
        for name, x0, x1 in xs:
            y, k = 0, 1
            while y < ph:
                y1 = min(ph, y + TILE_H)
                tid = f"{name}{k}"
                img.crop((x0, y, x1, y1)).save(tiles_dir / f"{tid}.png")
                tiles.append({"tile": tid, "box_px": [x0, y, x1, y1]})
                if y1 >= ph:
                    break
                y, k = y1 - OVERLAP, k + 1
        (tiles_dir / "index.json").write_text(json.dumps(tiles), encoding="utf-8")
    else:
        tiles = json.loads((tiles_dir / "index.json").read_text(encoding="utf-8"))

    def tile_of(l):
        cx = (l["bbox"][0] + l["bbox"][2]) / 2 / SCALE
        cy = (l["bbox"][1] + l["bbox"][3]) / 2 / SCALE
        best = None
        for t in tiles:
            x0, y0, x1, y1 = t["box_px"]
            if x0 <= cx < x1 and y0 <= cy < y1:
                # the tile whose centre is nearest vertically (overlaps)
                d = abs((y0 + y1) / 2 - cy)
                if best is None or d < best[0]:
                    best = (d, t["tile"])
        return best[1] if best else tiles[0]["tile"]

    alt_lines = {e: lines_of(e, pid) for e in alts}

    def alternatives(l):
        out = []
        y0, y1 = l["bbox"][1], l["bbox"][3]
        for e, ls in alt_lines.items():
            cands = [m for m in ls if min(y1, m["bbox"][3]) - max(y0, m["bbox"][1]) > 0.3 * (y1 - y0)
                     and min(l["bbox"][2], m["bbox"][2]) > max(l["bbox"][0], m["bbox"][0])]
            if not cands:
                continue
            m = max(cands, key=lambda m: fuzz.partial_ratio(l["text"], m["text"]))
            if m["text"] != l["text"]:
                out.append(m["text"])
        return list(dict.fromkeys(out))

    by_tile = {t["tile"]: [] for t in tiles}
    for l in draft:
        by_tile[tile_of(l)].append(l)
    order = [t["tile"] for t in tiles]
    with open(base / f"draft_{a.tag}.txt", "w", encoding="utf-8") as f:
        f.write(f"# {pid}: draft from engine {a.draft}. Tiles in reading order: {', '.join(order)}\n")
        for t in tiles:
            f.write(f"\n## tile {t['tile']}  (page pixels x {t['box_px'][0]}-{t['box_px'][2]}, y {t['box_px'][1]}-{t['box_px'][3]})\n")
            for l in sorted(by_tile[t["tile"]], key=lambda l: l["bbox"][1]):
                f.write(l["text"] + "\n")
                for alt in alternatives(l):
                    f.write(f"    ~ {alt}\n")
    print(pid, len(tiles), "tiles", len(draft), "draft lines")
