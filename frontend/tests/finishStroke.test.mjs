// node --test tests/  (from frontend/) — the stages between a lifted pen and
// the committed stroke: the hold result, the straight highlighter, the
// scribble that erases, then decimation and the width bake (the brush table).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  BRUSHES, DECIMATE_PT, bakeWidths, brushOpacities, brushWidths, decimate, finishStroke, opacityLaw, restyleBrush, strokeChannels, widthLaw,
} from "../src/ink/finishStroke.js";
import { decodeStroke, encodePoints, encodeStroke, newInk, strokeMaxWidth, strokeOpacity, strokeWidth } from "../src/ink/ink.js";

const line = (n = 5) => Array.from({ length: n }, (_, i) => ({ x: 100 + 10 * i, y: 200, p: 0.2 + 0.15 * i, t: 16 * i }));

test("the default pen's law is 1 + (p − 0.5), capped; constant-width styles have none", () => {
  const law = widthLaw({ tool: "pen" });
  assert.equal(law({ p: 0.5 }), 1);
  assert.equal(law({ p: 0 }), 0.5);
  assert.equal(law({ p: 1 }), 1.5);
  assert.equal(law({}), 1, "no pressure draws the nominal size");
  assert.equal(widthLaw({ tool: "pen", brush: "monoline" }), null);
  assert.equal(widthLaw({ tool: "highlighter" }), null);
  assert.equal(widthLaw({ tool: "pen", pen: false }), null);
  assert.equal(widthLaw({ tool: "crayon" }), null, "an unknown tool is written at a constant width");
  assert.equal(widthLaw({ tool: "pen", brush: "fountain" }), null);
  assert.equal(strokeChannels({ tool: "pen", pen: true }), "xyptw");
  assert.equal(strokeChannels({ tool: "pen", brush: "monoline", pen: true }), "xypt");
  assert.equal(strokeChannels({ tool: "highlighter", pen: true }), "xypt");
  assert.equal(strokeChannels({ tool: "pen", pen: false }), "xyt");
});

test("the brush table: Brush and Pencil have laws, the Pencil an opacity law; the channels follow the style and the tilt", () => {
  assert.deepEqual(Object.keys(BRUSHES), ["pen", "monoline", "brush", "pencil", "calligraphy"]);
  assert.equal(widthLaw({ tool: "pen", brush: "brush" })({ p: 0.4 }), 0.8, "the Brush's law before its tapers: 2p");
  assert.equal(widthLaw({ tool: "pen", brush: "brush", pen: false }) !== null, true, "a mouse Brush still writes w, from the speed law");
  assert.equal(widthLaw({ tool: "pen", brush: "pencil", pen: false }), null, "a mouse Pencil is even");
  assert.equal(widthLaw({ tool: "highlighter", brush: "brush" }), null, "a highlighter has no brushes");
  assert.equal(widthLaw({ tool: "pen", brush: "pen" })({ p: 1 }), 1.5, "'pen' names the plain pen");
  assert.ok(Math.abs(widthLaw({ tool: "pen", brush: "pencil" })({ p: 0.5, a: 60 }) - 5 / 3) < 1e-9);
  assert.equal(opacityLaw({ tool: "pen", brush: "pencil" })({ p: 1 }), 1);
  assert.equal(opacityLaw({ tool: "pen", brush: "pencil", pen: false }), null);
  assert.equal(opacityLaw({ tool: "pen" }), null);
  const tilted = [{ x: 0, y: 0, p: 0.5, t: 0 }, { x: 1, y: 0, p: 0.5, t: 8, a: 50, z: 120 }];
  assert.equal(strokeChannels({ tool: "pen", brush: "brush", pen: true }), "xyptw");
  assert.equal(strokeChannels({ tool: "pen", brush: "brush", pen: false }), "xytw");
  assert.equal(strokeChannels({ tool: "pen", brush: "pencil", pen: true }), "xyptwo");
  assert.equal(strokeChannels({ tool: "pen", brush: "pencil", pen: true }, tilted), "xyptazwo", "one tilted sample writes a and z");
  assert.equal(strokeChannels({ tool: "pen", pen: true }, tilted), "xyptazw");
  assert.equal(strokeChannels({ tool: "pen", brush: "pencil", pen: false }, tilted), "xyt", "a mouse or finger stroke keeps no tilt");
  // a stroke with a shape record is constant width whatever its brush: no law at all
  const shape = { kind: "line", params: { x1: 0, y1: 0, x2: 50, y2: 0 } };
  assert.equal(widthLaw({ tool: "pen", brush: "brush", pen: false, shape }), null, "a shape has no speed law");
  assert.equal(widthLaw({ tool: "pen", brush: "brush", pen: true, shape }), null, "nor a pressure law");
  assert.equal(widthLaw({ tool: "pen", pen: true, shape }), null);
  assert.equal(opacityLaw({ tool: "pen", brush: "pencil", pen: true, shape }), null, "nor any shading");
  assert.equal(strokeChannels({ tool: "pen", brush: "brush", pen: false, shape }), "xyt");
  // the untilted samples of a tilted stroke are written upright
  const stroke = encodeStroke({ id: "t", ch: "xyptaz", samples: tilted });
  assert.deepEqual(decodeStroke(stroke).map((s) => [s.a, s.z]), [[90, 0], [50, 120]]);
});

