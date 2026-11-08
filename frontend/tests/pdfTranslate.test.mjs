import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { isNameList, isTranslatable, segmentPage, selectionParagraphs, textRuns } from "../src/pdf/pdfTranslate.js";

// The fixture paper's pdf.js runs (tests/fixtures/translate/paper.tex,
// recorded by tests/bench/translateBench.mjs --record): a two-column revtex
// page with inline and display math.
const PAPER = JSON.parse(fs.readFileSync(new URL("./fixtures/translatePaper.json", import.meta.url)));
const page = (n) => segmentPage(PAPER.pages[n - 1].runs);
const blockWith = (blocks, s) => blocks.find((b) => b.text.includes(s));

// A run on a 10pt line: baseline-left (x, y), width from the text.
const run = (str, x, y, { h = 10, w = str.length * h * 0.5, font = "f1", asc = 0.7, desc = -0.2 } = {}) =>
  ({ str, x, y, w, h, font, asc, desc });
// A line of words starting at x, `gap` × h between words.
const words = (text, x, y, { gap = 0.3, h = 10, ...rest } = {}) => {
  const out = [];
  for (const word of text.split(" ")) {
    const r = run(word, x, y, { h, ...rest });
    out.push(r);
    x += r.w + gap * h;
  }
  return out;
};

test("textRuns maps items through the viewport and flags rotated text", () => {
  const vp = [1, 0, 0, -1, 0, 800];
  const [flat, up] = textRuns([
    { str: "Hello", transform: [10, 0, 0, 10, 72, 700], width: 25, fontName: "g_d0_f1" },
    { str: "arXiv", transform: [0, 10, -10, 0, 20, 400], width: 25, fontName: "g_d0_f1" },
  ], vp, { faces: new Map([["g_d0_f1", { name: "CMR10", type3: false }]]), styles: { g_d0_f1: { ascent: 0.69, descent: -0.19 } } });
  assert.deepEqual([flat.x, flat.y, flat.h, flat.face, flat.asc, flat.rotated], [72, 100, 10, "CMR10", 0.69, false]);
  assert.equal(up.rotated, true);
});

test("a block set whole in a bold or italic face says so, nearly all its text by the characters", () => {
  const set = (face, text = "Words of a heading set whole") => words(text, 72, 100).map((r) => ({ ...r, face }));
  assert.deepEqual([segmentPage(set("CMBX12"))[0].bold, segmentPage(set("CMBX12"))[0].italic], [true, false]);
  assert.deepEqual([segmentPage(set("CMTI10"))[0].bold, segmentPage(set("CMTI10"))[0].italic], [false, true]);
  assert.deepEqual([segmentPage(set("CMR10"))[0].bold, segmentPage(set("CMR10"))[0].italic], [false, false]);
  assert.equal(segmentPage(set(""))[0].bold, false, "a face without a name");

  const leadIn = [...set("CMBX10", "Notes:"), ...words("the rest of the line runs on in its roman text", 110, 100).map((r) => ({ ...r, face: "CMR10" }))];
  assert.equal(segmentPage(leadIn)[0].bold, false, "a bold lead-in");
  const mostly = [...set("CMBX10", "A heading that runs on over all these bold words"), { ...run("x", 340, 100), face: "CMR10" }];
  assert.equal(segmentPage(mostly)[0].bold, true, "one stray letter of another face");
});

test("a formula does not count against a bold block, nor a math italic for an italic one", () => {
  const bold = (text, x) => words(text, x, 100).map((r) => ({ ...r, face: "CMBX12" }));
  const formula = { ...run("Ωab", 200, 100), face: "CMMI10" };
  assert.equal(segmentPage([...bold("Dephasing under", 72), formula])[0].bold, true);
  const mathOnly = [{ ...run("Ωab", 72, 100), face: "CMMI10" }, { ...run("cdΩ", 110, 100), face: "CMMI10" }];
  assert.equal(segmentPage(mathOnly)[0].italic, false);
});

