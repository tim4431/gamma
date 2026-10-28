// The review card's arithmetic (pdf/highlightReview.js): which highlights
// are pending and in what order, the before picture's box and the old
// rectangles in it, and the old quote against the current one.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  PAGE_ASPECT, PREV_QUOTE_MAX, beforeCrop, cropAspect, cropBox, pendingInOrder, quoteDiff, rectsInCrop, reviewPlace, reviewPrev,
} from "../src/pdf/highlightReview.js";

const at = (id, pageNumber, y, x = 72, extra = {}) => ({
  id, position: { pageNumber, width: 612, height: 792, boundingRect: { x1: x, y1: y, x2: x + 100, y2: y + 12 },
    rects: [{ x1: x, y1: y, x2: x + 100, y2: y + 12 }], ...extra },
});

test("the pending highlights are the dashed ones, in document order", () => {
  const list = [
    at("p2", 2, 100, 72, { approx: true, match: 0.9 }),
    at("solid", 1, 50),
    at("lowRight", 1, 400, 320, { approx: true }),
    at("lost", 1, 10, 72, { approx: true, lost: true }),
    at("lowLeft", 1, 400, 72, { approx: true, match: 0.85 }),
    at("top", 1, 90, 72, { approx: true, match: 0.95 }),
  ];
  assert.deepEqual(pendingInOrder(list).map((h) => h.id), ["top", "lowLeft", "lowRight", "p2"]);
  assert.deepEqual(pendingInOrder(null), []);
  // a page measured at another size reads in the same order (fractions, not points)
  const big = at("big", 1, 180, 72, { approx: true, width: 1224, height: 1584 });
  assert.deepEqual(pendingInOrder([at("small", 1, 100, 72, { approx: true }), big]).map((h) => h.id), ["big", "small"]);
});

test("Next walks the pending ones and comes back to the first; one alone has no next", () => {
  const pending = pendingInOrder([at("a", 1, 10, 72, { approx: true }), at("b", 1, 20, 72, { approx: true }), at("c", 3, 5, 72, { approx: true })]);
  assert.deepEqual(Object.values(reviewPlace(pending, "a")).map((v) => v?.id ?? v), [0, 3, "b"]);
  assert.equal(reviewPlace(pending, "c").next.id, "a", "after the last, the first");
  assert.equal(reviewPlace(pending.slice(0, 1), "a").next, null);
  assert.deepEqual(reviewPlace(pending, "gone"), { index: -1, total: 3, next: null });
});

test("where it was before is read defensively", () => {
  const prev = { doc_id: "ab12cd", pageNumber: 3, rects: [[0.1, 0.2, 0.5, 0.22], [0.1, 0.5, 0.05, 0.6], "x", [0, 0, 1, 2]], quote: "Old text" };
  assert.deepEqual(reviewPrev({ prev }), { docId: "ab12cd", page: 3, rects: [[0.1, 0.2, 0.5, 0.22]], quote: "Old text", clipped: false });
  // a file that is no stored file's id, a page that is no page: the quote alone is shown
  assert.deepEqual(reviewPrev({ prev: { doc_id: "../x", pageNumber: 0.5, quote: 7 } }),
    { docId: "", page: 0, rects: [], quote: "", clipped: false });
  for (const pos of [null, {}, { prev: null }, { prev: [] }, { prev: "x" }]) assert.equal(reviewPrev(pos), null);
  // a quote as long as a prev keeps may be the start of a longer one
  assert.equal(reviewPrev({ prev: { ...prev, quote: "x".repeat(PREV_QUOTE_MAX) } }).clipped, true);
  assert.equal(reviewPrev({ prev: { ...prev, quote: "x".repeat(PREV_QUOTE_MAX - 1) } }).clipped, false);
});

test("the before picture is asked of the file it was on, and not through a share link", () => {
  const prev = reviewPrev({ prev: { doc_id: "ab12cd", pageNumber: 3, rects: [[0.2, 0.5, 0.6, 0.515]], quote: "Old text" } });
  const crop = beforeCrop(prev);
  assert.deepEqual(crop, { box: cropBox(prev.rects), path: `/ai/selection-crop/ab12cd?page=3&box=${cropBox(prev.rects).join(",")}` });
  assert.deepEqual(beforeCrop(prev, { area: true, pageAspect: 1.414 }).box, cropBox(prev.rects, { area: true, pageAspect: 1.414 }));
  // the route is a workspace member's: a share view asks for none, and the card shows the old quote
  assert.equal(beforeCrop(prev, { shared: true }), null);
  // nothing to ask for: no stored file, no page, no rectangle
  for (const other of [{ ...prev, docId: "" }, { ...prev, page: 0 }, { ...prev, rects: [] }, null]) assert.equal(beforeCrop(other), null);
});

