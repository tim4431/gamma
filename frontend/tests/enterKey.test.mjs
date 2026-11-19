// The Return key in the chat's text fields and a comment's (chat/enterKey.js):
// what an input method's Enter does (composing, tests/hotkeys.test.mjs), when
// Enter sends, and which way a follow-up sent while a reply runs goes.
import assert from "node:assert/strict";
import { test } from "node:test";
import { followUpSteers, othersOnEnter, sendsOnEnter } from "../src/chat/enterKey.js";

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

test("while a reply runs, Enter does what the follow-up preference says and Ctrl/⌘+Enter the other", () => {
  assert.equal(othersOnEnter(key("Enter", { ctrlKey: true })), true);
  assert.equal(othersOnEnter(key("Enter", { metaKey: true })), true);
  assert.equal(othersOnEnter(key("Enter", { shiftKey: true, ctrlKey: true })), true, "with Shift too");
  assert.equal(othersOnEnter(key("Enter")), false);
  assert.equal(othersOnEnter(key("Enter", { shiftKey: true })), false);
  assert.equal(othersOnEnter(key("Enter", { ctrlKey: true, nativeEvent: { isComposing: true } })), false);
  assert.equal(followUpSteers("steer"), true);
  assert.equal(followUpSteers("steer", true), false);
  assert.equal(followUpSteers("queue"), false);
  assert.equal(followUpSteers("queue", true), true);
  assert.equal(followUpSteers(undefined), true, "unset: steer");
});
