// Where a line link marks a text file (src/documents/viewers/textMark.js):
// the number as given, a quote that finds the line where it moved to (the
// nearest of several, the range kept), a quote no line holds, and a line past
// the end.
import test from "node:test";
import assert from "node:assert/strict";
import { placeMark } from "../src/documents/viewers/textMark.js";

const FILE = [
  "\\section{Method}",
  "We fit a small network to the training set by gradient descent.",
  "",
  "\\begin{figure}",
  "  \\includegraphics{figures/fig1.pdf}",
  "\\end{figure}",
  "}",
];

test("a line link with no quote marks its number, one line or a range", () => {
  assert.deepEqual(placeMark(FILE, { line: 2, to: null, quote: "" }), { from: 2, to: 2, moved: false, past: false, lost: false });
  assert.deepEqual(placeMark(FILE, { line: 4, to: 6, quote: "" }), { from: 4, to: 6, moved: false, past: false, lost: false });
  assert.deepEqual(placeMark(FILE, { line: 6, to: 40 }), { from: 6, to: 7, moved: false, past: false, lost: false },
    "a range past the end stops at the last line");
});

test("a quote that is the line's text keeps the number; its indentation does not count", () => {
  assert.equal(placeMark(FILE, { line: 2, quote: FILE[1] }).moved, false);
  assert.deepEqual(placeMark(FILE, { line: 5, quote: "\\includegraphics{figures/fig1.pdf}" }), { from: 5, to: 5, moved: false, past: false, lost: false });
  assert.equal(placeMark(FILE, { line: 7, quote: "}" }).from, 7, "a one-character quote is a quote");
});

test("a line that moved is marked where its text is now, the range as long as it was", () => {
  const moved = ["% a", "% b", ...FILE];
  assert.deepEqual(placeMark(moved, { line: 2, to: null, quote: FILE[1] }), { from: 4, to: 4, moved: true, past: false, lost: false });
  assert.deepEqual(placeMark(moved, { line: 4, to: 6, quote: "\\begin{figure}" }), { from: 6, to: 8, moved: true, past: false, lost: false });
});

test("of several lines that hold the quote, the nearest wins, the earlier on a tie", () => {
  const lines = ["}", "a", "b", "c", "}", "d", "}"];
  assert.equal(placeMark(lines, { line: 4, quote: "}" }).from, 5);
  assert.equal(placeMark(lines, { line: 6, quote: "}" }).from, 5, "lines 5 and 7 are as near: the earlier");
  assert.equal(placeMark(lines, { line: 2, quote: "}" }).from, 1);
});

test("the quote is matched exactly, then with its spaces folded, then inside a line", () => {
  const lines = ["x = f(a,  b)", "y = f(a, b) + 1", "x = f(a, b)"];
  assert.equal(placeMark(lines, { line: 2, quote: "x = f(a, b)" }).from, 3, "the exact text before a folded match");
  assert.equal(placeMark(["one", "x  =  1", "two"], { line: 1, quote: "x = 1" }).from, 2, "spaces folded");
  assert.deepEqual(placeMark(["one", "two", "the gradient descent step"], { line: 1, quote: "gradient descent" }),
    { from: 3, to: 3, moved: true, past: false, lost: false }, "inside a line");
});

test("a quote no line holds marks the number and says so", () => {
  assert.deepEqual(placeMark(FILE, { line: 3, quote: "a line deleted since" }), { from: 3, to: 3, moved: false, past: false, lost: true });
});

test("a line past the end marks nothing, unless its quote is found", () => {
  assert.deepEqual(placeMark(FILE, { line: 9999, quote: "" }), { from: 0, to: 0, moved: false, past: true, lost: false });
  assert.deepEqual(placeMark(FILE, { line: 9999, quote: "nowhere" }), { from: 0, to: 0, moved: false, past: true, lost: true });
  assert.deepEqual(placeMark(FILE, { line: 40, quote: "\\section{Method}" }), { from: 1, to: 1, moved: true, past: false, lost: false });
  assert.deepEqual(placeMark([], { line: 1, quote: "x" }), { from: 0, to: 0, moved: false, past: true, lost: true });
});
