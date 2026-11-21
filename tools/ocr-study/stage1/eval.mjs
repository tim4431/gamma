// Score a text-layer repair against the ink ground truth.
//
// usage: node eval.mjs [repo | baseline | oracle | <candidate.mjs>] [--file ID] [--page N] [--json out.json] [--quiet]
//   repo      (the default) the repository's frontend/src/pdf/scanText.js repairTextContent
//   baseline  the original items as pdf.js TextLayer draws them today
//   oracle    items built from the truth itself (a ceiling check of the metrics)
//   *.mjs     a module exporting repairTextContent(tc) -> {items, styles, lang, repaired}
//
// Inputs: pages.json, and under $OCR_STUDY_DIR/stage1: words/<file>-p<N>.json
// (dump_words.mjs: the page's TextContent, viewport and words),
// truth/<file>-p<N>.json (truth.py).
// Metric definitions: see METRICS below (also printed with --help).
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { PAGES, SRC, STUDY } from "./files.mjs";
import { tokenize, wordSpans, selection, isRotated } from "./tokens.mjs";

const METRICS = `
Words are the whitespace-separated tokens of the layer's selection string (items joined, "\\n" after hasEOL).
Output words are matched to truth words in stream order (LCS on the strings), so a folded trailing space is ignored.
Only truth words marked confident (truth.py) are scored for geometry; lines only when their ink band is confident.
Spans: x [e, e+width], y [f-(1-A)h, f+A h], h = hypot(transform[2], transform[3]) (sign ignored, as TextLayer does);
  A = 0.805 for baseline (TextLayer's measured serif ascent), 0.8 for candidates (the contract).
  A word inside a multi-word item gets its share of the item by Times/Helvetica/mono advances, up to the next word.
vertical  vTop, vBot  mean |span top - ink top|, |span bottom - ink bottom| over the line's ink height (per word)
          lineOK      share of lines whose span band (median of its words' span tops/bottoms) covers >= 90% of the
                      ink band and runs past it by <= 25% of the ink height in all
horizontal own        mean share of a word's ink x-extent inside its own span's x-range (x only)
          hit         the same, but only if the own span also contains the ink band's middle row (pointer on the word)
          stolen      mean share of a word's ink x-extent inside OTHER words' spans that contain its ink middle row
          |L|, L, R   mean |left edge error|, signed left error, signed right error (span edge - ink edge), in line heights
                      (a folded-space layer has R ~ +word gap for words that are not last on their line)
          cover       share of a line's ink columns covered by some span containing the line's middle row
text      text        page passes when the output's words equal the input's words in order; tokDiff = unmatched tokens
segment   blocks, trans  segmentPage(textRuns(items, vp.transform, {styles})) blocks and translatable blocks per page
          lpb         mean lines per block
invariants (contract) order (multiset-matched source order never decreases), shape (repaired, str, dir, width,
          upright transform [h,0,0,h,x,b], fontName styled with ascent .8/descent -.2, no " "/"" items,
          trailing space on non-EOL items, x+width = next x within 2% h on a line), NaN, page (inside page, width <= page)
`;

const argv = process.argv.slice(2);
if (argv.includes("--help")) { console.log("usage: node eval.mjs [repo|baseline|oracle|candidate.mjs] [--file ID] [--page N] [--json out.json] [--quiet]\n" + METRICS); process.exit(0); }
const mode = argv.length && !argv[0].startsWith("--") ? argv[0] : "repo";
const opt = (k) => (argv.includes(k) ? argv[argv.indexOf(k) + 1] : null);
const onlyFile = opt("--file"), onlyPage = opt("--page") ? Number(opt("--page")) : null, jsonOut = opt("--json");
const quiet = argv.includes("--quiet");

const { textRuns, segmentPage } = await import(pathToFileURL(`${SRC}/pdfTranslate.js`).href);
let repair = null;
if (mode !== "baseline" && mode !== "oracle") {
  const path = mode === "repo" ? `${SRC}/scanText.js` : resolve(mode);
  const mod = await import(pathToFileURL(path).href);
  repair = mod.repairTextContent || mod.default;
  if (typeof repair !== "function") throw new Error(`${path} exports no repairTextContent`);
}
const pages = JSON.parse(readFileSync(PAGES, "utf8"));
const show = new Set(pages.show || []);

