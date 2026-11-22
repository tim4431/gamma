"""Scan detector for PyMuPDF pages, the twin of detect.mjs's confirm.

is_scan_page(page) -> (bool, why): the page is a scanned image with an
invisible OCR text layer over it when nearly all of its shown glyphs are in an
invisible render mode (Tr 3 or 7) and the union of its painted images covers
most of the page. The thresholds are detect.mjs's CONFIRM.

Cost, in three steps, each run only when the one before it says "maybe":

1. A byte scan of the page's content streams, and those of the forms its
   resources name (nested up to 4 deep), for a `3 Tr` or `7 Tr` operator. No
   interpretation, only decompression: median 0.5 ms on a born-digital page.
   Without one there is no invisible text: not a scan. (On the 3809
   born-digital pages it never missed a page whose dict had invisible spans.)
2. Span alphas from get_text("dict", flags=0): MuPDF reports render modes 3
   and 7 as alpha 0 (get_texttrace reports mode 3 as type 3 but drops mode 7
   and doubles mode 2, so it is not used). Non-space chars weigh the spans.
3. get_image_info(): the union of the image boxes on a 100 x 100 grid of cell
   centres, as detect.mjs's confirm does.

CLI: python -I detect.py [--max N] [--full] [--out file.jsonl] (list.txt | pdf...)
  --full   run steps 2 and 3 on every page (for the near-miss report and
           timings), but the verdict is the same.
"""
from __future__ import annotations

import json
import re
import sys
import time

import pymupdf

INVISIBLE = 0.9
COVERAGE = 0.7
GRID = 100
_FORM_DEPTH = 4

# `3 Tr` / `7 Tr` as an operator: the operand stands alone, the operator ends
# at a delimiter or white space.
_TR_RE = re.compile(rb"(?<![\w.+\-])[37](?:\.0*)?\s+Tr(?![A-Za-z0-9_*'\"])")


def _form_xrefs(doc, xref, depth, seen):
    """Form XObjects in the Resources of object `xref` (a page or a form)."""
    if depth > _FORM_DEPTH:
        return
    kind, val = doc.xref_get_key(xref, "Resources/XObject")
    if kind == "xref":  # an indirect XObject dictionary
        dict_src = doc.xref_object(int(val.split()[0]), compressed=True)
    elif kind == "dict":
        dict_src = val
    else:
        return
    for ref in re.findall(r"/[^\s/<>\[\]()]+\s*(\d+)\s+0\s+R", dict_src):
        x = int(ref)
        if x in seen:
            continue
        seen.add(x)
        if doc.xref_get_key(x, "Subtype")[1] != "/Form":
            continue
        yield x
        yield from _form_xrefs(doc, x, depth + 1, seen)


def invisible_mode_used(page) -> bool:
    """Step 1: does any content stream the page draws set Tr 3 or 7?"""
    try:
        if _TR_RE.search(page.read_contents()):
            return True
    except Exception:
        return True  # unreadable: let the exact steps decide
    doc = page.parent
    try:
        for x in _form_xrefs(doc, page.xref, 0, set()):
            if _TR_RE.search(doc.xref_stream(x) or b""):
                return True
    except Exception:
        return True
    return False


def glyph_shares(page):
    """Step 2: (invisible, visible) non-space glyph counts."""
    inv = vis = 0
    d = page.get_text("dict", flags=0)
    for b in d["blocks"]:
        for line in b.get("lines", ()):
            for s in line["spans"]:
                n = sum(1 for ch in s["text"] if not ch.isspace())
                if s.get("alpha", 255) == 0:
                    inv += n
                else:
                    vis += n
    return inv, vis


