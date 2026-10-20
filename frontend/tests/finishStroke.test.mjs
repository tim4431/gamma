// node --test tests/  (from frontend/) — the stage between a lifted pen and
// the committed stroke: decimation and the width bake (the brush table).
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
  assert.deepEqual(finishStroke([], { style: { tool: "pen" }, pen: true }), { commit: null, erase: null, label: "ink stroke" });
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
