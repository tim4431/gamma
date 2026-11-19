// A scan's invisible OCR text layer, repaired from its TextContent alone (no
// pixels): pdf/pageText.js hands a page here once pdf/scanDetect.js says it
// is a scanned picture with such a layer. Pure, no pdf.js import. The study
// behind it, with its corpus and numbers, is docs/research/ocr.md.
//
// An OCR producer writes each word where it found it, sized and spaced by
// guesses that the viewer then takes literally. The worst layers in the study
// size every word from its own ink (4 to 10 pt on one printed line), draw it
// in a substitute font's width (about 0.73 of the printed word) and write it
// in the picture's pixel rows, a y-flipped text space that pdf.js's TextLayer
// draws a whole line above the print. Selection, highlights, find marks and
// the translation's paragraphs (pdf/pdfTranslate.js reads lines from sizes
// and baselines) all follow those guesses. The repair gives each printed line
// one band and lets its words tile it:
//
//  1. Words: the selection string (items joined, a line break after hasEOL)
//     cut at white space, so the words, and what a reader selects and copies,
//     are exactly the layer's. Inside an item of several words, a word's box
//     is its share of the item's width by the advances of the face the
//     browser draws that family in.
//  2. Each word's printed band, from its matrix: a flipped one (d < 0) hangs
//     the print down from f, [f - |d|, f]; an upright one is the OCR
//     convention, size = em with the baseline at f, [f - 0.2h, f + 0.75h].
//  3. Lines: words chained in stream order, never sorted (OCR writes reading
//     order; sorting by position mixes columns and equation lines). A word
//     starts a new line when its band overlaps the line's by less than half
//     the smaller height, when it starts before the word before it ends (a
//     wrap, another column: the words of a line stand side by side), or when
//     the gap to it is a column gutter. backend/gamma/pdf_scan.py chains
//     MuPDF's lines into rows by the same rule.
//  4. One band per line. Where words of x-height letters ("a", "on", "une")
//     are drawn clearly shorter than words with ascenders, the boxes hug each
//     word's ink and the line takes their union (outliers aside: scripts,
//     stray marks); otherwise each box is an estimate of the line's em and
//     the line takes their medians.
//  5. Tiling: each word's span runs to where the next one's starts, the space
//     folded into it ("word "), the line's last word ending in hasEOL. Where
//     boxes hug the ink (the one such producer in the corpus centres them),
//     the cut lies in the middle of the gap between two boxes. Where a box is
//     an em estimate, it starts where the producer put the word and its width
//     is a substitute font's guess, its slack on its right: the cut then lies
//     at most CUT_MARGIN before the next box. The line's ends reach out by half
//     its median gap, within a line height and the page's box.
//
// What comes out is a TextContent pdf.js's TextLayer and every reader of
// items take as they are: items in stream order, one a word, upright
// [h, 0, 0, h, x, baseline] in PDF space (a one-character item [2h, ...]:
// see the tiling) with the span [baseline - 0.2h,
// baseline + 0.8h] the line's band (the styles' ascent 0.8 and descent -0.2
// say so to pdfTranslate.textRuns; TextLayer measures about 0.805 for a
// serif), font ids kept (pdf/pdfFonts.js and pdf/paperFace.js look them up),
// rotated items passed through. It carries `repaired: true`.

const ASC = 0.75, DESC = 0.2; // an upright OCR box: ascender and descender per em
const GUTTER = 3;             // a gap over this many line heights inside a line is a column gutter
const OVERLAP = 0.5;          // share of the smaller height two bands overlap to share a line
const TIGHT_RATIO = 0.85;     // x-height words under this share of ascender words' height: boxes hug the ink
const MIN_SAMPLES = 5;        // words of each kind that test needs on a page
const OUTLIER = 0.5;          // a band past the median edge by this × the median height is left out
const END_CAP = 1;            // a line's ends reach out at most this many line heights
const CUT_MARGIN = 0.35;      // an em box's cut lies at most this many line heights before the next box

