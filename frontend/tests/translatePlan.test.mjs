import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { segmentPage } from "../src/pdf/pdfTranslate.js";
import { isCaption, isHeading, pageLayout, readingOrder } from "../src/pdf/pageFlow.js";
import { BREAK, chunkUnits, continues, createPlanner, requestFields } from "../src/pdf/translatePlan.js";
import { partials, results, textParts } from "../src/pdf/translateReply.js";

const PAPER = JSON.parse(fs.readFileSync(new URL("./fixtures/translatePaper.json", import.meta.url)));
const PAGES = new Map(PAPER.pages.map((p) => [p.pn, segmentPage(p.runs)]));
const at = (pn, s) => PAGES.get(pn).findIndex((b) => b.text.includes(s));

const PROSE = "the ensemble of atoms shares one excitation and loses its coherence when the blockade";
// A block as the segmenter makes it, for the rules' own tests.
const block = (text, { size = 10, nLines = 3, indent = 0, translate = true, x1 = 54, x2 = 300, y1 = 100, src } = {}) =>
  ({ text, src: src ?? text, spans: [], size, nLines, indent: nLines > 1 ? indent : null, translate,
     x1, x2, y1, y2: y1 + nLines * 11.5, lines: [] });

test("reading order goes down the left column before the right one", () => {
  const blocks = PAGES.get(1);
  const order = readingOrder(blocks).map((i) => blocks[i].text);
  const pos = (s) => order.findIndex((t) => t.includes(s));
  assert.ok(pos("We study how") < pos("I. INTRODUCTION"));
  assert.ok(pos("I. INTRODUCTION") < pos("Ensembles of neutral atoms"));
  assert.ok(pos("We consider N identical atoms") < pos("Hamiltonian reads"), "left column ends before the right one starts");
  assert.ok(pos("Hamiltonian reads") < pos("III. RESULTS"));
});

test("a full-width band of whitespace starts a region: the reference list reads after both columns", () => {
  const blocks = PAGES.get(2);
  const { order, col } = pageLayout(blocks);
  const conclusion = at(2, "We have shown that"), carried = at(2, "with a rate that scales");
  const refs = blocks.findIndex((b) => b.text.startsWith("[1]"));
  assert.ok(order.indexOf(conclusion) < order.indexOf(carried));
  assert.ok(order.indexOf(carried) < order.indexOf(refs));
  assert.notEqual(col[conclusion], col[carried]);
  assert.notEqual(col[conclusion].split(".")[0], col[refs].split(".")[0]);
});

test("a gap beside a figure is no region break: the column under the figure reads first", () => {
  // A figure fills the left column's top; the right column has a heading
  // with space around it there.
  const blocks = [
    block("right column paragraph at the top of the page " + PROSE, { x1: 316, x2: 562, y1: 60, nLines: 6 }),
    block("4. Comparison", { x1: 316, x2: 420, y1: 150, nLines: 1 }),
    block("right column paragraph after the heading " + PROSE, { x1: 316, x2: 562, y1: 180, nLines: 12 }),
    block("of sigma polarized light, left column text under the figure " + PROSE, { x1: 54, x2: 300, y1: 300, nLines: 10 }),
  ];
  const order = readingOrder(blocks);
  assert.deepEqual(order, [3, 0, 1, 2]);
});

test("headings and captions are told from running text", () => {
  assert.equal(isHeading(block("II. MODEL", { nLines: 1 }), 10), true);
  assert.equal(isHeading(block("Acknowledgments", { nLines: 1 }), 10), true);
  assert.equal(isHeading(block("Hamiltonian reads", { nLines: 1 }), 10), false);
  for (const h of ["A. Results and discussion", "V. CONCLUSION AND OUTLOOK", "B. Storage and retrieval", "A. Preliminaries",
                   "1 | INTRODUCTION", "A Proof of Theorem 1", "2 Methods", "II Model"]) {
    assert.equal(isHeading(block(h, { nLines: 1 }), 10), true, h);
  }
  for (const b of ["M. Saffman et al.", "12 J. Smith and B. Jones", "12 | Nature | Vol 600 | 14 October 2021", "Y. T. LIN, Y. TIAN, D. PEREZ, AND D. LIVESCU",
                   "2 | SMITH ET AL.", "M. Saffman and T. G. Walker", "A similar argument holds for the"]) {
    assert.equal(isHeading(block(b, { nLines: 1 }), 10), false, b);
  }
  for (const c of ["TABLE I. Fitted cloud widths.", "FIG. 2. The measured decay", "FIG. S.2. Detection fidelity",
                   "Figure A1. The setup", "Fig. A.1. Optics", "Supplemental Information Fig. S3. left the profile",
                   "Extended Data Fig. 3 | Spectra", "Figure 4: Results"]) {
    assert.equal(isCaption(block(c)), true, c);
  }
  for (const c of ["FIG. 1 (color). (a) Relevant levels", "FIG. 3 (color online). Accuracy and loss",
                   "FIGURE 1 (a) Schematic illustration", "Fig. 1 Conceptual overview. a Illustration", "TABLE II Parameters",
                   "Figure 2 (left): scaled dot-product attention."]) {
    assert.equal(isCaption(block(c)), true, c);
  }
  for (const t of ["Figure 1 compares the predicted rate", "Figs. 2–4 show the decay", "Tables 1–3 summarize the fits",
                   "Fig. 4 (solid line in (a)) along with the fit", "Figs. 2 Panels", "Figure 2 (left) shows the decay"]) {
    assert.equal(isCaption(block(t)), false, t);
  }
});

