// node --test tests/notebook.test.mjs (from frontend/) — the notebook module.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  DEFAULT_PAPER, firstSheetId, inkBySheet, newSheet, normalizePaper, paperBefore, paperLines, paperSizeKey,
  sheetIdAfter, sheetOfBlock, sheetsOf, stableId, turnPaper,
} from "../src/notebook/notebook.js";
import { treeOf } from "../src/replica/tree.js";

// tests/shared/paper.json: the same cases gamma/notebook.py passes
// (backend/tests/test_shared_fixtures.py).
const PAPER = JSON.parse(readFileSync(new URL("../../tests/shared/paper.json", import.meta.url), "utf8"));
for (const c of PAPER.normalize) {
  test(`paper: ${c.note}`, () => assert.deepEqual(normalizePaper(c.input, c.fallback), c.output));
}
for (const c of PAPER.lines) {
  test(`paper lines: ${c.note}`, () => assert.deepEqual(paperLines(normalizePaper(c.paper)), { lines: c.lines, dots: c.dots }));
}
for (const c of PAPER.sheets) {
  test(`page sheets: ${c.note}`, () => {
    const tree = treeOf(c.blocks, c.page);
    const ink = inkBySheet(tree);
    assert.deepEqual(sheetsOf(tree).map((s) => ({ id: s.id, paper: s.paper, ink: ink.get(s.id).map((b) => b.id) })), c.sheets);
  });
}

const tree = [
  { id: "intro", content: "about", properties: {}, children: [] },
  { id: "s1", content: "", properties: { sheet: { pattern: "ruled" } }, children: [
    { id: "g1", content: "", properties: { ink_url: "/api/uploads/a.ink" }, children: [
      { id: "note", content: "why", properties: {}, children: [
        { id: "g2", content: "", properties: { ink_url: "/api/uploads/b.ink" }, children: [] }] }] }] },
  { id: "s2", content: "", properties: { sheet: { width: 612 } }, children: [] },
];

test("a page's sheets, in order, take missing paper keys from the default paper", () => {
  const sheets = sheetsOf(tree);
  assert.deepEqual(sheets.map((s) => [s.id, s.index, s.paper.pattern, s.paper.width, s.paper.height]),
    [["s1", 0, "ruled", 595.28, 841.89], ["s2", 1, "blank", 612, 841.89]]);
  assert.deepEqual(sheetsOf([]), []);
});

test("ink belongs to the sheet it is under, at any depth", () => {
  const map = inkBySheet(tree);
  assert.deepEqual([...map.keys()], ["s1", "s2"]);
  assert.deepEqual(map.get("s1").map((b) => b.id), ["g1", "g2"]);
  assert.deepEqual(map.get("s2"), []);
  assert.equal(sheetOfBlock(tree, "g2"), "s1");
  assert.equal(sheetOfBlock(tree, "s2"), "s2");
  assert.equal(sheetOfBlock(tree, "intro"), null);
  assert.equal(sheetOfBlock(tree, "missing"), null);
});

test("a page among a note's blocks: the nearest sheet holds the ink, and a new one copies the paper before it", () => {
  const note = [
    { id: "a", content: "text", properties: {}, children: [] },
    { id: "p1", content: "", properties: { sheet: { pattern: "grid" } }, children: [
      { id: "p2", content: "", properties: { sheet: { pattern: "dots" } }, children: [
        { id: "g", content: "", properties: { ink_url: "/api/uploads/g.ink" }, children: [] }] }] },
    { id: "b", content: "more", properties: {}, children: [] },
  ];
  assert.equal(sheetOfBlock(note, "g"), "p2");
  assert.deepEqual(inkBySheet(note).get("p1"), []);
  assert.equal(paperBefore(note, "a").pattern, "blank");       // no page before: the default paper
  assert.equal(paperBefore(note, "p1").pattern, "grid");       // a page: its own
  assert.equal(paperBefore(note, "b").pattern, "dots");        // the last page before it in document order
  assert.equal(paperBefore(note, "missing").pattern, "dots");
});

test("stable ids: the same parts give the same block id on every device", () => {
  assert.equal(stableId("s", "a", "b"), stableId("s", "a", "b"));
  assert.notEqual(stableId("s", "a", "b"), stableId("s", "ab"));
  assert.match(firstSheetId("page1"), /^s[0-9a-f]{16}$/);
  assert.notEqual(firstSheetId("page1"), firstSheetId("page2"));
  assert.notEqual(sheetIdAfter("x"), firstSheetId("x"));
  // FNV-1a 64 of the empty string, pinned so another runtime can check its port
  assert.equal(stableId(""), "cbf29ce484222325");
});

test("paper sizes are recognised either way round", () => {
  const a4 = normalizePaper({});
  assert.equal(paperSizeKey(a4), "a4");
  assert.equal(paperSizeKey(turnPaper(a4)), "a4");
  assert.equal(paperSizeKey(normalizePaper({ width: 612, height: 792 })), "letter");
  assert.equal(paperSizeKey(normalizePaper({ width: 700, height: 700 })), "");
});

test("a new sheet stores its whole paper and starts folded: it shows its drawings itself", () => {
  assert.deepEqual(newSheet("s9", { pattern: "grid" }),
    { id: "s9", content: "", properties: { sheet: { ...DEFAULT_PAPER, pattern: "grid" }, collapsed: true }, children: [] });
});
