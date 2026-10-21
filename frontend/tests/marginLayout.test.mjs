// The PDF's comment margin: where the page stands beside its column, and
// how the cards stack (src/comments/marginLayout.js, docs/dev/comments.md
// "The PDF margin").
import test from "node:test";
import assert from "node:assert/strict";
import { CARD_GAP, CARD_W, COLUMN_GAP, EDGE, marginLeft, pageLeft, stackCards } from "../src/comments/marginLayout.js";

const tops = (items, opts) => Object.fromEntries(stackCards(items, opts));
const overlaps = (items, placed) => {
  const boxes = items.map((it) => [placed.get(it.key), placed.get(it.key) + it.height]).sort((a, b) => a[0] - b[0]);
  return boxes.some((b, i) => i > 0 && b[0] < boxes[i - 1][1]);
};

test("marginLeft centres the page and its column together, and leaves no margin without room", () => {
  const need = 600 + COLUMN_GAP + CARD_W;
  assert.equal(marginLeft(need + 2 * 100, 600), 100, "the page moves left by half the column");
  assert.equal(marginLeft(need + 2 * EDGE, 600), EDGE, "just room: the edge space at both sides");
  assert.equal(marginLeft(need + 2 * EDGE - 2, 600), null, "less: no margin");
  assert.equal(marginLeft(800, 800), null, "fit-width");
  assert.equal(marginLeft(0, 600), null, "a viewer not laid out yet");
  assert.equal(marginLeft(1200, 0), null, "a page of no size yet");
});

test("pageLeft is the centred page without a margin, and the page beside its column with one", () => {
  assert.equal(pageLeft(1000, 600, false), 200);
  assert.equal(pageLeft(1500, 600, true), marginLeft(1500, 600));
  assert.equal(pageLeft(900, 600, true), 150, "no room for the column: centred as before");
  assert.equal(pageLeft(800, 1200, true), 0, "a page wider than the viewer starts at its left");
});

test("stackCards puts each card level with its highlight while they do not meet", () => {
  assert.deepEqual(tops([{ key: "a", top: 10, height: 50 }, { key: "b", top: 200, height: 50 }]), { a: 10, b: 200 });
});

test("stackCards sorts by the highlight's top and puts a card that would overlap just under the one above", () => {
  const items = [{ key: "b", top: 40, height: 60 }, { key: "a", top: 20, height: 80 }, { key: "c", top: 60, height: 30 }];
  const placed = stackCards(items);
  assert.deepEqual(Object.fromEntries(placed), { a: 20, b: 20 + 80 + CARD_GAP, c: 20 + 80 + CARD_GAP + 60 + CARD_GAP });
  assert.equal(overlaps(items, placed), false);
});

test("stackCards keeps the order given for cards on the same line (a highlight's threads)", () => {
  const items = [{ key: "t1", top: 100, height: 40 }, { key: "t2", top: 100, height: 40 }];
  assert.deepEqual(tops(items), { t1: 100, t2: 100 + 40 + CARD_GAP });
});

test("stackCards keeps the active card level with its highlight: the ones under it go down, the ones over it up", () => {
  const items = [
    { key: "a", top: 100, height: 60 },
    { key: "b", top: 120, height: 60 },
    { key: "c", top: 140, height: 200 }, // the active one, grown with its thread
    { key: "d", top: 150, height: 40 },
  ];
  const placed = stackCards(items, { active: "c" });
  assert.equal(placed.get("c"), 140, "level with its highlight");
  assert.equal(placed.get("d"), 140 + 200 + CARD_GAP, "pushed down under it");
  assert.equal(placed.get("b"), 140 - CARD_GAP - 60, "moved up over it");
  assert.equal(placed.get("a"), placed.get("b") - CARD_GAP - 60, "and the one over that, further up");
  assert.equal(overlaps(items, placed), false);
});

test("stackCards moves no card above `min`: the cards stack down from it and the active one comes as near as they leave room", () => {
  const items = [{ key: "a", top: 10, height: 60 }, { key: "b", top: 20, height: 40 }];
  const placed = stackCards(items, { active: "b", min: 0 });
  assert.equal(placed.get("a"), 10, "the card over the active one stays at its highlight");
  assert.equal(placed.get("b"), 10 + 60 + CARD_GAP, "the active one goes under it");
  assert.equal(overlaps(items, placed), false);
  assert.deepEqual(tops(items, { active: "b", min: -100 }), { a: 20 - CARD_GAP - 60, b: 20 }, "with room above, as before");
  assert.deepEqual(tops([{ key: "a", top: -5, height: 10 }], { min: 0 }), { a: 0 }, "a card on its own comes down to it too");
});

test("stackCards under the cards of the page above (`min` past the highlights' tops) stacks down from `min`, active or not", () => {
  // the page above's last card ends 30 px into this page
  const items = [{ key: "a", top: 10, height: 50 }, { key: "b", top: 40, height: 50 }, { key: "c", top: 400, height: 50 }];
  assert.deepEqual(tops(items, { min: 30 }), { a: 30, b: 30 + 50 + CARD_GAP, c: 400 }, "none active");
  const placed = stackCards(items, { active: "b", min: 30 });
  assert.deepEqual(Object.fromEntries(placed), { a: 30, b: 30 + 50 + CARD_GAP, c: 400 }, "the active one as near as they leave room");
  assert.ok([...placed.values()].every((top) => top >= 30), "none over the cards above");
  assert.equal(overlaps(items, placed), false);
});

test("stackCards moves no card up that is clear of the active one", () => {
  const items = [{ key: "a", top: 0, height: 40 }, { key: "b", top: 300, height: 40 }];
  assert.deepEqual(tops(items, { active: "b" }), { a: 0, b: 300 });
});

test("stackCards with an active key it does not hold stacks from the top", () => {
  const items = [{ key: "a", top: 0, height: 40 }, { key: "b", top: 10, height: 40 }];
  assert.deepEqual(tops(items, { active: "gone" }), { a: 0, b: 40 + CARD_GAP });
  assert.deepEqual(tops([]), {});
});
