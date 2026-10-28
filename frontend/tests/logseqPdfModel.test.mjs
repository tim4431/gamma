// The pure block-tree operations the outliner's keys run on.
import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_HIGHLIGHT } from "../src/shared/model/palette.js";
import { setWallClock } from "../src/shared/model/wallClock.js";
import {
  EMPTY_VIEW, acceptedPosition, addChildBlock, addHighlightAsBlock, addSiblingBlock, blockPage, blockPosition, blocksToHighlights,
  closestPlace, closestPlacement, placeToReview, reviewUndone,
  closeEditing, extractBlock, findBlockContext, flattenBlocks, indentBlock, insertChild, insertSibling,
  isDescendant, isFolded, isFoldedAway, isHighlightBlock, makeBlockId,
  normalizeBlocks, outdentBlock, placedToCheck, revealBlock, roughMatchPercent, toggleFold, visibleBlocks, visibleNeighbor,
  withEditing,
} from "../src/shared/model/blockModel.js";

const N = (id, children = [], extra = {}) => ({ id, content: id, properties: {}, children, ...extra });
const ids = (list) => list.map((b) => b.id);
// a(a1), b, c(c1(c1x), c2)
const tree = () => [N("a", [N("a1")]), N("b"), N("c", [N("c1", [N("c1x")]), N("c2")])];

test("indent moves a block under its previous sibling; the first sibling stays", () => {
  const t = tree();
  assert.equal(indentBlock(t, "a"), t);
  const out = indentBlock(t, "b");
  assert.deepEqual(ids(out), ["a", "c"]);
  assert.deepEqual(ids(out[0].children), ["a1", "b"]);
  assert.deepEqual(ids(t), ["a", "b", "c"], "the input is not mutated");
});

test("outdent puts a block right after its parent, at the root or inside the grandparent", () => {
  assert.deepEqual(ids(outdentBlock(tree(), "a1")), ["a", "a1", "b", "c"]);
  const out = outdentBlock(tree(), "c1x");
  assert.deepEqual(ids(out[2].children), ["c1", "c1x", "c2"]);
  assert.deepEqual(out[2].children[0].children, []);
  assert.equal(outdentBlock(tree(), "b").length, 3, "a root block has nowhere to go");
});

test("insertSibling / insertChild place a block; an unknown anchor returns the same list", () => {
  const t = tree();
  assert.deepEqual(ids(insertSibling(t, "b", N("n"), false)), ["a", "n", "b", "c"]);
  assert.deepEqual(ids(insertSibling(t, "b", N("n"), true)), ["a", "b", "n", "c"]);
  assert.deepEqual(ids(insertSibling(t, "c2", N("n"), true)[2].children), ["c1", "c2", "n"]);
  assert.equal(insertSibling(t, "nope", N("n"), true), t);
  assert.deepEqual(ids(insertChild(t, "b", N("n"))[1].children), ["n"]);
  assert.deepEqual(ids(insertChild(t, "c", N("n"), true)[2].children), ["c1", "c2", "n"]);
  assert.deepEqual(ids(insertChild(t, "c", N("n"))[2].children), ["n", "c1", "c2"]);
});

test("extractBlock returns the subtree and the tree without it, untouched elsewhere", () => {
  const t = tree();
  const { extracted, remaining } = extractBlock(t, "c1");
  assert.equal(extracted.id, "c1");
  assert.deepEqual(ids(extracted.children), ["c1x"]);
  assert.deepEqual(ids(remaining[2].children), ["c2"]);
  assert.equal(remaining[0], t[0], "untouched branches keep their identity");
  assert.equal(extractBlock(t, "nope"), null);
});

test("isDescendant and findBlockContext", () => {
  const t = tree();
  assert.equal(isDescendant(t, "c", "c1x"), true);
  assert.equal(isDescendant(t, "c", "c"), true);
  assert.equal(isDescendant(t, "a", "c2"), false);
  assert.deepEqual(findBlockContext(t, "c1x"), { block: t[2].children[0].children[0], parentId: "c1", index: 0, depth: 2, ancestors: ["c", "c1"] });
  assert.deepEqual(findBlockContext(t, "b").parentId, null);
  assert.equal(findBlockContext(t, "nope"), null);
});

