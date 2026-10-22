// node --test tests/inkSaver.test.mjs (from frontend/) — the handwriting's
// save path (ink/inkSaver.js createInkSaver, docs/dev/handwriting.md
// "Fences"): when a save goes, what it sends and in which order, what it
// tells the draft store, the pill and the retry after a failure, and which
// requests go with keepalive. The draft store, the API and the keepalive
// room are fakes; the timers and the clock are node:test's mock timers.
// The e2e cannot drive a pagehide or a failing request, so this is the
// proof.
import assert from "node:assert/strict";
import { mock, test } from "node:test";
import { encodeStroke, inkProps, newInk, serializeInk } from "../src/ink/ink.js";
import { conflictMessage, createInkSaver } from "../src/ink/inkSaver.js";

const KiB = 1024;
// how long every upload's name is (inkStore.js UPLOAD_NAME_CHARS: /api/uploads/<24 hex>.ink)
const NAME = "/api/uploads/".length + 24 + ".ink".length;
const HOLD = Symbol("hold");

const stroke = (id, x = 10) => encodeStroke({ id, ch: "xyt", pen: false, samples: [{ x, y: 10, t: 0 }, { x: x + 5, y: 12, t: 8 }] });
const drawing = (...ids) => ({ ...newInk(1, 612, 792), strokes: ids.map((id, i) => stroke(id, 10 + 20 * i)) });
const emptyDrawing = () => ({ ...newInk(1, 612, 792), strokes: [] });
// a group's block in the page's tree, naming its file at `url`
const block = (id, url = "", over = {}) => ({ id, content: "", children: [], properties: { ink_url: url, ink_strokes: 1 }, ...over });
const httpError = (status, message = `HTTP ${status}`) => Object.assign(new Error(message), { status });
// every promise chain the flush started runs to its end (the timers are mocked, setImmediate is not)
const settle = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}
// Every saver a test made stops answering when the test ends, pass or fail,
// and what was under way runs out on the mock clock before it goes: a
// retry left to the real setTimeout would keep the test process alive.
const savers = [];
const withMockTimers = async (fn) => {
  mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_000_000 });
  try {
    await fn();
  } finally {
    for (const env of savers.splice(0)) env.stop();
    await settle();
    mock.timers.reset();
  }
};

// The server: an upload is answered with its file's url (u1, u2, … in the
// order the uploads come), a block's PUT with the block naming the url it
// was sent, a DELETE with nothing.
function answer(req, nextUrl) {
  if (req.line === "POST /api/upload-ink") return { url: nextUrl() };
  if (req.init.method === "PUT") return { properties: { ink_url: JSON.parse(req.init.body).properties.ink_url } };
  return {};
}

