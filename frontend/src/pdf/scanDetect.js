// Is a PDF page a scanned picture with an invisible OCR text layer over it?
// pdf/pageText.js asks, and repairs such a page's layer (pdf/scanText.js).
// Pure: no pdf.js import, its operator table (`OPS`) is passed in. The
// server's twin, backend/gamma/pdf_scan.py, applies the same rule with
// PyMuPDF. The study behind both is docs/research/ocr.md.
//
// The rule (`confirm`) reads the page's operator list: nearly all the glyphs
// it shows are in an invisible render mode (Tr 3 or 7) and the pictures it
// paints cover most of the page. pdf.js's TextContent carries neither the
// render mode nor the pictures, and an operator list is a pass of its own
// through the page in the worker (images decoded), which the render does not
// share. So a cheap `prescreen` on the TextContent comes first, and passes a
// page only when its text bears a trace of having been measured off a
// picture: no born-digital page that fails it needs the operator list. Over
// a study corpus of 418 scan pages from seven OCR producers it missed none,
// each passing one of its markers by 1.5 times or more, and it passed 4.9% of
// 3,809 born-digital pages. Then a document's fonts answer for its later
// pages (`ScanMemo`), so a scanned book pays for an operator list or two, not
// one a page.

// The prescreen's markers. Any one passes the page.
const PRESCREEN = {
  // Most text drawn in a y-flipped text space (a > 0, d < 0): a layer written
  // in the picture's pixel rows.
  mirror: 0.5,
  // Most text stretched across (|a/d| off 1): Tz fitted to each word's box.
  scaled: 0.2,
  scaleTol: 0.03,
  // Many sizes (in 0.05 pt steps), none of them most of the text: each word
  // sized from its own ink.
  sizes: 10,
  topSize: 0.3,
  // Most word origins on a pixel grid of a scan resolution (72 / dpi for 72,
  // 96, 100, 150, 200, 300, 400 and 600 dpi), within a rounding of positions
  // written to two decimals.
  lattice: 0.5,
  latticeMinWords: 3,
  latticeEps: 0.012,
  steps: [1, 0.75, 0.72, 0.48, 0.36, 0.24, 0.18, 0.12],
};

// The confirm: invisible glyphs (non-space) out of all shown, and the share of
// the page under the union of the painted pictures (on a grid of cell
// centres). A scan's coverage was 0.81 at least (a page's margins left
// white); a born-digital page with invisible text (LaTeXiT's payloads) had
// 0.17 at most, and its invisible share alone reached 0.97.
const CONFIRM = { invisible: 0.9, coverage: 0.7, grid: 100 };

// Upright, left-to-right items with visible characters.
function uprightItems(items) {
  return (items || []).filter((it) => {
    if (typeof it.str !== "string" || !it.str.trim() || !Array.isArray(it.transform)) return false;
    const [a, b, c, d] = it.transform;
    return a > 0 && Math.abs(b) <= 1e-3 * a && Math.abs(c) <= 1e-3 * Math.abs(d) && !!d;
  });
}

// The largest share of `words` whose origins lie on one lattice of the steps,
// counted from the first, middle or last word.
function latticeShare(words, P) {
  const n = words.length;
  if (n < P.latticeMinWords) return 0;
  const on = (v, q) => Math.abs(v / q - Math.round(v / q)) * q < P.latticeEps;
  let best = 0;
  for (const q of P.steps) {
    for (const ref of [words[0], words[n >> 1], words[n - 1]]) {
      const [, , , , x0, y0] = ref.transform;
      let k = 0;
      for (const w of words) if (on(w.transform[4] - x0, q) && on(w.transform[5] - y0, q)) k++;
      best = Math.max(best, k / n);
    }
  }
  return best;
}

