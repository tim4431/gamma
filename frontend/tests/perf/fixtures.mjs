// The timing budgets' fixtures (docs/dev/debugging.md "Performance
// budgets"), seeded so every run times the same data: an annotated page of
// 1,000 blocks, a drawing of 500 strokes, a PDF page's text runs. The ink
// benches (tests/bench/) draw the same drawing.
import { register } from "node:module";
import { generateNKeysBetween } from "fractional-indexing";
import { seedPositions } from "../../src/shared/model/blockOps.js";

// A seeded generator of numbers in [0, 1), and words drawn with it.
export function rng(seed = 7) {
  return () => (seed = (seed * 16807) % 2147483647) / 2147483647;
}
const WORDS = ("the of a spin field lattice measure state energy phase coupling transition model result "
  + "figure data quantum mean order").split(" ");
export const words = (rnd, n) => Array.from({ length: n }, () => WORDS[Math.floor(rnd() * WORDS.length)]).join(" ");

export const PAGE_ID = "perfpage0001";

// A page of 1,000 blocks as the server sends it (every node with its
// `position`), shaped like a well-annotated paper:
//   - 400 highlights (a pdf_position of 1-4 rects, a 25-word quote, a
//     #rrggbbaa colour), every other one with a note under it and every
//     sixteenth note with one more under that (200 + 25 blocks);
//   - 100 ink groups on the PDF's pages;
//   - 10 sheets, each with 5 ink groups and 5 notes under it;
//   - 30 text boxes;
//   - 60 notes naming two highlights each as [[refs]];
//   - 75 notes, the last of them `lastId`, 2 of them recordings and 20
//     stamped with the moment they were written (t0).
// → {tree, pos (id → position key, blockOps.seedPositions), lastId,
//    highlightIds, count}
export function annotatedPage(rnd = rng(7)) {
  let n = 0;
  const id = (kind) => `${kind}${(n++).toString(36).padStart(5, "0")}`;
  const rect = () => {
    const x1 = rnd() * 500, y1 = rnd() * 700;
    return { x1, y1, x2: x1 + 40 + rnd() * 60, y2: y1 + 12, width: 612, height: 792 };
  };
  const place = (page) => {
    const rects = Array.from({ length: 1 + Math.floor(rnd() * 4) }, rect);
    return { pageNumber: page, boundingRect: { ...rects[0], pageNumber: page }, rects: rects.map((r) => ({ ...r, pageNumber: page })) };
  };
  const block = (kind, content, properties = {}, children = []) => ({ id: id(kind), content, properties, children });
  const top = [], highlightIds = [];
  for (let i = 0; i < 400; i++) {
    const under = i % 2 ? [] : [block("n", words(rnd, 14), {}, i % 16 ? [] : [block("g", words(rnd, 12))])];
    const h = block("h", i % 3 ? "" : words(rnd, 6), { pdf_position: place(1 + (i % 30)), quote: words(rnd, 25), color: "#ffe066aa" }, under);
    highlightIds.push(h.id);
    top.push(h);
  }
  for (let i = 0; i < 100; i++) {
    top.push(block("i", "", { ink_url: `/api/uploads/ink${i}.ink`, ink_strokes: 12, pdf_position: place(1 + (i % 30)) }));
  }
  for (let s = 0; s < 10; s++) {
    const under = [];
    for (let k = 0; k < 5; k++) under.push(block("k", "", { ink_url: `/api/uploads/sheet${s}_${k}.ink`, ink_strokes: 30 }));
    for (let k = 0; k < 5; k++) under.push(block("m", words(rnd, 10)));
    top.push(block("s", "", { sheet: { width: 612, height: 792, pattern: "lined", spacing: 24 } }, under));
  }
  for (let i = 0; i < 30; i++) {
    top.push(block("t", words(rnd, 8), { text_box: { x: rnd() * 400, y: rnd() * 600, w: 160, h: 40, size: 12 }, pdf_page: 1 + (i % 30) }));
  }
  for (let i = 0; i < 60; i++) {
    top.push(block("r", `${words(rnd, 6)} see [[${highlightIds[(i * 7) % 400]}]] and [[${highlightIds[(i * 7 + 3) % 400]}]]`));
  }
  const t0 = 1757760000000;
  for (let i = 0; i < 75; i++) {
    const props = i === 10 || i === 40
      ? { recording: { started: t0 + i * 60000, state: "done", segments: [{ url: `/api/uploads/rec${i}.webm`, started: t0 + i * 60000, dur: 300000, bytes: 2400000, mime: "audio/webm" }] } }
      : i < 20 ? { t0: t0 + i * 1000 } : {};
    top.push(block("f", words(rnd, 16), props));
  }
  const keyed = (list) => {
    const keys = generateNKeysBetween(null, null, list.length);
    return list.map((b, i) => ({ ...b, position: keys[i], children: keyed(b.children) }));
  };
  const tree = keyed(top);
  return { tree, pos: seedPositions(tree, new Map()), lastId: top.at(-1).id, highlightIds, count: n };
}

