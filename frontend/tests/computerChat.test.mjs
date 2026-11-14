// The chat's computer side, its words and requests, and its conversation in
// the transcript's shape (src/chat/computer/computerChat.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CHANGES_SHOWN, NOTE_MAX, SOURCES_MAX, answerBody, askCommand, askPath, askPlace, askRisk, askTitle, awayFrom, canContinue, canSend, chatPath,
  currentStep, diffRows, drawsAsks, emptyLine, endText, freshLine, isLive, isRemote, lastEnded, liveTurn, markParts, modelChoice, modelDisplay,
  offersMenu, retryOf, revealsFile, scrolledToEnd, sourceNote, sourcesBody, stepNow, stepText, stripLine, turnBody, turnChanges, workPlace,
  turnLabel, waitsThere, historyRows, shownMessages, stepChip,
} from "../src/chat/computer/computerChat.js";
import { isChange, splitActions } from "../src/chat/agentSteps.js";
import { failureCopy } from "../src/chat/chatErrors.js";
import { keeperAgent, stripFacts } from "../src/chat/agents.js";

const agent = (over = {}) => ({
  id: "m2", role: "agent", agent: "codex", label: "Codex", turn_id: "t1", state: "done", text: "Fixed.", steps: [],
  changes: [], changed: 0, built: false, error: "", fresh: false, ...over,
});
const person = { id: "m1", role: "user", text: "fix this", selections: [] };
const ready = { agent: "codex", label: "Codex", ready: true, problem: "", folder: "Thesis", paused: false, turn: null };

test("the read asks for the bucket, with the version held once there is one and the agent picked", () => {
  assert.equal(chatPath("p 1"), "/local-agents/chat?bucket=p+1");
  assert.equal(chatPath("p1", 0), "/local-agents/chat?bucket=p1&version=0");
  assert.equal(chatPath("p1", 7), "/local-agents/chat?bucket=p1&version=7");
  assert.equal(chatPath("home", null, "claude"), "/local-agents/chat?bucket=home&agent=claude");
  assert.equal(chatPath("f1", 3, "codex"), "/local-agents/chat?bucket=f1&version=3&agent=codex");
});

test("a message posts its id, the bucket, the text trimmed and the passages with their place", () => {
  assert.deepEqual(turnBody("t1", "p1", "  fix this \n", [{ text: "a passage", page: 3, box: [0.1, 0.2, 0.3, 0.4] }, { text: "" }, { text: "b" }]), {
    id: "t1", bucket: "p1", text: "fix this",
    selections: [{ text: "a passage", page: 3, box: [0.1, 0.2, 0.3, 0.4] }, { text: "b", page: null, box: null }],
  });
  assert.deepEqual(turnBody("t1", "p1", "x").selections, []);
});

test("a turn still to end is the conversation's last agent message, queued or running", () => {
  assert.equal(isLive(agent({ state: "queued" })), true);
  assert.equal(isLive(agent({ state: "running" })), true);
  for (const state of ["done", "failed", "stopped"]) assert.equal(isLive(agent({ state })), false, state);
  assert.equal(isLive(person), false);
  const running = agent({ id: "m4", state: "running" });
  assert.equal(liveTurn({ messages: [person, agent(), { ...person, id: "m3" }, running] }), running);
  assert.equal(liveTurn({ messages: [person, agent()] }), null);
  assert.equal(liveTurn(null), null);
});

test("a step in words, done and while it runs", () => {
  assert.equal(stepText({ kind: "read", path: "sections/intro.tex" }), "Read sections/intro.tex");
  assert.equal(stepText({ kind: "edit", path: "main.tex" }), "Edited main.tex");
  assert.equal(stepText({ kind: "edit", path: "outside" }), "Edited a file outside the folder");
  assert.equal(stepText({ kind: "read" }), "Read a file");
  assert.equal(stepText({ kind: "run", program: "latexmk" }), "Ran latexmk");
  assert.equal(stepText({ kind: "run" }), "Ran a command");
  assert.equal(stepText({ kind: "search" }), "Searched the sources");
  assert.equal(stepText({ kind: "other" }), "Worked");
  assert.equal(stepText({ kind: "gamma", tool: "read_page" }), "Read Gamma: read_page", "a call of Gamma's tools, by its name");
  assert.equal(stepText({ kind: "gamma", tool: "" }), "Read Gamma");
  assert.equal(stepText({ kind: "gamma", tool: "comment" }), "Wrote in Gamma: comment", "a writing tool's call, as a write");
  assert.equal(stepNow({ kind: "gamma", tool: "highlight" }), "Writing in Gamma: highlight");
  assert.equal(stepNow({ kind: "edit", path: "main.tex" }), "Editing main.tex");
  assert.equal(stepNow({ kind: "run", program: "latexmk" }), "Running latexmk");
  assert.equal(stepNow({ kind: "search" }), "Searching the sources");
  assert.equal(stepNow({ kind: "gamma", tool: "search_library" }), "Reading Gamma: search_library");
  assert.equal(stepNow({ kind: "other" }), "", "nothing worth naming");
  assert.equal(stepNow(null), "");
});

