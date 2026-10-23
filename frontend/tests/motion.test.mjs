// The pure side of motion (shared/ui/motion.js, docs/dev/ui-design.md
// "Motion"): where two labels part for MorphText, the length of a computed
// transition list, and which arrivals are still under way around an element.
import assert from "node:assert/strict";
import { test } from "node:test";
import { arriving, longestMs, reducedMotion, textSplit } from "../src/shared/ui/motion.js";

test("textSplit: the letters two labels share at each end stay; the middle changes", () => {
  assert.deepEqual(textSplit("Copy", "Copied"), { head: "Cop", from: "y", to: "ied", tail: "" });
  assert.deepEqual(textSplit("Save", "Saved"), { head: "Save", from: "", to: "d", tail: "" });
  assert.deepEqual(textSplit("Check in", "Checked in"), { head: "Check", from: "", to: "ed", tail: " in" });
  assert.deepEqual(textSplit("Processing", "Done"), { head: "", from: "Processing", to: "Done", tail: "" }, "nothing shared: the whole label swaps");
  assert.deepEqual(textSplit("aa", "aaa"), { head: "aa", from: "", to: "a", tail: "" }, "the two ends never overlap");
  assert.deepEqual(textSplit("已复制", "复制"), { head: "", from: "已", to: "", tail: "复制" });
  assert.deepEqual(textSplit("👍 Like", "👍 Liked"), { head: "👍 Like", from: "", to: "d", tail: "" }, "by code point");
});

test("longestMs: the longest of a computed duration list", () => {
  assert.equal(longestMs("0.24s, 0.16s"), 240);
  assert.equal(longestMs("160ms"), 160);
  assert.equal(longestMs("0s"), 0);
  assert.equal(longestMs(""), 0);
});

test("arriving: the arrivals that move or size what they hold, on an element and what holds it", () => {
  const anim = (animationName, playState = "running") => ({ animationName, playState });
  const grow = anim("surface-in"), fade = anim("fade-in"), flash = anim("aiEditFadeA"), transition = { playState: "running" };
  const done = anim("surface-in", "finished");
  const popover = { getAnimations: () => [grow, fade, flash, transition], parentElement: null };
  const row = { getAnimations: () => [done, anim("collapse-open")], parentElement: popover };
  assert.deepEqual(arriving(row).map((a) => a.animationName), ["collapse-open", "surface-in"],
    "a body opening and the popover growing in; not a fade, a highlight, a transition or what has ended");
  assert.deepEqual(arriving({ getAnimations: () => [], parentElement: null }), []);
});

test("reducedMotion: false where the setting can't be read", () => {
  assert.equal(reducedMotion(), false);
});