test("the before picture's box is the old rectangles and about two lines around them, kept on the page", () => {
  // one line of text, 12 pt high on a Letter page
  const line = [0.2, 0.5, 0.6, 0.5 + 12 / 792];
  const box = cropBox([line]);
  const my = 2.4 * (12 / 792), mx = my * PAGE_ASPECT;
  const want = [line[0] - mx, line[1] - my, line[2] + mx, line[3] + my].map((v) => Math.round(v * 10000) / 10000);
  assert.deepEqual(box, want);
  assert.ok(box.every((v) => Number(v.toFixed(4)) === v), "four decimals");
  // several lines: their union, the margin from a line's height (the median), not the union's
  const two = cropBox([line, [0.1, 0.52, 0.7, 0.52 + 12 / 792]]);
  assert.deepEqual([two[0], two[2]], [Math.round((0.1 - mx) * 10000) / 10000, Math.round((0.7 + mx) * 10000) / 10000]);
  // an area's one rectangle says nothing of the text's size: a body line's margin
  const area = cropBox([[0.3, 0.3, 0.6, 0.6]], { area: true });
  assert.ok(Math.abs(0.3 - area[1] - 2.4 * 0.014) < 1e-4, `an area's margin: ${area}`);
  // at the page's corner it stops at the edge
  assert.deepEqual(cropBox([[0, 0, 0.3, 0.02]]).slice(0, 2), [0, 0]);
  assert.deepEqual(cropBox([[0.7, 0.98, 1, 1]]).slice(2), [1, 1]);
  // a wider page widens the margin across less
  const wide = cropBox([line], { pageAspect: 0.5 });
  assert.ok(wide[0] > box[0] && wide[1] === box[1]);
  for (const rects of [[], null, [[0.5, 0.5, 0.4, 0.6]], [[0, 0, 2, 1]]]) assert.equal(cropBox(rects), null);
});

test("the old rectangles fall in the picture where they were on the page", () => {
  const box = [0.1, 0.4, 0.7, 0.6];
  const [r] = rectsInCrop([[0.25, 0.45, 0.55, 0.5]], box);
  for (const [key, want] of Object.entries({ left: 0.25, top: 0.25, width: 0.5, height: 0.25 })) {
    assert.ok(Math.abs(r[key] - want) < 1e-9, `${key}: ${r[key]}`);
  }
  // one reaching past the box is cut to it, one outside it is left out
  const [cut] = rectsInCrop([[0.05, 0.45, 0.2, 0.5], [0.8, 0.45, 0.9, 0.5]], box);
  assert.equal(rectsInCrop([[0.05, 0.45, 0.2, 0.5], [0.8, 0.45, 0.9, 0.5]], box).length, 1);
  assert.ok(cut.left === 0 && Math.abs(cut.width - 0.1 / 0.6) < 1e-9);
  assert.deepEqual(rectsInCrop([[0.2, 0.45, 0.3, 0.5]], null), []);
  // the placeholder's shape: the box's width over its height on a page of its proportions
  assert.ok(Math.abs(cropAspect(box, 1.5) - (0.6 / 0.2) / 1.5) < 1e-9);
  assert.equal(cropAspect(null), 1);
});

