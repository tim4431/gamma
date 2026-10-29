"""Write "Atom arrays — iPad notes.note": a Notability note on the curated
atom-arrays paper (Bluvstein et al. 2022, Nature 604, 451; CC BY 4.0) with
the marks a reader makes on an iPad with the pencil and the highlighter,
for the ink-import README demo (record-ink-import.mjs).

The repo's Notability samples are not cleared for publication, so the note
is made here instead: the paper's PDF, and on its first two pages a yellow
highlighter over the abstract's first sentence, a pencil underline, a
circle, a bracket in the margin, an arrow and two handwritten margin words
("key!" and "how?"), drawn from hand-designed letter strokes with a little
jitter, slant and pressure. Every mark is placed from PyMuPDF's word
positions on the page.

It is written by gamma/notability_write.py in the shape Notability's own
"Note" export has, without Gamma's private round-trip copy (no
GammaNoteKey, plain random curve UUIDs), so Gamma's reader takes it the way
it takes a note made on an iPad: anchors flattened, widths and forces read,
the highlighter's alpha as its opacity.

    python make-notability-note.py [--pdf paper.pdf] [--out file.note]

Without --pdf the PDF is taken from the curated export (DEMO_EXPORT or
artifacts/readme-media/demo.zip). Prints a JSON summary: the file, the
stroke count per PDF page and the margin words' boxes in PDF points.
"""
import argparse
import datetime
import json
import math
import os
import random
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

import pymupdf  # noqa: E402

from gamma import notability as nb  # noqa: E402
from gamma import notability_map as nmap  # noqa: E402
from gamma import notability_write as nw  # noqa: E402

PAGE_ID = 'fy0-h_BqOHcH'
TITLE = 'Atom arrays — iPad notes'
PDF_NAME = '5E0C2A9B-7F41-4C1D-9B8E-2D6A41F3C7E2.pdf'  # Notability names a note's PDF by a UUID

GRAPHITE = (52, 56, 66, 255)       # the pencil
YELLOW = (255, 221, 51, 0x6b)      # Notability's highlighter alpha (0x6b ≈ 0.42)
PENCIL_W = 2.0                     # pt, curveswidth
WRITING_W = 1.75
HIGHLIGHT_W = 11.5

rng = random.Random(20220421)


# --- the paper ---------------------------------------------------------------------------------

def curated_pdf() -> bytes:
    """The atom-arrays PDF from the curated export: the page's doc_id in its pages.db."""
    archive = Path(os.environ.get('DEMO_EXPORT') or ROOT / 'artifacts/readme-media/demo.zip')
    with zipfile.ZipFile(archive) as z, tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / 'pages.db'
        db.write_bytes(z.read('pages.db'))
        con = sqlite3.connect(db)
        try:
            (props,) = con.execute('select properties from unified_blocks where id = ?', (PAGE_ID,)).fetchone()
        finally:
            con.close()
        return z.read(f"uploads/{json.loads(props)['doc_id']}.pdf")


def lines_of(page) -> list:
    """The page's text lines in reading order: (x0, y0, x1, y1, words), each word (x0, y0, x1, y1, text)."""
    lines = {}
    for x0, y0, x1, y1, text, block, line, _ in page.get_text('words'):
        lines.setdefault((block, line), []).append((x0, y0, x1, y1, text))
    out = []
    for words in lines.values():
        out.append((min(w[0] for w in words), min(w[1] for w in words), max(w[2] for w in words),
                    max(w[3] for w in words), words))
    return sorted(out, key=lambda l: (round(l[1]), l[0]))


def find(lines, phrase):
    """The box (x0, y0, x1, y1) of `phrase` (whole words, one line) and its line."""
    want = phrase.split()
    for line in lines:
        words = line[4]
        texts = [w[4] for w in words]
        for i in range(len(texts) - len(want) + 1):
            got = texts[i:i + len(want)]
            if got[:-1] == want[:-1] and got[-1].startswith(want[-1]):
                ws = words[i:i + len(want)]
                return (ws[0][0], min(w[1] for w in ws), ws[-1][2], max(w[3] for w in ws)), line
    raise ValueError(f'"{phrase}" is not on the page')


def line_starting(lines, start):
    for line in lines:
        if ' '.join(w[4] for w in line[4]).startswith(start):
            return line
    raise ValueError(f'No line starts with "{start}"')


# --- a hand ------------------------------------------------------------------------------------

