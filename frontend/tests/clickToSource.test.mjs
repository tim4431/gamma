// A click on a block's rendered markdown → the offset in its raw source the
// editor opens at (editor/clickToSource.js). Each case gives the rendered
// text around the click the way the module reads it from the DOM.
import assert from "node:assert/strict";
import { test } from "node:test";
import { gapInSource, locateInSource } from "../src/editor/clickToSource.js";

test("text inside markup maps to the same character in the source", () => {
  const src = "# Title\n\nsome **bold words** here";
  // Clicked between "bo" and "ld" in the <strong> text node.
  const at = locateInSource(src, { core: "bold words", anchor: 2, before: "Titlesome ", after: " here" });
  assert.equal(src.slice(at), "ld words** here");
});

test("repeated text is told apart by the rendered text around it", () => {
  const src = "alpha **x** one\n\nbeta *x* two";
  const at = locateInSource(src, { core: "x", anchor: 1, before: "one\nbeta ", after: " two", nth: 1 });
  assert.equal(src.slice(at), "* two");
  const first = locateInSource(src, { core: "x", anchor: 0, before: "alpha ", after: " one", nth: 0 });
  assert.equal(src.slice(first, first + 7), "x** one");
});

test("markup hidden in the rendered view (a long image url) does not break the match", () => {
  const src = "![](/api/uploads/abc.png)\n\n| a | b |\n| - | - |\n| cell | other |";
  const at = locateInSource(src, { core: "other", anchor: 3, before: "abcell", after: "" });
  assert.equal(src.slice(at), "er |");
});

test("clicked math lands at the start of its TeX", () => {
  const src = "where $x^2 + y$ holds";
  const at = locateInSource(src, { core: "x^2 + y", anchor: 0, before: "where ", after: " holds" });
  assert.equal(src.slice(at), "x^2 + y$ holds");
});

test("a core the source doesn't hold verbatim falls back to the part before or after the click", () => {
  const src = "Tom &amp; Jerry";
  const at = locateInSource(src, { core: "Tom & Jerry", anchor: 8, before: "", after: "" });
  assert.equal(src.slice(at), "rry");
  assert.equal(locateInSource(src, { core: "nowhere", anchor: 2 }), null);
});

test("a gap puts the caret on the blank line above the lower block", () => {
  const src = "first para\n\nsecond para";
  assert.deepEqual(gapInSource(src, src.indexOf("second")), { offset: 11, insert: false });
});

test("a gap with no blank line (two adjacent $$ formulas) asks for a new line", () => {
  const src = "$$a$$\n$$\nb\n$$";
  // The lower formula's TeX sits on the line after its opening $$: the span
  // moves the gap to the formula's first line.
  const spans = [{ from: 0, to: 5 }, { from: 6, to: 14 }];
  assert.deepEqual(gapInSource(src, src.indexOf("b"), spans), { offset: 6, insert: true });
});
