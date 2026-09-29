// The page session when saving goes wrong, over fakes for HTTP, the socket
// and timers: a batch the page changed under (only the refused op goes),
// signed out (nothing dropped), refused for good (only that batch), batches
// split to the server's size, text too long to save held back, retries that
// never give up, a batch id kept across retries, reloads that keep unsaved
// edits, a return to a page left mid-save, and the undo stack after a
// reload. collabSession.test.mjs covers the normal paths.
import assert from "node:assert/strict";
import { test } from "node:test";
import { applyOps, diffTrees, pushOp, seedPositions } from "../src/shared/model/blockOps.js";
import { MAX_CONTENT, MAX_OPS, MAX_RESCUES, RETRY_MS, createCollabSession } from "../src/collaboration/collabSession.js";
import { clearHistory, createHistory, observeTree, rebaseHistory, undoStep } from "../src/editor/blockHistory.js";

const ME = "this-client";
const block = (id, content = id, properties = {}, position = "a0") => ({ id, content, properties, children: [], position });
const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const httpError = (status, message, data) => Object.assign(new Error(message), { status, data });
const ack = (seq, ops) => ({ seq, ops });

// A session over fakes, like collabSession.test.mjs's; `api(path, init,
// body)` answers the HTTP calls. Timers fire only through h.fire().
function setup(t, api, { tree } = {}) {
  const h = { calls: [], remote: [], reloads: [], status: [], notices: [], keepalive: [], sockets: [], timers: new Map() };
  let timerId = 0;
  const options = {
    pageId: "page-a", canWrite: true,
    onRemoteOps: (ops, page, pos) => { h.remote.push(...ops); h.tree = applyOps(h.tree, ops, page, pos); },
    onReload: (page) => h.reloads.push(page),
    onStatus: (text) => h.status.push(text),
    onSaveNotice: (text, kind) => h.notices.push([kind, text]),
  };
  h.session = createCollabSession({
    clientId: ME,
    api: (path, init) => {
      const body = init?.body ? JSON.parse(init.body) : null;
      h.calls.push({ url: path, body });
      return api(path, init, body);
    },
    openSocket: () => {
      const ws = { readyState: 1, sent: [], send(m) { ws.sent.push(JSON.parse(m)); }, close() {} };
      h.sockets.push(ws);
      return ws;
    },
    keepalivePost: (path, body) => h.keepalive.push({ path, body: JSON.parse(body) }),
    opts: () => options,
    timers: { set: (fn, ms) => { h.timers.set(++timerId, { fn, ms }); return timerId; }, clear: (id) => h.timers.delete(id) },
  });
  h.options = options;
  h.socket = () => h.sockets[h.sockets.length - 1];
  h.message = (msg) => h.socket().onmessage({ data: JSON.stringify(msg) });
  h.fire = () => { const due = [...h.timers.values()]; h.timers.clear(); for (const { fn } of due) fn(); };
  // What App does with a fetched tree: this tab's unsaved edits laid over it, then a load.
  h.load = (page, fetched, seq = 0) => {
    options.pageId = page;
    h.tree = h.session.overlay(page, fetched);
    h.session.commit(h.tree, { isLoad: true, seq });
  };
  h.edit = (next) => { h.tree = next; return h.session.commit(next); };
  h.load("page-a", tree || [block("a"), block("b", "b", {}, "a1")]);
  h.session.connect("page-a");
  t.after(() => h.session.disconnect());
  return h;
}

const setText = (tree, id, content) => tree.map((n) => (n.id === id ? { ...n, content } : { ...n, children: setText(n.children || [], id, content) }));
const lastNotice = (h, kind = "pending") => [...h.notices].reverse().find(([k]) => k === kind)?.[1];

// --- C1: a batch the page changed under -----------------------------------------

