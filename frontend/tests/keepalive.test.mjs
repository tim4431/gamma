// node --test tests/keepalive.test.mjs (from frontend/) — the page's one
// keepalive ledger (shared/lib/keepalive.js, docs/dev/collab.md
// "Keepalive"): how a body is counted, the room of each priority, the
// claims, the release, and a closing tab's requests over a model of the
// browser's 64 KiB rule. The e2e cannot drive a pagehide, so this is the
// proof.
import assert from "node:assert/strict";
import { test } from "node:test";
import { planFlush } from "../src/ink/inkFlush.js";
import { KEEPALIVE_CAP, NORMAL_RESERVE, bodyBytes, createKeepaliveLedger, keepaliveShare } from "../src/shared/lib/keepalive.js";

const KiB = 1024;
const text = (n) => "a".repeat(n);
const settle = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

// The browser as the fetch spec has it: a keepalive request whose body would
// take the page's keepalive bodies in flight past 64 KiB is refused (a
// network error; nothing is sent). Every request stays on its way until the
// test answers or fails it.
function browser() {
  const b = { inFlight: 0, sent: [], refused: [] };
  b.fetch = (url, init = {}) => {
    const bytes = bodyBytes(init.body);
    if (init.keepalive && b.inFlight + bytes > KEEPALIVE_CAP) {
      b.refused.push({ url, bytes });
      return Promise.reject(new TypeError("Failed to fetch"));
    }
    let resolve, reject;
    const promise = new Promise((a, r) => { resolve = a; reject = r; });
    const req = { url, init, bytes, keepalive: !!init.keepalive, done: false };
    if (req.keepalive) b.inFlight += bytes;
    const end = () => { if (!req.done && req.keepalive) b.inFlight -= bytes; req.done = true; };
    req.answer = (value = { ok: true }) => { end(); resolve(value); };
    req.fail = (err = new TypeError("Failed to fetch")) => { end(); reject(err); };
    b.sent.push(req);
    return promise;
  };
  b.of = (url) => b.sent.filter((r) => r.url === url);
  return b;
}

test("bodyBytes counts what a fetch sends: a string's UTF-8, a Blob's size, a buffer's or a view's bytes, nothing for no body", () => {
  assert.deepEqual(["", "plain ascii", "é and ü", "€ 中文", "an emoji 🖊"].map((s) => bodyBytes(s)), [0, 11, 9, 10, 13]);
  for (const s of ["é and ü", "€ 中文", "an emoji 🖊", "\ud83d", "a\udc00b", "\ud83d\ud83d"]) {
    assert.equal(bodyBytes(s), new TextEncoder().encode(s).length, `${JSON.stringify(s)} as TextEncoder encodes it (a lone surrogate as U+FFFD)`);
  }
  assert.equal(bodyBytes(text(100000), KEEPALIVE_CAP), 100000, "a string over the cap is not encoded: its length stands for it");
  assert.equal(bodyBytes("€€€€", 10), 12, "one under the cap in characters is counted in bytes");
  assert.equal(bodyBytes(new Blob(["€ 中文"])), 10);
  assert.equal(bodyBytes(new ArrayBuffer(7)), 7);
  assert.equal(bodyBytes(new Uint16Array(5)), 10);
  assert.equal(bodyBytes(new DataView(new ArrayBuffer(16), 4, 8)), 8);
  assert.equal(bodyBytes(new URLSearchParams({ q: "é" })), "q=%C3%A9".length);
  assert.deepEqual([bodyBytes(undefined), bodyBytes(null)], [0, 0], "no body: a DELETE");
  assert.equal(bodyBytes(new FormData()), Infinity, "a length not known before it goes never goes with keepalive");
  assert.equal(bodyBytes(new ReadableStream()), Infinity);
});

test("keepaliveShare: a claim is what goes with keepalive in order from an empty page; a body over the cap claims nothing", () => {
  assert.equal(keepaliveShare([]), 0);
  assert.equal(keepaliveShare([text(KiB), text(2 * KiB)]), 3 * KiB);
  assert.equal(keepaliveShare([text(30 * KiB), text(40 * KiB), text(20 * KiB)]), 50 * KiB, "the second would pass the cap and goes plain; the third still fits");
  assert.equal(keepaliveShare([text(KEEPALIVE_CAP)]), KEEPALIVE_CAP);
  assert.equal(keepaliveShare([text(KEEPALIVE_CAP + 1)]), 0);
});

