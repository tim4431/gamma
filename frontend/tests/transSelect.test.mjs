import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { segmentPage } from "../src/pdf/pdfTranslate.js";
import { readingOrder } from "../src/pdf/pageFlow.js";
import { drawnOver } from "../src/pdf/transBlock.js";
import { blockAt, blocksQuote, highlightOn, linksOff, marksOver, menuBlockAt, onBlocks, pageReading, paragraphMenu, sameHighlight, selectionBlocks, unitsOnPage } from "../src/pdf/transSelect.js";

const PAPER = JSON.parse(fs.readFileSync(new URL("./fixtures/translatePaper.json", import.meta.url)));
const PAGES = new Map(PAPER.pages.map((p) => [p.pn, segmentPage(p.runs)]));
const parasOf = (pn) => PAGES.get(pn) || null;
const at = (pn, s) => PAGES.get(pn).findIndex((b) => b.text.includes(s));

// Page 1 translated: every translatable block has text, but `skip`.
function entryOf(pn, { skip = [], partial = null } = {}) {
  const paras = PAGES.get(pn);
  return { paras, texts: paras.map((p, i) => (p.translate && !skip.includes(i) ? `T${i}` : undefined)), partial };
}
const mid = (r) => [(r.x1 + r.x2) / 2, (r.y1 + r.y2) / 2];

test("a point finds the block whose line holds it, else whose box does", () => {
  const paras = PAGES.get(1);
  const i = at(1, "Hamiltonian reads");
  assert.equal(blockAt(paras, ...mid(paras[i].lines[0])), i);
  assert.equal(blockAt(paras, -50, -50), -1);
  const block = { x1: 10, x2: 100, y1: 10, y2: 40, lines: [{ x1: 10, x2: 100, y1: 10, y2: 20 }, { x1: 10, x2: 40, y1: 30, y2: 40 }] };
  assert.equal(blockAt([block], 80, 35), 0, "the end of a short last line is the block's");
});

test("a selection inside one translated block stands for it, with its original lines and text", () => {
  const entry = entryOf(1);
  const i = at(1, "Ensembles of neutral atoms");
  const blocks = selectionBlocks(entry, i, i, unitsOnPage(parasOf, 1));
  assert.deepEqual(blocks, [i]);
  const { rects, text } = blocksQuote(entry.paras, blocks);
  assert.equal(rects.length, entry.paras[i].lines.length, "a rect per original line");
  assert.deepEqual(rects[0], { x1: entry.paras[i].lines[0].x1, y1: entry.paras[i].lines[0].y1,
    x2: entry.paras[i].lines[0].x2, y2: entry.paras[i].lines[0].y2 });
  assert.equal(text, entry.paras[i].text.trim(), "the quote is the original's");
});

test("a selection in either part of a paragraph the column break cut stands for both parts", () => {
  const entry = entryOf(1);
  const units = unitsOnPage(parasOf, 1);
  const tail = at(1, "Hamiltonian reads");
  const head = [...units.get(tail)].find((j) => j !== tail);
  assert.ok(head >= 0 && head !== tail, "the unit has two blocks on the page");
  assert.ok(entry.paras[head].text.includes("rotating frame"), entry.paras[head].text);
  assert.deepEqual(selectionBlocks(entry, tail, tail, units), [head, tail], "in reading order");
  assert.deepEqual(selectionBlocks(entry, head, head, units), [head, tail]);
  const { text } = blocksQuote(entry.paras, [head, tail]);
  assert.ok(text.indexOf("rotating frame") < text.indexOf("Hamiltonian reads"), text);
});

test("a part with no translation of its own takes no part of its unit along", () => {
  const units = unitsOnPage(parasOf, 1);
  const tail = at(1, "Hamiltonian reads");
  const head = [...units.get(tail)].find((j) => j !== tail);
  const entry = entryOf(1, { skip: [tail] });
  assert.deepEqual(selectionBlocks(entry, head, head, units), [head]);
});

