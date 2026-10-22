// node --test tests/inkHistory.test.mjs (from frontend/) — the stroke
// history's rules (ink/inkHistory.js, useInkDocument.js's history): the
// cap, redo cleared by an edit, an undo or a redo landing on the drawings
// as they are now, an eraser pass's flushes folding into one entry, and
// what the pill says.
import assert from "node:assert/strict";
import { test } from "node:test";
import { encodeStroke, newInk, translateStrokes } from "../src/ink/ink.js";
import { HISTORY_CAP, foldInto, passFold, recordEntry, stepChanges, undoMessage } from "../src/ink/inkHistory.js";
import { setLocale } from "../src/shared/i18n/i18n.js";

const stroke = (id, x = 10) => encodeStroke({ id, ch: "xyt", pen: false, samples: [{ x, y: 10, t: 0 }, { x: x + 5, y: 12, t: 8 }] });
const drawing = (...strokes) => ({ ...newInk(1, 612, 792), strokes });
const ids = (ink) => ink.strokes.map((s) => s.id);
const empty = () => ({ undo: [], redo: [] });

test("the history keeps the last 200 entries", () => {
  assert.equal(HISTORY_CAP, 200);
  const h = empty();
  for (let i = 0; i <= HISTORY_CAP; i++) recordEntry(h, [{ id: "g1", page: 1, i }], { label: "ink stroke" });
  assert.equal(h.undo.length, 200);
  assert.equal(h.undo[0].changes[0].i, 1, "the oldest went");
  assert.equal(h.undo.at(-1).changes[0].i, 200);
});

test("an edit's entry empties the redo list; an eraser pass's keeps its pass", () => {
  const h = { undo: [], redo: [{ changes: [], label: "ink move" }] };
  const changes = [{ id: "g1", page: 1, before: drawing(stroke("a")), after: drawing() }];
  recordEntry(h, changes, { label: "ink erasure", pass: 7 });
  assert.deepEqual(h.redo, []);
  assert.deepEqual(h.undo, [{ changes, label: "ink erasure", pass: 7 }]);
  recordEntry(h, changes, { label: "ink stroke", pass: null });
  assert.deepEqual(h.undo[1], { changes, label: "ink stroke" }, "no pass, no key");
});

test("an undo lands on the drawing as it is now: strokes drawn meanwhile stay", () => {
  const a = stroke("a"), b = stroke("b", 40), c = stroke("c", 80);
  const entry = { label: "ink stroke", changes: [{ id: "g1", page: 1, before: drawing(a), after: drawing(a, b) }] };
  const now = drawing(a, b, c);   // someone added c since
  const undo = stepChanges(entry, false, () => now);
  assert.equal(undo.kept, 0);
  assert.equal(undo.changes[0].before, now, "the step starts from the drawing as it is now");
  assert.deepEqual(ids(undo.changes[0].after), ["a", "c"]);
  assert.deepEqual({ ...undo.changes[0], before: null, after: null }, { id: "g1", page: 1, before: null, after: null });
  // and redo puts b back onto whatever is there then
  const redo = stepChanges(entry, true, () => drawing(a, c));
  assert.equal(redo.kept, 0);
  assert.deepEqual(ids(redo.changes[0].after), ["a", "c", "b"], "b after a, behind c, which was added there meanwhile");
});

test("what someone changed since is kept over the step and counted; a group with no strokes now steps from the entry", () => {
  const a = stroke("a"), b = stroke("b", 40);
  const moved = translateStrokes(drawing(a, b), ["b"], 5, 5);   // someone moved b since
  const entry = { label: "ink stroke", changes: [{ id: "g1", page: 1, before: drawing(a), after: drawing(a, b) },
    { id: "g2", page: 1, before: drawing(), after: drawing(stroke("x")) }] };
  const { changes, kept } = stepChanges(entry, false, (id) => (id === "g1" ? moved : null));
  assert.equal(kept, 1, "b's undo lost to the move: one stroke kept");
  assert.deepEqual(ids(changes[0].after), ["a", "b"]);
  assert.equal(changes[0].after.strokes[1], moved.strokes[1], "the moved b, as they left it");
  assert.equal(changes[1].before, entry.changes[1].after, "g2 has no strokes now (gone): the step starts from the entry's after");
  assert.deepEqual(ids(changes[1].after), []);
});

