// npm run test:perf (from frontend/) — handwriting on a drawing of 500
// strokes (ink/ink.js, ink/inkStore.js): the outline caches, the eraser's
// flush and the stroke merge, held to budgets (docs/dev/debugging.md
// "Performance budgets"). The scenarios are the benches' (tests/bench/),
// which stay for comparing against another copy of src/ink.
import assert from "node:assert/strict";
import { test } from "node:test";
import { drawing, installInkShims } from "./fixtures.mjs";
import { budget, measure } from "./measure.mjs";

const served = installInkShims();
const ink = await import("../../src/ink/ink.js");
const store = await import("../../src/ink/inkStore.js");

const base = drawing(ink);
const bytes = ink.serializeInk(base);
const buildAll = (d) => { for (const s of d.strokes) ink.strokePath(s); return d; };
// How many strokes of `after` kept the outline object they had in `before`.
const kept = (before, after) => {
  const was = new Map(before.strokes.map((s) => [s.id, ink.strokePath(s)]));
  return after.strokes.filter((s) => was.get(s.id) === ink.strokePath(s)).length;
};
let fileNo = 0;
const serve = (d) => { const url = `/api/uploads/perf${++fileNo}.ink`; served.set(url, JSON.stringify(d)); return url; };
// The file another writer leaves: the first `n` pens recoloured, `add` new strokes.
function edited(n, add) {
  const out = JSON.parse(bytes);
  for (const s of out.strokes.filter((x) => x.tool === "pen").slice(0, n)) s.color = "#2463eb";
  for (let i = 0; i < add; i++) out.strokes.push({ ...out.strokes[i], id: `n${i}`, pts: out.strokes[i].pts.map((v, k) => (k === 0 ? v + 500 : v)) });
  return out;
}
// The drawing as a page open leaves it: loaded from its file, every outline
// built. Opened once and shared by a scenario's runs, none of which changes
// it: the store keeps every file it loads (and their outlines, ~37 MB a
// drawing here), so a fresh one per run would fill the heap.
async function opened() {
  const url = serve(JSON.parse(bytes));
  return { url, d: buildAll(await store.loadInk(url)) };
}

// Each scenario: `setup()` → {before, run}, `run()` → the drawing the layer
// then shows, whose outlines are built inside the timed span.
async function scenario(t, { setup, keep, limit }) {
  const { before, run } = await setup();
  assert.equal(kept(before, buildAll(await run())), keep, "outlines kept");
  budget(t, await measure(async ({ run }) => buildAll(await run()), { setup }), limit);
}

// A cold open is what loadInk does for a file it has not seen (its fetch
// and ink.js loadedInk), without the store's cache, which would keep every
// run's drawing.
const COLD = serve(JSON.parse(bytes));
test("page open, cold: 500 strokes loaded and every outline built", (t) => scenario(t, {
  setup: () => ({ before: { strokes: [] }, run: async () => ink.loadedInk(await (await fetch(COLD)).json()) }),
  keep: 0, limit: 320,
}));

test("a peer's file (10 recoloured, 5 new) replaces the one on screen", async (t) => {
  const { d } = await opened();
  await scenario(t, {
    setup: () => { const url = serve(edited(10, 5)); return { before: d, run: () => store.loadInk(url, () => d) }; },
    keep: 500, limit: 20,
  });
});

test("this tab's own save comes back merged (someone recoloured 10)", async (t) => {
  const { url, d } = await opened();
  await scenario(t, { setup: async () => {
    const id = `own${fileNo}`;
    store.setDraft(id, d, url);
    const mine = serve(JSON.parse(bytes)), merged = serve(edited(10, 0));
    return { before: d, run: async () => {
      store.markSaved(id, d, mine, merged);
      for (let i = 0; i < 50 && store.draft(id)?.url !== merged; i++) await new Promise((r) => setTimeout(r, 0));
      await store.loadInk(merged);
      await new Promise((r) => setTimeout(r, 0));
      return store.draft(id).ink;
    } };
  }, keep: 500, limit: 25 });
});

