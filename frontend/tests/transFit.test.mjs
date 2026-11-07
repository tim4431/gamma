// How large the overlay sets a block's text (src/pdf/transFit.js): the ratio,
// its store, and the page's cap on its body paragraphs.
import test from "node:test";
import assert from "node:assert/strict";
import { MIN_FIT, bestRatio, bodyBlocks, fitOf, keepCap, keepFit, layerCap, needsFit, pageCap, settledCap, streamRatio } from "../src/pdf/transFit.js";

const PROSE = "the atoms in the lattice move once the drive is switched on and off again";

test("a landed paragraph finds its largest ratio by halving, closer than one percent", () => {
  const tried = [];
  const ratio = bestRatio((r) => { tried.push(r); return r <= 0.8; });
  assert.ok(ratio <= 0.8 && ratio > 0.79, `ratio ${ratio}`);
  assert.ok(tried.length <= 9, `${tried.length} reflows`);
  assert.ok(tried.includes(ratio), "the answer is a ratio that fitted");
});

test("a block at full size asks once, and one that fits at no ratio stops at the floor", () => {
  let asked = 0;
  assert.equal(bestRatio(() => { asked += 1; return true; }), 1);
  assert.equal(asked, 1);
  assert.equal(bestRatio(() => false), MIN_FIT);
});

test("a streaming paragraph keeps its ratio while it fits, and takes one step down when not", () => {
  assert.equal(streamRatio(() => true, 0.8), 0.8);
  let asked = 0;
  assert.ok(Math.abs(streamRatio(() => { asked += 1; return false; }, 0.8) - 0.744) < 1e-9);
  assert.equal(asked, 1, "one measure per step");
  assert.equal(streamRatio(() => false, MIN_FIT), MIN_FIT, "not past the floor");
});

test("a settled cap is kept with the page's blocks, for its language", () => {
  const paras = [{}], others = [{}];
  assert.equal(settledCap(paras, "de|m"), null, "none settled yet");
  keepCap(paras, "de|m", 0.88);
  assert.equal(settledCap(paras, "de|m"), 0.88);
  assert.equal(settledCap(paras, "zh-CN|m"), null, "the same blocks under another language");
  assert.equal(settledCap(others, "de|m"), null, "another page's");
  keepCap(paras, "de|m", null);
  assert.equal(settledCap(paras, "de|m"), null, "a settle with no body paragraphs has no cap");
});

test("the ratios live with the page's blocks, and a remount measures nothing", () => {
  const paras = [{}, {}], others = [{}, {}];
  assert.equal(fitOf(paras, 0), null);
  keepFit(paras, 0, { text: "译文", ratio: 0.8, bare: 0 });
  assert.equal(fitOf(paras, 0).ratio, 0.8);
  assert.equal(fitOf(others, 0), null, "another page's array holds its own");
  assert.equal(needsFit(fitOf(paras, 0), "译文", 0, false), false);
  assert.equal(needsFit(fitOf(paras, 0), "另一个译文", 0, false), true, "another text");
  assert.equal(needsFit(null, "译文", 0, false), true, "nothing kept");
});

test("a base paint measures a block again only if a formula in it became a picture", () => {
  const kept = { text: "译文", ratio: 0.8, bare: 2 };
  assert.equal(needsFit(kept, "译文", 2, false), false, "they are text still");
  assert.equal(needsFit(kept, "译文", 1, false), true, "one became a picture");
  assert.equal(needsFit({ ...kept, bare: 0 }, "译文", 1, false), false, "a picture let go keeps the ratio");
});

test("a text that lands as its last partial searches, which the stream did not", () => {
  const rough = { text: "译文", ratio: 0.93, bare: 0, rough: true };
  assert.equal(needsFit(rough, "译文", 0, true), false);
  assert.equal(needsFit(rough, "译文", 0, false), true);
});

const para = (over = {}) => ({ translate: true, nLines: 4, size: 10, text: PROSE, ...over });

test("the body is running text of two lines or more at the page's size", () => {
  const paras = [
    para({ size: 16, nLines: 2, text: "1 Introduction" }),  // a heading
    para(),
    para({ size: 10.4 }),                                   // within 6 percent
    para({ size: 9.3 }),                                    // past it, a smaller block
    para({ nLines: 1 }),                                    // a one-liner
    para({ nLines: 2, text: "Energy (eV) 1.2 3.4" }),       // a table cell
    para({ translate: false }),
    para({ size: 10.2, nLines: 2, text: "A. Results" }),    // a heading at body size
  ];
  assert.deepEqual([...bodyBlocks(paras)], [1, 2]);
});

test("a reference list left original neither sets the page's size nor counts as body", () => {
  const paras = [para({ size: 9 }), para({ size: 9 }), para({ size: 9 }), para({ size: 10 })];
  assert.deepEqual([...bodyBlocks(paras, new Set([0, 1, 2]))], [3]);
});

test("the cap is the lower quartile of the body's ratios, of text that has landed", () => {
  const paras = [{}, {}, {}, {}, {}, {}];
  const texts = ["a", "b", "c", "d", "e", undefined];
  [0.9, 0.7, 1, 0.8, 0.6].forEach((ratio, i) => keepFit(paras, i, { text: texts[i], ratio, bare: 0 }));
  keepFit(paras, 5, { text: "partial", ratio: 0.2, bare: 0 });
  assert.equal(pageCap(paras, new Set([0, 1, 2, 3, 4, 5]), texts), 0.7, "the second of five, the partial out");
  assert.equal(pageCap(paras, new Set([2]), texts), 1, "one paragraph is its own");
  assert.equal(pageCap(paras, new Set(), texts), null, "no body, no cap");
  assert.equal(pageCap(paras, new Set([0]), ["another text"]), null, "a ratio of an older text does not count");
});

test("a paragraph asked for again leaves the page's settled cap as it was until it lands", () => {
  const paras = [{}, {}, {}, {}, {}, {}];
  const texts = ["a", "b", "c", "d", "e", "f"];
  const body = new Set([0, 1, 2, 3, 4, 5]);
  [0.9, 0.7, 1, 0.8, 0.6, 0.95].forEach((ratio, i) => keepFit(paras, i, { text: texts[i], ratio, bare: 0 }));
  const entry = { key: "zh-CN|m", paras, texts };
  const settled = layerCap(paras, entry, body, false);
  assert.deepEqual(settled, { cap: 0.7, settles: true });
  keepCap(paras, entry.key, settled.cap);
  // Block 1 streams its new text: its landed ratio, the one the cap took, is gone.
  keepFit(paras, 1, { text: "新", ratio: 0.95, bare: 0, rough: true });
  assert.equal(pageCap(paras, body, texts), 0.8, "the cap of what is left would grow");
  for (const working of [{ busy: new Set([1]) }, { busy: new Set(), partial: { 1: "新" } }]) {
    assert.deepEqual(layerCap(paras, { ...entry, ...working }, body, false), { cap: 0.7, settles: false });
  }
  assert.deepEqual(layerCap(paras, entry, body, true), { cap: 0.7, settles: false }, "a job owing the page blocks");
  // It lands: one settle.
  keepFit(paras, 1, { text: "新译", ratio: 0.75, bare: 0 });
  const landed = texts.map((x, i) => (i === 1 ? "新译" : x));
  assert.deepEqual(layerCap(paras, { ...entry, texts: landed, busy: null, partial: null }, body, false), { cap: 0.75, settles: true });
});
