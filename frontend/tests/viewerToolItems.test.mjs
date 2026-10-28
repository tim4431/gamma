// The viewer's side strip (app/viewerToolItems.js): its items, the
// preference that orders them, and the translate switch it replaced.
import test from "node:test";
import assert from "node:assert/strict";
import { PREFS } from "../src/app/prefDefs.js";
import { VIEWER_TOOLS, VIEWER_TOOL_ITEMS } from "../src/app/viewerToolItems.js";

const ORDER = ["zoom", "ink", "record", "translate", "paper", "notesView", "selectMode"];

test("every tool has its row's parts and is on the strip or in its ⋯ menu", () => {
  assert.equal(VIEWER_TOOLS.items, VIEWER_TOOL_ITEMS);
  assert.deepEqual(VIEWER_TOOL_ITEMS.map((item) => item.id), ORDER, "the strip's own order");
  for (const item of VIEWER_TOOL_ITEMS) {
    assert.ok(item.label && item.hint && item.keywords, `${item.id} has a label, a hint and synonyms`);
    assert.deepEqual(item.modes, ["always", "hidden"], `${item.id}: on the strip by default, or hidden`);
    assert.equal(VIEWER_TOOLS.modeLabel(item, "always"), "Shown");
    assert.equal(VIEWER_TOOLS.modeTip(item, "hidden"), "In the ⋯ menu at the end of the strip");
  }
});

test("the preference stores the layout, and the old translate switch is gone", () => {
  assert.equal(PREFS.viewerTools.key, "gamma-viewer-tools");
  assert.equal(PREFS.viewerTools.scope, "account");
  assert.equal(PREFS.viewerTools.default, VIEWER_TOOLS.DEFAULT);
  assert.deepEqual(VIEWER_TOOLS.DEFAULT.order, ORDER);
  const value = VIEWER_TOOLS.setMode(VIEWER_TOOLS.move(undefined, "record", 0), "translate", "hidden");
  assert.deepEqual(PREFS.viewerTools.parse(PREFS.viewerTools.serialize(value)), value);
  assert.equal(PREFS.viewerTools.parse("{"), undefined);
  assert.equal(PREFS.translateEnabled, undefined, "Translate hidden is the switch now");
});

test("a tool in use stays on the strip whatever its mode", () => {
  const value = { modes: { record: "hidden", translate: "hidden" } };
  const at = (options) => Object.fromEntries(VIEWER_TOOLS.place(value, options).map((x) => [x.id, x.at]));
  assert.equal(at().record, "menu");
  assert.equal(at({ pinned: { record: true } }).record, "bar", "while it records");
  assert.equal(at({ pinned: { record: true } }).translate, "menu");
});