test("a turn's pill: the agent at work, then how many steps it took", () => {
  const steps = [{ id: "s1", kind: "read", path: "a.tex", status: "completed" }, { id: "s2", kind: "edit", path: "b.tex", status: "started" },
    { id: "s3", kind: "search", status: "completed" }];
  assert.equal(currentStep({ steps }).id, "s2", "the newest still started");
  assert.equal(currentStep({ steps: steps.slice(0, 1) }).id, "s1");
  assert.equal(turnLabel(agent({ state: "running", steps }), "Codex"), "Codex is working: Editing b.tex");
  assert.equal(turnLabel(agent({ state: "queued" }), "Codex"), "Codex is working…");
  assert.equal(turnLabel(agent({ state: "running", label: "" }), "Claude Code"), "Claude Code is working…");
  assert.equal(turnLabel(agent({ steps }), "Codex"), "3 steps");
  assert.equal(turnLabel(agent({ steps: steps.slice(0, 1) }), "Codex"), "1 step");
});

test("what a turn changed, as its card lists it", () => {
  assert.equal(turnChanges(agent()), null);
  assert.deepEqual(turnChanges(agent({ changes: ["sections/intro.tex"], changed: 1 })),
    { shown: ["sections/intro.tex"], rest: [], more: 0, n: 1, built: false });
  const five = ["a.tex", "b.tex", "c.tex", "d.tex", "e.tex"];
  assert.deepEqual(turnChanges(agent({ changes: five, changed: 5, built: true })),
    { shown: five.slice(0, CHANGES_SHOWN), rest: five.slice(CHANGES_SHOWN), more: 2, n: 5, built: true });
  assert.deepEqual(turnChanges(agent({ changes: ["a.tex"], changed: 5 })),
    { shown: ["a.tex"], rest: [], more: 4, n: 5, built: false }, "the count names what is not listed");
  assert.deepEqual(turnChanges(agent({ changed: 3 })), { shown: [], rest: [], more: 3, n: 3, built: false });
  assert.deepEqual(turnChanges(agent({ built: true })), { shown: [], rest: [], more: 0, n: 0, built: true });
});

test("Retry sends the newest failed turn's message again", () => {
  const failed = agent({ state: "failed", error: "Codex stopped." });
  const asked = { ...person, selections: [{ text: "a passage", page: 2 }] };
  assert.deepEqual(retryOf({ messages: [asked, failed] }, failed), { text: "fix this", selections: [{ text: "a passage", page: 2 }] });
  assert.equal(retryOf({ messages: [asked, failed, person, agent()] }, failed), null, "only the newest");
  const done = agent();
  assert.equal(retryOf({ messages: [person, done] }, done), null, "only a failed one");
});

test("how a turn ended when it did not answer, and a session not resumed", () => {
  assert.equal(endText(agent({ state: "failed", error: "Codex is no longer this folder's agent." })), "Codex is no longer this folder's agent.");
  assert.equal(endText(agent({ state: "failed" })), "Failed");
  assert.equal(endText(agent({ state: "stopped" })), "Stopped");
  assert.equal(endText(agent()), "");
  assert.match(freshLine("Codex"), /^Codex started a new session/);
});

test("the strip: the agent and the folder, what it is at, what it waits for, why it cannot run", () => {
  assert.deepEqual(stripLine(ready), { text: "Codex on this computer · Thesis", kind: "ready" });
  assert.deepEqual(stripLine({ ...ready, folder: "" }), { text: "Codex on this computer", kind: "ready" });
  assert.equal(stripLine({ ...ready, ready: null }).text, "Checking Codex…");
  const running = agent({ state: "running", steps: [{ kind: "edit", path: "sections/intro.tex", status: "started" }] });
  assert.deepEqual(stripLine({ ...ready, turn: { turn_id: "t1", state: "running", ahead: false } }, running),
    { text: "Codex is working: Editing sections/intro.tex", kind: "working" });
  assert.equal(stripLine({ ...ready, turn: { turn_id: "t1", state: "running", ahead: false } }).text, "Codex is working…");
  assert.deepEqual(stripLine({ ...ready, turn: { turn_id: "t1", state: "queued", ahead: true } }),
    { text: "Waiting for Codex to finish its other work in this folder", kind: "waiting" });
  assert.equal(stripLine({ ...ready, turn: { turn_id: "t1", state: "queued", ahead: false } }).text, "Starting Codex…");
  assert.equal(stripLine({ ...ready, turn: null }, running).kind, "working", "the conversation's own turn, before the server's read says it");
  assert.deepEqual(stripLine({ ...ready, ready: false, problem: "Codex is not signed in on this computer." }),
    { text: "Codex is not signed in on this computer.", kind: "problem" });
  assert.deepEqual(stripLine({ ...ready, paused: true, turn: { state: "queued", ahead: true } }),
    { text: "The synced folder is paused on this computer: Codex starts once it syncs again.", kind: "paused" });
  // The chat's list's word for the agent (chat/agents.js stripFacts) until the side's read lands.
  assert.equal(stripLine(stripFacts({ kind: "computer", name: "codex", label: "Codex", ready: true, problem: "", models: [] })).text,
    "Codex on this computer");
});

