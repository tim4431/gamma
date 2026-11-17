// The chat's computer side, its words and requests, and its conversation in
// the transcript's shape (src/chat/computer/computerChat.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CHANGES_SHOWN, NOTE_MAX, SOURCES_MAX, agentHint, answerBody, askCommand, askPath, askPlace, askRisk, askTitle, awayFrom, canContinue, canSend, chatPath,
  currentStep, diffRows, emptyLine, endText, freshLine, isLive, isRemote, lastEnded, liveTurn, markParts, modelChoice, modelDisplay,
  offersMenu, retryOf, revealsFile, scrolledToEnd, sourceNote, sourcesBody, stepNow, stepText, stripLine, turnBody, turnChanges, workPlace,
  turnLabel, waitsThere, historyRows, sessionRows, sessionsAway, sessionsDir, sessionsWords, shownMessages, stepChip,
  steerTaken, steeringTurn,
} from "../src/chat/computer/computerChat.js";
import { isChange, splitActions } from "../src/chat/agentSteps.js";
import { failureCopy } from "../src/chat/chatErrors.js";
import { farAgents, stripFacts } from "../src/chat/agents.js";

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
  assert.equal(chatPath("p1", null, "codex", { thread: "th1" }), "/local-agents/chat?bucket=p1&agent=codex&thread=th1",
    "a thread's conversation");
  assert.equal(chatPath("p1", 4, "codex", { conversation: "b1" }), "/local-agents/chat?bucket=p1&version=4&agent=codex&conversation=b1",
    "a co-author's chat");
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
  // a call of Gamma's tools reads as Gamma AI's own call of that tool: done, and while it runs
  assert.equal(stepText({ kind: "gamma", tool: "read_page" }), "Read a page");
  assert.equal(stepText({ kind: "gamma", tool: "list_annotations" }), "Listed the highlights and comments");
  assert.equal(stepText({ kind: "gamma", tool: "comment" }), "Wrote a comment", "a writing tool's call, as a write");
  assert.equal(stepNow({ kind: "gamma", tool: "list_annotations" }), "Listing the highlights and comments…");
  assert.equal(stepNow({ kind: "gamma", tool: "highlight" }), "Highlighting a passage…");
  assert.equal(stepNow({ kind: "gamma", tool: "view_pdf_page" }), "Looking at a PDF page…", "no arguments: no page named");
  // a tool those words do not know, by its name; none at all, Gamma
  assert.equal(stepText({ kind: "gamma", tool: "new_tool" }), "Read Gamma: new_tool");
  assert.equal(stepNow({ kind: "gamma", tool: "new_tool" }), "Reading Gamma: new_tool");
  assert.equal(stepText({ kind: "gamma", tool: "" }), "Read Gamma");
  assert.equal(stepNow({ kind: "edit", path: "main.tex" }), "Editing main.tex");
  assert.equal(stepNow({ kind: "run", program: "latexmk" }), "Running latexmk");
  assert.equal(stepNow({ kind: "search" }), "Searching the sources");
  assert.equal(stepNow({ kind: "gamma", tool: "search_library" }), "Searching your library…");
  assert.equal(stepNow({ kind: "other" }), "", "nothing worth naming");
  assert.equal(stepNow(null), "");
});