test("brushWidths: the Brush's tapers run over the arc; live it has no end taper; a shape has none", () => {
  const style = { tool: "pen", brush: "brush", pen: true, size: 2 };
  const line = Array.from({ length: 21 }, (_, i) => ({ x: i, y: 0, p: 0.5, t: 8 * i }));
  const done = brushWidths(line, style), live = brushWidths(line, style, { live: true });
  assert.equal(done[0], 0.1, "the start of the stroke is the floor");
  assert.equal(done[20], 0.1, "and so is its end");
  assert.equal(done[10], 1, "the middle is 2p");
  assert.deepEqual(live.slice(0, 15), done.slice(0, 15), "the start taper is the same live");
  assert.equal(live[20], 1, "the stroke in progress has not ended: no end taper");
  // a short mark: each taper at most half its length, so its middle reaches 2p; a tap is not tapered at all
  const comma = Array.from({ length: 9 }, (_, i) => ({ x: i * 0.25, y: 0, p: 0.5, t: 8 * i }));
  assert.equal(brushWidths(comma, style)[4], 1, "a 2 pt mark of a 2 pt brush is 2p wide in its middle");
  assert.ok(brushWidths(comma, style, { live: true })[4] < 0.5, "while it is written the start taper keeps its full length");
  assert.deepEqual(brushWidths([{ x: 3, y: 4, p: 0.5, t: 0 }], style), [1], "a tap draws the law's width");
  assert.deepEqual(brushWidths([{ x: 3, y: 4, p: 0.5, t: 0 }, { x: 3, y: 4, p: 0.5, t: 30 }], style), [1, 1], "and so does a tap held still");
  assert.deepEqual(brushWidths([{ x: 3, y: 4, p: 0.5, t: 0 }], style, { live: true }), [0.1], "but not while the pen is down: no blot before it moves");
  // a shape (an even, snapped stroke) is constant width: no tapers, no speed law, no w
  const shape = line.map(({ x, y, t }) => ({ x: x * 4, y, t }));
  assert.equal(brushWidths(shape, { ...style, pen: false, shape: { kind: "line", params: {} } }), null, "a shape bakes no w");
  assert.equal(brushWidths(shape, { ...style, pen: false, shape: { kind: "line", params: {} } }, { live: true }), null, "nor while it is held");
  const sped = brushWidths(shape, { ...style, pen: false });
  assert.ok(sped[10] < 0.2, `the same samples drawn freehand by a fast mouse are thin (${sped[10]})`);
  assert.equal(brushWidths(line, { tool: "pen", brush: "monoline", pen: true }), null);
  assert.equal(brushOpacities(line, style), null);
  assert.deepEqual(brushOpacities(line.slice(0, 2), { tool: "pen", brush: "pencil", pen: true }), [0.575, 0.575]);
});

test("finishStroke: a held shape is constant width whatever the preset's style: it keeps its brush, bakes no w or o and is written xyt", () => {
  const samples = line(10);
  const hold = { kind: "line", params: { x1: 0, y1: 0, x2: 50, y2: 0 } };
  for (const brush of ["brush", "pencil", "monoline", undefined]) {
    for (const pen of [true, false]) {
      const out = finishStroke(samples, { style: { tool: "pen", brush, color: "#f00", size: 2 }, pen, hold });
      const what = `${brush || "plain"} preset, ${pen ? "a stylus" : "a mouse"}`;
      assert.equal(out.style.brush, brush, `${what}: the brush key stays`);
      assert.equal(strokeChannels(out.style, out.commit), "xyt", what);
      assert.ok(out.commit.every((s) => s.w === undefined && s.o === undefined), `${what}: nothing baked`);
      // encoded and drawn: the size end to end, where a Brush's tapers left both samples of a line at the floor
      const stroke = encodeStroke({ ...out.style, id: "s", t0: 1, samples: out.commit, ch: strokeChannels(out.style, out.commit) });
      assert.deepEqual([stroke.ch, stroke.brush, stroke.shape.kind], ["xyt", brush, "line"]);
      assert.deepEqual(decodeStroke(stroke).map((q) => strokeWidth(stroke, q)), [2, 2], `${what}: the preset's size at both ends`);
    }
  }
  // a closed shape too
  const circle = finishStroke(samples, { style: { tool: "pen", brush: "brush", size: 3 }, pen: true, hold: { kind: "circle", params: { cx: 100, cy: 100, r: 20 } } });
  assert.ok(circle.commit.length >= 49 && circle.commit.every((s) => s.w === undefined), "a Brush circle is even all round");
  assert.equal(strokeChannels(circle.style, circle.commit), "xyt");
});

// --- the selection's brush change ------------------------------------------------

