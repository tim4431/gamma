"""Characterize the text layer of PDFs (scanned-with-OCR vs born-digital).

Usage: python -I characterize.py [--pages N] [--json out.json] [--screen] PDF...

--screen   only the cheap per-page facts (image coverage, render-mode-3 share)
           for every page; used for the born-digital negative set.
Default    per file: producer/creator, then for up to N sample pages the full
           analysis: render modes, fonts, spans per word, per-line font-size
           spread, baseline agreement, word-box vs ink coverage, Tz.
"""
import json
import re
import statistics as st
import sys

import pymupdf

pymupdf.TOOLS.mupdf_display_errors(False)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def image_coverage(page):
    """Fraction of the page covered by images: the union of the image
    placements' boxes (scans are sometimes cut in strips), measured on a
    coarse 100x100 grid so overlaps are not double counted."""
    try:
        infos = page.get_image_info()
    except Exception:
        return 0.0
    pr = page.rect
    if not infos or pr.is_empty:
        return 0.0
    N = 100
    grid = bytearray(N * N)
    for info in infos:
        r = pymupdf.Rect(info["bbox"]) & pr
        if r.is_empty:
            continue
        gx0 = int((r.x0 - pr.x0) / pr.width * N)
        gx1 = int((r.x1 - pr.x0) / pr.width * N + 0.999)
        gy0 = int((r.y0 - pr.y0) / pr.height * N)
        gy1 = int((r.y1 - pr.y0) / pr.height * N + 0.999)
        for y in range(gy0, min(gy1, N)):
            grid[y * N + gx0:y * N + min(gx1, N)] = b"\x01" * (min(gx1, N) - gx0)
    return sum(grid) / (N * N)


def trace_stats(page):
    """Render-mode counts (chars), fonts, and Tz-like horizontal scaling."""
    modes, fonts, scales = {}, {}, []
    try:
        tr = page.get_texttrace()
    except Exception:
        return modes, fonts, scales, 0
    nchar = 0
    for s in tr:
        n = len(s["chars"])
        nchar += n
        modes[s["type"]] = modes.get(s["type"], 0) + n
        fonts[s["font"]] = fonts.get(s["font"], 0) + n
        # horizontal scaling: dir is the baseline direction; the span's
        # "size" is the font size. Compare glyph advance-based width with
        # the char bbox width sum is fragile; instead read the scale from
        # the span's bbox height vs size and the chars' x-advance vs widths.
        chars = s["chars"]
        if n >= 3 and s["size"] > 0:
            x0 = chars[0][2][0]
            x1 = chars[-1][3][2]
            # sum of nominal advances (glyph widths * size) is not exposed
            # per char; approximate with bbox widths
            boxw = sum(c[3][2] - c[3][0] for c in chars)
            if boxw > 0:
                scales.append((x1 - x0) / boxw)
    return modes, fonts, scales, nchar


TZ_RE = re.compile(rb"(-?\d+(?:\.\d+)?)\s+Tz")


def tz_values(doc, page):
    vals = []
    try:
        for xref in page.get_contents():
            data = doc.xref_stream(xref) or b""
            vals += [float(v) for v in TZ_RE.findall(data)]
    except Exception:
        pass
    return vals


