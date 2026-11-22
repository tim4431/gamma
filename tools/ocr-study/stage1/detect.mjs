// Scan detector for pdf.js pages: is this page a scanned image with an
// invisible OCR text layer over it?
//
// Two steps, both pure (no pdf.js import; `OPS` is passed in):
//
//   prescreen(tc)  on the TextContent alone, cheap (0.1-0.4 ms). It never misses a
//                  scan with an OCR layer in the study corpus (418 pages, every
//                  page past a 1.5x margin on some marker) and lets 4.9% of
//                  born-digital pages through. Its markers are the traces a
//                  layer measured off a page image leaves in the text geometry,
//                  which typesetting does not:
//                    mirror   most text drawn in a y-flipped text space (a > 0, d < 0)
//                    scaled   most text horizontally scaled (Tz fitted to each word box)
//                    sizes    many distinct sizes, none dominant (sizes measured from ink)
//                    lattice  word origins on a pixel grid of a scan resolution
//   confirm(...)   on the page's operator list (one extra worker pass): nearly
//                  all shown glyphs are in an invisible render mode (Tr & 3 == 3)
//                  and the union of the painted images covers most of the page.
//   ScanMemo       per document: once a page is confirmed, its fonts answer
//                  for later pages drawn in the same fonts, so a scanned book
//                  pays for one or two operator lists, not one per page.
//
// Usage in the viewer:
//   const pre = prescreen(tc);
//   if (!pre.pass) -> born-digital
//   else memo.guess(pre) ?? (c = confirm(await page.getOperatorList(), vp.transform, vp.width, vp.height, OPS), memo.record(c), c.scan)
//
// CLI: node detect.mjs [options] pdf... (see the bottom of the file).

// ---------------------------------------------------------------- prescreen

export const PRESCREEN = {
  mirror: 0.5, // share of upright chars in a y-flipped text space
  scaled: 0.2, // share of upright chars with |a / d| off 1 by more than SCALE_TOL
  scaleTol: 0.03,
  sizes: 10, // at least this many distinct sizes (0.05 pt bins) ...
  topSize: 0.3, // ... and the most common one under this share of chars
  lattice: 0.5, // share of word origins on one pixel lattice
  latticeMinWords: 3,
  latticeEps: 0.012, // pt: positions are often written with 2 decimals after a Tz shift
  // 72 / dpi for 72, 96, 100, 150, 200, 300, 400, 600 dpi
  steps: [1, 0.75, 0.72, 0.48, 0.36, 0.24, 0.18, 0.12],
};

const r3 = (v) => Math.round(v * 1000) / 1000;

// Upright (left-to-right, unrotated) items with visible characters.
function uprightWords(items) {
  const out = [];
  for (const it of items) {
    if (typeof it.str !== "string" || !it.str.trim()) continue;
    const [a, b, c, d] = it.transform;
    if (!(a > 0) || Math.abs(b) > 1e-3 * a || Math.abs(c) > 1e-3 * Math.abs(d) || !d) continue;
    out.push(it);
  }
  return out;
}

function latticeShare(words, P) {
  const n = words.length;
  if (n < P.latticeMinWords) return { share: 0, step: 0 };
  const refs = [words[0], words[n >> 1], words[n - 1]];
  const on = (v, q) => { const r = v / q; return Math.abs(r - Math.round(r)) * q < P.latticeEps; };
  let best = 0, step = 0;
  for (const q of P.steps) {
    for (const ref of refs) {
      const x0 = ref.transform[4], y0 = ref.transform[5];
      let k = 0;
      for (const w of words) if (on(w.transform[4] - x0, q) && on(w.transform[5] - y0, q)) k++;
      // a coarser step wins ties: a 0.24 lattice is also a 0.12 one
      if (k / n > best + 1e-9) { best = k / n; step = q; }
    }
  }
  return { share: best, step };
}

