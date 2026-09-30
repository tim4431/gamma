// node --test ipad/scripts/core.test.mjs (from the repository root, after
// build-core.mjs) — the bundle the app loads, run the way JavaScriptCore
// runs it: a bare context with none of the browser's or Node's globals
// (no TextEncoder, btoa, console, crypto), called only through
// GammaCore.pure / GammaCore.run with JSON strings, and a host whose one
// function answers synchronously like the Swift host.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../GammaIPad/Resources/gamma-core.js", import.meta.url), "utf8");

function core() {
  const context = vm.createContext({});          // only the language's own globals
  vm.runInContext(SOURCE, context);
  const G = context.GammaCore;
  return {
    pure: (name, ...args) => JSON.parse(G.pure(name, JSON.stringify(args))),
    run: async (name, host, ...args) => {
      const slot = G.run(name, JSON.stringify(args), { invoke: (m, a) => host(m, JSON.parse(a)) });
      await new Promise((r) => setImmediate(r)); // JavaScriptCore drains the microtasks when the call returns
      if (!slot.done) throw new Error("the slot was not settled by a synchronous host");
      if (slot.error) throw new Error(slot.error);
      return JSON.parse(slot.value);
    },
    context,
  };
}

test("the bundle needs none of the browser's globals", () => {
  const { context } = core();
  assert.equal(typeof context.GammaCore.pure, "function");
  assert.equal(typeof context.TextEncoder, "function", "polyfilled");
  assert.equal(typeof context.btoa, "function", "polyfilled");
});

test("ink and paper come out as the web app computes them", () => {
  const c = core();
  const stroke = c.pure("encodeStroke", { id: "s1", ch: "xypt", t0: 5, samples: [{ x: 10, y: 20, p: 0.5, t: 0 }, { x: 30, y: 25, p: 0.7, t: 8 }] });
  assert.deepEqual(stroke.pts, [1000, 2000, 500, 0, 2000, 500, 700, 8]);
  const file = c.pure("appendStroke", c.pure("newInk", 1, 612, 792), stroke);
  assert.equal(file.strokes.length, 1);
  const [geo] = c.pure("geometry", file);
  assert.equal(geo.kind, "fill");
  assert.ok(geo.points.length > 4, "an outline polygon");
  assert.deepEqual(c.pure("paperLines", { width: 200, height: 150, pattern: "ruled", spacing: 50 }).lines, [[0, 50, 200, 50], [0, 100, 200, 100]]);
  const id = c.pure("makeId");
  assert.match(id, /^[A-Za-z0-9_-]{12}$/);
  const vp = c.pure("viewportTransform", [0, 0, 612, 792], 0);
  assert.deepEqual(vp.transform, [1, 0, 0, -1, 0, 792]);
  const turned = c.pure("viewportTransform", [0, 0, 612, 792], 90);
  assert.deepEqual([turned.width, turned.height], [792, 612]);
  assert.deepEqual(turned.transform, [0, 1, 1, 0, 0, 0]);
});

test("a notebook made through the host is the one the web app makes", async () => {
  const c = core();
  const pages = new Map();
  const files = new Map();
  const host = (method, args) => {
    const ok = (value) => JSON.stringify({ value: value ?? null });
    switch (method) {
      case "config": return ok({ remoteWs: "w", user: "u", mode: "two-way" });
      case "page": { const p = pages.get(args[0]); return ok({ snapshot: p?.snapshot ?? null, version: p?.version ?? 0 }); }
      case "writeEdit": {
        const [id, snapshot, version] = args;
        if ((pages.get(id)?.version ?? 0) !== version) return ok(0);
        pages.set(id, { snapshot, version: version + 1 });
        return ok(version + 1);
      }
      case "storeText": { const name = `${String(files.size + 1).padStart(24, "0")}${args[1]}`; files.set(name, args[0]); return ok(name); }
      case "readText": return ok(files.get(args[0]) ?? null);
      default: return JSON.stringify({ error: `no ${method}` });
    }
  };
  const bookId = await c.run("createNotebook", host, { title: "On the iPad" });
  const sheet = await c.run("addSheet", host, bookId);
  const view = c.pure("pageView", pages.get(bookId).snapshot, bookId);
  assert.equal(view.kind, "notebook");
  assert.equal(view.sheets.length, 2);
  assert.equal(view.sheets[1].id, sheet);
  const stroke = c.pure("encodeStroke", { id: "k1", samples: [{ x: 5, y: 5 }, { x: 9, y: 9 }] });
  const file = c.pure("appendStroke", c.pure("newCanvasInk", 595.28, 841.89), stroke);
  const url = await c.run("saveInk", host, bookId, { blockId: "g1", ink: file, parent: sheet });
  assert.match(url, /^\/api\/uploads\/0+1\.ink$/);
  const again = c.pure("pageView", pages.get(bookId).snapshot, bookId);
  assert.deepEqual(again.sheets[1].ink, [{ id: "g1", url }]);
  assert.ok(files.get(url.split("/").pop()).startsWith('{"format":"gamma-ink","space":{"height":841.89,"kind":"canvas"'),
    "stored as the bytes every client writes");
  await assert.rejects(c.run("nope", host), /no nope/);
  const timeline = c.pure("inkTimeline", file);
  assert.deepEqual(timeline.strokes.map((s) => [s.id, s.index]), [["k1", 0]], "the replay's timeline, for the notes' replay");
});

test("a page among a note's blocks goes where the browser puts it", async () => {
  const c = core();
  const pages = new Map();
  const host = (method, args) => {
    const ok = (value) => JSON.stringify({ value: value ?? null });
    if (method === "config") return ok({ remoteWs: "w", user: "u", mode: "two-way" });
    if (method === "page") { const p = pages.get(args[0]); return ok({ snapshot: p?.snapshot ?? null, version: p?.version ?? 0 }); }
    if (method === "writeEdit") {
      const [id, snapshot, version] = args;
      if ((pages.get(id)?.version ?? 0) !== version) return ok(0);
      pages.set(id, { snapshot, version: version + 1 });
      return ok(version + 1);
    }
    return JSON.stringify({ error: `no ${method}` });
  };
  const noteId = await c.run("createPage", host, { title: "Notes" });
  const a = await c.run("addNote", host, noteId, { content: "first" });
  const z = await c.run("addNote", host, noteId, { content: "last" });
  const p1 = await c.run("addSheet", host, noteId, null, a);                       // right after the first note
  await c.run("setSheetPaper", host, noteId, p1, { pattern: "grid" });
  const p2 = await c.run("addSheet", host, noteId, null, p1);                      // the page after that page
  const tree = c.pure("tree", pages.get(noteId).snapshot, noteId);
  assert.deepEqual(tree.map((n) => n.id), [a, p1, p2, z]);
  assert.equal(p2, c.pure("pageView", pages.get(noteId).snapshot, noteId).sheets[1].id);
  const second = tree[2].properties;
  assert.equal(second.sheet.pattern, "grid", "the paper of the page before it");
  assert.equal(second.collapsed, true, "folded among a note's blocks");
});
