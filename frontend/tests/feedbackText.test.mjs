// The feedback view's words (src/feedback/feedbackText.js,
// docs/dev/annotations.md): an entry's heading as the Markdown form names it,
// how its place was found, its source line, and a Send's line and state.
import test from "node:test";
import assert from "node:assert/strict";
import { entryHeading, placedText, requestLine, sendState, sendWords, sentChip, sourceText, withdrawTitle } from "../src/feedback/feedbackText.js";
import { isWithdrawn } from "../src/feedback/feedbackView.js";

const entry = (kind, where = {}) => ({ id: "t1", file: "main.pdf", kind, where });
// A thread an entry is (the Send chip's tests).
const page = () => [{ id: "t2", block_id: "b1", page_id: "page", resolved_at: null, comments: [] }];

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

test("a text file's range: its lines and quote, a note under one, one line alone", () => {
  const lines = (where) => ({ id: "t3", file: "main.tex", format: "text", kind: "lines", where });
  assert.equal(entryHeading(lines({ line: 118, end: 120, quote: "the loss is\ndefined as", placed: "exact" })),
    "Lines 118–120 · “the loss is defined as”");
  assert.equal(entryHeading(lines({ line: 17, quote: "This note measures how much it helps", placed: "exact" })),
    "Line 17 · “This note measures how much it helps”", "a range of one line has no end");
  assert.equal(entryHeading(lines({ line: 17, end: 17, quote: "x", note: "why" })), "Line 17 · note on “x”");
  assert.equal(entryHeading(lines({ line: 4, end: 6, quote: "x", note: "why" })), "Lines 4–6 · note on “x”");
  assert.equal(entryHeading(lines({ quote: "x" })), "A note", "no lines: no place");
});

test("how the place was found: exactly, roughly, by estimate, not at all, not yet, on an earlier version", () => {
  assert.equal(placedText({ placed: "exact" }), "placed exactly");
  assert.equal(placedText({ placed: "rough", match: 0.874 }), "placed roughly (87%)");
  assert.equal(placedText({ placed: "rough" }), "placed roughly");
  assert.equal(placedText({ placed: "estimate" }), "placed by estimate");
  assert.equal(placedText({ placed: "lost" }), "not found in the current version", "the Markdown's words, for every format");
  assert.equal(placedText({ placed: "pending" }), "not placed yet");
  assert.equal(placedText({ placed: "earlier" }), "drawn on an earlier version");
  assert.equal(placedText({}), "", "a note, the whole file");
  assert.equal(placedText(undefined), "");
});

test("a region's place: exactly, on an earlier version of the picture, or the file is no longer a picture", () => {
  assert.equal(placedText({ placed: "exact" }, "region"), "placed exactly");
  assert.equal(placedText({ placed: "earlier" }, "region"), "drawn on an earlier version of the picture");
  assert.equal(placedText({ placed: "lost" }, "region"), "the file is no longer a picture");
  assert.equal(placedText({}, "region"), "");
  assert.equal(placedText({ placed: "earlier" }, "ink"), "drawn on an earlier version", "the PDF's words for the other kinds");
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
  assert.deepEqual(requestLine(sent, now),
    { line, kind: "sent", label: "Waiting for the agent", detail: "", title: "Waiting for the agent", tone: "pending", live: false });
  assert.equal(requestLine({ ...sent, threads: [{ id: "a" }] }, now).line, "Sent 5m ago · 1 comment");
  assert.equal(requestLine({ ...sent, state: "running", state_by: "Claude Code" }, now).label, "Claude Code is working…", "named by state_by");
  // the agent's own words go on a line of their own: the state stays short
  assert.deepEqual(requestLine({ ...sent, state: "running", state_by: "Codex", detail: "Editing main.tex" }, now),
    { line, kind: "running", label: "Codex is working…", detail: "Editing main.tex", title: "Editing main.tex", tone: "pending", live: true });
  assert.equal(requestLine({ ...sent, state: "running", detail: "rebuilding" }, now).label, "The agent is working…");
  assert.deepEqual(requestLine({ ...sent, state: "done", state_by: "Codex", detail: "Fixed both. Changed main.tex." }, now),
    { line, kind: "done", label: "Done by Codex", detail: "Fixed both. Changed main.tex.", title: "Fixed both. Changed main.tex.", tone: "ok", live: false },
    "with its summary");
  assert.equal(requestLine({ ...sent, state: "done" }, now).label, "Done");
  assert.deepEqual(requestLine({ ...sent, state: "failed", detail: "latexmk failed", state_by: "Claude Code" }, now),
    { line, kind: "failed", label: "Failed", detail: "latexmk failed", title: "Failed: latexmk failed", tone: "failed", live: false });
  assert.equal(requestLine({ ...sent, state: "failed" }, now).detail, "");
  assert.equal(requestLine({ ...sent, state: "failed", detail: "withdrawn", state_by: "tim" }, now).label, "Withdrawn");
  // the agent that runs the reader's Sends, or the computer that keeps the folder
  assert.equal(requestLine(sent, now, { label: "Codex", computer: "" }).label, "Waiting for Codex");
  assert.equal(requestLine(sent, now, { label: "Codex", computer: "TIM-PC", online: true }).label, "Waiting for TIM-PC");
  assert.equal(requestLine({ ...sent, state: "running", state_by: "Codex" }, now, { label: "Codex", computer: "TIM-PC" }).label, "Codex is working…");
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
  assert.equal(many.notes[1].text, "Codex takes at most 100 comments at a time: send a file or a directory of this synced folder.");
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
    "To have Codex or Claude Code answer here, turn one on in Settings → AI.");
  assert.deepEqual(sendWords(plan("", { label: "" }), 2), { button: "Send 2 comments", notes: [], blocked: false });
});

