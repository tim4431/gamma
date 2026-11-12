// The feedback view's words (src/feedback/feedbackText.js,
// docs/dev/feedback.md): an entry's heading as the Markdown form names it,
// how its place was found, its source line, and a Send's line and state.
import test from "node:test";
import assert from "node:assert/strict";
import { entryHeading, placedText, requestLine, sourceText } from "../src/feedback/feedbackText.js";

const entry = (kind, where = {}) => ({ id: "t1", file: "main.pdf", kind, where });

test("an entry's heading: a passage by its quote, a note under one, an area, handwriting, a note, the whole file", () => {
  assert.equal(entryHeading(entry("passage", { page: 3, quote: "the loss is defined as", placed: "exact" })),
    "Page 3 · “the loss is defined as”");
  assert.equal(entryHeading(entry("passage", { page: 3, quote: "the loss  is\ndefined as", note: "and here" })),
    "Page 3 · note on “the loss is defined as”", "a note under the passage, the quote on one line");
  const long = "word ".repeat(40).trim();
  assert.equal(entryHeading(entry("passage", { page: 1, quote: long })).length, "Page 1 · “”".length + 80, "a quote of 80 characters at most");
  assert.equal(entryHeading(entry("area", { page: 5, box: [0.1, 0.2, 0.5, 0.6] })), "Page 5 · area");
  assert.equal(entryHeading(entry("ink", { page: 2 })), "Page 2 · handwriting");
  assert.equal(entryHeading(entry("note", { note: "a thought" })), "A note");
  assert.equal(entryHeading(entry("file")), "The whole file");
  assert.equal(entryHeading({ id: "t2", kind: "passage" }), "A note", "no place on a page");
});

test("how the place was found: exactly, roughly, by estimate, not at all, not yet, on an earlier build", () => {
  assert.equal(placedText({ placed: "exact" }), "placed exactly");
  assert.equal(placedText({ placed: "rough", match: 0.874 }), "placed roughly (87%)");
  assert.equal(placedText({ placed: "rough" }), "placed roughly");
  assert.equal(placedText({ placed: "estimate" }), "placed by estimate");
  assert.equal(placedText({ placed: "lost" }), "not found in this build");
  assert.equal(placedText({ placed: "pending" }), "not placed yet");
  assert.equal(placedText({ placed: "earlier" }), "drawn on an earlier build");
  assert.equal(placedText({}), "", "a note, the whole file");
  assert.equal(placedText(undefined), "");
});

test("the source: the file's name and its line or lines, the line recorded at the comment's opening said so", () => {
  assert.equal(sourceText({ file: "sections/method.tex", line: 118, end: 120, version: "current" }), "method.tex:118–120");
  assert.equal(sourceText({ file: "main.tex", line: 18, version: "commented" }), "main.tex:18 (as commented)");
  assert.equal(sourceText({ file: "main.tex", line: 18, end: 18, version: "current" }), "main.tex:18", "one line");
  assert.equal(sourceText({ file: "main.tex" }), "main.tex");
  assert.equal(sourceText(null), "");
});

test("a Send's line: when and how many, then what became of it", () => {
  const now = Date.parse("2026-11-10T14:10:00Z");
  const sent = { id: "r1", at: "2026-11-10T14:05:00.000000Z", state: "sent", state_by: "", detail: "",
    threads: [{ id: "a", answered: false }, { id: "b", answered: false }, { id: "c", answered: true }] };
  assert.deepEqual(requestLine(sent, now), { line: "Sent 5m ago · 3 comments", state: "Waiting for the agent", kind: "sent" });
  assert.equal(requestLine({ ...sent, threads: [{ id: "a" }] }, now).line, "Sent 5m ago · 1 comment");
  assert.deepEqual(requestLine({ ...sent, state: "running", state_by: "Claude Code" }, now).state, "The agent is working");
  assert.deepEqual(requestLine({ ...sent, state: "running", detail: "rebuilding" }, now).state, "The agent is working: rebuilding");
  assert.deepEqual(requestLine({ ...sent, state: "done", state_by: "Claude Code" }, now),
    { line: "Sent 5m ago · 3 comments", state: "Done by Claude Code", kind: "done" });
  assert.equal(requestLine({ ...sent, state: "done" }, now).state, "Done");
  assert.deepEqual(requestLine({ ...sent, state: "failed", detail: "latexmk failed", state_by: "Claude Code" }, now),
    { line: "Sent 5m ago · 3 comments", state: "Failed: latexmk failed", kind: "failed" });
  assert.equal(requestLine({ ...sent, state: "failed" }, now).state, "Failed");
  assert.deepEqual(requestLine({ ...sent, state: "failed", detail: "withdrawn", state_by: "tim" }, now),
    { line: "Sent 5m ago · 3 comments", state: "Withdrawn", kind: "withdrawn" });
});
