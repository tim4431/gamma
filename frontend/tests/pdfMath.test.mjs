import test from "node:test";
import assert from "node:assert/strict";
import { isMathRun, lineParts, spanBox, textTokens } from "../src/pdf/pdfMath.js";

const run = (str, x, face, { h = 10, w = str.length * 5, y = 100, script = null, spaced = false, asc = 0.7, desc = -0.2 } = {}) =>
  ({ str, x, y, w, h, face, script, spaced, asc, desc });

test("math runs: math fonts, symbol-heavy text, scripts and italic variables", () => {
  assert.equal(isMathRun(run("V", 0, "KXFCTZ+CMMI10")), true);
  assert.equal(isMathRun(run("|", 0, "CMSY10")), true);
  assert.equal(isMathRun(run("x", 0, "GPKYPV+KaTeX_Math Italic")), true);
  assert.equal(isMathRun(run("Ω", 0, "CMR10")), true, "uppercase Greek is set in the text font");
  assert.equal(isMathRun(run("6", 0, "CMR7", { script: "sup" })), true);
  assert.equal(isMathRun(run("x", 0, "TimesNewRomanPS-ItalicMT")), true);
  assert.equal(isMathRun(run("ansatz", 0, "TimesNewRomanPS-ItalicMT")), false, "an emphasised word");
  assert.equal(isMathRun(run("Rydberg blockade", 0, "CMTI10")), false);
  assert.equal(isMathRun(run("print()", 0, "CMTT10")), true);
  assert.equal(isMathRun(run("We consider", 0, "CMR10")), false);
  assert.equal(isMathRun(run("states", 0, "")), false);
});

test("a formula's digits, operators and brackets join it into one span", () => {
  // "V(r) = C6/r6, and pairs": CMMI V, CMR "(", CMMI r, CMR ") =", CMMI C, script 6 …
  const runs = [
    run("V", 54, "CMMI10", { w: 5.8 }), run("(", 62, "CMR10", { w: 3.9 }), run("r", 65.9, "CMMI10", { w: 4.5 }),
    run(") =", 70.7, "CMR10", { w: 16.6 }), run("C", 92.3, "CMMI10", { w: 7.1, spaced: true }),
    run("6", 99.4, "CMR7", { w: 4, script: "sub", h: 7 }), run("/r", 103.9, "CMMI10", { w: 9.5 }),
    run("6", 113.6, "CMR7", { w: 4, script: "sup", h: 7 }),
    run(", and pairs at the edge", 118.1, "CMR10", { w: 100 }),
  ];
  const parts = lineParts(runs, 10);
  assert.deepEqual(parts.map((p) => p.kind), ["math", "text"]);
  assert.equal(parts[0].runs.length, 8);
  assert.equal(parts[1].str, ", and pairs at the edge");
});

test("two formulas with words between stay two spans; the space before each stays text", () => {
  const runs = [
    run("where", 0, "CMR10", { w: 25 }), run("x", 28, "CMMI10", { w: 5, spaced: true }),
    run("and", 36, "CMR10", { w: 15, spaced: true }), run("y", 54, "CMMI10", { w: 5, spaced: true }),
    run("are positions", 62, "CMR10", { w: 60, spaced: true }),
  ];
  const parts = lineParts(runs, 10);
  assert.deepEqual(parts.map((p) => [p.kind, p.str]), [
    ["text", "where "], ["math", "x"], ["text", " and "], ["math", "y"], ["text", " are positions"],
  ]);
});

test("a span's box covers its glyphs' ascent and descent", () => {
  const box = spanBox([run("P", 10, "CMEX10", { y: 100, asc: 0.04, desc: -0.6 }), run("i", 16, "CMMI7", { y: 104, h: 7 })], 100);
  assert.equal(box.x1, 10);
  assert.ok(Math.abs(box.y1 - (104 - 7 * 0.7)) < 1e-9, "the subscript's top is above the Σ's");
  assert.ok(Math.abs(box.y2 - 106) < 1e-9, "the Σ hangs 0.6 em below its baseline");
  assert.equal(box.base, 100);
});

test("citation markers and URLs in text are literal tokens", () => {
  assert.deepEqual(textTokens("over distances of micrometers [1,2]. See https://example.org/data."), [
    { kind: "text", str: "over distances of micrometers " }, { kind: "literal", str: "[1,2]" },
    { kind: "text", str: ". See " }, { kind: "literal", str: "https://example.org/data" }, { kind: "text", str: "." },
  ]);
  assert.deepEqual(textTokens("short distance [19–21], but"), [
    { kind: "text", str: "short distance " }, { kind: "literal", str: "[19–21]" }, { kind: "text", str: ", but" },
  ]);
  assert.deepEqual(textTokens("plain [a] text"), [{ kind: "text", str: "plain [a] text" }]);
});

test("a font's name decides only for math families, and never for a run of prose", () => {
  const prose = "Probing many-body dynamics on a";
  for (const face of ["GlosaMath-Bold", "ArialUnicodeMS", "EuclidCircularA-Regular", "LucidaSansUnicode"]) {
    assert.equal(isMathRun(run(prose, 0, face)), false, face);
  }
  assert.equal(isMathRun(run("x", 0, "EuclidMathOne")), true);
  assert.equal(isMathRun(run("x", 0, "LMMathItalic10-Regular")), true);
  assert.equal(isMathRun(run("x", 0, "TeXGyreTermesMath-Regular")), true);
  assert.equal(isMathRun(run("print", 0, "SourceCodePro-Regular")), true);
});

test("an italic function word is no variable; AIP's .I italics and remapped delimiters are math", () => {
  for (const w of ["et", "if", "is", "be", "of"]) assert.equal(isMathRun(run(w, 0, "Times-Italic")), false, w);
  assert.equal(isMathRun(run("x", 0, "AdvTTd0b5fdba.I")), true);
  assert.equal(isMathRun(run("ð", 0, "AdvP4C4E74")), true, "AIP's '(' in its symbol font");
});

test("a URL token stops at the marker of the formula after it", () => {
  const marked = `see https://doi.org/10.5281/zenodo.7848423${String.fromCharCode(1)}0${String.fromCharCode(2)}. Next`;
  const literal = textTokens(marked).find((p) => p.kind === "literal");
  assert.equal(literal.str, "https://doi.org/10.5281/zenodo.7848423");
});
