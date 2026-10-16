import test from "node:test";
import assert from "node:assert/strict";

// host.js reads the window once, at load: each case loads a fresh copy
// under its own fake window (the iPad shell's user script, or a browser).
let copy = 0;
async function load({ native, standalone = false } = {}) {
  const attrs = {};
  const posted = [];
  globalThis.document = { documentElement: { setAttribute: (k, v) => { attrs[k] = String(v); } } };
  globalThis.window = {
    matchMedia: (q) => ({ matches: standalone && q === "(display-mode: standalone)" }),
    navigator: {},
    ...(native ? {
      gammaNative: native,
      webkit: { messageHandlers: { gamma: { postMessage: (msg) => { posted.push(msg); return Promise.resolve({ taken: true }); } } } },
    } : {}),
  };
  const host = await import(`../src/shared/lib/host.js?copy=${++copy}`);
  return { host, attrs, posted };
}

// The user script's stub: what native sends before the bundle runs waits in `queue`.
function stub(fields) {
  const native = { platform: "ipad", version: "0.2.0", ink: true, pencil: true, servers: true, ...fields };
  native.receive = (msg) => new Promise((resolve, reject) => (native.queue ||= []).push({ msg, resolve, reject }));
  return native;
}

test("a browser has no host: nothing is posted and no attribute is set", async () => {
  const { host, attrs } = await load();
  assert.equal(host.nativeHost(), null);
  assert.equal(host.appShell(), null);
  await assert.rejects(host.postToHost({ type: "theme" }), /no native host/);
  assert.deepEqual(attrs, {});
});

test("the installed web app is a shell without a host", async () => {
  const { host, attrs } = await load({ standalone: true });
  assert.equal(host.nativeHost(), null);
  assert.equal(host.appShell(), "standalone");
  assert.deepEqual(attrs, { "data-shell": "standalone" });
});

test("under the iPad shell: the host, its attributes, and messages posted with their replies", async () => {
  const native = stub();
  const { host, attrs, posted } = await load({ native });
  assert.equal(host.nativeHost(), native);
  assert.equal(host.nativeHost().ink, true);
  assert.equal(host.appShell(), "ipad");
  assert.deepEqual(attrs, { "data-shell": "ipad", "data-host": "ipad" });
  assert.deepEqual(await host.postToHost({ type: "claim", pointerId: 3 }), { taken: true });
  assert.deepEqual(posted, [{ type: "claim", pointerId: 3 }]);
});

test("a declaration without a platform is no host", async () => {
  const { host } = await load({ native: { ink: true } });
  assert.equal(host.nativeHost(), null);
});

test("receive dispatches by type, resolves with the handler's answer, and drains the stub's queue", async () => {
  const native = stub();
  const early = native.receive({ type: "pencil", gesture: "tap", action: "switchEraser" });
  const { host } = await load({ native });
  assert.equal(native.queue, null, "the stub's queue is taken over");
  // The early message's type had no listener when it was drained: it resolves with nothing.
  assert.equal(await early, undefined);

  const seen = [];
  const off = host.onHostMessage("stroke", async (msg) => { seen.push(msg.pointerId); return { committed: true }; });
  assert.deepEqual(await native.receive({ type: "stroke", pointerId: 7, samples: [] }), { committed: true });
  assert.deepEqual(seen, [7]);
  // A type nobody listens for never hangs the host.
  assert.equal(await native.receive({ type: "future" }), undefined);
  // A throwing handler rejects the host's call.
  host.onHostMessage("pencil", () => { throw new Error("boom"); });
  await assert.rejects(native.receive({ type: "pencil" }), /boom/);
  off();
  assert.equal(await native.receive({ type: "stroke", pointerId: 8 }), undefined);
  assert.deepEqual(seen, [7]);
});

test("every message queued before load settles", async () => {
  const native = stub();
  const pending = [native.receive({ type: "stroke", pointerId: 1 }), native.receive({ type: "theme" })];
  await load({ native });
  assert.deepEqual(await Promise.all(pending), [undefined, undefined]);
});

test("unsubscribing an old listener leaves its replacement", async () => {
  const native = stub();
  const { host } = await load({ native });
  const offFirst = host.onHostMessage("pencil", () => "first");
  host.onHostMessage("pencil", () => "second");
  offFirst();
  assert.equal(await native.receive({ type: "pencil" }), "second");
});
