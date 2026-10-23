import assert from "node:assert/strict";
import { test } from "node:test";
import { setSessionScope, loadSession, saveSession, clearSession, flushSession } from "../src/app/sessionState.js";

test("reading state is isolated by account and workspace, including delayed saves", async (t) => {
  const data = new Map();
  t.mock.method(globalThis, "setTimeout", (fn) => { pending = fn; return 1; });
  t.mock.method(globalThis, "clearTimeout", () => { pending = null; });
  let pending = null;
  globalThis.localStorage = {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key),
  };
  t.after(() => { setSessionScope("", ""); delete globalThis.localStorage; });
  data.set("gamma-session", JSON.stringify({ focusedBlockId: "old-account" }));
  assert.deepEqual(loadSession(), {});
  setSessionScope("alice", "team");
  saveSession({ focusedBlockId: "alice-page", pdfScale: 1.5 });
  pending();
  setSessionScope("bob", "team");
  assert.deepEqual(loadSession(), {});
  saveSession({ focusedBlockId: "bob-page" });
  setSessionScope("alice", "team");
  assert.equal(pending, null, "switching cancels the previous account's pending save");
  assert.equal(loadSession().focusedBlockId, "alice-page");
  setSessionScope("alice", "personal");
  assert.deepEqual(loadSession(), {});
  setSessionScope("alice", "team");
  clearSession();
  assert.deepEqual(loadSession(), {});
});

// A close runs flushSession from pagehide and from visibilitychange: the
// first writes the save the debounce holds, the second finds none.
test("flushSession writes a pending save at once, and once", async (t) => {
  const data = new Map();
  let writes = 0;
  t.mock.method(globalThis, "setTimeout", () => 1);
  t.mock.method(globalThis, "clearTimeout", () => {});
  globalThis.localStorage = {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => { writes += 1; data.set(key, value); },
    removeItem: (key) => data.delete(key),
  };
  t.after(() => { setSessionScope("", ""); delete globalThis.localStorage; });
  setSessionScope("alice", "team");
  saveSession({ focusedBlockId: "page-1", pdfScale: 1.5 });
  assert.deepEqual(loadSession(), {}, "held by the debounce");
  flushSession();
  assert.deepEqual(loadSession(), { focusedBlockId: "page-1", pdfScale: 1.5 });
  flushSession();
  assert.equal(writes, 1, "the second flush finds nothing pending");
});