test("restyleBrush bakes the selected pens again for the new brush from their pressure and tilt; highlighters and no-ops stay", () => {
  const samples = Array.from({ length: 9 }, (_, i) => ({ x: 10 + 2 * i, y: 20, p: 0.2 + 0.1 * i, t: 8 * i, a: 40 + i, z: 200 }));
  const pen = encodeStroke({ id: "p", size: 2, t0: 5, ch: "xyptazw", samples: bakeWidths(samples, { tool: "pen", pen: true }), note: "mine" });
  const hl = encodeStroke({ id: "h", tool: "highlighter", size: 14, ch: "xypt", samples });
  const mouse = encodeStroke({ id: "m", size: 2, pen: false, ch: "xyt", samples: samples.map(({ x, y, t }) => ({ x, y, t })) });
  const ink = { ...newInk(1, 612, 792), strokes: [pen, hl, mouse] };
  const brushed = restyleBrush(ink, ["p", "h", "m"], "brush");
  const [b, h, m] = brushed.strokes;
  assert.equal(h, hl, "a highlighter takes no brush");
  assert.deepEqual([b.brush, b.ch, b.size, b.t0, b.note, b.id], ["brush", "xyptazw", 2, 5, "mine", "p"]);
  const expect = brushWidths(samples, { tool: "pen", brush: "brush", pen: true, size: 2 });
  assert.deepEqual(decodeStroke(b).map((s) => s.w), expect, "re-baked by the Brush's law from the stored pressure");
  assert.deepEqual(decodeStroke(b).map((s) => [s.p, s.a, s.z, s.t]), decodeStroke(pen).map((s) => [s.p, s.a, s.z, s.t]), "pressure, tilt and time untouched");
  assert.deepEqual([m.brush, m.ch, m.pen], ["brush", "xytw", false], "a mouse stroke turned Brush gets the speed law");
  assert.ok(decodeStroke(m).every((s) => s.w > 0 && s.p === undefined));
  // the pencil writes o; the Ballpoint and the plain pen give back the w they need
  const pencil = restyleBrush(brushed, ["p"], "pencil").strokes[0];
  assert.deepEqual([pencil.brush, pencil.ch], ["pencil", "xyptazwo"]);
  const [first] = decodeStroke(pencil);
  assert.ok(Math.abs(first.w - BRUSHES.pencil.w(samples[0])) < 0.0006 && Math.abs(first.o - 0.32) < 1e-9, "tilt widens, pressure darkens");
  const ball = restyleBrush({ ...ink, strokes: [pencil] }, ["p"], "monoline").strokes[0];
  assert.deepEqual([ball.brush, ball.ch], ["monoline", "xyptaz"], "the Ballpoint writes neither w nor o");
  const back = restyleBrush({ ...ink, strokes: [ball] }, ["p"], null).strokes[0];
  assert.equal("brush" in back, false, "the plain pen is the absent key");
  assert.deepEqual(back.pts, pen.pts, "and bakes the very samples it was written with");
  assert.equal(restyleBrush({ ...ink, strokes: [ball] }, ["p"], "pen").strokes[0].pts.length, back.pts.length, "'pen' is the plain pen too");
  // nothing to change: the same objects
  assert.equal(restyleBrush(ink, ["p"], null), ink, "already the plain pen");
  assert.equal(restyleBrush(ink, ["h"], "pencil"), ink, "only a highlighter selected");
  assert.equal(restyleBrush(ink, ["p"], "fountain"), ink, "an unknown brush is ignored");
  assert.equal(restyleBrush(ink, [], "brush"), ink);
  // already that brush and baked for it (within the rounding of the stored samples): the same object;
  // a key that disagrees with its widths (an older merge's brush on plain widths, or the reverse) is baked again
  const wavy = Array.from({ length: 40 }, (_, i) => ({ x: 10 + 0.737 * i, y: 20 + 3 * Math.sin(i / 3), p: 0.3 + 0.01 * i, t: 8 * i }));
  const brushStyle = { tool: "pen", brush: "brush", pen: true, size: 1.2 };
  const real = encodeStroke({ id: "r", ...brushStyle, ch: "xyptw", samples: bakeWidths(wavy, brushStyle) });
  const wavyInk = { ...newInk(1, 612, 792), strokes: [real] };
  assert.equal(restyleBrush(wavyInk, ["r"], "brush"), wavyInk, "choosing its brush again changes nothing");
  const plainWidths = encodeStroke({ id: "r", ...brushStyle, ch: "xyptw", samples: bakeWidths(wavy, { tool: "pen", pen: true }) });
  const near = (a, b) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= 0.02);
  const fixed = restyleBrush({ ...wavyInk, strokes: [plainWidths] }, ["r"], "brush").strokes[0];
  assert.ok(near(decodeStroke(fixed).map((s) => s.w), decodeStroke(real).map((s) => s.w)), "a Brush key on plain widths gets the Brush's");
  const { brush: _b, ...taperedPlain } = real;
  const plain = restyleBrush({ ...wavyInk, strokes: [taperedPlain] }, ["r"], null).strokes[0];
  assert.deepEqual(decodeStroke(plain).map((s) => s.w), decodeStroke(plainWidths).map((s) => s.w), "a plain pen on tapered widths gets the pen's");
  // a letter this build does not know keeps its place by stride
  const foreign = { ...pen, ch: "xyqptw", pts: encodePoints(bakeWidths(samples, { tool: "pen", pen: true }).map((s, i) => ({ ...s, rest: [i] })), "xyqptw") };
  const kept = restyleBrush({ ...ink, strokes: [foreign] }, ["p"], "pencil").strokes[0];
  assert.equal(kept.ch, "xyqptwo");
  assert.deepEqual(decodeStroke(kept).map((s) => s.rest[0]), samples.map((_, i) => i));
});

