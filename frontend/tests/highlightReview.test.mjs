// The review card's arithmetic (pdf/highlightReview.js): which highlights
// are pending or lost and in what order, which comes next, what the card
// shows for each and what its steps change, the before picture's box and
// the old rectangles in it, and the old quote against the current one.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  PAGE_ASPECT, PREV_QUOTE_MAX, beforeCrop, cropAspect, cropBox, lostPlace, pastLastPage, pendingInOrder, quoteDiff, rectsInCrop,
  reviewCardState, reviewPlace, reviewPrev, reviewQueue, reviewStep,
} from "../src/pdf/highlightReview.js";
import { acceptedPosition, closestPlacement } from "../src/shared/model/blockModel.js";

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

test("the review queue is the dashed and the hollow ones, in document order; a solid one is not in it", () => {
  const list = [
    at("p2", 2, 100, 72, { approx: true, match: 0.9 }),
    at("solid", 1, 50),
    at("lostLow", 1, 400, 320, { lost: true, doc_id: "ab12" }),
    at("lostAndPending", 1, 10, 72, { approx: true, lost: true }),
    at("rough", 1, 400, 72, { approx: true, match: 0.85 }),
  ];
  assert.deepEqual(reviewQueue(list).map((h) => h.id), ["lostAndPending", "rough", "lostLow", "p2"]);
  // Accept all is for the dashed ones only
  assert.deepEqual(pendingInOrder(list).map((h) => h.id), ["rough", "p2"]);
  assert.deepEqual(reviewQueue(null), []);
});

test("Next from a resolved highlight is the first unresolved one after its place, again the first after the last", () => {
  const queue = reviewQueue([at("a", 1, 10, 72, { approx: true }), at("b", 1, 300, 72, { lost: true }), at("c", 3, 5, 72, { approx: true })]);
  // accepted, so out of the queue: the count is of the ones left, Next the one after it
  const accepted = at("x", 1, 100);
  assert.deepEqual(reviewPlace(queue, accepted), { index: -1, total: 3, next: queue[1] });
  assert.equal(reviewPlace(queue, at("y", 2, 500)).next.id, "c", "on a page between");
  assert.equal(reviewPlace(queue, at("z", 4, 10)).next.id, "a", "after the last, the first");
  assert.equal(reviewPlace(queue, at("tie", 1, 300)).next.id, "b", "one at the very same place is next");
  // one still in it walks the queue as before; nothing left, no next
  assert.equal(reviewPlace(queue, queue[1]).next.id, "c");
  assert.deepEqual(reviewPlace([], accepted), { index: -1, total: 0, next: null });
  assert.equal(reviewPlace(queue, "x").next, null, "an id alone has no place to go on from");
});

test("Next after a step that took the highlight elsewhere goes on from where it stood", () => {
  const closest = { pageNumber: 5, width: 612, height: 792, rects: [{ x1: 72, y1: 300, x2: 400, y2: 312 }], match: 0.6, quote: "new" };
  const all = [at("A", 1, 10, 72, { lost: true }), at("B", 1, 300, 72, { lost: true, closest }), at("C", 2, 100, 72, { approx: true }),
    at("D", 3, 100, 72, { lost: true })];
  assert.deepEqual(reviewQueue(all).map((h) => h.id), ["A", "B", "C", "D"]);
  // Use closest match on B: it is on page 5 now, past every other; Next is C, which came after it, not A again
  const placed = reviewStep("placed", { position: all[1].position, quote: "old" });
  const b = { ...all[1], position: placed.after };
  const left = reviewQueue([all[0], b, all[2], all[3]]);
  assert.deepEqual(left.map((h) => h.id), ["A", "C", "D"]);
  assert.equal(reviewPlace(left, b, placed).next.id, "C");
  assert.equal(reviewPlace(left, b).next.id, "A", "by its new place alone it would start over");
  // a move from C's card up to page 1's top: Next is D, not A
  const moved = reviewStep("moved", { position: all[2].position, quote: "q" }, at("m", 1, 5).position);
  const c = { ...all[2], position: moved.after };
  const rest = reviewQueue([all[0], all[1], c, all[3]]);
  assert.equal(reviewPlace(rest, c, moved).next.id, "D");
  // one back in the queue (its Undo) is found there whatever the step says
  assert.equal(reviewPlace(reviewQueue(all), all[1], placed).next.id, "C");
  assert.equal(reviewPlace(reviewQueue(all), all[1], placed).index, 1);
});