// A saver over fakes, everything it does in one log, in order: a request
// ("POST /api/upload-ink"; `sent` keeps its init and its keepalive
// priority, and `reply(req, nextUrl)` answers it with a value, or an error
// thrown, or HOLD to leave it on its way until the test settles it with
// req.answer / req.fail), a call to the draft store ("markSending g1
// /api/uploads/u1.ink"), "liveSaved g1" and "status: …" (the pill's calls
// in full in `statuses`). `drafts`: [{id, ink, base, baseIds, upload}], all
// dirty; `blocks`: the page's tree, by default each draft's block naming
// its base. `room`: what keepaliveRoom answers (its asks in `ka.asked`).
// `stop()`: no request is answered from then on (withMockTimers).
function setup({ drafts = [], blocks = null, reply = answer, room = 56 * KiB } = {}) {
  const log = [], statuses = [], sent = [];
  const idOf = new Map(drafts.map((d) => [d.ink, d.id]));
  let uploads = 0, stopped = false;
  const store = {
    UPLOAD_NAME_CHARS: NAME,
    drafts: new Map(drafts.map((d) => [d.id, { base: "", baseIds: [], upload: null, ...d, dirty: true }])),
    flushes: 0,        // a flush reads the dirty drafts once, first
    sending: null,     // what markSending answers; null: at once
    dirtyDrafts() {
      store.flushes++;
      return [...store.drafts.values()].filter((d) => d.dirty)
        .map(({ id, ink, base, baseIds, upload }) => ({ id, ink, base, baseIds, upload }));
    },
    markSending(id, url) { log.push(`markSending ${id} ${url}`); return store.sending || Promise.resolve(null); },
    markSaved(id, ink, url, stored) {
      log.push(`markSaved ${id} ${url} ${stored}`);
      if (store.drafts.get(id)?.ink === ink) store.drafts.get(id).dirty = false;
    },
    markUnsent(id, url) { log.push(`markUnsent ${id} ${url}`); },
    markDeleted(id, ink, url) {
      log.push(`markDeleted ${id} ${url}`);
      if (store.drafts.get(id)?.ink === ink) store.drafts.get(id).dirty = false;
    },
  };
  const apiJson = (url, init = {}, opts) => {
    const req = { line: `${init.method} ${url}`, init, keepalive: opts?.keepalive ?? null };
    const promise = new Promise((resolve, reject) => { req.answer = resolve; req.fail = reject; });
    sent.push(req);
    log.push(req.line);
    if (stopped) return promise;
    let out;
    try { out = reply(req, () => `/api/uploads/u${++uploads}.ink`); } catch (err) { req.fail(err); return promise; }
    if (out !== HOLD) req.answer(out);
    return promise;
  };
  const ka = { room, asked: [] };
  const saver = createInkSaver({
    blocksRef: { current: blocks || drafts.map((d) => block(d.id, d.base || "")) },
    setStatus: (...call) => { statuses.push(call); log.push(`status: ${call[0]}`); },
    liveSaved: (ink) => log.push(`liveSaved ${idOf.get(ink)}`),
    inkStore: store,
    apiJson,
    API: "/api",
    keepaliveRoom: (priority) => { ka.asked.push(priority); return ka.room; },
  });
  const env = { saver, store, log, statuses, sent, ka, stop: () => { stopped = true; } };
  savers.push(env);
  return env;
}

test("the save goes 700 ms after the last edit, and at most 5 s after the first unsaved one", () => withMockTimers(async () => {
  const { saver, store } = setup({ drafts: [{ id: "g1", ink: drawing("a1") }] });
  saver.scheduleInk();
  mock.timers.tick(699);
  saver.scheduleInk();
  mock.timers.tick(699);
  assert.equal(store.flushes, 0, "each edit puts the save 700 ms after it");
  mock.timers.tick(1);
  assert.equal(store.flushes, 1);
  // writing on, an edit every 500 ms: no 700 ms pass without one, and the save goes 5 s after the first all the same
  saver.scheduleInk();
  for (let i = 0; i < 9; i++) { mock.timers.tick(500); saver.scheduleInk(); }
  mock.timers.tick(499);
  assert.equal(store.flushes, 1, "4999 ms after the first unsaved edit");
  mock.timers.tick(1);
  assert.equal(store.flushes, 2, "5 s after it, though the last edit was 500 ms ago");
  // a flush starts the count again: the next edit waits its 700 ms
  saver.scheduleInk();
  mock.timers.tick(699);
  assert.equal(store.flushes, 2);
  mock.timers.tick(1);
  assert.equal(store.flushes, 3);
  await settle();
}));

