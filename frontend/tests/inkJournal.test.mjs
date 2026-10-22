// node --test tests/inkJournal.test.mjs (from frontend/) — the draft journal:
// what an entry brings back (ink/inkJournal.js restoredInk), and inkStore
// writing, deleting and restoring entries through IndexedDB — an in-memory
// one here (docs/dev/handwriting.md "Client", the journal).
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { encodeStroke, newInk, restyleStrokes, serializeInk } from "../src/ink/ink.js";
import { JOURNAL_DAYS, journalKey, keyBlock, restoredInk } from "../src/ink/inkJournal.js";

const stroke = (id, x = 10) => encodeStroke({ id, ch: "xyt", pen: false, samples: [{ x, y: 10, t: 0 }, { x: x + 5, y: 12, t: 8 }] });
const drawing = (...ids) => ({ ...newInk(1, 612, 792), strokes: ids.map((id, i) => stroke(id, 10 + 20 * i)) });
const entry = (ink, over = {}) => ({ bytes: serializeInk(ink), base: "/api/uploads/a.ink", baseIds: ["a1"], at: Date.now(), tab: "gone", ...over });
// strokes that keep their place whatever drawing they are in
const A = stroke("AAAAAAAA", 10), B = stroke("BBBBBBBB", 50), S = stroke("SSSSSSSS", 90), T = stroke("TTTTTTTT", 130);
const file = (...strokes) => ({ ...newInk(1, 612, 792), strokes });
const ids = (ink) => ink?.strokes.map((s) => s.id);

test("restoredInk: the entry itself when the block still names its base; merged when someone saved; nothing when the base is gone or it is old", () => {
  const a = drawing("a1"), b = drawing("a1", "x1"), mine = drawing("a1", "y1");
  // the block still names the file the draft was edited from
  const same = restoredInk(entry(mine), { url: "/api/uploads/a.ink", current: a });
  assert.deepEqual(same.ink.strokes.map((s) => s.id), ["a1", "y1"]);
  assert.deepEqual(same.ids, ["y1"]);
  // someone saved meanwhile: three ways, so their stroke stays and ours joins
  const merged = restoredInk(entry(mine), { url: "/api/uploads/b.ink", current: b, base: a });
  assert.deepEqual(merged.ink.strokes.map((s) => s.id), ["a1", "x1", "y1"]);
  assert.deepEqual(merged.ids, ["y1"]);
  // they erased a stroke our entry still has: it does not come back
  const erased = restoredInk(entry(drawing("a1", "y1")), { url: "/api/uploads/c.ink", current: drawing(), base: a });
  assert.deepEqual(erased.ink.strokes.map((s) => s.id), ["y1"]);
  // the base is gone (no file to merge against), the entry is too old, or it holds what the block has
  assert.equal(restoredInk(entry(mine), { url: "/api/uploads/b.ink", current: b, base: null }), null);
  assert.equal(restoredInk(entry(mine, { at: Date.now() - (JOURNAL_DAYS + 1) * 86400000 }), { url: "/api/uploads/a.ink", current: a }), null);
  assert.equal(restoredInk(entry(a), { url: "/api/uploads/a.ink", current: a }), null);
  assert.equal(restoredInk({ ...entry(mine), bytes: "{nope" }, { url: "/api/uploads/a.ink", current: a }), null);
  // a block never uploaded (no file yet) takes the entry whole
  const fresh = restoredInk(entry(mine, { base: "" }), { url: "", current: null });
  assert.deepEqual(fresh.ids, ["a1", "y1"]);
  // and so does a block naming the entry's own upload, which the server lacks
  const lost = restoredInk(entry(mine), { url: "/api/uploads/m.ink", current: null, lost: true });
  assert.deepEqual(lost.ink.strokes.map((s) => s.id), ["a1", "y1"]);
  assert.deepEqual(lost.ids, ["a1", "y1"]);
});

