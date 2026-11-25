// node --test tests/inkSync.test.mjs (from frontend/) — handwriting saved
// while the writing goes on, through the real draft store
// (ink/inkStore.js) and the real save (ink/inkSaver.js createInkSaver),
// against a fake server that does what the block PUT does
// (backend/gamma/ops.py _Batch.merge_ink): the writer's file stored as sent
// when the stored file is its base or the upload itself, else merged with
// ink.js mergeInk (the server's algorithm: tests/shared/inkmerge.json pins
// both), and a batch sent again answered from its log (ops._replayed). The
// report these guard: "while writing, strokes written earlier suddenly
// disappear, and erased strokes reappear" (docs/dev/handwriting.md
// "Fences", "Client").
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mock, test } from "node:test";
import { installInkShims } from "./perf/fixtures.mjs";

const served = installInkShims();
const ink = await import("../src/ink/ink.js");
const inkStore = await import("../src/ink/inkStore.js");
const { createInkSaver } = await import("../src/ink/inkSaver.js");

const PAGE = "page";
const stroke = (id, x = 10) => ink.encodeStroke({ id, ch: "xyt", pen: false, samples: [{ x, y: 10, t: 0 }, { x: x + 5, y: 12, t: 8 }] });
let made = 0;
// a drawing of these strokes, each id always the same stroke
const strokes = new Map();
const drawing = (...ids) => ({ ...ink.newInk(1, 612, 792),
  strokes: ids.map((id) => strokes.get(id) || strokes.set(id, stroke(id, 10 + 20 * made++)).get(id)) });
const ids = (d) => (d?.strokes || []).map((s) => s.id);
const nameOf = (bytes) => `/api/uploads/${createHash("sha256").update(bytes).digest("hex").slice(0, 24)}.ink`;
const store = (d) => { const bytes = ink.serializeInk(d); served.set(nameOf(bytes), bytes); return nameOf(bytes); };
const fileAt = (url) => (url ? ink.loadedInk(JSON.parse(served.get(url))) : null);
const settle = () => new Promise((resolve) => setImmediate(resolve));
const httpError = (status) => Object.assign(new Error(`HTTP ${status}`), { status });

// The server and the page around one saver. `blocks`: group id → the file
// it names. Requests are answered at once, or held (`hold()`) until
// `next()` answers the oldest: `next({ lose: true })` applies it and loses
// its answer (a network error on the way back). `echo()` applies the
// server's batches to the page's tree in order, as the page socket does,
// and the tree's applied seq follows (collabSession.js applied).
function page(blocks) {
  const stored = new Map(Object.entries(blocks));
  const batches = [];   // {batch, id, props, seq}
  let applied = 0, holding = false;
  const queue = [], sent = [], statuses = [];
  const tree = { current: Object.entries(blocks).map(([id, url]) => ({ id, content: "", children: [], properties: { ink_url: url } })) };
  function apply(url, init) {
    if (init.method === "POST") { served.set(nameOf(init.body), init.body); return { url: nameOf(init.body) }; }
    const id = url.split("/").pop();
    if (!stored.has(id)) throw httpError(404);
    if (init.method === "DELETE") { stored.delete(id); batches.push({ id, gone: true, seq: batches.length + 1 }); return {}; }
    const { properties, base_properties: baseProps, base_ids: baseIds, batch } = JSON.parse(init.body);
    const hit = batch && batches.find((b) => b.batch === batch && b.id === id);
    if (hit) return { properties: hit.props, seq: hit.seq, page_id: PAGE };
    const now = stored.get(id), base = baseProps?.ink_url ?? "";
    let next = properties.ink_url;
    if (now !== base && now !== next) {
      const merged = ink.mergeInk(base ? fileAt(base) : null, fileAt(next), now ? fileAt(now) : { ...fileAt(next), strokes: [] }, base ? null : baseIds);
      next = store(merged.ink);
    }
    stored.set(id, next);
    const props = { ...properties, ink_url: next };
    batches.push({ batch, id, props, seq: batches.length + 1 });
    return { properties: props, seq: batches.length, page_id: PAGE };
  }
  const apiJson = (url, init = {}) => new Promise((resolve, reject) => {
    const req = { line: `${init.method} ${url}`, url, init, resolve, reject };
    // the save's timeout gives a request up: an error with no status, as fetch's AbortError is
    init.signal?.addEventListener("abort", () => {
      const at = queue.indexOf(req);
      if (at >= 0) queue.splice(at, 1);
      reject(new DOMException("aborted", "AbortError"));
    });
    sent.push(req);
    if (holding) queue.push(req);
    else { try { resolve(apply(url, init)); } catch (err) { reject(err); } }
  });
  const saver = createInkSaver({ blocksRef: tree, setStatus: (s) => statuses.push(s), liveSaved: () => {}, inkStore, apiJson, API: "/api",
    keepaliveRoom: () => 0, hidden: () => false });
  inkStore.setTreeApplied(() => ({ pageId: PAGE, seq: applied }));
  return {
    saver, tree, sent, statuses,
    puts: () => sent.filter((r) => r.init.method === "PUT").map((r) => JSON.parse(r.init.body)),
    hold() { holding = true; },
    flow() { holding = false; while (queue.length) this.next(); },
    next({ lose = false } = {}) {
      const req = queue.shift();
      try {
        const out = apply(req.url, req.init);
        if (lose) req.reject(new TypeError("Failed to fetch")); else req.resolve(out);
      } catch (err) { req.reject(err); }
    },
    echo() {
      for (const b of batches.slice(applied)) {
        tree.current = b.gone ? tree.current.filter((x) => x.id !== b.id)
          : tree.current.map((x) => (x.id === b.id ? { ...x, properties: { ...x.properties, ...b.props } } : x));
      }
      applied = batches.length;
    },
    stored: (id) => ids(fileAt(stored.get(id))),
    block: (id) => tree.current.find((x) => x.id === id),
  };
}
// an edit of group `id`, as useInkDocument.js applyInk makes it
const edit = (env, id, d) => inkStore.setDraft(id, d, env.block(id)?.properties.ink_url || "");
const withMockTimers = async (fn) => {
  mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_000_000 });
  try { await fn(); } finally { mock.timers.reset(); inkStore.setTreeApplied(null); }
};
// group `id` with drawing `d` stored and loaded here, its tree naming it
async function group(id, d) {
  const url = store(d);
  await inkStore.loadInk(url);
  return { [id]: url };
}

