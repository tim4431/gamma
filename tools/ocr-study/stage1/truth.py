"""Ink ground truth for the OCR study.

usage: python -I truth.py [--file ID] [--page N] [--overlay] [--crop y0,y1]
  reads  pages.json, $OCR_STUDY_DIR/corpus/<file>.pdf and
         $OCR_STUDY_DIR/stage1/words/<file>-p<N>.json (dump_words.mjs)
  writes $OCR_STUDY_DIR/stage1/truth/<file>-p<N>.json, and with --overlay
         $OCR_STUDY_DIR/stage1/overlays/<file>-p<N>.png (crop = fractions of
         the page height)
  ($OCR_STUDY_DIR defaults to tools/ocr-study/.work)

The page is rendered at 200 dpi in grey. The OCR text is invisible (render
mode 3), so the pixels are the scan. Every word (token of the selection
string, tokens.mjs) is placed on the page from its pdf.js item: x by the
item's width, split by characters inside a multi-word item; y by the matrix,
where a mirrored matrix (d < 0, the Lounis CTM) hangs the print DOWN from f
([f - |d|, f]) and an upright one sits on f ([f - .22h, f + .75h]).

Lines: tokens in stream order, broken at a "\n", at a vertical jump of more
than 0.6 x the line's height, or where x runs back.

Line ink band (top of ascenders to bottom of descenders): the rows over the
line's x-range around its centre; grow from the most inked row near the
centre while a row has >= max(2, 1% of the width) dark pixels. A band taller
than 1.4 x the page's median band (touching lines, math, sub/superscripts)
or shorter than 0.45 x is marked not confident.

Word ink x-extent: the dark columns over the band; between two neighbouring
words the cut is the widest blank run whose middle lies between the two
words' estimated centres; at a line's ends, the nearest blank run (from the
word's centre outwards) at least a word gap wide (Twd = .55 x the median cut,
held in [.12, .45] x the band). The word's ink is the dark columns between its
two cuts. Word confident when: both cuts are real gaps >= Twd; no blank run
inside the word reaches .8 x its narrower side gap; the ink is 0.45-2.3 x the
estimated width and holds the estimated centre; the token has a letter or
digit and its ink per char is 0.1-1.6 band heights; the line band is
confident.

Truth coordinates are PDF user space (y up): line top/bottom, word x0/x1.
"""
import json
import os
import re
import statistics as st
import sys

import numpy as np
import pymupdf
from PIL import Image, ImageDraw

pymupdf.TOOLS.mupdf_display_errors(False)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.abspath(os.environ.get("OCR_STUDY_DIR") or os.path.join(HERE, "..", ".work"))
STUDY = os.path.join(WORK, "stage1")
DPI = 200
S = DPI / 72.0


def is_rot(tr):
    a, b, c, d = tr[:4]
    return abs(b) > abs(a) * 0.05 or a < 0 or abs(c) > abs(d) * 0.05 + 1e-9


def est_boxes(W):
    """Estimated print box of every token in pixel coords: x0, x1, ytop, ybot."""
    items = W["tc"]["items"]
    T = W["vp"]["transform"]
    out = []
    for tk in W["tokens"]:
        xs0, xs1, ys0, ys1, hs = [], [], [], [], []
        rot = False
        for i, c0, c1 in tk["parts"]:
            it = items[i]
            tr = it["transform"]
            if is_rot(tr):
                rot = True
                continue
            n = max(len(it["str"]), 1)
            w = it["width"]
            x0 = tr[4] + w * c0 / n
            x1 = tr[4] + w * c1 / n
            d, f = tr[3], tr[5]
            h = abs(d)
            if d < 0:
                y0, y1 = f - h, f
            else:
                y0, y1 = f - 0.22 * h, f + 0.75 * h
            xs0.append(x0); xs1.append(x1); ys0.append(y0); ys1.append(y1); hs.append(h)
        if rot or not xs0:
            out.append(None)
            continue
        ux0, ux1, uy0, uy1 = min(xs0), max(xs1), min(ys0), max(ys1)
        # user -> viewport -> pixels (rotate 0: no shear)
        px0 = (T[0] * ux0 + T[4]) * S
        px1 = (T[0] * ux1 + T[4]) * S
        py_top = (T[3] * uy1 + T[5]) * S
        py_bot = (T[3] * uy0 + T[5]) * S
        out.append({"x0": px0, "x1": px1, "yt": py_top, "yb": py_bot, "h": (py_bot - py_top)})
    return out


