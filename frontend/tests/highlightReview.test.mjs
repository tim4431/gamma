// The review card's arithmetic (pdf/highlightReview.js): which highlights
// are pending or lost and in what order; what the card's Previous and Next
// walk, the ones resolved on a card standing where they stood, round from
// the last to the first; what the card shows for each, the same after a
// step as before it, its buttons and which are pressed or disabled; what
// its steps change (a lost one's Discard too) and whether a step is still
// in effect; the before picture's box and the old rectangles in it, and
// the old quote against the current one.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  PAGE_ASPECT, PREV_QUOTE_MAX, beforeCrop, cropAspect, cropBox, discardApplied, discardKind, discardStep, discardUndone,
  discardedPosition, entryPosition, lostPlace, pastLastPage, pendingInOrder, placeBox, quoteDiff, rectsInCrop,
  reviewActions, reviewCardState, reviewList, reviewPlace, reviewPrev, reviewQueue, reviewStep, stepInEffect, stepOrigin,
} from "../src/pdf/highlightReview.js";
import { acceptedPosition, blockPlace, blocksToHighlights, closestPlacement, findBlock, insertBlockAt } from "../src/shared/model/blockModel.js";

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

test("Previous and Next walk the list round, from the last to the first and back; one alone has neither", () => {
  const list = reviewList([at("a", 1, 10, 72, { approx: true }), at("b", 1, 20, 72, { approx: true }), at("c", 3, 5, 72, { approx: true })]);
  assert.deepEqual(list.map((e) => [e.id, e.step]), [["a", null], ["b", null], ["c", null]]);
  const first = reviewPlace(list, "a");
  assert.deepEqual([first.index, first.total, first.left, first.prev.id, first.next.id], [0, 3, 3, "c", "b"], "before the first, the last");
  assert.equal(reviewPlace(list, "c").next.id, "a", "after the last, the first");
  assert.equal(reviewPlace(list, "b").prev.id, "a");
  const alone = reviewPlace(list.slice(0, 1), "a");
  assert.deepEqual([alone.total, alone.prev, alone.next], [1, null, null], "one alone: Previous and Next disabled");
  assert.deepEqual(reviewPlace(list, "gone"), { index: -1, total: 3, left: 3, prev: null, next: null });
  assert.deepEqual(reviewList(null), []);
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

// The page of the walk tests: A and D hollow, B hollow with a closest match on page 5, C dashed.
const walkPage = () => {
  const closest = { pageNumber: 5, width: 612, height: 792, rects: [{ x1: 72, y1: 300, x2: 400, y2: 312 }], match: 0.6, quote: "new" };
  return [at("A", 1, 10, 72, { lost: true }), { ...at("B", 1, 300, 72, { lost: true, closest }), content: { text: "old" } },
    { ...at("C", 2, 100, 72, { approx: true, match: 0.9 }), content: { text: "q" } }, at("D", 3, 100, 72, { lost: true })];
};
const ids = (list) => list.map((e) => e.id);
const withPos = (all, id, position, extra = {}) => all.map((h) => (h.id === id ? { ...h, position, ...extra } : h));

test("a step changes neither the count nor the walk: a resolved one stands where it stood before its step", () => {
  const [A, B, C, D] = walkPage();
  assert.deepEqual(ids(reviewList([A, B, C, D])), ["A", "B", "C", "D"]);
  // Accept on C: solid where it is, still third of four, one fewer left to check
  const accepted = reviewStep("accepted", { position: C.position });
  let all = withPos([A, B, C, D], "C", accepted.after);
  let steps = new Map([["C", { step: accepted, h: C }]]);
  let list = reviewList(all, steps);
  assert.deepEqual(ids(list), ["A", "B", "C", "D"]);
  assert.equal(list[2].step, accepted, "C's step in effect");
  assert.deepEqual([reviewPlace(list, "C").index, reviewPlace(list, "C").total, reviewPlace(list, "C").left], [2, 4, 3], "\"3 of 4\" stays");
  assert.deepEqual([reviewPlace(list, "C").prev.id, reviewPlace(list, "C").next.id], ["B", "D"], "Previous and Next reach the same ones");
  // without the card's steps it is out of what is left to check (the Document menu's Review counts these)
  assert.deepEqual(ids(reviewList(all)), ["A", "B", "D"]);
  assert.deepEqual(reviewQueue(all).map((h) => h.id), ["A", "B", "D"]);
  // Use closest match on B: on page 5 now, past the others, and still second, where it stood
  const placed = reviewStep("placed", { position: B.position, quote: "old" });
  all = withPos(all, "B", placed.after, { content: { text: "new" } });
  steps = new Map(steps).set("B", { step: placed, h: B });
  list = reviewList(all, steps);
  assert.deepEqual(ids(list), ["A", "B", "C", "D"]);
  assert.equal(list[1].h.position, placed.after, "the highlight as drawn, where the card follows it");
  assert.deepEqual(entryPosition(list[1]), placed.after, "Previous and Next scroll to where it is now");
  // a move from C's accepted card up to page 1's top: C stands where it stood before Accept, pending, on page 2
  const moved = reviewStep("moved", { position: accepted.after, quote: "q", prior: accepted }, at("m", 1, 5).position);
  all = withPos(all, "C", moved.after, { content: { text: "moved here" } });
  steps = new Map(steps).set("C", { step: moved, h: C });
  list = reviewList(all, steps);
  assert.deepEqual(ids(list), ["A", "B", "C", "D"]);
  assert.deepEqual(stepOrigin(moved), { position: C.position, quote: "q" }, "the move's origin is Accept's");
  // all of them resolved: nothing left to check ("All reviewed"), the walk the same
  const a = reviewStep("moved", { position: A.position, quote: "" }, at("a2", 4, 10).position);
  const d = reviewStep("moved", { position: D.position, quote: "" }, at("d2", 4, 20).position);
  all = withPos(withPos(all, "A", a.after), "D", d.after);
  steps = new Map(steps).set("A", { step: a, h: A }).set("D", { step: d, h: D });
  list = reviewList(all, steps);
  assert.deepEqual(ids(list), ["A", "B", "C", "D"]);
  assert.deepEqual([reviewPlace(list, "D").left, reviewPlace(list, "D").total, reviewPlace(list, "D").next.id], [0, 4, "A"]);
});

test("where a highlight stood and what it quoted before the steps of its card", () => {
  const [, B, C] = walkPage();
  const accepted = reviewStep("accepted", { position: C.position });
  assert.deepEqual(stepOrigin(accepted), { position: C.position, quote: undefined }, "Accept keeps the quote");
  const placed = reviewStep("placed", { position: B.position, quote: "old" });
  const movedOn = reviewStep("moved", { position: placed.after, quote: "new", prior: placed }, at("m", 2, 10).position);
  assert.deepEqual(stepOrigin(movedOn), { position: B.position, quote: "old" }, "the quote before the first step that rewrote it");
  assert.deepEqual(stepOrigin(null), { position: null, quote: undefined });
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

test("the card's state: pending, and lost with and without a closest match", () => {
  const prev = { doc_id: "ab12", pageNumber: 1, rects: [[0.1, 0.2, 0.5, 0.22]], quote: "measured by Hahn [27]" };
  const pending = { ...at("p", 2, 100, 72, { approx: true, match: 0.874, prev }), content: { text: "measured by Hahn [25]" } };
  const card = reviewCardState(pending);
  assert.deepEqual([card.kind, card.percent, card.area, card.before.docId, card.step, card.position], ["pending", 87, false, "ab12", null, pending.position]);
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
  // solid, with no step of the card's: nothing to show
  assert.equal(reviewCardState(at("s", 1, 10)), null);
  assert.equal(reviewCardState(null), null);
});

test("after a step the card shows what it showed before it, drawn from where the highlight stood then, the step's button pressed", () => {
  const prev = { doc_id: "ab12", pageNumber: 1, rects: [[0.1, 0.2, 0.5, 0.22]], quote: "measured by Hahn [27]" };
  const pending = { ...at("p", 2, 100, 72, { approx: true, match: 0.874, prev }), content: { text: "measured by Hahn [25]" } };
  const before = reviewCardState(pending);
  // Accept: solid now, the card as it was
  const accepted = reviewStep("accepted", { position: pending.position });
  const solid = { ...pending, position: accepted.after };
  assert.deepEqual(reviewCardState(solid, { done: accepted }), { ...before, step: "accepted" });
  // a move from the accepted card: the pending card again, Moved pressed, with the quote it had then
  const moved = reviewStep("moved", { position: accepted.after, quote: "measured by Hahn [25]", prior: accepted }, at("m", 3, 10).position);
  const there = { ...pending, position: moved.after, content: { text: "somewhere else" } };
  assert.deepEqual(reviewCardState(there, { done: moved }), { ...before, step: "moved" });
  // Use closest match: the lost card, its closest match and comparison, though its place and quote are the candidate's now
  const closest = { pageNumber: 2, width: 612, height: 792, boundingRect: { x1: 72, y1: 300, x2: 400, y2: 312 },
    rects: [{ x1: 72, y1: 300, x2: 400, y2: 312 }], match: 0.62, quote: "measured long ago by Hahn" };
  const lost = { ...at("l", 1, 50, 72, { lost: true, doc_id: "ab12", closest }), content: { text: "measured by Hahn [27]" } };
  const lostBefore = reviewCardState(lost, { fileId: "cd34" });
  const placed = reviewStep("placed", { position: lost.position, quote: "measured by Hahn [27]" });
  const onIt = { ...lost, position: placed.after, content: { text: closest.quote } };
  assert.deepEqual(reviewCardState(onIt, { done: placed, fileId: "cd34" }), { ...lostBefore, step: "placed" });
  // Discard: the highlight as the card last had it
  const discarded = { kind: "discarded", deleted: true, before: { position: lost.position }, removed: {} };
  assert.deepEqual(reviewCardState(lost, { done: discarded, fileId: "cd34" }), { ...lostBefore, step: "discarded" });
});

test("the card's buttons stay in their places across a step: its own pressed, those that no longer apply disabled", () => {
  const row = (state, opts) => reviewActions(state, opts).map((a) => `${a.key}${a.pressed ? "*" : ""}${a.disabled ? "-" : ""}`).join(" ");
  const pending = { kind: "pending", step: null };
  const lost = { kind: "lost", closest: { percent: 70 }, step: null };
  assert.equal(row(pending), "accept move");
  assert.equal(row({ ...pending, step: "accepted" }), "accept* move", "Move… still moves an accepted one");
  assert.equal(row({ ...pending, step: "moved" }), "accept- move*");
  assert.equal(row(lost, { discard: "delete" }), "place move discard");
  assert.equal(row({ ...lost, step: "placed" }, { discard: "delete" }), "place* move discard-", "nothing lost to discard");
  assert.equal(row({ ...lost, step: "discarded" }, { discard: "unplace" }), "place- move- discard*", "nothing on the page to place or move");
  assert.equal(row({ ...lost, step: "moved" }, { discard: "delete" }), "place- move* discard-");
  // no closest match, or no Use closest match for this viewer; Discard only when what it does is known
  assert.equal(row({ ...lost, closest: null }, { discard: "delete" }), "move discard");
  assert.equal(row(lost, { place: false }), "move");
  assert.deepEqual(reviewActions(null), []);
  // each button's step
  assert.deepEqual(reviewActions(lost, { discard: "delete" }).map((a) => a.step), ["placed", "moved", "discarded"]);
});

test("a step is in effect only while the highlight is as the step left it", () => {
  const [A, B, C] = walkPage();
  const accepted = reviewStep("accepted", { position: C.position });
  const solid = { ...C, position: accepted.after };
  assert.equal(stepInEffect(accepted, solid), true);
  assert.equal(stepInEffect(accepted, solid, accepted.after), true, "by the stored position when known");
  assert.equal(stepInEffect(accepted, solid, null), false, "deleted (its block gone), though still drawn a moment");
  assert.equal(stepInEffect(null, solid), false);
  // the app's own undo puts it back dashed: no longer in effect, the card as it is now (to check again)
  const steps = new Map([["C", { step: accepted, h: C }]]);
  assert.equal(stepInEffect(accepted, C), false);
  let list = reviewList([A, B, C], steps);
  assert.deepEqual(list.map((e) => [e.id, e.step]), [["A", null], ["B", null], ["C", null]]);
  assert.deepEqual(reviewCardState(list[2].h, { done: list[2].step }), reviewCardState(C), "the card pending, Accept not pressed");
  // a collaborator moved it, or it was moved by hand: solid elsewhere, out of the walk
  list = reviewList([A, B, { ...C, position: at("x", 2, 500).position }], steps);
  assert.deepEqual(ids(list), ["A", "B"]);
  // deleted by others: out of the walk
  assert.deepEqual(ids(reviewList([A, B], steps)), ["A", "B"]);
  assert.deepEqual(ids(reviewList([A, B, solid], steps, (id) => (id === "C" ? null : undefined))), ["A", "B"], "its block gone");
});

test("the app's own undo of a move started from an accepted or placed card leaves that card's step in effect", () => {
  const [A, B, C, D] = walkPage();
  const row = (state) => reviewActions(state, { discard: "delete" }).map((a) => `${a.key}${a.pressed ? "*" : ""}${a.disabled ? "-" : ""}`).join(" ");
  // Accept on C, then Move… from its accepted card: the steps hold the move, its prior the Accept
  const accepted = reviewStep("accepted", { position: C.position });
  const moved = reviewStep("moved", { position: accepted.after, quote: "q", prior: accepted }, at("m", 1, 5).position);
  const steps = new Map([["C", { step: moved, h: C }]]);
  const page = (pos, text = "q") => withPos([A, B, C, D], "C", pos, { content: { text } });
  const stored = (pos) => (id) => (id === "C" ? pos : undefined);
  // the move in effect: Moved
  let all = page(moved.after, "moved here");
  let list = reviewList(all, steps, stored(moved.after));
  assert.deepEqual(list.map((e) => [e.id, e.step?.kind || null]), [["A", null], ["B", null], ["C", "moved"], ["D", null]]);
  // Ctrl+Z takes the move back: the highlight is as the Accept left it, so C stays, Accepted, where it stood
  all = page(accepted.after);
  list = reviewList(all, steps, stored(accepted.after));
  assert.deepEqual(list.map((e) => [e.id, e.step?.kind || null]), [["A", null], ["B", null], ["C", "accepted"], ["D", null]]);
  assert.equal(list[2].step, accepted, "the Accept, which its pressed button undoes");
  assert.deepEqual([reviewPlace(list, "C").index, reviewPlace(list, "C").total], [2, 4], "\"3 of 4\" stays");
  assert.deepEqual([reviewPlace(list, "B").next.id, reviewPlace(list, "D").prev.id], ["C", "C"], "Previous and Next still reach it");
  const state = reviewCardState(list[2].h, { done: list[2].step });
  assert.deepEqual(state, { ...reviewCardState(C), step: "accepted" }, "the pending card, Accept pressed");
  assert.equal(row(state), "accept* move", "Move… usable again");
  // and by the drawn highlight alone, its stored position not known
  assert.equal(reviewList(all, steps)[2].step, accepted);
  // a redo (Ctrl+Y) of the move: Moved again
  assert.equal(reviewList(page(moved.after), steps, stored(moved.after))[2].step, moved);
  // both taken back: dashed, to check
  list = reviewList(page(C.position), steps, stored(C.position));
  assert.deepEqual(list.map((e) => [e.id, e.step]), [["A", null], ["B", null], ["C", null], ["D", null]]);
  // Use closest match on B, then a move from its placed card, then Ctrl+Z: Placed, Discard still disabled
  const placed = reviewStep("placed", { position: B.position, quote: "old" });
  const movedOn = reviewStep("moved", { position: placed.after, quote: "new", prior: placed }, at("m2", 4, 10).position);
  const back = withPos([A, B, C, D], "B", placed.after, { content: { text: "new" } });
  list = reviewList(back, new Map([["B", { step: movedOn, h: B }]]), (id) => (id === "B" ? placed.after : undefined));
  assert.deepEqual(list.map((e) => [e.id, e.step?.kind || null]), [["A", null], ["B", "placed"], ["C", null], ["D", null]]);
  assert.equal(row(reviewCardState(list[1].h, { done: list[1].step })), "place* move discard-");
});

test("each step on the card says what it changes, for its undo", () => {
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

// --- Discard ------------------------------------------------------------------

// A page's tree (App's blocks: the page's children) with a lost highlight
// `l` among other notes, as the store keeps them.
const lostPos = { ...at("l", 1, 50, 72, { lost: true, doc_id: "ab12", closest: { pageNumber: 1, width: 612, height: 792,
  rects: [{ x1: 72, y1: 300, x2: 400, y2: 312 }], match: 0.6, quote: "near" } }).position };
const hl = (extra = {}) => ({ id: "l", content: "", children: [], properties: { quote: "This sentence is gone", color: "#ffe28f66",
  pdf_position: lostPos }, ...extra });
const page = (l = hl()) => [
  { id: "a", content: "First note", children: [], properties: {} },
  { id: "b", content: "Section", children: [{ id: "b1", content: "inside", children: [], properties: {} }, l], properties: {} },
  { id: "c", content: "Last note", children: [], properties: {} },
];

test("Discard deletes a lost highlight that holds nothing of its own, and keeps one with a note, children or comments", () => {
  assert.equal(discardKind(hl()), "delete");
  assert.equal(discardKind(hl({ content: "   " })), "delete", "a note of spaces is no note");
  assert.equal(discardKind(hl({ content: "Why did this go?" })), "unplace", "its own note");
  assert.equal(discardKind(hl({ children: [{ id: "k", content: "", children: [], properties: {} }] })), "unplace", "a child block, even empty");
  assert.equal(discardKind(hl(), 1), "unplace", "a comment thread");
  // only a lost one is discarded
  assert.equal(discardKind(hl({ properties: { pdf_position: at("s", 1, 10).position } })), null, "a solid highlight");
  assert.equal(discardKind(hl({ properties: { pdf_position: { ...lostPos, lost: false, approx: true } } })), null, "a dashed one");
  assert.equal(discardKind(hl({ properties: { pdf_position: { ...lostPos, pageNumber: 0 } } })), null, "no page to keep");
  assert.equal(discardKind(null), null);
  // kept as a note: its page alone, the shape of a highlight whose place is not known
  assert.deepEqual(discardedPosition(lostPos), { pageNumber: 1 });
});

test("Discard of a bare one deletes it as one tree edit, and its Undo puts it back where it was, lost", () => {
  const tree = page();
  const step = discardStep(tree, "l");
  assert.deepEqual([step.kind, step.deleted, step.removed.parentId, step.removed.index], ["discarded", true, "b", 1]);
  assert.equal(step.before.position, lostPos);
  const gone = discardApplied(tree, "l", step);
  assert.equal(findBlock(gone, "l"), null, "deleted");
  assert.deepEqual(gone[1].children.map((b) => b.id), ["b1"]);
  // the queue and the page's highlights no longer have it
  assert.deepEqual(reviewQueue(blocksToHighlights(gone)), []);
  const back = discardUndone(gone, "l", step);
  assert.deepEqual(back[1].children.map((b) => b.id), ["b1", "l"], "back in its place");
  assert.deepEqual(findBlock(back, "l"), hl(), "as it was: lost, its quote, its rectangles and its file");
  assert.deepEqual(reviewQueue(blocksToHighlights(back)).map((h) => h.id), ["l"], "in the queue again");
  assert.equal(discardUndone(back, "l", step), back, "back already: nothing to do");
  // its parent gone since: at the top's end
  const orphan = discardUndone(gone.filter((b) => b.id !== "b"), "l", step);
  assert.deepEqual(orphan.map((b) => b.id), ["a", "c", "l"]);
  assert.equal(discardApplied(gone, "l", step), gone, "a discard of one gone already changes nothing");
});

test("Discard of one with a note keeps the note off the PDF, and its Undo puts it back on the page", () => {
  const noted = hl({ content: "Why did this go?", children: [{ id: "k", content: "a reply", children: [], properties: {} }] });
  const tree = page(noted);
  const step = discardStep(tree, "l");
  assert.deepEqual([step.deleted, step.after], [false, { pageNumber: 1 }]);
  assert.ok(!("removed" in step));
  const off = discardApplied(tree, "l", step);
  const kept = findBlock(off, "l");
  assert.deepEqual(kept.properties, { quote: "This sentence is gone", color: "#ffe28f66", pdf_position: { pageNumber: 1 } },
    "its quote and colour kept; lost, closest, doc_id and the rectangles gone");
  assert.deepEqual([kept.content, kept.children.map((b) => b.id)], ["Why did this go?", ["k"]], "its note and children stay");
  assert.equal(off[0], tree[0], "the rest of the tree keeps its identity");
  // drawn nowhere, so out of the page's highlights and the queue
  assert.deepEqual(blocksToHighlights(off).map((h) => h.id), []);
  assert.deepEqual(reviewQueue(blocksToHighlights(off)), []);
  const back = discardUndone(off, "l", step);
  assert.deepEqual(findBlock(back, "l").properties.pdf_position, lostPos, "lost on its page again");
  assert.deepEqual(reviewQueue(blocksToHighlights(back)).map((h) => h.id), ["l"]);
  // changed since (moved by hand): the Undo leaves it
  const moved = discardApplied(tree, "l", step).map((b) => (b.id !== "b" ? b : { ...b, children: b.children.map((x) => (x.id !== "l" ? x
    : { ...x, properties: { ...x.properties, pdf_position: at("m", 2, 10).position } })) }));
  assert.equal(discardUndone(moved, "l", step), moved);
  // and a Discard whose highlight changed before it applied changes nothing
  assert.equal(discardApplied(moved, "l", step), moved);
});

test("the comment threads decide, and only a lost highlight is discarded", () => {
  const tree = page();
  assert.equal(discardStep(tree, "l", 2).deleted, false, "threads keep it as a note");
  assert.equal(discardStep(tree, "a"), null, "a plain note");
  assert.equal(discardStep(tree, "nope"), null);
  assert.equal(discardUndone(tree, "l", null), tree);
  assert.equal(discardUndone(tree, "l", reviewStep("accepted", { position: { ...lostPos, approx: true, lost: false } })), tree,
    "another kind of step is not a discard's");
});

test("after Discard the card stays as it was, Discarded; one it deleted stays in the walk, one deleted by others leaves it", () => {
  const tree = page();
  const seen = blocksToHighlights(tree)[0];
  const step = discardStep(tree, "l");
  const gone = discardApplied(tree, "l", step);
  const positionOf = (blocks) => (id) => findBlock(blocks, id)?.properties?.pdf_position ?? null;
  const steps = new Map([["l", { step, h: seen }]]);
  // deleted, drawn no more: the walk keeps it where it stood, the card as it was with Discarded pressed
  let list = reviewList(blocksToHighlights(gone), steps, positionOf(gone));
  assert.deepEqual(list.map((e) => [e.id, e.h, e.step]), [["l", seen, step]]);
  assert.deepEqual(reviewCardState(list[0].h, { done: list[0].step }), { ...reviewCardState(seen), step: "discarded" });
  assert.deepEqual(entryPosition(list[0]), lostPos, "Previous and Next scroll to where it stood");
  assert.deepEqual([reviewPlace(list, "l").left, reviewPlace(list, "l").total], [0, 1], "All reviewed");
  // Discarded pressed again (or the app's undo): back, lost, to check
  const back = discardUndone(gone, "l", step);
  list = reviewList(blocksToHighlights(back), steps, positionOf(back));
  assert.deepEqual(list.map((e) => [e.id, e.step]), [["l", null]]);
  assert.equal(stepInEffect(step, list[0].h, positionOf(back)("l")), false);
  // drawn on the page again (placed by hand since): out of the walk, nothing of the Discard to show
  const placedByHand = back.map((b) => (b.id !== "b" ? b : { ...b, children: b.children.map((x) => (x.id !== "l" ? x
    : { ...x, properties: { ...x.properties, pdf_position: at("m", 2, 10).position } })) }));
  assert.deepEqual(reviewList(blocksToHighlights(placedByHand), steps, positionOf(placedByHand)), []);
  // without the store's word (no positionOf), drawn no more is enough
  assert.deepEqual(reviewList([], steps).map((e) => e.id), ["l"]);

  // kept as a note: in effect while its block keeps the position Discard wrote; deleted by others, out of the walk
  const noted = page(hl({ content: "Why did this go?" }));
  const keep = discardStep(noted, "l");
  const off = discardApplied(noted, "l", keep);
  const kept = new Map([["l", { step: keep, h: blocksToHighlights(noted)[0] }]]);
  assert.deepEqual(reviewList(blocksToHighlights(off), kept, positionOf(off)).map((e) => e.id), ["l"]);
  const removed = off.map((b) => (b.id !== "b" ? b : { ...b, children: b.children.filter((x) => x.id !== "l") }));
  assert.deepEqual(reviewList(blocksToHighlights(removed), kept, positionOf(removed)), []);
});

test("a discarded one's card stays anchored where its rectangles were on its page", () => {
  const pos = { pageNumber: 1, width: 612, height: 792, rects: [{ x1: 72, y1: 100, x2: 300, y2: 112 }, { x1: 72, y1: 114, x2: 200, y2: 126 }] };
  // the page drawn at twice its size, 40 px from the left and scrolled 300 px up
  assert.deepEqual(placeBox({ left: 40, top: -300, width: 1224, height: 1584 }, pos), { left: 184, top: -100, right: 640, bottom: -48 });
  assert.equal(placeBox(null, pos), null);
  assert.equal(placeBox({ left: 0, top: 0, width: 612, height: 792 }, { pageNumber: 1 }), null, "a position with no rectangle");
});

test("a block's place in the tree, and a block put back there", () => {
  const tree = page();
  assert.deepEqual(blockPlace(tree, "c"), { parentId: null, index: 2 });
  assert.deepEqual(blockPlace(tree, "b1"), { parentId: "b", index: 0 });
  assert.equal(blockPlace(tree, "nope"), null);
  const x = { id: "x", content: "", children: [], properties: {} };
  assert.deepEqual(insertBlockAt(tree, null, 1, x).map((b) => b.id), ["a", "x", "b", "c"]);
  assert.deepEqual(insertBlockAt(tree, null, 99, x).map((b) => b.id), ["a", "b", "c", "x"], "past the last: at the end");
  const under = insertBlockAt(tree, "b", 0, x);
  assert.deepEqual(under[1].children.map((b) => b.id), ["x", "b1", "l"]);
  assert.equal(under[0], tree[0], "only the path to the parent is copied");
  assert.equal(insertBlockAt(tree, "nope", 0, x), tree, "no such parent: the same tree");
});