// ---------- helpers
const ov = (a0, a1, b0, b1) => Math.max(0, Math.min(a1, b1) - Math.max(a0, b0));
const median = (v) => { const s = [...v].sort((a, b) => a - b); return s.length ? s[(s.length - 1) >> 1] * 0.5 + s[s.length >> 1] * 0.5 : NaN; };
function unionLen(intervals, lo, hi) { // length of the union of intervals clipped to [lo, hi]
  const iv = intervals.map(([a, b]) => [Math.max(a, lo), Math.min(b, hi)]).filter(([a, b]) => b > a).sort((p, q) => p[0] - q[0]);
  let len = 0, cur = null;
  for (const [a, b] of iv) {
    if (!cur || a > cur[1]) { if (cur) len += cur[1] - cur[0]; cur = [a, b]; } else cur[1] = Math.max(cur[1], b);
  }
  if (cur) len += cur[1] - cur[0];
  return len;
}
function lcsAlign(a, b) { // a, b: string arrays -> pairs [i, j]
  const n = a.length, m = b.length;
  if (n === m && a.every((v, i) => v === b[i])) return a.map((_, i) => [i, i]);
  const W = m + 1, dp = new Uint16Array((n + 1) * W);
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i * W + j] = a[i] === b[j] ? dp[(i + 1) * W + j + 1] + 1 : Math.max(dp[(i + 1) * W + j], dp[i * W + j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push([i, j]); i++; j++; } else if (dp[(i + 1) * W + j] >= dp[i * W + j + 1]) i++; else j++;
  }
  return out;
}

// Oracle: one upright item per word, built from the ink truth (confident words),
// the rest from the baseline span; folded spaces, hasEOL on each line's last word.
function oracleTc(W, T) {
  const items = W.tc.items;
  const base = wordSpans(items, W.tokens, 0.805, W.tc.styles);
  const lineOf = new Map();
  for (const ln of T.lines) for (const k of ln.tokens) lineOf.set(k, ln);
  const font = Object.keys(W.tc.styles)[0];
  const styles = Object.fromEntries(Object.entries(W.tc.styles).map(([k, v]) => [k, { ...v, ascent: 0.8, descent: -0.2 }]));
  const out = [];
  W.tokens.forEach((tk, k) => {
    const tw = T.words[k], ln = lineOf.get(k), src = items[tk.parts[0][0]];
    let x0, x1, b, h;
    if (tw.conf && ln && ln.top != null) { h = ln.top - ln.bottom; b = ln.bottom + 0.2 * h; x0 = tw.x0; x1 = tw.x1; }
    else if (!base[k].rot) { h = base[k].y1 - base[k].y0; b = base[k].y0 + 0.195 * h; x0 = base[k].x0; x1 = base[k].x1; }
    else { out.push({ ...src, str: tk.t + " " }); return; }
    out.push({ str: tk.t, dir: "ltr", width: x1 - x0, height: h, transform: [h, 0, 0, h, x0, b], fontName: src.fontName || font, hasEOL: false, _line: ln?.id });
  });
  for (let i = 0; i < out.length; i++) { // fold spaces / EOL by truth line
    const it = out[i], nx = out[i + 1];
    if (it._line != null && nx && nx._line === it._line && nx.transform[4] > it.transform[4]) {
      it.str += " "; it.width = Math.max(nx.transform[4] - it.transform[4], it.width);
    } else it.hasEOL = true;
    delete it._line;
  }
  for (const it of out) delete it._line;
  return { items: out, styles, lang: W.tc.lang, repaired: true };
}