def runs(mask):
    """Runs of True in a 1-D bool array: list of (start, end) inclusive."""
    m = np.concatenate(([False], mask, [False]))
    d = np.diff(m.astype(np.int8))
    starts = np.nonzero(d == 1)[0]
    ends = np.nonzero(d == -1)[0] - 1
    return list(zip(starts.tolist(), ends.tolist()))


def group_lines(W, boxes):
    lines, cur = [], []
    for k, (tk, b) in enumerate(zip(W["tokens"], boxes)):
        if b is None:
            continue
        if cur:
            pb = boxes[cur[-1]]
            hh = st.median([boxes[j]["h"] for j in cur])
            yc, pyc = (b["yt"] + b["yb"]) / 2, st.median([(boxes[j]["yt"] + boxes[j]["yb"]) / 2 for j in cur])
            if (tk["eol"] or abs(yc - pyc) > 0.6 * max(hh, 1) or b["x0"] < pb["x1"] - 0.5 * max(hh, 1)
                    or b["x0"] - pb["x1"] > 3.0 * max(hh, 1)):  # a column gutter inside an OCR line
                lines.append(cur)
                cur = []
        cur.append(k)
    if cur:
        lines.append(cur)
    return lines


def build(W, overlay=False, crop=None):
    doc = pymupdf.open(os.path.join(WORK, "corpus", f"{W['file']}.pdf"))
    page = doc[W["page"] - 1]
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
    bg = float(np.median(arr))
    dark = arr < bg * 0.65
    H, Wd = dark.shape
    T = W["vp"]["transform"]
    boxes = est_boxes(W)
    lines = group_lines(W, boxes)
    toks = W["tokens"]

    # ---- pass 1: bands and internal cuts
    L = []
    for li, ks in enumerate(lines):
        bs = [boxes[k] for k in ks]
        hm = max(st.median([b["h"] for b in bs]), 2.0)
        yc = st.median([(b["yt"] + b["yb"]) / 2 for b in bs])
        X0 = max(int(min(b["x0"] for b in bs) - 0.3 * hm), 0)
        X1 = min(int(max(b["x1"] for b in bs) + 0.3 * hm), Wd - 1)
        R0 = max(int(yc - 1.6 * hm), 0)
        R1 = min(int(yc + 1.6 * hm), H - 1)
        ln = {"id": li, "ks": ks, "hm": hm, "yc": yc, "vconf": False, "why": []}
        L.append(ln)
        if X1 <= X0 or R1 <= R0:
            ln["why"].append("window")
            continue
        win = dark[R0:R1 + 1, X0:X1 + 1]
        # leave out rules and borders: columns dark over half the window
        keep = win.mean(axis=0) < 0.5
        prof = win[:, keep].sum(axis=1)
        rthr = max(2, 0.01 * int(keep.sum()))
        c0 = max(int(yc - 0.35 * hm) - R0, 0)
        c1 = min(int(yc + 0.35 * hm) - R0, len(prof) - 1)
        if c1 < c0:
            ln["why"].append("window")
            continue
        core = c0 + int(np.argmax(prof[c0:c1 + 1]))
        if prof[core] < rthr:
            ln["why"].append("noink")
            continue
        r0 = core
        while r0 > 0 and prof[r0 - 1] >= rthr:
            r0 -= 1
        r1 = core
        while r1 < len(prof) - 1 and prof[r1 + 1] >= rthr:
            r1 += 1
        ln["edge"] = r0 == 0 or r1 == len(prof) - 1
        ln["r0"], ln["r1"], ln["core"] = R0 + r0, R0 + r1, R0 + core
        ln["prof"], ln["R0"] = prof, R0
    Hm = st.median([ln["r1"] - ln["r0"] + 1 for ln in L if "r0" in ln and len(ln["ks"]) >= 3 and not ln["edge"]] or [10])
    for ln in L:
        if "r0" not in ln:
            continue
        hb = ln["r1"] - ln["r0"] + 1
        ok = True
        if hb > 1.4 * Hm:
            # touching lines: cut a side that runs too far at its ink valley
            prof, R0, core = ln["prof"], ln["R0"], ln["core"] - ln["R0"]
            r0, r1 = ln["r0"] - R0, ln["r1"] - R0
            peak = prof[core]
            clean = True
            if core - r0 > 0.9 * Hm:
                a, b = max(int(core - 0.9 * Hm), r0), int(core - 0.2 * Hm)
                if b > a:
                    v = a + int(np.argmin(prof[a:b + 1]))
                    clean &= prof[v] <= 0.15 * peak
                    r0 = v + 1
            if r1 - core > 0.9 * Hm:
                a, b = int(core + 0.15 * Hm), min(int(core + 0.9 * Hm), r1)
                if b > a:
                    v = a + int(np.argmin(prof[a:b + 1]))
                    clean &= prof[v] <= 0.15 * peak
                    r1 = v - 1
            if clean and 0.6 * Hm <= r1 - r0 + 1 <= 1.4 * Hm:
                ln["r0"], ln["r1"] = R0 + r0, R0 + r1
                ln["edge"] = r0 == 0 or r1 == len(prof) - 1
                ln["split"] = True
                hb = r1 - r0 + 1
        if ln["edge"]:
            ln["why"].append("edge"); ok = False
        if hb > 1.4 * Hm:
            ln["why"].append("tall"); ok = False
            # keep a usable band for the columns: around the core
            ln["r0"] = max(ln["r0"], int(ln["core"] - 0.6 * Hm))
            ln["r1"] = min(ln["r1"], int(ln["core"] + 0.6 * Hm))
        if hb < 0.45 * Hm:
            ln["why"].append("short"); ok = False
        ln["vconf"] = ok
        hbe = ln["r1"] - ln["r0"] + 1
        ln["hb"] = hbe
        # columns over the band
        bs = [boxes[k] for k in ln["ks"]]
        hm = ln["hm"]
        WX0 = max(int(min(b["x0"] for b in bs) - 2.5 * hbe), 0)
        WX1 = min(int(max(b["x1"] for b in bs) + 2.5 * hbe), Wd - 1)
        col = dark[ln["r0"]:ln["r1"] + 1, WX0:WX1 + 1].any(axis=0)
        ink = runs(col)
        gaps = runs(~col)  # (s, e) relative to WX0
        ln["WX0"], ln["col"], ln["ink"] = WX0, col, ink
        n = len(col)
        ln["gaps"] = [{"s": s, "e": e, "w": e - s + 1, "open": s == 0 or e == n - 1, "m": (s + e) / 2} for s, e in gaps]
        order = sorted(ln["ks"], key=lambda k: (boxes[k]["x0"] + boxes[k]["x1"]) / 2)
        ln["order"] = order
        ln["inorder"] = order == ln["ks"]
        cx = [(boxes[k]["x0"] + boxes[k]["x1"]) / 2 - WX0 for k in order]
        cuts = []
        for a in range(len(order) - 1):
            lo, hi = cx[a], cx[a + 1]
            cand = [g for g in ln["gaps"] if lo < g["m"] < hi and not g["open"]]
            if cand:
                bnd = (boxes[order[a]]["x1"] + boxes[order[a + 1]]["x0"]) / 2 - WX0
                g = max(cand, key=lambda g: (g["w"], -abs(g["m"] - bnd)))
                cuts.append(g)
            else:
                cuts.append(None)
        ln["cuts"] = cuts
    widths = [c["w"] for ln in L if "cuts" in ln and len(ln["ks"]) >= 3 for c in ln["cuts"] if c]
    Gm = st.median(widths) if widths else 0.3 * Hm

    # ---- pass 2: end cuts, word extents, confidence
    words = {}
    for ln in L:
        if "cuts" not in ln:
            for k in ln["ks"]:
                words[k] = {"k": k, "line": ln["id"], "conf": False, "why": ["line:" + "+".join(ln["why"])]}
            continue
        hb = ln["hb"]
        own = [c["w"] for c in ln["cuts"] if c]
        gm = st.median(own) if len(own) >= 2 else Gm
        twd = min(max(0.55 * gm, 0.12 * hb), 0.45 * hb)
        ln["twd"] = twd
        order, WX0, gaps, col = ln["order"], ln["WX0"], ln["gaps"], ln["col"]
        # line ends: scan outwards from just inside the first/last box's edge
        # (a box may be narrower than its word, or be a whole glued line)
        b0, bn = boxes[order[0]], boxes[order[-1]]
        cx0 = b0["x0"] - WX0 + min(0.25 * (b0["x1"] - b0["x0"]), 0.8 * hb)
        cxn = bn["x1"] - WX0 - min(0.25 * (bn["x1"] - bn["x0"]), 0.8 * hb)
        left = next((g for g in sorted(gaps, key=lambda g: -g["m"]) if g["m"] < cx0 and (g["w"] >= twd or g["open"])), None)
        right = next((g for g in sorted(gaps, key=lambda g: g["m"]) if g["m"] > cxn and (g["w"] >= twd or g["open"])), None)
        allcuts = [left] + ln["cuts"] + [right]
        line_ok_words = 0
        for a, k in enumerate(order):
            gl, gr = allcuts[a], allcuts[a + 1]
            why = []
            if not ln["vconf"]:
                why.append("line:" + "+".join(ln["why"]))
            if gl is None or gr is None:
                why.append("nocut")
            b = boxes[k]
            lo = (gl["e"] + 1) if gl else int(b["x0"] - WX0)
            hi = (gr["s"] - 1) if gr else int(b["x1"] - WX0)
            seg = col[max(lo, 0):max(hi + 1, 0)]
            idx = np.nonzero(seg)[0]
            if len(idx) == 0:
                words[k] = {"k": k, "line": ln["id"], "conf": False, "why": why + ["noink"]}
                continue
            i0, i1 = lo + int(idx[0]), lo + int(idx[-1])
            # blank runs inside the word
            inner = [g["w"] for g in gaps if g["s"] > i0 and g["e"] < i1]
            side = [g["w"] for g in (gl, gr) if g is not None]
            if gl is not None and gl["w"] < twd and not gl["open"]:
                why.append("leftgap")
            if gr is not None and gr["w"] < twd and not gr["open"]:
                why.append("rightgap")
            if inner and side and max(inner) >= 0.8 * min(side):
                why.append("innergap")
            inkw = i1 - i0 + 1
            estw = max(b["x1"] - b["x0"], 1)
            ecx = (b["x0"] + b["x1"]) / 2 - WX0
            if not (0.45 <= inkw / estw <= 2.3):
                why.append("ratio")
            if not (i0 - 0.15 * inkw <= ecx <= i1 + 0.15 * inkw):
                why.append("centre")
            t = toks[k]["t"]
            if not re.search(r"\w", t) or t.strip("_") == "":
                why.append("nonword")
            elif not (0.1 <= inkw / len(t) / hb <= 1.6):
                why.append("perchar")
            x0u = (WX0 + i0) / S / T[0] - T[4] / T[0]
            x1u = (WX0 + i1 + 1) / S / T[0] - T[4] / T[0]
            words[k] = {"k": k, "line": ln["id"], "x0": round(x0u, 3), "x1": round(x1u, 3),
                        "conf": not why, "why": why}
            if not why:
                line_ok_words += 1
        ln["okw"] = line_ok_words

    # ---- output
    def yu(py):  # pixel row -> user y
        return (py / S - T[5]) / T[3]

    out_lines = []
    for ln in L:
        o = {"id": ln["id"], "tokens": ln["ks"], "vconf": ln["vconf"], "why": ln["why"]}
        if "r0" in ln:
            o["top"] = round(yu(ln["r0"]), 3)
            o["bottom"] = round(yu(ln["r1"] + 1), 3)
        if "ink" in ln and ln.get("okw"):
            # ink columns of the line between its first and last word's ink
            xs = [words[k] for k in ln["ks"] if "x0" in words.get(k, {})]
            if xs:
                a0 = min(w["x0"] for w in xs)
                a1 = max(w["x1"] for w in xs)
                ir = []
                for s_, e_ in ln["ink"]:
                    u0 = (ln["WX0"] + s_) / S / T[0] - T[4] / T[0]
                    u1 = (ln["WX0"] + e_ + 1) / S / T[0] - T[4] / T[0]
                    if u1 > a0 + 1e-6 and u0 < a1 - 1e-6:
                        ir.append([round(max(u0, a0), 3), round(min(u1, a1), 3)])
                o["ink"] = ir
        o["inorder"] = ln.get("inorder", True)
        o["confWords"] = ln.get("okw", 0)
        out_lines.append(o)
    out_words = []
    for k, tk in enumerate(toks):
        w = words.get(k)
        if w is None:
            w = {"k": k, "line": None, "conf": False, "why": ["rotated"]}
        w["t"] = tk["t"]
        out_words.append(w)
    res = {"file": W["file"], "page": W["page"], "dpi": DPI, "bandMedianPx": Hm, "gapMedianPx": Gm,
           "lines": out_lines, "words": out_words}

    if overlay:
        draw_overlay(W, arr, boxes, L, words, res, crop)
    return res