test("a drag across blocks covers those between in reading order, and an end in the original is its block", () => {
  const entry = entryOf(1);
  const { order } = pageReading(entry.paras);
  assert.deepEqual(order, readingOrder(entry.paras), "a page with no reference list reads as the plain order");
  const a = at(1, "Ensembles of neutral atoms");
  const equation = PAGES.get(1).findIndex((b) => !b.translate && b.text.includes("Vij ninj"));
  assert.ok(equation >= 0 && !drawnOver(entry, equation), "the equation is left as it is");
  // From translated text to the equation, which is the original's, and the other way.
  const forth = selectionBlocks(entry, a, equation);
  const back = selectionBlocks(entry, equation, a);
  assert.deepEqual(forth, back);
  assert.deepEqual(forth, order.slice(order.indexOf(a), order.indexOf(equation) + 1));
  assert.ok(forth.includes(equation));
  // One end off every block: the other alone.
  assert.deepEqual(selectionBlocks(entry, -1, a), [a]);
  assert.deepEqual(selectionBlocks(entry, -1, -1), []);
});

test("a figure's labels between the ends stay out, an end on one is taken alone with the other", () => {
  const block = (y, text, extra = {}) => ({ x1: 54, x2: 300, y1: y, y2: y + 20, size: 10, nLines: 2, text, src: text, spans: [],
    lines: [{ x1: 54, x2: 300, y1: y, y2: y + 9 }, { x1: 54, x2: 300, y1: y + 11, y2: y + 20 }], translate: true, ...extra });
  const prose = "the atoms of the ensemble share one excitation and lose their coherence when the blockade";
  const paras = [
    block(100, `${prose} forms`),
    block(140, "Rabi frequency (MHz)", { translate: false, figure: true, nLines: 1 }),
    block(180, `${prose} radius grows`),
    block(220, `${prose} breaks down`),
  ];
  const entry = { paras, texts: ["A", null, "C", "D"] };
  assert.deepEqual(selectionBlocks(entry, 0, 3), [0, 2, 3]);
  // The label reads last, as furniture: from it, the other end alone.
  assert.deepEqual(selectionBlocks(entry, 1, 3), [3, 1]);
});

test("a streaming block counts as translated", () => {
  const paras = PAGES.get(1);
  const i = at(1, "Ensembles of neutral atoms");
  const entry = { paras, texts: [], partial: { [i]: "Ens" } };
  assert.equal(drawnOver(entry, i), true);
  assert.equal(drawnOver({ paras, texts: [] }, i), false);
  assert.equal(drawnOver({ paras, texts: paras.map(() => "") }, i), true, "a block whose words went before it is masked");
  assert.equal(drawnOver({ paras, texts: paras.map(() => null) }, i), false, "a block that came back as itself shows the original");
});

test("link boxes over translated text go, find marks there mark their block", () => {
  const paras = PAGES.get(1);
  const i = at(1, "Ensembles of neutral atoms");
  const j = at(1, "Hamiltonian reads");
  const [x, y] = mid(paras[i].lines[1]);
  const [u, v] = mid(paras[j].lines[0]);
  const links = [{ left: x - 5, top: y - 4, w: 10, h: 8 }, { left: u - 5, top: v - 4, w: 10, h: 8 }];
  assert.deepEqual(linksOff(links, paras, [i]), [links[1]]);
  assert.equal(linksOff(links, paras, []), links);
  const marks = [
    { rect: { x1: x - 5, y1: y - 4, x2: x + 5, y2: y + 4 }, active: false },
    { rect: { x1: x - 25, y1: y - 4, x2: x - 15, y2: y + 4 }, active: true },
    { rect: { x1: u - 5, y1: v - 4, x2: u + 5, y2: v + 4 }, active: false },
  ];
  const out = marksOver(marks, paras, [i]);
  assert.equal(out.length, 2, "one mark for the two hits in the translated block");
  assert.deepEqual(out[0], { rect: { x1: paras[i].x1, y1: paras[i].y1, x2: paras[i].x2, y2: paras[i].y2 }, active: true, block: true });
  assert.equal(out[1], marks[2], "a hit in the original stays where it is");
  assert.equal(marks[0].active, false, "the marks given are not changed");
});

test("a paragraph's menu is for a block with text the reader can have translated, a figure's too", () => {
  const entry = entryOf(1);
  const i = at(1, "Hamiltonian reads");
  assert.equal(menuBlockAt(entry, ...mid(entry.paras[i].lines[0])), i);
  const equation = entry.paras.findIndex((p) => !p.translate);
  assert.equal(menuBlockAt(entry, ...mid(entry.paras[equation].lines[0])), -1, "a display equation has none");
  const figure = { ...entry, paras: entry.paras.map((p, k) => (k === equation ? { ...p, figure: true } : p)) };
  assert.equal(menuBlockAt(figure, ...mid(entry.paras[equation].lines[0])), equation);
  assert.equal(menuBlockAt(entry, -50, -50), -1);
  assert.equal(drawnOver({ ...entry, original: new Set([i]) }, i), false, "a block shown in the original has nothing drawn");
});