test("bakeWidths writes w for a pressure pen only, three decimals, and leaves the samples otherwise alone", () => {
  const baked = bakeWidths(line(3), { tool: "pen", pen: true });
  assert.deepEqual(baked.map((s) => s.w), [0.7, 0.85, 1]);
  assert.deepEqual(baked.map(({ w, ...rest }) => rest), line(3));
  const same = line(3);
  assert.equal(bakeWidths(same, { tool: "highlighter", pen: true }), same);
  assert.equal(bakeWidths(same, { tool: "pen", brush: "monoline", pen: true }), same);
  assert.equal(bakeWidths(same, { tool: "pen", pen: false }), same);
});

test("decimate drops samples closer than 0.1 pt to the kept one before, keeping the first and the lift", () => {
  const dense = [{ x: 0, y: 0 }, { x: 0.02, y: 0 }, { x: 0.05, y: 0.01 }, { x: 0.2, y: 0 }, { x: 0.25, y: 0 }, { x: 1, y: 0 }];
  assert.deepEqual(decimate(dense), [{ x: 0, y: 0 }, { x: 0.2, y: 0 }, { x: 1, y: 0 }]);
  const sparse = line(4);
  assert.equal(decimate(sparse), sparse, "nothing dropped: the same array");
  // the lift sits on the last kept sample: it replaces it, so the stroke ends at the pen tip
  const lift = [{ x: 0, y: 0 }, { x: 5, y: 0 }, { x: 5.01, y: 0, p: 0.9 }];
  assert.deepEqual(decimate(lift), [{ x: 0, y: 0 }, { x: 5.01, y: 0, p: 0.9 }]);
  assert.equal(decimate([{ x: 0, y: 0 }, { x: 0.01, y: 0 }]).length, 2, "a tap keeps both its samples");
  assert.equal(DECIMATE_PT, 0.1);
});

test("finishStroke decimates then bakes, and names its stages' result", () => {
  const samples = [...line(3), { x: 120.01, y: 200, p: 0.9, t: 40 }];
  const out = finishStroke(samples, { style: { tool: "pen", color: "#1f1f1f", size: 2 }, pen: true });
  assert.equal(out.erase, null);
  assert.equal(out.label, "ink stroke");
  assert.deepEqual(out.commit.map((s) => [s.x, s.w]), [[100, 0.7], [110, 0.85], [120.01, 1.4]]);
  const mouse = finishStroke(line(3), { style: { tool: "pen", size: 2 }, pen: false });
  assert.ok(mouse.commit.every((s) => s.w === undefined));
  const style = { tool: "pen" };
  assert.deepEqual(finishStroke([], { style, pen: true }), { commit: null, erase: null, label: "ink stroke", style, status: null });
  assert.deepEqual(mouse.style, { tool: "pen", size: 2 }, "the style comes back");
  assert.deepEqual([mouse.status, mouse.erase], [null, null]);
});

// --- the hold result -----------------------------------------------------------

test("finishStroke: a hold result commits the shape's polyline with pen: false and the shape record, and names the pill", () => {
  const samples = line(10);                                   // span 144 ms, mean p 0.875
  const style = { tool: "pen", color: "#f00", size: 2 };
  const out = finishStroke(samples, { style, pen: true, hold: { kind: "circle", params: { cx: 100, cy: 100, r: 20 } } });
  assert.equal(out.label, "ink shape");
  assert.equal(out.erase, null);
  assert.deepEqual(out.status, { text: "Shape: circle.", once: false });
  assert.deepEqual(out.style, { ...style, pen: false, shape: { kind: "ellipse", params: { cx: 100, cy: 100, rx: 20, ry: 20, angle: 0 } } },
    "a circle is stored as an ellipse with equal radii");
  assert.equal(strokeChannels(out.style), "xyt", "an even line: no pressure, no width");
  assert.ok(out.commit.length >= 49);
  assert.deepEqual([out.commit[0].t, out.commit.at(-1).t], [0, 144], "t spread over the stroke's span");
  assert.ok(out.commit.every((s) => s.p === 0.88 && s.w === undefined), "p the mean pressure, nothing baked");
  const straight = finishStroke(samples, { style, pen: true, hold: { kind: "line", params: { x1: 0, y1: 0, x2: 50, y2: 0 } } });
  assert.equal(straight.label, "ink straight line");
  assert.deepEqual(straight.status, { text: "Straightened.", once: false });
  assert.deepEqual(straight.style.shape, { kind: "line", params: { x1: 0, y1: 0, x2: 50, y2: 0 } });
  assert.deepEqual(straight.commit.map((s) => [s.x, s.y, s.t]), [[0, 0, 0], [50, 0, 144]]);
  const arrow = finishStroke(samples, { style, pen: true, hold: { kind: "arrow", params: { x1: 0, y1: 0, x2: 50, y2: 0, head: 10 } } });
  assert.equal(arrow.label, "ink straight line");
  assert.equal(arrow.style.shape.kind, "arrow");
  for (const kind of ["rect", "triangle", "polygon", "ellipse"]) {
    const params = { rect: { cx: 0, cy: 0, w: 40, h: 20, angle: 0 }, triangle: { x1: 0, y1: 0, x2: 40, y2: 0, x3: 20, y3: 30 },
      polygon: { cx: 0, cy: 0, r: 30, n: 6, angle: 0 }, ellipse: { cx: 0, cy: 0, rx: 40, ry: 20, angle: 30 } }[kind];
    const shaped = finishStroke(samples, { style, pen: true, hold: { kind, params } });
    assert.equal(shaped.label, "ink shape");
    assert.deepEqual(shaped.style.shape, { kind, params });
    assert.equal(shaped.status.once, false);
  }
  // no hold, or a hold of no kind: the stroke goes on through the other stages
  assert.equal(finishStroke(samples, { style, pen: true, hold: null }).label, "ink stroke");
  assert.equal(finishStroke(samples, { style, pen: true, hold: { kind: null } }).label, "ink stroke");
  assert.equal(finishStroke(samples, { style, pen: true, hold: { kind: "curve", params: {} } }).label, "ink stroke", "an unknown kind has no polyline");
});