test("a paragraph goes on in the next block only when it breaks off mid-sentence", () => {
  const a = block(`${PROSE}. In the rotating frame the`);
  assert.equal(continues(a, block("Hamiltonian reads", { nLines: 1 }), 10), true, "one capitalised line after a word no sentence ends on");
  assert.equal(continues(block(`${PROSE} a weighted sum`), block("Scaled Dot-Product Attention", { nLines: 1 }), 10), false);
  assert.equal(continues(block(`${PROSE}. It ends here.`), block(`next paragraph starts ${PROSE}`), 10), false);
  assert.equal(continues(a, block(`opens a paragraph ${PROSE}`, { indent: 10 }), 10), false);
  assert.equal(continues(a, block("III. RESULTS", { nLines: 1 }), 10), false);
  assert.equal(continues(a, block(`smaller footnote text ${PROSE}`, { size: 8 }), 10), false);
  // The rest of a caption is a point smaller: near the body's size, no part of a paragraph.
  assert.equal(continues(a, block(`tions. a, Two-spin quantum walk under ${PROSE}`, { size: 8.97 }), 10), false);
  assert.equal(continues(a, block(`goes on at a size that rounds apart ${PROSE}`, { size: 9.96 }), 10), true);
  assert.equal(continues(block("one line", { nLines: 1 }), block(`goes on ${PROSE}`), 10), false);
  assert.equal(continues(a, block(`goes on without indent ${PROSE}`), 10), true);
  // A hyphenated word goes on in lower case only: not into an axis label.
  const hyphen = block(`${PROSE} creating a dipole trap at the small waist lo-`);
  assert.equal(continues(hyphen, block("Light-Cavity Detuning (MHz)", { nLines: 1 }), 10), false);
  assert.equal(continues(hyphen, block("cation (see SI D)", { nLines: 1 }), 10), true);
  // Labels and table cells are no running text.
  assert.equal(continues(block("Dichroic: 780/785", { nLines: 2 }), block("Pellicle beamsplitter", { nLines: 2 }), 10), false);
  assert.equal(continues(block(`${PROSE} the`), block("Rb Rb Cs Rb K Li Sr Sr", { nLines: 2 }), 10), false);
  // A one-line B is a paragraph's last line, or introduces an equation;
  // a narrow label after "…will be" is neither.
  const be = block(`${PROSE}, a possible realization will be`);
  assert.equal(continues(be, block("Time, T", { nLines: 1 }), 10, { wide: false }), false);
  assert.equal(continues(be, block("Protocol", { nLines: 1 }), 10, { wide: false, intro: true }), true);
  assert.equal(continues(be, block("given by the ratio of the rates.", { nLines: 1 }), 10, { wide: false }), true);
  // Without indents on the page, a capitalised B opens a paragraph.
  const reveal = block(`${PROSE}. These data reveal`);
  const next = block(`At the full trap depth we measure a coherence time ${PROSE}`);
  assert.equal(continues(reveal, next, 10, { spaced: true }), false);
  assert.equal(continues(reveal, next, 10, { spaced: false }), true);
  // A citation superscript after the period still ends the sentence.
  assert.equal(continues(block(`${PROSE} and coherent atom rearrangement.45`, { src: `${PROSE} and coherent atom rearrangement.{v1}` }),
    block(`furthermore such methods ${PROSE}`), 10), false);
});

