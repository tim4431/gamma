import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { isTranslatable, segmentPage, selectionParagraphs, textRuns } from "../src/pdf/pdfTranslate.js";

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

test("selectionParagraphs rejoins lines and keeps paragraph breaks", () => {
  assert.equal(selectionParagraphs("trans-\nlation of a\nline\n\nnext"), "translation of a line\n\nnext");
});