function invariants(out, W, inTokens, outTokens) {
  const v = { order: 0, shape: 0, nan: 0, page: 0, tiling: 0, details: {} };
  const bump = (k, d) => { v[k]++; if (d) v.details[d] = (v.details[d] || 0) + 1; };
  if (!out || out.repaired !== true) bump("shape", "repaired!=true");
  const [vx0, vy0, vx1, vy1] = W.view;
  const pw = vx1 - vx0, ph = vy1 - vy0;
  const items = out?.items || [];
  const styles = out?.styles || {};
  items.forEach((it, i) => {
    const nums = [it.width, it.height, ...(it.transform || [])];
    if (nums.some((x) => typeof x !== "number" || !Number.isFinite(x))) { bump("nan"); return; }
    if (typeof it.str !== "string") { bump("shape", "str"); return; }
    if (!it.str.trim()) bump("shape", it.str === "" ? "empty item" : "space item");
    if (typeof it.dir !== "string") bump("shape", "dir");
    if (!it.transform || it.transform.length !== 6) { bump("shape", "transform"); return; }
    const st = styles[it.fontName];
    if (!st) bump("shape", "fontName not in styles");
    else if (Math.abs(st.ascent - 0.8) > 1e-6 || Math.abs(st.descent + 0.2) > 1e-6) bump("shape", "style asc/desc");
    if (typeof it.hasEOL !== "boolean") bump("shape", "hasEOL");
    const t = it.transform;
    if (isRotated(t)) return; // rotated items pass through
    // A one-character item is [2h,0,0,h,x,b]: pdf.js's TextLayer stretches a
    // span only when its string is longer than one character or its matrix
    // is anisotropic (scanText.js), so a lone letter gets a wide matrix.
    const a = [...it.str].length === 1 ? 2 * t[3] : t[3];
    if (t[1] !== 0 || t[2] !== 0 || !(t[3] > 0) || Math.abs(t[0] - a) > 1e-6 * Math.max(1, t[3])) bump("shape", "not [h,0,0,h,x,b]");
    if (!it.hasEOL && !/\s$/.test(it.str)) bump("shape", "no trailing space");
    if (it.width > pw + 1e-6 || t[4] < vx0 - 1 || t[4] + it.width > vx1 + 1 || t[5] < vy0 - ph * 0.05 || t[5] > vy1 + ph * 0.05 || Math.abs(t[3]) > ph) bump("page");
    const nx = items[i + 1];
    if (!it.hasEOL && nx && nx.transform && !isRotated(nx.transform) && Math.abs(t[4] + it.width - nx.transform[4]) > 0.02 * Math.abs(t[3])) bump("tiling", "x+width != next x");
  });
  // order: map each output word to the next unused input word with the same string
  const queues = new Map();
  inTokens.forEach((tk, i) => { if (!queues.has(tk.t)) queues.set(tk.t, []); queues.get(tk.t).push(i); });
  let last = -1;
  for (const tk of outTokens) {
    const q = queues.get(tk.t);
    if (!q || !q.length) continue;
    const i = q.shift();
    if (i < last) v.order++;
    last = i;
  }
  return v;
}

