// node --test tests/  (from frontend/) — the pure stroke module.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  DEFAULT_TOOLS, HIGHLIGHTER_OPACITY, MAX_TOOLS, VERSION, appendStroke, boundsOf, decodeStroke, encodeStroke, eraseAt, eraserKeep, hitStrokes,
  inkBounds, newInk, normalizeTools, pdfPositionOf, removeStrokes, strokePath, strokeWidth, strokesInLasso, toolStyle,
  translateStrokes, transformStrokes, nearestInkStroke, restyleStrokes, duplicateStrokes, MAX_STROKES,
  mergeInk, serializeInk, inkProps, newCanvasInk, sameStroke,
  inkAtTime, inkTimeline, REPLAY_MAX, REPLAY_PAUSE,
  DEFAULT_SIZE, STROKE_DEFAULTS, byBand, cloneStrokes, encodePoints, inkBytes, inkFeatures, strokeBand, strokeBounds, strokeBytes,
  upgradeV1, withDefaults, appendStrokes, flipStrokes,
} from "../src/ink/ink.js";

const samples = (n = 5, x0 = 100, y0 = 200) =>
  Array.from({ length: n }, (_, i) => ({ x: x0 + 10 * i, y: y0 + 3 * i, p: 0.2 + 0.15 * i, t: 16 * i }));
// A pressure pen's samples as finishStroke bakes them: w = 1 + (p − 0.5).
const baked = (n = 5, x0 = 100, y0 = 200) => samples(n, x0, y0).map((s) => ({ ...s, w: Math.round((s.p + 0.5) * 1000) / 1000 }));
const pen = (over = {}) => encodeStroke({ id: "pen", samples: baked(), ch: "xyptw", t0: 100, ...over });

// --- the version 2 codec -----------------------------------------------------

test("a new file is version 2; w and o are multipliers, written × 1000 as deltas after the first sample", () => {
  assert.equal(VERSION, 2);
  assert.equal(newInk(1, 612, 792).version, 2);
  assert.equal(newCanvasInk(100, 100).version, 2);
  const s = encodeStroke({ id: "a", ch: "xypw", samples: [{ x: 1, y: 2, p: 0.2, w: 0.7 }, { x: 2, y: 2, p: 0.5, w: 1 }, { x: 3, y: 2, p: 1, w: 1.5 }] });
  assert.deepEqual(s.pts, [100, 200, 200, 700, 100, 0, 500, 300, 100, 0, 1000, 500]);
  assert.deepEqual(decodeStroke(s).map((q) => [q.x, q.p, q.w]), [[1, 0.2, 0.7], [2, 0.5, 1], [3, 1, 1.5]]);
  assert.equal("t" in decodeStroke(s)[0], false, "only the channels present are decoded");
  const o = encodeStroke({ id: "o", ch: "xyo", samples: [{ x: 0, y: 0, o: 1 }, { x: 1, y: 0, o: 0.4 }] });
  assert.deepEqual(o.pts, [0, 0, 1000, 100, 0, -600]);
  assert.deepEqual(decodeStroke(o).map((q) => q.o), [1, 0.4]);
  assert.deepEqual(encodePoints([{ x: 0, y: 0, w: 3.5 }, { x: 0, y: 0, w: -1 }, { x: 0, y: 0, w: 5 }], "xyw"), [0, 0, 3500, 0, 0, -3500, 0, 0, 4000],
    "w is capped to 0..4 (the pencil's slant reaches 3.5)");
  assert.deepEqual(decodeStroke({ ch: "xyw", pts: [0, 0, 4500] })[0].w, 4);
});

test("a letter the codec does not know rides along by stride and is written back in place", () => {
  const foreign = { id: "f", ch: "xyqp", pts: [100, 200, 7, 500, 10, 0, 8, 600] };
  const q = decodeStroke(foreign);
  assert.deepEqual(q, [{ x: 1, y: 2, p: 0.5, rest: [7] }, { x: 1.1, y: 2, p: 0.6, rest: [8] }]);
  assert.deepEqual(encodePoints(q, "xyqp"), foreign.pts);
  assert.equal("rest" in decodeStroke(pen())[0], false, "no rest array without unknown letters");
  // a cut keeps the stride: the pieces carry the letter and its values
  const ink = { ...newInk(1, 612, 792), strokes: [{ ...foreign, ch: "xyqp", pts: encodePoints(
    Array.from({ length: 9 }, (_, i) => ({ x: 100 + 10 * i, y: 200, p: 0.5, rest: [i] })), "xyqp") }] };
  const cut = eraseAt(ink, 140, 200, 3);
  assert.equal(cut.ink.strokes.length, 2);
  assert.deepEqual(cut.ink.strokes.map((s) => decodeStroke(s).map((x) => x.rest[0])), [[0, 1, 2, 3], [5, 6, 7, 8]]);
  assert.ok(cut.ink.strokes.every((s) => s.ch === "xyqp"));
});

test("the drawn width is size × w with w, else size: the whole rule", () => {
  const s = pen({ size: 2 });
  const [first, , , , last] = decodeStroke(s);
  assert.ok(Math.abs(strokeWidth(s, first) - 1.4) < 1e-9 && Math.abs(strokeWidth(s, last) - 2.6) < 1e-9);
  assert.equal(strokeWidth(encodeStroke({ size: 3, samples: samples() }), { p: 1 }), 3, "no w channel: the size");
  const hl = encodeStroke({ tool: "highlighter", size: 8, samples: samples() });
  assert.equal(strokeWidth(hl, decodeStroke(hl)[0]), 8);
  const b = inkBounds({ strokes: [s] });
  assert.ok(Math.abs(b[0] - (100 - 0.7)) < 1e-9 && Math.abs(b[2] - (140 + 1.3)) < 1e-9, "bounds use the baked width");
  // a file may omit every key with a default: the width, bounds and outline still work, and withDefaults fills them
  const bare = { id: "bare", pts: [30000, 40000, 500, 500] };
  assert.equal(strokeWidth(bare, decodeStroke(bare)[0]), DEFAULT_SIZE);
  assert.deepEqual(strokeBounds(bare), [300 - 0.8, 400 - 0.8, 305 + 0.8, 405 + 0.8]);
  assert.ok(strokePath(bare).d.startsWith("M"));
  const filled = withDefaults({ ...newInk(1, 612, 792), strokes: [bare, s] });
  assert.deepEqual(filled.strokes[0], { ...bare, ...STROKE_DEFAULTS });
  assert.equal(filled.strokes[1], s);
  const complete = { ...newInk(1, 612, 792), strokes: [s] };
  assert.equal(withDefaults(complete), complete, "nothing missing: the same object");
});

// tests/shared/inkv2.json: the version 1 upgrade, byte for byte, the same
// bytes gamma/ink.py upgrade_v1 + dumps write.
const V2 = JSON.parse(readFileSync(new URL("../../tests/shared/inkv2.json", import.meta.url), "utf8"));
for (const c of V2.cases) {
  test(`upgrade v1: ${c.note}`, () => {
    assert.equal(serializeInk(upgradeV1(JSON.parse(c.v1))), c.v2);
    assert.equal(serializeInk(upgradeV1(JSON.parse(c.v2))), c.v2, "idempotent on version 2");
  });
}
test("upgradeV1 leaves anything but a version 1 object as the same object", () => {
  const ink = newInk(1, 612, 792);
  assert.equal(upgradeV1(ink), ink);
  const newer = { ...ink, version: 3, strokes: [encodeStroke({ id: "a", ch: "xyp", samples: samples(2) })] };
  assert.equal(upgradeV1(newer), newer, "a newer file is never rewritten to 2");
  const odd = { ...ink, version: "1" };
  assert.equal(upgradeV1(odd), odd, "a malformed version is left for the server to refuse");
  const other = { format: "something", version: 1 };
  assert.equal(upgradeV1(other), other);
  assert.equal(upgradeV1(null), null);
});

test("bands: highlighters draw under ink; a tap prefers the ink above a highlighter", () => {
  const hl = encodeStroke({ id: "hl", tool: "highlighter", size: 14, samples: samples(2) });
  const under = encodeStroke({ id: "under", band: 0, samples: samples(2) });
  const top = encodeStroke({ id: "top", tool: "highlighter", band: 1, size: 14, samples: samples(2) });
  const ink = { ...newInk(1, 612, 792), strokes: [pen(), hl, under, top] };
  assert.deepEqual([strokeBand(pen()), strokeBand(hl), strokeBand(under), strokeBand(top)], [1, 0, 0, 1]);
  assert.deepEqual(byBand(ink.strokes).map((s) => s.id), ["hl", "under", "pen", "top"]);
  const plain = [pen(), encodeStroke({ id: "b", samples: samples() })];
  assert.equal(byBand(plain), plain, "nothing to reorder: the same array");
  // a thin pen over a wide highlighter: the pen is what a tap near both means
  const wide = encodeStroke({ id: "wide", tool: "highlighter", size: 20, samples: [{ x: 10, y: 10 }, { x: 100, y: 10 }] });
  const thin = encodeStroke({ id: "thin", size: 0.6, samples: [{ x: 10, y: 10 }, { x: 100, y: 10 }] });
  const groups = [{ id: "g", ink: { strokes: [thin, wide] } }];
  assert.deepEqual(nearestInkStroke(groups, 50, 13, 5), { id: "g", ids: ["thin"] }, "inside the highlighter, 2.7 pt from the pen's edge");
  assert.deepEqual(nearestInkStroke(groups, 50, 18, 5), { id: "g", ids: ["wide"] }, "out of the pen's reach");
});

test("pieces name their root, copies do not; unknown keys ride through every edit", () => {
  const s = pen({ note: "mine", shape: { kind: "line", params: { x0: 0 } }, fill: { color: "#ff0000" }, nib: 30, dash: "dashed" });
  const ink = { ...newInk(1, 612, 792), strokes: [s] };
  const { ink: cut } = eraseAt(ink, 120, 206, 1);
  assert.equal(cut.strokes.length, 2);
  for (const piece of cut.strokes) {
    assert.equal(piece.from, "pen");
    assert.equal(piece.note, "mine");
    assert.equal(piece.nib, 30);
    assert.equal(piece.dash, "dashed");
    assert.ok(!("shape" in piece) && !("fill" in piece), "a cut shape is just ink");
    assert.equal(piece.ch, "xyptw");
    assert.equal(piece.t0, 100);
  }
  const again = eraseAt(cut, 105, 201.5, 0.5).ink;
  assert.ok(again.strokes.every((p) => p.from === "pen"), "a cut of a piece keeps the root");
  const dup = duplicateStrokes(cut, cut.strokes.map((p) => p.id), 5, 5);
  assert.ok(dup.ink.strokes.slice(2).every((c) => !("from" in c) && c.note === "mine"));
  assert.ok(cloneStrokes(cut.strokes).every((c) => !("from" in c) && c.id !== cut.strokes[0].id));
  assert.equal(restyleStrokes(ink, ["pen"], { color: "#dc2626" }).strokes[0].note, "mine");
  assert.equal(translateStrokes(ink, ["pen"], 3, 3).strokes[0].note, "mine");
  assert.deepEqual(transformStrokes(ink, ["pen"], { cx: 0, cy: 0, scale: 2 }).strokes[0].shape, s.shape);
});

