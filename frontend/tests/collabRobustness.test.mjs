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
import { MAX_CONTENT, MAX_OPS, RETRY_MS, createCollabSession } from "../src/collaboration/collabSession.js";
import { clearHistory, createHistory, observeTree, rebaseHistory } from "../src/editor/blockHistory.js";

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
  assert.match(lastNotice(h), /Save failed/);
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
  let current = [N("R", "their long-standing note", [], "a0"), N("P", "my new block", [], "a1")];
  const remote = [{ op: "move", id: "R", parent: "P", position: "a0" }]; // they drag R under P
  current = applyOps(current, remote, PAGE, pos);
  assert.deepEqual(current.map((n) => [n.id, n.children.map((c) => c.id)]), [["P", ["R"]]]);
  const s = createHistory(current);
  s.undo = [{ tree: snapshot, caret: null }];
  rebaseHistory(s, (tree) => applyOps(tree, remote, PAGE, pos));
  assert.deepEqual(s.undo[0].tree.map((n) => n.id), ["R"], "R stays in the snapshot");
  // undoing our creation of P now moves R out before P goes — R survives
  const undo = diffTrees(current, s.undo[0].tree, PAGE, pos);
  assert.deepEqual(undo.map((op) => [op.op, op.id]), [["move", "R"], ["delete", "P"]]);
  // an insert under a parent the tree lacks stays out; a known block keeps its place
  assert.deepEqual(applyOps(snapshot, [{ op: "insert", id: "Z", parent: "P", position: "a0", content: "z", props: {} }], PAGE, pos), snapshot);
  const kept = applyOps(snapshot, [{ op: "insert", id: "R", parent: "P", position: "a0", content: "R2", props: {} }], PAGE, pos);
  assert.deepEqual(kept.map((n) => [n.id, n.content]), [["R", "R2"]]);
});
