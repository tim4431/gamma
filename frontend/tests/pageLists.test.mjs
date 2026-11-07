// The reference list on a page (src/pdf/pageLists.js) and how the planner and
// the page's flow read it, on hand-built blocks: a page of each kind of list
// the library holds, and the pages that look like one.
import test from "node:test";
import assert from "node:assert/strict";
import { pageFlow } from "../src/pdf/pageFlow.js";
import { inList, pageList } from "../src/pdf/pageLists.js";
import { createPlanner } from "../src/pdf/translatePlan.js";

const PROSE = "the ensemble of atoms shares one excitation and loses its coherence when the blockade is broken by the motion of the atoms in the trap";

// A block as the segmenter makes it.
const block = (text, { size = 8, nLines = 2, indent = 0, translate = true, x1 = 54, x2 = 300, y1 = 60 } = {}) =>
  ({ text, src: text, spans: [], size, nLines, indent: nLines > 1 ? indent : null, translate,
     x1, x2, y1, y2: y1 + nLines * size * 1.15, lines: [] });

// Blocks down a column, each `step` under the last one's end.
function column(texts, { y = 60, step = 2, ...over } = {}) {
  const out = [];
  for (const text of texts) {
    const b = block(text, { ...over, y1: y });
    out.push(b);
    y = b.y2 + step;
  }
  return out;
}

const listed = (paras) => [...pageList(paras).blocks].sort((a, b) => a - b);
const all = (paras) => paras.map((_, i) => i);

const BRACKETS = Array.from({ length: 8 }, (_, k) =>
  `[${k + 1}] A. Smith, B. Jones, and C. Doe, Phys. Rev. Lett. ${96 + k}, 01040${k} (200${k}).`);
const NATURE = Array.from({ length: 8 }, (_, k) =>
  `${k + 12}. Saffman, M., Walker, T. G. & Mølmer, K. Quantum information with Rydberg atoms. Rev. Mod. Phys. ${82 + k}, 2313–2371 (201${k}).`);
const AUTHOR_YEAR = Array.from({ length: 8 }, (_, k) =>
  `Pérez, J. and van der Berg, K. (20${10 + k}). A survey of the methods used in learning. Journal of Things, ${12 + k}(3), 45–60.`);
// A hanging second line is cut off as a block of its own, so an entry is two
// blocks and only one of them holds the journal and the year.
const HANGING = BRACKETS.flatMap((_, k) => [
  `[${k + 1}] A. Smith, B. Jones, C. Doe, and F. Roe, Long-range interactions`,
  `between atoms, Phys. Rev. Lett. ${96 + k}, 01040${k} (200${k}).`,
]);

test("each kind of list is read whole, by its shape: no heading is needed", () => {
  for (const [name, entries] of [["numbered [n]", BRACKETS], ["Nature's numbered", NATURE], ["author-year", AUTHOR_YEAR], ["hanging indent", HANGING]]) {
    const paras = column(entries);
    const list = pageList(paras);
    assert.equal(list.alone, true, name);
    assert.deepEqual(listed(paras), all(paras), name);
  }
});

test("a list in two columns is read column by column", () => {
  const paras = [...column(BRACKETS.slice(0, 4)), ...column(BRACKETS.slice(4), { x1: 316, x2: 562 })];
  assert.equal(pageList(paras).alone, true);
  assert.deepEqual(listed(paras), all(paras));
});

test("reading a page marks no block", () => {
  const paras = column(BRACKETS);
  const before = JSON.stringify(paras);
  pageList(paras);
  assert.equal(JSON.stringify(paras), before);
});

test("a citation table is no list: a few rows make no list, and more take their citation cells alone", () => {
  const body = column([`${PROSE}.`, `${PROSE}.`], { size: 10, nLines: 8, x2: 562, y: 400 });
  const table = (rows) => [
    ...column(Array.from({ length: rows }, (_, k) => `Method number ${k + 1}`), { nLines: 1, x2: 150, step: 3 }),
    ...column(Array.from({ length: rows }, (_, k) => `Smith et al. (20${10 + k})`), { nLines: 1, x1: 200, x2: 300, step: 3 }),
    ...body,
  ];
  assert.equal(pageList(table(5)).alone, false, "the rows of a short table");
  const long = table(7);
  assert.equal(pageList(long).alone, true);
  assert.deepEqual(listed(long), [7, 8, 9, 10, 11, 12, 13], "a long one's citation column, which reads the same translated");
});

