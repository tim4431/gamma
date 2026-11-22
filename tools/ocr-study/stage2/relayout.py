"""A reading-order step any engine's words could go through: split lines at
gaps no word space makes, then order the lines by recursive cuts (a wide
horizontal gap first, then a column gutter), each leaf top to bottom.

  python -I relayout.py <engine> [...]   -> out/<engine>+order/
"""

import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import OUT, line_of  # noqa: E402

SPLIT_GAP = 1.5     # a gap over 1.5 line heights inside a line starts a new line
H_CUT = 1.2         # a horizontal cut needs this many line heights of white
V_CUT = 0.8         # a gutter needs this many line heights of white


def split_lines(lines):
    out = []
    for l in lines:
        ws = sorted(l["words"], key=lambda w: w["bbox"][0]) if l["words"] else []
        if not ws:
            out.append(l)
            continue
        h = statistics.median(w["bbox"][3] - w["bbox"][1] for w in ws) or 1
        cur = [ws[0]]
        for w in ws[1:]:
            if w["bbox"][0] - cur[-1]["bbox"][2] > SPLIT_GAP * h:
                out.append(line_of(cur))
                cur = []
            cur.append(w)
        out.append(line_of(cur))
    return out


def gaps(intervals, lo, hi):
    """White runs between merged intervals, as (start, end)."""
    iv = sorted(intervals)
    out, end = [], lo
    for a, b in iv:
        if a > end:
            out.append((end, a))
        end = max(end, b)
    return out


def order(lines, H):
    """Two columns when some x between 0.3 and 0.7 of the text width is
    crossed by few lines and has text on both sides: then the lines crossing
    it (or wider than 0.55 of the width) are separators, and between two
    separators the left column is read before the right. One column: top
    to bottom."""
    if len(lines) <= 2:
        return sorted(lines, key=lambda l: l["bbox"][1])
    x0 = min(l["bbox"][0] for l in lines)
    x1 = max(l["bbox"][2] for l in lines)
    W = x1 - x0
    best = None
    for i in range(81):
        x = x0 + W * (0.3 + 0.4 * i / 80)
        cross = sum(1 for l in lines if l["bbox"][0] < x - 1 and l["bbox"][2] > x + 1)
        left = sum(1 for l in lines if l["bbox"][2] <= x + 1)
        right = sum(1 for l in lines if l["bbox"][0] >= x - 1)
        if left >= 3 and right >= 3 and (best is None or cross < best[0]):
            best = (cross, x)
    by_y = sorted(lines, key=lambda l: (l["bbox"][1], l["bbox"][0]))
    if best is None or best[0] > 0.15 * len(lines):
        return by_y
    g = best[1]
    out, left, right = [], [], []

    def flush():
        out.extend(sorted(left, key=lambda l: l["bbox"][1]))
        out.extend(sorted(right, key=lambda l: l["bbox"][1]))
        left.clear()
        right.clear()

    for l in by_y:
        a, b = l["bbox"][0], l["bbox"][2]
        if (a < g - 1 and b > g + 1) or b - a > 0.55 * W:
            flush()
            out.append(l)
        elif b <= g + 1:
            left.append(l)
        else:
            right.append(l)
    flush()
    return out


def regroup(lines):
    """Lines rebuilt from the words alone, by geometry: a word joins the line
    it overlaps vertically by half the smaller height, starting at most a
    quarter height left of the line's end and at most SPLIT_GAP heights
    right of it; words taken left to right."""
    words = [w for l in lines for w in l["words"] if w["text"].strip()]
    words.sort(key=lambda w: (w["bbox"][0], w["bbox"][1]))
    open_lines = []  # [words]
    for w in words:
        x0, y0, x1, y1 = w["bbox"]
        h = y1 - y0 or 1
        best = None
        for ln in open_lines:
            lx0, ly0, lx1, ly1 = ln[-1]["bbox"]
            lh = ly1 - ly0 or 1
            ov = min(y1, ly1) - max(y0, ly0)
            mh = min(h, lh)
            gap = x0 - lx1
            if ov >= 0.5 * mh and -0.25 * mh <= gap <= SPLIT_GAP * max(h, lh):
                score = ov / mh - gap / (10 * mh)
                if best is None or score > best[0]:
                    best = (score, ln)
        if best:
            best[1].append(w)
        else:
            open_lines.append([w])
    return [line_of(ws) for ws in open_lines]


REGROUP = "--regroup" in sys.argv
for engine in [a for a in sys.argv[1:] if not a.startswith("--")]:
    src = OUT / engine
    dst = OUT / f"{engine}+{'lines' if REGROUP else 'order'}"
    dst.mkdir(exist_ok=True)
    for f in src.glob("*.json"):
        rec = json.loads(f.read_text(encoding="utf-8"))
        lines = regroup(rec["lines"]) if REGROUP else split_lines(rec["lines"])
        hs = [l["bbox"][3] - l["bbox"][1] for l in lines if l["words"]]
        H = statistics.median(hs) if hs else 10
        rec["lines"] = order(lines, H)
        rec["engine"] = dst.name
        (dst / f.name).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    print(dst.name, "written")
