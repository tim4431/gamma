// node --test tests/  (from frontend/) — the tool strip's key table.
import assert from "node:assert/strict";
import { test } from "node:test";
import { stripKey } from "../src/ink/stripKeys.js";

const tools = [
  { id: "pen1", kind: "pen" }, { id: "pen2", kind: "pen" }, { id: "hl1", kind: "highlighter" }, { id: "hl2", kind: "highlighter" },
];
const key = (code, mods = {}) => ({ type: "keydown", code, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, repeat: false, ...mods });
const state = (patch = {}) => ({ tools, tool: "pen1", hasSelection: false, boxUndo: false, hasBox: false, optionsOpen: false, ...patch });

test("Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y step the stroke history, unless the block history owns Ctrl+Z", () => {
  assert.deepEqual(stripKey(key("KeyZ", { ctrlKey: true }), state()), { kind: "undo", redo: false, prevent: true, stop: true });
  assert.deepEqual(stripKey(key("KeyZ", { metaKey: true, shiftKey: true }), state()), { kind: "undo", redo: true, prevent: true, stop: true });
  assert.deepEqual(stripKey(key("KeyY", { ctrlKey: true }), state()), { kind: "undo", redo: true, prevent: true, stop: true });
  assert.equal(stripKey(key("KeyZ", { ctrlKey: true }), state({ boxUndo: true })), null);
  assert.deepEqual(stripKey(key("KeyZ", { ctrlKey: true, altKey: true }), state()), { kind: "toggle", what: "zoom" },
    "AltGr+Z is no undo: as a third-level letter it counts as a bare Z");
  assert.deepEqual(stripKey(key("KeyZ"), state()), { kind: "toggle", what: "zoom" }, "a bare Z is the zoom window's");
});

test("Escape closes one layer at a time: a text box keeps it, then the selection, the options row, the strip", () => {
  assert.equal(stripKey(key("Escape"), state({ hasBox: true, hasSelection: true, optionsOpen: true })), null);
  assert.deepEqual(stripKey(key("Escape"), state({ hasSelection: true, optionsOpen: true })), { kind: "deselect" });
  assert.deepEqual(stripKey(key("Escape"), state({ optionsOpen: true })), { kind: "closeOptions" });
  assert.deepEqual(stripKey(key("Escape"), state()), { kind: "close" });
});

test("Escape after the options row: the Shapes tool, then the ruler, then the zoom window, then the strip", () => {
  const all = { tool: "shape", ruler: true, zoom: true };
  assert.equal(stripKey(key("Escape"), state({ ...all, hasBox: true })), null, "a selected text box keeps it");
  assert.deepEqual(stripKey(key("Escape"), state({ ...all, hasSelection: true })), { kind: "deselect" });
  assert.deepEqual(stripKey(key("Escape"), state({ ...all, optionsOpen: true })), { kind: "closeOptions" }, "the ruler's row too");
  assert.deepEqual(stripKey(key("Escape"), state(all)), { kind: "off", what: "shape" });
  assert.deepEqual(stripKey(key("Escape"), state({ ...all, tool: "pen1" })), { kind: "off", what: "ruler" });
  assert.deepEqual(stripKey(key("Escape"), state({ tool: "pen1", zoom: true })), { kind: "off", what: "zoom" });
  assert.deepEqual(stripKey(key("Escape"), state({ tool: "eraser" })), { kind: "close" });
});

test("R, S and Z toggle the ruler, the Shapes tool and the zoom window, by the physical key", () => {
  assert.deepEqual(stripKey(key("KeyR"), state()), { kind: "toggle", what: "ruler" });
  assert.deepEqual(stripKey(key("KeyS"), state({ tool: "shape" })), { kind: "toggle", what: "shape" });
  assert.deepEqual(stripKey(key("KeyZ", { shiftKey: true }), state()), { kind: "toggle", what: "zoom" }, "Shift does not matter");
  assert.deepEqual(stripKey(key("KeyR", { ctrlKey: true, altKey: true }), state()), { kind: "toggle", what: "ruler" }, "AltGr is no modifier");
  assert.equal(stripKey(key("KeyR", { ctrlKey: true }), state()), null, "Ctrl+R is someone else's");
  assert.equal(stripKey(key("KeyS", { metaKey: true }), state()), null, "Cmd+S too");
  assert.equal(stripKey(key("KeyS", { altKey: true }), state()), null);
});

test("Enter is the zoom window's New line, unless a button, a link, a menu row or a selected text box has it", () => {
  assert.deepEqual(stripKey(key("Enter"), state({ zoom: true })), { kind: "newLine", prevent: true });
  assert.deepEqual(stripKey(key("NumpadEnter"), state({ zoom: true })), { kind: "newLine", prevent: true });
  assert.equal(stripKey(key("Enter"), state()), null, "no zoom window: Enter is nobody's here");
  assert.equal(stripKey(key("Enter"), state({ zoom: true, onControl: true })), null, "a focused button keeps its Enter");
  assert.equal(stripKey(key("Enter"), state({ zoom: true, hasBox: true })), null, "a selected text box opens on its Enter");
  assert.equal(stripKey(key("Enter", { ctrlKey: true }), state({ zoom: true })), null, "a chord is someone else's");
});