test("what the agent asks: the card's title, where a command runs, what allowing it means", () => {
  const command = { id: "a1", kind: "command", command: "latexmk -pdf main.tex", cwd: "", reason: "Build the PDF", at: "" };
  const files = { id: "a2", kind: "files", paths: ["outside"], diff: "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n same\n", at: "" };
  assert.equal(askTitle(command, "Codex"), "Codex wants to run a command");
  assert.equal(askTitle(command, "Claude Code"), "Claude Code wants to run a command");
  assert.equal(askTitle(files, "Codex"), "Codex wants to change files outside its sandbox");
  assert.equal(askTitle({ kind: "tool" }, "Codex"), "Codex wants to use a tool");
  assert.equal(askPlace(""), "In the directory it works in");
  assert.equal(askPlace("."), "In the directory it works in");
  assert.equal(askPlace("sections"), "In sections");
  assert.equal(askPlace("outside"), "In a folder outside the directory it works in");
  assert.equal(askPlace("~"), "In your home folder");
  assert.equal(askRisk(command), "It runs as you, with your files and the network.");
  assert.equal(askRisk(files), "It changes files outside the directory it works in.");
  assert.equal(askRisk({ kind: "tool" }), "");
  assert.deepEqual(diffRows(files.diff), [
    { kind: "head", text: "--- a/x" }, { kind: "head", text: "+++ b/x" }, { kind: "head", text: "@@ -1 +1 @@" },
    { kind: "del", text: "-old" }, { kind: "ins", text: "+new" }, { kind: "same", text: " same" },
  ]);
  assert.deepEqual(diffRows(""), []);
  assert.deepEqual(diffRows(undefined), []);
});

test("an asked command as its card draws it: the wrapper's command first, its lines, what would not show marked", () => {
  const plain = { kind: "command", command: "latexmk -pdf main.tex" };
  assert.deepEqual(askCommand(plain), { main: "latexmk -pdf main.tex", through: "", lines: 0 });
  const wrapped = { kind: "command", command: "pwsh.exe -Command 'latexmk -pdf main.tex'", inner: "latexmk -pdf main.tex", shell: "PowerShell" };
  assert.deepEqual(askCommand(wrapped), { main: "latexmk -pdf main.tex", through: "Through PowerShell: pwsh.exe -Command 'latexmk -pdf main.tex'", lines: 0 });
  assert.equal(askCommand({ ...wrapped, shell: "" }).main, wrapped.command, "no shell named: the whole command");
  assert.equal(askCommand({ command: "a\nb\nc", lines: 3 }).lines, 3);
  assert.equal(askCommand({ command: "a", lines: 1 }).lines, 0, "one line is not counted");
  const marked = { kind: "command", command: "echo \u202egnp.exe", command_shown: "echo ⟨U+202E⟩gnp.exe", marked: 1 };
  assert.equal(askCommand(marked).main, "echo ⟨U+202E⟩gnp.exe", "the server's shown form");
  assert.deepEqual(markParts(askCommand(marked).main, marked.marked),
    [{ text: "echo ", mark: false }, { text: "⟨U+202E⟩", mark: true }, { text: "gnp.exe", mark: false }]);
  assert.deepEqual(markParts("say ⟨U+202E⟩", 0), [{ text: "say ⟨U+202E⟩", mark: false }], "an ask that holds none: the agent's own letters");
  assert.deepEqual(askCommand(null), { main: "", through: "", lines: 0 });
});

test("Allow waits until a box was scrolled to its end, or had nothing to scroll", () => {
  assert.equal(scrolledToEnd(null), true, "no box");
  assert.equal(scrolledToEnd({ scrollTop: 0, clientHeight: 100, scrollHeight: 100 }), true, "nothing to scroll");
  assert.equal(scrolledToEnd({ scrollTop: 0, clientHeight: 100, scrollHeight: 400 }), false);
  assert.equal(scrolledToEnd({ scrollTop: 299, clientHeight: 100, scrollHeight: 400 }), true, "within 2 px of its end");
});

test("an answer posts the decision, and a decline's note when one was written", () => {
  assert.equal(askPath("t 1", "a/1"), "/local-agents/chat/turns/t%201/asks/a%2F1");
  assert.deepEqual(answerBody("once"), { decision: "once" });
  assert.deepEqual(answerBody("chat", "ignored"), { decision: "chat" });
  assert.deepEqual(answerBody("deny", "  use the cached copy "), { decision: "deny", note: "use the cached copy" });
  assert.deepEqual(answerBody("deny", "   "), { decision: "deny" });
  assert.equal(answerBody("deny", "x".repeat(NOTE_MAX + 10)).note.length, NOTE_MAX);
});