// {pass, why, fonts}: whether the page's TextContent could be a scan's OCR
// layer, and the characters drawn in each of its fonts (for ScanMemo).
export function prescreen(tc, P = PRESCREEN) {
  const words = uprightItems(tc?.items);
  let chars = 0, mirror = 0, scaled = 0, top = 0;
  const sizes = new Map(), fonts = {};
  for (const w of words) {
    const [a, , , d] = w.transform;
    const n = w.str.trim().length;
    chars += n;
    fonts[w.fontName] = (fonts[w.fontName] || 0) + n;
    if (d < 0) mirror += n;
    if (Math.abs(a / Math.abs(d) - 1) > P.scaleTol) scaled += n;
    const size = Math.round(Math.abs(d) * 20);
    sizes.set(size, (sizes.get(size) || 0) + n);
  }
  if (!chars) return { pass: false, why: "no upright text", fonts };
  for (const v of sizes.values()) top = Math.max(top, v);
  const marks = [];
  if (mirror / chars >= P.mirror) marks.push("mirror");
  if (scaled / chars >= P.scaled) marks.push("scaled");
  if (sizes.size >= P.sizes && top / chars < P.topSize) marks.push("sizes");
  if (latticeShare(words, P) >= P.lattice) marks.push("lattice");
  return { pass: marks.length > 0, why: marks.length ? marks.join(", ") : "typeset", fonts };
}

const times = (m, n) => [
  m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
  m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
  m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5],
];

