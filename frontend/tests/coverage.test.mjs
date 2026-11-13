import assert from "node:assert/strict";
import { test } from "node:test";
import {
  areaPicturesNote, canContinueAfterLimit, continueAfterLimitPrompt, coverageNote, formatSpans, limitNote, readSpans,
  shortenedNote, toolsOffNote, trimmedNote, truncatedNote, unseenSpans,
} from "../src/chat/coverage.js";

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
  // Spans past either end are clipped; overlapping ones merge.
  assert.deepEqual(unseenSpans(0, 10, [[-3, 2], [4, 6], [5, 7], [9, 40]]), [[3, 3], [8, 8]]);
});

test("a text file's read_page chips are read in lines, apart from a PDF's pages", () => {
  const actions = [
    { kind: "read", tool: "read_page", page_id: "f", lines: [401, 800] },
    { kind: "read", tool: "read_page", page_id: "f", lines: [1200, 1400] },
    { kind: "read", tool: "read_page", page_id: "f", lines: [790, 900] },
    { kind: "read", tool: "read_page", page_id: "f", pdf_pages: [3, 5] },
  ];
  assert.deepEqual(readSpans(actions, "f", "lines"), [[401, 900], [1200, 1400]]);
  assert.deepEqual(readSpans(actions, "f"), [[3, 5]]);
  assert.equal(formatSpans([[401, 900], [1200, 1400]], (n) => n.toLocaleString("en-US")), "401–900, 1,200–1,400");
  // A log's millions of lines are counted by their spans, not one by one.
  const started = performance.now();
  assert.deepEqual(unseenSpans(400, 30_000_000, [[401, 800]]), [[801, 30_000_000]]);
  assert.ok(performance.now() - started < 50);
});

test("a text file's context pill says which lines the model saw, read and missed", () => {
  const n = (v) => v.toLocaleString();
  const partial = { format: "text", title: "sections/method.tex", page_id: "f", doc_id: "h", native: false,
    native_requested: false, partial: true, chars: 20000, lines: 2310, lines_shown: 400, pages: 0, pages_shown: 0 };
  let note = coverageNote(partial, { actions: [], tools: false });
  assert.equal(note.short, `Model saw lines 1–400 of ${n(2310)}`);
  assert.match(note.long, new RegExp(`lines 1–400 of ${n(2310)} of “sections/method\\.tex” \\(about 5k tokens\\)`));
  assert.match(note.long, new RegExp(`Lines 401–${n(2310)} were not seen`));
  assert.match(note.long, /turn on Tools so it can read and search the whole file/);
  assert.equal(note.refused, false);
  // What the tools read on, in lines; a PDF chip on the page does not count.
  const reads = [{ kind: "read", page_id: "f", lines: [401, 800] }, { kind: "read", page_id: "f", pdf_pages: [1, 2] }];
  note = coverageNote(partial, { actions: reads, tools: true });
  assert.equal(note.short, `Model saw lines 1–400 of ${n(2310)} · read lines 401–800 with tools`);
  assert.match(note.long, /The assistant read lines 401–800 with its tools/);
  assert.match(note.long, new RegExp(`Lines 801–${n(2310)} were not seen`));
  assert.doesNotMatch(note.long, /turn on Tools|pages|PDF/);
  // A file that fit whole says nothing; a text file has no file to refuse.
  assert.equal(coverageNote({ ...partial, partial: false }), null);
  assert.equal(coverageNote({ ...partial, partial: false, native_requested: true }), null);
  // No line count known: the characters, as a PDF's entry falls back to.
  assert.equal(coverageNote({ ...partial, lines: 0, lines_shown: 0 }).short, `Model saw ${n(20000)} characters`);
  // An entry without a format, or a PDF's, keeps the PDF's words.
  assert.equal(coverageNote({ ...partial, format: "pdf", pages: 9, pages_shown: 2 }).short, "Model saw pages 1–2 of 9");
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
  // A file that never reached the provider doesn't blame it.
  const big = coverageNote({ ...partial, native_requested: true, native_skipped: "too_large" });
  assert.equal(big.short, "PDF too large to attach — text only, pages 1–2 of 9");
  assert.match(big.long, /too large to attach/);
  assert.doesNotMatch(big.long, /does not accept/);
  assert.equal(big.refused, true);
  assert.equal(coverageNote({ ...partial, partial: false, native_requested: true, native_skipped: "missing" }).short,
    "PDF file unavailable — sent as text");
  // A notes-only page that fit whole: nothing to say, PDF switch or not.
  assert.equal(coverageNote({ ...partial, doc_id: "", partial: false, native_requested: false }), null);
});

test("area highlights' pictures are announced", () => {
  assert.equal(areaPicturesNote({ title: "T" }), null);
  assert.equal(areaPicturesNote({ title: "T", area_pictures: 1 }).short, "Picture of 1 area highlight sent");
  assert.equal(areaPicturesNote({ title: "T", area_pictures: 3 }).short, "Pictures of 3 area highlights sent");
  assert.match(areaPicturesNote({ title: "T", area_pictures: 3 }).long, /notes of “T” include rectangle highlights/);
});