// --- the straight highlighter ------------------------------------------------------

const hl = { tool: "highlighter", color: "#ff0", size: 14 };
// a wobbly, slightly rising highlight: chord 95 pt (≥ 4 × 14), every sample within 6 pt of it, 3.6° off horizontal
const wobbly = Array.from({ length: 20 }, (_, i) => ({ x: 100 + 5 * i, y: 300 + (i % 2 ? 2 : -2) + 0.1 * i, p: 0.5, t: 10 * i }));

test("finishStroke: a highlighter preset's Always replaces a straight enough stroke by its chord, snapped horizontal within 4°", () => {
  const out = finishStroke(wobbly, { style: hl, pen: true, straight: "always" });
  assert.equal(out.label, "ink straight line");
  assert.deepEqual(out.status, { text: "Straightened.", once: true }, "the pill shows once a session");
  assert.equal(out.commit.length, 2);
  assert.equal(out.commit[0].y, out.commit[1].y, "snapped horizontal");
  assert.ok(Math.abs(out.commit[1].x - out.commit[0].x - 95.18) < 0.01, "the chord's length kept");
  assert.deepEqual([out.commit[0].t, out.commit[1].t, out.commit[0].p], [0, 190, 0.5]);
  assert.equal(out.style.pen, false);
  assert.equal(out.style.shape.kind, "line");
  assert.equal(strokeChannels(out.style), "xyt");
  // 10° off horizontal stays as drawn: the chord from the first to the last sample
  const slanted = wobbly.map((s, i) => ({ ...s, y: 300 + 0.9 * i }));
  const kept = finishStroke(slanted, { style: hl, pen: true, straight: "always" });
  assert.equal(kept.label, "ink straight line");
  assert.deepEqual(kept.commit.map((s) => [s.x, s.y]), [[100, 300], [slanted.at(-1).x, slanted.at(-1).y]]);
  assert.deepEqual(kept.style.shape.params, { x1: 100, y1: 300, x2: 195, y2: 317.1 });
  // bent (a sample over 6 pt off the chord) or short (under 4 widths): the highlight stays free
  const bent = wobbly.map((s, i) => (i === 10 ? { ...s, y: s.y + 10 } : s));
  const free = finishStroke(bent, { style: hl, pen: true, straight: "always" });
  assert.deepEqual([free.label, free.commit.length, free.style, free.status], ["ink stroke", 20, hl, null]);
  assert.equal(finishStroke(wobbly.slice(0, 10), { style: hl, pen: true, straight: "always" }).label, "ink stroke");
  // Hold and Off never take the chord here, nor does a pen
  assert.equal(finishStroke(wobbly, { style: hl, pen: true, straight: "hold" }).label, "ink stroke");
  assert.equal(finishStroke(wobbly, { style: hl, pen: true, straight: "off" }).label, "ink stroke");
  assert.equal(finishStroke(wobbly, { style: hl, pen: true }).label, "ink stroke");
  assert.equal(finishStroke(wobbly, { style: { tool: "pen", size: 14 }, pen: true, straight: "always" }).label, "ink stroke");
  // a hold result (the Hold mode's line) comes first
  const held = finishStroke(wobbly, { style: hl, pen: true, straight: "always", hold: { kind: "line", params: { x1: 0, y1: 0, x2: 50, y2: 0 } } });
  assert.deepEqual(held.style.shape.params, { x1: 0, y1: 0, x2: 50, y2: 0 });
  assert.equal(held.status.once, false);
});

// --- the scribble ----------------------------------------------------------------