// Natural advances (1/1000 em, ASCII 32..126) of Times and Helvetica, the
// faces pdf.js's serif and sans-serif fallbacks are drawn in; monospace is even.
const TIMES = [250, 333, 408, 500, 500, 833, 778, 333, 333, 333, 500, 564, 250, 333, 250, 278, 500, 500, 500, 500, 500, 500, 500, 500,
  500, 500, 278, 278, 564, 564, 564, 444, 921, 722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889, 722, 722, 556, 722,
  667, 556, 611, 722, 722, 944, 722, 722, 611, 333, 278, 333, 469, 500, 333, 444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500,
  278, 778, 500, 500, 500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444, 480, 200, 480, 541];
const HELV = [278, 278, 355, 556, 556, 889, 667, 222, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556,
  556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778,
  722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 222, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500,
  222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584];

function advance(ch, family) {
  if (family === "monospace") return 600;
  const table = family === "sans-serif" ? HELV : TIMES;
  let c = ch.charCodeAt(0);
  if (c < 32 || c > 126) c = ch.normalize("NFD").charCodeAt(0) || 0; // é as e
  if (c < 32 || c > 126) return family === "sans-serif" ? 556 : 500;
  return table[c - 32];
}

const median = (values) => {
  const s = [...values].sort((a, b) => a - b);
  return s.length ? (s[(s.length - 1) >> 1] + s[s.length >> 1]) / 2 : NaN;
};
const rotated = (t) => Math.abs(t[1]) > Math.abs(t[0]) * 0.05 || t[0] < 0 || Math.abs(t[2]) > Math.abs(t[3]) * 0.05 + 1e-9;
const sound = (it) => Array.isArray(it.transform) && it.transform.length === 6 &&
  it.transform.every(Number.isFinite) && Number.isFinite(it.width);

// The box of characters [c0, c1) of an item in PDF space: x by the advance
// shares `cum` (cumulative, over the item's characters), y the printed band.
function charBox(it, c0, c1, cum) {
  const t = it.transform;
  const total = cum[cum.length - 1] || 1;
  const x0 = t[4] + it.width * (cum[c0] / total), x1 = t[4] + it.width * (cum[c1] / total);
  const h = Math.abs(t[3]) || Math.hypot(t[2], t[3]);
  return t[3] < 0 ? { x0, x1, y0: t[5] - h, y1: t[5] } : { x0, x1, y0: t[5] - DESC * h, y1: t[5] + ASC * h };
}

// The page's words with their boxes, and rotated or unsound items as
// passthroughs ({pass}). A passthrough glued to a word with no white space
// between them (pdf.js joins "obtient" and a rotated "Rd&u)]") keeps that
// join: `glueNext` says to add neither a space nor a line break there.
function words(items, styles) {
  const out = [];
  const shares = new Map();
  const cumOf = (i) => {
    if (!shares.has(i)) {
      const it = items[i], family = styles[it.fontName]?.fontFamily, cum = [0];
      for (const ch of it.str) cum.push(cum[cum.length - 1] + advance(ch, family));
      // a surrogate pair is two chars of str but one of the loop: count chars then
      shares.set(i, cum.length === it.str.length + 1 ? cum : Array.from({ length: it.str.length + 1 }, (_, k) => k));
    }
    return shares.get(i);
  };
  let cur = null, lastPass = null;
  const flush = () => {
    if (cur?.parts.length) out.push(cur);
    cur = null;
  };
  items.forEach((it, i) => {
    if (typeof it.str !== "string") return;
    if (!sound(it) || rotated(it.transform)) {
      if (!it.str.length && !it.hasEOL) return;
      const joins = it.str.length > 0 && !/^\s/.test(it.str);
      if (cur && joins) cur.glueNext = true;
      if (lastPass && joins) lastPass.glueNext = true;
      flush();
      const pass = { pass: it };
      out.push(pass);
      lastPass = it.hasEOL || !it.str.length || /\s$/.test(it.str) ? null : pass;
      return;
    }
    const s = it.str;
    for (let c = 0; c < s.length; c++) {
      if (/\s/.test(s[c])) { flush(); lastPass = null; continue; }
      if (lastPass) { lastPass.glueNext = true; lastPass = null; }
      cur ||= { t: "", parts: [], fontName: it.fontName, dir: it.dir || "ltr" };
      cur.t += s[c];
      const last = cur.parts[cur.parts.length - 1];
      if (last && last[0] === i && last[2] === c) last[2] = c + 1;
      else cur.parts.push([i, c, c + 1]);
    }
    if (it.hasEOL) { flush(); lastPass = null; }
  });
  flush();
  for (const w of out) {
    if (w.pass) continue;
    // A word glued from several items takes the box of its first part and of
    // the parts that go on from it on its row: pdf.js glues a word across a
    // line wrap when it saw no line break, and that box would span two lines.
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const [i, c0, c1] of w.parts) {
      const b = charBox(items[i], c0, c1, cumOf(i));
      if (x1 > -Infinity) {
        const h = Math.min(y1 - y0, b.y1 - b.y0);
        const sameRow = Math.min(y1, b.y1) - Math.max(y0, b.y0) >= OVERLAP * h;
        if (!sameRow || b.x0 < x1 - 0.5 * h || b.x0 > x1 + 0.5 * h) break;
      }
      x0 = Math.min(x0, b.x0); x1 = Math.max(x1, b.x1); y0 = Math.min(y0, b.y0); y1 = Math.max(y1, b.y1);
    }
    Object.assign(w, { x0, x1, y0, y1, h: y1 - y0 });
    delete w.parts;
  }
  return out;
}