def draw_overlay(W, arr, boxes, L, words, res, crop):
    im = Image.fromarray(arr).convert("RGB")
    dr = ImageDraw.Draw(im, "RGBA")
    T = W["vp"]["transform"]
    items = W["tc"]["items"]
    # pdf.js TextLayer spans today (orange outline)
    for tk in W["tokens"]:
        for i, c0, c1 in tk["parts"]:
            tr = items[i]["transform"]
            if is_rot(tr):
                continue
            h = (tr[2] ** 2 + tr[3] ** 2) ** 0.5
            n = max(len(items[i]["str"]), 1)
            x0 = tr[4] + items[i]["width"] * c0 / n
            x1 = tr[4] + items[i]["width"] * c1 / n
            y0, y1 = tr[5] - 0.195 * h, tr[5] + 0.805 * h
            dr.rectangle([(T[0] * x0 + T[4]) * S, (T[3] * y1 + T[5]) * S, (T[0] * x1 + T[4]) * S, (T[3] * y0 + T[5]) * S],
                         outline=(255, 140, 0, 200))
    for b in boxes:
        if b:
            dr.rectangle([b["x0"], b["yt"], b["x1"], b["yb"]], outline=(0, 90, 255, 200))
    for ln in L:
        if "r0" not in ln:
            continue
        for k in ln["ks"]:
            w = words.get(k)
            if not w or "x0" not in w:
                continue
            px0 = (T[0] * w["x0"] + T[4]) * S
            px1 = (T[0] * w["x1"] + T[4]) * S
            col = (0, 200, 0, 70) if w["conf"] else (255, 0, 0, 70)
            dr.rectangle([px0, ln["r0"], px1, ln["r1"] + 1], fill=col)
    if crop:
        y0, y1 = crop
        im = im.crop((0, int(y0 * im.height), im.width, int(y1 * im.height)))
    os.makedirs(os.path.join(STUDY, "overlays"), exist_ok=True)
    im.save(os.path.join(STUDY, "overlays", f"{W['file']}-p{W['page']}.png"))


