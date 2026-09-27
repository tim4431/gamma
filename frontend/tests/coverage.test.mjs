import assert from "node:assert/strict";
import { test } from "node:test";
import { coverageNote, formatSpans, readSpans, trimmedNote, truncatedNote, unseenSpans } from "../src/chat/coverage.js";

const reads = [
  { kind: "read", tool: "read_page", page_id: "a", pdf_pages: [3, 5] },
  { kind: "read", tool: "read_page", page_id: "a", pdf_pages: [5, 6] },
  { kind: "read", tool: "read_page", page_id: "a", pdf_pages: [9, 9] },
  { kind: "read", tool: "read_page", page_id: "b", pdf_pages: [1, 2] },
  { kind: "read", tool: "read_block", page_id: "a" }, // no pages: a notes read
];

test("the pages a reply's tools read merge into spans per page", () => {
  assert.deepEqual(readSpans(reads, "a"), [[3, 6], [9, 9]]);
  assert.deepEqual(readSpans(reads, "b"), [[1, 2]]);
  assert.deepEqual(readSpans(reads, "zz"), []);
  assert.equal(formatSpans([[3, 6], [9, 9]]), "3–6, 9");
  assert.deepEqual(unseenSpans(2, 9, [[3, 6], [9, 9]]), [[7, 8]]);
  assert.deepEqual(unseenSpans(9, 9, []), []);
  assert.deepEqual(unseenSpans(2, 0, []), []); // total unknown: nothing to say
});

test("the context pill says what the model saw, read and missed", () => {
  const partial = { title: "Deterministic protocol for mapping a qubit", page_id: "a", doc_id: "d", native: false,
    native_requested: false, partial: true, chars: 20000, pages: 9, pages_shown: 2 };
  // Tools off: the old advice.
  let note = coverageNote(partial, { actions: [], tools: false });
  assert.equal(note.short, "Model saw pages 1–2 of 9");
  assert.match(note.long, /pages 1–2 of 9 of “Deterministic protocol for mapping a qubit” \(about 5k tokens\)/);
  assert.match(note.long, /turn on Tools/);
  assert.equal(note.refused, false);
  // Tools read on: what they read, what nobody saw, no "turn on Tools".
  note = coverageNote(partial, { actions: reads, tools: true });
  assert.equal(note.short, "Model saw pages 1–2 of 9 · read 3–6, 9 with tools");
  assert.match(note.long, /read pages 3–6, 9 with its tools/);
  assert.match(note.long, /Pages 7–8 were not seen/);
  assert.doesNotMatch(note.long, /turn on Tools/);
  // A selection placed on a page: named beside the head span.
  const selected = { ...partial, selection: { passages: [{ page: 7, section: "Results", found: true, crop: false }] } };
  assert.equal(coverageNote(selected).short, "Model saw pages 1–2 of 9 · around p. 7 · Results");
  assert.match(coverageNote(selected).long, /around your selection \(p\. 7 · Results\)/);
  // The selection-only shape (no head span) keeps its wording.
  const around = { ...partial, pages: 0, pages_shown: 0, selection: { passages: [{ page: 2, section: "Results" }] } };
  assert.equal(coverageNote(around).short, "Model saw text around p. 2 · Results");
  assert.match(coverageNote(around, { tools: true }).long, /Its tools can read and search the rest/);
  // Nothing to say for a paper that fit whole or a native attachment.
  assert.equal(coverageNote({ ...partial, partial: false }), null);
  assert.equal(coverageNote({ ...partial, partial: false, native: true, native_requested: true }), null);
  // A refused PDF file says so, with the span.
  const refused = coverageNote({ ...partial, native_requested: true });
  assert.equal(refused.short, "PDF file not accepted — text only, pages 1–2 of 9");
  assert.equal(refused.refused, true);
  assert.equal(coverageNote({ ...partial, partial: false, native_requested: true }).short, "PDF file not accepted — sent as text");
});

test("the per-reply pills: messages left out, a reply cut off", () => {
  assert.equal(trimmedNote(null), null);
  assert.equal(trimmedNote({ turns: 0 }), null);
  assert.equal(trimmedNote({ turns: 4 }).short, "Earlier messages left out: 4");
  assert.match(trimmedNote({ turns: 4 }).long, /4 oldest messages/);
  assert.equal(truncatedNote(false), null);
  assert.equal(truncatedNote(true).short, "Reply cut off at the output limit");
});