test("the fixture's paragraphs join across both column breaks and the page break", () => {
  const planner = createPlanner({ paras: (pn) => PAGES.get(pn) || null, inJob: (pn) => PAGES.has(pn) });
  const units = [...planner.planPage(1), ...planner.planPage(2)];
  const joined = units.filter((u) => u.targets.length > 1);
  assert.deepEqual(joined.map((u) => u.targets.map((t) => t.pn)), [[1, 1], [1, 2], [2, 2]]);
  const [column, page] = joined;
  assert.ok(column.src.includes(`In the rotating frame the ${BREAK} Hamiltonian reads`), column.src);
  assert.ok(page.src.includes(`design rule for ${BREAK} superatom experiments`), page.src);
  // Every translatable block is planned exactly once.
  const planned = units.flatMap((u) => u.targets.map((t) => `${t.pn}:${t.i}`));
  assert.equal(new Set(planned).size, planned.length);
  const translatable = [...PAGES].flatMap(([pn, bs]) => bs.map((b, i) => (b.translate ? `${pn}:${i}` : null)).filter(Boolean));
  assert.deepEqual([...planned].sort(), translatable.sort());
  // Its four references sit in two blocks, in two columns: no chain, so no
  // list (the rule counts blocks), and with `leave` too they are planned.
  const left = [];
  const leaving = createPlanner({ paras: (pn) => PAGES.get(pn) || null, inJob: (pn) => PAGES.has(pn), leave: (pn, i) => left.push(`${pn}:${i}`) });
  const all = [...leaving.planPage(1), ...leaving.planPage(2)];
  assert.deepEqual(left, []);
  assert.equal(all.length, units.length);
  assert.ok(all.some((u) => u.src.includes("Nat. Phys.")), "a reference is among them");
});

test("a block asked for alone is its whole paragraph, and a figure's text is a unit of its own", () => {
  const paras = (pn) => PAGES.get(pn) || null;
  const second = at(1, "Hamiltonian reads");
  // Either part of the paragraph the column break cut is the whole of it.
  for (const i of [second, at(1, "In the rotating frame the")]) {
    const unit = createPlanner({ paras, inJob: (pn) => pn === 1 }).planBlock(1, i);
    assert.ok(unit.src.includes(`In the rotating frame the ${BREAK} Hamiltonian reads`), unit.src);
    assert.ok(unit.section && "before" in unit && "after" in unit);
  }
  // A block that is no text to translate has none, and a block once.
  const planner = createPlanner({ paras });
  const equation = PAGES.get(1).findIndex((b) => !b.translate);
  assert.equal(planner.planBlock(1, equation), null);
  assert.ok(planner.planBlock(1, second));
  assert.equal(planner.planBlock(1, second), null, "claimed");
  const figure = PAGES.get(1).map((b) => ({ ...b }));
  Object.assign(figure[equation], { figure: true });
  const unit = createPlanner({ paras: (pn) => (pn === 1 ? figure : null) }).planBlock(1, equation);
  assert.deepEqual(unit.targets, [{ pn: 1, i: equation }]);
});

test("a reference list's block asked for alone is planned, the list check aside", () => {
  const paras = new Map([[1, [block(`Some text ${PROSE}.`, { nLines: 6, y1: 60 }),
    ...Array.from({ length: 8 }, (_, k) => block(`[${k + 1}] A. Smith and B. Jones, Phys. Rev. Lett. ${96 + k}, 01040${k} (200${k}).`, { nLines: 2, y1: 140 + 26 * k }))]]]);
  const left = [];
  const planner = createPlanner({ paras: (pn) => paras.get(pn) || null, leave: (pn, i) => left.push(i) });
  assert.equal(planner.planPage(1).length, 1);
  assert.ok(left.includes(3), `the list is left: ${left}`);
  const again = createPlanner({ paras: (pn) => paras.get(pn) || null, leave: () => assert.fail("no list check") });
  assert.deepEqual(again.planBlock(1, 3).targets, [{ pn: 1, i: 3 }]);
});

test("a unit renumbers its blocks' formula tokens and carries its context", () => {
  const planner = createPlanner({ paras: (pn) => PAGES.get(pn) || null, inJob: (pn) => PAGES.has(pn) });
  const units = [...planner.planPage(1), ...planner.planPage(2)];
  const column = units.find((u) => u.src.includes(`rotating frame the ${BREAK} Hamiltonian`));
  const numbers = [...column.src.matchAll(/\{v(\d+)\}/g)].map((m) => Number(m[1]));
  assert.deepEqual(numbers, numbers.map((_, k) => k + 1), "one sequence across the parts");
  assert.ok(column.tokens.every((t) => column.targets.some((x) => x.pn === t.pn && x.i === t.i)));
  const where = units.find((u) => u.src.startsWith("where"));
  const equation = PAGES.get(1).find((b) => !b.translate && b.text.includes("Vij ninj"));
  assert.equal(where.before, equation.text.trim(), "the text before a 'where' paragraph is its equation");
  assert.equal(where.section, "II. MODEL");
  const chunks = chunkUnits(units);
  assert.ok(chunks.every((c) => c.texts.length <= 10));
  assert.deepEqual(Object.keys(chunks[0].context), ["before", "after", "section"]);
});

