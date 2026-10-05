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
  assert.equal(view.kind, "page", "a notebook is a page with sheets");
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

// A host that keeps pages in memory, versioned like the Swift store.
function pagesHost() {
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
  return { pages, host };
}

test("a page among a note's blocks goes where the browser puts it", async () => {
  const c = core();
  const { pages, host } = pagesHost();
  const noteId = await c.run("createPage", host, { title: "Notes" });
  const a = await c.run("addNote", host, noteId, { content: "first" });
  const z = await c.run("addNote", host, noteId, { content: "last" });
  const p1 = await c.run("addSheet", host, noteId, { after: a });                  // right after the first note
  await c.run("setSheetPaper", host, noteId, p1, { pattern: "grid" });
  const p2 = await c.run("addSheet", host, noteId, { after: p1 });                 // the page after that page
  assert.equal(await c.run("addSheet", host, noteId, { after: p1, once: true }), p2, "once: the page after it is there");
  const tree = c.pure("tree", pages.get(noteId).snapshot, noteId);
  assert.deepEqual(tree.map((n) => n.id), [a, p1, p2, z]);
  assert.equal(p2, c.pure("pageView", pages.get(noteId).snapshot, noteId).sheets[1].id);
  const second = tree[2].properties;
  assert.equal(second.sheet.pattern, "grid", "the paper of the page before it");
  assert.equal(second.collapsed, true, "folded: the page shows its drawings");
  const last = await c.run("addSheet", host, noteId);                              // after the last page
  assert.deepEqual(c.pure("tree", pages.get(noteId).snapshot, noteId).map((n) => n.id), [a, p1, p2, last, z]);
});

test("the editing bar's outline edits go where the browser's do, and undo and redo put them back", async () => {
  const c = core();
  const { pages, host } = pagesHost();
  const page = await c.run("createPage", host, { title: "Outline" });
  const one = await c.run("addNote", host, page, { content: "one" });
  const two = await c.run("addNote", host, page, { content: "two" });
  const three = await c.run("addNote", host, page, { content: "three" });
  // the outline as text: "one(two) three", a new empty note as "·"
  const shape = () => {
    const show = (list) => list.map((n) => (n.content || "·") + (n.children.length ? `(${show(n.children)})` : "")).join(" ");
    return show(c.pure("tree", pages.get(page).snapshot, page));
  };
  assert.equal(await c.run("indent", host, page, one), null, "the first block has nothing to go under");
  assert.equal(await c.run("outdent", host, page, one), null, "nor anything to come out of");
  assert.equal(await c.run("moveBlock", host, page, three, 1), null, "the last goes no lower");
  const fresh = c.pure("makeId");
  const steps = [
    [() => c.run("indent", host, page, two), "one(two) three", "under the block above, last among its children"],
    [() => c.run("addNoteAfter", host, page, one, fresh), "one(two) · three", "a new note right after the block, past its children"],
    [() => c.run("moveBlock", host, page, three, -1), "one(two) three ·", "one step up among its siblings"],
    [() => c.run("outdent", host, page, two), "one two three ·", "right after the block it was under"],
  ];
  const undos = [];
  for (const [edit, after, what] of steps) {
    undos.push(await edit());
    assert.equal(shape(), after, what);
  }
  // undone newest first, then redone, as an undo stack runs them
  const before = ["one two three", ...steps.map((s) => s[1])];
  const redos = [];
  for (let i = undos.length - 1; i >= 0; i--) {
    redos.push(await c.run("restore", host, page, undos[i]));
    assert.equal(shape(), before[i], `undo ${i + 1}`);
  }
  for (let i = 0; i < steps.length; i++) {
    await c.run("restore", host, page, redos[steps.length - 1 - i]);
    assert.equal(shape(), steps[i][1], `redo ${i + 1}`);
  }
  assert.ok(fresh in pages.get(page).snapshot, "the new note came back under its id");
});

test("the editing bar's text commands are the web editor's, as one edit", () => {
  const c = core();
  const edit = (text, e) => e && { text: text.slice(0, e.from) + e.insert + text.slice(e.to), sel: [e.anchor, e.head] };
  assert.deepEqual(edit("a word", c.pure("format", "bold", "a word", 2, 6)), { text: "a **word**", sel: [4, 8] });
  assert.deepEqual(c.pure("format", "bold", "word", 2, 2), { from: 2, to: 2, insert: "****", anchor: 4, head: 4 },
    "an empty pair at the caret, as a small edit");
  assert.deepEqual(edit("a **word**", c.pure("format", "bold", "a **word**", 4, 8)), { text: "a word", sel: [2, 6] }, "toggled off");
  assert.deepEqual(edit("it", c.pure("format", "italic", "it", 0, 2)), { text: "*it*", sel: [1, 3] });
  assert.deepEqual(edit("run x", c.pure("format", "code", "run x", 4, 5)), { text: "run `x`", sel: [5, 6] });
  assert.deepEqual(edit("run `x`", c.pure("format", "code", "run `x`", 5, 6)), { text: "run x", sel: [4, 5] }, "toggled off");
  assert.equal(c.pure("format", "bold", "`a b`", 2, 3), null, "no marks inside inline code");
  assert.deepEqual(edit("old", c.pure("format", "strike", "old", 0, 3)), { text: "~~old~~", sel: [2, 5] });
  assert.deepEqual(edit("site", c.pure("format", "link", "site", 0, 4)), { text: "[site]()", sel: [7, 7] }, "the caret in the (…) slot");
  assert.deepEqual(edit("a", c.pure("format", "math", "a", 1, 1)), { text: "a$x$", sel: [2, 3] });
  assert.equal(c.pure("format", "bold", "$x+y$", 2, 2), null, "no marks inside math");
  assert.equal(c.pure("format", "nope", "a", 0, 0), null);
  assert.deepEqual(edit("# Title", c.pure("insert", "h2", "# Title", 7)), { text: "## Title", sel: [8, 8] }, "a heading re-levelled");
  assert.deepEqual(c.pure("insert", "h2", "# Title", 7), { from: 1, to: 1, insert: "#", anchor: 8, head: 8 });
  assert.deepEqual(edit("abc", c.pure("insert", "divider", "abc", 3)), { text: "abc\n---\n", sel: [8, 8] }, "on a line of its own");
  assert.deepEqual(edit("", c.pure("insert", "todo", "", 0)), { text: "- [ ] ", sel: [6, 6] });
  assert.equal(c.pure("insert", "page", "", 0), null, "only the insertions that change the text alone");
});

test("the library names folders and labels from their trees", () => {
  const c = core();
  const node = (parent, content, position = "a0") => ({ parent, position, content, props: {} });
  const trees = {
    folders: { folders: node(null, "", "a2"), f1: node("folders", "Physics"), f2: node("f1", "QEC / codes") },
    labels: { labels: node(null, "", "a3"), l1: node("labels", "to read") },
  };
  const roots = [
    { id: "p1", content: "Surface codes", position: "a0", props: { folders: ["f2", "gone"], labels: ["l1"] } },
    { id: "folders", content: "", position: "a2", props: {} },
  ];
  const rows = c.pure("libraryRows", roots, trees);
  assert.deepEqual(rows.map((r) => r.id), ["p1"], "the trees' roots are no pages");
  assert.deepEqual(rows[0].folders, ["Physics / QEC / codes"], "a path, never split on / or ,; a dangling id names nothing");
  assert.deepEqual(rows[0].labels, ["to read"]);
  assert.deepEqual(c.pure("libraryRows", roots)[0].folders, [], "before a round brings the trees");
});