test("a block names the font most of its text is in, when the file holds it and it is no Type 3", () => {
  const roman = words("the roman text of a paragraph runs on", 72, 100).map((r) => ({ ...r, font: "g_d0_f2", face: "CMR10" }));
  const emphasis = words("in italics", 250, 100).map((r) => ({ ...r, font: "g_d0_f3", face: "CMTI10" }));
  const [p] = segmentPage([...roman, ...emphasis]);
  assert.deepEqual([p.font, p.face], ["g_d0_f2", "CMR10"]);
  const loose = roman.map((r) => ({ ...r, noFile: true }));
  assert.equal(segmentPage(loose)[0].font, undefined, "drawn in a font of the machine's: not the paper's");
  assert.equal(segmentPage(roman.map((r) => ({ ...r, type3: true })))[0].font, undefined, "a Type 3 font");
  assert.equal(segmentPage(roman.map((r) => ({ ...r, face: "" })))[0].font, undefined, "a font without a name");
  const fixture = blockWith(page(1), "Ensembles of neutral atoms");
  assert.equal(fixture.face, "CMR10");
});

test("textRuns says which runs' fonts the file does not hold", () => {
  const items = [{ str: "Hi", transform: [10, 0, 0, 10, 72, 700], width: 10, fontName: "g_d0_f1" },
                 { str: "Ho", transform: [10, 0, 0, 10, 90, 700], width: 10, fontName: "g_d0_f2" }];
  const faces = new Map([["g_d0_f1", { name: "Helvetica", type3: false, noFile: true }], ["g_d0_f2", { name: "CMR10", type3: false, noFile: false }]]);
  const [loose, held] = textRuns(items, [1, 0, 0, -1, 0, 800], { faces });
  assert.deepEqual([loose.noFile, held.noFile], [true, undefined]);
});

test("a face changes a block's style, never what goes to the model", () => {
  const set = (face) => words("Words of a heading set whole", 72, 100).map((r) => ({ ...r, face }));
  const [plain] = segmentPage(set("CMR10")), [bold] = segmentPage(set("CMBX10"));
  assert.equal(bold.src, plain.src);
  assert.equal(bold.text, plain.text);
});

test("columns stay apart where the gutter runs, though the baselines differ", () => {
  // The fixture's page 1: "…collective Rabi frequency √N Ω grows with the
  // number" (left) shares a visual line with "state |G⟩ and the symmetric
  // singly excited state |W⟩ =" (right), 2.5pt apart.
  const blocks = page(1);
  const left = blockWith(blocks, "collective Rabi frequency");
  assert.ok(left && !left.text.includes("symmetric singly"), left?.text);
  for (const b of blocks.filter((x) => x.text.length > 200 && !x.text.startsWith("We study"))) {
    assert.ok(b.x2 < 306 || b.x1 > 310, `a body block crosses the gutter: ${b.text.slice(0, 60)}`);
  }
});

test("an inline formula that sorts onto the other column's line goes back into its sentence", () => {
  // The right column sits 0.47 line heights below the left. Its formula, in a
  // font with no metrics (the defaults' taller glyph box), sorts a little
  // higher than its words, before their line exists, and within reach of
  // the left column's line.
  const text = { asc: 0.683, desc: -0.218 }, math = { asc: 0.8, desc: -0.25, font: "m" };
  const dy = 4.7;
  const runs = [
    ...words("The atoms are loaded from a cloud into", 54, 100, text),
    ...words("the tweezers and imaged after a time of", 54, 112, text),
    ...words("flight that sets how far they spread out.", 54, 124, text),
    ...words("We scan the probe across the narrow", 315, 100 + dy, text),
    ...words("space resonance at fixed intensity", 315, 112 + dy, text),
    run("I", 480, 112 + dy, math), run("≃", 488, 112 + dy, math), run("170", 496, 112 + dy, math), run("I", 514, 112 + dy, math),
    ...words("Solid lines are", 522, 112 + dy, text),
    ...words("guides to the eye for the data shown.", 315, 124 + dy, text),
  ];
  const blocks = segmentPage(runs);
  const right = blockWith(blocks, "fixed intensity");
  assert.ok(right && /intensity I ≃ 170 ?I Solid lines are guides/.test(right.text), right?.text);
  assert.ok(!blockWith(blocks, "a time of").text.includes("170"), "the left column keeps its own words");
  // Boxes of one column's lines may touch at a point; none lies over another.
  const overlap = (a, b) => Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1) > 1 && Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1) > 1;
  assert.ok(blocks.every((a, i) => blocks.every((b, j) => i === j || !overlap(a, b))), "no block lies over another");
});