test("the old quote against the current one: a renumbered citation is struck and marked", () => {
  assert.deepEqual(quoteDiff("Spin echoes were measured by Hahn [27]", "Spin echoes were measured by Hahn [25]"), [
    { text: "Spin echoes were measured by Hahn ", tag: "same" },
    { text: "[27]", tag: "del" },
    { text: "[25]", tag: "add" },
  ]);
  // a seventh word before the change is cut to an ellipsis
  assert.equal(quoteDiff("Then spin echoes were measured by Hahn [27]", "Then spin echoes were measured by Hahn [25]")[0].text,
    "… spin echoes were measured by Hahn ");
  // whitespace reads as one space: a quote broken over lines is the same words
  assert.deepEqual(quoteDiff("two  words\nhere", "two words here"), [{ text: "two words here", tag: "same" }]);
  // a word added in the middle, the long stretches either side cut to six words beside it
  const before = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen";
  const after = "one two three four five six seven eight NEW nine ten eleven twelve thirteen fourteen fifteen";
  const parts = quoteDiff(before, after);
  assert.deepEqual(parts.map((p) => p.tag), ["same", "add", "same"]);
  assert.equal(parts[0].text, "… three four five six seven eight ");
  assert.equal(parts[1].text, "NEW ");
  assert.equal(parts[2].text, "nine ten eleven twelve thirteen fourteen …");
  // between two changes, a long stretch keeps six words at each end
  const mid = quoteDiff("A b c d e f g h i j k l m n o p Z", "X b c d e f g h i j k l m n o p Y");
  assert.equal(mid[2].text, " b c d e f g … k l m n o p ");
  // a short stretch stays whole; an empty side is all removed or all added
  assert.deepEqual(quoteDiff("a b", "a c"), [{ text: "a ", tag: "same" }, { text: "b", tag: "del" }, { text: "c", tag: "add" }]);
  assert.deepEqual(quoteDiff("", "new text"), [{ text: "new text", tag: "add" }]);
  assert.deepEqual(quoteDiff("old", ""), [{ text: "old", tag: "del" }]);
  assert.equal(quoteDiff("", "  "), null, "nothing to compare");
  assert.equal(quoteDiff(null, undefined), null);
});

test("a long passage with one change is compared quickly", () => {
  const words = Array.from({ length: 4000 }, (_, i) => `w${i}`);
  const before = words.join(" ");
  const after = [...words.slice(0, 2000), "changed", ...words.slice(2001)].join(" ");
  const started = Date.now();
  const parts = quoteDiff(before, after);
  assert.ok(Date.now() - started < 200, "the common start and end are matched before the table");
  assert.deepEqual(parts.map((p) => p.tag), ["same", "del", "add", "same"]);
  assert.deepEqual([parts[1].text, parts[2].text], ["w2000", "changed"]);
});

test("an old quote clipped to a prev's length is compared only as far as it goes", () => {
  // a paragraph of some 2000 characters whose one change, a citation, is past the cut (the server keeps the
  // first PREV_QUOTE_MAX characters, here inside a word)
  const words = Array.from({ length: 300 }, (_, i) => `word${i}`);
  const paragraph = (cite, list = words) => [...list, cite].join(" ");
  const clip = (text) => {
    const prev = reviewPrev({ prev: { doc_id: "ab", pageNumber: 1, rects: [], quote: text.slice(0, PREV_QUOTE_MAX) } });
    assert.equal(prev.clipped, true);
    assert.ok(/\w$/.test(prev.quote) && !paragraph("[25].").includes(`${prev.quote} `), "cut inside a word");
    return prev;
  };
  const old = clip(paragraph("[25]."));
  const plain = (parts) => parts.filter((p) => p.tag !== "same").map((p) => [p.tag, p.text.trim()]);
  // nothing in the old head changed: no word struck, none marked, and an ellipsis where it stops
  const same = quoteDiff(old.quote, paragraph("[27]."), { clipped: true });
  assert.deepEqual(plain(same), []);
  assert.ok(same.at(-1).text.endsWith("word137 …"), same.at(-1).text);
  // without knowing it was clipped, the cut word is struck and the rest of the paragraph marked added
  assert.ok(plain(quoteDiff(old.quote, paragraph("[27]."))).some(([tag]) => tag === "del"));
  // a word changed inside the head: exactly that word struck and its replacement marked
  const changed = words.map((w) => (w === "word50" ? "WORD50" : w));
  assert.deepEqual(plain(quoteDiff(old.quote, paragraph("[27].", changed), { clipped: true })),
    [["del", "word50"], ["add", "WORD50"]]);
  // a word inserted inside the head: only the insertion, nothing struck at the cut
  const inserted = [...words.slice(0, 80), "NEW", ...words.slice(80)];
  assert.deepEqual(plain(quoteDiff(old.quote, paragraph("[27].", inserted), { clipped: true })), [["add", "NEW"]]);
  // the old head's last whole word replaced: struck, its replacement marked, nothing past it
  const last = words.map((w) => (w === "word137" ? "WORD137" : w));
  assert.deepEqual(plain(quoteDiff(old.quote, paragraph("[27].", last), { clipped: true })),
    [["del", "word137"], ["add", "WORD137"]]);
  // a clipped quote that is one word cut short leaves nothing to compare
  assert.equal(quoteDiff("x".repeat(PREV_QUOTE_MAX), "x".repeat(2000), { clipped: true }), null);
});