def catmull(points, per=10):
    out = []
    for i in range(len(points) - 1):
        a, b, c, d = points[max(0, i - 1)], points[i], points[i + 1], points[min(len(points) - 1, i + 2)]
        for j in range(per):
            t = j / per
            out.append(tuple(0.5 * (2 * b[k] + (-a[k] + c[k]) * t + (2 * a[k] - 5 * b[k] + 4 * c[k] - d[k]) * t * t
                                    + (-a[k] + 3 * b[k] - 3 * c[k] + d[k]) * t ** 3) for k in (0, 1)))
    return out + [tuple(points[-1])]


def resample(points, step):
    """Points `step` apart along the polyline, the ends kept."""
    out, carry = [points[0]], 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        d = step - carry
        while d <= seg:
            out.append((x0 + (x1 - x0) * d / seg, y0 + (y1 - y0) * d / seg))
            d += step
        carry = seg - (d - step)
    if math.dist(out[-1], points[-1]) > step * 0.3:
        out.append(points[-1])
    return out


class Wobble:
    """A slow, smooth wander of the hand: a sum of a few sines of random phase."""
    def __init__(self, amp, scale):
        self.terms = [(rng.uniform(0.6, 1.4) / scale * f, rng.uniform(0, 2 * math.pi), amp / f)
                      for f in (1.0, 2.3, 4.1)]

    def __call__(self, s):
        return sum(a * math.sin(w * s + p) for w, p, a in self.terms)


def pencil(points, width=PENCIL_W, rgba=GRAPHITE, jitter=0.25, step=0.7, z=0, smooth=True):
    """A pencil stroke through `points` (pt, y down): smoothed, wandering a
    little across its path, its pressure rising in and lifting out."""
    path = resample(catmull(points) if smooth else points, step)
    wx, wy = Wobble(jitter, 18), Wobble(jitter, 18)
    s, out = 0.0, []
    for i, (x, y) in enumerate(path):
        if i:
            s += math.dist(path[i - 1], path[i])
        out.append((x + wx(s), y + wy(s)))
    n = len(out)
    pressure = []
    for i in range(n):
        u = i / max(1, n - 1)
        ramp = min(1.0, u / 0.12, (1 - u) / 0.18)
        pressure.append(max(0.08, (0.35 + 0.65 * ramp) * (0.88 + 0.12 * math.sin(7 * u + rng.uniform(-0.2, 0.2)))))
    tilt = rng.uniform(0.85, 1.05)
    azimuth = rng.uniform(-2.5, -2.2)
    return nw.Stroke(
        anchors=out, width=width, rgba=rgba,
        widths=[0.55 + 0.6 * p for p in pressure],
        forces=[nmap.MAX_FORCE * (0.18 + 0.42 * p) for p in pressure],
        altitudes=[tilt + 0.05 * math.sin(i / 9) for i in range(n)],
        azimuths=[(math.cos(azimuth), math.sin(azimuth))] * n,
        style=nb.STYLE_PENCIL, z=z)


def highlighter(x0, x1, y, height=HIGHLIGHT_W, z=0):
    """A highlighter pass along a line of text: nearly straight, a little high at the start."""
    pts = [(x0 - 2, y + 0.6), (x0 + (x1 - x0) * 0.35, y - 0.3), (x0 + (x1 - x0) * 0.7, y + 0.2), (x1 + 2.5, y - 0.4)]
    path = resample(catmull(pts, 6), 1.5)
    return nw.Stroke(anchors=path, width=height, rgba=YELLOW, style=nb.STYLE_HIGHLIGHTER, z=z)