const SCRIBBLE = JSON.parse(readFileSync(new URL("../../tests/shared/inkscribble.json", import.meta.url), "utf8"));
const scribbleCase = SCRIBBLE.cases.find((c) => c.scribble && !c.groups);

test("finishStroke: a scribble erases the pen strokes under it when the preference is on and some are there, else it is ink", () => {
  const samples = scribbleCase.samples.map(([x, y, p, t]) => ({ x, y, p, t }));
  const xs = samples.map((s) => s.x), ys = samples.map((s) => s.y);
  const inside = [{ x: Math.min(...xs) + 2, y: Math.min(...ys) + 2 }, { x: Math.max(...xs) - 2, y: Math.max(...ys) - 2 }];
  const groups = [{ id: "g1", ink: { strokes: [
    encodeStroke({ id: "u", size: 1, ch: "xy", samples: inside }),
    encodeStroke({ id: "h", tool: "highlighter", size: 1, ch: "xy", samples: inside }) ] } }];
  const style = { tool: "pen", color: "#1f1f1f", size: 2 };
  const ctx = { style, pen: true, k: scribbleCase.k, scribble: true, groups };
  assert.deepEqual(finishStroke(samples, ctx), { commit: null, erase: [{ gid: "g1", ids: ["u"] }], label: "ink scribble erase", style, status: null });
  // the eraser filter has the last word; nothing under it, or the preference off, and the scribble is ink
  assert.equal(finishStroke(samples, { ...ctx, keep: (s) => s.tool === "highlighter" }).label, "ink stroke");
  const ink = finishStroke(samples, { ...ctx, groups: [] });
  assert.deepEqual([ink.label, ink.erase, ink.style], ["ink stroke", null, style]);
  assert.ok(ink.commit.length > 8 && ink.commit.every((s) => typeof s.w === "number"), "committed as a pen stroke: decimated and baked");
  assert.equal(finishStroke(samples, { ...ctx, scribble: false }).label, "ink stroke");
  // a highlighter never scribbles, and a hold result wins
  assert.equal(finishStroke(samples, { ...ctx, style: { ...hl } }).label, "ink stroke");
  assert.equal(finishStroke(samples, { ...ctx, hold: { kind: "line", params: { x1: 0, y1: 0, x2: 50, y2: 0 } } }).label, "ink straight line");
  // a word is never a scribble
  const word = SCRIBBLE.cases.find((c) => !c.scribble && /cursive/.test(c.note));
  assert.equal(finishStroke(word.samples.map(([x, y, p, t]) => ({ x, y, p, t })), { ...ctx, k: word.k }).label, "ink stroke");
});