test("a running head at body size is furniture: a page-break join skips it", () => {
  const prev = [block(`${PROSE} of the cloud is described by the`, { nLines: 20, y1: 60 })];
  const next = [
    block("SMITH et al.", { nLines: 1, y1: 40, x1: 260, x2: 330 }),
    block(`thermal distribution of the atoms in the trap ${PROSE}`, { nLines: 20, y1: 70 }),
  ];
  const pages = new Map([[1, prev], [2, next]]);
  const units = createPlanner({ paras: (pn) => pages.get(pn) || null }).planPage(1);
  assert.deepEqual(units[0].targets, [{ pn: 1, i: 0 }, { pn: 2, i: 1 }]);
  // One that opens on an initial or a folio looks numbered, and is no heading.
  for (const head of ["M. Saffman et al.", "12 J. Smith and B. Jones", "12 | Nature | Vol 600 | 14 October 2021"]) {
    const heads = new Map([[1, prev], [2, [block(head, { nLines: 1, y1: 40, x1: 260, x2: 330 }), next[1]]]]);
    const planner = createPlanner({ paras: (pn) => heads.get(pn) || null });
    const [unit] = planner.planPage(1);
    assert.deepEqual(unit.targets, [{ pn: 1, i: 0 }, { pn: 2, i: 1 }], head);
    assert.equal(planner.planPage(2).find((u) => u.src === head).section, "", head);
  }
  // A section heading at the page's top stays in the flow.
  const heading = new Map([[1, prev], [2, [block("A. Results and discussion", { nLines: 1, y1: 40, x1: 260, x2: 430 }), next[1]]]]);
  assert.deepEqual(createPlanner({ paras: (pn) => heading.get(pn) || null }).planPage(1)[0].targets, [{ pn: 1, i: 0 }]);
});

test("a folio under two columns is furniture: the paragraph the column break cut joins past it", () => {
  const page = (foot) => [
    block(`${PROSE} of the cloud is described by the`, { nLines: 20, y1: 60 }),
    block(`thermal distribution of the atoms in the trap ${PROSE}`, { x1: 316, x2: 562, nLines: 20, y1: 60 }),
    block(foot, { nLines: 1, y1: 720, x1: 280, x2: 330, translate: false }),
  ];
  const first = (foot) => createPlanner({ paras: () => page(foot), inJob: (pn) => pn === 1 }).planPage(1)[0].targets.map((t) => t.i);
  for (const foot of ["035002-2", "110404-4", "1700123", "L12345"]) assert.deepEqual(first(foot), [0, 1], foot);
  // A row of ticks over the page's foot, or an equation there, has no folio's shape.
  for (const foot of ["0 1 2 3 4", "0.2 0.4 0.6", "(1) (2) (3)", "-0.25", "X - 41", "4/3 2"]) assert.deepEqual(first(foot), [0], foot);
});

test("a page with no flow of its own is walked past: the join and the section reach the page after it", () => {
  const pages = new Map([
    [1, [block("2. The Method", { nLines: 1, y1: 60 }), block(`${PROSE} of the cloud is described by the`, { nLines: 20, y1: 80 })]],
    [2, [block("FIG. 1. The whole setup on a page of its own.", { nLines: 2, y1: 700 })]],
    [3, [block(`thermal distribution of the atoms in the trap ${PROSE}`, { nLines: 20, y1: 70 }),
         block(`A new paragraph opens here and it goes on. ${PROSE}.`, { nLines: 10, y1: 320, indent: 12 })]],
  ]);
  const planner = createPlanner({ paras: (pn) => pages.get(pn) || null });
  const units = planner.planPage(1);
  assert.deepEqual(units[1].targets, [{ pn: 1, i: 1 }, { pn: 3, i: 0 }], "the paragraph joins past the figure's page");
  assert.equal(units[1].section, "2. The Method");
  const rest = planner.planPage(3);
  assert.equal(rest[0].section, "2. The Method", "later pages keep the section");
  // A page that holds no text at all, and a gap in what is segmented.
  const blank = new Map([...pages, [2, []]]);
  assert.equal(createPlanner({ paras: (pn) => blank.get(pn) || null }).planPage(3)[1].section, "2. The Method");
  const unread = new Map([[1, pages.get(1)], [3, pages.get(3)]]);
  assert.equal(createPlanner({ paras: (pn) => unread.get(pn) || null }).planPage(3)[0].section, "", "an unsegmented page stops the walk");
});