test("new blocks: sibling below or above, child, with fresh 12-char ids and nothing but the document's fields", () => {
  const t = tree();
  setWallClock(() => 1700000000000);
  let { blocks, newId } = addSiblingBlock(t, "b");
  setWallClock(null);
  assert.deepEqual(ids(blocks), ["a", "b", newId, "c"]);
  assert.match(newId, /^[A-Za-z0-9_-]{12}$/);
  // the moment it was made is the one field of its own (docs/dev/recording.md)
  assert.deepEqual(blocks[2], { id: newId, parentId: null, children: [], content: "", properties: { t0: 1700000000000 } });
  ({ blocks, newId } = addSiblingBlock(t, "b", { above: true }));
  assert.deepEqual(ids(blocks), ["a", newId, "b", "c"]);
  ({ blocks, newId } = addChildBlock(t, "b"));
  assert.deepEqual(ids(blocks[1].children), [newId]);
  assert.notEqual(makeBlockId(), makeBlockId());
});

// The viewer's state beside the tree: {editingId, folds}. Folding a block
// nobody touched here follows its stored properties.collapsed.
const folded = (id) => ({ properties: { collapsed: true } });

test("flattenBlocks lists every block with depth and parent; visibleBlocks skips folded subtrees", () => {
  const t = tree();
  t[2] = { ...t[2], ...folded("c") };
  assert.deepEqual(flattenBlocks(t).map((b) => [b.id, b.depth, b.parentId]),
    [["a", 0, null], ["a1", 1, "a"], ["b", 0, null], ["c", 0, null], ["c1", 1, "c"], ["c1x", 2, "c1"], ["c2", 1, "c"]]);
  assert.deepEqual(visibleBlocks(t, EMPTY_VIEW).map((b) => b.id), ["a", "a1", "b", "c"]);
  // the viewer's own fold wins over the stored one, both ways
  assert.deepEqual(visibleBlocks(t, { ...EMPTY_VIEW, folds: { c: false } }).map((b) => b.id), ["a", "a1", "b", "c", "c1", "c1x", "c2"]);
  assert.deepEqual(visibleBlocks(t, { ...EMPTY_VIEW, folds: { a: true } }).map((b) => b.id), ["a", "b", "c"]);
  assert.equal(visibleNeighbor(t, "c", 1, EMPTY_VIEW), null, "c is folded: its children are not shown");
  assert.equal(visibleNeighbor(t, "c", 1, { ...EMPTY_VIEW, folds: { c: false } })?.id, "c1");
});

test("revealBlock unfolds every ancestor that hides the target, in the view only", () => {
  const t = tree();
  t[2] = { ...t[2], ...folded("c"), children: [{ ...t[2].children[0], ...folded("c1") }, t[2].children[1]] };
  t[0] = { ...t[0], ...folded("a") };
  assert.equal(isFoldedAway(t, "c1x", EMPTY_VIEW), true);
  const view = revealBlock(t, EMPTY_VIEW, "c1x");
  assert.deepEqual(view.folds, { c: false, c1: false });
  assert.equal(isFoldedAway(t, "c1x", view), false);
  assert.equal(isFolded(t[0], view), true, "an ancestor of something else stays folded");
  assert.deepEqual(t[2].properties, { collapsed: true }, "the document is untouched");
  assert.equal(revealBlock(t, view, "c1x"), view, "nothing to open: the same view");
  assert.equal(revealBlock(t, EMPTY_VIEW, "b"), EMPTY_VIEW, "a root block is never hidden");
  assert.equal(isFoldedAway(t, "nope", EMPTY_VIEW), false);
});

