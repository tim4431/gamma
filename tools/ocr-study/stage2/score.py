"""Score engine outputs ($OCR_STUDY_DIR/stage2/out) against the ground truth
($OCR_STUDY_DIR/stage2/gt/<page id>.txt).

  python -I score.py [--engines a,b,...] [--pages ids] [--json out.json]

Ground truth: one printed line per line, in reading order. A line tagged
"§M " is display math, scored apart; "§F " is text inside a figure, ignored;
blank lines and lines starting with "#" are ignored.

Measures, after the same normalization of both sides (NFKC, so ligatures,
super- and subscript digits become plain characters; quotes, dashes and
minus signs unified; whitespace collapsed):
- cer: each truth line is aligned to the cheapest substring of the engine's
  page text (its lines joined by newlines), edit distance over truth
  characters. Reading order, columns and line merges cost nothing; text the
  engine invents is not counted here (see prec).
- wrec / wprec: words of the truth found in the engine's text, and words of
  the engine's text found in the truth (multisets; punctuation stripped from
  word ends; case kept).
"""

import argparse
import json
import pathlib
import re
import sys
import unicodedata
from collections import Counter

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import GT, OUT  # noqa: E402

TRANS = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'", "´": "'",
    "“": '"', "”": '"', "„": '"', "″": '"', "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "­": None, "​": None, "﻿": None,
})


def norm(s):
    s = unicodedata.normalize("NFKC", s).translate(TRANS)
    return re.sub(r"\s+", " ", s).strip()


def read_gt(pid):
    text, math = [], []
    for raw in (GT / f"{pid}.txt").read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("§F"):
            continue
        if line.startswith("§M"):
            s = norm(line[2:])
            if s:
                math.append(s)
            continue
        s = norm(line)
        if s:
            text.append(s)
    return text, math


def engine_text(engine, pid):
    f = OUT / engine / f"{pid}.json"
    if not f.exists():
        return None, None
    rec = json.loads(f.read_text(encoding="utf-8"))
    return [norm(l["text"]) for l in rec["lines"] if norm(l["text"])], rec.get("time")


def substring_distance(a, b):
    """Edit distance from a to its best-matching substring of b (free
    leading and trailing text in b), vectorized over b."""
    if not a:
        return 0
    if not b:
        return len(a)
    bb = np.frombuffer(b.encode("utf-32-le"), dtype=np.uint32)
    m = len(bb)
    idx = np.arange(m + 1, dtype=np.int64)
    prev = np.zeros(m + 1, dtype=np.int64)  # row 0: substring may start anywhere
    for ch in a:
        c = ord(ch)
        sub = prev[:-1] + (bb != c)
        e = np.empty(m + 1, dtype=np.int64)
        e[0] = prev[0] + 1
        e[1:] = np.minimum(prev[1:] + 1, sub)
        # insertions along the row: cur[j] = min_k<=j (e[k] + j - k)
        prev = np.minimum.accumulate(e - idx) + idx
    return int(prev.min())


def best_span(a, b):
    """(distance, start, end) of a's best-matching substring b[start:end]."""
    if not a or not b:
        return len(a), 0, 0
    bb = np.frombuffer(b.encode("utf-32-le"), dtype=np.uint32)
    m = len(bb)
    idx = np.arange(m + 1, dtype=np.int64)

    def last_row(chars, arr):
        prev = np.zeros(m + 1, dtype=np.int64)
        for ch in chars:
            sub = prev[:-1] + (arr != ord(ch))
            e = np.empty(m + 1, dtype=np.int64)
            e[0] = prev[0] + 1
            e[1:] = np.minimum(prev[1:] + 1, sub)
            prev = np.minimum.accumulate(e - idx) + idx
        return prev

    fwd = last_row(a, bb)
    end = int(fwd.argmin())
    dist = int(fwd[end])
    # the start: the same alignment run backwards from `end`
    rev = last_row(a[::-1], bb[::-1])
    # rev[k] is the cost of a ending k characters from the right end of b;
    # take the cheapest start at or before `end`
    k0 = m - end  # positions in the reversed string that begin at or after end
    starts = rev[k0:]
    k = int(starts.argmin()) + k0
    start = m - k
    return dist, start, end


def continuity(truth, lines):
    """Share of consecutive truth lines whose text follows directly in the
    engine's page text (at most 3 characters between them: a space, a
    newline, a hyphen). Pairs where either line matched poorly (over 30% of
    its characters) are left out."""
    page = "\n".join(lines)
    spans = []
    for t in truth:
        d, s, e = best_span(t, page)
        spans.append((d <= 0.3 * len(t), s, e))
    ok = n = 0
    for (g1, s1, e1), (g2, s2, e2) in zip(spans, spans[1:]):
        if not (g1 and g2):
            continue
        n += 1
        ok += 0 <= s2 - e1 <= 3
    return ok, n


WORD_END = re.compile(r"^[\W_]+|[\W_]+$")


def words(lines):
    out = Counter()
    for l in lines:
        for w in l.split(" "):
            w = WORD_END.sub("", w)
            if w:
                out[w] += 1
    return out


def score(truth, lines):
    page = "\n".join(lines)
    n = sum(len(t) for t in truth)
    dist = sum(substring_distance(t, page) for t in truth)
    tw, ew = words(truth), words(lines)
    hit = sum((tw & ew).values())
    cok, cn = continuity(truth, lines)
    return {"chars": n, "dist": dist, "cer": dist / n if n else 0.0, "cont_ok": cok, "cont_n": cn,
            "twords": sum(tw.values()), "ewords": sum(ew.values()), "hit": hit,
            "wrec": hit / max(1, sum(tw.values())), "wprec": hit / max(1, sum(ew.values()))}


def main():
    global GT
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="")
    ap.add_argument("--pages", default="")
    ap.add_argument("--gt", default=str(GT))
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    GT = pathlib.Path(a.gt)
    pids = [p for p in a.pages.split(",") if p] or sorted(f.stem for f in GT.glob("*.txt"))
    engines = [e for e in a.engines.split(",") if e] or sorted(d.name for d in OUT.iterdir() if d.is_dir())
    res = {}
    for e in engines:
        for pid in pids:
            truth, math = read_gt(pid)
            lines, t = engine_text(e, pid)
            if lines is None:
                continue
            r = {"text": score(truth, lines), "time": t}
            if math:
                r["math"] = score(math, lines)
            res.setdefault(e, {})[pid] = r
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(res, indent=1), encoding="utf-8")
    # table: engine x page CER (text), then totals
    w = max(len(e) for e in res) if res else 10
    print("CER % (text lines)".ljust(w), *(p.split("-")[0][:8].rjust(8) for p in pids), "   ALL  wrec wprec  cont")
    for e, per in res.items():
        cells, n, d, hit, tw, ew, co, cn = [], 0, 0, 0, 0, 0, 0, 0
        for pid in pids:
            r = per.get(pid)
            if not r:
                cells.append("-".rjust(8))
                continue
            s = r["text"]
            cells.append(f"{100 * s['cer']:8.2f}")
            n, d, hit, tw, ew = n + s["chars"], d + s["dist"], hit + s["hit"], tw + s["twords"], ew + s["ewords"]
            co, cn = co + s["cont_ok"], cn + s["cont_n"]
        print(e.ljust(w), *cells, f"{100 * d / max(1, n):6.2f} {100 * hit / max(1, tw):5.1f} {100 * hit / max(1, ew):5.1f} {100 * co / max(1, cn):5.1f}")


if __name__ == "__main__":
    main()