test("a page planned before the page its paragraph starts on still joins it, once", () => {
  const planner = createPlanner({ paras: (pn) => PAGES.get(pn) || null, inJob: () => true });
  const first = planner.planPage(2);
  const carried = first.find((u) => u.targets.some((t) => t.pn === 1));
  assert.deepEqual(carried.targets, [{ pn: 1, i: at(1, "Figure 1 compares") }, { pn: 2, i: at(2, "superatom experiments") }]);
  assert.ok(!planner.planPage(1).some((u) => u.targets.some((t) => t.pn === 2)));
});

test("walking back to a long paragraph's start never drops the block being planned", () => {
  const long = (n) => `${PROSE} `.repeat(Math.ceil(n / PROSE.length)).slice(0, n).trim();
  const pages = new Map([[1, [block(`${long(4400)} the`, { nLines: 40 })]], [2, [block(`continued ${long(3300)}.`, { nLines: 30 })]]]);
  const planner = createPlanner({ paras: (pn) => pages.get(pn) || null });
  const second = planner.planPage(2);
  assert.ok(second.some((u) => u.targets.some((t) => t.pn === 2 && t.i === 0)), "page 2's block is planned");
  const first = planner.planPage(1);
  assert.deepEqual(first.flatMap((u) => u.targets), [{ pn: 1, i: 0 }]);
});

test("a centred heading that wraps is one unit, and an author stack stays apart", () => {
  const body = block(`${PROSE}.`, { nLines: 8, y1: 60 });
  const after = block(`${PROSE}.`, { nLines: 8, y1: 300 });
  const centred = (text, y1, half, over = {}) => block(text, { nLines: 1, y1, x1: 177 - half, x2: 177 + half, ...over });
  const plan = (...lines) => {
    const page = [body, ...lines, after];
    const units = createPlanner({ paras: (pn) => (pn === 1 ? page : null) }).planPage(1);
    return units.slice(1, -1).map((u) => u.src);
  };
  assert.deepEqual(plan(centred("II. EXACT DYNAMICS OF A RELEASED", 200, 110), centred("WAVE PACKET", 211, 40)),
    [`II. EXACT DYNAMICS OF A RELEASED ${BREAK} WAVE PACKET`]);
  assert.deepEqual(plan(centred("A. Phase-plane formulation and the cost of a", 200, 110), centred("sudden catch", 211, 40)),
    [`A. Phase-plane formulation and the cost of a ${BREAK} sudden catch`]);
  assert.deepEqual(plan(centred("APPENDIX B: AB INITIO RYDBERG ERROR", 200, 110), centred("MODEL", 211, 20)),
    [`APPENDIX B: AB INITIO RYDBERG ERROR ${BREAK} MODEL`]);
  assert.deepEqual(plan(centred("APPENDIX A: EXPERIMENTAL SETUP AND", 200, 110, { size: 12 }), centred("DATA ANALYSIS", 211, 40, { size: 12 })),
    [`APPENDIX A: EXPERIMENTAL SETUP AND ${BREAK} DATA ANALYSIS`], "set large too");
  // An author stack, set large or in capitals, whatever its lines' widths.
  for (const stack of [["Alice Smith, Bob Jones and Carol White", "Dan Brown and Eve Black"], ["ALICE SMITH", "BOB JONES"],
                       ["ALICE SMITH, BOB JONES, CAROL WHITE, DAN BROWN", "EVE BLACK"], ["Harvard University, Department of Physics,", "Cambridge, MA 02138, USA"],
                       ["Massachusetts Institute of Technology", "Harvard University"]]) {
    const [a, b] = stack;
    const lines = [centred(a, 200, a.length * 3, { size: 12 }), centred(b, 212, b.length * 3, { size: 12 })];
    assert.deepEqual(plan(...lines), stack, a);
  }
  // A mark makes a line's number no section number.
  assert.deepEqual(plan(centred("1 Department of Physics, University of Somewhere", 200, 110, { src: "{v1} Department of Physics, University of Somewhere", size: 12 }),
    centred("Somewhere, Country", 212, 40, { size: 12 })), ["{v1} Department of Physics, University of Somewhere", "Somewhere, Country"]);
  // Not under the line's middle, or further away, or numbered itself.
  assert.deepEqual(plan(centred("II. THE MODEL AND ITS PARAMETERS HERE", 200, 110), block("WAVE PACKET", { nLines: 1, y1: 211, x1: 54, x2: 120 })),
    ["II. THE MODEL AND ITS PARAMETERS HERE", "WAVE PACKET"]);
  assert.deepEqual(plan(centred("II. EXACT DYNAMICS OF A RELEASED", 200, 110), centred("WAVE PACKET", 230, 40)),
    ["II. EXACT DYNAMICS OF A RELEASED", "WAVE PACKET"]);
  assert.deepEqual(plan(centred("II. EXACT DYNAMICS OF A RELEASED", 200, 110), centred("A. Wave packet", 211, 40)),
    ["II. EXACT DYNAMICS OF A RELEASED", "A. Wave packet"]);
});