test("a flush: the upload, markSending (awaited), the block's PUT naming the upload, the draft's base and its stroke ids, then markSaved and liveSaved", () => withMockTimers(async () => {
  const ink = drawing("a1", "b2");
  const env = setup({ drafts: [{ id: "g1", ink, base: "/api/uploads/base.ink", baseIds: ["a1"] }] });
  const journal = deferred();
  env.store.sending = journal.promise;
  const done = env.saver.flushInk();
  await settle();
  assert.deepEqual(env.log, ["POST /api/upload-ink", "markSending g1 /api/uploads/u1.ink"], "the PUT waits for the journal");
  assert.deepEqual(env.sent[0].init, { method: "POST", headers: { "Content-Type": "application/json" }, body: serializeInk(ink) });
  journal.resolve(null);
  await done;
  assert.deepEqual(env.log, ["POST /api/upload-ink", "markSending g1 /api/uploads/u1.ink", "PUT /api/blocks/g1",
    "markSaved g1 /api/uploads/u1.ink /api/uploads/u1.ink", "liveSaved g1"]);
  assert.deepEqual(env.sent[1].init, { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ properties: inkProps(ink, "/api/uploads/u1.ink"), base_properties: { ink_url: "/api/uploads/base.ink" }, base_ids: ["a1"] }) });
  assert.deepEqual(env.statuses, [], "a save that merged nothing says nothing");

  // a draft with no base names none; the answer names another file: the server merged someone's strokes in
  const fresh = drawing("c3");
  const merged = setup({ drafts: [{ id: "g2", ink: fresh, base: null, baseIds: null }],
    reply: (req, next) => (req.init.method === "PUT" ? { properties: { ink_url: "/api/uploads/merged.ink" } } : answer(req, next)) });
  await merged.saver.flushInk();
  assert.equal(merged.sent[1].init.body,
    JSON.stringify({ properties: inkProps(fresh, "/api/uploads/u1.ink"), base_properties: { ink_url: "" }, base_ids: [] }));
  assert.deepEqual(merged.log.slice(3), ["markSaved g2 /api/uploads/u1.ink /api/uploads/merged.ink", "liveSaved g2"]);
}));

test("leaving, with the upload named and the block still naming the draft's base: the PUT goes beside the upload, not waiting for markSending; otherwise after the upload's answer", () => withMockTimers(async () => {
  const ink = drawing("a1");
  const upload = { body: serializeInk(ink), url: "/api/uploads/named.ink" };
  const named = { id: "g1", ink, base: "/api/uploads/base.ink", baseIds: ["x"], upload };
  const env = setup({ drafts: [named] });
  env.store.sending = new Promise(() => {});    // a journal that never answers
  const done = env.saver.flushInk({ keepalive: true, leaving: true });
  assert.deepEqual(env.log, ["markSending g1 /api/uploads/named.ink", "POST /api/upload-ink", "PUT /api/blocks/g1"],
    "all three in the flush's own task");
  assert.equal(env.sent[0].init.body, upload.body, "the named upload's bytes");
  assert.equal(JSON.parse(env.sent[1].init.body).properties.ink_url, "/api/uploads/named.ink");
  await done;
  assert.deepEqual(env.log.slice(3), ["markSaved g1 /api/uploads/named.ink /api/uploads/named.ink", "liveSaved g1"]);

  for (const [what, over, opts] of [
    ["the block names another file: the server will merge, so the upload goes first", { blocks: [block("g1", "/api/uploads/theirs.ink")] }, { keepalive: true, leaving: true }],
    ["the page is not going away", {}, { keepalive: true }],
    ["the upload is not named yet", { drafts: [{ ...named, upload: null }] }, { keepalive: true, leaving: true }],
  ]) {
    const seq = setup({ drafts: [named], ...over });
    const journal = deferred();
    seq.store.sending = journal.promise;
    const flushed = seq.saver.flushInk(opts);
    assert.deepEqual(seq.log, ["POST /api/upload-ink"], what);
    await settle();
    assert.deepEqual(seq.log, ["POST /api/upload-ink", "markSending g1 /api/uploads/u1.ink"], what);
    journal.resolve(null);
    await flushed;
    assert.deepEqual(seq.log.slice(2), ["PUT /api/blocks/g1", "markSaved g1 /api/uploads/u1.ink /api/uploads/u1.ink", "liveSaved g1"], what);
  }
}));