test("the three priorities: critical has the cap less what is in flight, normal that less the critical claims, low that less the normal reserve", () => {
  const b = browser();
  const ledger = createKeepaliveLedger({ fetch: b.fetch });
  assert.deepEqual(["critical", "normal", "low"].map((p) => ledger.available(p)), [KEEPALIVE_CAP, KEEPALIVE_CAP, KEEPALIVE_CAP - NORMAL_RESERVE]);
  ledger.registerClaim("page-edits", () => 10 * KiB);
  ledger.fetch("/api/prefs/read-pos", { method: "PUT", body: text(5 * KiB) }, { priority: "normal" });
  assert.equal(ledger.inFlight, 5 * KiB);
  assert.deepEqual(["critical", "normal", "low"].map((p) => ledger.available(p)), [59 * KiB, 49 * KiB, 41 * KiB]);
  assert.equal(ledger.available(), 49 * KiB, "normal is the default");
  // the critical sender spends its own claim: nothing of it is held off its room
  ledger.fetch("/api/pages/p/ops", { method: "POST", body: text(10 * KiB) }, { priority: "critical" });
  assert.deepEqual(b.sent.map((r) => r.keepalive), [true, true]);
  assert.equal(ledger.available("critical"), 49 * KiB);
});

test("a claim is read only when a lower priority asks for room — never when it is registered or a critical request goes — and unregistered holds nothing", () => {
  const b = browser();
  const ledger = createKeepaliveLedger({ fetch: b.fetch });
  let reads = 0;
  const unclaim = ledger.registerClaim("page-edits", () => { reads++; return 20 * KiB; });
  assert.equal(reads, 0, "registering reads nothing");
  ledger.fetch("/api/pages/p/ops", { method: "POST", body: text(30 * KiB) }, { priority: "critical" });
  assert.equal(ledger.available("critical"), 34 * KiB);
  assert.equal(reads, 0, "a critical request and the critical room read nothing");
  assert.equal(ledger.available("normal"), 14 * KiB);
  assert.equal(reads, 1);
  ledger.fetch("/api/prefs/read-pos", { method: "PUT", body: text(KiB) }, { priority: "normal" });
  assert.equal(reads, 2, "each ask reads the claim afresh");
  unclaim();
  assert.equal(ledger.available("low"), 33 * KiB - NORMAL_RESERVE, "an unregistered claim holds nothing");
  assert.equal(reads, 2);
  // a claim registered again under its key replaces the old one, which can no longer unregister it
  const old = ledger.registerClaim("page-edits", () => 1 * KiB);
  ledger.registerClaim("page-edits", () => 2 * KiB);
  old();
  assert.equal(ledger.available("normal"), 31 * KiB);
  ledger.unregisterClaim("page-edits");
  assert.equal(ledger.available("normal"), 33 * KiB);
  // a claim that throws or answers nonsense holds nothing
  for (const [key, fn] of [["throws", () => { throw new Error("boom"); }], ["nan", () => NaN], ["negative", () => -5], ["none", () => undefined]]) {
    ledger.registerClaim(key, fn);
  }
  assert.equal(ledger.available("normal"), 33 * KiB);
  // claims past what is left leave the lower priorities no room, never less than none
  ledger.registerClaim("page-edits", () => 60 * KiB);
  assert.deepEqual(["critical", "normal", "low"].map((p) => ledger.available(p)), [33 * KiB, 0, 0]);
});

test("a keepalive request's bytes count until it settles, answered (with no body to read) or failed; the caller gets the fetch's own answer; one that throws at once counts nothing", async () => {
  const b = browser();
  const ledger = createKeepaliveLedger({ fetch: b.fetch });
  const first = ledger.fetch("/api/a", { method: "POST", body: text(10 * KiB) }, { priority: "normal" });
  const second = ledger.fetch("/api/b", { method: "POST", body: text(20 * KiB) }, { priority: "normal" });
  assert.equal(ledger.inFlight, 30 * KiB);
  b.sent[0].answer({ status: 200 });
  assert.deepEqual(await first, { status: 200 });
  await settle();
  assert.equal(ledger.inFlight, 20 * KiB, "released once answered");
  b.sent[1].fail();
  await assert.rejects(second, TypeError, "the failure reaches the caller");
  await settle();
  assert.equal(ledger.inFlight, 0, "released once failed");
  const throwing = createKeepaliveLedger({ fetch: () => { throw new TypeError("bad init"); } });
  assert.throws(() => throwing.fetch("/api/c", { method: "POST", body: "x" }), TypeError);
  assert.equal(throwing.inFlight, 0);
});