test("restoredInk never brings back a stroke the server deleted: not one a PUT that went unanswered had landed, not one the entry only edited", () => {
  const F0 = file(A), F1 = file(A, S);   // the draft added S; its upload F1 and the block's PUT went at pagehide, unanswered
  const F2 = file(A);                    // the PUT had landed, and someone erased S since
  // counted from the draft's own base, S looks like the entry's addition
  assert.deepEqual(ids(restoredInk(entry(F1, { base: "/api/uploads/f0.ink" }), { url: "/api/uploads/f2.ink", current: F2, base: F0 }).ink), [A.id, S.id],
    "(what the entry's base alone says)");
  // counted from the upload its tab sent the PUT for, which the server has: nothing to bring
  const sent = entry(F1, { base: "/api/uploads/f0.ink", sent: ["/api/uploads/f1.ink"] });
  assert.equal(restoredInk(sent, { url: "/api/uploads/f2.ink", current: F2, base: F1, baseUrl: "/api/uploads/f1.ink" }), null);
  // drawn on after that flush: only the later stroke comes back
  const later = entry(file(A, S, T), { base: "/api/uploads/f0.ink", sent: ["/api/uploads/f1.ink"] });
  const back = restoredInk(later, { url: "/api/uploads/f2.ink", current: F2, base: F1, baseUrl: "/api/uploads/f1.ink" });
  assert.deepEqual([ids(back.ink), back.ids], [[A.id, T.id], [T.id]]);
  // the block still names that upload: the entry as it is
  assert.deepEqual(ids(restoredInk(later, { url: "/api/uploads/f1.ink", current: F1, baseUrl: "/api/uploads/f1.ink" }).ink), [A.id, S.id, T.id]);
  // the entry recoloured A, someone erased A: A stays erased, though an edit beats a delete in a live merge
  const G0 = file(A, B), recoloured = restyleStrokes(G0, [A.id], { color: "#ff0000" });
  assert.equal(restoredInk(entry(recoloured, { base: "/api/uploads/g0.ink" }), { url: "/api/uploads/g2.ink", current: file(B), base: G0 }), null);
  // a piece the entry cut from a stroke erased since goes with it
  const piece = { ...stroke("PPPPPPPP", 10), from: A.id };
  assert.equal(restoredInk(entry(file(piece, B), { base: "/api/uploads/g0.ink" }), { url: "/api/uploads/g2.ink", current: file(B), base: G0 }), null);
  // while the recolour of a stroke still there comes back, beside what was added meanwhile
  const kept = restoredInk(entry(recoloured, { base: "/api/uploads/g0.ink" }), { url: "/api/uploads/g3.ink", current: file(A, B, T), base: G0 });
  assert.deepEqual([ids(kept.ink), kept.ink.strokes[0].color, kept.ids], [[A.id, B.id, T.id], "#ff0000", []]);
});

test("journal keys: one per workspace, block and tab", () => {
  assert.equal(journalKey("ws1", "blk", "t1"), "ws1/blk/t1");
  assert.equal(journalKey("", "blk", "t1"), "/blk/t1");
  assert.equal(keyBlock("blk/t1"), "blk");
  assert.equal(keyBlock("blk"), "blk", "a key without a tab names its block");
});

// --- inkStore through an in-memory IndexedDB ---------------------------------

// Key ranges as IDBKeyRange.bound makes them ({lower, upper}); a request
// without one reads every key. `reads` holds the keys each read returned.
function memoryIndexedDB() {
  const dbs = new Map();
  const reads = [];
  const later = (fn) => setTimeout(fn, 0);
  const inRange = (key, range) => !range || (key >= range.lower && key <= range.upper);
  return {
    dbs,
    reads,
    open(name) {
      const req = {};
      later(() => {
        const fresh = !dbs.has(name);
        if (fresh) dbs.set(name, new Map());
        const db = dbs.get(name);
        req.result = {
          createObjectStore: (store) => db.set(store, new Map()),
          transaction(store) {
            const data = db.get(store), runs = [], tx = {};
            const ask = (fn) => { const r = {}; runs.push(() => { r.result = fn(); }); return r; };
            const keys = (range) => [...data.keys()].sort().filter((k) => inRange(k, range));
            tx.objectStore = () => ({
              put: (value, key) => ask(() => { data.set(key, structuredClone(value)); return key; }),
              delete: (key) => ask(() => { data.delete(key); }),
              getAllKeys: (range) => ask(() => { const out = keys(range); reads.push(out); return out; }),
              getAll: (range) => ask(() => keys(range).map((k) => structuredClone(data.get(k)))),
            });
            later(() => { for (const run of runs) run(); tx.oncomplete?.(); });
            return tx;
          },
        };
        if (fresh) req.onupgradeneeded?.();
        req.onsuccess?.();
      });
      return req;
    },
  };
}

