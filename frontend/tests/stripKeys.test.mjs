// node --test tests/  (from frontend/) — the tool strip's key table.
import assert from "node:assert/strict";
import { test } from "node:test";
import { stripKey } from "../src/ink/stripKeys.js";

const tools = [
  { id: "pen1", kind: "pen" }, { id: "pen2", kind: "pen" }, { id: "hl1", kind: "highlighter" }, { id: "hl2", kind: "highlighter" },
];
const key = (code, mods = {}) => ({ code, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...mods });
const state = (patch = {}) => ({ tools, tool: "pen1", hasSelection: false, boxUndo: false, hasBox: false, ...patch });

test("Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y step the stroke history, unless the block history owns Ctrl+Z", () => {
  assert.deepEqual(stripKey(key("KeyZ", { ctrlKey: true }), state()), { kind: "undo", redo: false, prevent: true, stop: true });
  assert.deepEqual(stripKey(key("KeyZ", { metaKey: true, shiftKey: true }), state()), { kind: "undo", redo: true, prevent: true, stop: true });
  assert.deepEqual(stripKey(key("KeyY", { ctrlKey: true }), state()), { kind: "undo", redo: true, prevent: true, stop: true });
  assert.equal(stripKey(key("KeyZ", { ctrlKey: true }), state({ boxUndo: true })), null);
  assert.equal(stripKey(key("KeyZ", { ctrlKey: true, altKey: true }), state()), null);
  assert.equal(stripKey(key("KeyZ"), state()), null, "a bare Z is nothing");
});

test("Escape drops the selection, then closes the strip; a selected text box keeps it", () => {
  assert.deepEqual(stripKey(key("Escape"), state({ hasSelection: true })), { kind: "deselect" });
  assert.deepEqual(stripKey(key("Escape"), state()), { kind: "close" });
  assert.equal(stripKey(key("Escape"), state({ hasBox: true })), null);
});

test("Delete and Backspace remove the selection only while there is one", () => {
  assert.deepEqual(stripKey(key("Delete"), state({ hasSelection: true })), { kind: "delete", prevent: true });
  assert.deepEqual(stripKey(key("Backspace"), state({ hasSelection: true })), { kind: "delete", prevent: true });
  assert.equal(stripKey(key("Delete"), state()), null);
});

test("letters arm tools by physical key; P and H step through the pens and highlighters, wrapping", () => {
  assert.deepEqual(stripKey(key("KeyE"), state()), { kind: "pick", id: "eraser" });
  assert.deepEqual(stripKey(key("KeyL"), state()), { kind: "pick", id: "select" });
  assert.deepEqual(stripKey(key("KeyT"), state()), { kind: "pick", id: "text" });
  assert.deepEqual(stripKey(key("KeyV"), state()), { kind: "pick", id: null });
  assert.deepEqual(stripKey(key("KeyP"), state({ tool: "pen1" })), { kind: "pick", id: "pen2" });
  assert.deepEqual(stripKey(key("KeyP"), state({ tool: "pen2" })), { kind: "pick", id: "pen1" });
  assert.deepEqual(stripKey(key("KeyH"), state({ tool: "eraser" })), { kind: "pick", id: "hl1" });
  assert.deepEqual(stripKey(key("KeyH"), state({ tool: "hl2" })), { kind: "pick", id: "hl1" });
  assert.equal(stripKey(key("KeyH"), state({ tools: tools.slice(0, 2) })), null, "no highlighter to step to");
  assert.equal(stripKey(key("KeyE", { ctrlKey: true }), state()), null, "a chord is someone else's");
  assert.equal(stripKey(key("KeyE", { altKey: true }), state()), null);
});

test("digits arm the preset at that position, from the row or the keypad", () => {
  assert.deepEqual(stripKey(key("Digit1"), state()), { kind: "pick", id: "pen1" });
  assert.deepEqual(stripKey(key("Numpad3"), state()), { kind: "pick", id: "hl1" });
  assert.equal(stripKey(key("Digit9"), state()), null, "no ninth preset");
  assert.equal(stripKey(key("Digit0"), state()), null);
  assert.equal(stripKey(key("KeyQ"), state()), null);
  assert.equal(stripKey({ ctrlKey: true }, state()), null, "an event without a code");
});
