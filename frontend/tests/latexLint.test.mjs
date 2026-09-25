// KaTeX error underlines in raw math (editor/latexLint.js): where an error
// lands and when the caret hides it.
import assert from "node:assert/strict";
import { test } from "node:test";
import { latexErrors, visibleLatexErrors } from "../src/editor/latexLint.js";
import { insertionFor, latexCompletions } from "../src/editor/latexCompletion.js";

const range = (tex, display) => {
  const [e] = latexErrors(tex, display);
  return e && [tex.slice(e.from, e.to), e.message];
};

test("a valid formula has no errors", () => {
  assert.deepEqual(latexErrors("\\frac{a}{b} + \\sqrt[3]{x}", false), []);
  assert.deepEqual(latexErrors("\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}", true), []);
  assert.deepEqual(latexErrors("   ", false), [], "blank is not an error");
});

test("the error sits on the token KaTeX points at", () => {
  assert.deepEqual(range("\\frac{a}{b} + \\foo{x}", false), ["\\foo", "Undefined control sequence: \\foo"]);
  assert.equal(range("\\begin{aligned} a \\end{align}", true)[0], "\\end");
  assert.equal(range("a_{b}^{c}}", false)[0], "}");
  assert.equal(range("a \\right)", false)[0], "\\right");
  assert.equal(range("x^", false)[0], "^");
});

test("an unfinished formula lands on its last character", () => {
  assert.equal(range("\\mathbb{R", false)[0], "R");
  assert.equal(range("\\frac{a}{  ", false)[0], "{", "trailing blanks are skipped");
  assert.equal(range("\\left( x", false)[0], "x");
});

test("a lone trailing backslash is a command being typed, not an error", () => {
  assert.deepEqual(latexErrors("x + \\", false), []);
  assert.equal(range("\\foo \\", false)[0], "\\foo", "earlier errors still show");
});

test("the range the caret touches waits", () => {
  const errors = latexErrors("\\frac{a}{b} + \\foo{x}", false);
  const [e] = errors;
  for (const caret of [e.from, e.from + 2, e.to]) {
    assert.deepEqual(visibleLatexErrors(errors, caret), [], `caret at ${caret}`);
  }
  assert.deepEqual(visibleLatexErrors(errors, e.from - 1), errors);
  assert.deepEqual(visibleLatexErrors(errors, e.to + 1), errors);
  assert.deepEqual(visibleLatexErrors(errors, null), errors, "no caret shows everything");
  // Typing the tail of an open group: hidden at the end, shown once the
  // caret is elsewhere in the span.
  const open = latexErrors("\\mathbb{R", false);
  assert.deepEqual(visibleLatexErrors(open, 9), []);
  assert.deepEqual(visibleLatexErrors(open, 0), open);
});

test("the completion catalog never contradicts the linter", () => {
  const seen = new Map();
  for (const q of "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ") {
    for (const c of latexCompletions(q, 1000)) seen.set(c.name, c);
  }
  assert.ok(seen.size > 100, "the catalog was enumerated");
  // Modifiers that only make sense after something get that something.
  const context = {
    limits: (t) => "\\sum" + t, nolimits: (t) => "\\sum" + t,
    "middle|": (t) => "\\left(x" + t + "y\\right)",
  };
  for (const c of seen.values()) {
    const { text } = insertionFor(c, true);
    const filled = (context[c.name] || ((t) => t))(text.replace(/\{\}/g, "{x}").replace(/\[\]/g, "[2]"));
    assert.deepEqual(latexErrors(filled, true), [], `${c.name}: ${filled}`);
  }
});
