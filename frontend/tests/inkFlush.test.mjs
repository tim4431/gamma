// node --test tests/inkFlush.test.mjs (from frontend/) — what a flush of the
// handwriting drafts sends for each group, and which of it goes with fetch
// keepalive in the room the page's keepalive ledger leaves handwriting, the
// lowest priority (ink/inkFlush.js, shared/lib/keepalive.js,
// docs/dev/handwriting.md "Fences"). No step of the browser suite checks
// what a closing tab sends (the scratch tests/e2e/tabClose.mjs does), so
// this is the proof.
import assert from "node:assert/strict";
import { test } from "node:test";
import { planFlush } from "../src/ink/inkFlush.js";
import { KEEPALIVE_CAP, NORMAL_RESERVE, createKeepaliveLedger } from "../src/shared/lib/keepalive.js";

const KiB = 1024;
// a page with nothing else in flight and nothing claimed: the cap less the normal senders' reserve
const ROOM = createKeepaliveLedger().available("low");
// a group of `upload` KiB and a PUT of `put` bytes
const group = (id, upload, put = 600, over = {}) => ({ id, uploadBytes: upload * KiB, putBytes: put, beside: false, inFlight: null, ...over });
const keepalives = (plan) => plan.filter((p) => p.keepalive).map((p) => p.id);

test("planFlush: every group's requests are planned at once, keepalive to the groups that fit the room in order, the rest plain", () => {
  assert.equal(ROOM, KEEPALIVE_CAP - NORMAL_RESERVE, "56 KiB on an idle page");
  // three small groups fit together: every one goes with keepalive
  const small = planFlush([group("a", 10), group("b", 10), group("c", 10)], ROOM);
  assert.deepEqual(small, [{ id: "a", send: "both", keepalive: true }, { id: "b", send: "both", keepalive: true }, { id: "c", send: "both", keepalive: true }]);
  // the running sum of upload and PUT bodies: 2 × (25 KiB + 600 B) fit, a third would pass 56 KiB
  const sum = planFlush([group("a", 25), group("b", 25), group("c", 25), group("d", 4)], ROOM);
  assert.deepEqual(keepalives(sum), ["a", "b", "d"], "the third goes plain, and a smaller one after it still fits");
  assert.ok(sum.every((p) => p.send === "both"), "every group is sent, keepalive or not");
  // a group over the room alone (a 6 MB drawing) goes plain and leaves the room to the rest
  assert.deepEqual(keepalives(planFlush([group("big", 6144), group("a", 1), group("b", 1)], ROOM)), ["a", "b"]);
  // the boundary: exactly the room fits, one byte more does not
  assert.deepEqual(keepalives(planFlush([{ ...group("x", 0, 0), uploadBytes: ROOM - 100, putBytes: 100 }], ROOM)), ["x"]);
  assert.deepEqual(keepalives(planFlush([{ ...group("x", 0, 0), uploadBytes: ROOM - 100, putBytes: 101 }], ROOM)), []);
  // the room is what the ledger leaves now: less what is in flight and what the page's edits claim
  assert.deepEqual(keepalives(planFlush([group("a", 10), group("b", 10)], 15 * KiB)), ["a"]);
  assert.deepEqual(keepalives(planFlush([group("a", 10)], 0)), []);
  assert.deepEqual(planFlush([group("a", 1), { id: "gone", remove: true }], -Infinity),
    [{ id: "a", send: "both", keepalive: false }, { id: "gone", send: "delete", keepalive: false }],
    "a flush the page will live past asks for no room: every group plain, and every group sent");
});

test("planFlush: a group erased empty is a DELETE with no body, sent once; a drawing already on its way is not sent again", () => {
  const plan = planFlush([
    { id: "gone", remove: true, inFlight: null },
    { id: "going", remove: true, inFlight: "delete" },
    group("putting", 30, 600, { inFlight: "put" }),
    group("uploading", 30, 600, { inFlight: "upload" }),
    group("beside", 30, 600, { inFlight: "upload", beside: true }),
    group("fresh", 54),
  ], ROOM);
  assert.deepEqual(plan, [
    { id: "gone", send: "delete", keepalive: true },
    { id: "going", send: null, keepalive: false },        // its DELETE is on its way: a second would be answered 404
    { id: "putting", send: null, keepalive: false },      // its PUT is on its way
    { id: "uploading", send: null, keepalive: false },    // its PUT follows the upload's answer, or the journal brings it back
    { id: "beside", send: "put", keepalive: true },        // on pagehide: the PUT alone, beside the upload on its way
    { id: "fresh", send: "both", keepalive: true },        // 54 KiB + 600 B after the 600 B PUT: within 56 KiB
  ]);
  assert.deepEqual(planFlush([group("a", 1), { id: "gone", remove: true }], 0), [{ id: "a", send: "both", keepalive: false }, { id: "gone", send: "delete", keepalive: true }],
    "no room left (the ledger's room is never below 0): a group with a body goes plain, a DELETE still fits");
});