test("a conflict drops only the refused op; the rest goes out again, keeping its base", async (t) => {
  const h = setup(t, async (url, init, body) => {
    if (h.calls.length === 1) {
      const index = body.ops.findIndex((op) => op.id === "x");
      throw httpError(404, "no such block: x", { detail: "no such block: x", missing: "x", conflict: "missing", index });
    }
    return ack(2, body.ops);
  }, { tree: [block("a"), block("x", "x", {}, "a1"), block("y", "y", {}, "a2")] });
  // typing in x and in y (the editor open on y), one debounce window
  h.edit(setText(setText(h.tree, "x", "x — typed"), "y", "y — a whole paragraph"));
  // meanwhile x is deleted elsewhere; the delete reaches us before our batch goes
  h.message({ t: "ops", seq: 1, client: "other", ops: [{ op: "delete", id: "x" }] });
  assert.deepEqual(h.tree.map((n) => n.id), ["a", "y"]);
  await h.session.flush();
  assert.equal(h.calls.length, 2);
  assert.deepEqual(h.calls[1].body.ops, [{ op: "set", id: "y", content: "y — a whole paragraph", base: "y" }]);
  assert.notEqual(h.calls[1].body.batch, h.calls[0].body.batch, "what is left is another batch");
  assert.deepEqual(h.reloads, ["page-a"], "the page is refetched");
  assert.ok(!h.status.some((s) => /rejected/.test(s)), "nothing was rejected");
  assert.equal(h.session.hasPending(), false);
});

test("a cycle drops the move and keeps the text edits of the same batch", async (t) => {
  const h = setup(t, async (url, init, body) => {
    if (h.calls.length === 1) {
      const index = body.ops.findIndex((op) => op.op === "move");
      throw httpError(400, "cannot move a block into its own subtree", { conflict: "cycle", index });
    }
    return ack(1, body.ops);
  });
  const [a, b] = h.tree;
  h.edit([{ ...a, content: "a edited", children: [{ ...b, position: "a0" }] }]);
  await h.session.flush();
  assert.deepEqual(h.calls[0].body.ops.map((op) => op.op), ["set", "move"]);
  assert.deepEqual(h.calls[1].body.ops, [{ op: "set", id: "a", content: "a edited", base: "a" }]);
  assert.deepEqual(h.reloads, ["page-a"]);
});

test("the refetch after a conflict keeps the unsent text: it is still sent, from the base it was typed on", async (t) => {
  let answerResend;
  const h = setup(t, async (url, init, body) => {
    if (h.calls.length === 1) throw httpError(403, "block x is outside this page", { conflict: "moved", index: body.ops.findIndex((op) => op.id === "x") });
    if (h.calls.length === 2) return new Promise((resolve) => { answerResend = () => resolve(ack(2, body.ops)); });
    return ack(3, body.ops);
  }, { tree: [block("x"), block("y", "y", {}, "a1")] });
  h.edit(setText(setText(h.tree, "x", "x!"), "y", "y typed"));
  h.fire();
  await settle();
  assert.equal(h.calls.length, 2, "the rest went out again");
  assert.deepEqual(h.reloads, ["page-a"]);
  // App refetches: the server still has y's old text; x moved to another page
  h.load("page-a", [block("y", "y", {}, "a1")], 1);
  assert.equal(h.tree[0].content, "y typed", "the refetched page shows what is not saved yet");
  answerResend();
  await settle();
  // one more keystroke: diffed against the typed text, not taken as saved
  h.edit(setText(h.tree, "y", "y typed!"));
  await h.session.flush();
  assert.deepEqual(h.calls[1].body.ops, [{ op: "set", id: "y", content: "y typed", base: "y" }]);
  assert.deepEqual(h.calls[2].body.ops, [{ op: "set", id: "y", content: "y typed!", base: "y typed" }]);
});

