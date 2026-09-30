// node --test tests/  (from frontend/) — the pure stroke module.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  DEFAULT_TOOLS, HIGHLIGHTER_OPACITY, MAX_TOOLS, appendStroke, boundsOf, decodeStroke, encodeStroke, eraseAt, hitStrokes,
  inkBounds, newInk, normalizeTools, pdfPositionOf, removeStrokes, strokePath, strokeWidth, strokesInLasso, toolStyle,
  translateStrokes, transformStrokes, nearestInkStroke, restyleStrokes, duplicateStrokes, MAX_STROKES,
  nearestInkColor, PEN_COLORS, HIGHLIGHTER_COLORS, mergeInk, serializeInk, inkProps, newCanvasInk, sameStroke,
  inkAtTime, inkTimeline, REPLAY_MAX, REPLAY_PAUSE,
} from "../src/ink/ink.js";

const samples = (n = 5, x0 = 100, y0 = 200) =>
  Array.from({ length: n }, (_, i) => ({ x: x0 + 10 * i, y: y0 + 3 * i, p: 0.2 + 0.15 * i, t: 16 * i }));

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

test("width follows pressure for pens only", () => {
  const pen = encodeStroke({ samples: samples() });
  assert.ok(strokeWidth(pen, 1) > pen.size && strokeWidth(pen, 0) < pen.size);
  const hl = encodeStroke({ tool: "highlighter", size: 8, samples: samples() });
  assert.equal(strokeWidth(hl, 0), 8);
  const mouse = encodeStroke({ pen: false, samples: samples() });
  assert.equal(strokeWidth(mouse, 0.1), mouse.size);
});

test("monoline keeps pressure data but renders the same outline at any pressure", () => {
  const input = samples(9).map((s, i) => ({ ...s, p: (i + 1) / 10 }));
  const mono = encodeStroke({ id: "mono", brush: "monoline", samples: input, ch: "xypt", t0: 100 });
  assert.deepEqual(decodeStroke(mono), input);
  assert.equal(strokeWidth(mono, 0), mono.size);
  assert.equal(strokeWidth(mono, 1), mono.size);
  const hard = encodeStroke({ brush: "monoline", samples: input.map((s) => ({ ...s, p: 1 })) });
  assert.equal(strokePath(mono).d, strokePath(hard).d);
  const softPen = encodeStroke({ samples: input.map((s) => ({ ...s, p: 0 })) });
  const hardPen = encodeStroke({ samples: input.map((s) => ({ ...s, p: 1 })) });
  assert.notEqual(strokePath(softPen).d, strokePath(hardPen).d);
  assert.equal("brush" in softPen, false, "legacy strokes keep the same encoding");
  const ink = { ...newInk(1, 612, 792), strokes: [mono] };
  const cut = eraseAt(ink, 140, 212, 3);
  assert.equal(cut.ink.strokes.length, 2);
  assert(cut.ink.strokes.every((s) => s.brush === "monoline" && s.ch === "xypt" && s.t0 === 100));
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
  assert.equal(pos.pageNumber, 3);
  assert.deepEqual([pos.boundingRect.width, pos.boundingRect.height], [612, 792]);
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
  assert.equal(hl.d, "M100.00,200.00 L110.00,203.00 L120.00,206.00");
  const dot = strokePath(encodeStroke({ samples: samples(1) }));
  assert.ok(dot.d.length > 0, "a single tap still draws a dot");
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
});

test("a custom colour takes the nearest palette colour's name; palette colours are themselves", () => {
  for (const c of [...PEN_COLORS, ...HIGHLIGHTER_COLORS]) assert.equal(nearestInkColor(c), c);
  assert.equal(nearestInkColor("#DC2626"), "#dc2626");
  assert.equal(nearestInkColor("#e11d48"), "#dc2626");  // rose reads as red
  assert.equal(nearestInkColor("#000"), "#1f1f1f");
  assert.equal(nearestInkColor("#fef08a"), "#fde047");  // a paler yellow
  assert.equal(nearestInkColor("rgb(1, 2, 3)"), null);
  assert.equal(nearestInkColor(""), null);
});

// tests/shared/inkmerge.json: the same cases gamma/ink.py merge_ink passes
// (backend/tests/test_shared_fixtures.py).
const MERGE = JSON.parse(readFileSync(new URL("../../tests/shared/inkmerge.json", import.meta.url), "utf8"));
const mergeFile = (strokes) => strokes === null ? null : { ...newInk(1, 612, 792),
  strokes: strokes.map(([id, v]) => ({ id, tool: "pen", color: "#1f1f1f", size: 1.6, opacity: 1, pen: true, ch: "xy", pts: [100 + 100 * v, 100] })) };
for (const c of MERGE.cases) {
  test(`ink merge: ${c.note}`, () => {
    const { ink, clean } = mergeInk(mergeFile(c.base), mergeFile(c.ours), mergeFile(c.theirs));
    assert.deepEqual(ink.strokes.map((s) => [s.id, (s.pts[0] - 100) / 100]), c.result);
    assert.equal(clean, c.clean);
  });
}

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
    version: 1, format: "gamma-ink" };
  assert.equal(serializeInk(shuffled), one);
  assert.ok(one.startsWith('{"format":"gamma-ink","space":{"height":792,"kind":"pdf-page","page":1,"width":612},"strokes":[{"ch":"xyp",'));
  assert.ok(!serializeInk({ ...newInk(1, 612, 792), strokes: [{ ...a, brush: undefined, t0: null }] }).includes("t0"));
});

test("inkProps derives what a group's block carries from its file", () => {
  const ink = { ...newInk(3, 612, 792), strokes: [encodeStroke({ id: "a", samples: samples() })] };
  assert.deepEqual(inkProps(ink, "/api/uploads/x.ink"), {
    ink_url: "/api/uploads/x.ink", ink_strokes: 1, pdf_page: 3, pdf_position: pdfPositionOf(ink) });
  const sheet = { ...newCanvasInk(595.28, 841.89), strokes: [encodeStroke({ id: "a", samples: samples() })] };
  assert.deepEqual(sheet.space, { kind: "canvas", width: 595.28, height: 841.89 });
  assert.deepEqual(inkProps(sheet, "/api/uploads/y.ink"), { ink_url: "/api/uploads/y.ink", ink_strokes: 1, pdf_page: null, pdf_position: null });
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
  assert.deepEqual(inkTimeline(canvas([])), { duration: 0, strokes: [] });
  assert.deepEqual(inkTimeline(null), { duration: 0, strokes: [] });
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