def image_coverage(page) -> tuple[float, int]:
    """Step 3: share of the page under the union of the image boxes."""
    try:
        infos = page.get_image_info()
    except Exception:
        return 0.0, 0
    pr = page.rect
    if not infos or pr.is_empty:
        return 0.0, 0
    cells = bytearray(GRID * GRID)
    w, h = pr.width, pr.height
    for info in infos:
        x0, y0, x1, y1 = info["bbox"]
        # cell centres inside the box
        i0 = max(0, int(-(-((x0 - pr.x0) / w * GRID - 0.5) // 1)))
        i1 = min(GRID - 1, int(((x1 - pr.x0) / w * GRID - 0.5) // 1))
        j0 = max(0, int(-(-((y0 - pr.y0) / h * GRID - 0.5) // 1)))
        j1 = min(GRID - 1, int(((y1 - pr.y0) / h * GRID - 0.5) // 1))
        if i1 < i0 or j1 < j0:
            continue
        row = b"\x01" * (i1 - i0 + 1)
        for j in range(j0, j1 + 1):
            cells[j * GRID + i0:j * GRID + i1 + 1] = row
    return sum(cells) / (GRID * GRID), len(infos)


def is_scan_page(page, full=False):
    """(bool, why). With full=True every step runs; the verdict is the same."""
    used = invisible_mode_used(page)
    if not used and not full:
        return False, "no invisible text mode in the page's streams"
    inv, vis = glyph_shares(page)
    total = inv + vis
    share = inv / total if total else 0.0
    if not full and (not total or share < INVISIBLE):
        return False, f"invisible {share:.3f} of {total} glyphs"
    cover, n = image_coverage(page)
    scan = used and total > 0 and share >= INVISIBLE and cover >= COVERAGE
    why = f"{'scan' if scan else 'not a scan'}: invisible {share:.3f} of {total} glyphs, images cover {cover:.3f} ({n} images)"
    if full and not used and inv:
        why += " [invisible glyphs but no Tr 3/7 in the streams]"
    return scan, why


def _spread(n, k):
    if n <= k:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def main(argv):
    pymupdf.TOOLS.mupdf_display_errors(False)
    out, mx, full, files = None, None, False, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--out":
            out = argv[i + 1]; i += 1
        elif a == "--max":
            mx = int(argv[i + 1]); i += 1
        elif a == "--full":
            full = True
        elif a.endswith(".txt"):
            with open(a, encoding="utf-8") as fh:
                files += [l.strip() for l in fh if l.strip()]
        else:
            files.append(a)
        i += 1
    fh = open(out, "w", encoding="utf-8") if out else None
    for f in files:
        try:
            doc = pymupdf.open(f)
        except Exception as e:
            print("open failed", f, e, file=sys.stderr)
            continue
        pages = _spread(doc.page_count, mx) if mx else range(doc.page_count)
        for pno in pages:
            page = doc[pno]
            rec = {"f": f, "pn": pno + 1}
            t0 = time.perf_counter()
            rec["used"] = invisible_mode_used(page)
            t1 = time.perf_counter()
            scan, why = is_scan_page(page)
            t2 = time.perf_counter()
            rec.update(scan=scan, why=why, t_bytes=(t1 - t0) * 1e3, t_check=(t2 - t1) * 1e3)
            if full:
                t3 = time.perf_counter()
                inv, vis = glyph_shares(page)
                t4 = time.perf_counter()
                cover, n = image_coverage(page)
                t5 = time.perf_counter()
                tr = page.get_texttrace()
                t6 = time.perf_counter()
                tinv = sum(len(s["chars"]) for s in tr if s["type"] == 3)
                tall = sum(len(s["chars"]) for s in tr)
                rec.update(inv=inv, vis=vis, cover=cover, images=n, t_dict=(t4 - t3) * 1e3, t_img=(t5 - t4) * 1e3,
                           t_trace=(t6 - t5) * 1e3, trace_inv=tinv, trace_all=tall)
            line = json.dumps(rec)
            if fh:
                fh.write(line + "\n")
            else:
                print(f"{f.split('/')[-1]} p{pno + 1}: {why}")
        doc.close()
    if fh:
        fh.close()


if __name__ == "__main__":
    main(sys.argv[1:])