# Letters as hand-designed pen paths in units of the ascender height (y
# down, baseline 0, x-height 0.5): each a list of strokes.
GLYPHS = {
    'k': ([(0.0, -1.0), (-0.01, -0.5), (-0.02, 0.0)],
          [(0.3, -0.5), (0.12, -0.31), (0.02, -0.22), (0.14, -0.17), (0.25, -0.07), (0.35, 0.0)]),
    'e': ([(0.44, -0.21), (0.62, -0.25), (0.66, -0.39), (0.56, -0.5), (0.44, -0.42), (0.41, -0.2), (0.48, -0.03),
           (0.62, 0.0), (0.73, -0.08)],),
    'y': ([(0.8, -0.48), (0.82, -0.2), (0.89, -0.03), (0.99, -0.06), (1.06, -0.25), (1.09, -0.5), (1.08, -0.15),
           (1.05, 0.15), (0.99, 0.36), (0.88, 0.42), (0.8, 0.32)],),
    '!': ([(1.3, -1.0), (1.26, -0.55), (1.24, -0.25)],
          [(1.225, -0.06), (1.235, -0.03), (1.228, -0.01)]),
    'h': ([(0.02, -1.0), (0.0, -0.5), (-0.01, 0.0), (0.02, -0.28), (0.12, -0.46), (0.26, -0.47), (0.32, -0.3),
           (0.33, 0.0)],),
    'o': ([(0.63, -0.48), (0.51, -0.42), (0.47, -0.2), (0.53, -0.02), (0.66, 0.0), (0.77, -0.15), (0.76, -0.38),
           (0.65, -0.5), (0.55, -0.45)],),
    'w': ([(0.87, -0.48), (0.93, -0.15), (0.98, 0.0), (1.05, -0.25), (1.09, -0.38), (1.13, -0.2), (1.19, 0.0),
           (1.26, -0.25), (1.31, -0.5)],),
    '?': ([(1.43, -0.8), (1.51, -0.97), (1.65, -1.0), (1.74, -0.86), (1.69, -0.66), (1.59, -0.54), (1.57, -0.3)],
          [(1.565, -0.06), (1.575, -0.03), (1.568, -0.01)]),
}


def write(word, x, baseline, height, slant=0.2, z=0):
    """`word` handwritten with its left edge at x on `baseline`, `height` pt
    to the top of an ascender; its strokes and its box."""
    strokes, xs, ys = [], [], []
    for ch in word:
        lift = rng.uniform(-0.03, 0.03)          # each letter sits a little off the line
        grow = rng.uniform(0.95, 1.06)
        for path in GLYPHS[ch]:
            pts = [(x + (u - v * slant) * height * grow + rng.uniform(-0.12, 0.12),
                    baseline + (v + lift) * height * grow + rng.uniform(-0.12, 0.12)) for u, v in path]
            dot = len(path) == 3 and math.dist(path[0], path[-1]) < 0.05
            strokes.append(pencil(pts, width=WRITING_W * (1.5 if dot else 1), jitter=0.12, step=0.45, z=z + len(strokes)))
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
    return strokes, (min(xs), min(ys), max(xs), max(ys))


def ellipse(box, pad=(6, 4), turns=1.12, start=-2.6, tilt=-0.03):
    """A hand's ring round `box`: an ellipse, slightly turned, overshooting its start."""
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 + pad[0], (y1 - y0) / 2 + pad[1]
    pts = []
    for i in range(15):
        a = start + i / 14 * turns * 2 * math.pi
        r = 1 + 0.05 * math.sin(i * 1.9) + 0.04 * i / 14
        px, py = math.cos(a) * rx * r, math.sin(a) * ry * r
        pts.append((cx + px * math.cos(tilt) - py * math.sin(tilt), cy + px * math.sin(tilt) + py * math.cos(tilt)))
    return pts


# --- the note ----------------------------------------------------------------------------------