test("a big operator joins the line it is drawn on, not the one its baseline sits on", () => {
  // CMEX's Σ: ascent ~0, descent −0.6. Its baseline is 4pt below the upper
  // line's, but its glyph sits on the lower line.
  const runs = [
    ...words("state of the upper line here too", 54, 100),
    run("where", 54, 111.5),
    run("P", 85, 104, { asc: 0.04, desc: -0.6, font: "ex" }),
    ...words("runs over all pairs", 100, 111.5),
  ];
  const blocks = segmentPage(runs);
  assert.deepEqual(blocks.map((b) => b.text), ["state of the upper line here too where P runs over all pairs"]);
});

test("sub- and superscripts stay in their line, before or after their glyph", () => {
  const runs = [
    run("1", 50, 96, { h: 6, font: "r6" }),
    ...words("Institute for Test Physics", 54, 100, { font: "it" }),
    run("2", 50, 107, { h: 6, font: "r6" }),
    ...words("Department of Optics", 54, 111, { font: "it" }),
    ...words("energy E", 54, 125),
    run("2", 92, 121, { h: 7, font: "r7" }),
    run("x", 97, 125),
    run("i", 102, 127, { h: 7, font: "mi7" }),
  ];
  const texts = segmentPage(runs).map((b) => b.text);
  assert.ok(texts.includes("1Institute for Test Physics 2Department of Optics"), texts.join(" | "));
  assert.ok(texts.includes("energy E2 xi"), texts.join(" | "));
});

test("an inline fraction folds into the formula on its line, not the lines above and below it", () => {
  // σ = ½ √(1 + T) on a 10pt line: the numerator and the denominator stand
  // clear of its glyphs, under the words of the line above and over those of
  // the line below. TeX's radical hangs from a baseline at the line above's.
  const math = (s, x, over = {}) => ({ ...run(s, x, 209.5, { w: 6, ...over }), face: "CMMI10" });
  const line = [
    ...words("Gaussian with", 54, 209.5),
    math("σ", 120), math("=", 126, { w: 7.7 }),
    run("1", 139.3, 205.5, { h: 7, w: 4 }), run("2", 139.3, 212.9, { h: 7, w: 4 }),
    { ...run("√", 144.4, 201.2, { w: 8.3, asc: 0.75, desc: -0.25 }), face: "CMSY10" },
    run("1 +", 152.7, 209.5, { w: 15 }), math("T", 169.9),
    ...words("due to the position", 185, 209.5),
  ];
  const blocks = segmentPage([...words("the expanded state is a sheared and wide cloud", 54, 198), ...line, ...words("momentum correlation of the two", 54, 221)]);
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].src, "the expanded state is a sheared and wide cloud Gaussian with {v1} due to the position momentum correlation of the two");
  const [formula] = blocks[0].spans;
  assert.ok(/σ/.test(formula.str) && /√/.test(formula.str) && /T/.test(formula.str), formula.str);
  assert.ok(formula.y1 > 198, "the crop stays below the line above");
  // A small label well clear of the line it stands beside is no part of it.
  const beside = segmentPage([...words("the expanded state is a sheared cloud", 54, 198), ...words("in a wide trap", 54, 209.5), run("7", 170, 205.5, { h: 7, w: 4 })]);
  assert.ok(!beside.some((b) => /trap ?7/.test(b.text)), beside.map((b) => b.text).join(" | "));
});

