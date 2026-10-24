// Colored text as inline HTML (editor/mdMarks.js): the markup the color
// row and the "/" color commands write, the scanner the live editor hides
// its tags with (a colored run is an inline mark, so it nests with the
// others), and the recolor the selection toolbar's color row runs.
import assert from "node:assert/strict";
import { test } from "node:test";
import { COLOR_MARK, TEXT_COLORS, colorAt, colorSpan, recolor, scanMarks, tint } from "../src/editor/mdMarks.js";
import { colorPlan, colorStateAt } from "../src/editor/markCommands.js";

const colorSpans = (text) => scanMarks(text).filter((s) => s.marker === COLOR_MARK);
const span = (style, text) => `<span style="${style}">${text}</span>`;

// The text after a plan's changes, and its selection.
function apply(text, r) {
  let out = text;
  for (const c of [...r.changes].sort((a, b) => b.from - a.from)) out = out.slice(0, c.from) + c.insert + out.slice(c.to);
  return { text: out, sel: out.slice(r.selection.anchor, r.selection.head) };
}
const recolored = (text, from, to, patch) => {
  const r = recolor(text, from, to, patch);
  return r && apply(text, r);
};

test("colorSpan writes a text color or a translucent background tint", () => {
  assert.equal(colorSpan("#e5484d"), '<span style="color:#e5484d">');
  assert.equal(colorSpan("#e5484d", true), '<span style="background:#e5484d55">');
  assert.equal(tint("#e5484d"), "#e5484d55");
  assert.equal(TEXT_COLORS.length, 8);
  assert.ok(TEXT_COLORS.every((c) => c.label && /^#[0-9a-f]{6}$/.test(c.color)));
});

test("the scanner finds colored runs with their tag lengths and style", () => {
  const text = `a ${span("color:#e5484d", "red")} b ${span("background:#fde04755", "hi")} c`;
  const spans = colorSpans(text);
  assert.equal(spans.length, 2);
  const [red, tinted] = spans;
  assert.equal(text.slice(red.from, red.to), '<span style="color:#e5484d">red</span>');
  assert.equal(text.slice(red.from + red.openLen, red.to - red.closeLen), "red");
  assert.equal(red.style, "color:#e5484d");
  assert.equal(tinted.style, "background:#fde04755");
  // Hand-written variants: named colors, background-color, two properties.
  assert.equal(colorSpans('<span style="color: red">x</span>')[0].style, "color: red");
  assert.equal(colorSpans('<span style="background-color:#ff0">x</span>').length, 1);
  assert.equal(colorSpans('<span style="color:#f00; background:#ff0">x</span>')[0].style, "color:#f00; background:#ff0");
  // Not colors, not closed, or spanning lines: left alone.
  assert.equal(colorSpans('<span style="font-weight:bold">x</span>').length, 0);
  assert.equal(colorSpans('<span style="color:red">x').length, 0);
  assert.equal(colorSpans('<span style="color:red">a\nb</span>').length, 0);
  // A span inside a span: only the inner one is a run.
  const nested = `<span style="color:red">${span("background:#ff0", "x")}</span>`;
  assert.deepEqual(colorSpans(nested).map((s) => s.style), ["background:#ff0"]);
});

test("a colored run nests with the other marks, either way round", () => {
  const marks = (text) => scanMarks(text).map((s) => [s.marker, s.from, s.to]);
  assert.deepEqual(marks(`**${span("color:red", "x")}**`), [["**", 0, 36], [COLOR_MARK, 2, 34]]);
  assert.deepEqual(marks(span("color:red", "**x**")), [[COLOR_MARK, 0, 36], ["**", 24, 29]]);
  assert.deepEqual(marks(span("color:red", "<u>x</u>")), [[COLOR_MARK, 0, 39], ["<u>", 24, 32]]);
  // Crossing is no nesting: the mark scanned first keeps its place.
  assert.deepEqual(marks(`<u>a ${span("color:red", "b</u> c")}`).map(([m]) => m), ["<u>"]);
});

test("a selection of plain text is wrapped in one run, still selected", () => {
  assert.deepEqual(recolored("say word now", 4, 8, { color: "#e5484d" }),
    { text: `say ${span("color:#e5484d", "word")} now`, sel: "word" });
  assert.deepEqual(recolored("say word now", 4, 8, { background: tint("#3b82f6") }),
    { text: `say ${span("background:#3b82f655", "word")} now`, sel: "word" });
});

test("a run takes a new color, gains a tint in the same span, or loses its span", () => {
  const red = span("color:red", "word");
  assert.deepEqual(recolored(red, 24, 28, { color: "blue" }), { text: span("color:blue", "word"), sel: "word" });
  assert.deepEqual(recolored(red, 24, 28, { background: "#ff0" }), { text: span("color:red; background:#ff0", "word"), sel: "word" });
  assert.deepEqual(recolored(red, 24, 28, { color: null }), { text: "word", sel: "word" });
  // The whole run selected with its tags, or from inside its opening tag.
  assert.deepEqual(recolored(red, 0, red.length, { color: "blue" }).text, span("color:blue", "word"));
  assert.deepEqual(recolored(red, 3, 28, { color: "blue" }).text, span("color:blue", "word"));
});

test("part of a run splits it, and runs that end up alike merge", () => {
  const red = span("color:red", "abcdef");
  assert.deepEqual(recolored(red, 26, 28, { color: "blue" }),
    { text: span("color:red", "ab") + span("color:blue", "cd") + span("color:red", "ef"), sel: "cd" });
  assert.deepEqual(recolored(red, 26, 28, { color: null }),
    { text: `${span("color:red", "ab")}cd${span("color:red", "ef")}`, sel: "cd" });
  // Plain text after a run, given its color, joins it.
  const before = `${span("color:red", "ab")}cd`;
  assert.deepEqual(recolored(before, 33, 35, { color: "red" }), { text: span("color:red", "abcd"), sel: "cd" });
  // Across two runs and the text between them: one run.
  const two = `${span("color:red", "ab")} x ${span("color:blue", "cd")}`;
  assert.deepEqual(recolored(two, 0, two.length, { color: "green" }), { text: span("color:green", "ab x cd"), sel: "ab x cd" });
});

test("a recolor never cuts another mark in two", () => {
  // Half of a bold run: the whole of it is recolored.
  assert.deepEqual(recolored("**ab** cd", 3, 9, { color: "red" }), { text: span("color:red", "**ab** cd"), sel: "**ab** cd" });
  // Inside the bold: the run goes inside it.
  assert.deepEqual(recolored("**ab** cd", 2, 4, { color: "red" }), { text: `**${span("color:red", "ab")}** cd`, sel: "ab" });
});

test("each line of a multi-line selection is its own run", () => {
  assert.deepEqual(recolored("ab\ncd", 0, 5, { color: "red" }),
    { text: `${span("color:red", "ab")}\n${span("color:red", "cd")}`, sel: `ab</span>\n<span style="color:red">cd` });
});

test("a recolor that changes nothing is no edit", () => {
  assert.equal(recolor("plain", 0, 5, { color: null }), null);
  assert.equal(recolor(span("color:red", "x"), 24, 25, { color: "red" }), null);
});

test("colorAt reads the style a selection's runs share, null when they differ", () => {
  const red = span("color:red", "abc");
  assert.deepEqual(colorAt("plain", 0, 5), {});
  assert.deepEqual(colorAt(red, 24, 27), { color: "red" });
  assert.deepEqual(colorAt(red, 25, 26), { color: "red" }, "inside the run");
  const both = span("color:red; background:#ff0", "x");
  assert.deepEqual(colorAt(both, 0, both.length), { color: "red", background: "#ff0" });
  assert.equal(colorAt(`${red} plain`, 24, 38), null);
  assert.deepEqual(colorAt(red, 25, 25), { color: "red" }, "a caret reads its run");
});

test("color means nothing in code and math, as the marks do", () => {
  assert.equal(colorPlan("a `code` b", 3, 6, { color: "red" }), null);
  assert.equal(colorPlan("a $x+y$ b", 3, 6, { color: "red" }), null);
  assert.deepEqual(colorStateAt("a `code` b", 3, 6), { blocked: true, style: null });
  assert.deepEqual(colorStateAt("say word", 4, 8), { blocked: false, style: {} });
  assert.ok(colorPlan("say word", 4, 8, { color: "red" }));
});
