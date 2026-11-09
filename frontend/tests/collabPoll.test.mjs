// The page session without a socket (collabSession.js `fallback`): socket
// attempts that end without a hello start a poll of the op log every
// POLL_MS, whose answers go through the same ordered inbox; the threads,
// the build fence, a reload, a page in Recently deleted, refusals, a hidden
// tab, the minute's probe of the socket and its hello ending the poll, a
// page switch. Fakes for HTTP, the socket and timers (on a clock of their
// own: h.advance(ms) fires what falls due, in order). The socket's own
// paths are collabSession.test.mjs; the browser's is the e2e collab step
// with a blocked socket.
import assert from "node:assert/strict";
import { test } from "node:test";
import { applyOps } from "../src/shared/model/blockOps.js";
import {
  MAX_RETRY_MS, POLL_AFTER_FAILS, POLL_COMMENTS_EVERY, POLL_MS, RETRY_MS, SOCKET_HELLO_MS, SOCKET_PROBE_MS,
  createCollabSession,
} from "../src/collaboration/collabSession.js";

const ME = "this-client";
const BUILD = { version: "1.2.3", commit: "abc1234", label: "v1.2.3" };
const block = (id, content = id) => ({ id, content, properties: {}, children: [], position: "a0" });
const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const httpError = (status) => Object.assign(new Error(`${status}`), { status });

// The server's side: the page's op log, and what a read of it answers
// (the log as it stood when the read was asked). `fail`: a status (or an
// Error) the next reads fail with; `hold`: a deferred the next read waits
// on before it answers.
function server() {
  const srv = { log: [], fail: null, hold: null, build: BUILD };
  srv.add = (ops, client = "other") => {
    const b = { seq: srv.log.length + 1, ops, client, at: 0, actor: "" };
    srv.log.push(b);
    return b;
  };
  srv.api = async (url, init) => {
    if (init?.method === "POST") {
      if (srv.fail) throw typeof srv.fail === "number" ? httpError(srv.fail) : srv.fail;
      const body = JSON.parse(init.body);
      return { ...srv.add(body.ops, body.client), cursor: null };
    }
    const since = Number(new URL(url, "http://x").searchParams.get("since"));
    const answer = { seq: srv.log.length, batches: srv.log.filter((b) => b.seq > since), build: srv.build };
    if (srv.hold) await srv.hold.promise;
    if (srv.fail) throw typeof srv.fail === "number" ? httpError(srv.fail) : srv.fail;
    return answer;
  };
  return srv;
}

