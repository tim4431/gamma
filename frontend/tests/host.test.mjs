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

// The user script's stub: what native sends before the bundle runs waits in
// `queue`. The flags are HostInfo's (ipad/GammaIPad/Shell/Bridge.swift):
// host.js hands them over as declared, and the web acts on every one but
// inkLooks, which only says what the shell's live line can draw.
const FLAGS = { ink: true, inkLooks: ["pressure", "dash", "blend", "stabilize", "pencil", "calligraphy", "snap"], hold: true, pencil: true,
  pencilPhases: true, pencilRoll: true, haptics: true, servers: true };
function stub(fields) {
  const native = { platform: "ipad", version: "0.2.0", ...FLAGS, ...fields };
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

test("the host's flags are read as declared, and a flag it leaves out is no capability", async () => {
  const { host } = await load({ native: stub() });
  const h = host.nativeHost();
  for (const [flag, value] of Object.entries(FLAGS)) assert.deepEqual(h[flag], value, flag);
  const { host: older } = await load({ native: stub({ inkLooks: undefined, hold: undefined, pencilPhases: undefined, haptics: undefined }) });
  for (const flag of ["inkLooks", "hold", "pencilPhases", "haptics"]) assert.ok(!older.nativeHost()[flag], `an older shell: no ${flag}`);
});

test("hold and squeeze from native: answered by their listeners, and at once with nothing on a page without one", async () => {
  const native = stub();
  const early = native.receive({ type: "hold", pointerId: 4, samples: [[1, 2, 0.5, 0, 50, 90]] });
  const { host, posted } = await load({ native });
  // no hold listener when the queue was drained: the shell reads nothing as {}
  assert.equal(await early, undefined);
  host.onHostMessage("hold", ({ samples }) => (samples.length > 1 ? { shape: { kind: "line", params: { x1: 0, y1: 0, x2: 1, y2: 1 } } } : {}));
  assert.deepEqual(await native.receive({ type: "hold", pointerId: 4, samples: [[0, 0, 0.5, 0], [1, 1, 0.5, 700]] }),
    { shape: { kind: "line", params: { x1: 0, y1: 0, x2: 1, y2: 1 } } });
  assert.deepEqual(await native.receive({ type: "hold", pointerId: 4, samples: [[0, 0, 0.5, 0]] }), {});
  assert.equal(await native.receive({ type: "squeeze", phase: "began", action: "switchEraser" }), undefined, "no squeeze listener yet");
  const phases = [];
  host.onHostMessage("squeeze", ({ phase }) => { phases.push(phase); });
  host.onHostMessage("pencil", ({ gesture, phase }) => { phases.push(`${gesture}:${phase ?? "-"}`); });
  await native.receive({ type: "squeeze", phase: "began", action: "switchEraser" });
  await native.receive({ type: "pencil", gesture: "squeeze", action: "switchEraser", phase: "ended" });
  await native.receive({ type: "pencil", gesture: "tap", action: "switchEraser" });
  await native.receive({ type: "squeeze", phase: "cancelled", action: "switchEraser" });
  assert.deepEqual(phases, ["began", "squeeze:ended", "tap:-", "cancelled"]);
  // the web's haptic goes out like any other message, with its reply
  assert.deepEqual(await host.postToHost({ type: "haptic", kind: "snap", at: { x: 10, y: 20 } }), { taken: true });
  assert.deepEqual(posted.at(-1), { type: "haptic", kind: "snap", at: { x: 10, y: 20 } });
});