test("a colour restyle of 100 strokes", async (t) => {
  const { d } = await opened();
  const ids = d.strokes.slice(0, 100).map((s) => s.id);
  await scenario(t, {
    setup: () => ({ before: d, run: async () => ink.restyleStrokes(d, ids, { color: "#c2255c" }) }),
    keep: 500, limit: 2,
  });
});

// The eraser's flush (once an animation frame): a 20-point path, 20 pt
// between points, across the page through its strokes, at the middle
// eraser's radius (9 css px at 150 % zoom).
const line = (from, angle, step = 20, n = 20) =>
  Array.from({ length: n }, (_, i) => ({ x: from.x + i * step * Math.cos(angle), y: from.y + i * step * Math.sin(angle) }));
const RADIUS = 9 / 1.5;
const across = line({ x: 116, y: 210 }, 0.6 * Math.atan2(372, 380));
const above = line({ x: 40, y: 20 }, 0.05, 27);
const fresh = () => drawing(ink);

test("eraseAlong: one flush across the strokes, decoded samples cached", async (t) => {
  const after = ink.eraseAlong(base, across, RADIUS).ink;
  const segDistance = (x, y, a, b) => {
    const dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy;
    const f = l2 ? Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / l2)) : 0;
    return Math.hypot(a.x + f * dx - x, a.y + f * dy - y);
  };
  const inside = after.strokes.reduce((n, s) => n + ink.decodeStroke(s).filter((q) =>
    Math.min(...across.slice(1).map((b, i) => segDistance(q.x, q.y, across[i], b))) < RADIUS + ink.strokeWidth(s, q) / 2 - 0.02).length, 0);
  assert.equal(inside, 0, "samples left inside the swept band");
  const cut = new Set(after.strokes.filter((s) => s.from).map((s) => s.from));
  assert.ok(cut.size > 10);
  assert.deepEqual(new Set(ink.hitStrokesAlong(base, across, RADIUS)), cut, "the whole-stroke eraser hits the strokes the partial one cuts");
  budget(t, await measure(() => ink.eraseAlong(base, across, RADIUS)), 8);
});

test("eraseAlong: the same flush on a drawing not decoded yet", async (t) => {
  budget(t, await measure((d) => ink.eraseAlong(d, across, RADIUS), { setup: fresh }), 30);
});

test("hitStrokesAlong: the same flush, whole strokes, not decoded yet", async (t) => {
  budget(t, await measure((d) => ink.hitStrokesAlong(d, across, RADIUS), { setup: fresh }), 25);
});

test("eraseAlong: a flush where no stroke lies, not decoded yet", async (t) => {
  const clear = () => { const d = fresh(); return { ...d, strokes: d.strokes.filter((s) => ink.strokeBounds(s)[1] > 300) }; };
  const d = clear(), out = ink.eraseAlong(d, above, RADIUS);
  assert.equal(out.changed, false);
  assert.equal(out.ink, d, "a sweep that cuts nothing gives the same ink");
  budget(t, await measure((c) => ink.eraseAlong(c, above, RADIUS), { setup: clear }), 1);
});

test("mergeInk x20: 500 strokes changed on both sides", async (t) => {
  const recolour = (strokes, from, to, color) => strokes.map((s, i) => (i >= from && i < to ? { ...s, color } : s));
  const added = (prefix) => base.strokes.slice(0, 5).map((s, i) => ({ ...s, id: `${prefix}${i}` }));
  // ours recoloured the first 10 and drew 5; theirs recoloured 10 others,
  // erased the first 20 and drew 5
  const ours = { ...base, strokes: [...recolour(base.strokes, 0, 10, "#2463eb"), ...added("o")] };
  const theirs = { ...base, strokes: [...recolour(base.strokes, 100, 110, "#c2255c").slice(20), ...added("t")] };
  const merged = ink.mergeInk(base, ours, theirs);
  assert.equal(merged.clean, false, "an edit beats a delete, and says so");
  assert.equal(merged.returned.length, 10);
  assert.equal(merged.ink.strokes.length, 500);
  budget(t, await measure(() => { for (let k = 0; k < 20; k++) ink.mergeInk(base, ours, theirs); }), 12);
});