function setup(t, { canWrite = true, fallback = true, srv = server() } = {}) {
  const h = {
    srv, calls: [], remote: [], reloads: [], status: [], notices: [], comments: [], builds: [], sockets: [],
    timers: new Map(), now: 0, hidden: false, me: null, peers: [], vault: 0,
  };
  let timerId = 0;
  const options = {
    pageId: "page-a", canWrite,
    onRemoteOps: (ops, page, pos) => { h.remote.push(...ops); h.tree = applyOps(h.tree, ops, page, pos); },
    onReload: (page) => h.reloads.push(page),
    onStatus: (text) => h.status.push(text),
    onSaveNotice: (text, kind) => h.notices.push([kind, text]),
    onComments: (msg) => h.comments.push(msg),
    onVault: () => { h.vault += 1; },
    onBuild: (build) => h.builds.push(build),
  };
  h.session = createCollabSession({
    clientId: ME,
    api: (path, init) => {
      h.calls.push({ url: path, method: init?.method || "GET", at: h.now });
      return srv.api(path, init);
    },
    openSocket: (pageId) => {
      const ws = { pageId, readyState: 1, sent: [], closed: false, send(m) { ws.sent.push(JSON.parse(m)); }, close() { ws.closed = true; } };
      h.sockets.push(ws);
      return ws;
    },
    keepalivePost: () => {},
    opts: () => options,
    onPeers: (p) => { h.peers = p; },
    onMe: (m) => { h.me = m; },
    fallback,
    hidden: () => h.hidden,
    timers: {
      set: (fn, ms) => { h.timers.set(++timerId, { fn, ms, at: h.now + ms }); return timerId; },
      clear: (id) => h.timers.delete(id),
    },
  });
  h.options = options;
  // Fire every timer due within `ms`, earliest first, letting each one's
  // promises settle before the next.
  h.advance = async (ms) => {
    const end = h.now + ms;
    for (;;) {
      let next = null;
      for (const [id, x] of h.timers) if (x.at <= end && (!next || x.at < next[1].at)) next = [id, x];
      if (!next) break;
      h.timers.delete(next[0]);
      h.now = next[1].at;
      next[1].fn();
      await settle();
    }
    h.now = end;
    await settle();
  };
  h.waits = () => [...h.timers.values()].map((x) => x.ms).sort((a, b) => a - b);
  h.socket = () => h.sockets[h.sockets.length - 1];
  h.message = (msg) => h.socket().onmessage({ data: JSON.stringify(msg) });
  h.hello = (seq = h.srv.log.length) => h.message({ t: "hello", client: ME, seq, peers: [{ client: ME }], build: BUILD });
  h.drop = () => h.socket().onclose({ code: 1006 }); // an attempt that ends without a hello
  const reads = () => h.calls.filter((c) => c.method === "GET" && c.url.includes("/ops?since="));
  h.polls = () => reads().map((c) => c.url);
  h.pollTimes = () => reads().map((c) => c.at);
  h.load = (page, tree, seq = 0) => { options.pageId = page; h.tree = tree; h.session.commit(tree, { isLoad: true, seq }); };
  h.edit = (tree) => { h.tree = tree; h.session.commit(tree); };
  h.load("page-a", [block("a"), { ...block("b"), position: "a1" }]);
  h.session.connect("page-a");
  t.after(() => h.session.disconnect());
  return h;
}

// Two attempts in a row without a hello: the poll is on and has asked once.
async function polling(h) {
  for (let i = 0; i < POLL_AFTER_FAILS; i++) {
    h.drop();
    await h.advance(i < POLL_AFTER_FAILS - 1 ? 1000 : 0);
  }
  assert.equal(h.polls().length, 1, "the poll's first read");
}

test("without fallback no hello deadline is armed and closes poll nothing; the backoff is as it was", async (t) => {
  const h = setup(t, { fallback: false });
  assert.deepEqual(h.waits(), [], "no deadline for the hello");
  const waits = [];
  for (let i = 0; i < 5; i++) {
    h.drop();
    waits.push(...h.waits());
    await h.advance(waits.at(-1));
  }
  assert.deepEqual(waits, [1000, 2000, 4000, 8000, 15000]);
  assert.deepEqual(h.polls(), []);
  assert.equal(h.sockets.length, 6);
});

test("two attempts without a hello start the poll; a hello between them starts the count over", async (t) => {
  const h = setup(t);
  assert.deepEqual(h.waits(), [SOCKET_HELLO_MS], "the first attempt's hello deadline");
  h.drop();
  await h.advance(1000);
  h.hello();
  assert.equal(h.me.connected, true);
  h.drop(); // a close after a hello: no failure
  await h.advance(1000);
  h.drop(); // one failure: not yet
  await h.advance(2000);
  assert.deepEqual(h.polls(), [], "one failure since the hello polls nothing");
  h.drop(); // the second in a row
  await h.advance(0);
  assert.deepEqual(h.polls(), ["/pages/page-a/ops?since=0"]);
});

test("an attempt silent for SOCKET_HELLO_MS is closed and counted", async (t) => {
  const h = setup(t);
  h.drop();
  await h.advance(1000);
  const silent = h.socket();
  await h.advance(SOCKET_HELLO_MS - 1);
  assert.equal(silent.closed, false);
  assert.deepEqual(h.polls(), []);
  await h.advance(1);
  assert.equal(silent.closed, true, "given up");
  assert.equal(silent.onmessage, null, "and no longer heard");
  assert.deepEqual(h.polls(), ["/pages/page-a/ops?since=0"], "the second failure starts the poll");
});