// The benches' drawing: a version 2 drawing of `strokes` strokes, pens with
// baked widths and every tenth a highlighter, 60-140 samples each, its
// strokes encoded by `ink` (an ink.js module, so a bench draws it with the
// copy it times).
export function drawing(ink, { strokes = 500 } = {}) {
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const out = [];
  for (let i = 0; i < strokes; i++) {
    const hl = i % 10 === 9, n = 60 + Math.floor(rnd() * 80);
    let x = 40 + rnd() * 520, y = 40 + rnd() * 700, dx = rnd() - 0.5, dy = rnd() - 0.5;
    const samples = [];
    for (let j = 0; j < n; j++) {
      dx += (rnd() - 0.5) * 0.4; dy += (rnd() - 0.5) * 0.4;
      x += dx * 0.6; y += dy * 0.6;
      const p = 0.3 + rnd() * 0.6;
      samples.push({ x, y, p, t: j * 8, w: Math.round((1 + (p - 0.5)) * 1000) / 1000 });
    }
    out.push(ink.encodeStroke(hl
      ? { id: `h${i}`, tool: "highlighter", color: "#ffe066", size: 14, opacity: 0.6, pen: true, t0: 1757760000000, samples, ch: "xypt" }
      : { id: `s${i}`, tool: "pen", color: "#1f1f1f", size: 1.6, opacity: 1, pen: true, t0: 1757760000000, samples, ch: "xyptw" }));
  }
  return { format: "gamma-ink", version: 2, space: { kind: "pdf-page", page: 1, width: 612, height: 792 }, strokes: out };
}

// What ink/inkStore.js needs to load under node: the app's extensionless
// imports resolved as Vite resolves them, and the globals
// shared/lib/utils.js reads as it loads. `fetch` answers from the map this
// returns (a path → the file's body; anything else is a 404). Call it
// before importing inkStore.
export function installInkShims() {
  register("data:text/javascript," + encodeURIComponent(`
export async function resolve(specifier, context, next) {
  try { return await next(specifier, context); } catch (err) {
    if (err?.code === "ERR_MODULE_NOT_FOUND" && /^\\.\\.?\\//.test(specifier) && !/\\.[a-z]+$/.test(specifier)) return next(specifier + ".js", context);
    throw err;
  }
}`));
  const served = new Map();
  globalThis.window = globalThis;
  globalThis.location = new URL("http://bench.local/");
  globalThis.dispatchEvent = () => true;
  globalThis.fetch = async (url) => {
    const body = served.get(new URL(String(url), location.href).pathname);
    return body == null ? new Response("{}", { status: 404 }) : new Response(body, { status: 200, headers: { "Content-Type": "application/json" } });
  };
  Object.defineProperty(globalThis, "navigator", { value: {}, configurable: true });   // no Web Locks held open
  return served;
}

// A PDF page's text as pdf.js hands it to the search (`{text, hasEOL}`
// runs): 60 runs, some with an "ﬁ" ligature, a soft hyphen or a word
// hyphenated across the line's end.
export function pageRuns(rnd = rng(3)) {
  return Array.from({ length: 60 }, (_, i) => ({
    text: words(rnd, 9) + (i % 7 === 0 ? " ﬁnite lat­tice trans-" : ""),
    hasEOL: i % 2 === 1 || i % 7 === 0,
  }));
}
