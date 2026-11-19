"""Where the two ground-truth passes disagree, with a crop of each line.

  python -I diff_gt.py <page id> [...]

Under $OCR_STUDY_DIR/stage2:
reads gtprep/gt_A/<page>.txt and gtprep/gt_B/<page>.txt, aligns their lines
(after the scorer's normalization), and writes gtprep/<page>/diff.txt and
gtprep/<page>/diff/<n>.png: for each disagreement both readings, the line
numbers in each file, and a crop of the page around the line (found by
matching the text against every engine's lines on that page)."""

import difflib
import json
import pathlib
import sys

from PIL import Image
from rapidfuzz import fuzz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import IMG, OUT, PREP, SCALE  # noqa: E402
from score import norm  # noqa: E402


def read(path):
    out = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if raw.strip() and not raw.lstrip().startswith("#"):
            out.append((i, raw.rstrip()))
    return out


def key(s):
    return norm(s)


def locate(pid, texts):
    """The bbox (pt) of the engine line that best matches any of `texts`."""
    best = (0, None)
    for d in OUT.iterdir():
        f = d / f"{pid}.json"
        if not f.exists():
            continue
        for l in json.loads(f.read_text(encoding="utf-8"))["lines"]:
            for t in texts:
                t = t[2:].strip() if t.startswith("§") else t
                if len(t) < 3:
                    continue
                s = fuzz.ratio(norm(t), norm(l["text"])) if len(l["text"]) < 2.5 * len(t) else fuzz.partial_ratio(norm(t), norm(l["text"])) * 0.9
                if s > best[0]:
                    best = (s, l["bbox"])
    return best[1] if best[0] >= 55 else None


for pid in sys.argv[1:]:
    A, B = read(PREP / "gt_A" / f"{pid}.txt"), read(PREP / "gt_B" / f"{pid}.txt")
    sm = difflib.SequenceMatcher(a=[key(t) for _, t in A], b=[key(t) for _, t in B], autojunk=False)
    img = Image.open(IMG / f"{pid}.png")
    ddir = PREP / pid / "diff"
    ddir.mkdir(parents=True, exist_ok=True)
    items = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        a_part, b_part = A[i1:i2], B[j1:j2]
        # pair lines inside a replaced run by best similarity, in order
        used, pairs = set(), []
        for ai, (an, at) in enumerate(a_part):
            cands = [(fuzz.ratio(key(at), key(bt)), bi) for bi, (_, bt) in enumerate(b_part) if bi not in used]
            s, bi = max(cands) if cands else (0, None)
            if bi is not None and s >= 50:
                used.add(bi)
                pairs.append((a_part[ai], b_part[bi]))
            else:
                pairs.append((a_part[ai], None))
        pairs += [(None, b_part[bi]) for bi in range(len(b_part)) if bi not in used]
        for a_ln, b_ln in pairs:
            texts = [x[1] for x in (a_ln, b_ln) if x]
            bbox = locate(pid, texts)
            n = len(items) + 1
            crop = None
            if bbox:
                x0, y0, x1, y1 = (v / SCALE for v in bbox)
                h = y1 - y0
                box = (max(0, x0 - 60), max(0, y0 - 1.2 * h), min(img.width, x1 + 60), min(img.height, y1 + 1.2 * h))
                img.crop(tuple(int(v) for v in box)).save(ddir / f"{n}.png")
                crop = f"diff/{n}.png"
            items.append({"n": n, "A": a_ln, "B": b_ln, "crop": crop})
    with open(PREP / pid / "diff.txt", "w", encoding="utf-8") as f:
        f.write(f"# {pid}: {len(items)} disagreements between gt_A and gt_B ({len(A)} and {len(B)} lines)\n")
        for it in items:
            f.write(f"\n[{it['n']}] crop: {it['crop'] or 'none (not located; use the tiles)'}\n")
            f.write(f"  A line {it['A'][0]}: {it['A'][1]}\n" if it["A"] else "  A: (no line)\n")
            f.write(f"  B line {it['B'][0]}: {it['B'][1]}\n" if it["B"] else "  B: (no line)\n")
    print(pid, len(A), len(B), "lines;", len(items), "disagreements")