def main(argv):
    only_f = only_p = None
    overlay, crop = False, None
    it = iter(argv)
    for a in it:
        if a == "--file":
            only_f = next(it)
        elif a == "--page":
            only_p = int(next(it))
        elif a == "--overlay":
            overlay = True
        elif a == "--crop":
            crop = tuple(float(v) for v in next(it).split(","))
    pages = json.load(open(os.path.join(HERE, "pages.json"), encoding="utf-8"))
    os.makedirs(os.path.join(STUDY, "truth"), exist_ok=True)
    summary = {}
    for fid, spec in pages["files"].items():
        if only_f and fid != only_f:
            continue
        for pn in sorted(int(p) for p in spec["pages"]):
            if only_p and pn != only_p:
                continue
            W = json.load(open(os.path.join(STUDY, "words", f"{fid}-p{pn}.json"), encoding="utf-8"))
            res = build(W, overlay, crop)
            with open(os.path.join(STUDY, "truth", f"{fid}-p{pn}.json"), "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False)
            n = len(res["words"])
            c = sum(1 for w in res["words"] if w["conf"])
            nl = len(res["lines"])
            vl = sum(1 for l in res["lines"] if l["vconf"])
            reasons = {}
            for w in res["words"]:
                for r in w["why"]:
                    r = r.split(":")[0]
                    reasons[r] = reasons.get(r, 0) + 1
            print(f"{fid}-p{pn}: words {c}/{n} confident ({c / max(n, 1):.0%}), lines {vl}/{nl} band-confident; "
                  f"band {res['bandMedianPx']}px gap {res['gapMedianPx']}px; why {reasons}")
            s = summary.setdefault(fid, [0, 0, 0, 0])
            s[0] += c; s[1] += n; s[2] += vl; s[3] += nl
    print("\nper file: confident words / words, band-confident lines / lines")
    for fid, (c, n, vl, nl) in summary.items():
        print(f"  {fid:9s} {c:5d}/{n:5d} = {c / max(n, 1):.0%}   lines {vl}/{nl} = {vl / max(nl, 1):.0%}")
    if not only_f and not only_p:
        with open(os.path.join(STUDY, "truth", "summary.json"), "w", encoding="utf-8") as f:
            json.dump({k: {"confWords": v[0], "words": v[1], "confLines": v[2], "lines": v[3]} for k, v in summary.items()}, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