test("toggleFold flips the viewer's folding and the stored property together", () => {
  const { blocks, view } = toggleFold(tree(), EMPTY_VIEW, "c");
  assert.equal(blocks[2].properties.collapsed, true);
  assert.deepEqual(view.folds, { c: true });
  assert.equal(isFolded(blocks[2], view), true);
  const again = toggleFold(blocks, view, "c");
  assert.equal(again.blocks[2].properties.collapsed, false);
  assert.deepEqual(again.view.folds, { c: false });
  // a block revealed here (stored folded, shown open) folds again on the next toggle
  const t = tree();
  t[2] = { ...t[2], ...folded("c") };
  const shown = revealBlock(t, EMPTY_VIEW, "c1");
  assert.equal(toggleFold(t, shown, "c").view.folds.c, true);
  assert.deepEqual(toggleFold(t, EMPTY_VIEW, "nope"), { blocks: t, view: EMPTY_VIEW });
});

test("the open editor: one at a time, and a stale blur never closes the new one", () => {
  const v = withEditing(EMPTY_VIEW, "a");
  assert.equal(v.editingId, "a");
  assert.equal(withEditing(v, "a"), v, "the same view when nothing changes");
  assert.equal(withEditing(v, "b").editingId, "b");
  assert.equal(closeEditing(withEditing(v, "b"), "a").editingId, "b");
  assert.equal(closeEditing(v, "a").editingId, null);
});

test("normalizeBlocks gives every node a children array and nothing else", () => {
  const out = normalizeBlocks([{ id: "x", content: "", properties: { collapsed: true }, children: [{ id: "y", content: "" }] }]);
  assert.deepEqual(out, [{ id: "x", content: "", properties: { collapsed: true }, children: [{ id: "y", content: "", children: [] }] }]);
});

const POS = { pageNumber: 2, width: 600, height: 800, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [{ x1: 1, y1: 2, x2: 3, y2: 4 }] };
const GREEN = "#aaebaaa6";   // a highlight's colour as stored, the green wash at its alpha

test("blocksToHighlights lists positioned highlight blocks, by block id, and whether they carry a note", () => {
  const hl = (id, content, children = []) => N(id, children, { content, properties: { quote: "q", color: GREEN, pdf_position: POS } });
  const out = blocksToHighlights([N("plain"), hl("h1", ""), hl("h2", "a comment"), hl("h3", "", [N("k", [], { content: " " }), N("m", [], { content: "note" })]),
    N("pageonly", [], { properties: { quote: "q", pdf_position: { pageNumber: 4 } } }),
    N("ink", [], { properties: { ink_url: "/api/uploads/a.ink", pdf_position: POS } })]);
  assert.deepEqual(out.map((h) => [h.id, h.hasNote]), [["h1", false], ["h2", true], ["h3", true]]);
  assert.deepEqual(out[0], { id: "h1", content: { text: "q" }, comment: { text: "" }, hasNote: false, color: GREEN, position: POS });
});

test("a highlight's colour comes out stored; an older rgba string is respelled, an unreadable one is the default", () => {
  const colored = (id, color) => N(id, [], { content: "", properties: { quote: "q", color, pdf_position: POS } });
  const out = blocksToHighlights([colored("a", "rgba(170, 235, 170, 0.65)"), colored("b", "red"), colored("c", undefined)]);
  assert.deepEqual(out.map((h) => h.color), [GREEN, DEFAULT_HIGHLIGHT, DEFAULT_HIGHLIGHT]);
});

test("a highlight is a block with a position that is no ink group; its page is the position's", () => {
  const [hl, pageOnly, link, ink, box, note] = [
    { pdf_position: POS }, { pdf_position: { pageNumber: 4 } }, { pdf_position: POS, link_url: "https://x.test" },
    { ink_url: "", pdf_position: POS }, { text_box: { x: 1 }, pdf_page: 5 }, {},
  ].map((properties, i) => N(`b${i}`, [], { properties }));
  assert.deepEqual([hl, pageOnly, link, ink, box, note].map(isHighlightBlock), [true, true, true, false, false, false]);
  assert.deepEqual([hl, pageOnly, link, ink, box, note].map(blockPage), [2, 4, 2, 2, 5, null]);
  // the place on the page: a position with its rectangles, not its page alone
  assert.deepEqual([hl, pageOnly].map(blockPosition), [POS, null]);
});