test("a step that meets someone's change since counts at least one stroke, though none of ours was lost", () => {
  const a = stroke("a");
  const entry = { label: "ink move", changes: [{ id: "g1", page: 1, before: drawing(a), after: translateStrokes(drawing(a), ["a"], 30, 30) }] };
  const { changes, kept } = stepChanges(entry, false, () => drawing());   // a erased since
  assert.equal(kept, 1);
  assert.deepEqual(ids(changes[0].after), ["a"], "an edit beats a delete: the move undone brings a back");
  assert.equal(undoMessage(entry, false, kept, 1), "Undone: ink move (page 1; 1 stroke someone changed since was kept).");
});

test("an eraser pass's flushes fold into its entry across groups", () => {
  const a = stroke("a"), b = stroke("b", 40), c = stroke("c", 80);
  const h = empty();
  const first = [{ id: "g1", page: 1, before: drawing(a, b), after: drawing(b) }];
  recordEntry(h, first, { label: "ink erasure", pass: 5 });
  const g1now = first[0].after, g2before = drawing(c);
  const second = [{ id: "g1", page: 1, before: g1now, after: drawing() }, { id: "g2", page: 1, before: g2before, after: drawing() }];
  const { fold, held } = passFold(h, second, 5);
  assert.equal(fold, true);
  assert.equal(held(second[0]), h.undo[0].changes[0], "the entry's change of g1");
  assert.equal(held(second[1]), undefined, "g2 is not in it yet");
  foldInto(h.undo[0], second, held);
  assert.equal(h.undo.length, 1, "still one entry: one Ctrl+Z undoes the pass");
  const [g1, g2] = h.undo[0].changes;
  assert.deepEqual([ids(g1.before), ids(g1.after)], [["a", "b"], []], "g1: from before the pass to after its last flush");
  assert.equal(g2.before, g2before);
  assert.notEqual(g2, second[1], "a group joins as a copy of its change");
});

test("a group drawn otherwise than the entry's after, another pass or no pass starts a new entry", () => {
  const a = stroke("a"), b = stroke("b", 40);
  const h = empty();
  recordEntry(h, [{ id: "g1", page: 1, before: drawing(a, b), after: drawing(b) }], { label: "ink erasure", pass: 5 });
  const merged = drawing(b, stroke("theirs", 90));   // a save merged someone's stroke in meanwhile
  assert.equal(passFold(h, [{ id: "g1", page: 1, before: merged, after: drawing(stroke("theirs", 90)) }], 5).fold, false);
  const next = [{ id: "g1", page: 1, before: h.undo[0].changes[0].after, after: drawing() }];
  assert.equal(passFold(h, next, 6).fold, false, "another pass");
  assert.equal(passFold(h, next, null).fold, false, "no pass");
  recordEntry(h, next, { label: "ink stroke", pass: null });
  assert.equal(passFold(h, next, 5).fold, false, "the pass's entry is not the last one");
  assert.equal(passFold(empty(), next, 5).fold, false, "an empty history");
});

test("the pill says what was undone or redone, on which page, and how many strokes were kept", () => {
  const entry = { label: "ink move", changes: [] };
  assert.equal(undoMessage(entry, false, 0, 2), "Undone: ink move (page 2).");
  assert.equal(undoMessage(entry, true, 0, 2), "Redone: ink move (page 2).");
  assert.equal(undoMessage({ label: "partial ink erasure" }, false, 1, 3), "Undone: partial ink erasure (page 3; 1 stroke someone changed since was kept).");
  assert.equal(undoMessage(entry, false, 2, 1), "Undone: ink move (page 1; 2 strokes someone changed since were kept).");
  assert.equal(undoMessage(entry, false, 0, "?"), "Undone: ink move (page ?).", "a sheet no longer among the sheets");
});

test("the pill translates the label the history keeps in English", () => {
  setLocale("xx", { "Undone": "UNDONE", "Redone": "REDONE", "ink stroke": "INK STROKE" });
  try {
    assert.equal(undoMessage({ label: "ink stroke", changes: [] }, false, 0, 2), "UNDONE: INK STROKE (page 2).");
    assert.equal(undoMessage({ label: "ink stroke", changes: [] }, true, 0, 2), "REDONE: INK STROKE (page 2).");
  } finally {
    setLocale("en", {});
  }
});
