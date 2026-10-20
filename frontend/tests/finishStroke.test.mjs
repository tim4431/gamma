// node --test tests/  (from frontend/) — the stages between a lifted pen and
// the committed stroke: the hold result, the straight highlighter, the
// scribble that erases, then decimation and the width bake (the brush table).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { DECIMATE_PT, bakeWidths, decimate, finishStroke, strokeChannels, widthLaw } from "../src/ink/finishStroke.js";
import { decodeStroke, encodeStroke, strokeWidth } from "../src/ink/ink.js";

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
// then the one width rule.
const WIDTH = JSON.parse(readFileSync(new URL("../../tests/shared/inkwidth.json", import.meta.url), "utf8"));
for (const row of WIDTH.rows) {
  test(`width: ${row.note}`, () => {
    const style = { tool: row.tool, brush: row.brush || undefined, pen: row.pen };
    const [sample] = bakeWidths([{ x: 10, y: 10, p: row.p, a: row.a ?? undefined }], style);
    const ch = "xyp" + (row.a != null ? "a" : "") + (sample.w != null ? "w" : "");
    const stroke = encodeStroke({ id: "w", ...style, size: row.size, samples: [sample], ch });
    assert.equal(ch.includes("w") ? stroke.pts[ch.indexOf("w")] : null, row.w_enc);
    const [decoded] = decodeStroke(stroke);
    if (row.w == null) assert.equal(decoded.w, undefined);
    else assert.ok(Math.abs(decoded.w - row.w) < 1e-6, `w ${decoded.w} vs ${row.w}`);
    assert.ok(Math.abs(strokeWidth(stroke, decoded) - row.width) < 1e-6, `width ${strokeWidth(stroke, decoded)} vs ${row.width}`);
  });
}
