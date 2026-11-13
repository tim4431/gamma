// The Files window's place in a page's saved dock layout
// (explorer/useFilesWindow.js layoutKeeping): the window's place is the
// browser's, so a page's layout keeps it where the current one has it.
import assert from "node:assert/strict";
import { test } from "node:test";
import { layoutKeeping } from "../src/explorer/useFilesWindow.js";

test("a layout saved before the window existed keeps it where the current layout has it", () => {
  const saved = { left: [], right: ["notes", "chat"], bottom: [] };
  const current = { left: ["files"], right: ["chat", "notes"], bottom: [] };
  assert.deepEqual(layoutKeeping(saved, current, "files"), { left: ["files"], right: ["notes", "chat"], bottom: [] });
});

test("a saved layout's own place for the window gives way to the current one", () => {
  const saved = { left: ["files"], right: ["notes", "chat"], bottom: [] };
  const current = { left: [], right: ["notes", "files", "chat"], bottom: [] };
  assert.deepEqual(layoutKeeping(saved, current, "files"), { left: [], right: ["notes", "files", "chat"], bottom: [] });
  const short = { left: [], right: ["chat"], bottom: [] };
  assert.deepEqual(layoutKeeping(short, current, "files"), { left: [], right: ["chat", "files"], bottom: [] },
    "past the end of a shorter slot: last");
});

test("a current layout without the window takes it out of the saved one", () => {
  const saved = { left: ["files"], right: ["notes"], bottom: ["chat"] };
  const current = { left: [], right: ["notes"], bottom: ["chat"] };
  assert.deepEqual(layoutKeeping(saved, current, "files"), { left: [], right: ["notes"], bottom: ["chat"] });
});

test("nothing saved: the current layout; the saved object is not changed", () => {
  const current = { left: ["files"], right: [], bottom: [] };
  assert.equal(layoutKeeping(null, current, "files"), current);
  const saved = { left: [], right: ["notes"], bottom: [], extra: 1 };
  const out = layoutKeeping(saved, current, "files");
  assert.deepEqual(saved, { left: [], right: ["notes"], bottom: [], extra: 1 });
  assert.deepEqual(out, { left: ["files"], right: ["notes"], bottom: [], extra: 1 });
});