test("a paragraph's menu says of the whole paragraph what it holds, its translation as drawn and its original", () => {
  const a = at(1, "In the rotating frame the"), b = at(1, "Hamiltonian reads");
  const unit = [{ pn: 1, i: a }, { pn: 1, i: b }];
  const paras = PAGES.get(1);
  const span = { kind: "math", str: "Ω" };
  const texts = paras.map(() => undefined);
  texts[a] = "在旋转框架中 {v:9.9.1}";
  texts[b] = "哈密顿量为";
  const entry = { paras, texts, failed: new Set([b]), original: new Set([a]) };
  const menu = paragraphMenu(unit, (pn) => (pn === 1 ? entry : null), (pn, i, k) => (pn === 9 && i === 9 && k === 1 ? span : null));
  assert.deepEqual([menu.translated, menu.original], [true, true]);
  assert.equal(menu.translation, "在旋转框架中 Ω 哈密顿量为", "a formula as its text, the parts read on");
  assert.equal(menu.source, `${paras[a].text.trim()}\n${paras[b].text.trim()}`);
  const none = paragraphMenu(unit, (pn) => (pn === 1 ? { paras, texts: paras.map(() => null) } : null));
  assert.deepEqual([none.translated, none.original, none.translation], [false, false, ""]);
  assert.deepEqual(paragraphMenu(unit, () => null), { translated: false, original: false, translation: "", source: "" });
  const han = paragraphMenu(unit, () => ({ paras, texts: texts.map((t, k) => (k === a ? "在旋转框架中" : t)) }));
  assert.equal(han.translation, "在旋转框架中哈密顿量为", "no space between Han");
});

test("a highlight over a translation takes no pointer, its rows go to the paragraph's menu, and selecting it again finds it", () => {
  const paras = PAGES.get(1);
  const i = at(1, "Ensembles of neutral atoms"), j = at(1, "Hamiltonian reads");
  const size = { width: 612, height: 792 };
  // Stored at a page drawn 1.5 times its size, as a selection there keeps it.
  const stored = (id, blocks, extra = {}) => ({
    id, position: { width: 918, height: 1188, rects: blocksQuote(paras, blocks).rects.map((r) => ({ x1: r.x1 * 1.5, y1: r.y1 * 1.5, x2: r.x2 * 1.5, y2: r.y2 * 1.5 })), ...extra },
  });
  const over = onBlocks(paras, [i]);
  assert.equal(over(...mid(paras[i].lines[1])), true);
  assert.equal(over(...mid(paras[j].lines[0])), false, "a block with no translation drawn");
  assert.equal(onBlocks(paras, [])(...mid(paras[i].lines[1])), false);
  const whole = stored("whole", [i]), other = stored("other", [j]);
  // A right-click on the paragraph: the highlight under it, else the one on it.
  assert.equal(highlightOn([other, whole], paras, i, ...mid(paras[i].lines[1]), size), "whole");
  assert.equal(highlightOn([other, whole], paras, i, null, null, size), "whole", "the popup's ⋯ has no point");
  assert.equal(highlightOn([other], paras, i, null, null, size), null);
  // The same blocks selected again: the highlight made from them.
  const rects = blocksQuote(paras, [i]).rects;
  assert.equal(sameHighlight([other, whole], rects, size), "whole");
  assert.equal(sameHighlight([other], rects, size), null);
  assert.equal(sameHighlight([stored("more", [i, j])], rects, size), null, "more blocks are another highlight");
  assert.equal(sameHighlight([stored("area", [i], { area: true })], rects, size), null, "an area is no selection's");
});

test("a paragraph's original is every part of its unit, one on a page with no entry under the key included", () => {
  // Page 1's last paragraph runs on at page 2's top; page 2 holds another
  // language's translation, or none.
  const unit = [{ pn: 1, i: 27 }, { pn: 2, i: 1 }];
  const head = PAGES.get(1)[27].text.trim(), tail = PAGES.get(2)[1].text.trim();
  const entries = (pn) => (pn === 1 ? entryOf(1) : null);
  const menu = paragraphMenu(unit, entries, null, parasOf);
  assert.equal(menu.source, `${head}\n${tail}`);
  assert.equal(menu.translation, "T27", "the translation is what the key's entries hold");
  assert.equal(paragraphMenu(unit, entries).source, head, "without the segmented pages, the entries' blocks alone");
});
