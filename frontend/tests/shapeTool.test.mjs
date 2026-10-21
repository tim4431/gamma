// node --test tests/  (from frontend/) — the Shapes tool's drags
// (src/ink/shapeTool.js toolShape) over tests/shared/inkshapes.json `tool`:
// the shape record each drag commits with, the polyline shapes.js makes of
// it (how many samples, whether it closes), and the floor under which a
// drag draws nothing.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { SHAPE_MIN_PX, SHAPE_TOOL_KINDS, toolShape } from "../src/ink/shapeTool.js";
import { shapeSamples } from "../src/ink/shapes.js";
import { encodeStroke } from "../src/ink/ink.js";
import { finishStroke, strokeChannels } from "../src/ink/finishStroke.js";

const FIX = JSON.parse(readFileSync(new URL("../../tests/shared/inkshapes.json", import.meta.url), "utf8"));
const point = ([x, y]) => ({ x, y });

test("the tool's kinds and its floor, as the account's inkShapeKind and the docs name them", () => {
  assert.deepEqual(SHAPE_TOOL_KINDS, ["line", "arrow", "rect", "ellipse"]);
  assert.equal(SHAPE_MIN_PX, 4);
  assert.ok(FIX.tool.length >= 16, "the fixture's drags");
  assert.deepEqual([...new Set(FIX.tool.map((c) => c.kind))].sort(), [...SHAPE_TOOL_KINDS].sort(), "every kind has drags");
});

for (const c of FIX.tool) {
  test(`toolShape: ${c.note}`, () => {
    const got = toolShape(c.kind, point(c.from), point(c.to), { shift: c.shift, k: c.k });
    if (c.expect === null) { assert.equal(got, null); return; }
    assert.ok(got, "drew nothing");
    assert.equal(got.kind, c.kind);
    assert.deepEqual(got.params, c.expect.params, "the shape record's params, exactly");
    for (const v of Object.values(got.params)) assert.equal(Math.round(v * 100) / 100, v, "on the 1/100 grid");
    const samples = shapeSamples(got.kind, got.params, { span: 600, p: 0.5 });
    assert.equal(samples.length, c.expect.count, "the samples the stroke commits");
    const first = samples[0], last = samples.at(-1);
    assert.equal(first.x === last.x && first.y === last.y, c.expect.closed, "closed or open");
    assert.equal(first.t, 0);
    assert.equal(last.t, 600, "the time spread over the drag");
  });
}

test("a drag commits through the hold stage: an even line, written xyt, carrying its shape record", () => {
  const shape = toolShape("rect", { x: 50, y: 60 }, { x: 251, y: 161 }, { k: 4 / 3 });
  const style = { tool: "pen", brush: "monoline", color: "#2563eb", size: 2, opacity: 1, dash: "dashed",
    fill: { color: "#2563eb" } };
  const result = finishStroke([{ x: 50, y: 60, p: 0.5, t: 0 }, { x: 251, y: 161, p: 0.5, t: 480 }],
    { style, pen: false, k: 4 / 3, hold: shape });
  assert.equal(result.commit.length, 305);
  assert.equal(result.commit.at(-1).t, 480);
  assert.deepEqual(result.style.shape, { kind: "rect", params: shape.params });
  assert.equal(result.style.pen, false);
  assert.equal(strokeChannels(result.style, result.commit), "xyt", "no pressure, no width law: constant width");
  const stroke = encodeStroke({ ...result.style, samples: result.commit, ch: strokeChannels(result.style, result.commit) });
  assert.equal(stroke.brush, "monoline");
  assert.equal(stroke.dash, "dashed", "the pen's line rides along");
  assert.deepEqual(stroke.fill, { color: "#2563eb" });
  assert.ok(!stroke.pts.some((v) => Number.isNaN(v)));
});

test("Shift's direction about the start, and a box in any direction", () => {
  // straight down with Shift stays straight down
  assert.deepEqual(toolShape("line", { x: 10, y: 10 }, { x: 11, y: 110 }, { shift: true }).params, { x1: 10, y1: 10, x2: 10, y2: 110 });
  // a square laid down and to the left of the start
  assert.deepEqual(toolShape("rect", { x: 100, y: 100 }, { x: 40, y: 120 }, { shift: true }).params, { cx: 70, cy: 130, w: 60, h: 60, angle: 0 });
  // a flat drag still makes a (degenerate) rectangle and ellipse once it passes the floor
  assert.deepEqual(toolShape("ellipse", { x: 0, y: 0 }, { x: 10, y: 0 }).params, { cx: 5, cy: 0, rx: 5, ry: 0, angle: 0 });
  assert.equal(toolShape("triangle", { x: 0, y: 0 }, { x: 50, y: 50 }), null, "not a Shapes tool kind");
  assert.equal(toolShape("line", null, { x: 0, y: 0 }), null);
});