test("prose with many citations is no list", () => {
  const paragraph = `As shown by Smith, J. et al. (2019) and Jones, K. (2020), ${PROSE}, ${PROSE}, ${PROSE}.`;
  const paras = column(Array(7).fill(paragraph), { size: 10, nLines: 8, x2: 562 });
  assert.deepEqual(listed(paras), []);
});

test("a table of contents is no list", () => {
  const paras = column(["1. Introduction 3", "2. Methods 5", "2.1 The trap 6", "3. Results 9", "4. Discussion 12", "5. Outlook 14",
    "Acknowledgments 15", "References 16", "Appendix A: The optics 21"], { size: 10, nLines: 1 });
  assert.deepEqual(listed(paras), []);
});

test("running heads are no list: a chain of one, at the page's top or foot", () => {
  const paras = [
    block("PHYSICAL REVIEW A 96, 010401 (2017)", { size: 8, nLines: 1, y1: 30 }),
    ...column([`${PROSE}.`, `${PROSE}.`], { size: 10, nLines: 20, x2: 562, y: 60 }),
    block("010401-3 Phys. Rev. A 96, 010401 (2017)", { size: 8, nLines: 1, y1: 760 }),
  ];
  assert.deepEqual(listed(paras), []);
  assert.equal(inList(0, pageList(paras), { blocks: new Set(), alone: true }, null), false, "beside a list too");
});

test("a list stops at a section label, run in or a heading, and at a paragraph", () => {
  const entries = column(BRACKETS);
  const end = entries[entries.length - 1].y2;
  const after = [
    block("Acknowledgements We thank A. Smith for reading the manuscript. Funded by grant 12 (2020).", { y1: end + 2 }),
    block("Data availability The data are at https://example.org.", { y1: end + 30 }),
  ];
  assert.deepEqual(listed([...entries, ...after]), all(entries));
  const heading = block("Author contributions", { nLines: 1, y1: end + 2 });
  assert.deepEqual(listed([...entries, heading]), all(entries));
  const note = block(`We also note that ${PROSE} and that ${PROSE}.`, { nLines: 3, y1: end + 2 });
  assert.deepEqual(listed([...entries, note]), all(entries));
  // Nor is a label inside a list's span, or its heading above it, part of it.
  const split = [...entries.slice(0, 4), block("Funding", { nLines: 1, y1: entries[3].y2 + 1 }), ...column(BRACKETS.slice(4), { y: entries[3].y2 + 16 })];
  assert.deepEqual(listed(split), [0, 1, 2, 3, 5, 6, 7, 8]);
  const titled = [block("References", { nLines: 1, y1: 44 }), ...entries];
  assert.deepEqual(listed(titled), entries.map((_, i) => i + 1));
});

test("two lists apart in a column stay two chains: what lies between them is translated", () => {
  const top = column(BRACKETS.slice(0, 4));
  const bottom = column(BRACKETS.slice(4), { y: 500 });
  const between = block(`Between them ${PROSE}.`, { y1: 300, nLines: 2 });
  const paras = [...top, between, ...bottom];
  assert.equal(pageList(paras).alone, true);
  assert.equal(pageList(paras).blocks.has(4), false);
});

// A planner over pages, and what it left.
function plan(pages, order = [...pages.keys()], over = {}) {
  const left = [];
  const planner = createPlanner({ paras: (pn) => pages.get(pn) || null, leave: (pn, i) => left.push(`${pn}:${i}`), ...over });
  const units = order.flatMap((pn) => planner.planPage(pn));
  return { left, units, sent: units.flatMap((u) => u.targets.map((t) => `${t.pn}:${t.i}`)) };
}
const keys = (pn, paras) => paras.map((_, i) => `${pn}:${i}`);

test("a page qualifies alone on six chained cites, or on two beside a page that qualifies", () => {
  const strong = column(BRACKETS);
  const end = column(BRACKETS.slice(0, 2));
  const pages = new Map([[1, strong], [2, end]]);
  assert.equal(pageList(end).alone, false);
  assert.deepEqual(plan(pages).left, [...keys(1, strong), ...keys(2, end)]);
  // Whichever page is planned first, in either direction.
  assert.deepEqual(plan(pages, [2, 1]).left.sort(), [...keys(1, strong), ...keys(2, end)].sort());
  const before = new Map([[1, end], [2, strong]]);
  assert.deepEqual(plan(before).left, [...keys(1, end), ...keys(2, strong)]);
  // One cite is no chain, beside a list or not.
  const single = [block(BRACKETS[0]), block(`${PROSE}.`, { y1: 300, nLines: 6 })];
  assert.deepEqual(plan(new Map([[1, strong], [2, single]])).left, keys(1, strong));
});