test("a request's bytes count until its answer's body is in, as the browser counts them — not only until the headers", async () => {
  const sent = [];
  // answers with a body the test ends: the fetch's promise settles with the headers, the body comes after
  const ledger = createKeepaliveLedger({ fetch: (url, init) => {
    let push;
    const body = new ReadableStream({ start(c) { push = c; } });
    const req = { url, init, end: (text) => { push.enqueue(new TextEncoder().encode(text)); push.close(); }, error: () => push.error(new TypeError("reset")) };
    sent.push(req);
    return Promise.resolve(new Response(body, { status: 200, headers: { "Content-Type": "application/json" } }));
  } });
  // a body's chunks travel through a few turns of the event loop
  const drain = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r)); };
  const first = await ledger.fetch("/api/upload-ink", { method: "POST", body: text(40 * KiB) }, { priority: "low" });
  await drain();
  assert.equal(ledger.inFlight, 40 * KiB, "the headers are in, the body is not: the bytes still count");
  const read = first.json();   // the caller reads its own answer
  sent[0].end('{"url":"/api/uploads/a.ink"}');
  assert.deepEqual(await read, { url: "/api/uploads/a.ink" });
  await drain();
  assert.equal(ledger.inFlight, 0, "released once the body is in");
  // an answer nobody reads is released too, and so is one whose body fails
  await ledger.fetch("/api/prefs/read-pos", { method: "PUT", body: text(KiB) }, { priority: "normal" });
  await ledger.fetch("/api/blocks/b", { method: "PUT", body: text(2 * KiB) }, { priority: "low" });
  await drain();
  assert.equal(ledger.inFlight, 3 * KiB);
  sent[1].end("{}");
  sent[2].error();
  await drain();
  assert.equal(ledger.inFlight, 0);
});

test("a request that does not fit its priority's room goes as a plain fetch — never a keepalive the browser would refuse", async () => {
  const b = browser();
  const ledger = createKeepaliveLedger({ fetch: b.fetch });
  ledger.registerClaim("page-edits", () => 40 * KiB);
  // handwriting's room: 64 − 40 − 8 KiB
  ledger.fetch("/api/upload-ink", { method: "POST", body: text(17 * KiB), keepalive: true }, { priority: "low" });
  assert.deepEqual([b.sent[0].keepalive, "keepalive" in b.sent[0].init], [false, false], "plain, the init's own keepalive not asked");
  ledger.fetch("/api/upload-ink", { method: "POST", body: text(16 * KiB) }, { priority: "low" });
  assert.equal(b.sent[1].keepalive, true, "exactly the room fits");
  // the normal senders' room: 64 − 16 − 40
  ledger.fetch("/api/prefs/profile", { method: "PATCH", body: text(9 * KiB) }, { priority: "normal" });
  ledger.fetch("/api/prefs/read-pos", { method: "PUT", body: text(8 * KiB) }, { priority: "normal" });
  ledger.fetch("/api/uploads/parts/t", { method: "DELETE" }, { priority: "normal" });
  assert.deepEqual(b.sent.slice(2).map((r) => r.keepalive), [false, true, true], "the profile goes plain, the reading position fits, a body-less DELETE always does");
  ledger.fetch("/api/uploads/parts/u", { method: "DELETE" }, { priority: "low" });
  assert.equal(ledger.available("low"), 0, "handwriting's room is overdrawn (64 − 24 − 40 − 8 KiB): it is 0, never less");
  assert.equal(b.sent[5].keepalive, true, "a request with no body still goes with keepalive: it takes no room");
  // the edits: their claim was held for them
  ledger.fetch("/api/pages/p/ops", { method: "POST", body: text(40 * KiB) }, { priority: "critical" });
  assert.equal(b.sent[6].keepalive, true);
  ledger.fetch("/api/pages/p/ops", { method: "POST", body: text(KEEPALIVE_CAP + 1) }, { priority: "critical" });
  assert.equal(b.sent[7].keepalive, false, "a body over the cap never goes with keepalive");
  ledger.fetch("/api/upload-ink", { method: "POST", body: new FormData() }, { priority: "critical" });
  assert.equal(b.sent[8].keepalive, false, "nor one whose length is not known");
  assert.deepEqual(b.refused, [], "the browser refused nothing");
  assert.equal(ledger.inFlight, b.inFlight, "and the ledger counts what the browser counts");
  for (const r of b.sent) r.answer();
  await settle();
  assert.deepEqual([ledger.inFlight, b.inFlight], [0, 0]);
});