def words_and_lines(page):
    """rawdict spans -> per-word font size and baseline, grouped by visual line.

    Returns (words, spans_per_word, lines) where each word is
    (x0, y0, x1, y1, size, baseline_y, text) and lines are lists of words
    grouped by vertical overlap of their boxes (not by PDF line objects).
    """
    words = page.get_text("words")  # x0,y0,x1,y1,text,block,line,wordno
    d = page.get_text("rawdict")
    spans = []
    for b in d["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                if sp["chars"]:
                    spans.append(sp)
    # spans per word: count spans whose bbox intersects each word box
    span_boxes = [(pymupdf.Rect(s["bbox"]), s) for s in spans]
    spw = []
    out = []
    for w in words:
        r = pymupdf.Rect(w[:4])
        hit = [s for (sb, s) in span_boxes if sb.intersects(r) and abs(sb & r) > 0.3 * abs(r)]
        spw.append(len(hit) or 1)
        size = hit[0]["size"] if hit else (w[3] - w[1])
        base = hit[0]["origin"][1] if hit else w[3]
        out.append((w[0], w[1], w[2], w[3], size, base, w[4]))
    # group into visual lines by vertical centre overlap
    out_sorted = sorted(out, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines = []
    for w in out_sorted:
        cy = (w[1] + w[3]) / 2
        h = max(w[3] - w[1], 0.5)
        placed = False
        for ln in lines[-6:]:
            ly0 = st.median([x[1] for x in ln])
            ly1 = st.median([x[3] for x in ln])
            if ly0 - 0.15 * h <= cy <= ly1 + 0.15 * h:
                # avoid merging columns: different x region is fine, still same line
                ln.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    # split at column gutters: a horizontal gap over 2.5x the line's height
    split = []
    for ln in lines:
        ln.sort(key=lambda w: w[0])
        hh = st.median([w[3] - w[1] for w in ln])
        cur = [ln[0]]
        for a, b in zip(ln, ln[1:]):
            if b[0] - a[2] > 2.5 * hh:
                split.append(cur)
                cur = []
            cur.append(b)
        split.append(cur)
    return out, spw, split


def ink_coverage(page, words, dpi=150):
    """For each word, compare its box's x-extent with the ink run on its line.

    Render the page as printed (the OCR text is invisible so it does not draw;
    visible text would draw, which for born-digital pages is the same thing).
    For each word box, take the rows of its vertical band, find the dark
    columns, and grow from the box's centre to the ink run (gaps up to
    ~0.12 x box height, 1-1.8 pt, are bridged like letter spacing). Report
    box_width / ink_run_width and the centre offset in box widths.
    """
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    W, H = pix.width, pix.height
    buf = pix.samples
    s = dpi / 72.0
    # adaptive threshold: a fraction of the paper tone (scans can be grey
    # or tinted; downsampled bilevel strokes are grey, not black)
    hist = [0] * 256
    for v in buf[:: 97]:
        hist[v] += 1
    tot, acc, bg = sum(hist), 0, 255
    for v in range(256):
        acc += hist[v]
        if acc >= tot / 2:
            bg = v
            break
    thr = int(bg * 0.65)
    ratios, offsets, empty = [], [], 0
    for w in words:
        x0, y0, x1, y1 = [v * s for v in w[:4]]
        h = y1 - y0
        if h < 3 or x1 - x0 < 2 or not w[6].strip():
            continue
        ry0, ry1 = max(int(y0 + 0.15 * h), 0), min(int(y1 - 0.15 * h), H - 1)
        if ry1 <= ry0:
            continue
        bw = x1 - x0
        wx0, wx1 = max(int(x0 - 2 * bw), 0), min(int(x1 + 2 * bw), W - 1)
        dark = []
        for x in range(wx0, wx1 + 1):
            d = False
            for y in range(ry0, ry1 + 1):
                if buf[y * W + x] < thr:
                    d = True
                    break
            dark.append(d)
        # bridge letter gaps, stop at word spaces: ~0.12 box height, held
        # between 1.0 and 1.8 pt (a word space in 10 pt type is ~2.5 pt)
        gap = int(s * min(max(0.12 * h / s, 1.0), 1.8)) or 1
        bx0, bx1 = int(x0) - wx0, int(x1) - wx0
        idx = [i for i in range(max(bx0, 0), min(bx1 + 1, len(dark))) if dark[i]]
        if not idx:
            empty += 1
            continue
        lo, hi = idx[0], idx[-1]
        g = 0
        i = lo
        while i > 0:
            i -= 1
            if dark[i]:
                lo, g = i, 0
            else:
                g += 1
                if g > gap:
                    break
        g = 0
        i = hi
        while i < len(dark) - 1:
            i += 1
            if dark[i]:
                hi, g = i, 0
            else:
                g += 1
                if g > gap:
                    break
        ink = hi - lo + 1
        if ink < 2:
            continue
        ratios.append(bw / ink)
        offsets.append(((x0 + x1) / 2 - (wx0 + (lo + hi) / 2)) / bw)
    return ratios, offsets, empty


def q(vals, p):
    if not vals:
        return None
    v = sorted(vals)
    return round(v[min(int(p * (len(v) - 1) + 0.5), len(v) - 1)], 3)


def analyze_page(doc, page):
    modes, fonts, scales, nchar = trace_stats(page)
    res = {
        "page": page.number + 1,
        "image_cover": round(image_coverage(page), 3),
        "chars": nchar,
        "modes": modes,
        "mode3_share": round(modes.get(3, 0) / nchar, 3) if nchar else 0,
        "fonts": dict(sorted(fonts.items(), key=lambda kv: -kv[1])[:4]),
        "tz": sorted(set(tz_values(doc, page)))[:6],
    }
    if nchar == 0:
        return res
    words, spw, lines = words_and_lines(page)
    res["words"] = len(words)
    res["spans_per_word_med"] = q(spw, 0.5)
    # text objects per word: BT..ET blocks; approximate via rawdict lines per word
    d = page.get_text("dict")
    nlines_pdf = sum(len(b.get("lines", [])) for b in d["blocks"])
    res["pdf_lines_per_word"] = round(nlines_pdf / max(len(words), 1), 3)
    res["visual_lines"] = len(lines)
    spreads, base_dev, size_cv = [], [], []
    for ln in lines:
        if len(ln) < 3:
            continue
        sizes = [w[4] for w in ln if w[4] > 0]
        if sizes:
            spreads.append(max(sizes) / min(sizes))
        bases = [w[5] for w in ln]
        med = st.median(sizes) if sizes else 1
        base_dev.append((max(bases) - min(bases)) / med)
    res["line_size_spread_med"] = q(spreads, 0.5)
    res["line_size_spread_p90"] = q(spreads, 0.9)
    res["line_baseline_range_med_em"] = q(base_dev, 0.5)
    res["lines_sharing_baseline"] = (
        round(sum(1 for b in base_dev if b < 0.05) / len(base_dev), 3) if base_dev else None
    )
    ratios, offsets, empty = ink_coverage(page, words)
    res["box_over_ink_med"] = q(ratios, 0.5)
    res["box_over_ink_p10"] = q(ratios, 0.1)
    res["box_over_ink_p90"] = q(ratios, 0.9)
    res["centre_offset_med"] = q([abs(o) for o in offsets], 0.5)
    res["words_no_ink"] = empty
    res["hscale_med"] = q(scales, 0.5)
    res["sample_text"] = " ".join(w[6] for w in words[:14])[:140]
    return res


def sample_pages(n, k):
    if n <= k:
        return list(range(n))
    picks = {0, 1, n // 2, n - 1}
    step = max(n // k, 1)
    picks |= set(range(0, n, step))
    return sorted(picks)[:k + 2]


def characterize(path, k):
    doc = pymupdf.open(path)
    meta = doc.metadata or {}
    out = {
        "path": path,
        "pages": doc.page_count,
        "producer": meta.get("producer", ""),
        "creator": meta.get("creator", ""),
        "sample": [],
    }
    for i in sample_pages(doc.page_count, k):
        out["sample"].append(analyze_page(doc, doc[i]))
    return out


def screen(path):
    """Cheap per-page facts for every page."""
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        return {"path": path, "error": str(e)}
    pages = []
    for page in doc:
        modes, fonts, _, nchar = trace_stats(page)
        pages.append({
            "cover": round(image_coverage(page), 3),
            "chars": nchar,
            "mode3": modes.get(3, 0),
        })
    meta = doc.metadata or {}
    return {"path": path, "producer": meta.get("producer", ""),
            "creator": meta.get("creator", ""), "pages": pages}


def main(argv):
    k, jout, scr = 4, None, False
    paths = []
    it = iter(argv)
    for a in it:
        if a == "--pages":
            k = int(next(it))
        elif a == "--json":
            jout = next(it)
        elif a == "--screen":
            scr = True
        else:
            paths.append(a)
    results = []
    for p in paths:
        if scr:
            r = screen(p)
            results.append(r)
            if "error" in r:
                print(p, "ERROR", r["error"])
                continue
            pg = r["pages"]
            n = len(pg)
            scan = sum(1 for x in pg if x["cover"] > 0.6)
            m3 = sum(1 for x in pg if x["mode3"] > 0)
            notext = sum(1 for x in pg if x["chars"] == 0)
            print(f"{n:4d}p cover>.6:{scan:4d} mode3:{m3:4d} notext:{notext:4d} "
                  f"| {r['producer'][:30]!r} {r['creator'][:30]!r} | {p}")
            continue
        try:
            r = characterize(p, k)
        except Exception as e:
            print(p, "ERROR", e)
            continue
        results.append(r)
        print(f"=== {p}\n pages={r['pages']} producer={r['producer']!r} creator={r['creator']!r}")
        for s in r["sample"]:
            print("  ", json.dumps(s, ensure_ascii=False))
    if jout:
        with open(jout, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