test("inkStore journals a dirty draft until its upload lands, and a page open restores what a closed tab left", async () => {
  // shared/lib/utils.js (behind inkStore) imports without extensions and reads `window` as it loads
  register("data:text/javascript," + encodeURIComponent(`
    export async function resolve(specifier, context, next) {
      try { return await next(specifier, context); } catch (err) {
        if (err?.code === "ERR_MODULE_NOT_FOUND" && /^\\.\\.?\\//.test(specifier) && !/\\.[a-z]+$/.test(specifier)) return next(specifier + ".js", context);
        throw err;
      }
    }`));
  const files = new Map();
  const gates = new Map();   // path → a promise its fetch waits for
  const asked = new Set();   // the paths fetched
  globalThis.window = globalThis;
  globalThis.location = new URL("http://gamma.test/");
  globalThis.dispatchEvent = () => true;   // utils logs every API answer as a window event
  globalThis.fetch = async (url) => {
    const path = new URL(String(url), location.href).pathname;
    asked.add(path);
    if (gates.has(path)) await gates.get(path);
    const body = files.get(path);
    return body == null ? new Response(JSON.stringify({ detail: "gone" }), { status: 404 }) : new Response(body, { status: 200 });
  };
  const idb = memoryIndexedDB();
  globalThis.indexedDB = idb;
  globalThis.IDBKeyRange = { bound: (lower, upper) => ({ lower, upper }) };
  // a tab that is still open holds its lock; the others are gone
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { locks: {
    request: () => new Promise(() => {}),
    query: async () => ({ held: [{ name: "gamma-ink-tab-alive" }] }),
  } } });
  const utils = await import("../src/shared/lib/utils.js");
  utils.setCurrentWorkspace("ws1");
  const store = await import("../src/ink/inkStore.js");
  const until = async (ok, what) => {
    for (let i = 0; i < 200; i++) { if (await ok()) return; await new Promise((r) => setTimeout(r, 5)); }
    assert.fail(`timed out: ${what}`);
  };
  const entries = () => idb.dbs.get("gamma-ink-journal")?.get("drafts") || new Map();
  const restored = [];
  store.onInkRestored((r) => restored.push(r));
  const nameOf = async (body) => {
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body)));
    return `/api/uploads/${Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("").slice(0, 24)}.ink`;
  };
  const TAB = store.journalTab();

  // what closed tabs (and an open one, and this one) left behind, before this tab shows anything
  const a = drawing("a1"), b = drawing("a1", "x1");
  files.set("/api/uploads/a.ink", serializeInk(a));
  files.set("/api/uploads/b.ink", serializeInk(b));
  const old = Date.now() - (JOURNAL_DAYS + 1) * 86400000;
  // a pagehide PUT that landed beside an upload that did not: the block names the entry's own bytes' upload
  const lostEntry = entry(drawing("a1", "w1"));
  const lostUrl = await nameOf(lostEntry.bytes);
  // a flush that landed unanswered (its PUT sent, its upload there), and someone erased its stroke since
  const F0 = file(A), F1 = file(A, S), F2 = file(A, B);
  for (const [name, ink] of [["f0", F0], ["f1", F1], ["f2", F2]]) files.set(`/api/uploads/${name}.ink`, serializeInk(ink));
  const landed = entry(F1, { base: "/api/uploads/f0.ink", baseIds: [A.id], sent: ["/api/uploads/f1.ink"] });
  // an upload that landed with no PUT after it (the tab went between the two): the block still names the base
  const unsent = entry(file(A, T), { base: "/api/uploads/f0.ink", baseIds: [A.id] });
  files.set(await nameOf(unsent.bytes), unsent.bytes);
  // the block's file changes while its restore runs: it is restored against the new one
  files.set("/api/uploads/slow.ink", serializeInk(F0));
  let release;
  gates.set("/api/uploads/slow.ink", new Promise((r) => { release = r; }));
  const seed = {
    "ws1/same/gone": entry(drawing("a1", "y1")),                                      // the block still names a.ink
    "ws1/moved/gone": entry(drawing("a1", "y1")),                                     // the block names b.ink now
    "ws1/gone/gone": entry(drawing("a1", "y1"), { base: "/api/uploads/missing.ink" }), // its base is gone
    "ws1/old/gone": entry(drawing("a1", "y1"), { at: old }),
    "ws1/never/gone": entry(drawing("a1", "y1"), { at: old }),                       // old, and its block is never shown
    "ws1/open/alive": entry(drawing("a1", "y1"), { tab: "alive" }),                   // its tab is still open
    [`ws1/mine/${TAB}`]: entry(drawing("a1", "y1"), { tab: TAB }),                   // this tab's own
    "ws2/same/gone": entry(drawing("a1", "z1")),                                      // another workspace's
    "ws1/lost/gone": lostEntry,                                                       // the block names its upload, which never landed
    "ws1/stray/gone": entry(drawing("a1", "v1")),                                     // the block names a missing file it is not
    "ws1/landed/gone": landed,
    "ws1/unsent/gone": unsent,
    "ws1/twice/gone": entry(file(A, S), { base: "/api/uploads/f0.ink" }),            // two closed tabs drew in one group
    "ws1/twice/gone2": entry(file(A, T), { base: "/api/uploads/f0.ink", tab: "gone2" }),
    "ws1/race/gone": entry(file(A, S), { base: "/api/uploads/slow.ink" }),
  };
  await new Promise((r) => { const req = idb.open("gamma-ink-journal"); req.onupgradeneeded = () => req.result.createObjectStore("drafts"); req.onsuccess = r; });
  for (const [key, value] of Object.entries(seed)) entries().set(key, value);

  // a draft turning dirty is written under this tab's key (journalAll: now, as on pagehide), its upload landing deletes it
  const draft = drawing("d1");
  store.setDraft("drawn", draft, "");
  await store.journalAll();
  const written = entries().get(`ws1/drawn/${TAB}`);
  assert.deepEqual([written.bytes, written.base, written.tab, written.sent], [serializeInk(draft), "", TAB, []]);
  // a PUT about to go is written at once; an error answer takes it off, the answer deletes the entry
  await store.markSending("drawn", "/api/uploads/d.ink");
  assert.deepEqual(entries().get(`ws1/drawn/${TAB}`).sent, ["/api/uploads/d.ink"]);
  store.markUnsent("drawn", "/api/uploads/d.ink");
  await until(() => entries().get(`ws1/drawn/${TAB}`)?.sent?.length === 0, "an error answer takes the PUT off the entry");
  await store.markSending("drawn", "/api/uploads/d.ink");
  store.markSaved("drawn", draft, "/api/uploads/d.ink");
  await until(() => !entries().has(`ws1/drawn/${TAB}`), "the entry deleted once the upload landed");

  // a page open: the blocks are asked for, the journal is read, and asked again
  const race1 = file(A, B), race2 = file(A, B, T);
  files.set("/api/uploads/race1.ink", serializeInk(race1));
  files.set("/api/uploads/race2.ink", serializeInk(race2));
  const blocks = {
    same: { id: "same", properties: { ink_url: "/api/uploads/a.ink" } },
    moved: { id: "moved", properties: { ink_url: "/api/uploads/b.ink" } },
    gone: { id: "gone", properties: { ink_url: "/api/uploads/b.ink" } },
    old: { id: "old", properties: { ink_url: "/api/uploads/a.ink" } },
    open: { id: "open", properties: { ink_url: "/api/uploads/a.ink" } },
    mine: { id: "mine", properties: { ink_url: "/api/uploads/a.ink" } },
    lost: { id: "lost", properties: { ink_url: lostUrl } },
    stray: { id: "stray", properties: { ink_url: "/api/uploads/stray.ink" } },
    landed: { id: "landed", properties: { ink_url: "/api/uploads/f2.ink" } },
    unsent: { id: "unsent", properties: { ink_url: "/api/uploads/f0.ink" } },
    twice: { id: "twice", properties: { ink_url: "/api/uploads/f0.ink" } },
    race: { id: "race", properties: { ink_url: "/api/uploads/race1.ink" } },
  };
  const show = () => Object.values(blocks).forEach((blk) => store.inkFor(blk));
  show();
  // someone saves the race block while its restore waits for the entry's base
  await until(() => { show(); return asked.has("/api/uploads/slow.ink"); }, "the race block's restore under way");
  blocks.race = { id: "race", properties: { ink_url: "/api/uploads/race2.ink" } };
  show();
  release();
  await until(() => { show(); return restored.length >= 6 && !entries().has("ws1/gone/gone") && !entries().has("ws1/landed/gone"); }, "the restores");
  await new Promise((r) => setTimeout(r, 50));
  const draftIds = (id) => ids(store.draft(id)?.ink);
  assert.deepEqual(draftIds("same"), ["a1", "y1"], "restored as it was");
  assert.deepEqual(draftIds("moved"), ["a1", "x1", "y1"], "merged onto the file someone saved meanwhile");
  assert.deepEqual(draftIds("lost"), ["a1", "w1"], "the lost upload restored whole");
  assert.deepEqual(draftIds("unsent"), [A.id, T.id], "an upload no PUT followed: the block never named it, so it is restored");
  assert.equal(store.draft("landed"), null, "a PUT that landed unanswered: its stroke someone erased since stays erased");
  assert.deepEqual(draftIds("twice"), [A.id, S.id], "one closed tab's entry a visit");
  assert.deepEqual(draftIds("race"), [A.id, B.id, T.id, S.id], "restored again against the file saved while it ran");
  assert.deepEqual(restored.map((r) => [r.id, r.ids]).sort(),
    [["lost", ["a1", "w1"]], ["moved", ["y1"]], ["race", [S.id]], ["same", ["y1"]], ["twice", [S.id]], ["unsent", [T.id]]]);
  assert.deepEqual(restored.find((r) => r.id === "lost").prior.strokes, [], "Discard's target: the drawing the missing file showed, nothing");
  assert.deepEqual(ids(restored.find((r) => r.id === "moved").prior), ["a1", "x1"], "Discard's target: the file it was laid over");
  assert.deepEqual(ids(restored.find((r) => r.id === "race").prior), [A.id, B.id, T.id], "the file the block names now");
  // restored drafts save like edits: dirty, based on the file the block names now
  const dirty = new Map(store.dirtyDrafts().map((d) => [d.id, d]));
  assert.deepEqual([dirty.get("same").base, dirty.get("same").baseIds], ["/api/uploads/a.ink", ["a1"]]);
  assert.deepEqual([dirty.get("moved").base, dirty.get("moved").baseIds], ["/api/uploads/b.ink", ["a1", "x1"]]);
  assert.deepEqual([dirty.get("lost").base, dirty.get("lost").baseIds], [lostUrl, []], "its save uploads the file the block names");
  // the rest: dropped (base gone, too old, landed), kept for later (a tab still open, this tab's own, a second
  // closed tab's), another workspace's untouched and never read; a restored entry is this tab's now
  for (const id of ["gone", "old", "open", "mine", "stray"]) assert.equal(store.draft(id), null, `${id}: nothing restored`);
  assert.deepEqual([...entries().keys()].sort(), [
    `ws1/lost/${TAB}`, `ws1/mine/${TAB}`, `ws1/moved/${TAB}`, "ws1/open/alive", `ws1/race/${TAB}`, `ws1/same/${TAB}`,
    "ws1/stray/gone", `ws1/twice/${TAB}`, "ws1/twice/gone2", `ws1/unsent/${TAB}`, "ws2/same/gone"].sort());
  assert.ok(idb.reads.every((keys) => keys.every((k) => k.startsWith("ws1/"))), "only this workspace's entries are read");
});