test("a turn that asks: its pill and the strip wait for the person, its steps say what was asked", () => {
  const asks = [{ id: "a1", kind: "command", command: "latexmk", cwd: "", at: "" }];
  const asking = agent({ state: "running", asks, steps: [{ kind: "run", program: "latexmk", status: "asked" }] });
  assert.equal(turnLabel(asking, "Codex"), "Codex is waiting for you");
  assert.deepEqual(stripLine({ ...ready, turn: { turn_id: "t1", state: "running", ahead: false } }, asking),
    { text: "Waiting for you: Codex wants to run a command", kind: "asking" });
  assert.equal(stripLine({ ...ready, paused: true }, asking).kind, "paused");
  assert.equal(stripLine(ready, agent({ state: "running", asks: [] })).kind, "working", "no ask waiting");
  assert.equal(turnLabel(agent({ asks }), "Codex"), "0 steps", "an ended turn's asks wait for nothing");
  assert.equal(stepText({ kind: "run", program: "latexmk", status: "asked" }), "Asked to run latexmk");
  assert.equal(stepText({ kind: "run", program: "latexmk", status: "allowed" }), "Allowed to run latexmk");
  assert.equal(stepText({ kind: "run", status: "declined" }), "Not allowed to run a command");
  assert.equal(stepText({ kind: "edit", path: "outside", status: "asked" }), "Asked to edit a file outside the folder");
  assert.equal(stepText({ kind: "edit", path: "outside", status: "declined" }), "Not allowed to edit a file outside the folder");
  assert.equal(stepText({ kind: "other", status: "asked" }), "Asked to go further");
  assert.equal(stepText({ kind: "run", program: "latexmk", status: "completed" }), "Ran latexmk");
});

test("a message goes with text, no turn still to end, and an agent able to run", () => {
  const conversation = { messages: [person, agent()] };
  assert.equal(canSend(ready, conversation, "next"), true);
  assert.equal(canSend(ready, null, "first"), true);
  assert.equal(canSend(ready, conversation, "  "), false);
  assert.equal(canSend(ready, { messages: [person, agent({ state: "running" })] }, "next"), false);
  assert.equal(canSend({ ...ready, turn: { state: "queued" } }, conversation, "next"), false);
  assert.equal(canSend({ ...ready, paused: true }, conversation, "next"), false);
  assert.equal(canSend({ ...ready, ready: false }, conversation, "next"), false);
  assert.equal(canSend({ ...ready, ready: null }, conversation, "next"), true, "still checking: the server decides");
});

test("a message carries the model and effort chosen, neither when it is the agent's default", () => {
  assert.deepEqual(turnBody("t1", "p1", "x", [], { model: "gpt-5.5", effort: "high" }),
    { id: "t1", bucket: "p1", text: "x", selections: [], model: "gpt-5.5", effort: "high" });
  assert.deepEqual(turnBody("t1", "p1", "x", [], { model: "", effort: "low" }), { id: "t1", bucket: "p1", text: "x", selections: [], effort: "low" });
  assert.deepEqual(turnBody("t1", "p1", "x", [], { model: "", effort: "" }), { id: "t1", bucket: "p1", text: "x", selections: [] });
  // the agent picked, and where a conversation that begins works ("" one of its own; null: the conversation's)
  assert.deepEqual(turnBody("t1", "home", "x", [], { agent: "claude", dir: "d1" }),
    { id: "t1", bucket: "home", text: "x", selections: [], agent: "claude", dir: "d1" });
  assert.equal(turnBody("t1", "home", "x", [], { agent: "codex", dir: "" }).dir, "");
  assert.equal("dir" in turnBody("t1", "home", "x", [], { agent: "codex", dir: null }), false);
});

test("where the agent works: a menu of the offered directories until the first turn, then the one it works in", () => {
  const dirs = [{ id: "d1", path: "D:\\scratch", name: "Scratch" }, { id: "d2", path: "/tmp/b", name: "" }];
  const own = { ...ready, folder: "", dir: null };
  assert.deepEqual(workPlace(own, null, dirs), { pick: true, value: "", options: [["", "No folder"], ["d1", "Scratch"], ["d2", "/tmp/b"]] });
  assert.equal(workPlace(own, null, dirs, "d2").value, "d2");
  assert.equal(workPlace(own, null, dirs, "gone").value, "", "a directory no longer offered: none");
  assert.deepEqual(workPlace(own, null, []).options, [["", "No folder"]]);
  const said = { messages: [person, agent()] };
  assert.deepEqual(workPlace(own, said, dirs), { pick: false, text: "No folder" });
  assert.deepEqual(workPlace({ ...own, dir: { kind: "offered", id: "d1", name: "Scratch" } }, said, dirs), { pick: false, text: "Scratch" });
  assert.equal(workPlace({ ...ready, dir: { kind: "vault", name: "Thesis" } }, null, dirs), null, "a vault's: named in the strip's line");
  assert.equal(workPlace(remote, null, dirs), null, "another computer's");
  assert.equal(workPlace(null, null, dirs), null);
  // the empty transcript says where it works
  assert.equal(emptyLine({ ...ready, dir: { kind: "vault", name: "Thesis" } }, "Codex"),
    "Ask Codex about this file or to change it: it works in the synced folder's directory on this computer.");
  assert.equal(emptyLine({ ...ready, dir: { kind: "vault", name: "Thesis" } }, "Codex", "", null, false),
    "Ask Codex about this folder or its files, or to change them: it works in the synced folder's directory on this computer.", "a folder of it");
  assert.equal(emptyLine(own, "Codex", "", workPlace(own, null, dirs)), "Ask Codex anything: it works in an empty directory of its own on this computer.");
  assert.equal(emptyLine(own, "Codex", "", workPlace(own, null, dirs, "d1")), "Ask Codex anything: it works in the directory you chose on this computer.");
  // Show in folder: a page of a work folder kept here
  assert.equal(revealsFile({ ...ready, dir: { kind: "vault", name: "Thesis" } }, true), true);
  assert.equal(revealsFile({ ...ready, dir: { kind: "vault", name: "Thesis" } }, false), false, "a folder of it");
  assert.equal(revealsFile(own, true), false);
  assert.equal(revealsFile(remote, true), false);
});

