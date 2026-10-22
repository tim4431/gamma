// A micro-benchmark of the eraser's sweep (docs/dev/handwriting.md "Client",
// ink.js eraseAlong and hitStrokesAlong) — not part of `npm test`. From
// frontend/:
//
//   node tests/bench/inkErase.bench.mjs                  # the modules in src/ink
//   node tests/bench/inkErase.bench.mjs <dir>/src/ink    # another copy (an older commit's export)
//
// The layer flushes the eraser's path once an animation frame, so a flush
// has well under a frame to run in. On a generated drawing of 500 strokes
// (pens with baked widths and a few highlighters, ~100 samples each, drawn
// as inkCache.bench.mjs draws them) one flush of a 20-point path, 20 pt
// between points (what a fast mouse covers in a frame or two, 380 pt in
// all), crosses the page through its strokes, as the partial eraser
// (eraseAlong) and the whole-stroke eraser (hitStrokesAlong), and then
// where no stroke lies. Each prints the median ms over RUNS with the decode
// caches warm, as on a page that has drawn its strokes, and over fresh
// copies of the drawing (cold: every stroke near the path decoded first).
// The last line counts what the flush cut and the samples it left inside
// the swept band. A copy without eraseAlong (before the sweep) is timed
// as its layer ran it, the point eraser at each of the path's points.
import path from "node:path";
import { pathToFileURL } from "node:url";

const dir = path.resolve(process.argv[2] || path.join(import.meta.dirname, "../../src/ink"));
const ink = await import(pathToFileURL(path.join(dir, "ink.js")).href);
const eraseAlong = ink.eraseAlong
  || ((d, pts, r) => pts.reduce((acc, p) => { const out = ink.eraseAt(acc.ink, p.x, p.y, r); return out.changed ? out : acc; }, { ink: d, changed: false }));
const hitStrokesAlong = ink.hitStrokesAlong
  || ((d, pts, r) => { const ids = new Set(pts.flatMap((p) => ink.hitStrokes(d, p.x, p.y, r))); return d.strokes.map((s) => s.id).filter((id) => ids.has(id)); });

const STROKES = 500, RUNS = 31, FRAME_MS = 1000 / 60;
function drawing() {
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const strokes = [];
  for (let i = 0; i < STROKES; i++) {
    const hl = i % 10 === 9, n = 60 + Math.floor(rnd() * 80);
    let x = 40 + rnd() * 520, y = 40 + rnd() * 700, dx = rnd() - 0.5, dy = rnd() - 0.5;
    const samples = [];
    for (let j = 0; j < n; j++) {
      dx += (rnd() - 0.5) * 0.4; dy += (rnd() - 0.5) * 0.4;
      x += dx * 0.6; y += dy * 0.6;
      const p = 0.3 + rnd() * 0.6;
      samples.push({ x, y, p, t: j * 8, w: Math.round((1 + (p - 0.5)) * 1000) / 1000 });
    }
    strokes.push(ink.encodeStroke(hl
      ? { id: `h${i}`, tool: "highlighter", color: "#ffe066", size: 14, opacity: 0.6, pen: true, t0: 1757760000000, samples, ch: "xypt" }
      : { id: `s${i}`, tool: "pen", color: "#1f1f1f", size: 1.6, opacity: 1, pen: true, t0: 1757760000000, samples, ch: "xyptw" }));
  }
  return { format: "gamma-ink", version: 2, space: { kind: "pdf-page", page: 1, width: 612, height: 792 }, strokes };
}
// n points from `from`, `step` pt apart, heading `angle` (radians).
const line = (from, angle, step = 20, n = 20) =>
  Array.from({ length: n }, (_, i) => ({ x: from.x + i * step * Math.cos(angle), y: from.y + i * step * Math.sin(angle) }));
const RADIUS = 9 / 1.5;   // the middle eraser, 9 css px, at 150 % zoom

const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
// warm: RUNS on one drawing (its decoded samples cached after the first);
// cold: one run on each of RUNS fresh drawings.
function time(run, make) {
  const warm = [], cold = [];
  const d = make();
  for (let r = 0; r < RUNS; r++) { const t0 = performance.now(); run(d); warm.push(performance.now() - t0); }
  const fresh = Array.from({ length: RUNS }, make);
  for (const f of fresh) { const t0 = performance.now(); run(f); cold.push(performance.now() - t0); }
  return { warm: median(warm.slice(1)), cold: median(cold) };
}

const across = line({ x: 116, y: 210 }, 0.6 * Math.atan2(372, 380));   // through the middle of the page
const above = line({ x: 40, y: 20 }, 0.05, 27);
const clear = () => { const d = drawing(); return { ...d, strokes: d.strokes.filter((s) => ink.strokeBounds(s)[1] > 300) }; };
const rows = [
  ["partial (eraseAlong): one flush across the strokes", time((d) => eraseAlong(d, across, RADIUS), drawing)],
  ["whole strokes (hitStrokesAlong): the same flush", time((d) => hitStrokesAlong(d, across, RADIUS), drawing)],
  ["partial: one flush where no stroke lies", time((d) => eraseAlong(d, above, RADIUS), clear)],
];

// What the flush did: the strokes it cut, their pieces, and the samples
// left closer to the path than the radius plus their own half-width.
const base = drawing(), after = eraseAlong(base, across, RADIUS).ink;
const segDistance = (x, y, a, b) => {
  const dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / l2)) : 0;
  return Math.hypot(a.x + t * dx - x, a.y + t * dy - y);
};
const inside = after.strokes.reduce((n, s) => n + ink.decodeStroke(s).filter((q) =>
  Math.min(...across.slice(1).map((b, i) => segDistance(q.x, q.y, across[i], b))) < RADIUS + ink.strokeWidth(s, q) / 2 - 0.02).length, 0);
const roots = new Set(after.strokes.filter((s) => s.from).map((s) => s.from));

console.log(`modules: ${dir}${ink.eraseAlong ? "" : " (no eraseAlong: the point eraser at each path point)"}`);
console.log(`drawing: ${STROKES} strokes, ${base.strokes.reduce((n, s) => n + s.pts.length / s.ch.length, 0)} samples; a flush of ${across.length} points `
  + `over ${Math.round(Math.hypot(across.at(-1).x - across[0].x, across.at(-1).y - across[0].y))} pt, radius ${RADIUS} pt; median of ${RUNS} runs`);
for (const [what, { warm, cold }] of rows) console.log(`${what.padEnd(56)} ${warm.toFixed(2).padStart(7)} ms warm ${cold.toFixed(2).padStart(7)} ms cold`);
console.log(`the flush touched ${hitStrokesAlong(base, across, RADIUS).length} strokes and cut ${roots.size} into ${after.strokes.length - base.strokes.length + roots.size} pieces; `
  + `${inside} samples left inside the swept band; one frame is ${FRAME_MS.toFixed(1)} ms`);
