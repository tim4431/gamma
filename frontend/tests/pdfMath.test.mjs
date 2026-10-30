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

test("the publishers' six-digit AdvP symbol fonts, their Pi fonts and MathTime's raw fonts are math; AdvP text fonts are not", () => {
  assert.equal(isMathRun(run("j", 0, "AdvP4C4E74")), true, "CMSY's | extracted as j");
  assert.equal(isMathRun(run("ffiffiffi", 0, "AdvP4C4E46")), true, "a radical's extension");
  assert.equal(isMathRun(run("w", 0, "AdvP3EDAA1")), true);
  assert.equal(isMathRun(run("f", 0, "AdvPi2")), true);
  assert.equal(isMathRun(run("|", 0, "AdvPSSym")), true);
  assert.equal(isMathRun(run("κ > 0", 0, "RMTMI")), true, "mostly no math characters, but a MathTime math font");
  for (const face of ["AdvP6F00", "AdvP6F0B", "AdvPAD35", "AdvPSTim"]) {
    assert.equal(isMathRun(run("Thompson,", 0, face)), false, face);
  }
  assert.equal(isMathRun(run("states of the system", 0, "AdvP4C4E74")), false, "prose in a math font stays prose");
});

test("an italic for another code page is italic: its one-letter variables are math", () => {
  assert.equal(isMathRun(run("x", 0, "AdvOTdd3b7348.I+03")), true);
  assert.equal(isMathRun(run("J", 0, "AdvOT8244d044.BI+03")), true);
  assert.equal(isMathRun(run("x", 0, "AdvOTdd3b7348+03")), false, "upright");
  assert.equal(isMathRun(run("of", 0, "AdvOTdd3b7348.I+03")), false, "a word");
});

test("an accent joins the formula it is glued to; a text accent beside its letter stays text", () => {
  assert.equal(isMathRun(run("ˆ", 0, "AdvTTbdb21c9e+02")), false, "a hat on its own is nothing yet");
  const hat = [run("where", 0, "CMR10", { w: 25 }), run("x", 28, "CMMI10", { w: 5.7, spaced: true }),
    run("ˆ", 29, "CMR10", { w: 5, y: 99 }), run("is", 37, "CMR10", { w: 8, spaced: true })];
  assert.deepEqual(lineParts(hat, 10).map((p) => [p.kind, p.str]), [["text", "where "], ["math", "xˆ"], ["text", " is"]]);
  // TeX sets the hat of the second σ̂ in one run with the "+" before it.
  const sum = [run("σ", 0, "CMMI10", { w: 5.7 }), run("+ ˆ", 8, "CMR10", { w: 12 }), run("σ", 17, "CMMI10", { w: 5.7 })];
  assert.deepEqual(lineParts(sum, 10).map((p) => p.kind), ["math"]);
  // AIP's â (library 94521ee5 p1): the italic a, no variable on its own,
  // under its math font's "^".
  const hatted = [run("the operator", 300, "AdvOT483a8203", { w: 38 }),
    run("a", 341.86, "AdvOT65f8a23b.I", { w: 4.66, spaced: true }), run("^", 341.97, "AdvP4C4E59", { w: 4.48, y: 99.5 }),
    run("acts", 352, "AdvOT483a8203", { w: 16, spaced: true })];
  assert.deepEqual(lineParts(hatted, 9).map((p) => [p.kind, p.str]), [["text", "the operator "], ["math", "a^"], ["text", " acts"]]);
  // RMP's â (library 46a97c0b p6): the hat in a text font, over the italic a,
  // which pdf.js may give before or after it.
  for (const hatX of [75.51, 74.3]) {
    const runs = [run("the operator", 30, "AdvOT483a8203", { w: 40 }), run("a", 74.44, "AdvTTd0b5fdba.I", { w: 5.41, spaced: true }),
      run("ˆ", hatX, "AdvTTbdb21c9e+02", { w: 3.22 }), run("acts", 84, "AdvOT483a8203", { w: 16, spaced: true })].sort((p, q) => p.x - q.x);
    assert.deepEqual(lineParts(runs, 10).map((p) => p.kind), ["text", "math", "text"], `hat at ${hatX}`);
  }
  assert.deepEqual(lineParts([run("is", 0, "Times-Roman", { w: 8 }), run("a", 11, "Times-Italic", { w: 5, spaced: true }),
    run("state", 19, "Times-Roman", { w: 25, spaced: true })], 10).map((p) => p.kind), ["text"], "an italic article");
  // TeX's Š: a caron raised over the S, a CMR9 run of its own (a reference's
  // "N. Šibalić", library 69d9d529 p6), and "ˇCech's" with the caron first.
  const sibalic = [
    run("[20] N.", 60, "CMR9", { h: 9, w: 22 }), run("Sibali´", 85.69, "CMR9", { h: 9, w: 24.5, spaced: true }),
    run("ˇ", 85.94, "CMR9", { h: 9, w: 4.61, y: 70.23 }), run("c, J. D. Pritchard", 110.2, "CMR9", { h: 9, w: 70 }),
  ];
  assert.deepEqual(lineParts(sibalic, 9).map((p) => p.kind), ["text"]);
  const cech = [run("in", 0, "CMR10", { w: 9 }), run("ˇ", 12.5, "CMR10", { w: 5, y: 97, spaced: true }),
    run("Cech’s laboratory", 12, "CMR10", { w: 80 })];
  assert.deepEqual(lineParts(cech, 10).map((p) => p.kind), ["text"]);
  assert.equal(isMathRun({ ...run("as measured by ˇSimon and co-workers", 0, ""), type3: true }), false,
    "an accent makes no Type3 run of prose a formula");
  assert.equal(isMathRun(run("Nazlı U˘", 0, "CMR10")), false);
});