def marks(doc):
    """{page: [Stroke]} and the margin words' boxes, placed from the text."""
    p1, p2 = lines_of(doc[0]), lines_of(doc[1])
    out, z = {1: [], 2: []}, 0

    def add(page, stroke_or_list):
        nonlocal z
        for s in stroke_or_list if isinstance(stroke_or_list, list) else [stroke_or_list]:
            s.z = z
            z += 1
            out[page].append(s)

    # The abstract's first sentence, highlighted line by line.
    first = line_starting(p1, 'The ability to engineer')
    second = line_starting(p1, 'within a quantum processor')
    third = line_starting(p1, 'systems')
    ends = find(p1, 'systems1,2.')[0]
    for line, x1 in ((first, first[2]), (second, second[2]), (third, ends[2])):
        add(1, highlighter(line[0], x1, (line[1] + line[3]) / 2 + 0.3))

    # "key!" in the blank margin left of the abstract, an arrow up to the sentence.
    third_mid = (third[1] + third[3]) / 2
    key, key_box = write('key!', 165, third_mid + 10, 13.5)
    add(1, key)
    tail = (key_box[2] - 6, key_box[1] - 2.5)
    tip = (first[0] - 3.5, (second[1] + second[3]) / 2 - 1)
    mid = ((tail[0] + tip[0]) / 2 - 3, (tail[1] + tip[1]) / 2 - 3)
    add(1, pencil([tail, mid, tip], jitter=0.15))
    ang = math.atan2(tip[1] - mid[1], tip[0] - mid[0])
    barb = lambda side: (tip[0] - 6 * math.cos(ang + side * 0.55), tip[1] - 6 * math.sin(ang + side * 0.55))
    add(1, pencil([barb(1), tip, barb(-1)], jitter=0.08, smooth=False))

    # An underline under the claim.
    under = find(p1, 'dynamic, non-local connectivity,')[0]
    base = under[3] - 0.6
    add(1, pencil([(under[0] - 1.5, base + 0.8), ((under[0] + under[2]) / 2, base + 0.2), (under[2] + 2, base - 0.6)], jitter=0.2))

    # "how?" beside the line on transport.
    moved = line_starting(p1, 'are coherently transported')
    how, how_box = write('how?', 160, moved[3] - 2.0, 13.5)
    add(1, how)

    # A ring round "Rydberg states".
    add(1, pencil(ellipse(find(p1, 'Rydberg states')[0]), jitter=0.2))

    # A bracket in the right margin by the code states.
    top, bottom = line_starting(p1, 'seven-qubit Steane'), line_starting(p1, 'state on a torus')
    bx = max(top[2], bottom[2], line_starting(p1, 'realize a surface code')[2]) + 4
    add(1, pencil([(bx, top[1] + 1), (bx + 4, top[1] + 0.5), (bx + 4.6, (top[1] + bottom[3]) / 2),
                   (bx + 4, bottom[3] - 0.5), (bx - 0.5, bottom[3])], jitter=0.12))

    # Page 2: the caption's finding, highlighted.
    c1 = line_starting(p2, 'c, Parity oscillations')
    c2 = line_starting(p2, 'entanglement or coherence.')
    add(2, highlighter(c1[0], c1[2], (c1[1] + c1[3]) / 2 + 0.3))
    add(2, highlighter(c2[0], find(p2, 'entanglement or coherence.')[0][2], (c2[1] + c2[3]) / 2 + 0.3))
    return out, {'key': key_box, 'how': how_box}


def build(pdf_bytes: bytes, out: Path) -> dict:
    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    sizes = [(p.rect.width, p.rect.height) for p in doc]
    width = sizes[0][0]
    # Notability's canvas: the PDF pages stacked in document units of the
    # page width W, each bottom-aligned in its slot, x offset by the paper's
    # inset (gamma/notability_map.py frames, docs/research/notability-format.md).
    stub = nb.Note(title=TITLE, width=width, paper=nb.PaperSpec(size='letter', sizing='lockedWidth'),
                   layout=[nb.LayoutEntry(PDF_NAME, i) for i in range(1, len(sizes) + 1)],
                   pdfs={PDF_NAME: nb.PdfFile(PDF_NAME, f'PDFs/{PDF_NAME}', 2)})
    frames, warnings = nmap.frames(stub, {PDF_NAME: sizes}, [])
    if warnings:
        raise ValueError(warnings)
    by_page, words = marks(doc)
    strokes = []
    for page, items in by_page.items():
        f = frames[page - 1]
        for s in items:
            s.anchors = [(x / f.k + f.ox, y / f.k + f.oy) for x, y in s.anchors]
            s.uuid = nw.new_uuid()
            strokes.append(s)
    draft = nw.Draft(title=TITLE, width=width, paper=nw.Paper(),
                     created=datetime.datetime(2026, 9, 12, 19, 40, tzinfo=datetime.UTC),
                     modified=datetime.datetime(2026, 9, 12, 20, 5, tzinfo=datetime.UTC),
                     layout=[(PDF_NAME, i) for i in range(1, len(sizes) + 1)], pdfs=[PDF_NAME],
                     strokes=strokes, files={f'PDFs/{PDF_NAME}': pdf_bytes})
    out.parent.mkdir(parents=True, exist_ok=True)
    nw.write_note(draft, out)
    return {'file': str(out), 'title': TITLE, 'strokes': len(strokes),
            'pages': {str(p): len(v) for p, v in by_page.items()},
            'highlighter': {str(p): sum(s.style == nb.STYLE_HIGHLIGHTER for s in v) for p, v in by_page.items()},
            'words': {k: [round(v, 1) for v in b] for k, b in words.items()}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--pdf', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'artifacts/readme-media/ink-import' / f'{TITLE}.note')
    args = parser.parse_args()
    summary = build(args.pdf.read_bytes() if args.pdf else curated_pdf(), args.out)
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))