test("planFlush: with the page's edits claiming all of handwriting's room, an empty group's DELETE still goes with keepalive on going hidden, so pagehide does not send it again", () => {
  const sent = [];
  const ledger = createKeepaliveLedger({ fetch: (url, init) => { sent.push({ url, init }); return new Promise(() => {}); } });
  ledger.registerClaim("page-edits", () => 60 * KiB);
  assert.equal(ledger.available("low"), 0, "64 − 60 − 8 KiB: none, never less");
  const drafts = [group("a", 2), { id: "gone", remove: true, inFlight: null }];
  const hidden = planFlush(drafts, ledger.available("low"));
  assert.deepEqual(hidden, [{ id: "a", send: "both", keepalive: false }, { id: "gone", send: "delete", keepalive: true }]);
  ledger.fetch("/api/blocks/gone", { method: "DELETE" }, { priority: "low" });
  assert.equal(sent[0].init.keepalive, true, "the ledger lets a request with no body go with keepalive");
  // App holds that DELETE by drawing; pagehide's flush sends it no second time (a second would be answered 404)
  const leaving = planFlush(drafts.map((d) => (d.remove ? { ...d, inFlight: "delete" } : { ...d, beside: true })), ledger.available("low"));
  assert.deepEqual(leaving.map((p) => p.send), ["both", null]);
});

test("planFlush: a closing tab's two flushes — on going hidden, then on pagehide — share the ledger's room, and what the first has on its way is not sent again", () => {
  const sent = [];
  const ledger = createKeepaliveLedger({ fetch: (url, init) => { sent.push({ url, init }); return new Promise(() => {}); } });
  const drafts = [group("a", 20), group("b", 20), group("c", 30), { id: "gone", remove: true, inFlight: null }];
  // going hidden: a and b fit (40 KiB and their PUTs), c does not and goes plain, the DELETE has no body
  const hidden = planFlush(drafts, ledger.available("low"));
  assert.deepEqual(keepalives(hidden), ["a", "b", "gone"]);
  // App sends the uploads and the DELETE through the ledger (each PUT waits for its upload's answer)
  // and holds each by drawing while it is on its way
  const onItsWay = new Map();
  hidden.forEach((p, i) => {
    if (!p.keepalive) return;
    const d = drafts[i];
    if (p.send === "delete") ledger.fetch(`/api/blocks/${d.id}`, { method: "DELETE" }, { priority: "low" });
    else ledger.fetch("/api/upload-ink", { method: "POST", body: "x".repeat(d.uploadBytes) }, { priority: "low" });
    onItsWay.set(d.id, p.send === "delete" ? "delete" : "upload");
  });
  assert.ok(sent.every((r) => r.init.keepalive), "the ledger had room for every one");
  assert.equal(ledger.inFlight, 40 * KiB, "the uploads' bodies count; the DELETE has none");
  // pagehide: a and b send their PUT alone, beside the uploads on their way; c is tried again in what
  // is left; the DELETE on its way is not sent again
  const leaving = planFlush(drafts.map((d) => ({ ...d, beside: !d.remove, inFlight: onItsWay.get(d.id) ?? null })), ledger.available("low"));
  assert.deepEqual(leaving, [
    { id: "a", send: "put", keepalive: true }, { id: "b", send: "put", keepalive: true },
    { id: "c", send: "both", keepalive: false }, { id: "gone", send: null, keepalive: false },
  ]);
  const carried = ledger.inFlight + leaving.filter((p) => p.keepalive).reduce((n, p) => n + drafts.find((d) => d.id === p.id).putBytes, 0);
  assert.ok(carried <= KEEPALIVE_CAP - NORMAL_RESERVE, `the two flushes together carry ${carried} bytes with keepalive, the normal reserve left`);
});