test("inkFeatures lists what this build cannot show, null for a file it knows", () => {
  assert.equal(inkFeatures({ ...newInk(1, 612, 792), strokes: [pen(), encodeStroke({ tool: "highlighter", samples: samples() })] }), null);
  const odd = { ...newInk(1, 612, 792), version: 3, strokes: [
    encodeStroke({ tool: "crayon", samples: samples() }), encodeStroke({ brush: "fountain", samples: samples(), glow: 1 }),
    { id: "q", ch: "xyq", pts: [0, 0, 1] },
  ] };
  assert.deepEqual(inkFeatures(odd), { newer: true, tools: ["crayon"], brushes: ["fountain"], keys: ["glow"], letters: ["q"] });
  assert.equal(inkFeatures(null), null);
});

test("inkBytes is the upload's exact size, summed per stroke and cached by object", () => {
  const ink = { ...newInk(1, 612, 792), strokes: [pen(), encodeStroke({ id: "b", samples: samples(3) })] };
  assert.equal(inkBytes(ink), serializeInk(ink).length);
  // a null-valued key is not uploaded, so it is not counted either
  const nulled = { ...ink, strokes: [{ ...ink.strokes[0], dash: null }, ink.strokes[1]] };
  assert.equal(inkBytes(nulled), serializeInk(nulled).length);
  assert.equal(inkBytes({ ...ink, strokes: [] }), serializeInk({ ...ink, strokes: [] }).length);
  assert.equal(strokeBytes(ink.strokes[0]), strokeBytes(ink.strokes[0]));
  assert.equal(inkBytes(null), 0);
});

test("selection transforms share an origin, preserve non-position channels and undo without mutation", () => {
  const stroke = encodeStroke({ id: "a", size: 2, ch: "xyptaz", t0: 100,
    samples: [{ x: 20, y: 10, p: 0.3, t: 0, a: 40, z: 70 }, { x: 30, y: 10, p: 0.8, t: 25, a: 45, z: 80 }] });
  const other = encodeStroke({ id: "b", samples: samples() });
  const ink = { ...newInk(1, 612, 792), strokes: [stroke, other] };
  const moved = transformStrokes(ink, ["a"], { cx: 10, cy: 10, scale: 2, angle: Math.PI / 2 });
  assert.deepEqual(decodeStroke(moved.strokes[0]), [
    { x: 10, y: 30, p: 0.3, t: 0, a: 40, z: 70 }, { x: 10, y: 50, p: 0.8, t: 25, a: 45, z: 80 },
  ]);
  assert.equal(moved.strokes[0].size, 4);
  assert.equal(moved.strokes[0].id, "a");
  assert.equal(moved.strokes[0].t0, 100);
  assert.equal(moved.strokes[1], other);
  assert.equal(decodeStroke(stroke)[0].x, 20);
  const restored = transformStrokes(moved, ["a"], { cx: 10, cy: 10, scale: 0.5, angle: -Math.PI / 2 });
  assert.deepEqual(restored, ink);
  assert.equal(transformStrokes(ink, ["a"], { cx: 10, cy: 10 }), ink);
  assert.equal(transformStrokes(ink, ["a"], { cx: 10, cy: 10, scale: NaN }), ink);
  assert.equal(transformStrokes(ink, [], { cx: 10, cy: 10, scale: 2 }), ink);
});

test("touch selection finds a thin stroke between samples and prefers topmost ties", () => {
  const ink = { ...newInk(1, 612, 792), strokes: [
    encodeStroke({ id: "lower", size: 0.6, samples: [{ x: 10, y: 10 }, { x: 100, y: 10 }] }),
    encodeStroke({ id: "upper", size: 0.6, samples: [{ x: 10, y: 10 }, { x: 100, y: 10 }] }),
  ] };
  const groups = [{ id: "note", ink }];
  assert.deepEqual(nearestInkStroke(groups, 50, 14, 5), { id: "note", ids: ["upper"] });
  assert.equal(nearestInkStroke(groups, 50, 18, 5), null);
});

test("restyling selected ink preserves pressure and timing; mixed tools use their own widths", () => {
  const a = encodeStroke({ id: "a", samples: samples(), ch: "xypt", t0: 100 });
  const b = encodeStroke({ id: "b", tool: "highlighter", size: 14, opacity: 0.6, samples: samples() });
  const ink = { ...newInk(1, 612, 792), strokes: [a, b] };
  const edited = restyleStrokes(ink, ["a", "b"], { tool: "pen", size: 4 });
  assert.equal(edited.strokes[0].size, 4);
  assert.equal(edited.strokes[1], b);
  assert.equal(edited.strokes[0].pts, a.pts);
  assert.equal(edited.strokes[0].t0, 100);
  assert.deepEqual(decodeStroke(edited.strokes[0]), decodeStroke(a));
  const colored = restyleStrokes(edited, ["a", "b"], { color: "#DC2626" });
  assert(colored.strokes.every((s) => s.color === "#dc2626"));
  assert.equal(colored.strokes[1].opacity, 0.6);
  assert.equal(restyleStrokes(colored, ["a", "b"], { color: "#dc2626" }), colored, "same color is not an undo entry");
});

test("duplicate preserves originals and channels with fresh IDs; full notes do not evict ink", () => {
  const a = encodeStroke({ id: "a", samples: samples(), ch: "xypt", t0: 100 });
  const ink = { ...newInk(1, 612, 792), strokes: [a] };
  const copy = duplicateStrokes(ink, ["a"], 12, -8);
  assert.equal(copy.ink.strokes[0], a);
  assert.equal(copy.ids.length, 1);
  assert.notEqual(copy.ids[0], "a");
  const first = decodeStroke(copy.ink.strokes[1])[0];
  assert.deepEqual(first, { x: 112, y: 192, p: 0.2, t: 0 });
  assert.equal(copy.ink.strokes[1].t0, 100);
  const full = { ...ink, strokes: Array.from({ length: MAX_STROKES }, (_, i) => ({ ...a, id: String(i) })) };
  assert.deepEqual(duplicateStrokes(full, ["0"], 12, 12), { ink: full, ids: [] });
});

test("codec: deltas, pressure scale, exact round trip", () => {
  const s = encodeStroke({ id: "s1", samples: samples(), ch: "xypt", t0: 5 });
  // the backend's expectation for the same samples (tests/test_ink.py)
  assert.deepEqual(s.pts.slice(0, 8), [10000, 20000, 200, 0, 1000, 300, 350, 16]);
  assert.equal(s.t0, 5);
  const back = decodeStroke(s);
  assert.deepEqual(back.map((q) => [q.x, q.y, q.t]), samples().map((q) => [q.x, q.y, q.t]));
  assert.ok(Math.abs(back[1].p - 0.35) < 1e-9);
  assert.equal(decodeStroke(s), back, "decoded samples are cached per stroke object");
});

test("monoline keeps pressure data and no width channel; a pen's outline follows its baked width", () => {
  const input = samples(9).map((s, i) => ({ ...s, p: (i + 1) / 10 }));
  const mono = encodeStroke({ id: "mono", brush: "monoline", samples: input, ch: "xypt", t0: 100 });
  assert.deepEqual(decodeStroke(mono), input);
  for (const q of decodeStroke(mono)) assert.equal(strokeWidth(mono, q), mono.size);
  const hard = encodeStroke({ brush: "monoline", samples: input.map((s) => ({ ...s, p: 1 })) });
  assert.equal(strokePath(mono).d, strokePath(hard).d);
  const softPen = encodeStroke({ ch: "xypw", samples: input.map((s) => ({ ...s, p: 0, w: 0.5 })) });
  const hardPen = encodeStroke({ ch: "xypw", samples: input.map((s) => ({ ...s, p: 1, w: 1.5 })) });
  assert.notEqual(strokePath(softPen).d, strokePath(hardPen).d);
  const flat = encodeStroke({ ch: "xypw", samples: input.map((s) => ({ ...s, w: 1 })) });
  assert.equal(strokePath(flat).d, strokePath(encodeStroke({ samples: input, pen: false })).d, "w of 1 everywhere draws the nominal size");
  assert.equal("brush" in softPen, false, "a plain pen writes no brush");
  const ink = { ...newInk(1, 612, 792), strokes: [mono] };
  const cut = eraseAt(ink, 140, 212, 3);
  assert.equal(cut.ink.strokes.length, 2);
  assert(cut.ink.strokes.every((s) => s.brush === "monoline" && s.ch === "xypt" && s.t0 === 100 && s.from === "mono"));
  assert.equal(duplicateStrokes(ink, [mono.id], 10, 10).ink.strokes[1].brush, "monoline");
  assert.equal(restyleStrokes(ink, [mono.id], { color: "#dc2626", size: 4 }).strokes[0].brush, "monoline");
});

test("monoline presets survive storage and switching back to pen", () => {
  const mono = { ...DEFAULT_TOOLS[0], brush: "monoline" };
  assert.deepEqual(normalizeTools(JSON.parse(JSON.stringify([mono]))), [mono]);
  assert.equal(toolStyle(mono).brush, "monoline");
  const pen = normalizeTools([{ ...mono, brush: "pen" }])[0];
  assert.equal("brush" in toolStyle(pen), false);
  const highlighter = normalizeTools([{ ...DEFAULT_TOOLS[4], brush: "monoline" }])[0];
  assert.equal("brush" in toolStyle(highlighter), false);
});

