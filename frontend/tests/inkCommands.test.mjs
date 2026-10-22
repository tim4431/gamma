// node --test tests/inkCommands.test.mjs (from frontend/) — the handwriting
// commands' context (ink/inkCommands.js inkCommandContext, the fields App's
// command context carries) run through the catalog that reads it
// (app/appCommands.js APP_COMMANDS, dispatched as App's window listener
// does): the guards, and which keys a command keeps when it does nothing.
import assert from "node:assert/strict";
import { test } from "node:test";
import { APP_COMMANDS } from "../src/app/appCommands.js";
import { inkCommandContext } from "../src/ink/inkCommands.js";
import { dispatch } from "../src/shared/lib/hotkeys.js";

const by = (id) => APP_COMMANDS.find((c) => c.id === id);
// App's context as appCmdRef.current has it: readOnly beside the ink fields.
// The selection's actions answer as `answers` says and log their calls.
function contextOf({ open = true, tool = { tool: "pen" }, selection = { page: 1, items: [{ id: "g1", ids: ["a"] }] },
  readOnly = false, clip = true } = {}, answers = {}) {
  const calls = [];
  const actions = {
    copyInkSelection: (cut) => { calls.push(cut ? "cut" : "copy"); return answers.copy ?? true; },
    pasteAtCentre: () => { calls.push("paste"); return answers.paste; },
    duplicateInkSelection: () => { calls.push("duplicate"); return answers.duplicate; },
    selectAllInk: () => { calls.push("selectAll"); return answers.selectAll ?? true; },
  };
  const ctx = { readOnly, ...inkCommandContext({ inkUi: { open, tool: "pen1", options: false, pen: "pen1" }, inkTool: tool, inkSelection: selection,
    readOnly, inkClip: clip }, actions) };
  return { ctx, calls };
}
// A key as App's window listener hands it to the catalog (not from a text field).
function press(code, ctx) {
  const e = { code, key: code.slice(3).toLowerCase(), ctrlKey: true, metaKey: false, altKey: false, shiftKey: false, target: { tagName: "DIV" },
    prevented: false, preventDefault() { this.prevented = true; }, stopPropagation() {} };
  const cmd = dispatch(APP_COMMANDS, e, ctx, {}, false);
  return { id: cmd?.id ?? null, prevented: e.prevented };
}

test("the nine fields App's context carries for the handwriting commands", () => {
  const { ctx } = contextOf({ tool: { tool: "eraser" }, clip: false });
  const { readOnly: _r, ...fields } = ctx;
  assert.deepEqual(Object.keys(fields), ["inkOpen", "inkTool", "inkSelected", "inkClip", "inkCopy", "inkCut", "inkPaste", "inkDuplicate", "inkSelectAll"]);
  assert.deepEqual([fields.inkOpen, fields.inkTool, fields.inkSelected, fields.inkClip], [true, true, true, false]);
  assert.equal(contextOf({ tool: null }).ctx.inkTool, false, "the hand");
  assert.equal(contextOf({ open: false }).ctx.inkOpen, false);
  assert.equal(contextOf({ selection: null }).ctx.inkSelected, false);
});

test("the guards: copy, cut and duplicate wait for a selection; paste for the strip open, a tool armed and the clipboard; select all for the strip open and a tool armed", () => {
  const when = (id, patch) => by(id).when(contextOf(patch).ctx);
  for (const id of ["ink.copy", "ink.cut", "ink.duplicate"]) {
    assert.equal(when(id, {}), true, id);
    assert.equal(when(id, { selection: null }), false, id);
  }
  assert.equal(when("ink.paste", {}), true);
  assert.equal(when("ink.paste", { clip: false }), false);
  assert.equal(when("ink.paste", { tool: null }), false, "the hand armed");
  assert.equal(when("ink.paste", { open: false }), false);
  assert.equal(when("ink.selectAll", { selection: null, clip: false }), true);
  assert.equal(when("ink.selectAll", { tool: null }), false);
  assert.equal(when("ink.selectAll", { open: false }), false);
});

test("on a read-only page nothing is selected for the commands, and the keys are left alone", () => {
  const { ctx, calls } = contextOf({ readOnly: true });
  assert.equal(ctx.inkSelected, false);
  for (const code of ["KeyC", "KeyX", "KeyV", "KeyD", "KeyA"]) assert.deepEqual(press(code, ctx), { id: null, prevented: false }, code);
  assert.deepEqual(calls, []);
});

test("copy, cut and select all that do nothing leave the key to the browser; paste and duplicate keep it claimed", () => {
  const idle = contextOf({}, { copy: false, selectAll: false, paste: false, duplicate: false });
  assert.deepEqual(press("KeyC", idle.ctx), { id: null, prevented: false });
  assert.deepEqual(press("KeyX", idle.ctx), { id: null, prevented: false });
  assert.deepEqual(press("KeyA", idle.ctx), { id: null, prevented: false });
  assert.deepEqual(press("KeyV", idle.ctx), { id: "ink.paste", prevented: true }, "answers undefined whatever pasteAtCentre returned");
  assert.deepEqual(press("KeyD", idle.ctx), { id: "ink.duplicate", prevented: true });
  assert.deepEqual(idle.calls, ["copy", "cut", "selectAll", "paste", "duplicate"], "copy is copyInkSelection(false), cut copyInkSelection(true)");
  assert.equal(idle.ctx.inkPaste(), undefined);
  assert.equal(idle.ctx.inkDuplicate(), undefined);
  const busy = contextOf();
  for (const [code, id] of [["KeyC", "ink.copy"], ["KeyX", "ink.cut"], ["KeyA", "ink.selectAll"]]) {
    assert.deepEqual(press(code, busy.ctx), { id, prevented: true }, code);
  }
});