test("the model chip: the person's pick, else what the conversation ran with, else the agent's default", () => {
  const models = [
    { id: "gpt-5.5", label: "GPT-5.5", efforts: ["low", "medium", "high"], effort: "medium", default: true },
    { id: "gpt-5.5-mini", label: "GPT-5.5 mini", efforts: [], effort: "", default: false },
  ];
  const none = modelChoice(models);
  assert.deepEqual({ model: none.model, effort: none.effort, shown: none.shown.id }, { model: "", effort: "", shown: "gpt-5.5" });
  assert.equal(modelDisplay(none), "GPT-5.5");
  const ran = { messages: [person, agent({ model: "gpt-5.5-mini", effort: "" }), agent({ id: "m4", model: "gpt-5.5", effort: "high" })] };
  const kept = modelChoice(models, ran);
  assert.deepEqual([kept.model, kept.effort], ["gpt-5.5", "high"], "the newest turn's");
  assert.equal(modelDisplay(kept), "GPT-5.5 · high");
  // a turn run with the defaults says so (null, null); one from before turns kept them says nothing
  const back = modelChoice(models, { messages: [...ran.messages, agent({ id: "m6", model: null, effort: null })] });
  assert.deepEqual([back.model, back.effort], ["", ""], "the defaults, not an older turn's effort");
  assert.equal(modelChoice(models, { messages: [...ran.messages, agent({ id: "m8" })] }).effort, "high");
  assert.equal(lastEnded(ran), null);
  assert.equal(lastEnded({ messages: [agent({ ended_at: "2026-11-13T10:00:00Z" }), agent({ id: "m9", ended_at: null })] }),
    "2026-11-13T10:00:00Z", "the newest ended turn's");
  const picked = modelChoice(models, ran, { model: "gpt-5.5-mini", effort: "high" });
  assert.deepEqual([picked.model, picked.effort, picked.shown.id], ["gpt-5.5-mini", "", "gpt-5.5-mini"], "an effort the model lacks goes");
  const gone = modelChoice(models, null, { model: "o9", effort: "low" });
  assert.deepEqual([gone.model, gone.effort, gone.shown.id], ["", "low", "gpt-5.5"], "a model no longer listed is the default");
  assert.deepEqual(modelChoice([], ran), { model: "", effort: "", shown: null });
  assert.equal(modelDisplay(modelChoice([])), "");
  const first = modelChoice([{ id: "sonnet", label: "", efforts: [] }]);
  assert.equal(modelDisplay(first), "sonnet", "no default: the first, by its id when it has no name");
});

test("a passage chip asks for its source line and notes it", () => {
  const many = Array.from({ length: SOURCES_MAX + 3 }, (_, i) => ({ text: `q${i}`, page: 1, box: [0, 0, 1, 1] }));
  const body = sourcesBody("p1", [{ text: "a", page: 2, box: [0.1, 0.2, 0.3, 0.4], extra: 1 }, { text: "" }, ...many]);
  assert.equal(body.bucket, "p1");
  assert.equal(body.selections.length, SOURCES_MAX);
  assert.deepEqual(body.selections[0], { text: "a", page: 2, box: [0.1, 0.2, 0.3, 0.4] });
  assert.equal(sourceNote({ file: "sections/method.tex", line: 3, end: 5 }), "sections/method.tex:3");
  assert.equal(sourceNote({ file: "main.tex" }), "main.tex");
  assert.equal(sourceNote(null), "");
});

test("the strip's menu offers the terminal when the server says there is a session and no turn is to end", () => {
  const sessioned = { ...ready, session: true };
  assert.equal(canContinue(sessioned, { messages: [person, agent()] }), true);
  assert.equal(canContinue(sessioned, { messages: [person, agent({ state: "failed" })] }), true);
  assert.equal(canContinue(ready, { messages: [person, agent()] }), false, "no session the server knows of");
  assert.equal(canContinue({ ...ready, session: false }, { messages: [person, agent()] }), false);
  assert.equal(canContinue(sessioned, { messages: [person, agent(), person, agent({ id: "m4", state: "running" })] }), false);
  assert.equal(canContinue({ ...sessioned, turn: { state: "queued" } }, { messages: [person, agent()] }), false);
  assert.equal(canContinue(null, { messages: [person, agent()] }), false);
});

// Another server's work folder (step 4d): the server's `computer` for a seat the computer that keeps it runs.
const remote = { ...ready, folder: "paper", session: false, computer: "TIM-PC", online: true, since: null, remote: true };
const offline = (since) => ({ ...remote, ready: null, online: false, since });