// ---------- per page
function scorePage(W, T) {
  let out, ms = 0, asc;
  if (mode === "baseline") { out = W.tc; asc = 0.805; }
  else if (mode === "oracle") { out = oracleTc(W, T); asc = 0.8; }
  else {
    const tc = structuredClone(W.tc);
    const t0 = performance.now();
    out = repair(tc, { view: W.view });
    ms = performance.now() - t0;
    asc = 0.8;
  }
  const outTok = tokenize(out.items).tokens;
  const spans = wordSpans(out.items, outTok, asc, out.styles || {});
  const pairs = lcsAlign(W.tokens.map((t) => t.t), outTok.map((t) => t.t));
  const match = new Map(pairs); // truth k -> out j
  const textOk = pairs.length === W.tokens.length && pairs.length === outTok.length;
  const tokDiff = W.tokens.length + outTok.length - 2 * pairs.length;

  const lineById = new Map(T.lines.map((l) => [l.id, l]));
  const P = { words: 0, matched: 0, v: { n: 0, top: 0, bot: 0 }, h: { n: 0, own: 0, hit: 0, stolen: 0, absL: 0, L: 0, R: 0, own90: 0 }, lines: { n: 0, ok: 0 }, cover: { n: 0, sum: 0 } };
  // spans that contain a given y: precompute list of [x0,x1,y0,y1]
  const all = spans.map((s, j) => ({ ...s, j })).filter((s) => !s.rot);
  const lineSpans = new Map(); // line id -> list of span (for lineOK)
  for (const tw of T.words) {
    if (!tw.conf) continue;
    const ln = lineById.get(tw.line);
    if (!ln || ln.top == null || !ln.vconf) continue;
    P.words++;
    const j = match.get(tw.k);
    if (j == null || spans[j].rot) continue;
    P.matched++;
    const s = spans[j];
    const H = ln.top - ln.bottom, mid = (ln.top + ln.bottom) / 2;
    P.v.n++; P.v.top += Math.abs(s.y1 - ln.top) / H; P.v.bot += Math.abs(s.y0 - ln.bottom) / H;
    if (!lineSpans.has(ln.id)) lineSpans.set(ln.id, []);
    lineSpans.get(ln.id).push(s);
    const iw = tw.x1 - tw.x0;
    const own = ov(s.x0, s.x1, tw.x0, tw.x1) / iw;
    const atMid = s.y0 <= mid && mid <= s.y1;
    const others = all.filter((o) => o.j !== j && o.y0 <= mid && mid <= o.y1 && o.x1 > tw.x0 && o.x0 < tw.x1).map((o) => [o.x0, o.x1]);
    const stolen = unionLen(others, tw.x0, tw.x1) / iw;
    P.h.n++; P.h.own += own; P.h.hit += atMid ? own : 0; P.h.stolen += stolen;
    P.h.absL += Math.abs(s.x0 - tw.x0) / H; P.h.L += (s.x0 - tw.x0) / H; P.h.R += (s.x1 - tw.x1) / H;
    if (own >= 0.9) P.h.own90++;
  }
  for (const ln of T.lines) {
    if (!ln.vconf || ln.top == null) continue;
    const ss = lineSpans.get(ln.id);
    if (ss && ss.length) {
      const H = ln.top - ln.bottom;
      const s0 = median(ss.map((s) => s.y0)), s1 = median(ss.map((s) => s.y1));
      const o = ov(s0, s1, ln.bottom, ln.top);
      P.lines.n++;
      if (o >= 0.9 * H && (s1 - s0) - o <= 0.25 * H) P.lines.ok++;
    }
    if (ln.ink && ln.ink.length && ln.confWords) {
      const mid = (ln.top + ln.bottom) / 2;
      const lo = ln.ink[0][0], hi = ln.ink[ln.ink.length - 1][1];
      const cov = all.filter((o) => o.y0 <= mid && mid <= o.y1 && o.x1 > lo && o.x0 < hi).map((o) => [o.x0, o.x1]);
      const inkLen = ln.ink.reduce((a, [x0, x1]) => a + x1 - x0, 0);
      if (inkLen > 0) {
        let covered = 0;
        for (const [x0, x1] of ln.ink) covered += unionLen(cov, x0, x1);
        P.cover.n++; P.cover.sum += covered / inkLen;
      }
    }
  }
  // segmentation
  const vpT = W.vp.transform;
  let blocks = [];
  try { blocks = segmentPage(textRuns(out.items, vpT, { styles: out.styles })); } catch (e) { blocks = []; P.segError = String(e); }
  const seg = { blocks: blocks.length, trans: blocks.filter((b) => b.translate).length, lines: blocks.reduce((a, b) => a + (b.nLines || 0), 0) };
  const key = `${W.file}-p${W.page}`;
  if (show.has(key)) seg.first = blocks.slice(0, 12).map((b) => `[${b.translate ? "T" : "-"}] ${b.nLines}L ${(b.text || "").replace(/\s+/g, " ").slice(0, 110)}`);
  const inv = invariants(out, W, W.tokens, outTok);
  const sel = selection(out.items).s;
  return { key, file: W.file, page: W.page, kind: W.kind, ms, P, seg, inv, text: { ok: textOk, tokDiff, inWords: W.tokens.length, outWords: outTok.length, newlines: (sel.match(/\n/g) || []).length, truthLines: T.lines.length } };
}

// ---------- run
const t0 = performance.now();
const results = [];
for (const [fid, spec] of Object.entries(pages.files)) {
  if (onlyFile && fid !== onlyFile) continue;
  for (const pn of Object.keys(spec.pages).map(Number).sort((a, b) => a - b)) {
    if (onlyPage && pn !== onlyPage) continue;
    const W = JSON.parse(readFileSync(`${STUDY}/words/${fid}-p${pn}.json`, "utf8"));
    const T = JSON.parse(readFileSync(`${STUDY}/truth/${fid}-p${pn}.json`, "utf8"));
    results.push(scorePage(W, T));
  }
}