test("after the PDF was replaced a place to check is drawn dashed, with its match; Accept drops it with where it was", () => {
  const prev = { doc_id: "ab12", pageNumber: 1, rects: [[0.1, 0.2, 0.5, 0.22]], quote: "the old text" };
  const rough = { ...POS, approx: true, match: 0.874, prev };
  const estimated = { ...POS, area: true, approx: true, prev: { ...prev, quote: "" } };
  const lost = { ...POS, lost: true, approx: true, match: 0.9 };
  assert.deepEqual([POS, rough, estimated, lost, null].map(placedToCheck), [false, true, true, false, false]);
  // a whole percentage for a passage matched roughly; none for an exact place, an estimate or a lost one
  assert.deepEqual([POS, rough, estimated, lost, { ...POS, approx: true, match: "high" }].map(roughMatchPercent),
    [null, 87, null, null, null]);
  assert.deepEqual(acceptedPosition(rough), POS);
  assert.deepEqual(acceptedPosition(estimated), { ...POS, area: true });
  assert.deepEqual(acceptedPosition({ ...POS, prev }), POS, "a stale prev goes too");
  // an area nothing placed keeps the file it was measured on until accepted: accepted, its rectangles are
  // the current PDF's, so a later run does not read it from the old file and dash it again
  assert.deepEqual(acceptedPosition({ ...estimated, doc_id: "ab12" }), { ...POS, area: true });
  assert.equal(acceptedPosition(POS), POS, "a solid place is returned as it is");
  const stamped = { ...POS, doc_id: "ab12" };
  assert.equal(acceptedPosition(stamped), stamped, "a solid place measured on another file too: nothing to accept");
  assert.deepEqual(rough, { ...POS, approx: true, match: 0.874, prev }, "the input is not mutated");
});

// A lost highlight's closest match as the run stores it (gamma/highlight_relocate.py): the candidate's place in
// the page's current file, its confidence and its text there.
const CLOSEST = { pageNumber: 3, width: 612, height: 792, boundingRect: { x1: 70, y1: 100, x2: 300, y2: 124 },
  rects: [{ x1: 70, y1: 100, x2: 300, y2: 112 }, { x1: 70, y1: 112, x2: 180, y2: 124 }], match: 0.63, quote: "the new text" };

test("to review after a replacement: the dashed and the hollow; a lost one's closest match is read defensively", () => {
  const rough = { ...POS, approx: true, match: 0.9 };
  const lost = { ...POS, lost: true, doc_id: "ab12" };
  assert.deepEqual([POS, rough, lost, { ...lost, approx: true }, { ...POS, approx: true, area: true }, null].map(placeToReview),
    [false, true, true, true, true, false]);
  // well formed: the place in the stored shape, the match and the quote
  assert.deepEqual(closestPlace({ ...lost, closest: CLOSEST }), {
    position: { pageNumber: 3, width: 612, height: 792, boundingRect: CLOSEST.boundingRect, rects: CLOSEST.rects },
    match: 0.63, quote: "the new text",
  });
  // a missing bounding box is the rectangles' union; a stray rectangle is left out, a long quote clipped
  const loose = closestPlace({ closest: { ...CLOSEST, boundingRect: null, rects: [...CLOSEST.rects, { x1: "a" }], quote: "x".repeat(1200) } });
  assert.deepEqual(loose.position.boundingRect, { x1: 70, y1: 100, x2: 300, y2: 124 });
  assert.equal(loose.position.rects.length, 2);
  assert.equal(loose.quote.length, 1000);
  // malformed: no page, a page that is no page, no size, no rectangle, no match or one out of bounds
  for (const bad of [null, "x", [], { ...CLOSEST, pageNumber: 0 }, { ...CLOSEST, pageNumber: 1.5 }, { ...CLOSEST, width: 0 },
    { ...CLOSEST, height: "792" }, { ...CLOSEST, rects: [] }, { ...CLOSEST, rects: [{ x1: 1 }] }, { ...CLOSEST, match: 1.2 },
    { ...CLOSEST, match: "0.6" }]) {
    assert.equal(closestPlace({ ...lost, closest: bad }), null, JSON.stringify(bad));
  }
  assert.equal(closestPlace(lost), null);
});