test("the socket's backoff starts over at a hello, not when the socket opens", async (t) => {
  const h = setup(t, { fallback: false });
  h.drop();
  await h.advance(1000);
  h.drop();
  await h.advance(2000);
  h.socket().onopen?.(); // a proxy that lets the upgrade through and passes nothing
  h.drop();
  assert.deepEqual(h.waits(), [4000], "an open without a hello leaves the backoff growing");
  await h.advance(4000);
  h.hello();
  h.drop();
  assert.deepEqual(h.waits(), [1000], "a hello starts it over");
});

test("answers apply the batches in order and the next read asks since the new seq; me connected, the build to the fence", async (t) => {
  const h = setup(t);
  h.srv.add([{ op: "set", id: "a", content: "first" }]);
  await polling(h);
  assert.deepEqual(h.remote.map((op) => op.content), ["first"]);
  assert.equal(h.me.connected, true, "live over HTTP");
  assert.equal(h.me.via, "poll", "App's agent reveal refetches: the edit lands with the next read");
  assert.deepEqual(h.builds, [BUILD]);
  h.srv.add([{ op: "set", id: "a", content: "second" }]);
  h.srv.add([{ op: "set", id: "b", content: "third" }]);
  await h.advance(POLL_MS - 1);
  assert.equal(h.polls().length, 1, "every POLL_MS");
  await h.advance(1);
  assert.deepEqual(h.polls().slice(1), ["/pages/page-a/ops?since=1"]);
  assert.deepEqual(h.remote.map((op) => op.content), ["first", "second", "third"]);
  assert.deepEqual([h.tree[0].content, h.tree[1].content], ["second", "third"]);
  await h.advance(POLL_MS);
  assert.equal(h.polls().at(-1), "/pages/page-a/ops?since=3");
  assert.deepEqual(h.builds, [BUILD, BUILD, BUILD], "every answer's build");
});

test("a read-only session gets the edits too", async (t) => {
  const h = setup(t, { canWrite: false });
  h.srv.add([{ op: "set", id: "b", content: "theirs" }]);
  await polling(h);
  assert.equal(h.tree[1].content, "theirs");
  assert.ok(h.calls.every((c) => c.method === "GET"), "and sends nothing");
});

test("a run's first answer refetches the threads and the vault's standing and sends a waiting save; every POLL_COMMENTS_EVERY answers again; a hidden tab asks nothing", async (t) => {
  const h = setup(t);
  h.srv.fail = new Error("offline");
  h.edit([block("a", "mine"), { ...block("b"), position: "a1" }]);
  await h.advance(400); // the typing debounce: the save fails, its retry waits RETRY_MS
  assert.equal(h.session.hasPending(), true);
  h.srv.fail = null;
  await polling(h);
  assert.deepEqual(h.comments, [null], "the threads, as a hello would");
  assert.equal(h.vault, 1, "and the vault's standing, as a hello would");
  assert.equal(h.session.hasPending(), false, "the waiting save went at once");
  for (let i = 0; i < POLL_COMMENTS_EVERY - 1; i++) await h.advance(POLL_MS);
  assert.deepEqual(h.comments, [null], `not before the ${POLL_COMMENTS_EVERY}th answer after`);
  await h.advance(POLL_MS);
  assert.deepEqual(h.comments, [null, null], "about every 30 s");
  assert.equal(h.vault, 2, "the vault's standing with them");
  // hidden: nothing is asked, however long
  const asked = h.polls().length;
  h.hidden = true;
  await h.advance(10 * POLL_MS);
  assert.equal(h.polls().length, asked);
  h.hidden = false;
  await h.advance(POLL_MS);
  assert.equal(h.polls().length, asked + 1);
  assert.deepEqual(h.comments, [null, null, null], "showing again starts a run: the threads again");
});

