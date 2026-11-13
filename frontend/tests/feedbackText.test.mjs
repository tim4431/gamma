// The feedback view's words (src/feedback/feedbackText.js,
// docs/dev/feedback.md): an entry's heading as the Markdown form names it,
// how its place was found, its source line, and a Send's line and state.
import test from "node:test";
import assert from "node:assert/strict";
import { entryHeading, placedText, requestLine, sendWords, sourceText, withdrawTitle } from "../src/feedback/feedbackText.js";

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

test("a region's heading: its rect in the picture's pixels, a note under one, one with no frame", () => {
  assert.equal(entryHeading(entry("region", { rect: [40, 60, 360, 240], size: [1200, 800], placed: "exact" })),
    "Region · 320×180 px at (40, 60)");
  assert.equal(entryHeading(entry("region", { rect: [40, 60, 360, 240], note: "the legend" })),
    "Note on a region · 320×180 px at (40, 60)");
  assert.equal(entryHeading(entry("region", {})), "Region");
  assert.equal(entryHeading(entry("region", { note: "a thought" })), "Note on a region");
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

test("a region's place: exactly, on an earlier version of the picture, or the file is no longer a picture", () => {
  assert.equal(placedText({ placed: "exact" }, "region"), "placed exactly");
  assert.equal(placedText({ placed: "earlier" }, "region"), "drawn on an earlier version of the picture");
  assert.equal(placedText({ placed: "lost" }, "region"), "the file is no longer a picture");
  assert.equal(placedText({}, "region"), "");
  assert.equal(placedText({ placed: "earlier" }, "ink"), "drawn on an earlier build", "the PDF's words for the other kinds");
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
  const line = "Sent 5m ago · 3 comments";
  assert.deepEqual(requestLine(sent, now), { line, state: "Waiting for the agent", detail: "", kind: "sent" });
  assert.equal(requestLine({ ...sent, threads: [{ id: "a" }] }, now).line, "Sent 5m ago · 1 comment");
  assert.deepEqual(requestLine({ ...sent, state: "running", state_by: "Claude Code" }, now).state, "Claude Code is working", "named by state_by");
  // the agent's own words go on a line of their own: the state stays short
  assert.deepEqual(requestLine({ ...sent, state: "running", state_by: "Codex", detail: "Editing main.tex" }, now),
    { line, state: "Codex is working", detail: "Editing main.tex", kind: "running" });
  assert.deepEqual(requestLine({ ...sent, state: "running", detail: "rebuilding" }, now),
    { line, state: "The agent is working", detail: "rebuilding", kind: "running" });
  assert.deepEqual(requestLine({ ...sent, state: "done", state_by: "Claude Code" }, now),
    { line, state: "Done by Claude Code", detail: "", kind: "done" });
  assert.deepEqual(requestLine({ ...sent, state: "done", state_by: "Codex", detail: "Fixed both. Changed main.tex." }, now),
    { line, state: "Done by Codex", detail: "Fixed both. Changed main.tex.", kind: "done" }, "with its summary");
  assert.equal(requestLine({ ...sent, state: "done" }, now).state, "Done");
  assert.deepEqual(requestLine({ ...sent, state: "failed", detail: "latexmk failed", state_by: "Claude Code" }, now),
    { line, state: "Failed", detail: "latexmk failed", kind: "failed" });
  assert.equal(requestLine({ ...sent, state: "failed" }, now).detail, "");
  assert.deepEqual(requestLine({ ...sent, state: "failed", detail: "withdrawn", state_by: "tim" }, now),
    { line, state: "Withdrawn", detail: "", kind: "withdrawn" });
});

test("Withdraw's title says it stops the agent only where an agent on this computer holds the request", () => {
  assert.equal(withdrawTitle({ state: "running", state_by: "Codex", local: true }),
    "Stop Codex and withdraw: what it changed already stays in the files.");
  // an outside agent working through a token: nothing here stops it
  assert.equal(withdrawTitle({ state: "running", state_by: "Claude Code" }), "");
  assert.equal(withdrawTitle({ state: "running", state_by: "Claude Code", local: false }), "");
  assert.equal(withdrawTitle({ state: "running", state_by: "", local: true }), "");
  assert.equal(withdrawTitle({ state: "sent", state_by: "", local: true }), "");
  assert.equal(withdrawTitle(null), "");
});

test("the Send block: the folder's agent named, its limit, its problem, others' comments; else the generic button", () => {
  const plan = (mode, extra = {}) => ({ mode, label: "Codex", problem: "", why: "", ...extra });
  const ready = sendWords(plan("ready"), 2, 0);
  assert.equal(ready.button, "Send 2 comments to Codex");
  assert.deepEqual(ready.notes, [{ tone: "hint", text: "Codex works on this computer, in this folder's directory, and answers each comment." }]);
  assert.equal(ready.blocked, false);
  assert.equal(sendWords(plan("ready"), 1).button, "Send 1 comment to Codex");
  const others = sendWords(plan("ready"), 3, 1);
  assert.equal(others.notes[1].text, "1 of these comments is by someone else: Codex treats it as context.");
  assert.equal(sendWords(plan("ready"), 3, 2).notes[1].text, "2 of these comments are by someone else: Codex treats them as context.");
  const many = sendWords(plan("ready"), 101);
  assert.equal(many.blocked, true);
  assert.equal(many.notes[1].tone, "warn");
  assert.equal(many.notes[1].text, "Codex takes at most 100 comments at a time: send a file or a directory of this work folder.");
  assert.equal(sendWords(plan("ready"), 100).blocked, false, "100 is still one Send");
  const down = sendWords(plan("not_ready", { problem: "Codex is not signed in on this computer: run `codex login` in a terminal." }), 2);
  assert.equal(down.button, "Send 2 comments to Codex", "still named");
  assert.deepEqual(down.notes, [{ tone: "warn",
    text: "Codex is not signed in on this computer: run `codex login` in a terminal. This Send fails until then." }]);
  assert.deepEqual(sendWords(plan("checking"), 2).notes, [{ tone: "hint", text: "Checking Codex…" }]);
  const other = sendWords(plan("other", { why: "Only the person who keeps this folder on this computer can send to Codex." }), 2, 1);
  assert.equal(other.button, "Send 2 comments");
  assert.deepEqual(other.notes, [{ tone: "hint", text: "Only the person who keeps this folder on this computer can send to Codex." }]);
  assert.equal(other.blocked, false);
  assert.equal(sendWords(plan("other"), 200).blocked, false, "an outside agent has no limit here");
  assert.equal(sendWords(plan("offer", { label: "" }), 2).notes[0].text,
    "To have Codex or Claude Code answer here, turn one on in the sync panel: the sync button in the title bar, then ⋯ on this folder.");
  assert.deepEqual(sendWords(plan("", { label: "" }), 2), { button: "Send 2 comments", notes: [], blocked: false });
});