// Words chained into visual lines ({ws}), a passthrough ending a line.
function lines(seq) {
  const out = [];
  let line = null;
  for (const w of seq) {
    if (w.pass) { line = null; out.push(w); continue; }
    if (line) {
      const [b0, b1] = line.band;
      const prev = line.ws[line.ws.length - 1];
      const overlap = Math.min(b1, w.y1) - Math.max(b0, w.y0);
      const sameRow = overlap >= OVERLAP * Math.min(b1 - b0, w.h);
      if (!sameRow || w.x0 < prev.x1 || w.x0 - prev.x1 > GUTTER * (b1 - b0)) line = null;
    }
    if (!line) { line = { ws: [], band: null }; out.push(line); }
    line.ws.push(w);
    line.band = [median(line.ws.map((v) => v.y0)), median(line.ws.map((v) => v.y1))];
  }
  return out;
}

// Do this page's boxes hug each word's ink? Then words of x-height letters
// are drawn clearly shorter than words with an ascender or capital on the
// same lines; boxes sized to the line's em do not depend on the letters, even
// where sizes vary (equations, per-word size estimates).
const X_ONLY = /^[acemnorsuvwxz]+[.,;:]?$/, ASCENDER = /[bdfhiklt0-9A-Z]/;
function inkTight(lns) {
  const xs = [], asc = [];
  for (const l of lns) {
    if (!l.ws || l.ws.length < 3) continue;
    const m = median(l.ws.map((w) => w.h));
    if (!(m > 0)) continue;
    for (const w of l.ws) {
      if (X_ONLY.test(w.t)) xs.push(w.h / m);
      else if (ASCENDER.test(w.t)) asc.push(w.h / m);
    }
  }
  return xs.length >= MIN_SAMPLES && asc.length >= MIN_SAMPLES && median(xs) < TIGHT_RATIO * median(asc);
}

function lineBand(ws, tight) {
  const b0 = median(ws.map((w) => w.y0)), b1 = median(ws.map((w) => w.y1));
  if (!tight) return [b0, b1];
  const mh = median(ws.map((w) => w.h));
  const tops = ws.map((w) => w.y1).filter((y) => y <= b1 + OUTLIER * mh);
  const bottoms = ws.map((w) => w.y0).filter((y) => y >= b0 - OUTLIER * mh);
  return [Math.min(...bottoms), Math.max(...tops)];
}

// Steps 4 and 5 share their measures with pdf/ocrText.js, which lays out a
// recognized page's lines as these lines are laid out, so both select,
// search and translate alike.
//
// Each line of words ({ws}, boxes y up with `h` their height; a passthrough
// has none) gets its band (b0 to b1, height H) and the gaps between its
// words (`gaps`); a line of boxes with no height takes the page's median
// line height. Answers the page's median gap in line heights, which a
// one-word line reaches out by.
export function bandLines(lns, tight) {
  const gapsH = [];
  for (const l of lns) {
    if (!l.ws) continue;
    [l.b0, l.b1] = lineBand(l.ws, tight);
    l.H = l.b1 - l.b0;
    l.gaps = [];
    for (let k = 1; k < l.ws.length; k++) l.gaps.push(l.ws[k].x0 - l.ws[k - 1].x1);
    if (l.gaps.length && l.H > 0) gapsH.push(median(l.gaps) / l.H);
  }
  const pageH = median(lns.filter((l) => l.H > 0).map((l) => l.H)) || 1;
  for (const l of lns) if (l.ws && !(l.H > 0)) { l.b0 -= DESC * pageH; l.H = pageH; }
  return gapsH.length ? median(gapsH) : 0.4;
}