function aggregate(rs) {
  const A = { pages: rs.length, words: 0, matched: 0, v: { n: 0, top: 0, bot: 0 }, h: { n: 0, own: 0, hit: 0, stolen: 0, absL: 0, L: 0, R: 0, own90: 0 }, lines: { n: 0, ok: 0 }, cover: { n: 0, sum: 0 },
    textOk: 0, tokDiff: 0, blocks: 0, trans: 0, segLines: 0, inv: { order: 0, shape: 0, nan: 0, page: 0, tiling: 0 }, ms: 0 };
  for (const r of rs) {
    const P = r.P;
    A.words += P.words; A.matched += P.matched;
    for (const k of ["n", "top", "bot"]) A.v[k] += P.v[k];
    for (const k of Object.keys(A.h)) A.h[k] += P.h[k];
    A.lines.n += P.lines.n; A.lines.ok += P.lines.ok; A.cover.n += P.cover.n; A.cover.sum += P.cover.sum;
    A.textOk += r.text.ok ? 1 : 0; A.tokDiff += r.text.tokDiff;
    A.blocks += r.seg.blocks; A.trans += r.seg.trans; A.segLines += r.seg.lines;
    for (const k of Object.keys(A.inv)) A.inv[k] += r.inv[k];
    A.ms += r.ms;
  }
  const d = (a, b) => (b ? a / b : NaN);
  return {
    pages: A.pages, words: A.words, matched: d(A.matched, A.words),
    vTop: d(A.v.top, A.v.n), vBot: d(A.v.bot, A.v.n), lineOK: d(A.lines.ok, A.lines.n),
    own: d(A.h.own, A.h.n), own90: d(A.h.own90, A.h.n), hit: d(A.h.hit, A.h.n), stolen: d(A.h.stolen, A.h.n),
    absL: d(A.h.absL, A.h.n), L: d(A.h.L, A.h.n), R: d(A.h.R, A.h.n), cover: d(A.cover.sum, A.cover.n),
    textOk: A.textOk, tokDiff: A.tokDiff, blocksPerPage: d(A.blocks, A.pages), transPerPage: d(A.trans, A.pages), lpb: d(A.segLines, A.blocks),
    inv: A.inv, msPerPage: d(A.ms, A.pages),
  };
}
const byFile = {};
for (const r of results) (byFile[r.file] ||= []).push(r);
const files = Object.fromEntries(Object.entries(byFile).map(([f, rs]) => [f, aggregate(rs)]));
const ALL = aggregate(results);
const POOR = aggregate(results.filter((r) => ["lounis", "hansch", "holstein", "kitagawa", "feshbach", "naca"].includes(r.file)));
const GOOD = aggregate(results.filter((r) => !["lounis", "hansch", "holstein", "kitagawa", "feshbach", "naca"].includes(r.file)));

// ---------- print
const f2 = (x) => (Number.isFinite(x) ? x.toFixed(2) : "  - ");
const f1 = (x) => (Number.isFinite(x) ? x.toFixed(1) : " - ");
const pc = (x) => (Number.isFinite(x) ? `${Math.round(x * 100)}%` : " - ");
const head = "file       pg words match  vTop vBot lineOK |  own own90  hit stolen  |L|     L     R cover | text tokDiff | blk  trn lpb | order shape tiling page nan";
const row = (name, a) => `${name.padEnd(10)} ${String(a.pages).padStart(2)} ${String(a.words).padStart(5)} ${pc(a.matched).padStart(5)} ${f2(a.vTop).padStart(5)} ${f2(a.vBot).padStart(4)} ${pc(a.lineOK).padStart(6)} | ${pc(a.own).padStart(4)} ${pc(a.own90).padStart(5)} ${pc(a.hit).padStart(4)} ${pc(a.stolen).padStart(6)} ${f2(a.absL).padStart(5)} ${f2(a.L).padStart(5)} ${f2(a.R).padStart(5)} ${pc(a.cover).padStart(5)} | ${String(a.textOk).padStart(2)}/${a.pages} ${String(a.tokDiff).padStart(6)} | ${f1(a.blocksPerPage).padStart(4)} ${f1(a.transPerPage).padStart(4)} ${f1(a.lpb).padStart(3)} | ${String(a.inv.order).padStart(5)} ${String(a.inv.shape).padStart(5)} ${String(a.inv.tiling).padStart(6)} ${String(a.inv.page).padStart(4)} ${String(a.inv.nan).padStart(3)}`;
console.log(`mode: ${mode}   pages: ${results.length}   time: ${((performance.now() - t0) / 1000).toFixed(1)} s${repair ? `   repair: ${ALL.msPerPage.toFixed(1)} ms/page` : ""}`);
console.log(head);
for (const [f, a] of Object.entries(files)) console.log(row(f, a));
console.log(row("POOR", POOR));
console.log(row("GOOD", GOOD));
console.log(row("ALL", ALL));
if (!quiet) {
  for (const r of results) if (r.seg.first) {
    console.log(`\n${r.key}: ${r.seg.blocks} blocks, ${r.seg.trans} translatable`);
    for (const s of r.seg.first) console.log("  " + s);
  }
  const det = {};
  for (const r of results) for (const [k, n] of Object.entries(r.inv.details)) det[k] = (det[k] || 0) + n;
  if (Object.keys(det).length) console.log("\ninvariant details:", JSON.stringify(det));
}
if (jsonOut) writeFileSync(jsonOut, JSON.stringify({ mode, metrics: METRICS, all: ALL, poor: POOR, good: GOOD, files, pages: results }, null, 1));
