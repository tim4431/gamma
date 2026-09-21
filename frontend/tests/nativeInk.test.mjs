import assert from "node:assert/strict";
import { test } from "node:test";
import { assertSameContext, checkNativeResult, hasNativeInk, presentNativeInk } from "../src/ink/nativeInk.js";

const context = { user: "alice", workspace: "lab", pageId: "paper", pdfUrl: "/api/uploads/paper.pdf" };
const request = { requestId: "request", existing: true, blockId: "ink", ink: {
  format: "gamma-ink", version: 1, space: { kind: "pdf-page", page: 2, width: 612, height: 792 }, strokes: [],
} };
const result = () => ({ requestId: "request", blockId: "ink", expectedURL: "/api/uploads/old.ink", ink: structuredClone(request.ink) });

test("native ink is optional and refuses identity/workspace/document changes", () => {
  assert.equal(hasNativeInk({}), false);
  assert.doesNotThrow(() => assertSameContext(context, context));
  for (const key of Object.keys(context)) assert.throws(() => assertSameContext(context, { ...context, [key]: "other" }));
  assert.throws(() => assertSameContext(context, { ...context, readOnly: true }));
});
test("native replies must match the page geometry and target; explicit copies get fresh IDs", () => {
  assert.deepEqual(checkNativeResult(request, result()), request.ink);
  const wrong = result(); wrong.ink.space.page = 3;
  assert.throws(() => checkNativeResult(request, wrong));
  assert.throws(() => checkNativeResult(request, { ...result(), blockId: "different" }));
  assert.doesNotThrow(() => checkNativeResult(request, { ...result(), blockId: "copy", expectedURL: null, asCopy: true }));
});
test("a failed save retains the editor; retry acknowledges only after persistence", async () => {
  const host = new EventTarget(), messages = [];
  let close, attempts = 0;
  host.webkit = { messageHandlers: { gammaInk: { postMessage: async (message) => {
    messages.push(message);
    if (message.action === "open") return new Promise((resolve) => { close = resolve; });
  } } } };
  const opened = presentNativeInk(request, async () => { if (++attempts === 1) throw new Error("offline"); }, host);
  host.dispatchEvent(new CustomEvent("gamma-native-ink-save", { detail: result() }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(messages.at(-1).action, "failed");
  host.dispatchEvent(new CustomEvent("gamma-native-ink-save", { detail: result() }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(messages.at(-1).action, "saved");
  close(); await opened;
  host.dispatchEvent(new CustomEvent("gamma-native-ink-save", { detail: result() }));
  assert.equal(attempts, 2, "closing removes the listener");
});
