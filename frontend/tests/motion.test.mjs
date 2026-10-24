// The pure side of motion (shared/ui/motion.js, docs/dev/ui-design.md
// "Motion"): where two labels part for MorphText, the length of a computed
// transition list, which arrivals are still under way around an element,
// and what a list's change glides (ListMotion).
import assert from "node:assert/strict";
import { test } from "node:test";
import { arriving, listGlides, longestMs, reducedMotion, sameKeys, textSplit } from "../src/shared/ui/motion.js";

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
  const glide = { getAnimations: () => [{ id: "list-move", playState: "running" }], parentElement: null };
  assert.equal(arriving({ getAnimations: () => [], parentElement: glide }).length, 1, "a row gliding to its place");
});

// A box at (x, y), 20 px tall.
const at = (x, y) => ({ x, y, h: 20 });

test("listGlides: a block inserted mid-list fades in where it rests while the rows below glide down to make room", () => {
  const items = [
    { was: at(0, 0), now: at(0, 0), up: -1 },
    { was: null, now: at(0, 20), up: -1 }, // the new row
    { was: at(0, 20), now: at(0, 40), up: -1 },
    { was: at(0, 40), now: at(0, 60), up: -1 },
  ];
  const { moves, enters } = listGlides(items);
  assert.deepEqual(moves, [{ i: 2, x: 0, y: -20 }, { i: 3, x: 0, y: -20 }], "each glides from 20 px above");
  assert.deepEqual(enters, [1]);
});

test("listGlides: what an item holds travels with it, so each glides by its own move less its holder's", () => {
  const items = [
    { was: at(0, 100), now: at(0, 0), up: -1 }, // a row moved up 100
    { was: at(14, 120), now: at(14, 20), up: -1 }, // the body of its children, beside it
    { was: at(14, 120), now: at(14, 20), up: 1 }, // a child: carried by the body
    { was: at(14, 140), now: at(28, 40), up: 1 }, // a child that was also indented
  ];
  const { moves } = listGlides(items);
  assert.deepEqual(moves, [{ i: 0, x: 0, y: 100 }, { i: 1, x: 0, y: 100 }, { i: 3, x: -14, y: 0 }]);
});

test("listGlides: a new item held by another new one does not fade on its own", () => {
  const items = [
    { was: null, now: at(0, 0), up: -1 }, // a pasted block
    { was: null, now: at(14, 20), up: -1 }, // the body of its children
    { was: null, now: at(14, 20), up: 1 }, // a child inside it
  ];
  assert.deepEqual(listGlides(items).enters, [0, 1]);
});

test("listGlides: the new body a row is indented into does not fade, so the row glides in seen", () => {
  const items = [
    { was: at(0, 0), now: at(0, 0), up: -1 }, // the row above, its new parent
    { was: null, now: at(14, 20), up: -1 }, // the body of its children, new
    { was: at(0, 20), now: at(28, 20), up: 1 }, // the row indented into it
  ];
  const { moves, enters } = listGlides(items);
  assert.deepEqual(enters, []);
  assert.deepEqual(moves, [{ i: 2, x: -28, y: 0 }]);
});

test("listGlides: nothing far from the window, no long throws, and past a cap the change lands at once", () => {
  const band = { top: -150, bottom: 950, far: 800 };
  const out = [{ was: at(0, 3000), now: at(0, 3020), up: -1 }];
  assert.deepEqual(listGlides(out, band).moves, [], "a row moving out of sight");
  const into = [{ was: at(0, 3000), now: at(0, 100), up: -1 }];
  assert.deepEqual(listGlides(into, band).moves, [], "a row thrown from far below");
  const rows = (n) => Array.from({ length: n }, (_, i) => ({ was: at(0, i * 10), now: at(0, i * 10 + 20), up: -1 }));
  assert.equal(listGlides(rows(5), { moving: 4 }).moves.length, 0, "more moving than the cap");
  assert.equal(listGlides(rows(4), { moving: 4 }).moves.length, 4);
  const fresh = (n) => Array.from({ length: n }, (_, i) => ({ was: null, now: at(0, i * 20), up: -1 }));
  assert.deepEqual(listGlides(fresh(3), { entering: 2 }).enters, [], "a paste of many blocks arrives at once");
});

test("listGlides: an item held still stays, and what it holds is not carried", () => {
  const items = [
    { was: at(0, 20), now: at(14, 0), up: -1, held: true }, // a row with a popup open in it
    { was: at(0, 40), now: at(0, 20), up: -1 },
  ];
  assert.deepEqual(listGlides(items).moves, [{ i: 1, x: 0, y: 20 }]);
  assert.deepEqual(listGlides([{ was: at(0, 0.3), now: at(0, 0), up: -1 }]).moves, [], "less than half a pixel");
});

test("reducedMotion: false where the setting can't be read", () => {
  assert.equal(reducedMotion(), false);
});

test("sameKeys: the same keys in the same order", () => {
  assert.equal(sameKeys(["a", "b"], ["a", "b"]), true);
  assert.equal(sameKeys(["a", "b"], ["b", "a"]), false);
  assert.equal(sameKeys(["a"], ["a", "b"]), false);
  assert.equal(sameKeys(null, ["a"]), false);
  const k = ["a"];
  assert.equal(sameKeys(k, k), true);
});