test("bounds, pdf_position and stroke editing", () => {
  let ink = newInk(3, 612, 792);
  assert.equal(inkBounds(ink), null);
  ink = appendStroke(ink, encodeStroke({ id: "a", samples: samples() }));
  ink = appendStroke(ink, encodeStroke({ id: "b", samples: samples(3, 300, 300) }));
  const b = inkBounds(ink);
  assert.ok(b[0] < 100 && b[1] < 200 && b[2] > 320 && b[3] > 306);
  const pos = pdfPositionOf(ink);
  // the page size once, beside the box: no size or page in the rects
  assert.deepEqual([pos.pageNumber, pos.width, pos.height], [3, 612, 792]);
  assert.deepEqual(Object.keys(pos.boundingRect), ["x1", "y1", "x2", "y2"]);
  assert.deepEqual(pos.rects, [pos.boundingRect]);
  assert.deepEqual(removeStrokes(ink, ["a"]).strokes.map((s) => s.id), ["b"]);
});

test("eraser hit test finds the stroke under the point and nothing else", () => {
  let ink = newInk(1, 612, 792);
  ink = appendStroke(ink, encodeStroke({ id: "a", samples: samples() }));          // (100,200)→(140,212)
  ink = appendStroke(ink, encodeStroke({ id: "b", samples: samples(3, 300, 300) })); // (300,300)→(320,306)
  assert.deepEqual(hitStrokes(ink, 120, 206, 2), ["a"]);
  assert.deepEqual(hitStrokes(ink, 310, 303, 2), ["b"]);
  assert.deepEqual(hitStrokes(ink, 200, 250, 2), []);
  assert.deepEqual(hitStrokes(ink, 120, 230, 30), ["a"], "radius reaches the stroke");
});

test("translate moves only the named strokes, by editing two integers", () => {
  let ink = newInk(1, 612, 792);
  ink = appendStroke(ink, encodeStroke({ id: "a", samples: samples() }));
  ink = appendStroke(ink, encodeStroke({ id: "b", samples: samples(3, 300, 300) }));
  const moved = translateStrokes(ink, ["a"], 10.5, -20);
  const a = decodeStroke(moved.strokes[0]), b = decodeStroke(moved.strokes[1]);
  assert.deepEqual([a[0].x, a[0].y, a[4].x, a[4].y], [110.5, 180, 150.5, 192]);
  assert.deepEqual([b[0].x, b[0].y], [300, 300]);
  assert.equal(translateStrokes(ink, ["a"], 0.001, 0), ink, "a sub-unit move is a no-op");
});

test("partial eraser cuts a stroke into the pieces outside the eraser", () => {
  let ink = newInk(1, 612, 792);
  ink = appendStroke(ink, encodeStroke({ id: "a", samples: samples(9) }));   // x = 100 … 180
  const { ink: cut, changed } = eraseAt(ink, 140, 212, 3);                   // hits the middle sample (140, 212)
  assert.equal(changed, true);
  assert.equal(cut.strokes.length, 2);
  const [p, q] = cut.strokes.map(decodeStroke);
  assert.deepEqual([p[0].x, p[p.length - 1].x], [100, 130]);
  assert.deepEqual([q[0].x, q[q.length - 1].x], [150, 180]);
  assert.ok(cut.strokes.every((s) => s.id !== "a" && s.tool === "pen" && s.size === 2), "pieces keep the look, get fresh ids");
  assert.equal(eraseAt(ink, 400, 400, 3).changed, false, "a miss changes nothing");
  // a piece of one sample is dropped
  assert.equal(eraseAt(ink, 110, 203, 3).ink.strokes.length, 1);
});

test("lasso selects strokes with most samples inside the polygon", () => {
  let ink = newInk(1, 612, 792);
  ink = appendStroke(ink, encodeStroke({ id: "a", samples: samples() }));          // 100..140 × 200..212
  ink = appendStroke(ink, encodeStroke({ id: "b", samples: samples(3, 300, 300) })); // 300..320 × 300..306
  const box = [[90, 190], [150, 190], [150, 220], [90, 220]];
  assert.deepEqual(strokesInLasso(ink, box), ["a"]);
  assert.deepEqual(strokesInLasso(ink, [[0, 0], [400, 0], [400, 400], [0, 400]]), ["a", "b"]);
  assert.deepEqual(strokesInLasso(ink, [[125, 190], [150, 190], [150, 220], [125, 220]]), [], "two of five samples inside");
  const b = boundsOf(ink, ["b"]);
  assert.ok(b[0] < 300 && b[2] > 320 && boundsOf(ink, ["zz"]) === null);
});

test("paths: pens are filled outlines, highlighters stroked polylines", () => {
  const pen = strokePath(encodeStroke({ samples: samples() }));
  assert.equal(pen.stroke, false);
  assert.ok(pen.d.startsWith("M") && pen.d.endsWith("Z") && pen.d.includes("Q"));
  const hl = strokePath(encodeStroke({ tool: "highlighter", size: 12, samples: samples(3) }));
  assert.equal(hl.stroke, true);
  assert.equal(hl.width, 12);
  assert.equal(hl.multiply, true);
  assert.equal(hl.d, "M100.00,200.00 L110.00,203.00 L120.00,206.00");
  const dot = strokePath(encodeStroke({ samples: samples(1) }));
  assert.ok(dot.d.length > 0, "a single tap still draws a dot");
  // an unknown tool: a source-over polyline at its mean drawn width; dashes from the size
  const odd = strokePath(encodeStroke({ tool: "crayon", size: 2, ch: "xypw", dash: "dashed", samples: baked(3) }));
  assert.equal(odd.stroke, true);
  assert.equal(odd.multiply, false);
  assert.ok(Math.abs(odd.width - 2 * 0.85) < 1e-9);
  assert.ok(odd.dash.split(" ").length === 2);
  assert.equal(strokePath(encodeStroke({ tool: "pen", brush: "fountain", ch: "xypw", samples: baked(3) })).stroke, false, "an unknown brush draws as a pen from w");
});

test("tool presets: a stored list is validated, bad entries dropped, nothing left → the defaults", () => {
  const stored = [
    { id: "a", kind: "pen", color: "#DC2626", size: 4 },
    { id: "a", kind: "highlighter", color: "#fde047", size: 14 },      // duplicate id → fresh id
    { kind: "pen", color: "red", size: 2 },                             // not hex
    { kind: "pen", color: "#000000", size: 99 },                        // out of range
    { kind: "pencil", color: "#000000", size: 2 },                      // unknown kind
    "junk",
  ];
  const tools = normalizeTools(stored);
  assert.equal(tools.length, 2);
  assert.deepEqual(tools[0], { id: "a", kind: "pen", color: "#dc2626", size: 4 });
  assert.equal(tools[1].kind, "highlighter");
  assert.notEqual(tools[1].id, "a");
  assert.deepEqual(normalizeTools([]), DEFAULT_TOOLS);
  assert.deepEqual(normalizeTools("nope"), DEFAULT_TOOLS);
  assert.equal(normalizeTools(Array(30).fill({ kind: "pen", color: "#000000", size: 2 })).length, MAX_TOOLS);
  assert.deepEqual(toolStyle(tools[0]), { tool: "pen", color: "#dc2626", size: 4, opacity: 1 });
  assert.deepEqual(toolStyle(tools[1]), { tool: "highlighter", color: "#fde047", size: 14, opacity: HIGHLIGHTER_OPACITY });
  // a preset's own opacity — the swatch's alpha when picked — rides along; the kind's usual one is left out
  const [faint, usual, bad] = normalizeTools([
    { kind: "highlighter", color: "#fde047", size: 14, opacity: 0.333 },
    { kind: "highlighter", color: "#fde047", size: 14, opacity: HIGHLIGHTER_OPACITY },
    { kind: "pen", color: "#000000", size: 2, opacity: 5 },
  ]);
  assert.equal(faint.opacity, 0.33);
  assert.equal(toolStyle(faint).opacity, 0.33);
  assert.ok(!("opacity" in usual) && !("opacity" in bad));
  assert.equal(toolStyle(bad).opacity, 1);
});

// tests/shared/inkmerge.json: the same cases gamma/ink.py merge_ink passes
// (backend/tests/test_shared_fixtures.py). A stroke is [id, v] or [id, v,
// look]: v its geometry's version, look the keys over the default stroke.
const MERGE = JSON.parse(readFileSync(new URL("../../tests/shared/inkmerge.json", import.meta.url), "utf8"));
const MERGE_DEFAULT = { tool: "pen", color: "#1f1f1f", size: 1.6, opacity: 1, pen: true, ch: "xy" };
const LOOK_KEYS = ["color", "size", "opacity", "brush", "dash", "nib", "band", "fill", "from", "tool", "pen"];
const mergeFile = (strokes) => strokes === null ? null : { ...newInk(1, 612, 792),
  strokes: strokes.map(([id, v, look]) => ({ id, ...MERGE_DEFAULT, ...(look || {}), pts: [100 + 100 * v, 100] })) };
const mergeTriple = (s) => {
  const look = {};
  for (const k of LOOK_KEYS) if (s[k] != null && (k === "from" || JSON.stringify(s[k]) !== JSON.stringify(MERGE_DEFAULT[k]))) look[k] = s[k];
  return Object.keys(look).length ? [s.id, (s.pts[0] - 100) / 100, look] : [s.id, (s.pts[0] - 100) / 100];
};
for (const c of MERGE.cases) {
  test(`ink merge: ${c.note}`, () => {
    const { ink, clean, lostOurs, returned } = mergeInk(mergeFile(c.base), mergeFile(c.ours), mergeFile(c.theirs),
      c.base === null ? c.base_ids || null : null);
    assert.deepEqual(ink.strokes.map(mergeTriple), c.result);
    assert.equal(clean, c.clean);
    if (c.lost_ours) assert.deepEqual(lostOurs, c.lost_ours);
    if (c.returned) assert.deepEqual(returned, c.returned);
  });
}

test("the merge keeps untouched strokes as the identical objects and ours' file-level extras", () => {
  const a = pen({ id: "a" }), b = encodeStroke({ id: "b", samples: samples(3) });
  const base = { ...newInk(1, 612, 792), strokes: [a, b] };
  const theirs = { ...base, strokes: [a, b, encodeStroke({ id: "x", samples: samples(2) })] };
  const ours = { ...base, origin: "tablet", strokes: [a, { ...b, color: "#dc2626" }] };
  const r = mergeInk(base, ours, theirs);
  assert.equal(r.ink.strokes[0], a);
  assert.equal(r.ink.strokes[1], ours.strokes[1]);
  assert.equal(r.ink.strokes[2], theirs.strokes[2]);
  assert.equal(r.ink.origin, "tablet");
  assert.equal(r.ink.version, 2);
  assert.deepEqual([r.clean, r.lostOurs, r.returned], [true, [], []]);
  // an explicit band at its default equals an omitted one
  const same = mergeInk(base, { ...base, strokes: [{ ...a, band: 1 }, b] }, { ...base, strokes: [a, { ...b, color: "#2563eb" }] });
  assert.equal(same.clean, true);
  assert.equal(same.ink.strokes[1].color, "#2563eb");
});