test("another computer's agent: the strip names that computer, and says when it is away and a message waits", () => {
  const now = new Date(2026, 10, 13, 18, 0);
  const since = new Date(2026, 10, 13, 14, 2).toISOString();
  assert.deepEqual(stripLine(remote), { text: "Codex on TIM-PC · paper", kind: "ready" });
  assert.equal(stripLine({ ...remote, folder: "" }).text, "Codex on TIM-PC");
  assert.match(stripLine(offline(since), null, now).text, /^TIM-PC is offline since 2:02.*: a message waits and runs when it connects$/);
  assert.equal(stripLine(offline(since), null, now).kind, "away");
  assert.equal(stripLine(offline(null), null, now).text, "TIM-PC is offline: a message waits and runs when it connects", "no time known");
  const queued = agent({ state: "queued", text: "" });
  assert.match(stripLine({ ...offline(since), turn: { turn_id: "t1", state: "queued", ahead: false } }, queued, now).text,
    /^Waiting for TIM-PC, offline since 2:02.*: the message runs when it connects$/);
  assert.equal(stripLine(offline(null), queued, now).text, "Waiting for TIM-PC, which is offline: the message runs when it connects",
    "the conversation's own turn, before the server's read says it");
  assert.equal(stripLine({ ...remote, turn: { turn_id: "t1", state: "queued", ahead: false } }).text, "Starting Codex…", "connected: it starts");
  const running = agent({ state: "running", steps: [{ kind: "edit", path: "main.tex", status: "started" }] });
  assert.equal(stripLine({ ...remote, turn: { turn_id: "t1", state: "running", ahead: false } }, running).text, "Codex is working: Editing main.tex");
  assert.deepEqual(stripLine({ ...remote, paused: true }),
    { text: "The synced folder is paused on TIM-PC: Codex starts once it syncs again.", kind: "paused" });
  assert.equal(stripLine({ ...remote, ready: false, problem: "Codex is not signed in on TIM-PC." }).text, "Codex is not signed in on TIM-PC.");
  assert.equal(stripLine({ ...remote, ready: null }).text, "Checking Codex…");
});

test("another computer's agent: a message goes while it is away, and its turn waits for it", () => {
  const conversation = { messages: [person, agent()] };
  assert.equal(canSend(offline(null), conversation, "next"), true);
  assert.equal(canSend({ ...offline(null), ready: false }, conversation, "next"), true, "away: what it last said does not hold the message");
  assert.equal(canSend({ ...remote, ready: false }, conversation, "next"), false, "connected and unable to run");
  assert.equal(canSend({ ...offline(null), paused: true }, conversation, "next"), false);
  assert.equal(canSend({ ...offline(null), turn: { state: "queued" } }, conversation, "next"), false, "one turn at a time");
  assert.equal(awayFrom(offline(null)), "TIM-PC");
  assert.equal(awayFrom(remote), "");
  assert.equal(awayFrom({ ...ready, online: false }), "", "this computer's agent is never away");
  assert.equal(turnLabel(agent({ state: "queued" }), "Codex", "TIM-PC"), "Waiting for TIM-PC");
  assert.equal(turnLabel(agent({ state: "running" }), "Codex", "TIM-PC"), "Codex is working…", "running: it came back");
  assert.equal(turnLabel(agent({ state: "queued" }), "Codex", ""), "Codex is working…");
});

test("another computer's turn that asks: the page says it waits for the answer on that computer, and draws no card", () => {
  const now = new Date(2026, 10, 13, 18, 0);
  const since = new Date(2026, 10, 13, 14, 2).toISOString();
  const waiting = agent({ state: "running", waiting: true, steps: [{ kind: "run", program: "curl", status: "asked" }] });
  const running = { ...remote, turn: { turn_id: "t1", state: "running", ahead: false } };
  assert.equal(waitsThere(running, waiting), true);
  assert.equal(waitsThere(ready, { ...waiting }), false, "this computer's agent asks in the chat");
  assert.equal(waitsThere(running, agent({ state: "running", waiting: false })), false);
  assert.deepEqual(stripLine(running, waiting, now), { text: "Waiting for your answer on TIM-PC", kind: "asking" });
  assert.equal(turnLabel(waiting, "Codex", "", "TIM-PC"), "Codex is waiting for your answer on TIM-PC");
  // the computer away while it waits: away wins
  const gone = { ...offline(since), turn: running.turn };
  assert.match(stripLine(gone, waiting, now).text, /^Waiting for your answer on TIM-PC, offline since 2:02/);
  assert.equal(stripLine(gone, waiting, now).kind, "away");
  assert.deepEqual(stripLine({ ...offline(null), turn: running.turn }, waiting, now),
    { text: "Waiting for your answer on TIM-PC, which is offline", kind: "away" });
  assert.equal(turnLabel(waiting, "Codex", "TIM-PC", "TIM-PC"), "Waiting for your answer on TIM-PC, which is offline");
  assert.equal(stripLine({ ...running, paused: true }, waiting, now).kind, "paused");
  // an ended turn's flag waits for nothing
  const ended = agent({ waiting: true, steps: [{ kind: "run", program: "curl", status: "declined" }] });
  assert.equal(stripLine(remote, ended, now).text, "Codex on TIM-PC · paper");
  assert.equal(turnLabel(ended, "Codex", "", "TIM-PC"), "1 step");
  assert.equal(stepText(waiting.steps[0]), "Asked to run curl");
  assert.equal(drawsAsks(ready), true);
  assert.equal(drawsAsks(remote), false, "another computer's asks are answered there");
});