test("a 410 or a reload batch refetches the page and polls nothing until its load, then since the load's seq; a failed refetch resumes too", async (t) => {
  const h = setup(t);
  await polling(h);
  h.srv.fail = 410;
  await h.advance(POLL_MS);
  assert.deepEqual(h.reloads, ["page-a"]);
  let asked = h.polls().length;
  await h.advance(5 * POLL_MS);
  assert.equal(h.polls().length, asked, "nothing while the reload is on its way");
  h.srv.fail = null;
  for (let i = 0; i < 7; i++) h.srv.add([{ op: "set", id: "a", content: `v${i}` }]);
  h.load("page-a", [block("a", "v6"), { ...block("b"), position: "a1" }], 7);
  await h.advance(POLL_MS);
  assert.equal(h.polls().at(-1), "/pages/page-a/ops?since=7");
  // a reload in the log (a backup restored)
  h.srv.add([{ op: "reload" }]);
  await h.advance(POLL_MS);
  assert.deepEqual(h.reloads, ["page-a", "page-a"]);
  asked = h.polls().length;
  await h.advance(5 * POLL_MS);
  assert.equal(h.polls().length, asked);
  h.session.reloadFailed("page-a"); // App could not refetch: it keeps the tree and tries again
  await h.advance(POLL_MS);
  assert.equal(h.polls().length, asked + 1, "the reads go on");
});

test("a 404 refetches the page once and backs off; an answer while the page is parked means it is back", async (t) => {
  const h = setup(t);
  await polling(h);
  h.srv.fail = 404;
  await h.advance(POLL_MS);
  assert.deepEqual(h.reloads, ["page-a"]);
  h.session.gone("page-a", { id: "page-a" }); // App's refetch: 404 with its trash entry
  const asked = h.polls().length;
  await h.advance(RETRY_MS - 1);
  assert.equal(h.polls().length, asked, "backed off");
  await h.advance(1);
  assert.equal(h.polls().length, asked + 1);
  assert.deepEqual(h.reloads, ["page-a"], "once per streak");
  h.srv.fail = null; // restored
  await h.advance(2 * RETRY_MS);
  assert.deepEqual(h.reloads, ["page-a", "page-a"], "back: refetched");
});

for (const status of [401, 403]) {
  test(`a ${status} stops the poll and the socket`, async (t) => {
    const h = setup(t);
    await polling(h);
    h.srv.fail = status;
    await h.advance(POLL_MS);
    assert.deepEqual(h.waits(), [], "nothing armed: no read, no attempt");
    assert.equal(h.me.connected, false);
    assert.deepEqual(h.status, status === 403 ? ["You no longer have access to this page."] : []);
    const [asked, sockets] = [h.polls().length, h.sockets.length];
    await h.advance(10 * SOCKET_PROBE_MS);
    assert.deepEqual([h.polls().length, h.sockets.length], [asked, sockets]);
  });
}

test("a failed read backs off from RETRY_MS to MAX_RETRY_MS, says not connected, and puts the socket back on its short backoff", async (t) => {
  const h = setup(t);
  await polling(h);
  await h.advance(2000); // the third attempt (its backoff was armed before any read answered)
  h.drop();
  assert.ok(h.waits().includes(SOCKET_PROBE_MS), "polls answer: the next attempt is the minute's probe");
  h.srv.fail = new Error("offline");
  await h.advance(POLL_MS);
  assert.equal(h.me.connected, false);
  assert.deepEqual(h.waits(), [RETRY_MS, 4000], "the read's retry, and the socket back on its backoff");
  // (the socket's attempts meanwhile get no hello: each is given up after SOCKET_HELLO_MS)
  const from = h.pollTimes().length - 1;
  await h.advance(3000 + 6000 + 12000 + 24000 + 48000 + 60000 + 60000);
  const at = h.pollTimes().slice(from);
  assert.deepEqual(at.slice(1).map((x, i) => x - at[i]),
    [RETRY_MS, 2 * RETRY_MS, 4 * RETRY_MS, 8 * RETRY_MS, 16 * RETRY_MS, MAX_RETRY_MS, MAX_RETRY_MS]);
  assert.ok(h.sockets.length > 8, "the socket kept trying on its own backoff");
});