// Where the words of a banded line tile it: n + 1 cuts, word k's span
// running from cuts[k] to cuts[k + 1]. `pageGap` is bandLines' answer,
// `tight` whether the boxes hug the ink, and [left, right] the page, which
// the line's ends do not cross.
export function tileCuts({ ws, H, gaps }, { pageGap, tight, left = -Infinity, right = Infinity }) {
  const gap = gaps.length ? median(gaps) : pageGap * H;
  const reach = Math.min(Math.max(gap, 0) / 2, END_CAP * H);
  const n = ws.length;
  const cuts = [Math.max(ws[0].x0 - reach, Math.min(ws[0].x0, left))];
  // never backwards (a box inside its neighbour), so no width is negative
  for (let k = 1; k < n; k++) {
    const middle = (ws[k - 1].x1 + ws[k].x0) / 2;
    cuts[k] = Math.max(cuts[k - 1], tight ? middle : Math.max(middle, ws[k].x0 - CUT_MARGIN * H));
  }
  cuts[n] = Math.max(cuts[n - 1], Math.min(ws[n - 1].x1 + reach, Math.max(ws[n - 1].x1, right)));
  return cuts;
}

// The repaired TextContent of a page pdf/scanDetect.js called a scan. `view`
// is the page's box in PDF space (`page.view`, [x1, y1, x2, y2]), which a
// line's ends do not cross; without it the page's outermost boxes stand in.
export function repairTextContent(tc, { view = null } = {}) {
  const items = tc.items || [];
  const sourceStyles = tc.styles || {};
  const seq = words(items, sourceStyles);
  const lns = lines(seq);
  const tight = inkTight(lns);
  const pageGap = bandLines(lns, tight);
  // A line's ends stay on the page: its box, or the outermost boxes.
  let left = Infinity, right = -Infinity;
  if (view?.length === 4) [left, right] = [Math.min(view[0], view[2]), Math.max(view[0], view[2])];
  else for (const w of seq) if (!w.pass) { left = Math.min(left, w.x0); right = Math.max(right, w.x1); }

  const out = [];
  const used = new Set();
  lns.forEach((l, at) => {
    if (l.pass) {
      const it = l.pass;
      if (typeof it.fontName === "string") used.add(it.fontName);
      // A run of passed-through items (a rotated line, its words and pdf.js's
      // spaces) reads as pdf.js gave it; where a repaired line follows, a
      // line break keeps the two from running together, unless the input
      // glued them into one word.
      const meets = !lns[at + 1]?.pass;
      out.push(meets && !l.glueNext && !it.hasEOL && it.str && !/\s$/.test(it.str) ? { ...it, hasEOL: true } : it);
      return;
    }
    const { ws, b0, H } = l;
    const n = ws.length;
    const cuts = tileCuts(l, { pageGap, tight, left, right });
    ws.forEach((w, k) => {
      const last = k === n - 1;
      const str = last ? w.t : `${w.t} `;
      if (typeof w.fontName === "string") used.add(w.fontName);
      out.push({
        str,
        dir: w.dir,
        width: cuts[k + 1] - cuts[k],
        height: H,
        // TextLayer stretches a span of one character to its width only
        // when its matrix is stretched (by more than 1.5 across): the line's
        // last word "a" would otherwise keep a serif's width.
        transform: [[...str].length === 1 ? 2 * H : H, 0, 0, H, cuts[k], b0 + 0.2 * H],
        fontName: w.fontName,
        // the next (passed-through) item goes on with this word
        hasEOL: last && !w.glueNext,
      });
    });
  });
  const styles = {};
  for (const [name, style] of Object.entries(sourceStyles)) styles[name] = { ...style, ascent: 0.8, descent: -0.2 };
  for (const name of used) styles[name] ||= { fontFamily: "serif", ascent: 0.8, descent: -0.2, vertical: false };
  return { items: out, styles, lang: tc.lang ?? null, repaired: true };
}
