// copyPayload (shared/lib/utils.js): a copy whose content is still being
// made (a citation rendering, a bibliography fetched). With the async
// clipboard the item has to exist before anything is awaited, or Safari
// drops the click's gesture; without it the legacy copy runs once the
// content is in. The clipboard, the item and execCommand are stubs; jsdom
// supplies the document the legacy copy works in.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { JSDOM } from "jsdom";

// shared/lib/utils.js imports without extensions and reads `window` as it loads.
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    try { return await next(specifier, context); } catch (err) {
      if (err?.code === "ERR_MODULE_NOT_FOUND" && /^\\.\\.?\\//.test(specifier) && !/\\.[a-z]+$/.test(specifier)) return next(specifier + ".js", context);
      throw err;
    }
  }`));
globalThis.window = globalThis;
globalThis.location = new URL("http://gamma.test/");
globalThis.dispatchEvent = () => true;
globalThis.document = new JSDOM("<body></body>").window.document;
const { copyPayload } = await import("../src/shared/lib/utils.js");

// A secure page whose clipboard records each write (`fails`: refuses it),
// or a plain-HTTP one without (`secure: false`). The legacy copy's
// execCommand fires the copy event and records what it set.
function page(t, { secure = true, fails = false } = {}) {
  const writes = [];
  const legacy = [];
  const made = [];
  class ClipboardItem {
    constructor(items) { this.items = items; made.push(Object.keys(items)); }
  }
  const clipboard = {
    async write(items) {
      if (fails) throw new Error("NotAllowedError");
      const flavours = {};
      for (const [type, blob] of Object.entries(items[0].items)) flavours[type] = await (await blob).text();
      writes.push(flavours);
    },
  };
  globalThis.isSecureContext = secure;
  globalThis.ClipboardItem = secure ? ClipboardItem : undefined;
  Object.defineProperty(globalThis.navigator, "clipboard", { value: secure ? clipboard : undefined, configurable: true });
  document.execCommand = () => {
    const flavours = {};
    const event = new document.defaultView.Event("copy");
    event.clipboardData = { setData: (type, value) => { flavours[type] = value; } };
    document.querySelector("textarea").dispatchEvent(event);
    legacy.push(flavours);
    return true;
  };
  t.after(() => { delete globalThis.ClipboardItem; delete document.execCommand; });
  return { writes, legacy, made };
}

const later = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

test("the clipboard item is made at once, before the content is in, with both flavours", async (t) => {
  const { writes, made } = page(t);
  const content = later();
  const copied = copyPayload(content.promise);
  assert.deepEqual(made, [["text/plain", "text/html"]], "made in the click itself");
  content.resolve({ text: "Smith, J. (2023).", html: "<i>Smith</i>" });
  assert.equal(await copied, true);
  assert.deepEqual(writes, [{ "text/plain": "Smith, J. (2023).", "text/html": "<i>Smith</i>" }]);
});

test("plain text alone when asked; content without HTML pastes the same as HTML", async (t) => {
  const { writes } = page(t);
  assert.equal(await copyPayload(Promise.resolve({ text: "@book{a}" }), { html: false }), true);
  assert.equal(await copyPayload(Promise.resolve({ text: "a < b\n& c" })), true);
  assert.deepEqual(writes, [{ "text/plain": "@book{a}" }, { "text/plain": "a < b\n& c", "text/html": "a &lt; b<br>&amp; c" }]);
});

test("without the async clipboard, or when it refuses, the legacy copy runs once the content is in", async (t) => {
  const plain = page(t, { secure: false });
  assert.equal(await copyPayload(Promise.resolve({ text: "x", html: "<b>x</b>" })), true);
  assert.deepEqual(plain.legacy, [{ "text/plain": "x", "text/html": "<b>x</b>" }]);
  const refused = page(t, { fails: true });
  assert.equal(await copyPayload(Promise.resolve({ text: "y" }), { html: false }), true);
  assert.deepEqual(refused.legacy, [{ "text/plain": "y" }]);
});

test("content that fails rejects with its own error", async (t) => {
  page(t);
  await assert.rejects(copyPayload(Promise.reject(new Error("The style is not on this server."))), /not on this server/);
  page(t, { secure: false });
  await assert.rejects(copyPayload(Promise.reject(new Error("offline"))), /offline/);
});
