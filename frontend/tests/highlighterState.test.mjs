// node --test tests/  (from frontend/) — the text highlighter's session
// state and keys (pdf/highlighterState.js): which highlighter is armed, the
// strip's buttons, and what 1–9, Enter and Escape do while one is armed or
// the selection popup is open.
import assert from "node:assert/strict";
import { test } from "node:test";
import { HL_IDLE, armedTo, disarmed, highlighterKey, menuClosed, menuOpened, nextArmed, popupKey, stripPresets, toggled } from "../src/pdf/highlighterState.js";

const hl = (id, extra = {}) => ({ id, name: "", style: "highlight", color: { hex: "#ffe28f", alpha: 0.65 }, ...extra });
const LIST = [hl("a"), hl("b"), hl("c"), hl("x", { hidden: true })];
const key = (code, more = {}) => ({ code, ...more });

test("a press arms a highlighter, a press on the armed one turns it off, and on again brings the last one back", () => {
  let s = toggled(HL_IDLE, "b", LIST);
  assert.deepEqual([s.armed, s.last], ["b", "b"]);
  s = toggled(s, "b", LIST);
  assert.deepEqual([s.armed, s.last], [null, "b"]);
  assert.equal(toggled(s, null, LIST).armed, "b", "on again: the last one");
  assert.equal(toggled({ ...HL_IDLE, last: "gone" }, null, LIST).armed, "a", "the last one removed: the first shown");
  assert.equal(toggled(armedTo(HL_IDLE, "c"), null, LIST).armed, null, "a command with one armed turns it off");
  assert.equal(toggled(HL_IDLE, null, []).armed, null);
  assert.equal(disarmed(HL_IDLE), HL_IDLE);
  // armed one removed elsewhere (Settings, another device): it counts as off, so on arms again at once
  const stale = { ...HL_IDLE, armed: "gone", last: "gone" };
  assert.equal(toggled(stale, null, LIST).armed, "a");
  assert.equal(toggled(stale, "gone", LIST).armed, "gone", "a press on its id arms it as any id");
});

test("next walks the strip's buttons and wraps; from off, the first", () => {
  assert.equal(nextArmed(HL_IDLE, LIST).armed, "a");
  assert.equal(nextArmed(armedTo(HL_IDLE, "a"), LIST).armed, "b");
  assert.equal(nextArmed(armedTo(HL_IDLE, "c"), LIST).armed, "a", "wraps past the shown ones");
  assert.equal(nextArmed(armedTo(HL_IDLE, "x"), LIST).armed, "a", "from a pinned hidden one, round to the first");
});

test("the strip: the shown ones, a hidden one armed pinned after them; hidden in Viewer tools, the armed one alone", () => {
  assert.deepEqual(stripPresets(LIST, null).map((p) => p.id), ["a", "b", "c"]);
  assert.deepEqual(stripPresets(LIST, "x").map((p) => p.id), ["a", "b", "c", "x"]);
  assert.deepEqual(stripPresets(LIST, "b").map((p) => p.id), ["a", "b", "c"]);
  assert.deepEqual(stripPresets(LIST, "b", true).map((p) => p.id), ["b"]);
  assert.deepEqual(stripPresets(LIST, null, true), []);
});

test("a menu opens at its button and closes", () => {
  const s = menuOpened(HL_IDLE, "a", { x: 3, y: 4 });
  assert.deepEqual(s.menu, { id: "a", x: 3, y: 4, anchorBottom: undefined });
  assert.equal(menuClosed(s).menu, null);
  assert.equal(menuClosed(HL_IDLE), HL_IDLE);
});

test("while one is armed: 1–9 arm by key, its own key or Escape turns it off, and others' keys are left alone", () => {
  const at = { list: LIST, armed: "a" };
  assert.deepEqual(highlighterKey(key("Digit2"), at), { kind: "arm", id: "b" });
  assert.deepEqual(highlighterKey(key("Numpad4"), at), { kind: "arm", id: "x" }, "a hidden one by its key");
  assert.deepEqual(highlighterKey(key("Digit1"), at), { kind: "off" }, "its own key");
  assert.deepEqual(highlighterKey(key("Escape"), at), { kind: "off" });
  assert.equal(highlighterKey(key("Digit9"), at), null, "no highlighter at that key");
  assert.equal(highlighterKey(key("Digit2"), { list: LIST, armed: null }), null, "none armed");
  for (const [e, why] of [
    [key("Digit2", { ctrlKey: true }), "a chord"], [key("Digit2", { metaKey: true }), "a chord"], [key("Digit2", { altKey: true }), "Alt"],
    [key("Digit2", { repeat: true }), "a repeat"], [key("Digit2", { isComposing: true }), "an IME"], [key("Digit2", { keyCode: 229 }), "an IME"],
  ]) assert.equal(highlighterKey(e, at), null, why);
  assert.deepEqual(highlighterKey(key("Digit2", { ctrlKey: true, altKey: true }), at), { kind: "arm", id: "b" }, "AltGr counts as none");
  for (const guard of ["inField", "dialog", "inkOpen"]) assert.equal(highlighterKey(key("Digit2"), { ...at, [guard]: true }), null, guard);
});

test("while the popup is open: 1–9 press the highlighter at that key, Enter the armed one", () => {
  assert.deepEqual(popupKey(key("Digit3"), { list: LIST, armed: null }), { kind: "pick", id: "c" });
  assert.deepEqual(popupKey(key("Enter"), { list: LIST, armed: "b" }), { kind: "pick", id: "b" });
  assert.deepEqual(popupKey(key("NumpadEnter"), { list: LIST, armed: "b" }), { kind: "pick", id: "b" });
  assert.equal(popupKey(key("Enter"), { list: LIST, armed: null }), null, "Enter with none armed is the page's");
  assert.equal(popupKey(key("Enter"), { list: LIST, armed: "gone" }), null);
  assert.equal(popupKey(key("Enter"), { list: LIST, armed: "b", onControl: true }), null, "a focused button keeps its Enter");
  assert.deepEqual(popupKey(key("Digit1"), { list: LIST, armed: "b", onControl: true }), { kind: "pick", id: "a" }, "digits still mark");
  assert.equal(popupKey(key("Digit7"), { list: LIST, armed: null }), null);
  assert.equal(popupKey(key("KeyA"), { list: LIST, armed: null }), null);
  for (const guard of ["inField", "dialog", "inkOpen"]) assert.equal(popupKey(key("Digit1"), { list: LIST, armed: null, [guard]: true }), null, guard);
});