test("the merged file keeps theirs' space and a stroke equals its copy", () => {
  const a = encodeStroke({ id: "a", samples: samples() });
  const theirs = { ...newInk(2, 500, 700), strokes: [a] };
  const ours = { ...newInk(2, 612, 792), strokes: [a, encodeStroke({ id: "b", samples: samples(3) })] };
  const { ink } = mergeInk({ ...newInk(2, 612, 792), strokes: [] }, ours, theirs);
  assert.deepEqual(ink.space, theirs.space);
  assert.ok(sameStroke(a, { ...a, pts: [...a.pts] }));
  assert.ok(!sameStroke(a, { ...a, color: "#dc2626" }));
  assert.ok(sameStroke({ ...a, t0: null }, { ...a, t0: undefined }));
});

test("serializeInk sorts keys at every level and drops empty ones, so equal strokes are equal bytes", () => {
  const a = encodeStroke({ id: "a", samples: samples(2), t0: 5 });
  const one = serializeInk({ ...newInk(1, 612, 792), strokes: [a] });
  const shuffled = { strokes: [Object.fromEntries(Object.entries(a).reverse())], space: { height: 792, width: 612, page: 1, kind: "pdf-page" },
    version: 2, format: "gamma-ink" };
  assert.equal(serializeInk(shuffled), one);
  assert.ok(one.startsWith('{"format":"gamma-ink","space":{"height":792,"kind":"pdf-page","page":1,"width":612},"strokes":[{"ch":"xyp",'));
  assert.ok(!serializeInk({ ...newInk(1, 612, 792), strokes: [{ ...a, brush: undefined, t0: null }] }).includes("t0"));
});

test("inkProps derives what a group's block carries from its file", () => {
  const ink = { ...newInk(3, 612, 792), strokes: [encodeStroke({ id: "a", samples: samples() })] };
  assert.deepEqual(inkProps(ink, "/api/uploads/x.ink"), {
    ink_url: "/api/uploads/x.ink", ink_strokes: 1, pdf_position: pdfPositionOf(ink) });
  const sheet = { ...newCanvasInk(595.28, 841.89), strokes: [encodeStroke({ id: "a", samples: samples() })] };
  assert.deepEqual(sheet.space, { kind: "canvas", width: 595.28, height: 841.89 });
  assert.deepEqual(inkProps(sheet, "/api/uploads/y.ink"), { ink_url: "/api/uploads/y.ink", ink_strokes: 1, pdf_position: null });
});

// --- replay ------------------------------------------------------------------
const timedStroke = (id, t0, pts, y = 0) => encodeStroke({ id, t0, ch: "xyt", samples: pts.map(([x, t]) => ({ x, y, t })) });
const canvas = (strokes) => ({ ...newCanvasInk(100, 100), strokes });

test("replay: strokes play in the order drawn, each on its own clock, a long pause shortened", () => {
  const a = timedStroke("a", 5000, [[0, 0], [10, 100], [20, 200]]);
  const b = timedStroke("b", 1000, [[0, 0], [10, 50]], 10);          // drawn first, second in the file
  const line = inkTimeline(canvas([a, b]));
  assert.deepEqual(line.strokes.map((s) => [s.id, s.index, s.start, s.end]),
    [["b", 1, 0, 50], ["a", 0, 50 + REPLAY_PAUSE, 250 + REPLAY_PAUSE]]);
  assert.deepEqual(line.strokes[1].times, [50 + REPLAY_PAUSE, 150 + REPLAY_PAUSE, 250 + REPLAY_PAUSE]);
  assert.equal(line.duration, 250 + REPLAY_PAUSE);
});

test("replay: a pause inside a stroke shortens too, a short gap between strokes stays", () => {
  const a = timedStroke("a", 0, [[0, 0], [1, 10000], [2, 10010]]);
  const b = timedStroke("b", 10110, [[0, 0], [1, 20]], 5);          // 100 ms after a lifted
  const line = inkTimeline(canvas([a, b]));
  assert.deepEqual(line.strokes[0].times, [0, REPLAY_PAUSE, REPLAY_PAUSE + 10]);
  assert.deepEqual(line.strokes[1].times, [REPLAY_PAUSE + 110, REPLAY_PAUSE + 130]);
});

test("replay: a stroke without timing follows the one before it in the file; a cut run keeps its time", () => {
  const t = timedStroke("t", 1000, [[0, 0], [1, 30]]);
  const bare = encodeStroke({ id: "u", ch: "xy", samples: [{ x: 0, y: 1 }, { x: 1, y: 1 }, { x: 2, y: 1 }] });
  const early = timedStroke("e", 10, [[0, 0], [1, 10]], 2);
  const line = inkTimeline(canvas([t, bare, early]));
  assert.deepEqual(line.strokes.map((s) => s.id), ["e", "t", "u"]);
  const u = line.strokes[2];
  assert.equal(u.start, line.strokes[1].end + REPLAY_PAUSE / 2);
  assert.deepEqual(u.times.map((v) => v - u.start), [0, 150, 300]);
  // the partial eraser's run from 500 ms into a stroke begun at 1000 was written at 1500, after a stroke at 1400
  const run = encodeStroke({ id: "r", t0: 1000, ch: "xyt", samples: [{ x: 0, y: 0, t: 500 }, { x: 1, y: 0, t: 520 }] });
  const mid = timedStroke("m", 1400, [[0, 0], [1, 50]], 3);
  assert.deepEqual(inkTimeline(canvas([run, mid])).strokes.map((s) => [s.id, s.start, s.end]), [["m", 0, 50], ["r", 100, 120]]);
});

test("replay: a long drawing plays within REPLAY_MAX; an empty one takes no time", () => {
  const many = Array.from({ length: 100 }, (_, i) => timedStroke(`s${i}`, i * 1000, [[0, 0], [1, 300]], i));
  const line = inkTimeline(canvas(many));
  assert.equal(line.duration, REPLAY_MAX);
  assert.ok(Math.abs(line.strokes[99].end - REPLAY_MAX) < 1e-6);
  assert.ok(line.strokes.every((s, i) => !i || s.start >= line.strokes[i - 1].end));
  assert.deepEqual(inkTimeline(canvas([])), { duration: 0, strokes: [], times: new Map() });
  assert.deepEqual(inkTimeline(null), { duration: 0, strokes: [], times: new Map() });
});

test("replay: the drawing at a moment holds the strokes begun, the one being written cut short", () => {
  const a = timedStroke("a", 0, [[0, 0], [10, 100], [20, 200]]);
  const b = timedStroke("b", 250, [[0, 0], [10, 100]], 10);
  const ink = canvas([a, b]);
  const line = inkTimeline(ink);                                    // a: 0–200, b: 250–350
  assert.deepEqual(inkAtTime(ink, line, -1).strokes, []);
  const first = inkAtTime(ink, line, 120);
  assert.deepEqual(first.strokes.map((s) => s.id), ["a"]);
  assert.deepEqual(decodeStroke(first.strokes[0]).map((p) => p.x), [0, 10]);
  assert.equal(first.strokes[0].t0, 0);
  const second = inkAtTime(ink, line, 260);
  assert.equal(second.strokes[0], a);
  assert.deepEqual(decodeStroke(second.strokes[1]).map((p) => p.x), [0]);
  assert.equal(inkAtTime(ink, line, line.duration), ink);
  assert.equal(ink.strokes[0], a);                                  // the file is untouched
});

// --- the eraser filter (docs/dev/handwriting.md "Erasing") ------------------

test("eraserKeep is the account's filter as one predicate; an unknown tool goes only under all", () => {
  const pen = { tool: "pen" }, mono = { tool: "pen", brush: "monoline" }, hl = { tool: "highlighter" }, odd = { tool: "crayon" }, bare = {};
  assert.deepEqual([pen, mono, hl, odd, bare].map(eraserKeep("all")), [true, true, true, true, true]);
  assert.deepEqual([pen, mono, hl, odd, bare].map(eraserKeep("pen")), [true, true, false, false, true], "a stroke without a tool is a pen");
  assert.deepEqual([pen, mono, hl, odd, bare].map(eraserKeep("highlighter")), [false, false, true, false, false]);
  assert.deepEqual([pen, hl].map(eraserKeep(undefined)), [true, true], "no filter keeps everything");
});

test("hitStrokes and eraseAt take keep(stroke) and leave the strokes it refuses as they are", () => {
  let ink = newInk(1, 612, 792);
  ink = appendStroke(ink, encodeStroke({ id: "a", samples: samples() }));                           // a pen, (100,200)→(140,212)
  ink = appendStroke(ink, encodeStroke({ id: "h", tool: "highlighter", size: 10, samples: samples() })); // a highlighter on top of it
  assert.deepEqual(hitStrokes(ink, 120, 206, 2), ["a", "h"]);
  assert.deepEqual(hitStrokes(ink, 120, 206, 2, eraserKeep("pen")), ["a"]);
  assert.deepEqual(hitStrokes(ink, 120, 206, 2, eraserKeep("highlighter")), ["h"]);
  const cut = eraseAt(ink, 120, 206, 3, eraserKeep("highlighter"));
  assert.equal(cut.changed, true);
  assert.equal(cut.ink.strokes[0], ink.strokes[0], "the pen stroke is the same object, so its caches survive");
  assert.ok(cut.ink.strokes.slice(1).every((s) => s.tool === "highlighter" && s.from === "h"), "only the highlighter is cut");
  assert.equal(eraseAt(ink, 120, 206, 3, () => false).changed, false, "a filter that refuses everything changes nothing");
});

