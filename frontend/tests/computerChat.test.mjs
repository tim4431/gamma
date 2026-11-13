// The chat's computer side, its words and requests (src/chat/computer/computerChat.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  canSend, changesLine, chatPath, currentStep, endText, freshLine, isLive, liveTurn, stepNow, stepText, stripLine, turnBody,
  turnLabel,
} from "../src/chat/computer/computerChat.js";

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

test("what a turn changed, on one line", () => {
  assert.equal(changesLine(agent()), "");
  assert.equal(changesLine(agent({ changes: ["sections/intro.tex"], changed: 1 })), "Changed sections/intro.tex");
  assert.equal(changesLine(agent({ changes: ["sections/intro.tex", "refs.bib"], changed: 2, built: true })),
    "Changed sections/intro.tex and 1 more · The PDF was rebuilt");
  assert.equal(changesLine(agent({ changes: ["a.tex"], changed: 5 })), "Changed a.tex and 4 more", "the count names what is not listed");
  assert.equal(changesLine(agent({ changed: 3 })), "Changed 3 files");
  assert.equal(changesLine(agent({ built: true })), "The PDF was rebuilt");
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