// A closing tab fires visibilitychange (hidden) before pagehide. Hidden:
// the profile and the reading position go (normal), and handwriting's flush
// (low) sends what planFlush fits into the room the ledger leaves it. Pagehide:
// the queued page edits (critical) — which have no other copy — and then
// handwriting's second flush, its PUTs beside the uploads on their way.
async function closeTab({ edits, drafts, profile = 3 * KiB, readPos = 300 }) {
  const b = browser();
  const ledger = createKeepaliveLedger({ fetch: b.fetch });
  const bodies = edits.map((n) => text(n));
  let left = false;   // usePageCollab: nothing more is claimed once pagehide has sent the edits
  ledger.registerClaim("page-edits", () => (left ? 0 : keepaliveShare(bodies)));
  // App's flushInk: keepalive when the plan fits the group and the ledger still has room, else plain
  const inkRequest = (url, body, ka) => (ka && bodyBytes(body) <= ledger.available("low")
    ? ledger.fetch(url, { method: "POST", body }, { priority: "low" }) : b.fetch(url, { method: "POST", body }));
  const groups = drafts.map((k, i) => ({ id: `g${i}`, uploadBytes: k * KiB, putBytes: 600, beside: false, inFlight: null }));
  // visibilitychange to hidden
  ledger.fetch("/api/prefs/profile", { method: "PATCH", body: text(profile) }, { priority: "normal" });
  ledger.fetch("/api/prefs/read-pos", { method: "PUT", body: text(readPos) }, { priority: "normal" });
  const hidden = planFlush(groups, ledger.available("low"));
  const uploading = new Set();
  hidden.forEach((p, i) => {
    inkRequest("/api/upload-ink", text(groups[i].uploadBytes), p.keepalive);
    if (p.keepalive && b.sent.at(-1).keepalive) uploading.add(groups[i].id);
  });
  // pagehide: the edits first (usePageCollab registers its listener first), then handwriting again
  for (const body of bodies) ledger.fetch("/api/pages/p/ops", { method: "POST", body }, { priority: "critical" });
  left = true;
  const leaving = planFlush(groups.map((g) => ({ ...g, beside: true, inFlight: uploading.has(g.id) ? "upload" : null })), ledger.available("low"));
  leaving.forEach((p, i) => {
    if (p.send === "both") inkRequest("/api/upload-ink", text(groups[i].uploadBytes), p.keepalive);
    if (p.send) inkRequest("/api/blocks/g", text(groups[i].putBytes), p.keepalive);
  });
  return { b, ledger, posts: b.of("/api/pages/p/ops") };
}

test("closing a tab: handwriting flushes on going hidden, and the edits' POST on pagehide still goes with keepalive whenever the edits alone fit the cap", async () => {
  const editSets = [[], [KiB], [20 * KiB], [30 * KiB, 25 * KiB], [KEEPALIVE_CAP - 2 * KiB], [KEEPALIVE_CAP], [40 * KiB, 24 * KiB]];
  const draftSets = [[10, 10], [50], [20, 30, 5], [1, 1, 1, 1], [60]];
  for (const edits of editSets) {
    for (const drafts of draftSets) {
      const { b, ledger, posts } = await closeTab({ edits, drafts });
      const label = `edits ${edits.map((n) => n / KiB)} KiB, drafts ${drafts} KiB`;
      assert.equal(posts.length, edits.length, label);
      assert.ok(posts.every((r) => r.keepalive), `${label}: every edit POST goes with keepalive`);
      assert.deepEqual(b.refused, [], `${label}: the browser refused nothing`);
      assert.equal(ledger.inFlight, b.inFlight, label);
      assert.ok(b.inFlight <= KEEPALIVE_CAP, label);
    }
  }
  // the room the edits do not need is handwriting's: with nothing queued, the hidden flush carries 50 KiB
  const idle = await closeTab({ edits: [], drafts: [50] });
  assert.ok(idle.b.of("/api/upload-ink")[0].keepalive);
  // edits over the cap go plain whatever is left, so they claim nothing and starve nobody
  const huge = await closeTab({ edits: [KEEPALIVE_CAP + 1], drafts: [40] });
  assert.deepEqual([huge.posts[0].keepalive, huge.b.of("/api/upload-ink")[0].keepalive], [false, true]);
  assert.deepEqual(huge.b.refused, []);
});

test("closing a tab: the edits' claim is held off handwriting's room, so a drawing that would crowd them out goes plain", async () => {
  // without the ledger (release T): the drawing's 50 KiB upload went with keepalive on going hidden,
  // and the edits' 20 KiB POST on pagehide passed the cap — refused, the typed text lost
  const unshared = browser();
  unshared.fetch("/api/upload-ink", { method: "POST", body: text(50 * KiB), keepalive: true });
  unshared.fetch("/api/pages/p/ops", { method: "POST", body: text(20 * KiB), keepalive: true }).catch(() => {});
  assert.deepEqual(unshared.refused.map((r) => r.url), ["/api/pages/p/ops"]);
  // through the ledger: the drawing goes plain (the draft journal keeps it), the edits with keepalive
  const { b, posts } = await closeTab({ edits: [20 * KiB], drafts: [50] });
  assert.equal(b.of("/api/upload-ink")[0].keepalive, false);
  assert.equal(posts[0].keepalive, true);
  assert.deepEqual(b.refused, []);
  // and the normal senders keep their room ahead of handwriting
  assert.deepEqual(["/api/prefs/profile", "/api/prefs/read-pos"].map((u) => b.of(u)[0].keepalive), [true, true]);
});