test("Delete and Backspace remove the selection only while there is one", () => {
  assert.deepEqual(stripKey(key("Delete"), state({ hasSelection: true })), { kind: "delete", prevent: true });
  assert.deepEqual(stripKey(key("Backspace"), state({ hasSelection: true })), { kind: "delete", prevent: true });
  assert.equal(stripKey(key("Delete"), state()), null);
});

test("letters arm tools by physical key; P and H step through the pens and highlighters, wrapping, Shift backwards", () => {
  assert.deepEqual(stripKey(key("KeyL"), state()), { kind: "pick", id: "select" });
  assert.deepEqual(stripKey(key("KeyT"), state()), { kind: "pick", id: "text" });
  assert.deepEqual(stripKey(key("KeyV"), state()), { kind: "pick", id: null });
  assert.deepEqual(stripKey(key("KeyP"), state({ tool: "pen1" })), { kind: "pick", id: "pen2" });
  assert.deepEqual(stripKey(key("KeyP"), state({ tool: "pen2" })), { kind: "pick", id: "pen1" });
  assert.deepEqual(stripKey(key("KeyH"), state({ tool: "eraser" })), { kind: "pick", id: "hl1" });
  assert.deepEqual(stripKey(key("KeyH"), state({ tool: "hl2" })), { kind: "pick", id: "hl1" });
  assert.deepEqual(stripKey(key("KeyP", { shiftKey: true }), state({ tool: "pen1" })), { kind: "pick", id: "pen2" }, "Shift+P wraps back to the last pen");
  assert.deepEqual(stripKey(key("KeyP", { shiftKey: true }), state({ tool: "pen2" })), { kind: "pick", id: "pen1" });
  assert.deepEqual(stripKey(key("KeyH", { shiftKey: true }), state({ tool: "hl2" })), { kind: "pick", id: "hl1" });
  assert.deepEqual(stripKey(key("KeyH", { shiftKey: true }), state({ tool: "eraser" })), { kind: "pick", id: "hl2" }, "from another tool, Shift starts at the end");
  assert.equal(stripKey(key("KeyH"), state({ tools: tools.slice(0, 2) })), null, "no highlighter to step to");
  assert.equal(stripKey(key("KeyL", { ctrlKey: true }), state()), null, "a chord is someone else's");
  assert.equal(stripKey(key("KeyL", { altKey: true }), state()), null);
  assert.deepEqual(stripKey(key("KeyL", { ctrlKey: true, altKey: true }), state()), { kind: "pick", id: "select" }, "AltGr counts as no modifier");
});

test("E is a tap or a hold: the keydown arms, repeats are nothing, the keyup says how long it was held", () => {
  assert.deepEqual(stripKey(key("KeyE"), state()), { kind: "eraser", phase: "down" });
  assert.equal(stripKey(key("KeyE", { repeat: true }), state({ eraserDown: 1000 })), null);
  assert.deepEqual(stripKey({ ...key("KeyE"), type: "keyup", timeStamp: 1450 }, state({ eraserDown: 1000 })), { kind: "eraser", phase: "up", held: 450 });
  assert.equal(stripKey({ ...key("KeyE"), type: "keyup", timeStamp: 1450 }, state()), null, "a keyup with no down seen (the strip opened mid-press)");
  assert.equal(stripKey({ ...key("KeyP"), type: "keyup" }, state({ eraserDown: 1000 })), null, "no other key acts on its keyup");
  assert.equal(stripKey(key("KeyE", { ctrlKey: true }), state()), null);
});

test("the bracket keys step the armed preset's width; with Shift its pen style", () => {
  assert.deepEqual(stripKey(key("BracketLeft"), state()), { kind: "width", step: -1 });
  assert.deepEqual(stripKey(key("BracketRight"), state()), { kind: "width", step: 1 });
  assert.deepEqual(stripKey(key("BracketRight", { shiftKey: true }), state()), { kind: "brush", step: 1 }, "Shift+] is the next style");
  assert.deepEqual(stripKey(key("BracketLeft", { shiftKey: true }), state()), { kind: "brush", step: -1 }, "Shift+[ the one before");
  assert.deepEqual(stripKey(key("BracketLeft", { shiftKey: true, ctrlKey: true, altKey: true }), state()), { kind: "brush", step: -1 },
    "matched by the physical key: AltGr is no modifier");
  assert.equal(stripKey(key("BracketRight", { metaKey: true }), state()), null);
  assert.equal(stripKey(key("BracketRight", { metaKey: true, shiftKey: true }), state()), null, "a chord is someone else's");
});

test("digits arm the preset at that position, from the row or the keypad", () => {
  assert.deepEqual(stripKey(key("Digit1"), state()), { kind: "pick", id: "pen1" });
  assert.deepEqual(stripKey(key("Numpad3"), state()), { kind: "pick", id: "hl1" });
  assert.deepEqual(stripKey(key("Digit2", { shiftKey: true }), state()), { kind: "pick", id: "pen2" }, "Shift+2 is still the second key");
  assert.equal(stripKey(key("Digit9"), state()), null, "no ninth preset");
  assert.equal(stripKey(key("Digit0"), state()), null);
  assert.equal(stripKey(key("KeyQ"), state()), null);
  assert.equal(stripKey({ ctrlKey: true }, state()), null, "an event without a code");
});