test("a page's neighbour qualifies only by itself: a list does not spread from page to page", () => {
  const strong = column(BRACKETS), weak = column(BRACKETS.slice(0, 3));
  const pages = new Map([[1, strong], [2, weak], [3, weak]]);
  assert.deepEqual(plan(pages).left, [...keys(1, strong), ...keys(2, weak)]);
});

test("a chain that counts through a neighbour reads as entries: data statements after a list stay translated", () => {
  const strong = column(BRACKETS);
  const statements = [
    block("Data availability", { nLines: 1, size: 10, y1: 60 }),
    block("The numerical data that support the findings of this study are available from the Zenodo repository at https://doi.org/10.5281/zenodo.1234.", { y1: 76 }),
    block("Code availability", { nLines: 1, size: 10, y1: 100 }),
    block("The code used for numerical simulations is available from the Zenodo repository at https://doi.org/10.5281/zenodo.5678.", { y1: 116 }),
  ];
  assert.deepEqual(listed(statements), []);
  const after = plan(new Map([[1, strong], [2, statements]]));
  assert.deepEqual(after.left, keys(1, strong));
  assert.deepEqual(after.sent.sort(), keys(2, statements).sort());
  // The end of a list cut by a hanging indent holds tails with no name or
  // opening number, and stays a list.
  const tails = column(["Journal of combinatorics, 14(1):59-73, 1998.", "Springer, New York, NY, 2013.", "Naval Research Logistics Quarterly, 2(1-2):83-97, 1955."]);
  assert.deepEqual(plan(new Map([[1, strong], [2, tails]])).left, [...keys(1, strong), ...keys(2, tails)]);
  // Alone on six cites, identifiers count as they always did.
  const preprints = column(Array.from({ length: 6 }, (_, k) => `A survey of the methods used in learning, arXiv:21${k}1.0123${k}.`));
  assert.deepEqual(listed(preprints), all(preprints));
});

test("a page whose neighbour is not segmented decides by itself, and a wrong answer translates the block", () => {
  const strong = column(BRACKETS), weak = column(BRACKETS.slice(0, 3));
  const alone = plan(new Map([[2, weak]]));
  assert.deepEqual(alone.left, []);
  assert.deepEqual(alone.sent, keys(2, weak));
  // The page beside it, segmented meanwhile, tells the planner on its next look.
  const pages = new Map([[2, weak]]);
  const left = [];
  const planner = createPlanner({ paras: (pn) => pages.get(pn) || null, leave: (pn, i) => left.push(`${pn}:${i}`) });
  pages.set(1, strong);
  planner.planPage(2);
  assert.deepEqual(left, keys(2, weak));
});

test("a block with its translation already is never left", () => {
  const paras = column(BRACKETS);
  const { left, sent } = plan(new Map([[1, paras]]), [1], { filled: (pn, i) => i === 3 });
  assert.deepEqual(left, keys(1, paras).filter((k) => k !== "1:3"));
  assert.deepEqual(sent, []);
});

test("without `leave` a list is planned like the rest", () => {
  const paras = column(BRACKETS);
  const { sent } = plan(new Map([[1, paras]]), [1], { leave: null });
  assert.deepEqual(sent, keys(1, paras));
});

test("a paragraph stops at the list's edge, walking forward or back", () => {
  // A paragraph cut by the column break, the list at the next column's top.
  const open = block(`${PROSE} and it runs on in the next column with the`, { size: 8, nLines: 12, y1: 60 });
  const entries = column(BRACKETS, { x1: 316, x2: 562, y: 60 });
  const pages = new Map([[1, [open, ...entries]]]);
  assert.deepEqual(plan(pages).sent, ["1:0"]);
  assert.ok(plan(pages, [1], { leave: null }).units[0].targets.length >= 1);
  // A page planned first: the walk back stops at the list above it.
  const list = column(BRACKETS);
  const tail = [...list, block(`goes on as the last line of ${PROSE}`, { size: 8, nLines: 12, x1: 316, x2: 562, y1: 60 })];
  const after = new Map([[1, [block(`${PROSE} with the`, { size: 8, nLines: 12 })]], [2, tail]]);
  const planned = plan(after, [2, 1]);
  assert.deepEqual(planned.sent.sort(), ["1:0", "2:8"]);
  assert.ok(planned.units.every((u) => u.targets.length === 1), "no unit reaches over the list");
});