test("a run's own metrics stand for any radical but Computer Modern's", () => {
  const runs = [...words("the upper line of the text", 54, 198), run("√", 100, 201.2, { w: 8 }), ...words("then the lower line", 110, 209.5)];
  assert.ok(segmentPage(runs)[0].text.startsWith("the upper line"));
  assert.ok(segmentPage(runs)[0].text.includes("√"), "it stands with the line its metrics put it on");
});

test("a display equation's number at the right margin stays apart from the paragraph below it", () => {
  for (const number of ["(1)", "(12a)", "(A.7)", "(2.14)"]) {
    const blocks = segmentPage([
      ...words("the scale factor of the trap is set by the depth of it", 54, 100),
      run(number, 290, 112, { w: 12 }),
      ...words("provided the scale factor is held and so the wave packet", 54, 123),
      ...words("stays where it is in the trap for all of the time.", 54, 134),
    ]);
    assert.deepEqual(blocks.map((b) => b.text), ["the scale factor of the trap is set by the depth of it", number,
      "provided the scale factor is held and so the wave packet stays where it is in the trap for all of the time."]);
    assert.equal(blocks[1].translate, false);
  }
  // A list's number at the left edge opens its item as before.
  const item = segmentPage([run("(1)", 54, 100, { w: 12 }), ...words("provided the scale factor is held", 70, 100), ...words("and the wave packet stays in the trap.", 54, 111)]);
  assert.deepEqual(item.map((b) => b.text), ["(1) provided the scale factor is held and the wave packet stays in the trap."]);
});

test("rotated text never joins the column it runs past", () => {
  const vertical = { ...run("arXiv:2601.18506v1 [quant-ph] 26 Jan 2026", 20, 400, { w: 300 }), rotated: true };
  const blocks = segmentPage([...words("Here we show that a driven superatom", 54, 400), vertical]);
  assert.deepEqual(blocks.map((b) => b.text), ["Here we show that a driven superatom"]);
});

test("a loose line's wide space is no gutter: the lines around it run through", () => {
  const runs = [
    ...words("blockaded superatom is limited to the ground state of the", 54, 100),
    ...words("on time.", 54, 111.5),
    ...words("The Hilbert space of a perfectly blockaded atom", 54 + 45 + 11, 111.5),
    ...words("superatom is limited to the ground state and the singly", 54, 123),
  ];
  const blocks = segmentPage(runs);
  assert.equal(blocks.length, 1, blocks.map((b) => b.text).join(" | "));
});

test("a URL broken after a slash, in its own font, stays in its paragraph", () => {
  // The first line is confidently the body font, the second the URL's: a
  // font change, but the line above ended in that font.
  const runs = [
    ...words("Data and the analysis scripts for every figure are available at", 54, 100),
    run("https://x.org/", 357, 100, { font: "tt" }),
    run("superatom-dephasing", 54, 111.5, { font: "tt" }),
    run(".", 150, 111.5),
  ];
  const [block, ...rest] = segmentPage(runs);
  assert.equal(rest.length, 0, segmentPage(runs).map((b) => b.text).join(" | "));
  assert.ok(block.text.includes("https://x.org/superatom-dephasing"), block.text);
  assert.equal(segmentPage(runs.map((r) => (r.font === "tt" && r.y > 105 ? { ...r, font: "other" } : r))).length, 2,
    "the same lines in an unrelated font are two paragraphs");
});