test("a wrapped heading is the section under it, whole", () => {
  const body = block(`${PROSE}.`, { nLines: 8, y1: 60 });
  const lines = [block("IV. NUMERICAL VALIDATION AND", { nLines: 1, y1: 200, x1: 67, x2: 287 }), block("REALISTIC CONDITIONS", { nLines: 1, y1: 211, x1: 117, x2: 237 })];
  const page = [body, ...lines, block(`${PROSE}.`, { nLines: 8, y1: 300 })];
  const units = createPlanner({ paras: (pn) => (pn === 1 ? page : null) }).planPage(1);
  assert.equal(units[units.length - 1].section, "IV. NUMERICAL VALIDATION AND REALISTIC CONDITIONS");
});

test("a section is a heading that names one: no running head, label, formula, fragment or sentence", () => {
  const under = (blocks) => {
    const units = createPlanner({ paras: (pn) => (pn === 1 ? blocks : null) }).planPage(1);
    return units[units.length - 1].section;
  };
  const para = (y1) => block(`${PROSE}.`, { nLines: 12, y1, x2: 562 });
  const heading = block("2. The Method", { nLines: 1, y1: 60 });
  for (const text of ["and sta-", "the main text", ". For either state, the", "(a) Panel", "1 and not too large detuning", "70569 Stuttgart, Germany",
                      "RB BR RR", "Let U be the matrix that holds them:", "The Hamiltonian of the", "3. If we exchange two rows, the sign changes."]) {
    assert.equal(under([heading, block(text, { nLines: 1, y1: 80, size: 12 }), para(100)]), "2. The Method", text);
  }
  assert.equal(under([heading, block("A{v1}A", { nLines: 1, y1: 80, size: 12, src: "{v1} = I" }), para(100)]), "2. The Method", "a bare formula");
  assert.equal(under([heading, block("2.1. The trap", { nLines: 1, y1: 80 }), para(100)]), "2.1. The trap");
  assert.equal(under([heading, block("3. The Outlook.", { nLines: 1, y1: 80 }), para(100)]), "3. The Outlook.", "a short heading ends in a period in some styles");
  // A numbered line right under a paragraph is a line of it, and a heading
  // right under a heading is one.
  assert.equal(under([heading, block(`${PROSE}.`, { nLines: 2, y1: 90 }), block("1. The Resonator supports a mode with waist size", { nLines: 1, y1: 113 }), para(130)]), "2. The Method");
  assert.equal(under([heading, block("A. The Trap", { nLines: 1, y1: 72 }), para(100)]), "A. The Trap");
  const nihongo = block("第二章 実験方法", { nLines: 1, y1: 300, size: 12 });
  assert.equal(under([heading, para(100), nihongo, para(320)]), "第二章 実験方法");
});

test("a text in the same place on two pages is a running head, and a contents line that recurs as a heading is not", () => {
  const head = (pn) => block(`${pn} Quantum Sensing`, { nLines: 1, y1: 72, x1: 54, x2: 200 });
  const page = (pn, ...rest) => [head(pn), block(`${PROSE}.`, { nLines: 12, y1: 90, x2: 562 }), ...rest];
  const pages = new Map([[1, [block("1. Opening", { nLines: 1, y1: 72 }), block(`${PROSE}.`, { nLines: 12, y1: 90, x2: 562 })]],
    [2, page(2)], [3, page(3)]]);
  const units = createPlanner({ paras: (pn) => pages.get(pn) || null }).planPage(3);
  assert.equal(units.find((u) => u.src.startsWith("the ensemble")).section, "1. Opening");
  const contents = new Map([[1, [block("A. The Trap", { nLines: 1, y1: 300 })]],
    [2, [block("A. The Trap", { nLines: 1, y1: 72 }), block(`${PROSE}.`, { nLines: 12, y1: 90, x2: 562 })]]]);
  assert.equal(createPlanner({ paras: (pn) => contents.get(pn) || null }).planPage(2)[1].section, "A. The Trap");
});