test("the Send block of the computer that keeps the folder: named with that computer, waiting while it is offline", () => {
  const plan = (mode, extra = {}) => ({ mode, label: "Codex", problem: "", why: "", computer: "TIM-PC", online: true, ...extra });
  const ready = sendWords(plan("ready"), 2, 0);
  assert.equal(ready.button, "Send 2 comments to Codex");
  assert.deepEqual(ready.notes, [{ tone: "hint", text: "Codex works on TIM-PC, in this folder's directory there, and answers each comment." }]);
  const away = sendWords(plan("away", { online: false }), 2, 0);
  assert.equal(away.button, "Send 2 comments to Codex", "still named: it runs once TIM-PC connects");
  assert.deepEqual(away.notes, [{ tone: "hint", text: "TIM-PC is offline: this runs when it connects." }]);
  assert.equal(sendWords(plan("away", { online: false }), 101).blocked, true, "its limit holds");
  assert.deepEqual(sendWords(plan("not_ready", { problem: "Codex is not signed in on TIM-PC." }), 2).notes,
    [{ tone: "warn", text: "Codex is not signed in on TIM-PC. This Send fails until then." }]);
});

test("a Send's state, one wording wherever it shows: waiting, the agent at work, done, failed with its reason, withdrawn", () => {
  const send = (state, detail = "", state_by = "") => ({ request_id: "r1", state, at: "2026-11-10T13:59:00.000000Z", detail, state_by });
  assert.deepEqual(sendState(send("sent")),
    { kind: "sent", label: "Waiting for the agent", detail: "", title: "Waiting for the agent", tone: "pending", live: false });
  assert.deepEqual(sendState(send("running", "Editing sections/method.tex")),
    { kind: "running", label: "The agent is working…", detail: "Editing sections/method.tex", title: "Editing sections/method.tex", tone: "pending", live: true });
  assert.equal(sendState(send("running")).title, "The agent is working…");
  assert.deepEqual(sendState(send("running", "Editing main.tex", "Codex")),
    { kind: "running", label: "Codex is working…", detail: "Editing main.tex", title: "Editing main.tex", tone: "pending", live: true }, "named by state_by");
  assert.deepEqual(sendState(send("done", "Fixed both.", "Codex")),
    { kind: "done", label: "Done by Codex", detail: "Fixed both.", title: "Fixed both.", tone: "ok", live: false });
  assert.equal(sendState(send("done")).label, "Done");
  assert.deepEqual(sendState(send("failed", "the build failed")),
    { kind: "failed", label: "Failed", detail: "the build failed", title: "Failed: the build failed", tone: "failed", live: false });
  assert.equal(sendState(send("failed")).title, "Failed");
  assert.deepEqual(sendState(send("failed", "withdrawn", "tim")),
    { kind: "withdrawn", label: "Withdrawn", detail: "", title: "Withdrawn", tone: "", live: false });
});

test("the Send chip: a thread's Send in sendState's words; none without one, or withdrawn", () => {
  const on = (state, detail = "") => ({ ...page()[0], sent: { request_id: "r1", state, at: "2026-11-10T13:59:00.000000Z", detail } });
  assert.deepEqual(sentChip(on("running", "Editing main.tex")), sendState(on("running", "Editing main.tex").sent));
  assert.equal(sentChip(on("sent")).label, "Waiting for the agent");
  assert.ok(isWithdrawn(on("failed", "withdrawn").sent));
  assert.ok(!isWithdrawn(on("running", "withdrawn").sent));
  assert.equal(sentChip(on("failed", "withdrawn")), null, "the person took it back");
  assert.equal(sentChip({ ...page()[0], sent: null }), null);
  assert.equal(sentChip(page()[0]), null);
});

test("a waiting Send names the agent that answers the reader's Sends, or the computer that keeps the folder, offline in the title", () => {
  const on = (state) => ({ ...page()[0], sent: { request_id: "r1", state, at: "2026-11-10T13:59:00.000000Z", detail: "" } });
  const here = { name: "codex", label: "Codex", ready: true, problem: "" };
  assert.deepEqual(sentChip(on("sent"), here),
    { kind: "sent", label: "Waiting for Codex", detail: "", title: "Waiting for Codex", tone: "pending", live: false }, "this computer's own");
  const there = { ...here, computer: "TIM-PC", online: true };
  assert.deepEqual(sentChip(on("sent"), there),
    { kind: "sent", label: "Waiting for TIM-PC", detail: "", title: "Waiting for TIM-PC", tone: "pending", live: false });
  assert.deepEqual(sentChip(on("sent"), { ...there, ready: null, online: false }),
    { kind: "sent", label: "Waiting for TIM-PC", detail: "", title: "TIM-PC is offline: this runs when it connects.", tone: "pending", live: false });
  assert.equal(sentChip(on("running"), { ...there, online: false }).label, "The agent is working…", "taken: the computer is back");
});