test("a list in the flow is no section: the heading before it still heads what follows", () => {
  // Entries short enough to read as numbered headings.
  const entries = Array.from({ length: 8 }, (_, k) => `${k + 12}. Saffman, M. & Walker, T. G. Rev. Mod. Phys. ${82 + k}, 2313–2371 (201${k}).`);
  const paras = [
    block(`Before the heading ${PROSE}.`, { y1: 30 }),
    block("Outlook", { size: 10, nLines: 1, y1: 50 }),
    ...column(entries, { y: 66 }),
    block(`After the list ${PROSE}.`, { y1: 500, nLines: 3 }),
  ];
  const { units } = plan(new Map([[1, paras]]));
  assert.equal(units.at(-1).section, "Outlook");
  assert.equal(plan(new Map([[1, paras]]), [1], { leave: null }).units.at(-1).section, "Outlook");
});

test("the page's body size does not count its list, and a small list on a mixed page has columns", () => {
  const text = `${PROSE}. ${PROSE}.`;
  const paras = [
    ...column([text, text], { size: 8.25, nLines: 6, x2: 562 }),
    ...column([...NATURE, ...NATURE], { size: 6, x1: 54, x2: 300, y: 300, step: 0 }),
    ...column(NATURE, { size: 6, x1: 316, x2: 562, y: 300, step: 0 }),
  ];
  assert.equal(pageFlow(paras).body, 6, "more of the page's text is the list");
  const f = pageFlow(paras, pageList(paras));
  assert.equal(f.body, 8.25);
  assert.equal(f.flow.length, paras.length, "the list stays in the flow");
  assert.notEqual(f.col[2], f.col[18], "in its columns");
});

test("a small list beside a column of body text reads as a column after it, so the page break's paragraph joins the body", () => {
  const open = [block(`${PROSE} and it runs on to the next page with the`, { size: 8.25, nLines: 30 })];
  const carried = block(`results in this work ${PROSE}.`, { size: 8.25, nLines: 30, y1: 70 });
  const entries = column(NATURE, { size: 6.5, x1: 316, x2: 556, y: 60, step: 4 });
  const paras = [entries[0], carried, ...entries.slice(1)];
  const f = pageFlow(paras, pageList(paras));
  assert.equal(f.flow[0], 1, "the body comes first, not the list's first line at the page's top");
  assert.deepEqual(f.flow, [1, 0, ...entries.slice(1).map((_, k) => k + 2)]);
  assert.notEqual(f.col[1], f.col[0]);
  const { units, left } = plan(new Map([[1, open], [2, paras]]));
  assert.deepEqual(units[0].targets, [{ pn: 1, i: 0 }, { pn: 2, i: 1 }]);
  assert.equal(left.length, entries.length);
});

test("a page that is all list reads by the list's size, a running head apart", () => {
  const paras = [block("Smith and Jones", { size: 9, nLines: 1, y1: 30 }), ...column(NATURE, { size: 7, y: 60 })];
  const f = pageFlow(paras, pageList(paras));
  assert.equal(f.body, 7);
  assert.ok(!f.flow.includes(0), "the head is furniture as before");
});

test("an entry's tail the list missed does not set the body size of a page that is all list", () => {
  const paras = [
    block("A. Smith et al.: Quantum metrology", { size: 9, nLines: 1, y1: 30 }),
    block("Modern Physics Vol. 256, edited by W. Quint and M. Vogel", { size: 7, nLines: 2, y1: 44 }),
    ...column([...NATURE, ...NATURE, ...NATURE], { size: 9, y: 70 }),
  ];
  const list = pageList(paras);
  assert.equal(list.alone, true);
  assert.equal(list.blocks.has(1), false, "the tail is not read as an entry");
  assert.equal(pageFlow(paras, list).body, 9);
});

test("the short end of a list changes nothing in the page's flow", () => {
  const paras = [
    ...column([`${PROSE}.`, `${PROSE}.`], { size: 10, nLines: 12, x2: 562 }),
    ...column(NATURE.slice(0, 3), { size: 7, y: 400 }),
  ];
  const list = pageList(paras);
  assert.equal(list.alone, false);
  assert.deepEqual(pageFlow(paras, list).flow, pageFlow(paras).flow);
});
