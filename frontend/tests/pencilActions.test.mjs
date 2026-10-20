// node --test tests/  (from frontend/) — the Pencil's double tap and squeeze.
import assert from "node:assert/strict";
import { test } from "node:test";
import { backTool, pencilAction } from "../src/ink/pencilActions.js";

const tools = [{ id: "pen1", kind: "pen" }, { id: "pen2", kind: "pen" }, { id: "hl1", kind: "highlighter" }];
const prefs = { tools, pen: "pen2" };

test("switchEraser toggles the eraser against the tool before it, else the stylus pen", () => {
  assert.deepEqual(pencilAction({ action: "switchEraser" }, prefs, { tool: "pen1", previous: null }), { pick: "eraser" });
  assert.deepEqual(pencilAction("switchEraser", prefs, { tool: "eraser", previous: "pen1" }), { pick: "pen1" });
  assert.deepEqual(pencilAction("switchEraser", prefs, { tool: "eraser", previous: "gone" }), { pick: "pen2" }, "a removed preset falls back to the pen");
  assert.deepEqual(pencilAction("switchEraser", prefs, { tool: "eraser", previous: "eraser" }), { pick: "pen2" });
  assert.deepEqual(pencilAction("switchEraser", prefs, { tool: null, previous: "select" }), { pick: "eraser" });
});

test("switchPrevious goes back to the tool armed before, the strip's tools included", () => {
  assert.deepEqual(pencilAction("switchPrevious", prefs, { tool: "pen1", previous: "select" }), { pick: "select" });
  assert.deepEqual(pencilAction("switchPrevious", prefs, { tool: "pen1", previous: "text" }), { pick: "text" });
  assert.deepEqual(pencilAction("switchPrevious", prefs, { tool: "pen1", previous: null }), { pick: "pen2" });
  assert.deepEqual(pencilAction("switchPrevious", { tools, pen: "nope" }, { tool: "hl1", previous: null }), { pick: "pen1" }, "no stylus pen: the first preset");
});

test("the palette and the attributes open the armed tool's row, or arm the pen with its row", () => {
  assert.deepEqual(pencilAction("showColorPalette", prefs, { tool: "pen1", options: false }), { ui: { open: true, options: true } });
  assert.deepEqual(pencilAction("showInkAttributes", prefs, { tool: "pen1", options: true }), { ui: { open: true, options: false } });
  assert.deepEqual(pencilAction("showColorPalette", prefs, { tool: null, options: false }), { ui: { open: true, tool: "pen2", options: true } });
});

test("anything else is nothing", () => {
  assert.equal(pencilAction("ignore", prefs, { tool: "pen1" }), null);
  assert.equal(pencilAction({}, prefs, {}), null);
  assert.equal(pencilAction(null, {}, {}), null);
});

test("backTool is the tool armed before, else the stylus pen: the eraser's return reads it too", () => {
  assert.equal(backTool(prefs, { tool: "eraser", previous: "pen1" }), "pen1");
  assert.equal(backTool(prefs, { tool: "eraser", previous: "select" }), "select");
  assert.equal(backTool(prefs, { tool: "eraser", previous: "eraser" }), "pen2", "the same tool is no way back");
  assert.equal(backTool(prefs, { tool: "eraser", previous: "gone" }), "pen2", "a removed preset falls back to the pen");
  assert.equal(backTool(prefs, { tool: "eraser", previous: null }), "pen2");
  assert.equal(backTool({ tools, pen: "nope" }, { tool: "eraser" }), "pen1", "no stylus pen: the first preset");
  assert.equal(backTool({}, {}), null);
});