test("a preset keeps its smoothing level and a highlighter's straightening; toolStyle carries them to the layer", () => {
  const [pen, hl] = normalizeTools([{ ...DEFAULT_TOOLS[0], stabilize: 3, straight: "always" }, { ...DEFAULT_TOOLS[4], stabilize: 4.2, straight: "always" }]);
  assert.equal(pen.stabilize, 3);
  assert.equal("straight" in pen, false, "straightening is a highlighter's alone");
  assert.deepEqual([hl.stabilize, hl.straight], [4, "always"]);
  assert.deepEqual(normalizeTools([{ ...DEFAULT_TOOLS[0], stabilize: 0, straight: "off" }]), [{ ...DEFAULT_TOOLS[0] }], "0 and a pen's straight are left out");
  assert.deepEqual(normalizeTools([{ ...DEFAULT_TOOLS[4], straight: "hold" }])[0], { ...DEFAULT_TOOLS[4] }, "hold is the default, left out");
  assert.equal(normalizeTools([{ ...DEFAULT_TOOLS[4], stabilize: 9, straight: "maybe" }])[0].stabilize, undefined, "out of range is dropped");
  assert.deepEqual(toolStyle(pen), { tool: "pen", color: pen.color, size: pen.size, opacity: 1, stabilize: 3 });
  assert.deepEqual(toolStyle(hl), { tool: "highlighter", color: hl.color, size: hl.size, opacity: HIGHLIGHTER_OPACITY, stabilize: 4, straight: "always" });
  assert.deepEqual(toolStyle(DEFAULT_TOOLS[4]), { tool: "highlighter", color: DEFAULT_TOOLS[4].color, size: 14, opacity: HIGHLIGHTER_OPACITY }, "a plain preset's style is as before");
});

// --- the clipboard, flips and moving strokes between groups ---------------------

test("cloneStrokes mints fresh ids, drops from, moves by (dx, dy) and keeps t0, pressure, time, width and shape", () => {
  const s = pen({ from: "root", shape: { kind: "line", params: { x1: 100, y1: 200, x2: 140, y2: 212 } }, note: "mine" });
  const used = new Set(["pen"]);
  const [c] = cloneStrokes([s], 10, -5, used);
  assert.notEqual(c.id, "pen");
  assert.ok(used.has(c.id), "the id is recorded as used");
  assert.ok(!("from" in c), "a copy is not a piece");
  assert.deepEqual([c.t0, c.ch, c.note, c.size], [100, "xyptw", "mine", s.size]);
  assert.deepEqual(c.shape, { kind: "line", params: { x1: 110, y1: 195, x2: 150, y2: 207 } }, "the shape's params move with the samples");
  const before = decodeStroke(s), after = decodeStroke(c);
  assert.deepEqual(after.map((q) => [q.x - 10, q.y + 5, q.p, q.t, q.w]), before.map((q) => [q.x, q.y, q.p, q.t, q.w]));
  const [still] = cloneStrokes([s]);
  assert.deepEqual(decodeStroke(still).map((q) => [q.x, q.y]), before.map((q) => [q.x, q.y]), "no offset by default");
  assert.equal(still.shape, s.shape, "and no offset keeps the shape record itself");
  assert.notEqual(still.pts, s.pts, "the samples are copied, never shared");
});

test("appendStrokes adds strokes as they are and re-mints only an id the group already holds", () => {
  const a = pen(), b = pen({ id: "other", from: "pen" });
  const ink = { ...newInk(1, 612, 792), strokes: [a] };
  const { ink: out, ids } = appendStrokes(ink, [b, { ...a, color: "#dc2626" }]);
  assert.equal(out.strokes.length, 3);
  assert.equal(out.strokes[1], b, "a stroke with a free id is the same object, from and all");
  assert.notEqual(ids[1], "pen", "a colliding id is re-minted");
  assert.equal(out.strokes[2].color, "#dc2626");
  assert.deepEqual(ids, out.strokes.slice(1).map((s) => s.id));
  assert.equal(appendStrokes(ink, []).ink, ink, "nothing to add: the same object");
});

test("flipStrokes mirrors the samples about the centre, keeps everything else, and twice is the identity", () => {
  const s = pen({ nib: 30, shape: { kind: "ellipse", params: { cx: 120, cy: 206, rx: 30, ry: 10, angle: 20 } } });
  const h = encodeStroke({ id: "h", tool: "highlighter", samples: samples(4, 300, 400), ch: "xypt" });
  const ink = { ...newInk(1, 612, 792), strokes: [s, h] };
  const box = boundsOf(ink, ["pen"]);
  const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
  const once = flipStrokes(ink, ["pen"], { cx, cy, axis: "h" });
  assert.equal(once.strokes[1], h, "an unselected stroke is the same object");
  const before = decodeStroke(s), after = decodeStroke(once.strokes[0]);
  assert.equal(after.length, before.length);
  after.forEach((q, i) => {
    assert.ok(Math.abs(q.x - (2 * cx - before[i].x)) < 0.011, "x is reflected about the centre");
    assert.deepEqual([q.y, q.p, q.t, q.w], [before[i].y, before[i].p, before[i].t, before[i].w], "y, pressure, time and width stay");
  });
  assert.deepEqual([once.strokes[0].id, once.strokes[0].size, once.strokes[0].ch, once.strokes[0].t0], ["pen", s.size, "xyptw", 100]);
  assert.equal(once.strokes[0].nib, 150, "a nib reflects to 180 − nib");
  const shape = once.strokes[0].shape.params;
  assert.ok(Math.abs(shape.cx - (2 * cx - 120)) < 1e-9 && shape.cy === 206, "a shape's centre reflects with it");
  assert.equal(shape.angle, 160, "an ellipse's angle becomes 180° − angle");
  const twice = flipStrokes(once, ["pen"], { cx, cy, axis: "h" });
  assert.deepEqual(twice.strokes[0].pts, s.pts, "two horizontal flips give back the very samples");
  assert.equal(twice.strokes[0].nib, 30);
  assert.ok(Math.abs(twice.strokes[0].shape.params.cx - 120) < 1e-9 && twice.strokes[0].shape.params.angle === 20);
  const down = flipStrokes(ink, ["pen"], { cx, cy, axis: "v" });
  decodeStroke(down.strokes[0]).forEach((q, i) => {
    assert.ok(Math.abs(q.y - (2 * cy - before[i].y)) < 0.011);
    assert.equal(q.x, before[i].x);
  });
  assert.equal(down.strokes[0].shape.params.angle, 160, "a vertical flip negates the angle, mod 180");
  assert.deepEqual(flipStrokes(down, ["pen"], { cx, cy, axis: "v" }).strokes[0].pts, s.pts, "and twice vertically too");
  assert.equal(flipStrokes(ink, ["none"], { cx, cy, axis: "h" }), ink, "nothing selected: the same object");
  assert.equal(flipStrokes(ink, ["pen"], { cx, cy, axis: "x" }), ink, "an unknown axis: the same object");
  // a rectangle's angle stays in [-45, 45) and a polygon's in [0, 360 / n)
  const r = pen({ id: "r", shape: { kind: "rect", params: { cx: 10, cy: 10, w: 20, h: 8, angle: -45 } } });
  const p = pen({ id: "p", shape: { kind: "polygon", params: { cx: 10, cy: 10, r: 5, n: 5, angle: 50 } } });
  const shapes = flipStrokes({ ...ink, strokes: [r, p] }, ["r", "p"], { cx: 0, cy: 0, axis: "h" });
  assert.deepEqual(shapes.strokes[0].shape.params, { cx: -10, cy: 10, w: 8, h: 20, angle: -45 }, "45° is −45° a quarter turn on, sides swapped");
  assert.deepEqual(shapes.strokes[1].shape.params, { cx: -10, cy: 10, r: 5, n: 5, angle: 58 }, "180 − 50 = 130, mod 72");
});

// --- a shape record follows the samples -----------------------------------------
// (docs/dev/handwriting.md "Client": the params an editor rebuilds `pts`
// from go through every edit that moves the samples)
import { shapeSamples } from "../src/ink/shapes.js";
import { transformPoint } from "../src/ink/ink.js";

const segmentGap = (p, a, b) => {
  const dx = b.x - a.x, dy = b.y - a.y, len2 = dx * dx + dy * dy;
  const u = len2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)) : 0;
  return Math.hypot(p.x - (a.x + u * dx), p.y - (a.y + u * dy));
};
// How far apart two polylines lie: the farthest any sample of either is
// from the other's path.
function polylineGap(a, b) {
  const from = (pts, path) => Math.max(...pts.map((p) => Math.min(...path.slice(1).map((q, i) => segmentGap(p, path[i], q)))));
  return Math.max(from(a, b), from(b, a));
}
// A shape as finishStroke commits it: its dense samples, pen false, xyt, the record beside them.
const SHAPES = {
  line: { x1: 100, y1: 200, x2: 180.5, y2: 230.25 },
  arrow: { x1: 100, y1: 200, x2: 220, y2: 240, head: 18 },
  triangle: { x1: 100, y1: 300, x2: 160, y2: 220, x3: 210, y3: 310 },
  rect: { cx: 150, cy: 250, w: 80, h: 40, angle: 20 },
  polygon: { cx: 150, cy: 250, r: 40, n: 6, angle: 10 },
  circle: { cx: 150, cy: 250, r: 30 },
  ellipse: { cx: 150, cy: 250, rx: 50, ry: 20, angle: 160 },
};
const shaped = (kind, params) => encodeStroke({ id: kind, ch: "xyt", pen: false, samples: shapeSamples(kind, params, { span: 300 }), shape: { kind, params } });

test("a shape record follows every geometry edit: rebuilt from its params after a move, a resize and turn, a copy or a flip, it lies on the moved samples", () => {
  const edits = {
    "a move": (ink, id) => translateStrokes(ink, [id], 12.34, -7.5).strokes[0],
    "scale 1.5 and rotate 30°": (ink, id) => transformStrokes(ink, [id], { cx: 140, cy: 240, scale: 1.5, angle: Math.PI / 6 }).strokes[0],
    "a turn the other way, smaller": (ink, id) => transformStrokes(ink, [id], { cx: 0, cy: 0, scale: 0.75, angle: -1.1 }).strokes[0],
    "a copy with an offset": (ink) => cloneStrokes(ink.strokes, 25.5, 40)[0],
    "a duplicate": (ink, id) => { const d = duplicateStrokes(ink, [id], -10, 5); return d.ink.strokes.find((s) => s.id === d.ids[0]); },
    "a horizontal flip": (ink, id) => flipStrokes(ink, [id], { cx: 133.33, cy: 0, axis: "h" }).strokes[0],
    "a vertical flip": (ink, id) => flipStrokes(ink, [id], { cx: 0, cy: 251.7, axis: "v" }).strokes[0],
  };
  for (const [kind, params] of Object.entries(SHAPES)) {
    const s = shaped(kind, params);
    const ink = { ...newInk(1, 612, 792), strokes: [s] };
    assert.ok(polylineGap(shapeSamples(kind, params), decodeStroke(s)) < 0.01, `${kind}: the samples are the shape's to begin with`);
    for (const [name, edit] of Object.entries(edits)) {
      const out = edit(ink, kind);
      assert.equal(out.shape.kind, kind);
      const gap = polylineGap(shapeSamples(kind, out.shape.params), decodeStroke(out));
      assert.ok(gap <= 0.5, `${kind} after ${name}: rebuilt from ${JSON.stringify(out.shape.params)}, ${gap.toFixed(3)} pt from the moved samples`);
    }
  }
});

