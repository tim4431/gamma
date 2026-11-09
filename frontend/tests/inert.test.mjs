// `inert` is a boolean prop in React 19: true writes inert="", false or
// undefined writes nothing, and the empty string reads as false, so
// `inert={x ? "" : undefined}` (the React 18 spelling) makes nothing inert
// and says so only in development. A folded dock window, a closing fold and
// Settings while it asks rely on it (docs/dev/debugging.md, "Gotchas worth
// knowing"), and so does everything that skips `closest("[inert]")`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { JSDOM } from "jsdom";

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (name.endsWith(".jsx")) out.push(p);
  }
  return out;
}

// The value written after `inert=`: a string literal, or the expression in
// its braces up to the one that closes them, strings and nested braces
// skipped whole. Null for a bare `inert`, which is true.
function inertValue(src, at) {
  if (src[at] === '"' || src[at] === "'") return src.slice(at, src.indexOf(src[at], at + 1) + 1);
  if (src[at] !== "{") return null;
  let depth = 0;
  for (let i = at; i < src.length; i++) {
    const c = src[i];
    if (c === '"' || c === "'" || c === "`") {
      i = src.indexOf(c, i + 1);
      if (i < 0) break;
    } else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return src.slice(at, i + 1);
  }
  return src.slice(at);
}

// A value that can be a string: a string literal, an expression holding
// the empty string, or one that gives a string as a branch (`x ? "…" : …`,
// `x && "…"`). A comparison with a string (`phase === "closing"`) is a
// boolean.
const stringy = (value) => /^["'`]/.test(value) || /(["'`])\1/.test(value) || /(?:^\{|[?:]|&&|\|\|)\s*["'`]/.test(value);

test("no element is given a string for inert", () => {
  const found = [];
  for (const file of walk(new URL("../src", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"))) {
    const src = readFileSync(file, "utf8");
    for (const m of src.matchAll(/\binert=/g)) {
      const value = inertValue(src, m.index + m[0].length);
      if (value && stringy(value)) found.push(`${file.replace(/^.*[\\/]src[\\/]/, "src/")}: inert=${value}`);
    }
  }
  assert.deepEqual(found, [], "give inert a boolean (inert={!!x}): a string reads as false when empty");
});

test("React writes inert for true and takes it away for false", async () => {
  const dom = new JSDOM("<!doctype html><body></body>");
  Object.assign(globalThis, { window: dom.window, document: dom.window.document });
  globalThis.navigator ??= dom.window.navigator;
  const React = (await import("react")).default;
  const { flushSync } = (await import("react-dom")).default;
  const { createRoot } = (await import("react-dom/client")).default;
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const fold = (inert) => flushSync(() => root.render(React.createElement("div", { inert }, "Rows")));
  fold(true);
  assert.equal(host.firstChild.getAttribute("inert"), "");
  fold(false);
  assert.equal(host.firstChild.hasAttribute("inert"), false);
  fold(true);
  assert.equal(host.firstChild.getAttribute("inert"), "");
  fold(undefined);
  assert.equal(host.firstChild.hasAttribute("inert"), false);
  root.unmount();
});