test("the per-reply pills: messages left out, a reply cut off", () => {
  assert.equal(trimmedNote(null), null);
  assert.equal(trimmedNote({ turns: 0 }), null);
  assert.equal(trimmedNote({ turns: 4 }).short, "Earlier messages left out: 4");
  assert.match(trimmedNote({ turns: 4 }).long, /4 oldest messages/);
  assert.equal(truncatedNote(false), null);
  assert.equal(truncatedNote(true).short, "Reply cut off at the output limit");
});

test("a reply that stopped short says why, and offers Continue only as the conversation's last word", () => {
  const path = "Settings / AI / Tool usage / Tool limits";
  assert.equal(limitNote(null), null);
  assert.equal(limitNote({ kind: "other" }), null);
  const rounds = limitNote({ kind: "rounds", rounds: 32, changes: 40 });
  assert.equal(rounds.short, "Stopped at the tool-round limit");
  assert.match(rounds.long, /used its 32 tool rounds/);
  assert.ok(rounds.long.includes(path));
  assert.match(limitNote({ kind: "rounds", rounds: 1 }).long, /its 1 tool round before/);
  const changes = limitNote({ kind: "changes", rounds: 3, changes: 110 });
  assert.equal(changes.short, "Stopped at the change limit");
  assert.match(changes.long, /change limit for one message after 110 changes/);
  const full = limitNote({ kind: "window", rounds: 5, changes: 0 });
  assert.equal(full.short, "Stopped: the conversation is full");
  assert.ok(full.long.includes("in short form") && !full.long.includes(path));
  // Unlimited was on, but this shared connection keeps the limits: the
  // pill says so instead of pointing at the switch that is already on.
  const withheld = limitNote({ kind: "rounds", rounds: 32, changes: 0, withheld: true });
  assert.ok(withheld.long.includes("does not apply on a shared AI connection") && !withheld.long.includes(path));
  assert.ok(truncatedNote(true).long.includes("Continue carries on"));

  const last = { limit: { kind: "rounds", rounds: 32 }, isLast: true, idle: true, readOnly: false };
  assert.equal(canContinueAfterLimit(last), true);
  assert.equal(canContinueAfterLimit({ truncated: true, isLast: true, idle: true }), true);
  for (const off of [{ isLast: false }, { idle: false }, { readOnly: true }, { limit: null }]) {
    assert.equal(canContinueAfterLimit({ ...last, ...off }), false, JSON.stringify(off));
  }
  assert.equal(continueAfterLimitPrompt(), "Please continue where you stopped.");
});

test("passages that went short say what was sent, and why", () => {
  const n = (v) => v.toLocaleString();
  assert.equal(shortenedNote(null), null);
  assert.equal(shortenedNote({ items: [], budget: 60_000, asked: 60_000 }), null);
  // One passage, cut by the budget the request asked for.
  const one = shortenedNote({ budget: 12_000, asked: 12_000,
    items: [{ kind: "pdf", n: 1, of: 1, kept: 12_000, total: 31_400 }] });
  assert.equal(one.short, `PDF passage shortened: first ${n(12_000)} of ${n(31_400)} characters sent`);
  assert.match(one.long, /Settings \/ AI \/ Chat \/ Context size/);
  assert.equal(one.refused, true);
  // Several, after the window fit shrank the budget: numbered, summed, the window to blame.
  const many = shortenedNote({ budget: 8_000, asked: 60_000, items: [
    { kind: "pdf", n: 2, of: 3, kept: 4_000, total: 9_000 },
    { kind: "note", n: 1, of: 1, kept: 2_000, total: 2_500 },
    { kind: "block", n: 1, of: 2, kept: 2_000, total: 7_000 }] });
  assert.equal(many.short, `3 passages shortened: ${n(8_000)} of ${n(18_500)} characters sent`);
  assert.match(many.long, new RegExp(`^PDF passage 2: first ${n(4_000)} of ${n(9_000)} characters\\. Selected note text: .* Attached block: `));
  assert.match(many.long, /model's window couldn't hold all of it/);
});

test("a reply that went without tools names its model and the reason", () => {
  assert.equal(toolsOffNote("", "gemma3"), null);
  assert.equal(toolsOffNote(undefined, "gemma3"), null);
  const note = toolsOffNote("upstream 404: No endpoints found that support tool use", "gemma3");
  assert.equal(note.short, "Answered without tools");
  assert.equal(note.refused, true);
  assert.ok(note.long.startsWith("gemma3 can't call tools on this connection (upstream 404: No endpoints found that support tool use)"));
  assert.ok(toolsOffNote("answered in text", "").long.startsWith("This model can't call tools"));
});