test("a highlight past the PDF's last page is told apart; a page count not known yet tells nothing", () => {
  assert.equal(pastLastPage({ pageNumber: 10 }, 3), true);
  assert.equal(pastLastPage({ pageNumber: 3 }, 3), false);
  assert.equal(pastLastPage({ pageNumber: 10 }, 0), false);
  assert.equal(pastLastPage(null, 3), false);
  // it stays in the queue, last in document order, for Next and the Document menu to reach
  assert.deepEqual(reviewQueue([at("gone", 10, 100, 72, { lost: true }), at("here", 2, 100, 72, { approx: true })]).map((h) => h.id),
    ["here", "gone"]);
});

test("a lost highlight's before is its own place, a picture when its file is another than the page's", () => {
  const lost = { ...at("l", 2, 396, 61.2, { lost: true, doc_id: "ab12" }), content: { text: "The trap light is detuned" } };
  const place = lostPlace(lost, "cd34");
  assert.deepEqual(place, { docId: "ab12", page: 2, rects: [[0.1, 0.5, 0.2634, 0.5152]], quote: "The trap light is detuned",
    clipped: false, current: false });
  assert.equal(beforeCrop(place).path.startsWith("/ai/selection-crop/ab12?page=2&box="), true);
  // measured on the page's current file, or with no file named: its wording alone
  for (const pos of [{ doc_id: "cd34" }, {}]) {
    const own = lostPlace({ ...lost, position: { ...lost.position, doc_id: undefined, ...pos } }, "cd34");
    assert.deepEqual([own.current, own.docId, own.quote], [true, "", "The trap light is detuned"], JSON.stringify(pos));
    assert.equal(beforeCrop(own), null);
  }
  // a name that is no stored file (a PDF shown from its link alone): no picture, though not this file's
  assert.deepEqual([lostPlace({ ...lost, position: { ...lost.position, doc_id: "unstored" } }, "cd34")].map((p) => [p.current, p.docId]),
    [[false, ""]]);
  // rectangles kept on the page, none without the frame's size
  const off = lostPlace({ ...lost, position: { ...lost.position, rects: [{ x1: -10, y1: 700, x2: 700, y2: 800 }, { x1: "a" }] } });
  assert.deepEqual(off.rects, [[0, 0.8838, 1, 1]]);
  assert.deepEqual(lostPlace({ ...lost, position: { ...lost.position, width: 0 } }).rects, []);
});