test("a tall glyph beside the text neither anchors its line nor makes the text a script", () => {
  // A drop cap level with the third line; the right column runs level with
  // the left one across a 1.6-line gutter.
  const runs = [run("M", 54, 129.6, { h: 34, font: "dc" })];
  for (let k = 0; k < 4; k++) {
    runs.push(run("aking predictions from the data", 74, 100 + k * 11.5, { w: 226 }));
    runs.push(run("the right column carries its text", 316, 100 + k * 11.5, { w: 226 }));
  }
  const blocks = segmentPage(runs);
  assert.ok(blocks.every((b) => b.x2 < 308 || b.x1 > 308), blocks.map((b) => `${b.x1}-${b.x2}`).join(" "));
  assert.ok(blocks.every((b) => !/\{v/.test(b.src) || b.text === "M"), blocks.map((b) => b.src).join(" | "));
});

test("an OCR layer's words, a little off in size and baseline, are no scripts", () => {
  const sizes = [10.6, 9.0, 10.4, 9.1, 10.5, 8.9], shifts = [0, 1.2, -0.8, 1.2, -1, -1.2];
  let x = 54;
  const runs = "This is accomplished by tilting etalon".split(" ").map((w, k) => {
    const r = run(w, x, 100 + shifts[k], { h: sizes[k] });
    x += r.w + 3;
    return r;
  });
  const [block] = segmentPage(runs);
  assert.ok(!block.src.includes("{v"), block.src);
});

test("a page's last line splits at the gutter with only one line above it", () => {
  const runs = [];
  for (const y of [111.5, 134.5]) {
    runs.push(run("left column text of the paragraph", 54, y, { w: 246 }));
    runs.push(run("right column text of its paragraph", 316, y, { w: 246 }));
  }
  const blocks = segmentPage(runs);
  assert.ok(blocks.every((b) => b.x2 < 308 || b.x1 > 308), blocks.map((b) => `${b.x1}-${b.x2}`).join(" "));
});

test("a block records its first line's indent", () => {
  const runs = [...words("Ensembles of neutral atoms", 64, 100), ...words("excited to high-lying states", 54, 111.5)];
  const [block] = segmentPage(runs);
  assert.equal(block.indent, 10);
  assert.equal(segmentPage(words("one line", 54, 100))[0].indent, null);
});

test("isTranslatable keeps prose and skips symbol soup", () => {
  assert.equal(isTranslatable("We consider N identical atoms."), true);
  assert.equal(isTranslatable("x y z p q"), false);
  assert.equal(isTranslatable("(1)"), false);
});

test("a formula's operator names are no words: what is left of an equation stays original", () => {
  assert.equal(isTranslatable(" cos sin ;"), false);
  assert.equal(isTranslatable("cos2 sin2 = 1"), false);
  assert.equal(isTranslatable("arctan x + exp y"), false);
  assert.equal(isTranslatable("log max min"), false);
  // Fewer than four letters of words left: an equation with a word or two.
  assert.equal(isTranslatable(" cos and sin"), false);
  assert.equal(isTranslatable("The cost of the sine"), true, "a word that begins like one");
  assert.equal(isTranslatable("min of the cost, in log scale"), true);
  assert.equal(isTranslatable("Max Planck"), true, "names keep their capital");
});

test("an author line is a list of names with their marks; a name pair without marks or prose is not", () => {
  // Author lines from the library: the mark after the name or after its
  // comma, a space either side, footnote signs beside it, a stray token
  // before the first name, particles.
  for (const src of [
    "Alexandra A. Geim{v1}, Nazlı U˘gur K¨oyl¨uo˘glu{v2}, Simon J. Evered{v3}, Rahul Sahay{v4},",
    "Haruka Tanji-Suzuki{v1}, Ian D. Leroux{v2}, Jonathan Simon{v3}, and Vladan Vuleti ´c{v4}",
    "J. D. Thompson,{v1} T. G. Tiecke,{v2} A. S. Zibrov,{v3} and M. D. Lukin{v4}",
    "Yue Wu{v1}, Shimon Kolkowitz {v2}, Shruti Puri {v3} & Jeff D. Thompson{v4}",
    "B. A. Bernevig {v1} , L. Fu {v2} , L. Ju {v3} , A. H. MacDonald {v4} , K. F. Mak {v5} & J. Shan {v6}",
    "Nathanan Tantivasadakarn ,{v1} Ruben Verresen ,{v2} and Ashvin Vishwanath{v3}",
    "A. Muzi Falconi ,{v1} R. Panza ,{v2} S. Sbernardori ,{v3} R. Forti ,{v4}R. Klemt ,{v5}",
    "K. J. Satzinger{v1}*, Y.-J Liu{v2}, A. Smith{v3}, C. Knapp{v4}†, M. Newman{v5}",
    "Jouni Kainulainen,{v1}* Christoph Federrath,{v2} Thomas Henning{v3}",
    "Hannah J. Manetsch{v1}, Xudong Lv{v4}, Kon H. Leung{v5}✉ & Manuel Endres{v6}✉",
    "{v1} Ran Finkelstein{v2}, Ohad Lib{v3}, Yoav Sagi{v4}",
    "Jan van der Waals{v1}, Ana María de la Cruz{v2}",
    "Emily H. Qiu,{v1} Tamara Sumarac,ˇ{v2} Peiran Niu,{v3} Shai Tsesses,{v4}",
  ]) assert.equal(isNameList(src), true, src);
  for (const src of [
    "Roman Schmied and Philipp Treutlein",
    "Chetan Nayak{v1}",
    "Theory{v1}, Experiment{v2}",
    "where {v1}, {v2} and {v3} are the Rabi frequencies",
    "Bais {v1} and Bais et al. {v2}1992; Bais, van Driel, et al., 1993",
  ]) assert.equal(isNameList(src), false, src);
});

test("an affiliation line is no author line: its marks open the affiliation after them", () => {
  for (const src of [
    "{v1}Department of Physics, Harvard University, Cambridge, MA 02138, USA",
    "{v1}California Institute of Technology, Pasadena {v2}Harvard Quantum Initiative, Harvard University",
    "{v1}Harvard University, Cambridge {v2}Massachusetts Institute of Technology, Cambridge {v3}California Institute of Technology, Pasadena",
    "{v1}Stanford University, {v2}Google Research, {v3}UC Berkeley",
    "{v1}Stanford University {v2}Google Research {v3}UC Berkeley",
  ]) assert.equal(isNameList(src), false, src);
});

test("a title or heading in title case with a formula is no author line", () => {
  for (const src of [
    "B. Defining and Computing the Improvement Factor {v1}",
    "Spin Squeezing and Entanglement of {v1}Rb Atoms in an Optical Cavity",
    "Rydberg Atom Arrays and Quantum Simulation of {v1} Gauge Theories",
    "Topological Order and Anyons in a {v1} Toric Code",
    "Rydberg Blockade and Entanglement of Two Atoms{v1}",
    // Two names with one mark read the same as a title with a footnote: both
    // stay translatable.
    "Quantum Optics and Atomic Physics{v1}",
    "Ariel Sommer and Jonathan Simon{v1}",
  ]) assert.equal(isNameList(src), false, src);
  const sup = (str, x) => run(str, x, 96, { h: 6, w: str.length * 3, font: "r6" });
  const [title] = segmentPage([
    run("Spin Squeezing and Entanglement of", 54, 100, { w: 170 }), sup("87", 228), run("Rb Atoms in an Optical Cavity", 234, 100, { w: 140 }),
  ]);
  assert.equal(title.src, "Spin Squeezing and Entanglement of {v1}Rb Atoms in an Optical Cavity");
  assert.equal(title.translate, true);
});

test("an author line with superscript affiliation marks stays original", () => {
  const sup = (str, x) => run(str, x, 96, { h: 6, w: str.length * 3, font: "r6" });
  const runs = [
    run("Alexandra A. Geim", 54, 100, { w: 80 }), sup("1", 134),
    run(", Simon J. Evered", 137, 100, { w: 80 }), sup("2", 217),
    run(", and Mikhail D. Lukin", 220, 100, { w: 100 }), sup("1,2", 320),
    ...words("We study the dynamics of a driven superatom.", 54, 130),
  ];
  const [authors, prose] = segmentPage(runs);
  assert.equal(authors.src, "Alexandra A. Geim{v1}, Simon J. Evered{v2}, and Mikhail D. Lukin{v3}");
  assert.equal(authors.translate, false);
  assert.equal(prose.translate, true);
});

test("selectionParagraphs rejoins lines and keeps paragraph breaks", () => {
  assert.equal(selectionParagraphs("trans-\nlation of a\nline\n\nnext"), "translation of a line\n\nnext");
});
