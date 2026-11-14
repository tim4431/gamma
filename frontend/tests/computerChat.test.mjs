// The chat's computer side, its words and requests (src/chat/computer/computerChat.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CHANGES_SHOWN, NOTE_MAX, SOURCES_MAX, answerBody, askCommand, askPath, askPlace, askRisk, askTitle, awayFrom, canContinue, canSend, chatPath,
  currentStep, diffRows, drawsAsks, endText, freshLine, isLive, isRemote, lastEnded, liveTurn, markParts, modelChoice, modelDisplay, offersMenu,
  retryOf, scrolledToEnd, sideMark, sourceNote, sourcesBody, stepNow, stepText, stripLine, turnBody, turnChanges, turnLabel, waitsThere,
} from "../src/chat/computer/computerChat.js";
import { keeperComputer } from "../src/vault/vaultState.js";

const agent = (over = {}) => ({
  id: "m2", role: "agent", agent: "codex", label: "Codex", turn_id: "t1", state: "done", text: "Fixed.", steps: [],
  changes: [], changed: 0, built: false, error: "", fresh: false, ...over,
});
const person = { id: "m1", role: "user", text: "fix this", selections: [] };
const ready = { agent: "codex", label: "Codex", ready: true, problem: "", folder: "Thesis", paused: false, turn: null };

test("the read asks for the page, with the version held once there is one", () => {
  assert.equal(chatPath("p 1"), "/local-agents/chat?page=p%201");
  assert.equal(chatPath("p1", 0), "/local-agents/chat?page=p1&version=0");
  assert.equal(chatPath("p1", 7), "/local-agents/chat?page=p1&version=7");
});

test("a message posts its id, the page, the text trimmed and the passages with their place", () => {
  assert.deepEqual(turnBody("t1", "p1", "  fix this \n", [{ text: "a passage", page: 3, box: [0.1, 0.2, 0.3, 0.4] }, { text: "" }, { text: "b" }]), {
    id: "t1", page: "p1", text: "fix this",
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
  assert.equal(stepNow({ kind: "edit", path: "main.tex" }), "Editing main.tex");
  assert.equal(stepNow({ kind: "run", program: "latexmk" }), "Running latexmk");
  assert.equal(stepNow({ kind: "search" }), "Searching the sources");
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
    { text: "The work folder is paused on this computer: Codex starts once it syncs again.", kind: "paused" });
  // The page's own word for the agent (vaultState computerAgent) until the side's read lands.
  assert.equal(stripLine({ name: "codex", label: "Codex", ready: true, problem: "", running: null, folder: "Thesis", paused: false }).text,
    "Codex on this computer · Thesis");
});

test("what the agent asks: the card's title, where a command runs, what allowing it means", () => {
  const command = { id: "a1", kind: "command", command: "latexmk -pdf main.tex", cwd: "", reason: "Build the PDF", at: "" };
  const files = { id: "a2", kind: "files", paths: ["outside"], diff: "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n same\n", at: "" };
  assert.equal(askTitle(command, "Codex"), "Codex wants to run a command");
  assert.equal(askTitle(command, "Claude Code"), "Claude Code wants to run a command");
  assert.equal(askTitle(files, "Codex"), "Codex wants to change files outside its sandbox");
  assert.equal(askTitle({ kind: "tool" }, "Codex"), "Codex wants to use a tool");
  assert.equal(askPlace(""), "In the work folder");
  assert.equal(askPlace("."), "In the work folder");
  assert.equal(askPlace("sections"), "In sections");
  assert.equal(askPlace("outside"), "In a folder outside the work folder");
  assert.equal(askPlace("~"), "In your home folder");
  assert.equal(askRisk(command), "It runs as you, with your files and the network.");
  assert.equal(askRisk(files), "It changes files outside the work folder.");
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
    { id: "t1", page: "p1", text: "x", selections: [], model: "gpt-5.5", effort: "high" });
  assert.deepEqual(turnBody("t1", "p1", "x", [], { model: "", effort: "low" }), { id: "t1", page: "p1", text: "x", selections: [], effort: "low" });
  assert.deepEqual(turnBody("t1", "p1", "x", [], { model: "", effort: "" }), { id: "t1", page: "p1", text: "x", selections: [] });
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
  assert.equal(body.page, "p1");
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
    { text: "The work folder is paused on TIM-PC: Codex starts once it syncs again.", kind: "paused" });
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

test("the side drawn from the page's computers where this computer has no agent for it", () => {
  const vault = { kind: "folder", name: "paper", paused: false, agent: { name: "codex", label: "Codex" } };
  const pc = (over = {}) => ({ id: "c1", name: "TIM-PC", online: true, since: null, vault,
    assistants: [{ name: "codex", label: "Codex", found: true, ready: true, version: "0.167.0", code: "" }], ...over });
  const seat = keeperComputer([pc()]);
  assert.deepEqual(seat, {
    name: "codex", label: "Codex", ready: true, problem: "", computer: "TIM-PC", online: true,
    running: null, folder: "paper", paused: false, since: null, remote: true,
  });
  assert.deepEqual(stripLine(seat), { text: "Codex on TIM-PC · paper", kind: "ready" }, "the strip before the side's read");
  const since = "2026-11-13T14:02:00Z";
  const away = keeperComputer([pc({ online: false, since })]);
  assert.deepEqual([away.online, away.since, away.ready, awayFrom(away)], [false, since, null, "TIM-PC"]);
  assert.equal(keeperComputer([pc({ online: true, since })]).since, null, "connected: no time it went away");
  assert.equal(keeperComputer([pc({ vault: { ...vault, paused: true } })]).paused, true);
  assert.equal(keeperComputer([pc({ vault: { ...vault, agent: null } })]), null, "no agent on");
  assert.equal(keeperComputer([]), null, "another account's: the server names no computer");
});

test("the side switch's mark: waiting while a turn of the page asks, working while one runs or the agent works in the folder", () => {
  const computer = { label: "Codex", running: null };
  const turn = (fields = {}) => ({ kind: "computer-turn", state: "running", params: { page: "p1" }, progress: {}, ...fields });
  assert.deepEqual(sideMark(computer, [], "p1"), { kind: "", text: "" });
  assert.deepEqual(sideMark(computer, [turn({ progress: { phase: "asking" } })], "p1"), { kind: "waiting", text: "Codex is waiting for you" });
  assert.deepEqual(sideMark(computer, [turn()], "p1"), { kind: "working", text: "Codex is working…" });
  assert.equal(sideMark(computer, [turn({ params: { page: "p2" } })], "p1").kind, "", "another page's turn");
  assert.equal(sideMark(computer, [turn({ state: "queued" })], "p1").kind, "");
  assert.equal(sideMark({ ...computer, running: { kind: "send", request_id: "r1" } }, [], "p1").kind, "working", "a Send in the folder");
  assert.equal(sideMark(null, [turn()], "p1").kind, "");
});