test("Use closest match: the candidate's place and text, lost, closest, doc_id and the review's flags gone", () => {
  const lost = { ...POS, lost: true, doc_id: "ab12", approx: true, match: 0.9,
    prev: { doc_id: "cd34", pageNumber: 1, rects: [[0.1, 0.1, 0.2, 0.2]], quote: "old" }, closest: CLOSEST };
  const placed = closestPlacement(lost);
  assert.deepEqual(placed, { position: { pageNumber: 3, width: 612, height: 792, boundingRect: CLOSEST.boundingRect, rects: CLOSEST.rects },
    quote: "the new text" });
  for (const key of ["lost", "closest", "doc_id", "approx", "match", "prev", "area"]) assert.ok(!(key in placed.position), key);
  assert.equal(placedToCheck(placed.position), false, "drawn solid");
  assert.equal(closestPlacement({ ...POS, lost: true }), null, "nothing to apply without a closest match");
  assert.equal(lost.closest, CLOSEST, "the input is not mutated");
});

test("the review card's Undo takes its step back, and nothing over a later change", () => {
  const pending = { ...POS, approx: true, match: 0.87, prev: { doc_id: "ab12", pageNumber: 1, rects: [], quote: "old" } };
  // Accept: the position as it was, the quote untouched
  const accepted = { quote: "q", color: GREEN, pdf_position: acceptedPosition(pending) };
  const acceptStep = { kind: "accepted", before: { position: pending }, after: acceptedPosition(pending) };
  assert.deepEqual(reviewUndone(accepted, acceptStep), { quote: "q", color: GREEN, pdf_position: pending });
  // the same place with its keys in another order (a round trip) is the same place
  const reordered = { ...accepted, pdf_position: { rects: POS.rects, boundingRect: POS.boundingRect, height: 800, width: 600, pageNumber: 2 } };
  assert.deepEqual(reviewUndone(reordered, acceptStep).pdf_position, pending);
  // changed since (moved by hand, or the app's undo already took it back): nothing
  const moved = { ...accepted, pdf_position: { ...POS, pageNumber: 5 } };
  assert.equal(reviewUndone(moved, acceptStep), moved);
  assert.equal(reviewUndone({ ...accepted, pdf_position: pending }, acceptStep).pdf_position, pending);
  assert.equal(reviewUndone(accepted, null), accepted);
  // Use closest match, or a move: the old position and the old quote back
  const lost = { ...POS, lost: true, doc_id: "ab12", closest: CLOSEST };
  const placed = closestPlacement(lost);
  const placeStep = { kind: "placed", before: { position: lost, quote: "the old text" }, after: placed.position };
  assert.deepEqual(reviewUndone({ quote: placed.quote, pdf_position: placed.position }, placeStep), { quote: "the old text", pdf_position: lost });
  // an area had no quote: the one the move took is dropped again
  const area = { ...POS, area: true, lost: true };
  const moveStep = { kind: "moved", before: { position: area, quote: "" }, after: POS };
  assert.deepEqual(reviewUndone({ quote: "selected text", pdf_position: POS }, moveStep), { pdf_position: area });
});

test("a highlight made in the viewer is a block whose id is the highlight's, its position as given", () => {
  setWallClock(() => 1700000000000);
  const [block] = addHighlightAsBlock([], { id: "hx", position: POS, content: { text: "q" }, comment: { text: "c" }, color: GREEN });
  setWallClock(null);
  assert.deepEqual(block, { id: "hx", parentId: null, children: [], content: "c",
    properties: { t0: 1700000000000, color: GREEN, quote: "q", pdf_position: POS } });
});