test("the section is found however far back it stands, and a caption sits under the page's last heading", () => {
  const pages = new Map([[1, [block("2. The Method", { nLines: 1, y1: 60 })]]]);
  for (let pn = 2; pn <= 12; pn++) pages.set(pn, Array.from({ length: 12 }, (_, k) => block(`${PROSE}.`, { nLines: 3, y1: 60 + k * 40, x2: 562 })));
  pages.get(12).push(block("FIG. 1. The setup.", { nLines: 2, y1: 700 }));
  const units = createPlanner({ paras: (pn) => pages.get(pn) || null }).planPage(12);
  assert.ok(units.every((u) => u.section === "2. The Method"), units.map((u) => u.section).join("|"));
});

test("a page outside the job is never joined into; filled blocks are skipped", () => {
  const alone = createPlanner({ paras: (pn) => PAGES.get(pn) || null, inJob: (pn) => pn === 1 }).planPage(1);
  assert.ok(alone.every((u) => u.targets.every((t) => t.pn === 1)));
  const own = createPlanner({ paras: (pn) => (pn === 1 ? PAGES.get(1) : null) }).planPage(1);
  assert.equal(own.filter((u) => u.targets.length > 1).length, 1, "the column join stays");
  const skip = at(1, "Ensembles of neutral atoms");
  const units = createPlanner({ paras: (pn) => PAGES.get(pn) || null, filled: (pn, i) => pn === 1 && i === skip }).planPage(1);
  assert.ok(!units.some((u) => u.targets.some((t) => t.i === skip)));
});

test("chunks close at their unit or character limit and carry the edges' context", () => {
  const unit = (n, k) => ({ src: "x".repeat(n), chars: n, parts: ["x"], targets: [{ pn: 1, i: k }], tokens: [],
                            before: `before ${k}`, after: `after ${k}`, section: `section ${k}` });
  const chunks = chunkUnits([unit(10, 0), unit(10, 1), unit(10, 2)], { maxUnits: 2 });
  assert.deepEqual(chunks.map((c) => c.texts.length), [2, 1]);
  assert.deepEqual(chunks[0].context, { before: "before 0", after: "after 1", section: "section 0" });
  assert.deepEqual(chunkUnits([unit(1300, 0), unit(1300, 1), unit(10, 2)], { maxChars: 2500 }).map((c) => c.texts.length), [2, 1]);
});

test("a request's fields name the paper, whether it waits for the brief and whether the server may keep it", () => {
  assert.deepEqual(requestFields({ pageId: "p", waitBrief: false, before: "b", section: "" }),
    { page_id: "p", wait_brief: false, keep: true, before: "b" });
  assert.deepEqual(requestFields({ head: "Opening text", keep: false, after: "a", section: "1 Introduction" }),
    { head: "Opening text", wait_brief: true, keep: false, after: "a", section: "1 Introduction" });
  assert.deepEqual(requestFields(), { wait_brief: true, keep: true }, "by default a request waits for the brief and may be kept");
  assert.deepEqual(requestFields({ pageId: "p", fresh: true }), { page_id: "p", wait_brief: true, keep: true, fresh: true },
    "a text asked for afresh says so");
});

const unit2 = (parts, tokens = []) => ({ parts, targets: parts.map((_, k) => ({ pn: 1, i: k })), tokens });

test("a joined translation is split back at the marker, or by length when the model lost it", () => {
  const u = unit2(["In the rotating frame the", "Hamiltonian reads"]);
  assert.deepEqual(results(u, "在旋转坐标系中，{br}哈密顿量为").map((r) => r.text), ["在旋转坐标系中，", "哈密顿量为"]);
  const [x, y] = results(u, "在旋转坐标系中，哈密顿量写作如下形式。").map((r) => r.text);
  assert.equal(x + y, "在旋转坐标系中，哈密顿量写作如下形式。");
  assert.equal(x, "在旋转坐标系中，", "cut at the clause mark near the source's share");
  // Doubled markers: no space appears between CJK characters.
  assert.deepEqual(results(u, "在旋转坐标系中，{br}驱动系统的{br}哈密顿量为").map((r) => r.text).join(""), "在旋转坐标系中，驱动系统的哈密顿量为");
  assert.ok(results(u, "only one {br}").every((r) => r.text), "an empty half is refilled by length");
  // Never cut inside a number or after an abbreviation.
  const de = unit2(["Für eine kugelförmige Wolke erreicht die Blockadetreue", "0.987 bei der optimalen Rabi-Frequenz und fällt dann ab."]);
  for (const r of results(de, "Für eine kugelförmige Wolke erreicht die Blockadetreue 0.987 bei der optimalen Rabi-Frequenz, wie in Abb. 3 gezeigt, und fällt dann ab.")) {
    assert.ok(!/^\d|\d\.$|Abb\.$/.test(r.text), r.text);
  }
  // Nor at a comma between digits.
  const comma = unit2(["Die Blockadetreue erreicht den Wert", "0,987 und 1,000 Zählungen bei der optimalen Frequenz."]);
  for (const r of results(comma, "Die Blockadetreue erreicht den Wert 0,987 und 1,000 Zählungen bei der optimalen Frequenz.")) {
    assert.ok(!/\d,$/.test(r.text) && !/^\d/.test(r.text), r.text);
  }
  // A three-part chain keeps both markers.
  assert.deepEqual(results(unit2(["a", "b", "c"]), "甲{br}乙{br}丙").map((r) => r.text), ["甲", "乙", "丙"]);
});