// The box a picture painted through `m` takes: the unit square mapped.
function unitBox(m) {
  const xs = [m[4], m[0] + m[4], m[2] + m[4], m[0] + m[2] + m[4]];
  const ys = [m[5], m[1] + m[5], m[3] + m[5], m[1] + m[3] + m[5]];
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

// Glyphs a show-text op draws, spaces and kerning numbers left out.
function glyphCount(glyphs) {
  let n = 0;
  for (const g of Array.isArray(glyphs) ? glyphs : []) {
    if (!g || typeof g !== "object" || g.isSpace) continue;
    if (typeof g.unicode === "string" && !g.unicode.trim()) continue;
    n++;
  }
  return n;
}

// Share of a w × h page under the union of `boxes`, on a grid of cell centres.
function unionCoverage(boxes, w, h, G) {
  if (!boxes.length || !(w > 0) || !(h > 0)) return 0;
  const cells = new Uint8Array(G * G);
  for (const [x1, y1, x2, y2] of boxes) {
    const i0 = Math.max(0, Math.ceil((x1 / w) * G - 0.5)), i1 = Math.min(G - 1, Math.floor((x2 / w) * G - 0.5));
    const j0 = Math.max(0, Math.ceil((y1 / h) * G - 0.5)), j1 = Math.min(G - 1, Math.floor((y2 / h) * G - 0.5));
    for (let j = j0; j <= j1; j++) if (i1 >= i0) cells.fill(1, j * G + i0, j * G + i1 + 1);
  }
  let n = 0;
  for (const c of cells) n += c;
  return n / (G * G);
}

// {scan, why, invisibleShare, coverage, fonts}: the rule on the page's operator
// list (page.getOperatorList()), walked from the scale-1 viewport's transform so
// the pictures' boxes are in the viewport's terms. The render mode and the CTM
// are tracked through save/restore and forms; the page's annotations are not
// the page's drawing and end the walk. `fonts` holds each font's visible and
// invisible glyph counts (for ScanMemo).
export function confirm(list, viewport, OPS, C = CONFIRM) {
  const fns = list?.fnArray || [], args = list?.argsArray || [];
  const boxes = [], stack = [], fonts = {};
  let ctm = viewport.transform, tr = 0, font = null, visible = 0, invisible = 0;
  const image = (m) => boxes.push(unitBox(m));
  const show = (glyphs) => {
    const n = glyphCount(glyphs);
    const counts = (fonts[font] ||= [0, 0]);
    if ((tr & 3) === 3) { invisible += n; counts[1] += n; } else { visible += n; counts[0] += n; }
  };
  for (let i = 0; i < fns.length; i++) {
    const fn = fns[i], a = args[i];
    if (fn === OPS.beginAnnotation) break;
    switch (fn) {
      case OPS.save: stack.push([ctm, tr, font]); break;
      case OPS.restore: { const s = stack.pop(); if (s) [ctm, tr, font] = s; break; }
      case OPS.transform: ctm = times(ctm, a); break;
      case OPS.setTextRenderingMode: tr = a[0]; break;
      case OPS.setFont: font = a[0]; break;
      case OPS.showText: case OPS.showSpacedText: case OPS.nextLineShowText: show(a[0]); break;
      case OPS.nextLineSetSpacingShowText: show(a[2]); break;
      case OPS.paintFormXObjectBegin: stack.push([ctm, tr, font]); if (a?.[0]) ctm = times(ctm, a[0]); break;
      case OPS.paintFormXObjectEnd: { const s = stack.pop(); if (s) [ctm, tr, font] = s; break; }
      case OPS.paintImageXObject: case OPS.paintInlineImageXObject:
      case OPS.paintImageMaskXObject: case OPS.paintSolidColorImageMask:
        image(ctm);
        break;
      case OPS.paintImageMaskXObjectGroup:
        for (const im of a?.[0] || []) image(times(ctm, im.transform || [1, 0, 0, 1, 0, 0]));
        break;
      case OPS.paintImageMaskXObjectRepeat: {
        const [, sx, kx, ky, sy, at] = a;
        for (let p = 0; p + 1 < at.length; p += 2) image(times(ctm, [sx, kx, ky, sy, at[p], at[p + 1]]));
        break;
      }
      case OPS.paintImageXObjectRepeat: {
        const [, sx, sy, at] = a;
        for (let p = 0; p + 1 < at.length; p += 2) image(times(ctm, [sx, 0, 0, sy, at[p], at[p + 1]]));
        break;
      }
      default:
    }
  }
  const coverage = unionCoverage(boxes, viewport.width, viewport.height, C.grid);
  const glyphs = visible + invisible;
  const invisibleShare = glyphs ? invisible / glyphs : 0;
  const scan = glyphs > 0 && invisibleShare >= C.invisible && coverage >= C.coverage;
  const why = glyphs ? `invisible ${invisibleShare.toFixed(3)} of ${glyphs} glyphs, pictures cover ${coverage.toFixed(3)}` : "no text shown";
  return { scan, why, invisibleShare, coverage, fonts };
}

// What a document's confirmed pages say about its fonts. pdf.js names a
// font once per document (the TextContent's fontName is the operator list's
// setFont), and an OCR layer's fonts draw nothing visible while a
// born-digital page's draw what it shows. After a page passes the prescreen,
// `guess(pre)` answers true when every character is in fonts seen only
// invisible on confirmed scan pages, false when every one is in fonts seen
// only visible, and null otherwise: then the page is confirmed and
// `record`ed. A wrong true could only reshape text that is invisible anyway;
// a wrong false needs an OCR font that draws visible text elsewhere in the
// file. In page order the study's scans needed 20 operator lists for 418
// pages, and its born-digital passes 88 for 185, with no wrong answer.
export class ScanMemo {
  constructor() { this.fonts = new Map(); }

  record(conf) {
    for (const [id, [vis, inv]] of Object.entries(conf.fonts || {})) {
      const e = this.fonts.get(id) || { vis: 0, inv: 0, scans: 0 };
      e.vis += vis;
      e.inv += inv;
      if (conf.scan && inv) e.scans++;
      this.fonts.set(id, e);
    }
  }

  guess(pre) {
    const seen = Object.keys(pre.fonts || {}).map((id) => this.fonts.get(id));
    if (!seen.length || seen.some((e) => !e)) return null;
    if (seen.every((e) => e.vis === 0 && e.inv > 0 && e.scans > 0)) return true;
    if (seen.every((e) => e.vis > 0 && e.inv === 0)) return false;
    return null;
  }
}
