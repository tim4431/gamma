// What the notes lock refuses on a page whose vault pushes everything and
// does not pull (src/vault/notesLock.js): a highlight's row may change itself, never a note.
import assert from "node:assert/strict";
import { test } from "node:test";
import { hopTarget, lockedRow, touchesLocked } from "../src/vault/notesLock.js";

const hl = (id, children = []) => ({ id, content: "", properties: { pdf_position: { page: 1, rects: [] } }, children });
const note = (id, children = []) => ({ id, content: id, properties: {}, children });

// h1, n1 (holding h2), h3, h4 (holding n2), h5
const tree = () => [hl("h1"), note("n1", [hl("h2")]), hl("h3"), hl("h4", [note("n2")]), hl("h5")];

test("a highlight's row is the only one the lock leaves open", () => {
  assert.equal(lockedRow(hl("h")), false);
  assert.equal(lockedRow(note("n")), true);
  assert.equal(lockedRow({ id: "ink", properties: { pdf_position: { page: 1 }, ink: { strokes: [] } }, children: [] }), false,
    "a block with a place on the PDF that no other kind claims reads as a highlight, as BlockTree reads it");
  assert.equal(lockedRow(null), false);
});

test("a highlight may go, move and nest only where no note is touched", () => {
  const t = tree();
  assert.equal(touchesLocked(t, "h1", "delete"), false, "a highlight that holds no note goes");
  assert.equal(touchesLocked(t, "h4", "delete"), true, "one that holds a note does not, nor are its children lifted");
  assert.equal(touchesLocked(t, "n1", "delete"), true);
  assert.equal(touchesLocked(t, "h3", "indent"), true, "under a note: the note would gain a child");
  assert.equal(touchesLocked(t, "h5", "indent"), false, "under a highlight, after the note it holds: the note stays as it was");
  assert.equal(touchesLocked([hl("a"), hl("b")], "b", "indent"), false, "under a highlight");
  assert.equal(touchesLocked(t, "h1", "indent"), false, "the first row stays where it is");
  assert.equal(touchesLocked(t, "h2", "outdent"), true, "out of a note: the note would lose a child");
  assert.equal(touchesLocked([hl("a", [hl("b")])], "b", "outdent"), false);
  assert.equal(touchesLocked(t, "h1", "move", 1), true, "swapped with a note");
  assert.equal(touchesLocked(t, "h3", "move", -1), true);
  assert.equal(touchesLocked([hl("a"), hl("b")], "a", "move", 1), false);
  assert.equal(touchesLocked(t, "gone", "delete"), true, "a row not in the tree");
});

test("the arrows hop over the locked rows to the next highlight, or nowhere", () => {
  const t = tree();
  assert.equal(hopTarget(t, "h1", 1, null).id, "h2", "past n1, into its child");
  assert.equal(hopTarget(t, "h2", -1, null).id, "h1", "past n1 going up");
  assert.equal(hopTarget(t, "h4", 1, null).id, "h5", "past n2");
  assert.equal(hopTarget(t, "h5", 1, null), null);
  assert.equal(hopTarget([note("a"), hl("b")], "b", -1, null), null, "only notes above");
});