test("a shape record's params stay in the recogniser's ranges after a turn, and the edits that change nothing keep it", () => {
  const ink = { ...newInk(1, 612, 792), strokes: Object.entries(SHAPES).map(([kind, params]) => shaped(kind, params)) };
  const turned = transformStrokes(ink, Object.keys(SHAPES), { cx: 140, cy: 240, scale: 1.5, angle: Math.PI / 6 });
  const params = Object.fromEntries(turned.strokes.map((s) => [s.shape.kind, s.shape.params]));
  const [cx, cy] = transformPoint(150, 250, { cx: 140, cy: 240, scale: 1.5, angle: Math.PI / 6 }).map((v) => Math.round(v * 100) / 100);
  assert.deepEqual(params.rect, { cx, cy, w: 60, h: 120, angle: -40 }, "20° + 30° is −40° a quarter turn on: the sides swap, times 1.5");
  assert.equal(params.polygon.angle, 40, "a hexagon's 10° + 30°, in [0°, 60°)");
  assert.equal(params.polygon.n, 6, "the side count as it is");
  assert.equal(params.ellipse.angle, 10, "an ellipse's 160° + 30°, in [0°, 180°)");
  assert.deepEqual([params.ellipse.rx, params.ellipse.ry, params.circle.r, params.arrow.head], [75, 30, 45, 27], "the lengths times the scale");
  assert.ok(!("angle" in params.circle), "a circle has no angle to turn");
  // unselected strokes, and edits that move nothing, keep the very objects
  const one = transformStrokes(ink, ["rect"], { cx: 0, cy: 0, scale: 2 });
  assert.ok(one.strokes.every((s, i) => s.shape.kind === "rect" || s === ink.strokes[i]));
  assert.equal(translateStrokes(ink, ["rect"], 0, 0.001), ink, "a move under the grid moves nothing");
  assert.equal(transformStrokes(ink, ["rect"], { cx: 0, cy: 0 }), ink);
  const turnedLine = transformStrokes(ink, ["line"], { cx: 100, cy: 200, scale: 1, angle: Math.PI / 2 }).strokes[0].shape.params;
  assert.deepEqual(turnedLine, { x1: 100, y1: 200, x2: 69.75, y2: 280.5 }, "a quarter turn about its first end");
});

test("a curve's points follow the samples; a record of a kind this build does not know is dropped by an edit, the stroke kept", () => {
  const pts = [{ x: 100, y: 200 }, { x: 130, y: 180 }, { x: 170, y: 220 }, { x: 200, y: 200 }];
  const params = Object.fromEntries(pts.flatMap((p, i) => [[`x${i + 1}`, p.x], [`y${i + 1}`, p.y]]));
  const curve = encodeStroke({ id: "c", ch: "xy", pen: false, samples: pts, shape: { kind: "curve", params: { ...params, closed: false } } });
  const odd = encodeStroke({ id: "o", ch: "xy", pen: false, samples: pts, shape: { kind: "spiral", params: { cx: 150, cy: 200, turns: 3 } },
    fill: { color: "#ff0000", opacity: 0.25 }, note: "mine" });
  const ink = { ...newInk(1, 612, 792), strokes: [curve, odd] };
  const edits = [
    translateStrokes(ink, ["c", "o"], 5, 6),
    transformStrokes(ink, ["c", "o"], { cx: 150, cy: 200, scale: 1.5, angle: Math.PI / 6 }),
    { ...ink, strokes: cloneStrokes(ink.strokes, -4, 8) },
    flipStrokes(ink, ["c", "o"], { cx: 150, cy: 0, axis: "h" }),
  ];
  for (const out of edits) {
    const [c, o] = out.strokes;
    const moved = decodeStroke(c);
    moved.forEach((q, i) => {
      assert.ok(Math.abs(c.shape.params[`x${i + 1}`] - q.x) < 0.011 && Math.abs(c.shape.params[`y${i + 1}`] - q.y) < 0.011, "each point with its sample");
    });
    assert.equal(c.shape.params.closed, false, "a flag as it is");
    assert.ok(!("shape" in o), "an unknown kind's params cannot follow: the record goes");
    assert.deepEqual([o.fill, o.note, o.ch], [odd.fill, "mine", "xy"], "the stroke keeps everything else");
  }
});

test("a key a record leaves out follows too: an arrow's head drawn by default is written on a resize, an angle of 0 on a turn or a flip", () => {
  const bare = {
    arrow: { x1: 100, y1: 200, x2: 260, y2: 230 },      // its head drawn as min(length / 4, 24) = 24
    rect: { cx: 150, cy: 250, w: 80, h: 40 },
    polygon: { cx: 150, cy: 250, r: 40, n: 5 },
    ellipse: { cx: 150, cy: 250, rx: 50, ry: 20 },
  };
  const edits = {
    "scale 1.5 and rotate 30°": (ink, id) => transformStrokes(ink, [id], { cx: 140, cy: 240, scale: 1.5, angle: Math.PI / 6 }).strokes[0],
    "a resize": (ink, id) => transformStrokes(ink, [id], { cx: 0, cy: 0, scale: 2 }).strokes[0],
    "a horizontal flip": (ink, id) => flipStrokes(ink, [id], { cx: 133.33, cy: 0, axis: "h" }).strokes[0],
  };
  for (const [kind, params] of Object.entries(bare)) {
    const ink = { ...newInk(1, 612, 792), strokes: [shaped(kind, params)] };
    for (const [name, edit] of Object.entries(edits)) {
      const out = edit(ink, kind);
      const gap = polylineGap(shapeSamples(kind, out.shape.params), decodeStroke(out));
      assert.ok(gap <= 0.5, `${kind} after ${name}: rebuilt from ${JSON.stringify(out.shape.params)}, ${gap.toFixed(3)} pt from the moved samples`);
    }
  }
  const resized = transformStrokes({ ...newInk(1, 612, 792), strokes: [shaped("arrow", bare.arrow)] }, ["arrow"], { cx: 0, cy: 0, scale: 2 });
  assert.equal(resized.strokes[0].shape.params.head, 48, "the head it was drawn with, times the scale");
  const moved = translateStrokes({ ...newInk(1, 612, 792), strokes: [shaped("rect", bare.rect), shaped("arrow", bare.arrow)] }, ["rect", "arrow"], 5, 5);
  assert.ok(moved.strokes.every((s) => !("angle" in s.shape.params) && !("head" in s.shape.params)), "a move writes neither");
});

// The preset list on the account (docs/dev/handwriting.md "Presets follow
// the account"): a hidden flag, and keys this build does not know kept as
// they are, under a byte cap per preset.
import { visibleTools } from "../src/ink/ink.js";
test("normalizeTools keeps a hidden flag and unknown keys per preset, capped at 512 bytes; visibleTools filters the hidden ones", () => {
  const stored = [
    { ...DEFAULT_TOOLS[0], hidden: true, dash: "dotted", nib: 45 },
    { ...DEFAULT_TOOLS[1], hidden: false },
    { ...DEFAULT_TOOLS[4], hidden: "yes", style: { kind: "marker" } },
  ];
  const [hidden, shown, hl] = normalizeTools(JSON.parse(JSON.stringify(stored)));
  assert.deepEqual(hidden, { ...DEFAULT_TOOLS[0], hidden: true, dash: "dotted", nib: 45 }, "the known keys first, then the extras verbatim");
  assert.deepEqual(Object.keys(hidden), ["id", "kind", "color", "size", "dash", "hidden", "nib"], "dash is known, nib an extra");
  assert.equal("hidden" in shown, false, "false is left out");
  assert.equal("hidden" in hl, false, "only true hides");
  assert.deepEqual(hl.style, { kind: "marker" }, "an object rides along");
  assert.deepEqual(visibleTools([hidden, shown, hl]), [shown, hl]);
  const plain = [shown, hl];
  assert.equal(visibleTools(plain), plain, "the same list when nothing is hidden");
  // the cap: an extra that would take the preset past 512 bytes is dropped, the next small one still fits
  const big = normalizeTools([{ ...DEFAULT_TOOLS[0], blob: "x".repeat(600), tiny: 1 }])[0];
  assert.equal("blob" in big, false);
  assert.equal(big.tiny, 1);
  assert.ok(JSON.stringify(big).length <= 512);
  // the cap counts UTF-8 bytes (what the profile stores), not code units: 200 CJK characters are 600 bytes
  const cjk = normalizeTools([{ ...DEFAULT_TOOLS[0], note: "\u4e2d".repeat(200), tiny: 1 }])[0];
  assert.equal("note" in cjk, false);
  assert.equal(cjk.tiny, 1);
  // a list that hides every preset shows its first: the strip is never empty
  const all = normalizeTools([{ ...DEFAULT_TOOLS[0], hidden: true }, { ...DEFAULT_TOOLS[1], hidden: true }]);
  assert.deepEqual(all.map((t) => !!t.hidden), [false, true]);
  assert.equal(visibleTools(all).length, 1);
  // a round trip through JSON changes nothing
  assert.deepEqual(normalizeTools(JSON.parse(JSON.stringify([hidden, shown, hl]))), [hidden, shown, hl]);
});

// --- pen styles and lines (docs/dev/handwriting.md "The brush table", "Dash geometry") ----
import { BRUSH_ORDER, KNOWN_BRUSHES, bandRuns, dashArray, dashDots, dashPattern, dashRuns, opacityBand, strokeOpacity } from "../src/ink/ink.js";

