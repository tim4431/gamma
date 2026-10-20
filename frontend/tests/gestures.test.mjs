// node --test tests/  (from frontend/) — scribble-to-erase over
// tests/shared/inkscribble.json, and the target rules one by one.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { isScribble, scribbleTargets } from "../src/ink/gestures.js";
import { encodeStroke } from "../src/ink/ink.js";

const FIX = JSON.parse(readFileSync(new URL("../../tests/shared/inkscribble.json", import.meta.url), "utf8"));
const decode = (rows) => rows.map(([x, y, p, t]) => ({ x, y, p, t }));
// The fixture's filter, as ink.js eraserKeep builds it: 'all' keeps every
// stroke, 'pen' tool pen of any brush, 'highlighter' tool highlighter.
const keepFor = (filter) => (s) => filter === "all" || (filter === "pen" ? (s.tool || "pen") === "pen" : s.tool === "highlighter");
const groupsOf = (groups) => groups.map((g) => ({ id: g.gid, ink: { strokes: g.strokes.map((s) => encodeStroke({
  id: s.id, tool: s.tool, brush: s.brush, size: s.size, color: "#1f1f1f", ch: "xy", samples: s.samples.map(([x, y]) => ({ x, y })) })) } }));

for (const c of FIX.cases) {
  test(`scribble: ${c.note}`, () => {
    const samples = decode(c.samples);
    assert.equal(isScribble(samples, { k: c.k }), c.scribble);
    if (c.groups) assert.deepEqual(scribbleTargets(samples, groupsOf(c.groups), { keep: keepFor(c.filter) }), c.targets);
  });
}

const scribble = decode(FIX.cases.find((c) => c.scribble && !c.groups).samples);

test("isScribble: the duration can be given, and a slow or missing clock is no scribble", () => {
  assert.equal(isScribble(scribble, { k: 1 }), true);
  assert.equal(isScribble(scribble, { k: 1, durationMs: 2500 }), false, "over 2 s");
  assert.equal(isScribble(scribble, { k: 0.3, durationMs: 1000 }), false, "under 150 css px/s at k = 0.3");
  assert.equal(isScribble(scribble.map(({ x, y, p }) => ({ x, y, p })), { k: 1 }), false, "no t, no duration");
  assert.equal(isScribble(scribble.slice(0, 5), { k: 1 }), false);
  assert.equal(isScribble([], { k: 1 }), false);
});

// A stroke as a rectangle of samples: its bounds are the box plus the width.
const box = (id, x0, y0, x1, y1, extra = {}) => encodeStroke({ id, size: 1, ch: "xy", samples: [{ x: x0, y: y0 }, { x: x1, y: y0 }, { x: x1, y: y1 }, { x: x0, y: y1 }], ...extra });
// A scribble's sample box: a dense zigzag over [x0, x1] × [y0, y1].
const scribbleOver = (x0, y0, x1, y1) => {
  const out = [];
  for (let i = 0; i <= 40; i++) out.push({ x: x0 + ((x1 - x0) * (i % 2 ? 1 : 0)), y: y0 + ((y1 - y0) * i) / 40, p: 0.5, t: 10 * i });
  return out;
};

test("scribbleTargets: 70 % of a stroke's box inside, not a highlighter, not over 3× the scribble, keep's say, group order", () => {
  const s = scribbleOver(100, 100, 200, 140);           // box 100×40, diagonal 107.7
  const groups = [
    { id: "g1", ink: { strokes: [box("in", 110, 105, 190, 135), box("edge", 100, 100, 200, 140)] } },
    { id: "g2", ink: { strokes: [box("sixty", 160, 105, 260, 135), box("seventy", 171, 105, 271, 135), box("hl", 110, 105, 190, 135, { tool: "highlighter" })] } },
    { id: "g3", ink: { strokes: [box("huge", 120, 110, 420, 130), box("tool", 120, 110, 180, 130, { tool: "crayon" })] } },
    { id: "g4", ink: { strokes: [box("far", 400, 400, 450, 450)] } },
  ];
  // "sixty": 100 wide, 40 of it (x 160..200) inside → 40 %; "seventy": 29.5 of 101 inside, under 70 %
  assert.deepEqual(scribbleTargets(s, groups), [{ gid: "g1", ids: ["in", "edge"] }, { gid: "g3", ids: ["tool"] }]);
  const seventy = box("ok", 129, 105, 229, 135);         // 101 wide with the width, 71.5 of it (x 128.5..200) inside → 70.8 %
  assert.deepEqual(scribbleTargets(s, [{ id: "g", ink: { strokes: [seventy] } }]), [{ gid: "g", ids: ["ok"] }]);
  const sixtyNine = box("no", 131, 105, 231, 135);        // 69.5 of 101 inside → 68.8 %
  assert.deepEqual(scribbleTargets(s, [{ id: "g", ink: { strokes: [sixtyNine] } }]), []);
  // the size rule: "huge" is 300 wide (diagonal 301 > 3 × 107.7); a 3× box passes
  const wide = box("wide", 0, 119, 300, 121);              // diagonal ≈ 300 < 323
  assert.deepEqual(scribbleTargets(scribbleOver(0, 100, 300, 140), [{ id: "g", ink: { strokes: [wide] } }]), [{ gid: "g", ids: ["wide"] }]);
  // keep decides last
  assert.deepEqual(scribbleTargets(s, groups, { keep: (st) => st.tool !== "crayon" }), [{ gid: "g1", ids: ["in", "edge"] }]);
  assert.deepEqual(scribbleTargets(s, groups, { keep: () => false }), []);
  assert.deepEqual(scribbleTargets(s, []), []);
  assert.deepEqual(scribbleTargets([], groups), []);
  assert.deepEqual(scribbleTargets(s, [{ id: "empty", ink: { strokes: [] } }, { id: "none" }]), []);
});
