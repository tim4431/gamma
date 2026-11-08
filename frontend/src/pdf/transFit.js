// How large the translated view's overlay (pdf/TransOverlay.jsx) sets a block's
// text: the ratio of the block's own size that fits its box, kept so that it is
// measured once, and the page's cap on its body paragraphs. Pure but for that
// store, which holds no DOM, so the tests run it in Node. The overlay is laid
// out at one reference scale (transBlock.js REF_SCALE), so none of this depends
// on the zoom. docs/dev/translation.md "How a translation is set" describes it.
import { bodySize, isHeading, isProse } from "./pageFlow.js";

// The smallest a block is set, of its size: gamma/pdf_translated.py MIN_SCALE,
// so that the screen and the export give up on the same blocks. Past it the
// text runs out of its box, in sight: the overlay cuts nothing.
export const MIN_FIT = 0.05;
const CLOSE = 0.01;      // how near a landed paragraph's search comes, of the size
const STREAM_STEP = 0.93; // a streaming paragraph's step down
const NEAR_BODY = 0.06;  // how far a body paragraph's size is from the page's

// A landed paragraph's ratio: the largest that `fits` says yes to, found by
// halving. A block at full size asks once. A wrapped text is not quite
// monotonic, so the answer is always a ratio that was tried, or the floor.
export function bestRatio(fits) {
  if (fits(1)) return 1;
  let lo = MIN_FIT, hi = 1;
  while (hi - lo > CLOSE) {
    const mid = (lo + hi) / 2;
    if (fits(mid)) lo = mid;
    else hi = mid;
  }
  return lo;
}

// A streaming paragraph's next ratio, from the one it has: its text only grows,
// so one step down at most, and the search waits for the final text.
export const streamRatio = (fits, ratio) => (fits(ratio) ? ratio : Math.max(MIN_FIT, ratio * STREAM_STEP));

// The ratios measured, by the page's `paras` array, so that they live as long
// as the page's blocks: {block index → {text, ratio, bare, rough}}, `bare`
// counting the formulas in the block that were text, not pictures, when it was
// measured, `rough` saying the ratio is a streaming one's.
const FITS = new WeakMap();

export const fitOf = (paras, i) => FITS.get(paras)?.get(i) || null;

export function keepFit(paras, i, fit) {
  if (!FITS.has(paras)) FITS.set(paras, new Map());
  FITS.get(paras).set(i, fit);
}

// The page's paints (transPaint.js pagePaints), by the same array: read once, at
// the first paint the page's overlay sees. null until then. The text does not
// come into it: a block's ground and ink are the page's.
const PAINTS = new WeakMap();

export const paintsOf = (paras) => PAINTS.get(paras) || null;
export const keepPaints = (paras, paints) => { PAINTS.set(paras, paints); };

// The page's cap as it last settled, by the same array and the translate key it
// is for: the blocks are one array under every key, and another language's cap
// says nothing of this one's. A job's remount writes this, not a cap of what has
// landed so far.
const CAPS = new WeakMap();

export function settledCap(paras, key) {
  const kept = CAPS.get(paras);
  return kept?.key === key ? kept.cap : null;
}
export const keepCap = (paras, key, cap) => { CAPS.set(paras, { key, cap }); };

// Whether a block must be measured for `text`, now holding `bare` formulas as
// text: nothing is kept for that text, a formula has become a picture, or the
// text has landed (not `streaming`) on a streaming paragraph's rough ratio.
export function needsFit(kept, text, bare, streaming) {
  return kept?.text !== text || bare < kept.bare || (!streaming && !!kept.rough);
}

// The page's body paragraphs, by index: running text of two lines or more at
// the page's size. Headings, one-liners, table cells and a reference list left
// original (`left`) are outside the cap.
export function bodyBlocks(paras, left) {
  const body = bodySize(paras, left);
  const near = (p) => Math.abs(p.size - body) <= body * NEAR_BODY;
  const out = new Set();
  paras.forEach((p, i) => {
    if (p.translate && p.nLines >= 2 && near(p) && !left?.has(i) && !isHeading(p, body) && isProse(p.text)) out.add(i);
  });
  return out;
}

// The page's cap: the lower quartile of the ratios of its body paragraphs
// (`body`) with text landed (`texts`, a block's final results), null with none.
// Neighbours stop differing in size, one paragraph that barely fits does not set
// the page, and it keeps its own smaller ratio (min() in app.css).
export function pageCap(paras, body, texts) {
  const ratios = [];
  for (const i of body) {
    const kept = fitOf(paras, i);
    if (kept && texts[i] && kept.text === texts[i]) ratios.push(kept.ratio);
  }
  if (!ratios.length) return null;
  ratios.sort((a, b) => a - b);
  return ratios[Math.floor((ratios.length - 1) / 4)];
}

// The cap a page's layer writes, for its `entry` (its `key`, `texts`, `busy` and
// `partial`) and its `body`: {cap, settles}. While a job works on the page
// (`working`: it owes the page blocks) or a paragraph of it is in flight or
// streaming, which is all a job on one paragraph does to a page that is done,
// the cap that last settled under the key, so that text that has landed does
// not move: the paragraph asked for again has dropped its landed ratio for its
// streaming one, and the cap of what is left would be another. Else the cap
// of what has landed, which settles (keepCap).
export function layerCap(paras, entry, body, working) {
  const busy = working || !!entry.busy?.size || Object.keys(entry.partial || {}).length > 0;
  return busy ? { cap: settledCap(paras, entry.key), settles: false } : { cap: pageCap(paras, body, entry.texts), settles: true };
}
