// The Return key in the chat's text fields and a comment's (chat/enterKey.js):
// what an input method's Enter does (composing, tests/hotkeys.test.mjs), and
// when Enter sends.
import assert from "node:assert/strict";
import { test } from "node:test";
import { sendsOnEnter, sendsOnShiftEnter } from "../src/chat/enterKey.js";

const key = (k, extra = {}) => ({ key: k, shiftKey: false, ctrlKey: false, metaKey: false, keyCode: 13, nativeEvent: {}, ...extra });

test("an Enter that confirms an input method's word never sends", () => {
  for (const onScreen of [false, true]) {
    assert.equal(sendsOnEnter(key("Enter", { nativeEvent: { isComposing: true } }), onScreen), false);
    assert.equal(sendsOnEnter(key("Enter", { keyCode: 229, metaKey: true }), onScreen), false);
  }
});

test("with a keyboard Enter sends and Shift+Enter breaks the line", () => {
  assert.equal(sendsOnEnter(key("Enter"), false), true);
  assert.equal(sendsOnEnter(key("Enter", { shiftKey: true }), false), false);
  assert.equal(sendsOnEnter(key("a"), false), false);
});

test("on an on-screen keyboard Return breaks the line; Ctrl/⌘+Enter still sends", () => {
  assert.equal(sendsOnEnter(key("Enter"), true), false);
  assert.equal(sendsOnEnter(key("Enter", { metaKey: true }), true), true);
  assert.equal(sendsOnEnter(key("Enter", { ctrlKey: true }), true), true);
  assert.equal(sendsOnEnter(key("Enter", { shiftKey: true, metaKey: true }), true), false);
});

test("in a comment Enter breaks the line, Shift+Enter and Ctrl/⌘+Enter send", () => {
  assert.equal(sendsOnShiftEnter(key("Enter"), false), false);
  assert.equal(sendsOnShiftEnter(key("Enter", { shiftKey: true }), false), true);
  assert.equal(sendsOnShiftEnter(key("Enter", { ctrlKey: true }), false), true);
  assert.equal(sendsOnShiftEnter(key("Enter", { metaKey: true }), false), true);
  assert.equal(sendsOnShiftEnter(key("a", { shiftKey: true }), false), false);
  assert.equal(sendsOnShiftEnter(key("Enter", { shiftKey: true, nativeEvent: { isComposing: true } }), false), false);
  assert.equal(sendsOnShiftEnter(key("Enter", { keyCode: 229, ctrlKey: true }), false), false);
});

test("in a comment on an on-screen keyboard Return breaks the line and the button sends", () => {
  assert.equal(sendsOnShiftEnter(key("Enter"), true), false);
  assert.equal(sendsOnShiftEnter(key("Enter", { shiftKey: true }), true), false);
  assert.equal(sendsOnShiftEnter(key("Enter", { metaKey: true }), true), true, "a tablet's keyboard");
});
