// The note source Gamma last copied (editor/sourceCopy.js): a paste of
// exactly it is the note's own text, which pasteMath leaves as it is.
import assert from "node:assert/strict";
import { test } from "node:test";
import { EditorSelection, EditorState } from "@codemirror/state";
import { copiedText, isOutlineCopy, isSourceCopy, sourceCopied } from "../src/editor/sourceCopy.js";

const state = (doc, ranges) => EditorState.create({
  doc, selection: EditorSelection.create(ranges), extensions: EditorState.allowMultipleSelections.of(true),
});

test("the text a copy takes is CodeMirror's: the selections, else the carets' lines", () => {
  const doc = "one \\[ x \\]\ntwo\nthree";
  assert.equal(copiedText(state(doc, [EditorSelection.range(4, 11)])), "\\[ x \\]");
  assert.equal(copiedText(state(doc, [EditorSelection.range(0, 3), EditorSelection.range(12, 15)])), "one\ntwo");
  assert.equal(copiedText(state(doc, [EditorSelection.cursor(2), EditorSelection.cursor(5), EditorSelection.cursor(13)])),
    "one \\[ x \\]\ntwo", "a line once, however many carets are on it");
});

test("a paste is the note's own source when it is the text last copied", () => {
  sourceCopied("a \\[ x \\]\r\nb");
  assert.ok(isSourceCopy("a \\[ x \\]\nb"), "line breaks as a paste reads them");
  assert.ok(!isSourceCopy("a \\[ x \\]"), "a part of it is other text");
});

test("only a subtree's copy is an outline: one block's own list is list source", () => {
  sourceCopied("- a\n- b");
  assert.ok(isSourceCopy("- a\n- b"));
  assert.ok(!isOutlineCopy("- a\n- b"), "a copy out of one block");
  sourceCopied("- a\r\n  - - b\n", { outline: true });
  assert.ok(isOutlineCopy("- a\n  - - b\n"), "line breaks as a paste reads them");
  assert.ok(!isOutlineCopy("- a\n  - - b"), "the same text trimmed is not the clip");
  sourceCopied("- a\n  - - b\n");
  assert.ok(!isOutlineCopy("- a\n  - - b\n"), "a later copy of the same text says for itself");
});