test("markers lost or doubled join back with no space only between kana or Han: Hangul keeps its word spaces", () => {
  const u = unit2(["The first part of the sentence is here", "and the second part of the sentence too"]);
  const doubled = results(u, "첫 번째 부분은 {br} 여기에 {br} 있습니다 그리고 두 번째 부분도").map((r) => r.text).join(" ");
  assert.equal(doubled, "첫 번째 부분은 여기에 있습니다 그리고 두 번째 부분도");
  const three = unit2(["The first part of it", "the second part of it", "the third part of it"]);
  assert.equal(results(three, "첫 번째 부분은 {br} 여기에 있습니다").map((r) => r.text).join(" "), "첫 번째 부분은 여기에 있습니다");
  assert.equal(results(u, "最初の部分は{br}ここに{br}あります").map((r) => r.text).join(""), "最初の部分はここにあります");
});

test("a block that only held a hyphenated word's tail is masked, not re-sent", () => {
  const u = unit2(["the blockade fails at large separation.", ""]);
  assert.deepEqual(results(u, "阻塞在大间距时失效。").map((r) => r.text), ["阻塞在大间距时失效。", ""]);
  assert.deepEqual(results(unit2(["same", "text"]), "same {br} text").map((r) => r.text), [null, null], "a reply equal to the source is untranslated");
});

test("tokens land in the block they belong to, by its own numbers", () => {
  const u = { parts: ["where {v1} is", "and {v2} the rate"], targets: [{ pn: 1, i: 4 }, { pn: 2, i: 0 }],
              tokens: [{ pn: 1, i: 4, k: 3 }, { pn: 2, i: 0, k: 1 }] };
  assert.deepEqual(results(u, "其中 {v1} 是{br}而 {v2} 为速率").map((r) => r.text), ["其中 {v3} 是", "而 {v1} 为速率"]);
  assert.deepEqual(results(u, "其中 {v1} 和 {v2}{br}是速率").map((r) => r.text), ["其中 {v3} 和 {v:2.0.1}", "是速率"]);
  assert.deepEqual(partials(u, "其中 {v1} 是{br}而 {v"), [{ pn: 1, i: 4, text: "其中 {v3} 是" }, { pn: 2, i: 0, text: "而" }]);
  assert.deepEqual(partials(u, "其中 {v1} 是{b"), [{ pn: 1, i: 4, text: "其中 {v3} 是" }]);
});

test("a translation's tokens are drawn as its formulas, citation markers and URLs, the screen and the file alike", () => {
  const x = { kind: "math", str: "x", x1: 1, y1: 2, x2: 3, y2: 4, base: 3.5 };
  const omega = { kind: "math", str: "Ω", x1: 5, y1: 6, x2: 7, y2: 8, base: 7.5 };
  const spans = [x, { kind: "text", str: "[3]" }, { kind: "text", str: "https://example.org/" }];
  const spanOf = (pn, i, k) => (pn === 4 && i === 2 && k === 1 ? omega : null);
  assert.deepEqual(textParts("设 {v1} 为位置{v2}，见 {v3}", { spans, page: 3, spanOf }),
    ["设 ", { span: x, page: 3 }, " 为位置[3]，见 https://example.org/"]);
  assert.deepEqual(textParts("{v:4.2.1}{v1} 和 {v:4.2.2}{v9}。", { spans, page: 3, spanOf }),
    [{ span: omega, page: 4 }, { span: x, page: 3 }, " 和 。"], "a token naming no span is dropped");
  assert.deepEqual(textParts("{v:4.2.1}", { spans, page: 3 }), [], "no lookup: another block's token names nothing");
  assert.deepEqual(textParts("", { spans }), []);
  assert.deepEqual(textParts("没有公式", {}), ["没有公式"]);
});