test("a group erased empty with no caption and no notes is DELETEd, then markDeleted; with a caption or notes it is saved as an empty drawing", () => withMockTimers(async () => {
  for (const [what, blocks] of [
    ["a blank caption", [block("g1", "/api/uploads/old.ink", { content: "  " })]],
    ["its block not in the tree", []],
  ]) {
    const gone = setup({ drafts: [{ id: "g1", ink: emptyDrawing(), base: "/api/uploads/old.ink" }], blocks });
    await gone.saver.flushInk();
    assert.deepEqual(gone.log, ["DELETE /api/blocks/g1", `markDeleted g1 ${blocks.length ? "/api/uploads/old.ink" : ""}`], what);
    assert.deepEqual(gone.sent[0].init, { method: "DELETE" }, "no body");
  }
  for (const over of [{ content: "A caption" }, { children: [block("n1", "", { properties: {} })] }]) {
    const kept = setup({ drafts: [{ id: "g1", ink: emptyDrawing(), base: "/api/uploads/old.ink" }], blocks: [block("g1", "/api/uploads/old.ink", over)] });
    await kept.saver.flushInk();
    assert.deepEqual(kept.log, ["POST /api/upload-ink", "markSending g1 /api/uploads/u1.ink", "PUT /api/blocks/g1",
      "markSaved g1 /api/uploads/u1.ink /api/uploads/u1.ink", "liveSaved g1"]);
    assert.equal(JSON.parse(kept.sent[1].init.body).properties.ink_strokes, 0);
  }
}));

test("a 5xx, a network error or a 404 (the block's insert still queued) says the handwriting is not saved yet and tries again 2 s later, only when no save waits already", () => withMockTimers(async () => {
  for (const failure of [httpError(500, "Internal Server Error"), httpError(503), new TypeError("Failed to fetch"),
    httpError(404, "Block not found"), httpError(408), httpError(429)]) {
    let down = true;
    const env = setup({ drafts: [{ id: "g1", ink: drawing("a1") }], reply: (req, next) => { if (down) throw failure; return answer(req, next); } });
    await env.saver.flushInk();
    assert.deepEqual(env.statuses, [[`Handwriting not saved yet: ${failure.message}`]], failure.message);
    assert.deepEqual(env.log, ["POST /api/upload-ink", `status: Handwriting not saved yet: ${failure.message}`]);
    assert.equal(env.saver.pending(), true, "the retry waits on the timer");
    mock.timers.tick(1999);
    assert.equal(env.store.flushes, 1);
    down = false;
    mock.timers.tick(1);
    assert.equal(env.store.flushes, 2, "2 s later");
    await settle();
    assert.equal(env.store.drafts.get("g1").dirty, false, "saved on the retry");
    mock.timers.tick(10000);
    assert.equal(env.store.flushes, 2, "and nothing more");
  }

  // the PUT failing: an answer with a status says it did not land (markUnsent); with none it may have landed
  const putFails = (failure) => setup({ drafts: [{ id: "g1", ink: drawing("a1") }],
    reply: (req, next) => { if (req.init.method === "PUT") throw failure; return answer(req, next); } });
  const refused = putFails(httpError(502));
  await refused.saver.flushInk();
  assert.deepEqual(refused.log, ["POST /api/upload-ink", "markSending g1 /api/uploads/u1.ink", "PUT /api/blocks/g1",
    "markUnsent g1 /api/uploads/u1.ink", "status: Handwriting not saved yet: HTTP 502"]);
  assert.equal(refused.saver.pending(), true);
  const cut = putFails(new TypeError("Failed to fetch"));
  await cut.saver.flushInk();
  assert.deepEqual(cut.log, ["POST /api/upload-ink", "markSending g1 /api/uploads/u1.ink", "PUT /api/blocks/g1",
    "status: Handwriting not saved yet: Failed to fetch"]);
  assert.equal(cut.saver.pending(), true);

  // an edit while the request is out: its save stands, and the failure sets no timer beside it
  const busy = setup({ drafts: [{ id: "g1", ink: drawing("a1") }], reply: () => HOLD });
  const out = busy.saver.flushInk();
  busy.saver.scheduleInk();
  busy.sent[0].fail(httpError(500));
  await out;
  assert.equal(busy.statuses.length, 1);
  mock.timers.tick(700);
  assert.equal(busy.store.flushes, 2, "the edit's save, 700 ms after it");
  mock.timers.tick(1300);
  assert.equal(busy.store.flushes, 2, "no retry of the failure's own at 2 s");

  // two groups failing in one flush: each says so, and one timer holds the retry of both, so a flush
  // that starts before it (the tab going hidden) leaves none behind
  let down = true;
  const both = setup({ drafts: [{ id: "g1", ink: drawing("a1") }, { id: "g2", ink: drawing("b1") }],
    reply: (req, next) => { if (down) throw httpError(500); return answer(req, next); } });
  await both.saver.flushInk();
  assert.equal(both.statuses.length, 2);
  down = false;
  await both.saver.flushInk();
  assert.equal(both.saver.pending(), false);
  mock.timers.tick(2000);
  assert.equal(both.store.flushes, 2, "no retry runs after it");
  await settle();
}));