test("normalizeTools keeps a pen's known brush and a line on pens and highlighters; toolStyle carries both to the stroke", () => {
  const [brush, pencil, plain, odd, hl, hlBrush] = normalizeTools([
    { ...DEFAULT_TOOLS[0], brush: "brush", dash: "dashed" },
    { ...DEFAULT_TOOLS[1], brush: "pencil", dash: "wavy" },
    { ...DEFAULT_TOOLS[2], brush: "pen", dash: null },
    { ...DEFAULT_TOOLS[3], brush: "fountain" },
    { ...DEFAULT_TOOLS[4], dash: "dotted" },
    { ...DEFAULT_TOOLS[5], brush: "pencil" },
  ]);
  assert.deepEqual([brush.brush, brush.dash], ["brush", "dashed"]);
  assert.deepEqual([pencil.brush, "dash" in pencil], ["pencil", false], "an unknown line is dropped");
  assert.ok(!("brush" in plain) && !("dash" in plain), "'pen' is the absent key, and solid has no dash");
  assert.equal("brush" in odd, false, "an unknown brush is dropped: this build could not bake it");
  assert.deepEqual([hl.dash, "brush" in hlBrush], ["dotted", false], "a highlighter takes a line, never a brush");
  assert.deepEqual(toolStyle(brush), { tool: "pen", color: brush.color, size: brush.size, opacity: 1, brush: "brush", dash: "dashed" });
  assert.deepEqual(toolStyle(hl), { tool: "highlighter", color: hl.color, size: hl.size, opacity: HIGHLIGHTER_OPACITY, dash: "dotted" });
  assert.deepEqual(normalizeTools(JSON.parse(JSON.stringify([brush, pencil, hl]))), [brush, pencil, hl], "a round trip changes nothing");
  assert.deepEqual(BRUSH_ORDER, [null, "monoline", "brush", "pencil"]);
  assert.deepEqual([...KNOWN_BRUSHES], ["monoline", "brush", "pencil"]);
  assert.equal(inkFeatures({ ...newInk(1, 612, 792), strokes: [pen({ brush: "brush" }), pen({ id: "q", brush: "pencil", dash: "dotted" })] }), null,
    "the brushes and the lines are this build's own");
});

test("restyleStrokes sets or clears a line on every selected tool, ignores other values and is the same object for a no-op", () => {
  const a = pen(), h = encodeStroke({ id: "h", tool: "highlighter", size: 14, samples: samples(3) });
  const ink = { ...newInk(1, 612, 792), strokes: [a, h] };
  const dashed = restyleStrokes(ink, ["pen", "h"], { dash: "dashed" });
  assert.deepEqual(dashed.strokes.map((s) => s.dash), ["dashed", "dashed"]);
  assert.equal(dashed.strokes[0].pts, a.pts, "the samples stay: a look change");
  assert.equal(restyleStrokes(dashed, ["pen", "h"], { dash: "dashed" }), dashed, "already dashed: no entry");
  const solid = restyleStrokes(dashed, ["pen"], { dash: null });
  assert.equal("dash" in solid.strokes[0], false, "null clears the key");
  assert.equal(solid.strokes[1], dashed.strokes[1]);
  assert.equal(restyleStrokes(ink, ["pen"], { dash: "wavy" }), ink, "an unknown line is ignored");
  assert.equal(restyleStrokes(ink, ["pen"], { brush: "pencil" }), ink, "a brush change is restyleBrush's (finishStroke.js)");
  assert.equal(restyleStrokes(ink, ["pen"], { dash: null }), ink, "solid already");
});

import { restyleBrush } from "../src/ink/finishStroke.js";
test("restyleBrush on a stroke with a shape record changes its brush key alone: it stays constant width, no w and no o", () => {
  const line = { kind: "line", params: { x1: 100, y1: 200, x2: 180, y2: 200 } };
  const shaped = encodeStroke({ id: "s", brush: "brush", pen: false, size: 3, shape: line, t0: 5, ch: "xyt",
    samples: [{ x: 100, y: 200, t: 0 }, { x: 180, y: 200, t: 144 }] });
  const ink = { ...newInk(1, 612, 792), strokes: [shaped, pen({ id: "f" })] };
  const pencil = restyleBrush(ink, ["s", "f"], "pencil");
  const [s, f] = pencil.strokes;
  assert.deepEqual([s.brush, s.ch, s.shape, s.pen, s.size, s.t0], ["pencil", "xyt", line, false, 3, 5], "the key changes, the channels do not");
  assert.deepEqual(decodeStroke(s), decodeStroke(shaped), "the same samples");
  assert.ok(decodeStroke(s).every((q) => q.w === undefined && q.o === undefined && strokeWidth(s, q) === 3), "the size end to end");
  assert.equal(f.ch, "xyptwo", "a freehand pen selected with it is baked for the pencil");
  const plain = restyleBrush(pencil, ["s"], null).strokes[0];
  assert.deepEqual(["brush" in plain, plain.ch, plain.shape], [false, "xyt", line], "the plain pen drops the key, and nothing is baked either");
  assert.equal(restyleBrush(ink, ["s"], "brush"), ink, "already a Brush: the same drawing");
  // a shape an older build baked the Brush's tapers for (both ends at the floor) loses them
  const tapered = { ...shaped, ch: "xytw", pts: encodePoints([{ x: 100, y: 200, t: 0, w: 0.1 }, { x: 180, y: 200, t: 144, w: 0.1 }], "xytw") };
  const even = restyleBrush({ ...ink, strokes: [tapered] }, ["s"], "brush").strokes[0];
  assert.deepEqual([even.brush, even.ch, even.pts], ["brush", "xyt", shaped.pts]);
});

// tests/shared/inkdash.json: the same patterns and runs gamma/ink.py draws
// (backend/tests/test_shared_fixtures.py).
const DASHES = JSON.parse(readFileSync(new URL("../../tests/shared/inkdash.json", import.meta.url), "utf8"));
const close = (a, b, what) => assert.ok(Math.abs(a - b) < 0.0006, `${what}: ${a} vs ${b}`);
for (const row of DASHES.patterns) {
  test(`dash pattern: ${row.note}`, () => {
    const pattern = dashPattern({ dash: row.dash || undefined, size: row.size });
    if (row.on == null) assert.equal(pattern, null);
    else { close(pattern[0], row.on, "on"); close(pattern[1], row.off, "off"); }
    // a stroke of that look: a stroked path takes the dasharray; a pressure stroke none, it is cut into runs
    const look = { id: "d", size: row.size, ...(row.dash ? { dash: row.dash } : {}) };
    const hl = row.size >= 5;
    const stroke = row.w ? encodeStroke({ ...look, ch: "xyptw", samples: baked(4) })
      : encodeStroke({ ...look, tool: hl ? "highlighter" : "pen", brush: hl ? undefined : "monoline", ch: "xypt", samples: samples(4) });
    assert.equal(strokePath(stroke).dash ?? null, row.dasharray);
    if (row.dasharray) assert.equal(dashArray(stroke), row.dasharray);
  });
}
test("dash runs: a dashed pressure stroke is cut by arc length from phase 0, its boundaries lerped (inkdash.json runs)", () => {
  const { runs } = DASHES;
  const got = dashRuns(runs.samples, { dash: runs.dash, size: runs.size });
  assert.equal(got.length, runs.runs.length);
  got.forEach((run, i) => {
    assert.equal(run.length, runs.runs[i].samples.length, `run ${i} sample count`);
    run.forEach((s, j) => { for (const k of ["x", "y", "p", "t", "w"]) close(s[k], runs.runs[i].samples[j][k], `run ${i} sample ${j} ${k}`); });
  });
  // tilt is copied from the sample before a boundary
  const tilted = runs.samples.map((s, i) => ({ ...s, a: 40 + i, z: 100 + i }));
  const cut = dashRuns(tilted, { dash: "dashed", size: 2 })[1][0];
  assert.deepEqual([cut.a, cut.z], [41, 101]);
  // a solid stroke and a dot are one run
  assert.deepEqual(dashRuns(runs.samples, { size: 2 }), [runs.samples]);
  assert.deepEqual(dashRuns([runs.samples[0]], { dash: "dashed", size: 2 }), [[runs.samples[0]]]);
});
test("dash dots: a dotted pressure stroke is a dot at each on-run's middle, its diameter size × w there (inkdash.json dots)", () => {
  const { dots } = DASHES;
  const got = dashDots(dots.samples, { dash: dots.dash, size: dots.size });
  assert.equal(got.length, dots.dots.length);
  got.forEach((q, i) => {
    close(q.arc, dots.dots[i].arc, `dot ${i} arc`);
    close(q.x, dots.dots[i].x, `dot ${i} x`);
    close(q.w, dots.dots[i].w, `dot ${i} w`);
    close(dots.size * q.w, dots.dots[i].diameter, `dot ${i} diameter`);
  });
});

test("strokePath draws a dashed pressure stroke as its runs, a dotted one as dots, a dashed Ballpoint as a dashed centreline", () => {
  const long = Array.from({ length: 11 }, (_, i) => ({ x: 100 + 4 * i, y: 200, p: 0.5, t: 8 * i, w: 1 }));
  const solid = strokePath(encodeStroke({ id: "s", size: 2, ch: "xyptw", samples: long }));
  const dashed = strokePath(encodeStroke({ id: "d", size: 2, dash: "dashed", ch: "xyptw", samples: long }));
  const dotted = strokePath(encodeStroke({ id: "o", size: 2, dash: "dotted", ch: "xyptw", samples: long }));
  const subpaths = (d) => d.split("M").length - 1;
  assert.equal(subpaths(solid.d), 1);
  assert.equal(subpaths(dashed.d), 5, "40 pt in 4 + 4 pt dashes: five outlined runs in one path");
  assert.equal(subpaths(dotted.d), 10, "a dot every 4.2 pt");
  assert.ok(dotted.d.includes("a1.00,1.00"), "each dot a circle of radius size × w / 2");
  assert.ok([solid, dashed, dotted].every((p) => p.stroke === false && p.dash === undefined));
  const ball = strokePath(encodeStroke({ id: "b", size: 2, brush: "monoline", dash: "dashed", ch: "xypt", samples: samples() }));
  assert.deepEqual([ball.stroke, ball.width, ball.dash, ball.multiply], [true, 2, "4 4", false]);
  assert.equal(strokePath(encodeStroke({ id: "b2", size: 2, brush: "monoline", ch: "xypt", samples: samples() })).stroke, false,
    "a solid Ballpoint keeps its outline");
});