test("erasing or undoing while the group's save is out: the second save waits for the first's answer and goes on its base, so what was erased stays erased", () => withMockTimers(async () => {
  // a stroke drawn, saved, and erased while that save is out
  const env = page(await group("g1", drawing("a")));
  env.hold();
  edit(env, "g1", drawing("a", "x"));
  const out = env.saver.flushInk();
  edit(env, "g1", drawing("a"));         // x erased while the save is out
  env.saver.flushInk();                  // the timer's flush, 700 ms later: it waits
  assert.equal(env.sent.filter((r) => r.init.method === "POST").length, 1, "one save of the group on its way");
  env.flow();
  await out;
  await settle();
  env.flow();
  await settle();
  assert.deepEqual(env.stored("g1"), ["a"], "x stays erased on the server");
  env.echo();
  assert.deepEqual(ids(inkStore.inkShown(env.block("g1"))), ["a"], "and on screen");

  // an erasure undone while its save is out
  const undo = page(await group("g2", drawing("a", "y")));
  undo.hold();
  edit(undo, "g2", drawing("a"));
  const gone = undo.saver.flushInk();
  edit(undo, "g2", drawing("a", "y"));   // the undo
  undo.saver.flushInk();
  undo.flow();
  await gone;
  await settle();
  undo.flow();
  await settle();
  assert.deepEqual(undo.stored("g2"), ["a", "y"], "the undo stands");
  assert.deepEqual(undo.statuses, [], "and nobody else being here, no pill about someone changing it");
}));

test("the save's answer before its echo: the group keeps showing the drawing just saved, a stroke then lands on it, and a later change by someone drops it", () => withMockTimers(async () => {
  const env = page(await group("g3", drawing("a")));
  edit(env, "g3", drawing("a", "b"));
  await env.saver.flushInk();
  // answered; the tree still names the old file (its echo not here yet)
  assert.deepEqual(ids(inkStore.inkShown(env.block("g3"))), ["a", "b"], "not the old file");
  edit(env, "g3", drawing("a", "b", "c"));
  await env.saver.flushInk();
  assert.deepEqual(env.stored("g3"), ["a", "b", "c"], "the stroke drawn in the gap went onto the saved drawing");
  env.echo();
  assert.deepEqual(ids(inkStore.inkShown(env.block("g3"))), ["a", "b", "c"]);
  // someone else's save, after ours in the page's order: shown at once
  const theirs = store(drawing("a", "b", "c", "p"));
  env.tree.current = env.tree.current.map((x) => (x.id === "g3" ? { ...x, properties: { ink_url: theirs } } : x));
  await inkStore.loadInk(theirs);
  assert.deepEqual(ids(inkStore.inkShown(env.block("g3"))), ["a", "b", "c", "p"]);

  // a new group: shown from its first save's answer on, though the tree names no file yet
  const fresh = page({ g4: "" });
  edit(fresh, "g4", drawing("n"));
  await fresh.saver.flushInk();
  assert.deepEqual(ids(inkStore.inkShown(fresh.block("g4"))), ["n"]);
}));