test("any other 4xx will not change on a retry: a sticky 'not saved' pill, the draft kept, no retry; a PUT refused is markUnsent", () => withMockTimers(async () => {
  const tooBig = setup({ drafts: [{ id: "g1", ink: drawing("a1") }],
    reply: (req, next) => { if (req.line === "POST /api/upload-ink") throw httpError(413, "file too large (max 6 MB)"); return answer(req, next); } });
  await tooBig.saver.flushInk();
  assert.deepEqual(tooBig.statuses, [["Handwriting not saved: file too large (max 6 MB)", null, { sticky: true }]]);
  assert.deepEqual(tooBig.log, ["POST /api/upload-ink", "status: Handwriting not saved: file too large (max 6 MB)"]);
  assert.equal(tooBig.saver.pending(), false);
  mock.timers.tick(60000);
  assert.equal(tooBig.store.flushes, 1, "never retried");
  assert.equal(tooBig.store.drafts.get("g1").dirty, true, "the draft stays");

  for (const status of [400, 403, 409, 422]) {
    const env = setup({ drafts: [{ id: "g1", ink: drawing("a1") }],
      reply: (req, next) => { if (req.init.method === "PUT") throw httpError(status); return answer(req, next); } });
    await env.saver.flushInk();
    assert.deepEqual(env.log, ["POST /api/upload-ink", "markSending g1 /api/uploads/u1.ink", "PUT /api/blocks/g1",
      "markUnsent g1 /api/uploads/u1.ink", `status: Handwriting not saved: HTTP ${status}`], `${status}`);
    assert.equal(env.saver.pending(), false);
  }

  // a DELETE refused: the empty draft stays, unmarked
  const kept = setup({ drafts: [{ id: "g1", ink: emptyDrawing(), base: "/api/uploads/old.ink" }], reply: () => { throw httpError(403, "Forbidden"); } });
  await kept.saver.flushInk();
  assert.deepEqual(kept.log, ["DELETE /api/blocks/g1", "status: Handwriting not saved: Forbidden"]);
  assert.equal(kept.store.drafts.get("g1").dirty, true);
}));