test("signed out (401) or another account signed in (409): nothing is dropped, it goes out after sign-in", async (t) => {
  let signedIn = false;
  const h = setup(t, async (url, init, body) => {
    if (!signedIn) {
      throw h.calls.length === 1
        ? httpError(409, 'This tab is signed in as "alice", but the browser session is now signed out.', { detail: "…" })
        : httpError(401, "401 Unauthorized");
    }
    return ack(1, body.ops);
  });
  h.edit(setText(h.tree, "a", "typed before the session changed"));
  await h.session.flush();
  h.fire(); // the retry: still signed out
  await settle();
  assert.equal(h.reloads.length, 0, "no reload over the unsaved text");
  assert.equal(h.session.hasPending(), true);
  assert.match(lastNotice(h), /you're signed out/);
  signedIn = true;
  h.fire();
  await settle();
  assert.equal(h.calls.length, 3);
  assert.equal(h.calls[2].body.batch, h.calls[0].body.batch, "the same batch, the same id");
  assert.deepEqual(h.calls[2].body.ops, [{ op: "set", id: "a", content: "typed before the session changed", base: "a" }]);
  assert.equal(h.session.hasPending(), false);
  assert.equal(lastNotice(h), "", "the notice goes once it is saved");
});

test("a batch refused for good is dropped alone; what was queued after it still goes, and the notice lasts", async (t) => {
  const first = deferred();
  const h = setup(t, async (url, init, body) => (h.calls.length === 1 ? first.promise : ack(1, body.ops)));
  h.edit(setText(h.tree, "a", "a1"));
  h.fire(); // the first batch is out
  h.edit(setText(h.tree, "b", "b1")); // queued behind it
  first.reject(httpError(403, "you can only view this workspace", { detail: "you can only view this workspace" }));
  await settle();
  h.fire();
  await settle();
  assert.deepEqual(h.calls[1].body.ops, [{ op: "set", id: "b", content: "b1", base: "b" }]);
  assert.equal(lastNotice(h, "rejected"), "Save rejected: you can only view this workspace");
  assert.deepEqual(h.reloads, ["page-a"]);
  assert.equal(h.session.hasPending(), false);
});

test("every op on a block deleted meanwhile goes at once: one round trip, one refetch", async (t) => {
  const h = setup(t, async (url, init, body) => {
    const index = body.ops.findIndex((op) => op.parent === "Y" || op.id === "Y");
    if (index >= 0) throw httpError(404, "no such parent: Y", { missing: "Y", conflict: "missing", index });
    return ack(2, body.ops);
  }, { tree: [block("Y", "parent Y"), block("Z", "Z", {}, "a1")] });
  // a paste under Y (one pasted note with a child of its own), and an edit of Z
  const pasted = Array.from({ length: 40 }, (_, i) => block(`s${i}`, `pasted ${i}`));
  pasted[0] = { ...pasted[0], children: [block("g0", "grandchild")] };
  h.edit([{ ...h.tree[0], children: pasted }, { ...h.tree[1], content: "Z edited" }]);
  // Y is deleted elsewhere; the delete reaches us before our batch goes
  h.message({ t: "ops", seq: 1, client: "other", ops: [{ op: "delete", id: "Y" }] });
  await h.session.flush();
  assert.equal(h.calls.length, 2, "one refusal, then what is left");
  assert.deepEqual(h.calls[1].body.ops, [{ op: "set", id: "Z", content: "Z edited", base: "Z" }]);
  assert.deepEqual(h.reloads, ["page-a"], "refetched once");
  assert.match(lastNotice(h, "rejected"), /couldn't be saved/, "the lost notes are not dropped silently");
  assert.equal(h.session.hasPending(), false);
});

test("my delete racing a move into it: the moved note goes out ahead of the delete, typing and all", async (t) => {
  // The server holds P5 under P0 (someone moved it there): a batch deleting
  // P0 takes P5 along — unless P5 leaves P0 first.
  const h = setup(t, async (url, init, body) => {
    const ops = body.ops;
    const del = ops.findIndex((op) => op.op === "delete" && op.id === "P0");
    const out = ops.findIndex((op) => op.op === "move" && op.id === "P5" && op.parent === "page-a");
    const set = ops.findIndex((op) => op.op === "set" && op.id === "P5");
    if (del >= 0 && set > del && !(out >= 0 && out < del)) {
      throw httpError(404, "no such block: P5", { missing: "P5", conflict: "missing", index: set });
    }
    return ack(2, ops);
  }, { tree: [block("P0", "parent"), block("P5", "note", {}, "a1")] });
  h.edit(h.tree.filter((n) => n.id !== "P0")); // we delete P0 …
  h.edit(setText(h.tree, "P5", "note — typed on")); // … and type in P5 (both unsent)
  // their move of P5 under P0 arrives; our base has no P0 any more: P5, our
  // edit of it on its way, stays where we have it
  h.message({ t: "ops", seq: 1, client: "other", ops: [{ op: "move", id: "P5", parent: "P0", position: "a0" }] });
  assert.deepEqual(h.tree.map((n) => n.id), ["P5"]);
  await h.session.flush();
  assert.equal(h.calls.length, 2);
  assert.deepEqual(h.calls[1].body.ops.map((op) => [op.op, op.id, op.parent]),
    [["insert", "P5", "page-a"], ["move", "P5", "page-a"], ["delete", "P0", undefined], ["set", "P5", undefined]]);
  assert.equal(h.session.hasPending(), false);
  assert.deepEqual(h.tree.map((n) => [n.id, n.content]), [["P5", "note — typed on"]]);
});

test("a block missing again after its rescue is a conflict: no growing batches, and the loss is told", async (t) => {
  const h = setup(t, async (url, init, body) => {
    const index = body.ops.findIndex((op) => op.op === "set" && op.id === "P5");
    if (index >= 0) throw httpError(404, "no such block: P5", { missing: "P5", conflict: "missing", index });
    return ack(1, body.ops);
  }, { tree: [block("P0", "parent"), block("P5", "note", {}, "a1")] });
  h.edit(h.tree.filter((n) => n.id !== "P0"));
  h.edit(setText(h.tree, "P5", "note — typed on"));
  await h.session.flush();
  const posts = h.calls.filter((c) => c.body);
  assert.deepEqual(posts.map((c) => c.body.ops.length), [2, 4, 1], "the rescue once, then only what can go");
  assert.deepEqual(posts[2].body.ops, [{ op: "delete", id: "P0" }]);
  assert.match(lastNotice(h, "rejected"), /couldn't be saved/);
  assert.equal(h.session.hasPending(), false);
});

test("the rescue budget is per trouble, not per page: a save that goes through restores it", async (t) => {
  let seq = 0;
  const h = setup(t, async (url, init, body) => {
    const set = body.ops.findIndex((op) => op.op === "set");
    if (!body.ops.some((op) => op.op === "insert")) {
      throw httpError(404, `no such block: ${body.ops[set].id}`, { missing: body.ops[set].id, conflict: "missing", index: set });
    }
    return ack(++seq, body.ops);
  }, { tree: [block("a")] });
  for (let i = 0; i < MAX_RESCUES + 3; i++) {
    h.edit(setText(h.tree, "a", `edit ${i}`));
    await h.session.flush();
  }
  const posts = h.calls.filter((c) => c.body);
  assert.equal(posts.length, 2 * (MAX_RESCUES + 3));
  assert.deepEqual(posts[posts.length - 1].body.ops.map((op) => op.op), ["insert", "move", "set"],
    "still rescued after more than MAX_RESCUES saves");
  assert.equal(h.session.hasPending(), false);
});

// --- ours ordered after theirs ------------------------------------------------------

test("a block both moved ends where the server has it: ours, ordered last, goes back on top", async (t) => {
  const answer = deferred();
  const h = setup(t, () => answer.promise, { tree: [block("a"), block("b", "b", {}, "a1"), block("x", "x", {}, "a2")] });
  const [a, b, x] = h.tree;
  h.edit([{ ...a, children: [{ ...x, position: "a0" }] }, b]); // we indent x under a
  const saving = h.session.flush();
  // their move of x under b was ordered before ours; it lands here after our move
  h.message({ t: "ops", seq: 1, client: "other", ops: [{ op: "move", id: "x", parent: "b", position: "a0" }] });
  assert.deepEqual(h.tree.map((n) => [n.id, n.children.map((c) => c.id)]), [["a", []], ["b", ["x"]]]);
  answer.resolve(ack(2, [{ op: "move", id: "x", parent: "a", position: "a0" }]));
  await saving;
  assert.deepEqual(h.tree.map((n) => [n.id, n.children.map((c) => c.id)]), [["a", ["x"]], ["b", []]]);
  assert.equal(h.session.hasPending(), false);
  h.edit([{ ...h.tree[0], content: "a!" }, h.tree[1]]);
  await h.session.flush();
  assert.deepEqual(h.calls[h.calls.length - 1].body.ops, [{ op: "set", id: "a", content: "a!", base: "a" }], "nothing but the new edit goes out");
});

test("a note deleted elsewhere that our rescue brought back shows here again, with our text", async (t) => {
  const second = deferred();
  const h = setup(t, async (url, init, body) => {
    if (h.calls.length === 1) throw httpError(404, "no such block: x", { missing: "x", conflict: "missing", index: 0 });
    return second.promise;
  }, { tree: [block("a"), block("x", "x", {}, "a1")] });
  h.edit(setText(h.tree, "x", "x — typed"));
  const saving = h.session.flush();
  await settle(); // refused: x goes out again ahead of the edit
  assert.deepEqual(h.calls[1].body.ops.map((op) => [op.op, op.id]), [["insert", "x"], ["move", "x"], ["set", "x"]]);
  // the delete that raced us arrives now and takes x off the screen …
  h.message({ t: "ops", seq: 1, client: "other", ops: [{ op: "delete", id: "x" }] });
  assert.deepEqual(h.tree.map((n) => n.id), ["a"]);
  // … but the server re-created it with our batch, ordered after that delete
  second.resolve(ack(2, [
    { op: "insert", id: "x", parent: "page-a", position: "a1", content: "x", props: {} },
    { op: "move", id: "x", parent: "page-a", position: "a1" },
    { op: "set", id: "x", content: "x — typed" },
  ]));
  await saving;
  assert.deepEqual(h.tree.map((n) => [n.id, n.content]), [["a", "a"], ["x", "x — typed"]]);
  assert.equal(h.session.hasPending(), false);
});

test("ours goes back on top also when its own fan-out and a catch-up bring it by after the ack", async (t) => {
  const rescueOps = [
    { op: "insert", id: "x", parent: "page-a", position: "a1", content: "x", props: {} },
    { op: "move", id: "x", parent: "page-a", position: "a1" },
    { op: "set", id: "x", content: "x — typed" },
  ];
  const log = deferred();
  const h = setup(t, async (url, init, body) => {
    if (url.includes("?since=")) return log.promise;
    if (h.calls.length === 1) throw httpError(404, "no such block: x", { missing: "x", conflict: "missing", index: 0 });
    return ack(2, rescueOps); // seq 1, the delete, hasn't reached this tab: the ack needs a catch-up
  }, { tree: [block("a"), block("x", "x", {}, "a1")] });
  h.edit(setText(h.tree, "x", "x — typed"));
  const saving = h.session.flush();
  await settle();
  assert.ok(h.calls.some((c) => c.url.includes("?since=0")), "catching up");
  h.message({ t: "ops", seq: 2, client: ME, ops: rescueOps }); // our own fan-out, while the log is on its way
  log.resolve({ seq: 2, batches: [{ seq: 1, client: "other", ops: [{ op: "delete", id: "x" }] }, { seq: 2, client: ME, ops: rescueOps }] });
  await saving;
  await settle();
  assert.deepEqual(h.tree.map((n) => [n.id, n.content]), [["a", "a"], ["x", "x — typed"]]);
});

test("a move this tab can't place (out of or into a block it deleted, unsent) refetches the page", async (t) => {
  const answer = deferred();
  const h = setup(t, () => answer.promise, { tree: [{ ...block("P"), children: [block("c")] }, block("q", "q", {}, "a1")] });
  h.edit(h.tree.filter((n) => n.id !== "P")); // we delete P, c with it
  h.session.flush();
  // someone moved c out of P before our delete reached the server: there, c survives
  h.message({ t: "ops", seq: 1, client: "other", ops: [{ op: "move", id: "c", parent: "page-a", position: "a2" }] });
  assert.deepEqual(h.reloads, ["page-a"]);
  // a move this tab can place refetches nothing
  h.message({ t: "ops", seq: 2, client: "other", ops: [{ op: "move", id: "q", parent: "page-a", position: "a3" }] });
  assert.deepEqual(h.reloads, ["page-a"]);
  // one moved into P goes with P on the server — and here too, no edit of ours on its way
  h.message({ t: "ops", seq: 3, client: "other", ops: [{ op: "move", id: "q", parent: "P", position: "a0" }] });
  assert.deepEqual(h.tree.map((n) => n.id), []);
  assert.deepEqual(h.reloads, ["page-a"]);
  answer.resolve(ack(4, [{ op: "delete", id: "P" }]));
  await h.session.flush();
  assert.equal(h.session.hasPending(), false);
});

// --- a page moved to Recently deleted ------------------------------------------------

test("edits to a page moved to Recently deleted wait, unsent and never dropped, and go once it is back", async (t) => {
  let trashed = true;
  const gone = [];
  const entry = { id: "page-a", title: "Page A" };
  const h = setup(t, async (url, init, body) => {
    if (trashed) throw httpError(404, "page is in Recently deleted", { detail: "page is in Recently deleted", trashed: entry });
    return ack(4, body.ops);
  });
  h.options.onGone = (page, g) => gone.push([page, g]);
  h.edit(setText(h.tree, "a", "typed as it went"));
  await h.session.flush();
  assert.deepEqual(gone, [["page-a", { trashed: entry }]]);
  assert.equal(h.timers.size, 0, "no retries while it is in Recently deleted");
  h.edit(setText(h.tree, "a", "typed as it went, and after"));
  await h.session.flush();
  assert.equal(h.timers.size, 0);
  assert.equal(h.calls.length, 1, "nothing more sent");
  assert.equal(h.session.hasPending(), true);
  assert.ok(!lastNotice(h), "the open page says so itself (onGone), not the save pill");
  assert.ok(!h.notices.some(([kind]) => kind === "rejected"), "nothing rejected");
  assert.deepEqual(h.reloads, []);
  // restored: the room's reload refetches; the fetched page lacks the edits, the overlay puts them back
  trashed = false;
  h.message({ t: "reload" });
  h.load("page-a", [block("a"), block("b", "b", {}, "a1")], 3);
  assert.equal(h.tree[0].content, "typed as it went, and after");
  assert.deepEqual(gone[gone.length - 1], ["page-a", null]);
  h.fire();
  await settle();
  await h.session.flush();
  assert.equal(h.calls[1].body.batch, h.calls[0].body.batch, "the parked batch, under its own id");
  assert.deepEqual(h.calls[2].body.ops, [{ op: "set", id: "a", content: "typed as it went, and after", base: "typed as it went" }]);
  assert.equal(h.session.hasPending(), false);
});

test("the room's `trashed` message parks the page's edits before any save is refused", async (t) => {
  const gone = [];
  const h = setup(t, async (url, init, body) => ack(2, body.ops));
  h.options.onGone = (page, g) => gone.push([page, g]);
  h.message({ t: "trashed" });
  assert.deepEqual(gone, [["page-a", { trashed: null }]]);
  h.edit(setText(h.tree, "a", "typed after"));
  assert.equal(h.timers.size, 0);
  await h.session.flush();
  assert.equal(h.calls.length, 0);
  h.session.pagehide();
  assert.equal(h.keepalive.length, 0, "no keepalive the server would refuse");
  h.load("page-b", [block("c")]);
  assert.match(lastNotice(h), /Recently deleted/, "seen from another page, the save pill says why edits wait");
  h.load("page-a", [block("a"), block("b", "b", {}, "a1")], 1); // restored and refetched
  await h.session.flush();
  assert.deepEqual(h.calls.map((c) => c.body.ops), [[{ op: "set", id: "a", content: "typed after", base: "a" }]]);
});

test("a flush, focus or `online` tries a waiting batch at once; failing there starts the retries over", async (t) => {
  let up = false;
  const h = setup(t, async (url, init, body) => { if (!up) throw new TypeError("Failed to fetch"); return ack(1, body.ops); });
  const waits = () => [...h.timers.values()].map((x) => x.ms);
  h.edit(setText(h.tree, "a", "typed offline"));
  await h.session.flush();
  h.fire(); await settle();
  h.fire(); await settle();
  assert.deepEqual(waits(), [4 * RETRY_MS]);
  await h.session.flush(); // leaving the page: tried at once, and it fails
  assert.deepEqual(waits(), [RETRY_MS], "not stretched further by the flush");
  h.session.retryNow(); // the window regains focus
  await settle();
  assert.deepEqual(waits(), [RETRY_MS]);
  assert.equal(lastNotice(h), "Not saved yet — the server can't be reached. Retrying…");
  up = true;
  h.session.retryNow(); // back online
  await settle();
  assert.equal(h.session.hasPending(), false);
  assert.equal(lastNotice(h), "");
  assert.ok(h.calls.every((c) => c.body.batch === h.calls[0].body.batch), "one batch id throughout");
});

// --- C2: batches the server takes -----------------------------------------------

test("a paste of 600 blocks goes out as batches of at most 500 ops, in order", async (t) => {
  const h = setup(t, async (url, init, body) => ack(h.calls.length, body.ops));
  const pasted = Array.from({ length: 600 }, (_, i) => ({ id: `p${i}`, content: `line ${i}`, properties: {}, children: [] }));
  h.edit([...h.tree, ...pasted]);
  await h.session.flush();
  assert.deepEqual(h.calls.map((c) => c.body.ops.length), [MAX_OPS, 100]);
  assert.deepEqual([...h.calls[0].body.ops, ...h.calls[1].body.ops].map((op) => op.id), pasted.map((p) => p.id));
  assert.notEqual(h.calls[0].body.batch, h.calls[1].body.batch);
  assert.equal(h.session.hasPending(), false);
});

test("text over the limit is held back with a notice, the editor stays open, and it goes once shortened", async (t) => {
  const h = setup(t, async (url, init, body) => ack(h.calls.length, body.ops));
  const long = "x".repeat(MAX_CONTENT + 1);
  const ops = h.edit(setText(h.tree, "a", long).map((n) => (n.id === "a" ? { ...n, properties: { color: "red" } } : n)));
  assert.deepEqual(ops, [{ op: "set", id: "a", props: { color: "red" } }], "the property change still goes, the text doesn't");
  assert.equal(h.session.tooLong("a"), true);
  assert.match(lastNotice(h), /too long to save/);
  // a refetch meanwhile keeps the long text on screen, and it is still not taken as saved
  await h.session.flush();
  h.load("page-a", [{ ...block("a"), properties: { color: "red" } }, block("b", "b", {}, "a1")], 1);
  assert.equal(h.tree[0].content, long);
  h.edit(setText(h.tree, "a", "short again"));
  await h.session.flush();
  assert.deepEqual(h.calls[h.calls.length - 1].body.ops, [{ op: "set", id: "a", content: "short again", base: "a" }]);
  assert.equal(h.session.tooLong("a"), false);
  assert.equal(lastNotice(h), "");
});

// --- C3 / C4: retries ------------------------------------------------------------

test("a failed batch keeps its id on every retry and goes at once when the socket is back", async (t) => {
  let up = false;
  const h = setup(t, async (url, init, body) => { if (!up) throw new TypeError("Failed to fetch"); return ack(1, body.ops); });
  h.edit(setText(h.tree, "a", "typed offline"));
  await h.session.flush();
  assert.deepEqual([...h.timers.values()].map((x) => x.ms), [RETRY_MS]);
  h.edit(setText(h.tree, "a", "typed offline, more")); // typing doesn't cut the wait short
  assert.deepEqual([...h.timers.values()].map((x) => x.ms), [RETRY_MS]);
  up = true;
  h.message({ t: "hello", client: ME, color: 0, seq: 0, peers: [] }); // reconnected
  await settle();
  assert.equal(h.calls[1].body.batch, h.calls[0].body.batch);
  assert.deepEqual(h.calls[1].body.ops, h.calls[0].body.ops);
  await h.session.flush();
  assert.notEqual(h.calls[2].body.batch, h.calls[0].body.batch, "the text typed since is the next batch");
  assert.deepEqual(h.calls[2].body.ops, [{ op: "set", id: "a", content: "typed offline, more", base: "typed offline" }]);
  assert.equal(h.session.hasPending(), false);
});

test("an insert the server already had converges on the block as the server has it", async (t) => {
  const h = setup(t, async (url, init, body) => ack(1, body.ops.map((op) => (op.id === "n"
    ? { ...op, parent: "a", content: "edited by someone else meanwhile", props: { color: "yellow" } } : op))));
  h.edit([...h.tree, { id: "n", content: "draft", properties: {}, children: [] }]);
  await h.session.flush();
  const n = h.tree[0].children.find((c) => c.id === "n");
  assert.deepEqual([n.content, n.properties], ["edited by someone else meanwhile", { color: "yellow" }]);
});

// --- E4: in-flight accounting ------------------------------------------------------

test("text folded into a queued property-only set is protected from remote text like any set", async (t) => {
  const answer = deferred();
  const h = setup(t, () => answer.promise, { tree: [block("b", "hello")] });
  h.message({ t: "hello", client: ME, color: 0, seq: 0, peers: [] });
  h.edit([{ ...h.tree[0], properties: { color: "red" } }]);
  h.edit([{ ...h.tree[0], content: "hello world MINE" }]);
  const q = [];
  pushOp(q, { op: "set", id: "b", props: { color: "red" } });
  assert.equal(pushOp(q, { op: "set", id: "b", content: "x", base: "hello" }).content, undefined, "pushOp names what it folded into");
  // another client's text for the block arrives before our batch is answered
  h.message({ t: "ops", seq: 1, client: "other", ops: [{ op: "set", id: "b", content: "hello THEIRS" }] });
  assert.equal(h.tree[0].content, "hello world MINE", "held back, not typed over");
  const saving = h.session.flush();
  answer.resolve(ack(2, [{ op: "set", id: "b", content: "hello THEIRS world MINE", props: { color: "red" } }]));
  await saving;
  assert.equal(h.tree[0].content, "hello THEIRS world MINE");
});

// --- E5: back on a page left mid-save ------------------------------------------------

test("a page reopened while its save is still retrying shows the edit and resumes that save", async (t) => {
  let fail = true;
  const h = setup(t, async (url, init, body) => {
    if (fail && url.includes("page-a")) throw httpError(500, "database is locked");
    return ack(1, body.ops.map((op) => (op.id === "a" ? { ...op, content: "a + typed, merged" } : op)));
  });
  h.edit(setText(h.tree, "a", "a + typed"));
  await h.session.flush(); // 500: a retry is armed
  h.load("page-b", [block("c")]); // leaving tries once more: 500 again
  await settle();
  // back to A before the retry: the fetch doesn't have the edit
  h.load("page-a", [block("a"), block("b", "b", {}, "a1")], 0);
  assert.equal(h.tree[0].content, "a + typed");
  fail = false;
  h.fire();
  await settle();
  assert.equal(h.session.hasPending(), false);
  assert.equal(h.tree[0].content, "a + typed, merged", "its answer lands on this visit's tree");
});

// --- E3: a refetch that failed ------------------------------------------------------

test("a failed refetch stops holding the socket's batches back", (t) => {
  const h = setup(t, async () => ({}));
  h.message({ t: "reload" });
  assert.deepEqual(h.reloads, ["page-a"]);
  h.message({ t: "ops", seq: 1, client: "other", ops: [{ op: "set", id: "b", content: "b from them" }] });
  assert.equal(h.tree[1].content, "b", "held while the reload is pending");
  h.session.reloadFailed("page-a");
  assert.equal(h.tree[1].content, "b from them");
});

test("a socket closed for lost access is not reopened", (t) => {
  const h = setup(t, async () => ({}));
  h.socket().onclose({ code: 4403 });
  assert.equal(h.timers.size, 0);
  assert.match(h.status[h.status.length - 1], /no longer have access/);
});

// --- E2: undo after a reload ------------------------------------------------------

test("a reload empties the undo stack; its snapshots would delete what it brought", () => {
  const N = (id, content = id, children = []) => ({ id, content, properties: {}, children, collapsed: false, editMode: false });
  const origin = new WeakMap();
  const o = { originOf: (tree) => origin.get(tree) };
  const first = [N("u1")];
  const s = createHistory(first);
  observeTree(s, [N("u1", "u1 edited")], o);
  assert.equal(s.undo.length, 1);
  const remote = [N("u1", "u1 edited"), N("r")];
  origin.set(remote, "remote");
  observeTree(s, remote, o);
  assert.equal(s.undo.length, 1, "another client's ops are no step, and keep the stack");
  const reloaded = [N("u1", "u1 edited"), N("r"), N("moved in", "a note moved here")];
  origin.set(reloaded, "load");
  observeTree(s, reloaded, o);
  assert.equal(s.undo.length, 0);
  clearHistory(s);
  assert.equal(s.redo.length, 0);
});

test("rebasing a snapshot over a move under a block it lacks keeps the block where it was", () => {
  const N = (id, content = id, children = [], position) => ({ id, content, properties: {}, children, position, collapsed: false, editMode: false });
  const PAGE = "PG";
  const snapshot = [N("R", "their long-standing note", [], "a0")]; // before we made P
  const pos = seedPositions(snapshot, new Map());
  pos.set("P", "a1");
  const before = [N("R", "their long-standing note", [], "a0"), N("P", "my new block", [], "a1")];
  const remote = [{ op: "move", id: "R", parent: "P", position: "a0" }]; // they drag R under P
  const current = applyOps(before, remote, PAGE, pos);
  assert.deepEqual(current.map((n) => [n.id, n.children.map((c) => c.id)]), [["P", ["R"]]]);
  const s = createHistory(current);
  s.undo = [{ tree: snapshot, caret: null, mark: 0 }];
  rebaseHistory(s, remote, { pageId: PAGE, pos, before });
  assert.deepEqual(s.undo[0].tree.map((n) => n.id), ["R"], "R stays in the snapshot");
  // undoing our creation of P now moves R out before P goes — R survives
  const undo = diffTrees(current, s.undo[0].tree, PAGE, pos);
  assert.deepEqual(undo.map((op) => [op.op, op.id]), [["move", "R"], ["delete", "P"]]);
  let restored = null;
  assert.equal(undoStep(s, { setBlocks: (tree) => { restored = tree; } }).kept, false);
  assert.deepEqual(restored.map((n) => [n.id, n.content]), [["R", "their long-standing note"]]);
  // an insert under a parent the tree lacks stays out; a known block keeps its place
  assert.deepEqual(applyOps(snapshot, [{ op: "insert", id: "Z", parent: "P", position: "a0", content: "z", props: {} }], PAGE, pos), snapshot);
  const kept = applyOps(snapshot, [{ op: "insert", id: "R", parent: "P", position: "a0", content: "R2", props: {} }], PAGE, pos);
  assert.deepEqual(kept.map((n) => [n.id, n.content]), [["R", "R2"]]);
});