test("a turn's pill: the agent at work, then how many steps it took", () => {
  const steps = [{ id: "s1", kind: "read", path: "a.tex", status: "completed" }, { id: "s2", kind: "edit", path: "b.tex", status: "started" },
    { id: "s3", kind: "search", status: "completed" }];
  assert.equal(currentStep({ steps }).id, "s2", "the newest still started");
  assert.equal(currentStep({ steps: steps.slice(0, 1) }).id, "s1");
  assert.equal(turnLabel(agent({ state: "running", steps }), "Codex"), "Codex is working: Editing b.tex");
  assert.equal(turnLabel(agent({ state: "queued" }), "Codex"), "Starting Codex…", "queued: not at work yet");
  assert.equal(turnLabel(agent({ state: "queued" }), "Codex", "", "", true), "Waiting for Codex to finish its other work…",
    "queued behind other work in its directory");
  assert.equal(turnLabel(agent({ state: "running" }), "Codex", "", "", true), "Codex is working…", "running: `ahead` says nothing");
  assert.equal(turnLabel(agent({ state: "running", label: "" }), "Claude Code"), "Claude Code is working…");
  // once it ended, Gamma AI's summary of its steps: the files read and edited, each counted once, the commands it ran
  assert.equal(turnLabel(agent({ steps }), "Codex"), "3 steps · read 1 file, edited 1 file, searched");
  assert.equal(turnLabel(agent({ steps: steps.slice(0, 1) }), "Codex"), "1 step · read 1 file");
  const build = [{ kind: "read", path: "a.tex" }, { kind: "read", path: "b.tex" }, { kind: "read", path: "a.tex" },
    { kind: "edit", path: "a.tex" }, { kind: "run", program: "latexmk" }, { kind: "run", program: "latexmk" },
    { kind: "gamma", tool: "read_page" }, { kind: "run", program: "rm", status: "declined" }, { kind: "edit", path: "c.tex", status: "failed" }]
    .map((s) => ({ status: "completed", ...s }));
  assert.equal(turnLabel(agent({ steps: build }), "Codex"), "9 steps · read 2 files, edited 1 file, ran latexmk 2 times, read 1 page",
    "neither the step not allowed nor the one that failed is counted");
  assert.equal(turnLabel(agent({ steps: [{ kind: "run", program: "latexmk" }, { kind: "run", program: "bibtex" }, { kind: "run" }] }), "Codex"),
    "3 steps · ran 3 commands", "several programs are counted, not named");
  assert.equal(turnLabel(agent({ steps: [{ kind: "run", program: "latexmk" }] }), "Codex"), "1 step · ran latexmk");
  assert.equal(turnLabel(agent({ steps: [{ kind: "other" }] }), "Codex"), "1 step", "other work has no word");
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

test("the strip: the agent and the folder, what it waits for, why it cannot run; a turn's progress is the pill's", () => {
  assert.deepEqual(stripLine(ready), { text: "Codex on this computer · Thesis", kind: "ready" });
  assert.deepEqual(stripLine({ ...ready, folder: "" }), { text: "Codex on this computer", kind: "ready" });
  assert.deepEqual(stripLine({ ...ready, lent: { by: "alice" } }), { text: "alice's Codex on this computer · Thesis", kind: "ready" },
    "a co-author's agent names its owner");
  assert.deepEqual(stripLine({ ...ready, ready: null }), { text: "Checking Codex…", kind: "checking" });
  const running = agent({ state: "running", steps: [{ kind: "edit", path: "sections/intro.tex", status: "started" }] });
  const resting = { text: "Codex on this computer · Thesis", kind: "ready" };
  assert.deepEqual(stripLine({ ...ready, turn: { turn_id: "t1", state: "running", ahead: false } }, running), resting, "running");
  assert.deepEqual(stripLine({ ...ready, turn: { turn_id: "t1", state: "queued", ahead: true } }), resting, "queued behind other work");
  assert.deepEqual(stripLine({ ...ready, turn: { turn_id: "t1", state: "queued", ahead: false } }), resting, "starting");
  assert.deepEqual(stripLine({ ...ready, turn: null }, running), resting, "the conversation's own turn, before the server's read says it");
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
  assert.equal(stripLine(ready, agent({ state: "running", asks: [] })).kind, "ready", "no ask waiting: the pill says it works");
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

test("where the agent works: a choice of the offered directories until the first turn, then the one it works in", () => {
  const dirs = [{ id: "d1", path: "D:\\scratch", name: "Scratch" }, { id: "d2", path: "/tmp/b", name: "" }];
  const own = { ...ready, folder: "", dir: null };
  const options = [["", "Its own directory"], ["d1", "Scratch"], ["d2", "/tmp/b"]];
  assert.deepEqual(workPlace(own, null, dirs), { pick: true, value: "", options, text: "Its own directory" });
  assert.deepEqual(workPlace(own, null, dirs, "d2"), { pick: true, value: "d2", options, text: "/tmp/b" });
  assert.equal(workPlace(own, null, dirs, "gone").value, "", "a directory no longer offered: none");
  assert.deepEqual(workPlace(own, null, []).options, [["", "Its own directory"]]);
  // as a Codex thread starts in the project used last: the folders used last first, the newest the default
  const used = ["gone", "d2", ""];
  const byUse = [["", "Its own directory"], ["d2", "/tmp/b"], ["d1", "Scratch"]];
  assert.deepEqual(workPlace(own, null, dirs, null, used), { pick: true, value: "d2", options: byUse, text: "/tmp/b" },
    "the newest still offered");
  assert.equal(workPlace(own, null, dirs, "", used).value, "", "a pick, Its own directory too, comes before the one used last");
  assert.equal(workPlace(own, null, dirs, "d1", used).value, "d1");
  assert.equal(workPlace(own, null, dirs, null, ["", "d2"]).value, "", "the last began in a directory of its own");
  assert.equal(workPlace(own, null, dirs, "gone", ["d1"]).value, "d1", "a pick no longer offered: the one used last");
  // once the conversation has a turn, its own: shown among the others, not to change
  const said = { messages: [person, agent()] };
  assert.deepEqual(workPlace(own, said, dirs), { pick: false, value: "", options, text: "Its own directory" });
  assert.deepEqual(workPlace({ ...own, dir: { kind: "offered", id: "d1", name: "Scratch" } }, said, dirs),
    { pick: false, value: "d1", options, text: "Scratch" });
  assert.deepEqual(workPlace({ ...own, dir: { kind: "offered", id: "d9", name: "Old" } }, said, dirs),
    { pick: false, value: "d9", options: [...options, ["d9", "Old"]], text: "Old" }, "one no longer offered stays the conversation's");
  assert.equal(workPlace({ ...ready, dir: { kind: "vault", name: "Thesis" } }, null, dirs), null, "a vault's: named in the strip's line");
  assert.equal(workPlace({ ...remote, dir: { kind: "vault", name: "paper" } }, null, dirs), null, "another computer's vault");
  const far = { ...remote, folder: "", dir: null, dirs: [{ id: "f1", name: "Drafts" }] };
  assert.deepEqual(workPlace(far, null, dirs).options, [["", "Its own directory"], ["f1", "Drafts"]], "the directories that computer offers");
  assert.equal(workPlace({ ...far, lent: { by: "alice" } }, null, dirs), null, "a co-author's: a synced folder's alone");
  assert.equal(workPlace(null, null, dirs), null);
  // the empty transcript says where it works
  assert.equal(emptyLine({ ...ready, dir: { kind: "vault", name: "Thesis" } }, "Codex"),
    "Ask Codex about this file or to change it: it works in the synced folder's directory on this computer.");
  assert.equal(emptyLine({ ...ready, dir: { kind: "vault", name: "Thesis" } }, "Codex", "", null, false),
    "Ask Codex about this folder or its files, or to change them: it works in the synced folder's directory on this computer.", "a folder of it");
  assert.equal(emptyLine(own, "Codex", "", workPlace(own, null, dirs)), "Ask Codex anything: it works in an empty directory of its own on this computer.");
  assert.equal(emptyLine(own, "Codex", "", workPlace(own, null, dirs, "d1")), "Ask Codex anything: it works in the directory you chose on this computer.");
  assert.equal(emptyLine(far, "Codex", "TIM-PC", workPlace(far, null, dirs)),
    "Ask Codex anything: it works in an empty directory of its own on TIM-PC.");
  assert.equal(emptyLine(far, "Codex", "TIM-PC", workPlace(far, null, dirs, "f1")),
    "Ask Codex anything: it works in the directory you chose on TIM-PC.");
  assert.equal(emptyLine({ ...remote, dir: { kind: "vault", name: "paper" } }, "Codex", "TIM-PC"),
    "Ask Codex about this file or to change it: it works in the synced folder's directory on TIM-PC.");
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

test("another computer's agent: the strip names that computer, and says when it is away and to send once it is back", () => {
  const now = new Date(2026, 10, 13, 18, 0);
  const since = new Date(2026, 10, 13, 14, 2).toISOString();
  assert.deepEqual(stripLine(remote), { text: "Codex on TIM-PC · paper", kind: "ready" });
  assert.equal(stripLine({ ...remote, folder: "" }).text, "Codex on TIM-PC");
  assert.match(stripLine(offline(since), null, now).text, /^TIM-PC is offline since 2:02.*: send this once it connects\.$/);
  assert.equal(stripLine(offline(since), null, now).kind, "away");
  assert.equal(stripLine(offline(null), null, now).text, "TIM-PC is offline: send this once it connects.", "no time known");
  const queued = agent({ state: "queued", text: "" });
  assert.match(stripLine({ ...offline(since), turn: { turn_id: "t1", state: "queued", ahead: false } }, queued, now).text,
    /^Waiting for TIM-PC, offline since 2:02.*: the message runs when it connects$/);
  assert.equal(stripLine(offline(null), queued, now).text, "Waiting for TIM-PC, which is offline: the message runs when it connects",
    "the conversation's own turn, before the server's read says it");
  assert.equal(stripLine({ ...remote, turn: { turn_id: "t1", state: "queued", ahead: false } }).text, "Codex on TIM-PC · paper",
    "connected: it starts, as the pill says");
  const running = agent({ state: "running", steps: [{ kind: "edit", path: "main.tex", status: "started" }] });
  assert.equal(stripLine({ ...remote, turn: { turn_id: "t1", state: "running", ahead: false } }, running).text, "Codex on TIM-PC · paper");
  assert.deepEqual(stripLine({ ...remote, paused: true }),
    { text: "The synced folder is paused on TIM-PC: Codex starts once it syncs again.", kind: "paused" });
  assert.equal(stripLine({ ...remote, ready: false, problem: "Codex is not signed in on TIM-PC." }).text, "Codex is not signed in on TIM-PC.");
  assert.deepEqual(stripLine({ ...remote, ready: null }), { text: "Checking Codex…", kind: "checking" });
});

test("another computer's agent: no message goes while it is away, and a turn queued before waits for it", () => {
  const conversation = { messages: [person, agent()] };
  assert.equal(canSend(offline(null), conversation, "next"), false, "the server queues nothing for a computer away");
  assert.equal(canSend(remote, conversation, "next"), true);
  assert.equal(canSend({ ...remote, ready: false }, conversation, "next"), false, "connected and unable to run");
  assert.equal(canSend({ ...offline(null), paused: true }, conversation, "next"), false);
  assert.equal(canSend({ ...offline(null), turn: { state: "queued" } }, conversation, "next"), false, "one turn at a time");
  assert.equal(awayFrom(offline(null)), "TIM-PC");
  assert.equal(awayFrom(remote), "");
  assert.equal(awayFrom({ ...ready, online: false }), "", "this computer's agent is never away");
  assert.equal(turnLabel(agent({ state: "queued" }), "Codex", "TIM-PC"), "Waiting for TIM-PC");
  assert.equal(turnLabel(agent({ state: "running" }), "Codex", "TIM-PC"), "Codex is working…", "running: it came back");
  assert.equal(turnLabel(agent({ state: "queued" }), "Codex", ""), "Starting Codex…");
});

test("another computer's turn that asks: the page says it waits for the answer on that computer, or draws the card it relays", () => {
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
  // one that computer relays here: waiting for you, the card drawn as this computer's
  const relayed = agent({ state: "running", waiting: false, asks: [{ id: "a1", kind: "command", command: "latexmk -pdf main.tex", once: true }] });
  assert.equal(waitsThere(running, relayed), false);
  assert.deepEqual(stripLine(running, relayed, now), { text: "Waiting for you: Codex wants to run a command", kind: "asking" });
  assert.deepEqual(stripLine(gone, relayed, now).kind, "asking", "answered here though the computer is away: it takes it once back");
  assert.equal(turnLabel(relayed, "Codex", "", "TIM-PC"), "Codex is waiting for you");
});

test("another computer's agent: no menu on the strip, no terminal", () => {
  assert.equal(isRemote(remote), true);
  assert.equal(isRemote(ready), false);
  assert.equal(offersMenu(ready), true);
  assert.equal(offersMenu(remote), false, "Show in folder and Continue in a terminal need the window to be that computer");
  assert.equal(offersMenu(null), false);
  assert.equal(canContinue(remote, { messages: [person, agent()] }), false, "the server says no session");
});

test("the composer chip's hint on an agent that cannot answer now: offline, or not ready with its problem", () => {
  const pc = (over = {}) => ({ id: "c1", name: "TIM-PC", online: true, since: null,
    assistants: [{ name: "codex", label: "Codex", found: true, ready: true, version: "0.167.0", code: "", enabled: true }], ...over });
  const [far] = farAgents([pc()]);
  assert.equal(agentHint(far), null, "connected and ready");
  assert.deepEqual(agentHint(farAgents([pc({ online: false })])[0]),
    { text: "offline", problem: false, title: "TIM-PC is offline: send this once it connects." });
  assert.deepEqual(agentHint({ ...far, ready: false, problem: "Codex is not signed in on TIM-PC." }),
    { text: "not ready", problem: true, title: "Codex is not signed in on TIM-PC." });
  assert.deepEqual(agentHint({ id: "gamma", kind: "gamma", ready: false, problem: "No AI service is connected." }),
    { text: "not ready", problem: true, title: "No AI service is connected." }, "Gamma AI too");
  assert.equal(agentHint({ id: "codex", kind: "computer", ready: null, problem: "" }), null, "still checking");
  assert.equal(agentHint(null), null);
});

test("the strip drawn from the page's computers until the side's read lands", () => {
  const vault = { kind: "folder", name: "paper", paused: false, work: true };
  const pc = (over = {}) => ({ id: "c1", name: "TIM-PC", online: true, since: null, vault,
    assistants: [{ name: "codex", label: "Codex", found: true, ready: true, version: "0.167.0", code: "", enabled: true }], ...over });
  const seat = stripFacts(farAgents([pc()])[0]);
  assert.deepEqual(seat, {
    agent: "codex", label: "Codex", ready: true, problem: "", models: [], folder: "paper", paused: false,
    computer: "TIM-PC", online: true, since: null, remote: true,
  });
  assert.deepEqual(stripLine(seat), { text: "Codex on TIM-PC · paper", kind: "ready" }, "the strip before the side's read");
  const since = "2026-11-13T14:02:00Z";
  const away = stripFacts(farAgents([pc({ online: false, since })])[0]);
  assert.deepEqual([away.online, away.since, away.ready, awayFrom(away)], [false, since, null, "TIM-PC"]);
  assert.equal(stripFacts(farAgents([pc({ online: true, since })])[0]).since, null, "connected: no time it went away");
  assert.equal(stripFacts(farAgents([pc({ vault: { ...vault, paused: true } })])[0]).paused, true);
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
    { kind: "file", summary: "Read main.tex", path: "main.tex" }, { kind: "fileEdit", summary: "Edited main.tex", path: "main.tex", error: true },
    { kind: "run", summary: "Not allowed to run latexmk", program: "latexmk", declined: true }, { kind: "work", summary: "Worked" }]);
  assert.ok(reply.actions.every((a) => !isChange(a)), "no step reads as a change of the library or the notes");
  assert.deepEqual(splitActions(reply.actions), { library: [], notes: [], failed: 1, declined: 1 });
  assert.deepEqual([reply.role, reply.turnId, reply.label, reply.text, reply.partial, reply.model, reply.effort, reply.notice],
    ["ai", "t1", "Codex", "Fixed.", false, "GPT Fake", "high", ""]);
  assert.deepEqual(reply.pill, { label: "4 steps · read 1 file", waiting: false, queued: false },
    "neither the edit that failed nor the command not allowed is counted");
  assert.deepEqual(reply.files, { shown: ["main.tex"], rest: [], more: 0, n: 1, built: true, vault: true });
  assert.deepEqual(reply.asks, []);
  assert.equal("errorKind" in reply, false);
  assert.equal(shownMessages({ messages: [agent({ model: "gone-model" })] }, info)[0].model, "gone-model", "a model no longer listed by its id");
  assert.equal(shownMessages({ messages: [agent()] }, ready)[0].files, null);
  // the comment threads its agent opened, which its card lists
  const opened = [{ id: "th1", page_id: "p1", block_id: "b1", text: "Which spelling?" }];
  assert.deepEqual(shownMessages({ messages: [agent({ threads: opened })] }, ready)[0].threads, opened);
  assert.deepEqual(reply.threads, [], "none opened");
  assert.deepEqual(stepChip({ kind: "search" }), { kind: "search", summary: "Searched the sources" });
  // a file's path and a command's program, which the ended turn's pill counts and names
  assert.deepEqual(stepChip({ kind: "edit", path: "a.tex", status: "completed" }), { kind: "fileEdit", summary: "Edited a.tex", path: "a.tex" });
  assert.deepEqual(stepChip({ kind: "run", program: "latexmk", status: "declined" }),
    { kind: "run", summary: "Not allowed to run latexmk", program: "latexmk", declined: true });
  // A call of Gamma's tools is the kind Gamma AI's call of that tool is: the pill counts it as a read.
  const reads = [{ kind: "gamma", tool: "read_page", status: "completed" }, { kind: "gamma", tool: "list_pages", status: "completed" },
    { kind: "gamma", tool: "view_pdf_page", status: "failed" }, { kind: "gamma", tool: "read_annotation" }].map(stepChip);
  assert.deepEqual(reads.map((c) => c.kind), ["read", "list", "view", "read"]);
  assert.deepEqual(reads[2], { kind: "view", summary: "Looked at a PDF page", error: true });
  assert.equal(splitActions(reads).failed, 1);
  assert.equal(reads.filter(isChange).length, 0, "a read changes nothing");
  // a writing tool's call is a step of its own, never listed among the reply's changes
  const writes = ["comment", "resolve", "highlight"].map((tool) => stepChip({ kind: "gamma", tool, status: "completed" }));
  assert.deepEqual(writes.map((c) => c.kind), ["write", "write", "write"]);
  assert.equal(writes.filter(isChange).length, 0);
});

test("the transcript's shape: a turn under way is partial, its pill the agent at work, its asks drawn while they wait here", () => {
  const ask = { id: "a1", kind: "command", command: "latexmk -pdf main.tex", cwd: "." };
  const running = agent({ state: "running", text: "Look", steps: [{ id: "s1", kind: "edit", path: "a.tex", status: "started" }] });
  const [live] = shownMessages({ messages: [running] }, ready);
  assert.deepEqual([live.partial, live.pill], [true, { label: "Codex is working: Editing a.tex", waiting: false, queued: false }]);
  const [asking] = shownMessages({ messages: [{ ...running, asks: [ask] }] }, ready);
  assert.deepEqual([asking.asks, asking.pill], [[ask], { label: "Codex is waiting for you", waiting: true, queued: false }]);
  const far = { ...ready, remote: true, computer: "TIM-PC", online: true };
  const [there] = shownMessages({ messages: [{ ...running, waiting: true }] }, far);
  assert.deepEqual([there.asks, there.pill], [[], { label: "Codex is waiting for your answer on TIM-PC", waiting: true, queued: false }],
    "another computer's are answered there");
  const [relayed] = shownMessages({ messages: [{ ...running, asks: [{ ...ask, once: true }] }] }, far);
  assert.deepEqual([relayed.asks, relayed.pill], [[{ ...ask, once: true }], { label: "Codex is waiting for you", waiting: true, queued: false }],
    "one that computer relays is answered here");
  // queued: the pill waits with a clock, behind other work in its directory as the server says it
  const queued = agent({ state: "queued", text: "" });
  assert.deepEqual(shownMessages({ messages: [queued] }, { ...ready, turn: { turn_id: "t1", state: "queued", ahead: true } })[0].pill,
    { label: "Waiting for Codex to finish its other work…", waiting: false, queued: true });
  assert.deepEqual(shownMessages({ messages: [queued] }, { ...ready, turn: { turn_id: "t1", state: "queued", ahead: false } })[0].pill,
    { label: "Starting Codex…", waiting: false, queued: true });
  assert.equal(shownMessages({ messages: [queued] }, { ...ready, turn: { turn_id: "t9", state: "queued", ahead: true } })[0].pill.label,
    "Starting Codex…", "another turn's place in the queue says nothing of this one");
  assert.deepEqual(shownMessages({ messages: [queued] }, { ...far, online: false })[0].pill,
    { label: "Waiting for TIM-PC", waiting: false, queued: true }, "queued for a computer away");
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
  const far = { conversations: [{ id: "c9", agent: "codex", agent_id: "computer:pc9:codex", updated_at: "" }], active: null };
  assert.equal(historyRows({ computer: far })[0].agent, "computer:pc9:codex", "another computer's: by its agent's id, which picks it");
});

test("History lists a co-author's chats with the reader's agent under their name", () => {
  const lent = [{ id: "b1", title: "Fix the caption", agent: "codex", updated_at: "2026-11-08T10:00:00Z", turns: 1, person: "bob" }];
  const rows = historyRows({ shown: { title: "Now", preview: "" }, computer: { conversations: [], active: null, lent } });
  const theirs = rows.find((r) => r.id === "b1");
  assert.deepEqual([theirs.lent, theirs.by, theirs.agent, !!theirs.current], [true, "bob's chat with Codex", "codex", false]);
  const shown = historyRows({ agent: "codex", shownId: "b1", shown: { title: "Fix the caption", preview: "" },
    computer: { conversations: [], active: null, lent } });
  assert.deepEqual(shown.map((r) => r.id), [""], "the one shown in place of the reader's own is listed once");
});

test("History lists the CLI's sessions of the directory where the reader's agent works on its computer", () => {
  const vault = { ...ready, folder_id: "W1", dir: { kind: "vault", name: "Thesis", path: "C:/thesis" } };
  assert.equal(sessionsDir(vault), "W1", "a synced folder's, as its id");
  assert.equal(sessionsDir({ ...vault, dir: { kind: "vault", name: "Thesis" } }), "", "a vault another account made");
  assert.equal(sessionsDir({ ...ready, dir: { kind: "offered", id: "d1", name: "Scratch" } }), "d1");
  const own = { ...ready, dir: null };
  assert.equal(sessionsDir(own, { pick: true, value: "d2" }), "d2", "the one picked for a conversation that begins");
  assert.equal(sessionsDir(own, { pick: true, value: "" }), "", "a directory of its own holds Gamma's alone");
  assert.equal(sessionsDir(own, { pick: false, text: "Its own directory" }), "");
  const far = { ...ready, remote: true, computer: "TIM-PC", online: true, folder_id: "W1",
    dir: { kind: "vault", name: "Thesis" } };
  assert.equal(sessionsDir(far), "W1", "another computer's agent, in the reader's own vault there");
  assert.equal(sessionsDir({ ...far, folder_id: null, dir: { kind: "offered", id: "d1", name: "Scratch" } }), "d1");
  assert.equal(sessionsDir({ ...far, lent: { by: "alice" } }), "", "a co-author's");
  assert.equal(sessionsDir({ ...vault, lent: { by: "alice" } }), "", "a co-author's");
  assert.equal(sessionsDir(null), "");
  assert.equal(sessionsAway("offline", "TIM-PC"), "TIM-PC is offline: its sessions show once it connects.");
  assert.equal(sessionsAway("older", "TIM-PC"), "TIM-PC needs a newer Gamma to list its sessions here.");
  assert.equal(sessionsAway("waiting", "TIM-PC"), "TIM-PC has not answered yet.");
  assert.equal(sessionsAway(undefined, "TIM-PC"), "");
  const rows = sessionRows([
    { id: "s1", title: "Fix the abstract", at: "2026-11-14T10:00:00Z", source: "cli", ours: false, busy: false },
    { id: "s2", title: "", at: "", source: "vscode", ours: false, busy: true },
    { id: "s3", title: "Shorten the title", at: "", source: "gamma", ours: true, busy: false },
    { id: "s4", title: "x", at: "", source: "other", ours: false, busy: false },
  ], "Codex");
  assert.deepEqual(rows.map((r) => [r.id, r.title, r.updated_at, r.by, r.busy]), [
    ["s1", "Fix the abstract", "2026-11-14T10:00:00Z", "Terminal", ""],
    ["s2", "", "", "VS Code", "Codex has this session open in another window: close it there first."],
    ["s3", "Shorten the title", "", "Continued here", ""],
    ["s4", "x", "", "", ""],
  ]);
  assert.deepEqual(sessionRows(null, "Codex"), []);
  // the section names the CLI and the directory, and the computer only when it is another
  assert.deepEqual(sessionsWords(vault, "Codex"), { caption: "Codex sessions in Thesis",
    empty: "Sessions you start with Codex in Thesis show here." });
  assert.equal(sessionsWords(far, "Codex", null, "TIM-PC").caption, "Codex sessions in Thesis on TIM-PC");
  assert.equal(sessionsWords({ ...ready, dir: { kind: "offered", id: "d1", name: "Scratch" } }, "Claude Code").caption,
    "Claude Code sessions in Scratch");
  const place = { pick: true, value: "d2", options: [["", "Its own directory"], ["d2", "Notes"]] };
  assert.equal(sessionsWords(own, "Codex", place).caption, "Codex sessions in Notes", "the one picked, by its name");
});

test("History names each row's agent while the chat has several, a computer agent's after its title", () => {
  const agents = [{ id: "gamma", kind: "gamma", label: "Gamma AI", where: "" },
    { id: "codex", agentId: "computer:pc1:codex", kind: "computer", label: "Codex", where: "" },
    { id: "computer:pc9:claude", agentId: "computer:pc9:claude", kind: "computer", label: "Claude Code", where: "TIM-PC" }];
  const gamma = [{ id: "g1", title: "Older AI", updated_at: "2026-11-01T10:00:00Z" }];
  const computer = { conversations: [
    { id: "c1", title: "Here", agent: "codex", agent_id: "computer:pc1:codex", updated_at: "2026-11-05T10:00:00Z" },
    { id: "c2", title: "There", agent: "claude", agent_id: "computer:pc9:claude", updated_at: "2026-11-04T10:00:00Z" },
    { id: "c3", title: "Gone", agent: "codex", agent_id: "computer:pc5:codex", updated_at: "2026-11-03T10:00:00Z" }],
  active: null, lent: [{ id: "b1", title: "Theirs", agent: "codex", agent_id: "computer:pc1:codex", updated_at: "", person: "bob" }] };
  const rows = historyRows({ agent: "codex", shown: { title: "Now", preview: "" }, gamma, computer, agents });
  assert.deepEqual(rows.map((r) => [r.id, r.agentName, r.by || ""]), [
    ["", "Codex", "Codex"], ["c1", "Codex", "Codex"], ["c2", "Claude Code on TIM-PC", "Claude Code on TIM-PC"],
    ["c3", "Codex", "Codex"], ["g1", "Gamma AI", ""], ["b1", "Codex", "bob's chat with Codex"]]);
  // one agent alone: nothing to tell apart
  const alone = historyRows({ shown: { title: "Now", preview: "" }, gamma, agents: agents.slice(0, 1) });
  assert.deepEqual(alone.map((r) => [r.agentName, r.by]), [[undefined, undefined], [undefined, undefined]]);
});

test("a turn steered into reads as its part before each message, the message, and the turn going on after it", () => {
  const read = (path) => ({ id: path, kind: "read", status: "completed", path });
  const turn = agent({
    state: "running", text: "Looking at the intro.\n\nRenamed the section.", steps: [read("a.tex"), read("b.tex")],
    steers: [{ id: "s1", text: "Only the intro.", selections: [{ text: "the intro", page: 2 }], at: "",
      place: { text: "Looking at the intro.".length, steps: 1 } }],
  });
  const rows = shownMessages({ messages: [person, turn] }, ready);
  assert.deepEqual(rows.map((r) => [r.role, r.text]), [
    ["user", "fix this"], ["ai", "Looking at the intro."], ["user", "Only the intro."], ["ai", "Renamed the section."]]);
  const [, before, steered, after] = rows;
  assert.deepEqual([before.actions.length, after.actions.length], [1, 1]);
  assert.equal(before.partial, false);
  assert.equal(after.partial, true);
  assert.equal(after.turnId, "t1");
  assert.equal(steered.steered, true);
  assert.deepEqual(steered.passages, [{ text: "the intro", page: 2 }]);
  assert.equal(steerTaken({ messages: [turn] }, "s1"), true);
  assert.equal(steerTaken({ messages: [turn] }, "s2"), false);
  // taken before the agent wrote or did anything: no empty part before it
  const early = shownMessages({ messages: [person, agent({ steers: [{ ...turn.steers[0], place: { text: 0, steps: 0 } }] })] }, ready);
  assert.deepEqual(early.map((r) => r.role), ["user", "user", "ai"]);
});

test("Steer goes to the conversation's turn while its agent works, not one waiting for its directory", () => {
  assert.equal(steeringTurn({ messages: [person, agent({ state: "running" })] }), "t1");
  assert.equal(steeringTurn({ messages: [person, agent({ state: "queued" })] }), "");
  assert.equal(steeringTurn({ messages: [person, agent()] }), "");
});