test("the pencil: its shades are up to three paths, o quantised up to 0.33 / 0.66 / 1, cut where o crosses a band", () => {
  assert.deepEqual([0.15, 0.33, 0.34, 0.66, 0.7, 1, undefined].map(opacityBand), [0.33, 0.33, 0.66, 0.66, 1, 1, 1]);
  const ramp = [{ x: 0, y: 0, o: 0.2, w: 1 }, { x: 10, y: 0, o: 0.8, w: 2 }, { x: 20, y: 0, o: 0.8, w: 2 }];
  const runs = bandRuns(ramp);
  assert.deepEqual(runs.map((r) => r.band), [0.33, 0.66, 1]);
  close(runs[0].samples.at(-1).x, 10 * (0.33 - 0.2) / 0.6, "the first cut where o reaches 0.33");
  close(runs[1].samples[0].x, runs[0].samples.at(-1).x, "the next run starts at the cut");
  close(runs[1].samples.at(-1).x, 10 * (0.66 - 0.2) / 0.6, "and ends where o reaches 0.66");
  close(runs[1].samples.at(-1).w, 1 + (0.66 - 0.2) / 0.6, "the cut lerps w too");
  assert.deepEqual(runs[2].samples.slice(1), ramp.slice(1));
  assert.deepEqual(bandRuns([{ x: 0, y: 0 }, { x: 1, y: 0 }]).map((r) => r.band), [1], "no o: one run at full strength");
  // a pencil stroke's path: bands, the grain marked; a mouse pencil is even, still grain
  const s = encodeStroke({ id: "p", size: 2, brush: "pencil", ch: "xyptwo",
    samples: ramp.map((q, i) => ({ ...q, p: 0.2 + 0.3 * i, t: 8 * i })) });
  const path = strokePath(s);
  assert.deepEqual(path.bands.map((b) => b.alpha), [0.33, 0.66, 1]);
  assert.ok(path.grain && path.stroke === false && path.bands.every((b) => b.d.startsWith("M")));
  const mouse = strokePath(encodeStroke({ id: "m", size: 2, brush: "pencil", pen: false, ch: "xyt", samples: samples() }));
  assert.deepEqual([!!mouse.bands, mouse.grain, mouse.stroke], [false, true, false]);
  assert.equal(strokePath(pen()).grain, false);
  // the one opacity rule: opacity × o
  assert.equal(strokeOpacity({ opacity: 0.5 }, { o: 0.4 }), 0.2);
  assert.equal(strokeOpacity({ opacity: 0.5 }, {}), 0.5);
  assert.equal(strokeOpacity({}, { o: 0.4 }), 0.4);
});

import { outlineOptions, runOutline } from "../src/ink/ink.js";
// The box around an outline path's points.
const pathBox = (d) => {
  const pts = [...d.matchAll(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)].map((m) => [Number(m[1]), Number(m[2])]);
  return [Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))];
};

test("a dash or a shade is outlined at its drawn width whatever its length: ten times the page's scale, a two-sample run with its midpoint (gamma/ink.py stroke_outline)", () => {
  // a 0.8 pt pen's 1.6 pt dash: 0.8 pt across with round ends, never a disc as wide as the dash is long
  const [x0, y0, x1, y1] = pathBox(runOutline([{ x: 0, y: 0, w: 1 }, { x: 0.8, y: 0, w: 1 }, { x: 1.6, y: 0, w: 1 }], outlineOptions({ size: 0.8 })));
  assert.ok(Math.abs(y1 - y0 - 0.8) < 0.05, `0.8 pt across (${y0} … ${y1})`);
  assert.ok(Math.abs(x0 + 0.4) < 0.05 && Math.abs(x1 - 2) < 0.05, `round ends half a width past the run (${x0} … ${x1})`);
  // a dash of two lerped ends: size × w all along, its end cap included (perfect-freehand alone draws
  // the points it puts between two at pressure 0.5, the nominal size)
  const two = pathBox(runOutline([{ x: 0, y: 0, w: 1.5 }, { x: 4, y: 0, w: 1.5 }], outlineOptions({ size: 2 })));
  assert.ok(Math.abs(two[2] - 5.5) < 0.05 && Math.abs(two[3] - 1.5) < 0.05, `the end cap at size × w / 2 = 1.5 (${two})`);
  // so a thin dashed pressure stroke keeps to its line
  const line = Array.from({ length: 21 }, (_, i) => ({ x: 100 + 1.2 * i, y: 200, p: 0.5, t: 8 * i, w: 1 }));
  const thin = strokePath(encodeStroke({ id: "t", size: 0.8, dash: "dashed", ch: "xyptw", samples: line }));
  const box = pathBox(thin.d);
  assert.ok(box[1] > 199.55 && box[3] < 200.45, `a 0.8 pt dashed line stays 0.8 pt tall (${box})`);
  // and so does one short enough for a single dash (longer than on, shorter than on + off)
  const short = Array.from({ length: 9 }, (_, i) => ({ x: 100 + 0.3 * i, y: 200, p: 0.5, t: 8 * i, w: 1 }));
  const one = strokePath(encodeStroke({ id: "u", size: 0.8, dash: "dashed", ch: "xyptw", samples: short }));
  const ob = pathBox(one.d);
  assert.equal(one.d.split("M").length - 1, 1, "one dash");
  assert.ok(ob[1] > 199.55 && ob[3] < 200.45, `a one-dash 0.8 pt line stays 0.8 pt tall (${ob})`);
  assert.ok(Math.abs(ob[0] - 99.6) < 0.05 && Math.abs(ob[2] - 102) < 0.05, `the dash is 1.6 pt with round ends (${ob})`);
});

test("a band run of no length is left out, as the server leaves it out (stroke_bands)", () => {
  // o meets the 0.33 edge right at the first sample, then rises: the whole stroke is the middle shade
  const up = bandRuns([{ x: 0, y: 0, o: 0.33, w: 1 }, { x: 10, y: 0, o: 0.5, w: 1 }]);
  assert.deepEqual(up.map((r) => r.band), [0.66]);
  // and falls to an edge at the last sample: no dot of the lighter shade at the end
  const down = bandRuns([{ x: 0, y: 0, o: 0.5, w: 1 }, { x: 10, y: 0, o: 0.33, w: 1 }]);
  assert.deepEqual(down.map((r) => r.band), [0.66]);
  // a stroke of no length is one run, in its last sample's band
  assert.deepEqual(bandRuns([{ x: 5, y: 5, o: 0.2 }, { x: 5, y: 5, o: 0.9 }]).map((r) => [r.band, r.samples.length]), [[1, 2]]);
  assert.deepEqual(bandRuns([]), []);
});

// --- the caches: keyed by the pts array (docs/dev/handwriting.md "Client", Caches) ---

import { cachedStrokePath as cachedPath, strokePlaceholder as placeholderOf } from "../src/ink/ink.js";

test("caches: a colour restyle and a clone with the same pts arrays reuse the polygon; a move or a new size builds it again", () => {
  const ink = { ...newInk(1, 612, 792), strokes: [pen({ id: "a" }), pen({ id: "b", samples: baked(6, 300, 400) })] };
  const before = ink.strokes.map((s) => strokePath(s));
  const box = strokeBounds(ink.strokes[0]), dec = decodeStroke(ink.strokes[0]);
  // a recolour is a new stroke object around the same pts array
  const recoloured = restyleStrokes(ink, ["a"], { color: "#2463eb" });
  assert.notEqual(recoloured.strokes[0], ink.strokes[0]);
  assert.equal(recoloured.strokes[0].pts, ink.strokes[0].pts);
  assert.equal(strokePath(recoloured.strokes[0]), before[0], "the same polygon object after a colour restyle");
  assert.equal(strokeBounds(recoloured.strokes[0]), box);
  assert.equal(decodeStroke(recoloured.strokes[0]), dec);
  // a clone of the drawing whose strokes keep their pts arrays (a merge's composed strokes, withDefaults)
  const clone = { ...ink, strokes: ink.strokes.map((s) => ({ ...s })) };
  assert.deepEqual(clone.strokes.map((s) => strokePath(s)), before);
  assert.ok(clone.strokes.every((s, i) => strokePath(s) === before[i]), "the same polygon objects after a clone with identical pts");
  // a deep copy has new arrays: nothing to reuse until inkStore.adoptStrokes hands the arrays back
  const parsed = JSON.parse(JSON.stringify(ink));
  assert.equal(cachedPath(parsed.strokes[0]), undefined);
  assert.notEqual(strokePath(parsed.strokes[0]), before[0]);
  assert.equal(strokePath(parsed.strokes[0]).d, before[0].d, "built again, to the same outline");
  // a move writes new pts; a width restyle keeps them but changes what the outline is built from
  const moved = translateStrokes(ink, ["a"], 5, 0);
  assert.notEqual(strokePath(moved.strokes[0]), before[0]);
  assert.equal(strokePath(moved.strokes[1]), before[1], "the stroke not moved is untouched");
  const wider = restyleStrokes(ink, ["a"], { size: 4 });
  assert.equal(wider.strokes[0].pts, ink.strokes[0].pts);
  assert.notEqual(strokePath(wider.strokes[0]), before[0]);
  assert.notDeepEqual(strokeBounds(wider.strokes[0]), box);
  assert.equal(decodeStroke(wider.strokes[0]), dec, "the decoded samples do not depend on the size");
  // back to the old size: the first polygon again, from the same array
  assert.equal(strokePath(restyleStrokes(wider, ["a"], { size: ink.strokes[0].size }).strokes[0]), before[0]);
  // a line restyle builds again (a dashed pressure stroke is cut into runs)
  const dashed = restyleStrokes(ink, ["a"], { dash: "dashed" });
  assert.equal(dashed.strokes[0].pts, ink.strokes[0].pts);
  assert.notEqual(strokePath(dashed.strokes[0]), before[0]);
});

test("caches: cachedStrokePath only looks, and a placeholder is the samples as a polyline at the mean width", () => {
  const s = pen({ id: "lazy", samples: baked(4, 10, 10) });
  assert.equal(cachedPath(s), undefined, "nothing built yet");
  const p = placeholderOf(s);
  assert.equal(p.stroke, true);
  assert.match(p.d, /^M10\.00,10\.00 L20\.00,13\.00 L30\.00,16\.00 L40\.00,19\.00$/);
  const widths = decodeStroke(s).map((q) => strokeWidth(s, q));
  assert.ok(Math.abs(p.width - widths.reduce((a, b) => a + b, 0) / widths.length) < 1e-9);
  assert.equal(placeholderOf({ ...s }), p, "kept per pts array too");
  assert.equal(cachedPath(s), undefined, "a placeholder is not the outline");
  const built = strokePath(s);
  assert.equal(cachedPath(s), built);
  assert.equal(cachedPath({ ...s, color: "#000000" }), built);
});
