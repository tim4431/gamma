import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

// host.js reads the window once, at load: each case loads a fresh copy
// under its own fake window (the iPad shell's user script, the Android
// app's document-start script, or a browser). The iPad's `native` comes
// with WebKit's reply handler; `before(window)` runs first, as a
// document-start script does, on a window with no WebKit.
let copy = 0;
async function load({ native, standalone = false, before } = {}) {
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
  before?.(globalThis.window);
  const host = await import(`../src/shared/lib/host.js?copy=${++copy}`);
  return { host, attrs, posted };
}

// The Android app's document-start script, as the app injects it
// (shell/HostBridge.kt): the file with its HostInfo in place of the
// __GAMMA_HOST_INFO__ placeholder (Kotlin's String.replace), beside the
// message port `gammaAndroidHost`.
const ANDROID_HOST_JS = fs.readFileSync(new URL("../../android/app/src/main/assets/shell/host.js", import.meta.url), "utf8");
const ANDROID_INFO = { haptics: true, hold: true, ink: true, inkLooks: ["pressure", "dash", "blend", "stabilize", "pencil", "calligraphy", "snap"],
  keepAwake: true, openFiles: true, pencil: true, pencilGestures: ["tap", "squeeze"], platform: "android", save: true, shellBar: true, version: "0.1.0" };
// Native's end of that port: JSON strings both ways. A post is answered on
// a later turn with `answer(msg)`'s value, or its throw as an error reply;
// `send(msg)` is a message from native, resolving with what the page posts
// back for it. `said` is every message the page posted.
function androidPort(answer) {
  const listeners = [];
  const said = [];
  const results = new Map();
  let next = 0;
  const deliver = (m) => { for (const fn of listeners) fn({ data: JSON.stringify(m) }); };
  const port = {
    postMessage(data) {
      assert.equal(typeof data, "string", "the port carries strings");
      const m = JSON.parse(data);
      said.push(m);
      if (m.id != null) {
        setTimeout(() => {
          let value;
          try { value = answer(m.msg); } catch (err) { deliver({ reply: m.id, error: err.message }); return; }
          deliver({ reply: m.id, value });
        });
      }
      if (m.result != null) results.get(m.result)?.(m);
    },
    addEventListener(type, fn) { if (type === "message") listeners.push(fn); },
  };
  const send = (msg) => new Promise((resolve) => { const n = ++next; results.set(n, resolve); deliver({ send: n, msg }); });
  return { port, said, send };
}
const runAndroidScript = (window, port) => {
  window.top = window;
  window.gammaAndroidHost = port;
  new Function(ANDROID_HOST_JS.replaceAll("__GAMMA_HOST_INFO__", JSON.stringify(ANDROID_INFO)))();
};

// The user script's stub: what native sends before the bundle runs waits in
// `queue`. The flags are HostInfo's (ipad/GammaIPad/Shell/Bridge.swift):
// host.js hands them over as declared, and the web acts on every one but
// inkLooks, which only says what the shell's live line can draw.
const FLAGS = { ink: true, inkLooks: ["pressure", "dash", "blend", "stabilize", "pencil", "calligraphy", "snap"], hold: true, pencil: true,
  pencilPhases: true, pencilRoll: true, haptics: true, openFiles: true, shellBar: true, editMenu: true, keepAwake: true };
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

test("under the Android shell: its script's post carries the messages and the replies, and native's messages come back answered", async () => {
  const { port, said, send } = androidPort((msg) => {
    if (msg.type === "refuse") throw new Error("Gamma for Android has no refuse.");
    return msg.type === "claim" ? { taken: true } : msg.type === "nothing" ? null : {};
  });
  let early;
  const { host, attrs } = await load({ before: (w) => {
    runAndroidScript(w, port);
    // sent before the bundle runs: the script's stub queues it
    early = send({ type: "pencil", gesture: "tap", action: "switchEraser" });
  } });
  assert.equal(typeof globalThis.window.webkit, "undefined", "no WebKit in an Android web view");
  assert.equal(host.appShell(), "android");
  assert.deepEqual(attrs, { "data-host": "android", "data-shell": "android" });
  const h = host.nativeHost();
  for (const [flag, value] of Object.entries(ANDROID_INFO)) assert.deepEqual(h[flag], value, flag);
  assert.equal(h.pencilPhases, undefined, "no squeeze phases");
  assert.equal(h.pencilRoll, undefined, "no barrel roll");
  assert.deepEqual(said[0], { hello: true }, "the document says hello first, so native holds its reply proxy");

  assert.deepEqual(await host.postToHost({ type: "claim", pointerId: 3, at: { x: 1, y: 2 } }), { taken: true });
  assert.deepEqual(said.at(-1), { id: 1, msg: { type: "claim", pointerId: 3, at: { x: 1, y: 2 } } });
  assert.deepEqual(await host.postToHost({ type: "theme" }), {});
  assert.equal(await host.postToHost({ type: "nothing" }), null, "a reply's value as native wrote it");
  await assert.rejects(host.postToHost({ type: "refuse" }), /^Error: Gamma for Android has no refuse\.$/);
  // replies paired by id, whatever order they come back in
  const both = await Promise.all([host.postToHost({ type: "claim", pointerId: 4 }), host.postToHost({ type: "theme" })]);
  assert.deepEqual(both, [{ taken: true }, {}]);

  // the early message had no listener when host.js drained the stub: null, which native reads as {}
  assert.deepEqual(await early, { result: 1, value: null });
  host.onHostMessage("stroke", ({ pointerId }) => ({ committed: pointerId === 7 }));
  assert.deepEqual(await send({ type: "stroke", pointerId: 7, samples: [] }), { result: 2, value: { committed: true } });
  host.onHostMessage("hold", () => { throw new Error("boom"); });
  assert.deepEqual(await send({ type: "hold", pointerId: 7, samples: [] }), { result: 3, error: "boom" });
  assert.deepEqual(await send({ type: "future" }), { result: 4, value: null }, "a type nobody listens for never hangs native");
});