export function prescreenStats(tc, P = PRESCREEN) {
  const words = uprightWords(tc.items);
  let chars = 0, mirror = 0, scaled = 0;
  const sizes = new Map();
  const fonts = {};
  for (const w of words) {
    const [a, , , d] = w.transform;
    const n = w.str.trim().length;
    chars += n;
    fonts[w.fontName] = (fonts[w.fontName] || 0) + n;
    if (d < 0) mirror += n;
    if (Math.abs(a / Math.abs(d) - 1) > P.scaleTol) scaled += n;
    const h = Math.round(Math.abs(d) * 20);
    sizes.set(h, (sizes.get(h) || 0) + n);
  }
  let top = 0;
  for (const v of sizes.values()) if (v > top) top = v;
  const lat = latticeShare(words, P);
  return {
    words: words.length, chars,
    mirror: chars ? mirror / chars : 0,
    scaled: chars ? scaled / chars : 0,
    sizes: sizes.size, topSize: chars ? top / chars : 0,
    lattice: lat.share, step: lat.step, fonts,
  };
}

export function prescreen(tc, P = PRESCREEN) {
  const s = prescreenStats(tc, P);
  if (!s.words) return { pass: false, why: "no upright text", stats: s };
  const hits = [];
  if (s.mirror >= P.mirror) hits.push(`mirror ${r3(s.mirror)}`);
  if (s.scaled >= P.scaled) hits.push(`scaled ${r3(s.scaled)}`);
  if (s.sizes >= P.sizes && s.topSize < P.topSize) hits.push(`sizes ${s.sizes} top ${r3(s.topSize)}`);
  if (s.lattice >= P.lattice) hits.push(`lattice ${r3(s.lattice)} @${s.step}`);
  return hits.length
    ? { pass: true, why: hits.join(", "), stats: s }
    : { pass: false, why: `typeset (mirror ${r3(s.mirror)}, scaled ${r3(s.scaled)}, sizes ${s.sizes}/${r3(s.topSize)}, lattice ${r3(s.lattice)})`, stats: s };
}

// ---------------------------------------------------------------- confirm

export const CONFIRM = {
  invisible: 0.9, // share of shown glyphs in render mode 3 or 7
  coverage: 0.7, // share of the page under the union of image boxes
  grid: 100, // cells per side for the union
};

const times = (m, n) => [
  m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
  m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
  m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5],
];