test("a PUT that landed with its answer lost: the retry sends it again under its batch, answered from the log, and the newer drawing goes on what it stored", () => withMockTimers(async () => {
  const env = page(await group("g5", drawing("a")));
  env.hold();
  edit(env, "g5", drawing("a", "x"));
  const out = env.saver.flushInk();
  env.next();                            // the upload
  await settle();
  env.next({ lose: true });              // the PUT lands; its answer does not
  await out;
  assert.deepEqual(env.stored("g5"), ["a", "x"]);
  edit(env, "g5", drawing("a"));         // x erased before the retry
  env.flow();
  mock.timers.tick(2000);                // the retry
  await settle();
  await settle();
  assert.deepEqual(env.stored("g5"), ["a"], "x stays erased: the newest drawing went on the landed save's file");
  const puts = env.sent.filter((r) => r.init.method === "PUT");
  assert.equal(puts[1].init.body, puts[0].init.body, "the lost PUT went again as it was");
}));

test("a group's file that fails to load is fetched again later, and the group keeps showing what it showed", () => withMockTimers(async () => {
  const shown = drawing("s");
  const env = page(await group("g6", shown));
  assert.deepEqual(ids(inkStore.inkShown(env.block("g6"))), ["s"]);
  // someone saves; the file they name is not there yet (their upload is still on its way)
  const later = drawing("s", "t");
  const bytes = ink.serializeInk(later), url = nameOf(bytes);
  env.tree.current = [{ ...env.block("g6"), properties: { ink_url: url } }];
  inkStore.inkShown(env.block("g6"));
  await settle();
  assert.equal(inkStore.fileState(url), "failed");
  assert.deepEqual(ids(inkStore.inkShown(env.block("g6"))), ["s"], "the drawing it showed, not nothing");
  served.set(url, bytes);                // the upload lands
  mock.timers.tick(2000);
  inkStore.inkShown(env.block("g6"));
  await settle();
  assert.deepEqual(ids(inkStore.inkShown(env.block("g6"))), ["s", "t"], "fetched again");
}));

test("a group erased empty while an earlier save of it is out, then the page changes: what follows decides from the block as its page showed it, and a captioned group is not deleted", () => withMockTimers(async () => {
  const env = page(await group("g7", drawing("a")));
  env.tree.current = [{ ...env.block("g7"), content: "A caption" }];
  env.hold();
  edit(env, "g7", drawing("a", "b"));
  const out = env.saver.flushInk();
  edit(env, "g7", { ...drawing("a"), strokes: [] });   // erased empty
  env.saver.flushInk();                                // it waits behind the save on its way
  env.tree.current = [];                               // another page opens
  env.flow();
  await out;
  await settle();
  env.flow();
  await settle();
  assert.equal(env.sent.some((r) => r.init.method === "DELETE"), false, "no DELETE: the block holds a caption");
  assert.deepEqual(env.stored("g7"), [], "saved as an empty drawing");
}));

test("an empty group's DELETE on its way closes the group to strokes while its block is on the page, and a DELETE whose answer was lost counts as landed once the block has gone", () => withMockTimers(async () => {
  const env = page(await group("g8", drawing("a")));
  env.hold();
  edit(env, "g8", { ...drawing("a"), strokes: [] });
  const out = env.saver.flushInk();
  assert.equal(inkStore.isDeleting("g8"), true);
  env.next({ lose: true });   // it lands; its answer does not
  await out;
  assert.equal(inkStore.isDeleting("g8"), true, "not known not to have landed: still closed");
  env.echo();                 // the block goes
  env.flow();
  mock.timers.tick(2000);     // the retry: a 404 for a block the page no longer shows
  await settle();
  assert.equal(env.sent.filter((r) => r.init.method === "DELETE").length, 2);
  assert.equal(env.saver.pending(), false, "no more retries");
  inkStore.dropDraft("g8");   // an undo puts the group back fresh (useInkDocument.js applyInk)
  assert.equal(inkStore.isDeleting("g8"), false);
}));