test("keepalive: the groups planFlush fits into keepaliveRoom('low'), in order, go through the ledger at 'low', each request asking the room again as it starts; a flush the page will live past asks for none", () => withMockTimers(async () => {
  const ga = { id: "ga", ink: drawing("a1"), base: "/api/uploads/a0.ink", baseIds: ["a0"] }, gb = { id: "gb", ink: drawing("b1", "b2") };
  // what a group needs of the room: its upload's bytes and its PUT's, sized with an upload name's length while the upload is not named
  const needs = (d) => Buffer.byteLength(serializeInk(d.ink))
    + Buffer.byteLength(JSON.stringify({ properties: inkProps(d.ink, ""), base_properties: { ink_url: d.base || "" }, base_ids: d.baseIds || [] })) + NAME;
  const env = setup({ drafts: [ga, gb], room: needs(ga), reply: () => HOLD });
  env.saver.flushInk({ keepalive: true });
  assert.deepEqual(env.sent.map((r) => [r.line, r.keepalive]), [["POST /api/upload-ink", "low"], ["POST /api/upload-ink", null]],
    "room for the first group exactly: the second goes plain");
  assert.deepEqual(env.ka.asked, ["low", "low"], "the plan's room, then the first upload's as it starts");
  const short = setup({ drafts: [ga, gb], room: needs(ga) - 1, reply: () => HOLD });
  short.saver.flushInk({ keepalive: true });
  assert.deepEqual(short.sent.map((r) => r.keepalive), [null, null], "one byte less: neither");

  // the PUT that waited for its upload's answer asks again: the room is gone meanwhile, so it goes plain
  env.ka.room = 0;
  env.sent[0].answer({ url: "/api/uploads/u1.ink" });
  await settle();
  assert.deepEqual(env.sent.slice(2).map((r) => [r.line, r.keepalive]), [["PUT /api/blocks/ga", null]]);

  // a flush the page will live past: every request plain, and no room asked
  const plain = setup({ drafts: [ga, gb], reply: () => HOLD });
  plain.saver.flushInk();
  assert.deepEqual(plain.sent.map((r) => r.keepalive), [null, null]);
  assert.deepEqual(plain.ka.asked, []);

  // no room at all: a DELETE, which has no body, still goes with keepalive
  const del = setup({ drafts: [{ id: "gd", ink: emptyDrawing(), base: "/api/uploads/d.ink" }], room: 0, reply: () => HOLD });
  del.saver.flushInk({ keepalive: true });
  assert.deepEqual(del.sent.map((r) => [r.line, r.keepalive]), [["DELETE /api/blocks/gd", "low"]]);
}));

test("a keepalive request of a drawing still on its way is not sent again: a later flush skips it, or sends the PUT alone beside the upload when leaving; settled, the drawing goes again", () => withMockTimers(async () => {
  const ink = drawing("a1");
  const env = setup({ drafts: [{ id: "g1", ink, base: "/api/uploads/base.ink", upload: { body: serializeInk(ink), url: "/api/uploads/named.ink" } }],
    reply: () => HOLD });
  env.saver.flushInk({ keepalive: true });                    // going hidden: the upload on its way
  env.saver.flushInk({ keepalive: true });                    // again: nothing new
  assert.deepEqual(env.log, ["POST /api/upload-ink"]);
  env.saver.flushInk({ keepalive: true, leaving: true });     // pagehide: the PUT alone, beside the upload on its way
  assert.deepEqual(env.log, ["POST /api/upload-ink", "markSending g1 /api/uploads/named.ink", "PUT /api/blocks/g1"]);
  assert.equal(env.sent[1].keepalive, "low");
  env.saver.flushInk({ keepalive: true, leaving: true });     // the PUT on its way: nothing
  assert.equal(env.log.length, 3);

  const empty = emptyDrawing();
  const del = setup({ drafts: [{ id: "gd", ink: empty, base: "/api/uploads/d.ink" }], reply: () => HOLD });
  del.saver.flushInk({ keepalive: true });
  del.saver.flushInk({ keepalive: true });
  assert.deepEqual(del.log, ["DELETE /api/blocks/gd"], "its DELETE on its way: a second would be answered 404");
  del.sent[0].fail(httpError(404));
  await settle();
  del.saver.flushInk({ keepalive: true });
  assert.deepEqual(del.log, ["DELETE /api/blocks/gd", "status: Handwriting not saved yet: HTTP 404", "DELETE /api/blocks/gd"],
    "settled, it goes again");

  // only what went with keepalive is held: a plain request on its way stops nothing
  const plain = setup({ drafts: [{ id: "g1", ink: drawing("a1") }], reply: () => HOLD });
  plain.saver.flushInk();
  plain.saver.flushInk();
  assert.deepEqual(plain.log, ["POST /api/upload-ink", "POST /api/upload-ink"]);
}));