test("once polls answer a failed attempt waits SOCKET_PROBE_MS; retryNow({socket}) tries at once, retryNow() reads at once", async (t) => {
  const h = setup(t);
  await polling(h);
  await h.advance(2000);
  h.drop();
  assert.ok(h.waits().includes(SOCKET_PROBE_MS));
  assert.equal(h.me.connected, true, "polls answer: the probe's close leaves the page live");
  const sockets = h.sockets.length;
  // the window regaining focus: a read now, the socket's schedule left alone
  await h.advance(500);
  const asked = h.polls().length;
  h.session.retryNow();
  await h.advance(0);
  assert.equal(h.polls().length, asked + 1);
  assert.equal(h.sockets.length, sockets);
  assert.ok(h.waits().includes(SOCKET_PROBE_MS), "the probe still armed");
  // the browser's `online`: the socket now
  h.session.retryNow({ socket: true });
  assert.equal(h.sockets.length, sockets + 1);
  assert.ok(!h.waits().includes(SOCKET_PROBE_MS), "instead of the probe");
  h.session.retryNow({ socket: true });
  assert.equal(h.sockets.length, sockets + 1, "not while one is connecting");
});

test("a probe's hello stops the poll: no read armed, the read on its way ignored, and the hello's catch-up follows it", async (t) => {
  const h = setup(t);
  await polling(h);
  await h.advance(2000);
  h.drop();
  h.srv.hold = deferred(); // the next read hangs
  await h.advance(POLL_MS);
  const asked = h.polls().length;
  h.srv.add([{ op: "set", id: "a", content: "while switching" }]);
  await h.advance(SOCKET_PROBE_MS - POLL_MS); // the probe
  assert.equal(h.polls().length, asked, "no read beside the one on its way");
  h.hello(1);
  assert.equal(h.me.connected, true);
  assert.equal(h.me.via, "socket");
  assert.deepEqual(h.waits(), [], "no read armed");
  const builds = h.builds.length;
  const hold = h.srv.hold;
  h.srv.hold = null;
  hold.resolve();
  await settle();
  assert.equal(h.builds.length, builds, "the late answer is ignored");
  assert.deepEqual(h.polls().slice(asked), ["/pages/page-a/ops?since=0"], "the hello's catch-up");
  assert.equal(h.tree[0].content, "while switching");
  await h.advance(10 * POLL_MS);
  assert.equal(h.polls().length, asked + 1, "and nothing more");
});

test("a hello while an ack's gap catch-up is on its way: the hello's catch-up follows it and reaches the hello's seq", async (t) => {
  const h = setup(t);
  await polling(h);
  h.srv.add([{ op: "set", id: "a", content: "theirs" }]); // seq 1, not read yet
  h.srv.hold = deferred(); // the gap's read hangs
  h.edit([block("a"), { ...block("b", "mine"), position: "a1" }]);
  await h.advance(1000); // the save: its ack (seq 2) leaves a gap
  const asked = h.polls().length;
  assert.equal(h.polls()[asked - 1], "/pages/page-a/ops?since=0", "the gap's catch-up");
  h.srv.add([{ op: "set", id: "b", content: "theirs too" }]); // seq 3
  h.hello(3);
  const hold = h.srv.hold;
  h.srv.hold = null;
  hold.resolve();
  await h.advance(100);
  assert.deepEqual(h.polls().slice(asked), ["/pages/page-a/ops?since=2"], "the hello's catch-up after the gap's");
  assert.deepEqual(h.remote.map((op) => op.content), ["theirs", "theirs too"]);
});

test("a page switch while polling asks nothing for the old page; the new one is read once loaded, beside its socket attempt; disconnect stops the poll", async (t) => {
  const h = setup(t);
  await polling(h);
  const asked = h.polls().length;
  h.session.disconnect(); // what usePageCollab does when the page changes
  h.options.pageId = "page-b";
  h.session.connect("page-b");
  assert.equal(h.socket().pageId, "page-b", "the new page's socket attempt");
  await h.advance(3 * POLL_MS);
  assert.equal(h.polls().length, asked, "not loaded yet: nothing asked, of either page");
  h.load("page-b", [block("p")], 5);
  await h.advance(POLL_MS);
  assert.deepEqual(h.polls().slice(asked), ["/pages/page-b/ops?since=5"]);
  h.session.disconnect();
  await h.advance(10 * POLL_MS);
  assert.equal(h.polls().length, asked + 1);
  assert.deepEqual(h.waits(), []);
});
