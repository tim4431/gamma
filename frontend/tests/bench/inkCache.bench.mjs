// A micro-benchmark of the handwriting outline caches (docs/dev/handwriting.md
// "Client", Caches) — not part of `npm test`. Its scenarios are budgeted in
// tests/perf/ink.perf.mjs (`npm run test:perf`); this file stays for
// comparing two copies of the modules. From frontend/:
//
//   node tests/bench/inkCache.bench.mjs                  # the modules in src/ink
//   node tests/bench/inkCache.bench.mjs <dir>/src/ink    # another copy (an older commit's export)
//
// A generated version 2 drawing of 500 strokes (pens with baked widths and a
// few highlighters, ~100 samples each) has its outlines built cold, then again
// after each of the things that hand the layer new stroke objects: the file a
// peer's save names loading in place of the one on screen (they recoloured 10
// strokes and added 5), the merged file this tab's own save comes back with
// (someone recoloured 10 strokes meanwhile), and a colour restyle of 100
// strokes here. For each it prints the median ms over RUNS and how many of the
// strokes kept the outline object they had before, so two copies of the
// modules compare as before / after.
import path from "node:path";
import { pathToFileURL } from "node:url";
import { drawing, installInkShims } from "../perf/fixtures.mjs";

// The app's extensionless imports, the globals inkStore reads as it loads,
// and the files the scenarios serve.
const served = installInkShims();

const dir = path.resolve(process.argv[2] || path.join(import.meta.dirname, "../../src/ink"));
const ink = await import(pathToFileURL(path.join(dir, "ink.js")).href);
const store = await import(pathToFileURL(path.join(dir, "inkStore.js")).href);

const STROKES = 500, RUNS = 7;
const base = drawing(ink, { strokes: STROKES });
const bytes = ink.serializeInk(base);
// A recolour of the first `n` pens and `add` new strokes, as the file another
// writer would leave.
function edited(n, add) {
  const out = JSON.parse(bytes);
  for (const s of out.strokes.filter((x) => x.tool === "pen").slice(0, n)) s.color = "#2463eb";
  for (let i = 0; i < add; i++) out.strokes.push({ ...out.strokes[i], id: `n${i}`, pts: out.strokes[i].pts.map((v, k) => (k === 0 ? v + 500 : v)) });
  return out;
}

const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
const buildAll = (d) => { for (const s of d.strokes) ink.strokePath(s); };
const reused = (before, after) => {
  const was = new Map(before.strokes.map((s) => [s.id, ink.strokePath(s)]));
  return after.strokes.filter((s) => was.get(s.id) === ink.strokePath(s)).length;
};
let fileNo = 0;
const serve = (d) => { const url = `/api/uploads/bench${++fileNo}.ink`; served.set(url, JSON.stringify(d)); return url; };
async function time(scenario) {
  const ms = [];
  let kept = 0;
  for (let r = 0; r < RUNS; r++) {
    const { before, run } = await scenario();
    const t0 = performance.now();
    const after = await run();
    buildAll(after);
    ms.push(performance.now() - t0);
    kept = reused(before, after);
  }
  return { ms: median(ms), kept };
}
// The drawing as a page open leaves it: parsed from its file and drawn.
async function opened() {
  const url = serve(JSON.parse(bytes));
  const d = await store.loadInk(url);
  buildAll(d);
  return { url, d };
}

const rows = [];
rows.push(["cold: open the page, build every outline", await time(async () => {
  const url = serve(JSON.parse(bytes));
  return { before: { strokes: [] }, run: () => store.loadInk(url) };
})]);
rows.push(["a peer saved (10 recoloured, 5 new): their file replaces ours", await time(async () => {
  const { d } = await opened();
  const url = serve(edited(10, 5));
  // inkFor's reload of a block whose file changed, the drawing on screen
  // offered for adoption (an older loadInk takes no second argument)
  return { before: d, run: () => store.loadInk(url, () => d) };
})]);
rows.push(["own save came back merged (someone recoloured 10)", await time(async () => {
  const { url, d } = await opened();
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
})]);
rows.push(["a colour restyle of 100 strokes here", await time(async () => {
  const { d } = await opened();
  const ids = d.strokes.slice(0, 100).map((s) => s.id);
  return { before: d, run: async () => ink.restyleStrokes(d, ids, { color: "#c2255c" }) };
})]);

console.log(`modules: ${dir}`);
console.log(`drawing: ${STROKES} strokes, ${base.strokes.reduce((n, s) => n + s.pts.length / s.ch.length, 0)} samples, ${(bytes.length / 1024).toFixed(0)} KB; median of ${RUNS} runs`);
for (const [what, { ms, kept }] of rows) console.log(`${what.padEnd(64)} ${ms.toFixed(1).padStart(7)} ms   outlines kept ${kept}`);
process.exit(0);