function unitBox(m) {
  const xs = [m[4], m[0] + m[4], m[2] + m[4], m[0] + m[2] + m[4]];
  const ys = [m[5], m[1] + m[5], m[3] + m[5], m[1] + m[3] + m[5]];
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

// Glyphs a show-text op draws, spaces and kerning numbers left out.
function glyphCount(arr) {
  let n = 0;
  if (!Array.isArray(arr)) return 0;
  for (const g of arr) {
    if (!g || typeof g !== "object") continue;
    if (g.isSpace) continue;
    const u = g.unicode;
    if (typeof u === "string" && u.trim() === "") continue;
    n++;
  }
  return n;
}

// opList: {fnArray, argsArray} of page.getOperatorList(); viewportTransform,
// width, height: page.getViewport({scale: 1}). The page's annotations are not
// the page's drawing and are skipped.
export function confirm(opList, viewportTransform, viewportWidth, viewportHeight, OPS, C = CONFIRM) {
  const { fnArray: fns, argsArray: args } = opList;
  const boxes = [];
  const stack = [];
  let ctm = viewportTransform, tr = 0, font = null, visible = 0, invisible = 0;
  const fonts = {};
  const image = (m) => boxes.push(unitBox(m));
  const show = (glyphs) => {
    const n = glyphCount(glyphs);
    const e = (fonts[font] ||= [0, 0]);
    if ((tr & 3) === 3) { invisible += n; e[1] += n; } else { visible += n; e[0] += n; }
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
      case OPS.showText: case OPS.showSpacedText: show(a[0]); break;
      case OPS.nextLineShowText: show(a[0]); break;
      case OPS.nextLineSetSpacingShowText: show(a[2]); break;
      case OPS.paintFormXObjectBegin: stack.push([ctm, tr, font]); if (a[0]) ctm = times(ctm, a[0]); break;
      case OPS.paintFormXObjectEnd: { const s = stack.pop(); if (s) [ctm, tr, font] = s; break; }
      case OPS.paintImageXObject: case OPS.paintInlineImageXObject:
      case OPS.paintImageMaskXObject: case OPS.paintSolidColorImageMask:
        image(ctm);
        break;
      case OPS.paintImageMaskXObjectGroup:
        for (const im of a[0] || []) image(times(ctm, im.transform || [1, 0, 0, 1, 0, 0]));
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
  const coverage = unionCoverage(boxes, viewportWidth, viewportHeight, C.grid);
  const glyphs = visible + invisible;
  const invisibleShare = glyphs ? invisible / glyphs : 0;
  const scan = glyphs > 0 && invisibleShare >= C.invisible && coverage >= C.coverage;
  const why = !glyphs ? "no text shown"
    : `${scan ? "scan" : "not a scan"}: invisible ${r3(invisibleShare)} of ${glyphs} glyphs, images cover ${r3(coverage)} (${boxes.length} boxes)`;
  return { scan, why, invisibleShare, coverage, glyphs, images: boxes.length, fonts };
}

// ---------------------------------------------------------------- per-document memo
//
// pdf.js font ids (`fontName`, `setFont`'s first argument) are per document. An
// OCR layer's fonts draw nothing but invisible text, and a born-digital
// page's fonts draw visible text, so a confirmed page tells how its fonts are
// used. After the prescreen passes, `guess(prescreenResult)` answers
//   true   every char is in a font seen only invisible, on confirmed scan pages;
//   false  every char is in a font seen only visible;
//   null   anything else: run the confirm, then `record(confirmResult)`.
// A wrong `true` can only reshape text that is invisible anyway (a page whose
// image went missing); a wrong `false` needs an OCR font drawing visible text
// on another page of the same file.
export class ScanMemo {
  constructor() { this.fonts = new Map(); }
  record(conf) {
    for (const [id, [vis, inv]] of Object.entries(conf.fonts || {})) {
      const e = this.fonts.get(id) || { vis: 0, inv: 0, scans: 0 };
      e.vis += vis; e.inv += inv; if (conf.scan && inv) e.scans++;
      this.fonts.set(id, e);
    }
  }
  guess(pre) {
    const ids = Object.keys(pre.stats?.fonts || {});
    if (!ids.length) return null;
    const es = ids.map((id) => this.fonts.get(id));
    if (es.some((e) => !e)) return null;
    if (es.every((e) => e.vis === 0 && e.inv > 0 && e.scans > 0)) return true;
    if (es.every((e) => e.vis > 0 && e.inv === 0)) return false;
    return null;
  }
}

// Share of the page under the union of boxes, on a grid of cell centres.
function unionCoverage(boxes, w, h, G) {
  if (!boxes.length || !(w > 0) || !(h > 0)) return 0;
  const cells = new Uint8Array(G * G);
  for (const [x1, y1, x2, y2] of boxes) {
    const i0 = Math.max(0, Math.ceil((x1 / w) * G - 0.5)), i1 = Math.min(G - 1, Math.floor((x2 / w) * G - 0.5));
    const j0 = Math.max(0, Math.ceil((y1 / h) * G - 0.5)), j1 = Math.min(G - 1, Math.floor((y2 / h) * G - 0.5));
    for (let j = j0; j <= j1; j++) cells.fill(1, j * G + i0, j * G + i1 + 1);
  }
  let n = 0;
  for (const c of cells) n += c;
  return n / (G * G);
}

// ---------------------------------------------------------------- CLI
//
// node detect.mjs --out file.jsonl [--max N] [--confirm all|pass] [--sample sample.json] (list.txt | pdf...)
//   --max N        at most N pages per file, spread evenly (default every page)
//   --confirm all  run getOperatorList + confirm on every page (positives)
//   --confirm pass only on pages the prescreen passes (default), plus the
//                  (file, page) pairs listed in --sample (a JSON array of [file, page])
// Each line of the output: {f, pn, pre: {pass, why, stats}, conf?, t: {tc, pre, ol, conf}}.

const isMain = process.argv[1] && import.meta.url.toLowerCase().endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop().toLowerCase());
if (isMain) {
  process.on("unhandledRejection", (e) => console.error("unhandled", e?.message || e));
  process.on("uncaughtException", (e) => console.error("uncaught", e?.message || e));
  const fs = await import("node:fs");
  // the repo's pdf.js, resolved from this file's location (tools/ocr-study/stage1)
  const PDFJS = new URL("../../../frontend/node_modules/pdfjs-dist/legacy/build/pdf.mjs", import.meta.url);
  const { getDocument, OPS } = await import(PDFJS.href);
  const argv = process.argv.slice(2);
  const opt = { out: null, max: Infinity, confirm: "pass", sample: null };
  const files = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--out") opt.out = argv[++i];
    else if (a === "--max") opt.max = Number(argv[++i]);
    else if (a === "--confirm") opt.confirm = argv[++i];
    else if (a === "--sample") opt.sample = argv[++i];
    else if (a.endsWith(".txt")) files.push(...fs.readFileSync(a, "utf8").split(/\r?\n/).filter(Boolean));
    else files.push(a);
  }
  const norm = (f) => f.replace(/\\/g, "/").toLowerCase();
  const sample = new Set();
  if (opt.sample) for (const [f, pn] of JSON.parse(fs.readFileSync(opt.sample, "utf8"))) sample.add(`${norm(f)}#${pn}`);
  if (opt.out) fs.writeFileSync(opt.out, "");
  const emit = (rec) => { if (opt.out) fs.appendFileSync(opt.out, JSON.stringify(rec) + "\n"); else console.log(`${rec.f.split(/[\\/]/).pop()} p${rec.pn}: pre ${rec.pre.pass ? "PASS" : "-"} (${rec.pre.why})${rec.conf ? ` | ${rec.conf.why}` : ""}`); };
  const spread = (n, k) => { if (n <= k) return [...Array(n).keys()].map((i) => i + 1); const s = new Set(); for (let i = 0; i < k; i++) s.add(1 + Math.round((i * (n - 1)) / (k - 1))); return [...s]; };
  const now = () => performance.now();
  for (const f of files) {
    let doc;
    try { doc = await getDocument({ data: new Uint8Array(fs.readFileSync(f)), useSystemFonts: true, verbosity: 0, isEvalSupported: false }).promise; }
    catch (e) { console.error("open failed", f, e.message); continue; }
    for (const pn of spread(doc.numPages, opt.max)) {
      try {
        const page = await doc.getPage(pn);
        const t = {};
        let t0 = now();
        const tc = await page.getTextContent();
        t.tc = now() - t0;
        t0 = now();
        const pre = prescreen(tc, process.env.PRESCREEN_JSON ? { ...PRESCREEN, ...JSON.parse(process.env.PRESCREEN_JSON) } : PRESCREEN);
        t.pre = now() - t0;
        const rec = { f, pn, pre, t, items: tc.items.length };
        if (opt.confirm !== "none" && (opt.confirm === "all" || pre.pass) || sample.has(`${norm(f)}#${pn}`)) {
          const vp = page.getViewport({ scale: 1 });
          t0 = now();
          const ol = await page.getOperatorList();
          t.ol = now() - t0;
          t0 = now();
          rec.conf = confirm(ol, vp.transform, vp.width, vp.height, OPS);
          t.conf = now() - t0;
          rec.ops = ol.fnArray.length;
          rec.sampled = sample.has(`${norm(f)}#${pn}`);
        }
        emit(rec);
        page.cleanup();
      } catch (e) { console.error("page failed", f, pn, e.message); }
    }
    await doc.destroy();
  }
}