test("going to the background while the group pays a PUT it owes sends that PUT again, never the newest drawing on the base from before it: what was erased stays erased", () => withMockTimers(async () => {
  const env = page(await group("h1", drawing("a")));
  env.hold();
  edit(env, "h1", drawing("a", "x"));
  const out = env.saver.flushInk();
  env.next();                            // the upload
  await settle();
  env.next({ lose: true });              // the PUT lands, its answer does not: owed
  await out;
  edit(env, "h1", drawing("a"));         // x erased
  mock.timers.tick(2000);                // the retry: the owed PUT goes again first (held)
  await settle();
  env.saver.flushInk({ keepalive: true });   // the tab goes to the background while it is out
  const bodies = env.puts();
  assert.ok(bodies.every((b) => b.batch === bodies[0].batch && b.properties.ink_url === bodies[0].properties.ink_url),
    "only the owed PUT, again: no newer drawing on the old base");
  env.flow();
  await settle();
  env.flow();
  await settle();
  assert.deepEqual(env.stored("h1"), ["a"], "x stays erased");
}));

test("a PUT that never answers frees its group after the save's timeout, and the drawing then saves through the owed PUT", () => withMockTimers(async () => {
  const env = page(await group("h2", drawing("a")));
  env.hold();
  edit(env, "h2", drawing("a", "b"));
  const out = env.saver.flushInk();
  env.next();                            // the upload; the PUT never answers (a half-open connection)
  await settle();
  mock.timers.tick(60000);               // SAVE_TIMEOUT_MS
  await out;
  assert.match(env.statuses.at(-1), /not saved yet/);
  env.flow();
  mock.timers.tick(2000);                // the retry
  await settle();
  await settle();
  assert.deepEqual(env.stored("h2"), ["a", "b"]);
  assert.equal(inkStore.draft("h2").dirty, false);
}));

test("after its echo, a tree naming no file for a moment (a refetch laying the group's insert over it) keeps the saved drawing on screen", () => withMockTimers(async () => {
  const env = page({ h3: "" });
  edit(env, "h3", drawing("n", "m"));
  await env.saver.flushInk();
  env.echo();
  env.tree.current = [{ ...env.block("h3"), properties: { ink_url: "" } }];
  assert.deepEqual(ids(inkStore.inkShown(env.block("h3"))), ["n", "m"]);
}));

test("a flush resolves once a group it left behind a save on its way is saved too (Transcribe reads the group after it)", () => withMockTimers(async () => {
  const env = page(await group("h4", drawing("a")));
  env.hold();
  edit(env, "h4", drawing("a", "b"));
  env.saver.flushInk();
  edit(env, "h4", drawing("a", "b", "c"));
  let done = false;
  const flushed = env.saver.flushInk().then(() => { done = true; });
  await settle();
  assert.equal(done, false, "the newer drawing is not saved yet");
  env.flow();
  await settle();
  env.flow();
  await flushed;
  assert.equal(inkStore.draft("h4").dirty, false);
  assert.deepEqual(env.stored("h4"), ["a", "b", "c"]);
}));

test("a file answered 404 is fetched again twice and then left; one answered 403 is not fetched again", () => withMockTimers(async () => {
  const missing = nameOf("never stored");
  await inkStore.loadInk(missing);
  assert.equal(inkStore.fileState(missing), "failed");
  for (const wait of [2000, 4000]) {
    mock.timers.tick(wait);
    assert.equal(inkStore.fileState(missing), "unknown", "forgotten, so the next ask fetches it again");
    await inkStore.loadInk(missing);
  }
  assert.equal(inkStore.fileState(missing), "gone", "three 404s");
  mock.timers.tick(60000);
  assert.equal(inkStore.fileState(missing), "gone");
  const forbidden = nameOf("not for this account"), fetchAll = globalThis.fetch;
  globalThis.fetch = async (url, init) => (String(url).endsWith(forbidden.split("/").pop()) ? new Response("{}", { status: 403 }) : fetchAll(url, init));
  try {
    await inkStore.loadInk(forbidden);
    assert.equal(inkStore.fileState(forbidden), "gone", "a 403 is not asked again");
  } finally { globalThis.fetch = fetchAll; }
}));
