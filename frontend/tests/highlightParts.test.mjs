import { test } from "node:test";
import assert from "node:assert/strict";
import { canJoin, joinHighlights, takesParts, withPart } from "../src/shared/model/highlightParts.js";
import { findBlock } from "../src/shared/model/blockModel.js";

const r = (x1, y1, x2, y2) => ({ x1, y1, x2, y2 });
// A passage on page `page`, measured on a page `w` wide (its height in proportion).
const at = (rects, { page = 3, w = 600 } = {}) => ({
  pageNumber: page, width: w, height: w * 1.3, rects,
  boundingRect: { x1: Math.min(...rects.map((q) => q.x1)), y1: Math.min(...rects.map((q) => q.y1)),
    x2: Math.max(...rects.map((q) => q.x2)), y2: Math.max(...rects.map((q) => q.y2)) },
});
const hl = (id, quote, pos, extra = {}) => ({ id, content: "", properties: { color: "#ffd40080", quote, pdf_position: pos }, children: [], ...extra });

test("a passage after the highlight's own: its rects last, taken into the highlight's frame, its text after", () => {
  const foot = at([r(50, 700, 280, 712)]);
  const top = at([r(320, 100, 560, 112), r(320, 112, 450, 124)], { w: 1200 }); // measured at twice the zoom
  const { position, quote } = withPart(foot, "All light pulses are performed by two", top, "circularly-polarized beams (schematic).");
  assert.deepEqual(position.rects, [r(50, 700, 280, 712), r(160, 50, 280, 56), r(160, 56, 225, 62)]);
  assert.deepEqual(position.boundingRect, r(50, 50, 280, 712));
  assert.equal(position.width, 600);
  assert.equal(position.pageNumber, 3);
  assert.equal(quote, "All light pulses are performed by two circularly-polarized beams (schematic).");
});

test("a passage that reads before the highlight goes ahead of it, rects and text", () => {
  const top = at([r(320, 100, 560, 112)]);
  const foot = at([r(50, 700, 280, 712)]);
  const { position, quote } = withPart(top, " beams. ", foot, "two", { before: true });
  assert.deepEqual(position.rects, [r(50, 700, 280, 712), r(320, 100, 560, 112)]);
  assert.equal(quote, "two beams.");
});

test("only passages of one page measured on its PDF as it is take parts", () => {
  const pos = at([r(1, 1, 20, 10)]);
  assert.equal(takesParts(pos), true);
  for (const flag of [{ area: true }, { lost: true }, { approx: true }, { doc_id: "old" }]) {
    assert.equal(takesParts({ ...pos, ...flag }), false, JSON.stringify(flag));
    assert.equal(withPart(pos, "a", { ...pos, ...flag }, "b"), null);
  }
  assert.equal(takesParts({ pageNumber: 3 }), false, "a highlight placed by its page alone");
  assert.equal(withPart(pos, "a", at([r(1, 1, 5, 5)], { page: 4 }), "b"), null, "another page");
});

test("a joined highlight with a note stays under the one it joined, as a plain note with its id and children", () => {
  const child = { id: "c", content: "under it", properties: {}, children: [] };
  const blocks = [
    hl("a", "first part", at([r(50, 700, 280, 712)]), { content: "the note" }),
    { id: "n", content: "a note between", properties: {}, children: [] },
    hl("b", "second part", at([r(320, 100, 560, 112)]), { content: "its own note", children: [child] }),
  ];
  blocks[2].properties.style = "underline";
  const out = joinHighlights(blocks, "a", "b");
  assert.deepEqual(out.map((x) => x.id), ["a", "n"]);
  const a = out[0];
  assert.equal(a.content, "the note");
  assert.equal(a.properties.quote, "first part second part");
  assert.equal(a.properties.pdf_position.rects.length, 2);
  assert.equal(a.properties.color, "#ffd40080");
  const note = a.children.at(-1);
  assert.equal(note.id, "b");
  assert.equal(note.content, "its own note");
  assert.deepEqual(note.properties, {});
  assert.deepEqual(note.children, [child]);
});

test("a joined highlight with nothing of its own gives its children to the one it joined, or goes", () => {
  const child = { id: "c", content: "under it", properties: {}, children: [] };
  const blocks = [hl("a", "one", at([r(50, 700, 280, 712)])), hl("b", "two", at([r(320, 100, 560, 112)]), { children: [child] })];
  const out = joinHighlights(blocks, "a", "b", { before: true });
  assert.deepEqual(out.map((x) => x.id), ["a"]);
  assert.equal(out[0].properties.quote, "two one");
  assert.deepEqual(out[0].children.map((x) => x.id), ["c"]);
  const bare = joinHighlights([hl("a", "one", at([r(1, 1, 9, 9)])), hl("b", "two", at([r(1, 20, 9, 29)]))], "a", "b");
  assert.equal(findBlock(bare, "b"), null);
  // a comment thread on it (the server's, which the tree does not show) keeps it, as a note
  const kept = joinHighlights([hl("a", "one", at([r(1, 1, 9, 9)])), hl("b", "two", at([r(1, 20, 9, 29)]))], "a", "b", { keep: true });
  assert.equal(findBlock(kept, "b")?.properties.pdf_position, undefined);
  assert.equal(kept[0].children[0].id, "b");
});

test("a highlight nested under the one it joins joins it too, its place in the tree taken by its children", () => {
  const inner = hl("b", "inner", at([r(1, 20, 9, 29)]));
  const blocks = [hl("a", "outer", at([r(1, 1, 9, 9)]), { children: [inner] })];
  const out = joinHighlights(blocks, "a", "b");
  assert.equal(out[0].properties.quote, "outer inner");
  assert.deepEqual(out[0].children, []);
});

test("what cannot join: another page, an area, a link region, itself, one the other lies under", () => {
  const base = () => [hl("a", "one", at([r(1, 1, 9, 9)])), hl("b", "two", at([r(1, 20, 9, 29)]))];
  assert.equal(canJoin(base(), "a", "b"), true);
  assert.equal(canJoin(base(), "a", "a"), false);
  assert.equal(canJoin(base(), "a", "missing"), false);
  const other = base(); other[1].properties.pdf_position = at([r(1, 20, 9, 29)], { page: 4 });
  assert.equal(canJoin(other, "a", "b"), false);
  const area = base(); area[1].properties.pdf_position.area = true;
  assert.equal(canJoin(area, "a", "b"), false);
  const link = base(); link[1].properties.link_url = "https://example.org";
  assert.equal(canJoin(link, "a", "b"), false);
  assert.equal(joinHighlights(link, "a", "b"), null);
  const under = [hl("b", "outer", at([r(1, 1, 9, 9)]), { children: [hl("a", "inner", at([r(1, 20, 9, 29)]))] })];
  assert.equal(canJoin(under, "a", "b"), false, "the one it would join lies under it");
  const note = [hl("a", "one", at([r(1, 1, 9, 9)])), { id: "b", content: "a note", properties: {}, children: [] }];
  assert.equal(canJoin(note, "a", "b"), false);
});