test("a host's own post wins over WebKit's handler", async () => {
  const posts = [];
  const native = stub({ post: (msg) => { posts.push(msg); return Promise.resolve({ via: "post" }); } });
  const { host, posted } = await load({ native });
  assert.deepEqual(await host.postToHost({ type: "theme" }), { via: "post" });
  assert.deepEqual(posts, [{ type: "theme" }]);
  assert.deepEqual(posted, [], "WebKit's handler is left alone");
  // a post that throws rejects the message, as a handler's would
  const { host: throwing } = await load({ native: stub({ post: () => { throw new Error("closed"); } }) });
  await assert.rejects(throwing.postToHost({ type: "theme" }), /closed/);
});

test("a host with neither a post of its own nor WebKit's handler has no transport", async () => {
  const native = stub();
  const { host } = await load({ before: (w) => { w.gammaNative = native; } });
  assert.equal(host.appShell(), "ipad");
  await assert.rejects(host.postToHost({ type: "theme" }), /no native host/);
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
  const { host: older } = await load({ native: stub({ inkLooks: undefined, hold: undefined, pencilPhases: undefined, haptics: undefined, openFiles: undefined, shellBar: undefined, editMenu: undefined, keepAwake: undefined }) });
  for (const flag of ["inkLooks", "hold", "pencilPhases", "haptics", "openFiles", "shellBar", "editMenu", "keepAwake"]) assert.ok(!older.nativeHost()[flag], `an older shell: no ${flag}`);
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

// The browser's screen wake lock (navigator.wakeLock) and a document that
// can be hidden and shown: `held` the locks not given back, `lose()` the
// browser letting them go as it hides the page.
function wakeLockPage() {
  const page = { asked: 0, held: new Set(), listeners: new Set(), visibilityState: "visible" };
  globalThis.document = {
    documentElement: { setAttribute() {} },
    get visibilityState() { return page.visibilityState; },
    addEventListener: (type, fn) => { if (type === "visibilitychange") page.listeners.add(fn); },
    removeEventListener: (type, fn) => { if (type === "visibilitychange") page.listeners.delete(fn); },
  };
  Object.defineProperty(globalThis.navigator, "wakeLock", {
    configurable: true,
    value: {
      request: async (type) => {
        assert.equal(type, "screen");
        page.asked++;
        if (page.visibilityState !== "visible") throw new DOMException("hidden", "NotAllowedError");
        const gone = new Set();
        const lock = { release: async () => { page.held.delete(lock); }, addEventListener: (t, fn) => { if (t === "release") gone.add(fn); } };
        lock.lose = () => { page.held.delete(lock); for (const fn of gone) fn(); };
        page.held.add(lock);
        return lock;
      },
    },
  });
  page.show = (visible) => {
    page.visibilityState = visible ? "visible" : "hidden";
    if (!visible) for (const lock of [...page.held]) lock.lose();
    for (const fn of [...page.listeners]) fn();
  };
  return page;
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test("keepAwake: a shell that declares it is told, once a change", async () => {
  const { host, posted } = await load({ native: stub({ keepAwake: true }) });
  host.keepAwake(true);
  host.keepAwake(true);
  host.keepAwake(false);
  host.keepAwake(false);
  assert.deepEqual(posted, [{ type: "keepAwake", on: true }, { type: "keepAwake", on: false }]);
});

test("keepAwake: elsewhere the screen wake lock, asked again each time the page is shown, given back at the end", async () => {
  try {
    const { host } = await load();
    const doc = wakeLockPage();
    host.keepAwake(true);
    host.keepAwake(true);
    await settle();
    assert.equal(doc.asked, 1);
    assert.equal(doc.held.size, 1);
    // hidden, the browser lets the lock go; shown again, it is asked for again
    doc.show(false);
    assert.equal(doc.held.size, 0);
    doc.show(true);
    await settle();
    assert.equal(doc.asked, 2);
    assert.equal(doc.held.size, 1);
    host.keepAwake(false);
    await settle();
    assert.equal(doc.held.size, 0);
    assert.equal(doc.listeners.size, 0);
    // asked while hidden: refused, then had once shown
    doc.visibilityState = "hidden";
    host.keepAwake(true);
    await settle();
    assert.equal(doc.held.size, 0);
    doc.show(true);
    await settle();
    assert.equal(doc.held.size, 1);
    // let go before the lock came: it is given back as it comes
    host.keepAwake(false);
    host.keepAwake(true);
    host.keepAwake(false);
    await settle();
    assert.equal(doc.held.size, 0);
  } finally {
    delete globalThis.navigator.wakeLock;
  }
});

test("keepAwake: with neither a shell that keeps the screen on nor a wake lock, nothing happens", async () => {
  const { host, posted } = await load({ native: stub({ keepAwake: undefined }) });
  host.keepAwake(true);
  host.keepAwake(false);
  assert.deepEqual(posted, [], "an older shell is not told");
  const { host: browser } = await load();
  assert.doesNotThrow(() => { browser.keepAwake(true); browser.keepAwake(false); });
});