test("another computer's agent: no menu on the strip, no terminal", () => {
  assert.equal(isRemote(remote), true);
  assert.equal(isRemote(ready), false);
  assert.equal(offersMenu(ready), true);
  assert.equal(offersMenu(remote), false, "Show in folder and Continue in a terminal need the window to be that computer");
  assert.equal(offersMenu(null), false);
  assert.equal(canContinue(remote, { messages: [person, agent()] }), false, "the server says no session");
});

test("the strip drawn from the page's computers until the side's read lands", () => {
  const vault = { kind: "folder", name: "paper", paused: false, agent: { name: "codex", label: "Codex" } };
  const pc = (over = {}) => ({ id: "c1", name: "TIM-PC", online: true, since: null, vault,
    assistants: [{ name: "codex", label: "Codex", found: true, ready: true, version: "0.167.0", code: "" }], ...over });
  const seat = stripFacts(keeperAgent([pc()]));
  assert.deepEqual(seat, {
    agent: "codex", label: "Codex", ready: true, problem: "", models: [], folder: "paper", paused: false,
    computer: "TIM-PC", online: true, since: null, remote: true,
  });
  assert.deepEqual(stripLine(seat), { text: "Codex on TIM-PC · paper", kind: "ready" }, "the strip before the side's read");
  const since = "2026-11-13T14:02:00Z";
  const away = stripFacts(keeperAgent([pc({ online: false, since })]));
  assert.deepEqual([away.online, away.since, away.ready, awayFrom(away)], [false, since, null, "TIM-PC"]);
  assert.equal(stripFacts(keeperAgent([pc({ online: true, since })])).since, null, "connected: no time it went away");
  assert.equal(stripFacts(keeperAgent([pc({ vault: { ...vault, paused: true } })])).paused, true);
  assert.equal(stripFacts(null), null);
});

test("the transcript's shape: a person's message carries its passages' pages", () => {
  const [mine] = shownMessages({ messages: [{ ...person, selections: [{ text: "a passage", page: 3, box: [0, 0, 1, 1] }, { text: "" }, { text: "b" }] }] }, ready);
  assert.deepEqual(mine, { id: "m1", role: "user", text: "fix this", passages: [{ text: "a passage", page: 3 }, { text: "b", page: null }] });
});

test("the transcript's shape: a turn done, with its steps as plain chips, its model and what it changed", () => {
  const info = { ...ready, models: [{ id: "gpt-fake", label: "GPT Fake" }], dir: { kind: "vault", name: "Thesis" } };
  const steps = [{ id: "s1", kind: "read", path: "main.tex", status: "completed" }, { id: "s2", kind: "edit", path: "main.tex", status: "failed" },
    { id: "s3", kind: "run", program: "latexmk", status: "declined" }, { id: "s4", kind: "other", status: "completed" }];
  const [, reply] = shownMessages({ messages: [person, agent({ steps, model: "gpt-fake", effort: "high", changes: ["main.tex"], changed: 1, built: true })] }, info);
  assert.deepEqual(reply.actions, [
    { kind: "file", summary: "Read main.tex" }, { kind: "fileEdit", summary: "Edited main.tex", error: true },
    { kind: "run", summary: "Not allowed to run latexmk", declined: true }, { kind: "work", summary: "Worked" }]);
  assert.ok(reply.actions.every((a) => !isChange(a)), "no step reads as a change of the library or the notes");
  assert.deepEqual(splitActions(reply.actions), { library: [], notes: [], failed: 1, declined: 1 });
  assert.deepEqual([reply.role, reply.turnId, reply.label, reply.text, reply.partial, reply.model, reply.effort, reply.notice],
    ["ai", "t1", "Codex", "Fixed.", false, "GPT Fake", "high", ""]);
  assert.deepEqual(reply.pill, { label: "4 steps", waiting: false });
  assert.deepEqual(reply.files, { shown: ["main.tex"], rest: [], more: 0, n: 1, built: true, vault: true });
  assert.deepEqual(reply.asks, []);
  assert.equal("errorKind" in reply, false);
  assert.equal(shownMessages({ messages: [agent({ model: "gone-model" })] }, info)[0].model, "gone-model", "a model no longer listed by its id");
  assert.equal(shownMessages({ messages: [agent()] }, ready)[0].files, null);
  assert.deepEqual(stepChip({ kind: "search" }), { kind: "search", summary: "Searched the sources" });
  // A call of Gamma's tools is the kind Gamma AI's call of that tool is: the pill counts it as a read.
  const reads = [{ kind: "gamma", tool: "read_page", status: "completed" }, { kind: "gamma", tool: "list_pages", status: "completed" },
    { kind: "gamma", tool: "view_pdf_page", status: "failed" }, { kind: "gamma", tool: "read_annotation" }].map(stepChip);
  assert.deepEqual(reads.map((c) => c.kind), ["read", "list", "view", "read"]);
  assert.deepEqual(reads[2], { kind: "view", summary: "Read Gamma: view_pdf_page", error: true });
  assert.equal(splitActions(reads).failed, 1);
  assert.equal(reads.filter(isChange).length, 0, "a read changes nothing");
  // a writing tool's call is a step of its own, never listed among the reply's changes
  const writes = ["comment", "resolve", "highlight"].map((tool) => stepChip({ kind: "gamma", tool, status: "completed" }));
  assert.deepEqual(writes.map((c) => c.kind), ["write", "write", "write"]);
  assert.equal(writes.filter(isChange).length, 0);
});