// tests/shared/inkwidth.json: the same rows gamma/ink.py stroke_polyline
// draws (backend/tests/test_shared_fixtures.py) — bake, encode, decode,
// then the one width rule and the one opacity rule. A single-sample row
// bakes its one sample; a multi-sample row the whole stroke, per sample.
const WIDTH = JSON.parse(readFileSync(new URL("../../tests/shared/inkwidth.json", import.meta.url), "utf8"));
const near = (a, b, tol, what) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a} vs ${b}`);
for (const row of WIDTH.rows) {
  test(`width: ${row.note}`, () => {
    const style = { tool: row.tool, brush: row.brush || undefined, pen: row.pen, size: row.size, ...(row.nib != null ? { nib: row.nib } : {}),
      ...(row.shape ? { shape: row.shape } : {}) };
    let stroke, decoded, ch;
    if (row.samples) {
      const samples = row.samples.map(([x, y, p, t, a]) => ({ x, y, ...(p != null ? { p } : {}), t, ...(a != null ? { a } : {}) }));
      const baked = bakeWidths(samples, style);
      if (row.ws) baked.forEach((s, i) => near(s.w, row.ws[i], 0.0005, `w at ${i}`));
      else assert.ok(baked.every((s) => s.w === undefined && s.o === undefined), "nothing baked");
      if (row.os) baked.forEach((s, i) => near(s.o, row.os[i], 0.0005, `o at ${i}`));
      ch = strokeChannels(style, samples);
      if (row.pen === false) assert.equal(ch, row.ws ? "xytw" : "xyt", "a mouse brush is written without pressure, a shape without w");
      stroke = encodeStroke({ id: "w", ...style, samples: baked, ch });
      assert.deepEqual(ch.includes("w") ? stroke.pts.filter((_, i) => i % ch.length === ch.indexOf("w")) : null, row.w_encs);
      if (!row.ws) assert.ok(decodeStroke(stroke).every((q) => strokeWidth(stroke, q) === row.size), "the size at every sample");
      decoded = decodeStroke(stroke)[row.at];
      // the speed law is the same sample for sample whatever the time between them
      if (row.pen === false) assert.deepEqual(bakeWidths(samples.map((s, i) => ({ ...s, t: i })), style).map((s) => s.w), baked.map((s) => s.w));
    } else {
      const [sample] = bakeWidths([{ x: 10, y: 10, p: row.p, a: row.a ?? undefined }], style);
      ch = "xyp" + (row.a != null ? "a" : "") + (sample.w != null ? "w" : "") + (sample.o != null ? "o" : "");
      stroke = encodeStroke({ id: "w", ...style, samples: [sample], ch });
      assert.equal(ch.includes("w") ? stroke.pts[ch.indexOf("w")] : null, row.w_enc);
      if ("o_enc" in row) assert.equal(ch.includes("o") ? stroke.pts[ch.indexOf("o")] : null, row.o_enc);
      [decoded] = decodeStroke(stroke);
    }
    if (row.w == null) assert.equal(decoded.w, undefined);
    else near(decoded.w, row.w, 1e-6, "w");
    near(strokeWidth(stroke, decoded), row.width, 1e-6, "width");
    if (row.o == null) assert.equal(decoded.o, undefined);
    else { near(decoded.o, row.o, 1e-6, "o"); near(strokeOpacity(stroke, decoded), row.o, 1e-6, "opacity"); }
  });
}

test("restyleBrush holds a w that would draw past the server's 100 pt cap there; strokeMaxWidth is size × the largest w", () => {
  const samples = Array.from({ length: 5 }, (_, i) => ({ x: 10 * i, y: 0, p: 1, t: 8 * i, a: 30, z: 0 }));
  const big = encodeStroke({ id: "big", size: 60, ch: "xyptazw", samples: bakeWidths(samples, { tool: "pen", pen: true }) });
  assert.equal(strokeMaxWidth(big), 90, "a 60 pt pen at full pressure: 1.5 × 60");
  const pencil = restyleBrush({ ...newInk(1, 612, 792), strokes: [big] }, ["big"], "pencil").strokes[0];
  assert.ok(strokeMaxWidth(pencil) <= 100, `held at the cap (${strokeMaxWidth(pencil)})`);
  assert.equal(decodeStroke(pencil)[2].w, 1.666, "w = floor(100 / 60, three decimals), not the law's 3.5");
  assert.equal(strokeMaxWidth(encodeStroke({ id: "e", size: 3, ch: "xy", samples: [{ x: 0, y: 0 }] })), 3, "no w: the size");
});

// --- the calligraphy pen (docs/dev/handwriting.md "The brush table") ------------
import { travelAt } from "../src/ink/finishStroke.js";
import { DEFAULT_NIB } from "../src/ink/ink.js";

test("calligraphy: the law reads the travel and the nib, a mouse stroke has it too (xytw), a shape has none", () => {
  const style = { tool: "pen", brush: "calligraphy", size: 2, nib: 45 };
  assert.ok(widthLaw({ ...style, pen: false }), "a mouse or finger stroke bakes the direction alone");
  assert.equal(widthLaw({ ...style, pen: false, shape: { kind: "line", params: {} } }), null, "a shape is even");
  assert.equal(strokeChannels({ ...style, pen: false }), "xytw");
  assert.equal(strokeChannels({ ...style, pen: true }), "xyptw");
  assert.equal(strokeChannels({ ...style, pen: false, shape: { kind: "line", params: {} } }), "xyt");
  const law = BRUSHES.calligraphy.w;
  assert.equal(law({ p: 0.5 }, null, 45), 1, "no travel: the nib's whole length");
  assert.ok(Math.abs(law({ p: 0.5 }, [1, 1], 45) - 1) < 1e-12, "down to the right across a 45° nib: full");
  assert.ok(Math.abs(law({ p: 0.5 }, [1, -1], 45) - 0.12) < 1e-12, "along it: the hairline");
  assert.ok(Math.abs(law({ p: 1 }, [0, 1], 0) - 1.15) < 1e-12, "full pressure: 1.15");
  // the style's nib turns the widths; without one the stroke is a 45° nib
  const down = Array.from({ length: 5 }, (_, i) => ({ x: 10, y: 10 + 3 * i, p: 0.5, t: 8 * i }));
  assert.deepEqual(brushWidths(down, { ...style, pen: true, nib: 0 }), [1, 1, 1, 1, 1], "down across a level nib");
  assert.deepEqual(brushWidths(down, { ...style, pen: true, nib: 90 }), [0.12, 0.12, 0.12, 0.12, 0.12], "down along an upright one");
  assert.deepEqual(brushWidths(down, { tool: "pen", brush: "calligraphy", pen: true }), brushWidths(down, { ...style, pen: true, nib: DEFAULT_NIB }));
  // the pressure counts only when the stroke reads it
  const pressed = down.map((s) => ({ ...s, p: 1 }));
  assert.deepEqual(brushWidths(pressed, { ...style, pen: false, nib: 0 }), [1, 1, 1, 1, 1], "pen false: p read as 0.5");
  assert.deepEqual(brushWidths(pressed, { ...style, pen: true, nib: 0 }), [1.15, 1.15, 1.15, 1.15, 1.15]);
  assert.deepEqual(brushWidths(down, { ...style, pen: true }, { live: true }), brushWidths(down, { ...style, pen: true }), "no tapers: live is the same");
});

test("calligraphy: travelAt is the neighbours' chord, one-sided at the ends, the segment ending there at a reversal, null with no length", () => {
  const pts = [[0, 0], [2, 0], [4, 0], [2, 0], [0, 0]].map(([x, y]) => ({ x, y }));
  assert.deepEqual(travelAt(pts, 0), [2, 0]);
  assert.deepEqual(travelAt(pts, 1), [4, 0]);
  assert.deepEqual(travelAt(pts, 2), [2, 0], "before and after coincide: the segment ending at it");
  assert.deepEqual(travelAt(pts, 4), [-2, 0]);
  assert.equal(travelAt([{ x: 3, y: 3 }], 0), null);
  assert.equal(travelAt([{ x: 3, y: 3 }, { x: 3, y: 3 }], 1), null);
  assert.equal(travelAt([{ x: 3, y: 3 }, { x: 3, y: 3 }, { x: 5, y: 3 }], 0), null, "one sample each way, no farther: a repeated first sample has none");
  assert.deepEqual(travelAt([{ x: 3, y: 3 }, { x: 3, y: 3 }, { x: 5, y: 3 }], 1), [2, 0]);
});

test("restyleBrush to calligraphy bakes with the stroke's own nib, else 45, and writes it; a change away drops it", () => {
  const samples = Array.from({ length: 6 }, (_, i) => ({ x: 10 + 3 * i, y: 20 + 3 * i, p: 0.5, t: 8 * i }));
  const pen = encodeStroke({ id: "p", size: 2, ch: "xyptw", samples: bakeWidths(samples, { tool: "pen", pen: true }) });
  const turned = encodeStroke({ id: "q", size: 2, nib: 135, ch: "xyptw", samples: bakeWidths(samples, { tool: "pen", pen: true }) });
  const mouse = encodeStroke({ id: "m", size: 2, pen: false, ch: "xyt", samples: samples.map(({ x, y, t }) => ({ x, y, t })) });
  const ink = { ...newInk(1, 612, 792), strokes: [pen, turned, mouse] };
  const out = restyleBrush(ink, ["p", "q", "m"], "calligraphy");
  const [p, q, m] = out.strokes;
  assert.deepEqual([p.brush, p.nib, p.ch], ["calligraphy", 45, "xyptw"], "no nib of its own: 45");
  assert.deepEqual(decodeStroke(p).map((s) => s.w), [1, 1, 1, 1, 1, 1], "down to the right across a 45° nib");
  assert.deepEqual([q.nib, ...decodeStroke(q).map((s) => s.w)], [135, 0.12, 0.12, 0.12, 0.12, 0.12, 0.12], "its own nib, along which it runs");
  assert.deepEqual([m.brush, m.ch, m.pen], ["calligraphy", "xytw", false], "a mouse stroke gets w from its direction");
  assert.equal(restyleBrush(out, ["p", "q", "m"], "calligraphy"), out, "choosing it again changes nothing");
  // nib 0 survives (written with != null)
  const flat = restyleBrush({ ...ink, strokes: [{ ...pen, nib: 0 }] }, ["p"], "calligraphy").strokes[0];
  assert.equal(flat.nib, 0);
  const back = restyleBrush(out, ["p", "q"], "brush");
  assert.ok(back.strokes.slice(0, 2).every((s) => s.brush === "brush" && !("nib" in s)), "a change away drops the nib");
  const plain = restyleBrush(out, ["p"], null).strokes[0];
  assert.deepEqual(["brush" in plain, "nib" in plain], [false, false]);
  assert.deepEqual(plain.pts, pen.pts, "and bakes the very samples the pen had");
  // a stroke in calligraphy without a nib (an older writer's) gets one
  const { nib: _n, ...bare } = p;
  const fixed = restyleBrush({ ...ink, strokes: [bare] }, ["p"], "calligraphy").strokes[0];
  assert.equal(fixed.nib, 45);
});

test("finishStroke: a held calligraphy shape keeps its nib and brush, constant width; another brush's shape takes no nib and no stroke a Lock angle", () => {
  const samples = line(10);
  const hold = { kind: "line", params: { x1: 0, y1: 0, x2: 50, y2: 0 } };
  const cal = finishStroke(samples, { style: { tool: "pen", brush: "calligraphy", nib: 30, nibLock: true, color: "#000", size: 2 }, pen: true, hold });
  assert.deepEqual([cal.style.brush, cal.style.nib, "nibLock" in cal.style], ["calligraphy", 30, false]);
  assert.equal(strokeChannels(cal.style, cal.commit), "xyt");
  const mono = finishStroke(samples, { style: { tool: "pen", brush: "monoline", nib: 30, color: "#000", size: 2 }, pen: false, hold });
  assert.equal("nib" in mono.style, false, "a nib only on a calligraphy pen");
  // freehand: decimated and baked with the nib, the style handed back as it came
  const style = { tool: "pen", brush: "calligraphy", nib: 0, color: "#000", size: 2 };
  const down = Array.from({ length: 5 }, (_, i) => ({ x: 10, y: 10 + 3 * i, p: 0.5, t: 8 * i }));
  const free = finishStroke(down, { style, pen: true });
  assert.equal(free.style, style);
  assert.deepEqual(free.commit.map((s) => s.w), [1, 1, 1, 1, 1]);
});