test("⟨Ĵ⟩ set as a symbol font's h and i around an italic J and a hat is one span", () => {
  // A Reviews of Modern Physics paper's "the signal ⟨Ĵz⟩, where": CMSY's ⟨ ⟩
  // come out as h i.
  const runs = [
    run("the signal", 0, "AdvOT483a8203", { w: 45 }), run("h", 48, "AdvP4C4E74", { w: 3.5, spaced: true }),
    run("J", 51.5, "AdvTTd0b5fdba.I", { w: 4.5 }), run("ˆ", 52.5, "AdvTTbdb21c9e+02", { w: 3 }),
    run("i", 56, "AdvP4C4E74", { w: 3.5 }), run(", where", 59.5, "AdvOT483a8203", { w: 30 }),
  ];
  const parts = lineParts(runs, 10);
  assert.deepEqual(parts.map((p) => [p.kind, p.str]), [["text", "the signal "], ["math", "hJˆi"], ["text", ", where"]]);
});

test("letters set tight between a formula's delimiters are the formula's; a word between two variables is not", () => {
  // |a⟩ and |vac⟩: the text italic's a, the text font's vac between CMSY's |
  // and ⟩, which the publishers' font without a Unicode map extracts as j, i.
  const ket = (label, face) => [
    run("mode", 0, "AdvOT483a8203", { w: 22 }), run("j", 25, "AdvP4C4E74", { w: 2, spaced: true }),
    run(label, 27, face, { w: 5 * label.length }), run("i", 27 + 5 * label.length, "AdvP4C4E74", { w: 3.5 }),
    run("with spin up", 34 + 5 * label.length, "AdvOT483a8203", { w: 50, spaced: true }),
  ];
  for (const [label, face] of [["a", "AdvTTd0b5fdba.I"], ["vac", "AdvOT483a8203"]]) {
    assert.deepEqual(lineParts(ket(label, face), 10).map((p) => [p.kind, p.str]),
      [["text", "mode "], ["math", `j${label}i`], ["text", " with spin up"]], label);
  }
  // A term symbol: the text font's S between a superscript and a subscript.
  const term = [run("the", 0, "CMR10", { w: 15 }), run("1", 18, "CMR7", { w: 3.5, h: 7, script: "sup", spaced: true }),
    run("S", 21.5, "CMR10", { w: 5.5 }), run("0", 27, "CMR7", { w: 3.5, h: 7, script: "sub" }),
    run("state", 33, "CMR10", { w: 22, spaced: true })];
  assert.deepEqual(lineParts(term, 10).map((p) => [p.kind, p.str]), [["text", "the "], ["math", "1S0"], ["text", " state"]]);
  // An ion: the text font's Ca between two superscripts.
  const ion = [run("single", 0, "CMR10", { w: 27 }), run("40", 30, "CMR7", { w: 7, h: 7, script: "sup", spaced: true }),
    run("Ca", 37, "CMR10", { w: 11 }), run("+", 48, "CMR7", { w: 5, h: 7, script: "sup" }),
    run("ion", 56, "CMR10", { w: 14, spaced: true })];
  assert.deepEqual(lineParts(ion, 10).map((p) => [p.kind, p.str]), [["text", "single "], ["math", "40Ca+"], ["text", " ion"]]);
  const tex = [run("state", 0, "CMR10", { w: 25 }), run("|", 28, "CMSY10", { w: 2.8, spaced: true }),
    run("vac", 30.8, "CMR10", { w: 15 }), run("⟩", 45.8, "CMSY10", { w: 3.9 }), run("is", 53, "CMR10", { w: 8, spaced: true })];
  assert.deepEqual(lineParts(tex, 10).map((p) => [p.kind, p.str]), [["text", "state "], ["math", "|vac⟩"], ["text", " is"]]);
  const apart = [
    run("x", 0, "CMMI10", { w: 5 }), run("and", 8, "CMR10", { w: 15, spaced: true }), run("y", 26, "CMMI10", { w: 5, spaced: true }),
  ];
  assert.deepEqual(lineParts(apart, 10).map((p) => p.kind), ["math", "text", "math"]);
  // Nature's HardingText sets the word as tight against the italic variables
  // as a ket's letters (library ea3c8db8 p2, "the qubits p and q, and").
  const harding = [
    run("on the qubits", 420, "HardingText-Regular", { h: 8.25, w: 52 }),
    run("p", 474.2, "HardingText-RegularItalic", { h: 8.25, w: 4.82, spaced: true }),
    run("and", 479.94, "HardingText-Regular", { h: 8.25, w: 13.97 }),
    run("q", 494.84, "HardingText-RegularItalic", { h: 8.25, w: 4.5 }),
    run(", and introducing", 499.34, "HardingText-Regular", { h: 8.25, w: 60 }),
  ];
  assert.deepEqual(lineParts(harding, 8.25).map((p) => [p.kind, p.str]),
    [["text", "on the qubits "], ["math", "p"], ["text", "and"], ["math", "q"], ["text", ", and introducing"]]);
  // The same paper's "τ̂u and τ̂v": a subscript before the word, a Greek letter after.
  const hatted = (x, sub) => [run("τ", x, "HardingText-RegularItalic", { h: 8.25, w: 4 }),
    run("ˆ", x + 0.3, "HardingText-Regular", { h: 8.25, w: 3.5, y: 99 }),
    run(sub, x + 4, "HardingText-RegularItalic", { h: 5.8, w: 3, y: 101.5, script: "sub" })];
  const pair = [...hatted(100, "u"), run("and", 107.9, "HardingText-Regular", { h: 8.25, w: 13.97 }), ...hatted(122.77, "v")];
  assert.deepEqual(lineParts(pair, 8.25).map((p) => [p.kind, p.str]), [["math", "τˆu"], ["text", "and"], ["math", "τˆv"]]);
  // A word with its spaces inside its own run touches both variables.
  for (const [a, word, b, face] of [["x", " and ", "y", "Times-Italic"], ["α", " or ", "β", "Times-Roman"]]) {
    const runs = [run(a, 0, face, { w: 5 }), run(word, 5, "Times-Roman", { w: 4 * word.length }),
      run(b, 5 + 4 * word.length, face, { w: 5 })];
    assert.deepEqual(lineParts(runs, 10).map((p) => p.kind), ["math", "text", "math"], word);
  }
  const planck = [run("h", 0, "CMMI10", { w: 5.8 }), run("and", 6.5, "CMR10", { w: 15 }), run("i", 22, "CMMI10", { w: 3.5 })];
  assert.deepEqual(lineParts(planck, 10).map((p) => p.kind), ["math", "text", "math"], "CMMI's h and i are letters");
  // A PDF that cuts its words into pieces (library 1bc0a24f p6): the
  // emphasised "than" as italic "th", "a", "n".
  const cut = [run("th", 0, ".SFNS-RegularItalic", { w: 10 }), run("a", 10, ".SFNS-RegularItalic", { w: 5 }),
    run("n", 15, ".SFNS-RegularItalic", { w: 5 })];
  assert.equal(lineParts(cut, 10)[1].str, "a", "a letter between two italic pieces of a word");
  const cjk = [
    run("设", 0, "SimSun", { w: 10 }), run("x", 10, "CMMI10", { w: 5 }), run("为", 15, "SimSun", { w: 10 }),
    run("y", 25, "CMMI10", { w: 5 }),
  ];
  assert.deepEqual(lineParts(cjk, 10).map((p) => p.kind), ["text", "math", "text", "math"], "CJK text sets no spaces");
});

test("a URL token stops at the marker of the formula after it", () => {
  const marked = `see https://doi.org/10.5281/zenodo.7848423${String.fromCharCode(1)}0${String.fromCharCode(2)}. Next`;
  const literal = textTokens(marked).find((p) => p.kind === "literal");
  assert.equal(literal.str, "https://doi.org/10.5281/zenodo.7848423");
});