test("the transcript's shape: a turn under way is partial, its pill the agent at work, its asks drawn here only", () => {
  const ask = { id: "a1", kind: "command", command: "latexmk -pdf main.tex", cwd: "." };
  const running = agent({ state: "running", text: "Look", steps: [{ id: "s1", kind: "edit", path: "a.tex", status: "started" }] });
  const [live] = shownMessages({ messages: [running] }, ready);
  assert.deepEqual([live.partial, live.pill], [true, { label: "Codex is working: Editing a.tex", waiting: false }]);
  const [asking] = shownMessages({ messages: [{ ...running, asks: [ask] }] }, ready);
  assert.deepEqual([asking.asks, asking.pill], [[ask], { label: "Codex is waiting for you", waiting: true }]);
  const far = { ...ready, remote: true, computer: "TIM-PC", online: true };
  const [there] = shownMessages({ messages: [{ ...running, waiting: true }] }, far);
  assert.deepEqual([there.asks, there.pill], [[], { label: "Codex is waiting for your answer on TIM-PC", waiting: true }],
    "another computer's are answered there");
  const [ended] = shownMessages({ messages: [{ ...running, state: "done", asks: [ask] }] }, ready);
  assert.deepEqual([ended.partial, ended.asks], [false, []], "an ended turn draws no card");
});

test("the transcript's shape: a stopped turn ends stopped, a failed one carries the card's fields, a fresh one its line", () => {
  const [stopped] = shownMessages({ messages: [agent({ state: "stopped", text: "Half" })] }, ready);
  assert.equal(stopped.text, "Half\n\n*(stopped)*");
  assert.equal(shownMessages({ messages: [agent({ state: "stopped", text: "" })] }, ready)[0].text, "*(stopped)*");
  const [failed] = shownMessages({ messages: [agent({ state: "failed", text: "", error: "Codex was turned off on this computer." })] }, ready);
  assert.deepEqual([failed.error, failed.errorKind, failed.errorProvider, failed.errorSaid],
    [true, "agent", "Codex", "Codex was turned off on this computer."]);
  assert.equal(failureCopy(failed.errorKind, { said: failed.errorSaid }).headline, "Codex was turned off on this computer.",
    "the card is headed by the computer's own sentence");
  const [partway] = shownMessages({ messages: [agent({ state: "failed", text: "Begun", error: "The turn timed out." })] }, ready);
  assert.deepEqual([partway.error, partway.errorKind, partway.text], [undefined, "agent", "Begun"], "with text, the card is compact");
  assert.equal(shownMessages({ messages: [agent({ fresh: true })] }, ready)[0].notice,
    "Codex started a new session: it does not remember the earlier turns of this conversation.");
  assert.deepEqual(shownMessages(null, ready), []);
});

test("History lists every agent's conversations by time, the one shown first", () => {
  const gamma = [{ id: "g1", title: "Older AI", preview: "x", count: 2, updated_at: "2026-11-01T10:00:00Z" }];
  const computer = { conversations: [{ id: "c1", title: "With Codex", agent: "codex", updated_at: "2026-11-05T10:00:00Z", turns: 1 }],
    active: { id: "c2", title: "Claude now", agent: "claude", updated_at: "2026-11-06T10:00:00Z", turns: 2 } };
  const rows = historyRows({ shown: { title: "Now", preview: "now" }, gamma, computer });
  assert.deepEqual(rows.map((r) => [r.id, r.agent, !!r.active, !!r.current]), [
    ["", "gamma", true, false], ["c2", "claude", false, true], ["c1", "codex", false, false], ["g1", "gamma", false, false]]);
  // a computer agent shown: its conversation first, Gamma AI's current one listed by its version
  const shown = historyRows({ agent: "claude", shownId: "c2", shown: { title: "Claude now", preview: "" }, gamma, computer,
    gammaNow: { title: "AI now", preview: "p", updated_at: "2026-11-07T10:00:00Z" } });
  assert.deepEqual(shown.map((r) => [r.id, r.agent, !!r.active, !!r.current]), [
    ["", "claude", true, false], ["gamma", "gamma", false, true], ["c1", "codex", false, false], ["g1", "gamma", false, false]]);
  assert.equal(historyRows({ shown: null, gamma: [] }).length, 0, "a computer agent with no conversation yet lists none shown");
  assert.equal(historyRows({ shown: { title: "Now" }, gammaNow: { title: "x" } }).length, 1, "Gamma AI's current one is the one shown");
});