test("the merge report on the pill, after markSaved and liveSaved: strokes of ours not applied, strokes someone erased that came back, or merged", () => withMockTimers(async () => {
  for (const [conflict, said] of [
    [{ clean: false, lost_ours: ["a"], returned: ["b"] }, "Your change to 1 stroke was not applied: someone changed it since"],
    [{ clean: false, lost_ours: ["a", "b"] }, "Your change to 2 strokes was not applied: someone changed them since"],
    [{ clean: false, returned: ["b"] }, "1 stroke someone erased came back"],
    [{ clean: false, lost_ours: [], returned: ["b", "c", "d"] }, "3 strokes someone erased came back"],
    [{ clean: false }, "Merged with changes made meanwhile"],
    [{ clean: true, lost_ours: ["a"] }, null],
    [undefined, null],
  ]) {
    const env = setup({ drafts: [{ id: "g1", ink: drawing("a1") }],
      reply: (req, next) => (req.init.method === "PUT" ? { properties: { ink_url: "/api/uploads/m.ink" }, ink_conflict: conflict } : answer(req, next)) });
    await env.saver.flushInk();
    assert.deepEqual(env.log.slice(3), ["markSaved g1 /api/uploads/u1.ink /api/uploads/m.ink", "liveSaved g1", ...(said ? [`status: ${said}`] : [])],
      JSON.stringify(conflict));
    assert.deepEqual(env.statuses, said ? [[said]] : []);
  }
}));

test("conflictMessage: the merge's report as the pill says it, ours lost before strokes come back, nothing for a clean save", () => {
  assert.equal(conflictMessage({ clean: false, lost_ours: ["a"], returned: ["b"] }), "Your change to 1 stroke was not applied: someone changed it since");
  assert.equal(conflictMessage({ clean: false, lost_ours: ["a", "b"] }), "Your change to 2 strokes was not applied: someone changed them since");
  assert.equal(conflictMessage({ clean: false, lost_ours: [], returned: ["b"] }), "1 stroke someone erased came back");
  assert.equal(conflictMessage({ clean: false, returned: ["b", "c", "d"] }), "3 strokes someone erased came back");
  assert.equal(conflictMessage({ clean: false }), "Merged with changes made meanwhile");
  assert.equal(conflictMessage({ clean: false, lost_ours: [], returned: [] }), "Merged with changes made meanwhile");
  assert.equal(conflictMessage({ clean: true, lost_ours: ["a"] }), null);
  assert.equal(conflictMessage(undefined), null);
  assert.equal(conflictMessage(null), null);
});

test("pending(): a save waiting on its timer, set by an edit or a retry; a flush that starts clears it, so the timer never runs a second one", () => withMockTimers(async () => {
  const env = setup({ drafts: [{ id: "g1", ink: drawing("a1") }] });
  assert.equal(env.saver.pending(), false);
  env.saver.scheduleInk();
  assert.equal(env.saver.pending(), true);
  const done = env.saver.flushInk();     // visibilitychange's flush, before the timer
  assert.equal(env.saver.pending(), false);
  await done;
  mock.timers.tick(700);
  assert.equal(env.store.flushes, 1, "the timer it cleared never runs");
  env.saver.scheduleInk();
  mock.timers.tick(700);
  assert.equal(env.store.flushes, 2);
  assert.equal(env.saver.pending(), false, "the timer's own flush clears it");
  await settle();
}));