test("the card's state: pending, lost with and without a closest match, and resolved", () => {
  const prev = { doc_id: "ab12", pageNumber: 1, rects: [[0.1, 0.2, 0.5, 0.22]], quote: "measured by Hahn [27]" };
  const pending = { ...at("p", 2, 100, 72, { approx: true, match: 0.874, prev }), content: { text: "measured by Hahn [25]" } };
  const card = reviewCardState(pending);
  assert.deepEqual([card.kind, card.percent, card.area, card.before.docId], ["pending", 87, false, "ab12"]);
  assert.deepEqual(card.diff.map((p) => p.tag), ["same", "del", "add"]);
  // an estimate: no percentage; an area: no comparison
  const area = reviewCardState({ ...pending, position: { ...pending.position, area: true, match: undefined } });
  assert.deepEqual([area.percent, area.diff], [null, null]);

  // lost, nothing close: where it was, a picture of the file it was measured on, with its quote
  const closest = { pageNumber: 2, width: 612, height: 792, boundingRect: { x1: 72, y1: 300, x2: 400, y2: 312 },
    rects: [{ x1: 72, y1: 300, x2: 400, y2: 312 }], match: 0.62, quote: "measured long ago by Hahn" };
  const lost = { ...at("l", 1, 50, 72, { lost: true, doc_id: "ab12" }), content: { text: "measured by Hahn [27]" } };
  const alone = reviewCardState(lost, { fileId: "cd34" });
  assert.deepEqual([alone.kind, alone.before.docId, alone.before.quote, alone.closest, alone.diff], ["lost", "ab12", "measured by Hahn [27]", null, null]);
  // with a closest match: its percentage, its place and the stored quote against its text
  const near = reviewCardState({ ...lost, position: { ...lost.position, closest } }, { fileId: "cd34" });
  assert.deepEqual([near.closest.percent, near.closest.position.pageNumber, near.closest.quote], [62, 2, "measured long ago by Hahn"]);
  assert.ok(near.diff.some((p) => p.tag === "del" && p.text.includes("[27]")) && near.diff.some((p) => p.tag === "add" && p.text.includes("long ago")));
  assert.equal(near.before.docId, "ab12", "the picture of where it was stays");
  // on the current file: the wording alone, or none when the comparison holds it
  const here = { ...lost, position: { ...lost.position, doc_id: undefined } };
  assert.deepEqual([reviewCardState(here).before.current, reviewCardState(here).before.quote], [true, "measured by Hahn [27]"]);
  assert.equal(reviewCardState({ ...here, position: { ...here.position, closest } }).before, null);
  // a lost area keeps no closest match
  assert.equal(reviewCardState({ ...lost, position: { ...lost.position, area: true, closest } }).closest, null);
  // a lost one that was pending reads as lost
  assert.equal(reviewCardState({ ...lost, position: { ...lost.position, approx: true, match: 0.9 } }).kind, "lost");

  // resolved: a step taken on the card left it solid; back in the queue (undone), it reads as what it is again
  const done = reviewStep("accepted", { position: pending.position });
  const solid = { ...pending, position: done.after };
  assert.deepEqual(reviewCardState(solid, { done }), { kind: "resolved", step: "accepted" });
  assert.equal(reviewCardState(pending, { done }).kind, "pending");
  assert.equal(reviewCardState(solid), null, "solid with no step of the card's: nothing to show");
  assert.equal(reviewCardState(null), null);
});

test("each step on the card says what it changes, for its Undo", () => {
  const prev = { doc_id: "ab12", pageNumber: 1, rects: [], quote: "old" };
  const pending = at("p", 2, 100, 72, { approx: true, match: 0.9, prev, doc_id: "ab12" }).position;
  const accept = reviewStep("accepted", { position: pending, quote: "q" });
  assert.deepEqual(accept, { kind: "accepted", before: { position: pending }, after: acceptedPosition(pending) });
  assert.ok(!("quote" in accept.before), "Accept keeps the quote");
  assert.equal(reviewStep("accepted", { position: at("s", 1, 10).position }), null, "nothing to accept on a solid one");
  const closest = { pageNumber: 2, width: 612, height: 792, rects: [{ x1: 72, y1: 300, x2: 400, y2: 312 }], match: 0.6, quote: "new" };
  const lost = { ...at("l", 1, 50).position, lost: true, closest };
  const placed = reviewStep("placed", { position: lost, quote: "old text" });
  assert.deepEqual(placed, { kind: "placed", before: { position: lost, quote: "old text" }, after: closestPlacement(lost).position });
  assert.equal(reviewStep("placed", { position: { ...lost, closest: undefined } }), null);
  const to = at("m", 3, 10).position;
  assert.deepEqual(reviewStep("moved", { position: lost, quote: "old text" }, to), { kind: "moved", before: { position: lost, quote: "old text" }, after: to });
  assert.equal(reviewStep("moved", { position: lost }), null, "a move needs where it went");
  assert.equal(reviewStep("other", { position: lost }), null);
  assert.equal(reviewStep("accepted", {}), null);
  // a move from a card already resolved keeps that card's step, for the move's Undo to go back to, while the
  // position moved from is the one that step wrote
  const solid = accept.after;
  const again = reviewStep("moved", { position: solid, quote: "q", prior: accept }, to);
  assert.deepEqual(again, { kind: "moved", before: { position: solid, quote: "q" }, after: to, prior: accept });
  assert.ok(!("prior" in reviewStep("moved", { position: lost, quote: "q", prior: accept }, to)), "not over a later change");
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
