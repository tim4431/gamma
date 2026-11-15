// A comment field's Send to {agent} tick (src/comments/sendTo.js,
// docs/dev/comments.md "Sent to an agent"): its default, the agent it names,
// `@name` ticking it, and what the field says under the text.
import test from "node:test";
import assert from "node:assert/strict";
import { editHint, namedAgent, sendNote, startsTicked, tickAgent, tickedPlaceholder } from "../src/comments/sendTo.js";

const CODEX = { id: "computer:pc1:codex", name: "codex", label: "Codex", ready: true, problem: "" };
const CLAUDE = { id: "computer:pc1:claude", name: "claude", label: "Claude Code", ready: true, problem: "" };
const FAR = { id: "computer:nas9:codex", name: "codex", label: "Codex", ready: true, problem: "", computer: "TIM-PC", online: true };
const said = (author, extra = {}) => ({ id: `c${Math.random()}`, author, body: "x", deleted: false, ...extra });
const thread = (...comments) => ({ id: "t1", comments });

test("the tick starts ticked in a thread a computer's agent spoke last in, else as the person left it", () => {
  const asked = thread(said("tim"), said("tim", { agent: "Codex", agent_kind: "computer", agent_id: CODEX.id }));
  assert.equal(startsTicked(asked, false), true, "the agent asked: the reply goes to it");
  assert.equal(startsTicked(thread(...asked.comments, said("ana")), false), false, "a person spoke since");
  assert.equal(startsTicked(thread(said("tim", { agent: "Gamma AI", agent_kind: "gamma" })), false), false, "no computer's agent");
  assert.equal(startsTicked(null, true), true, "a new thread: the person's last choice");
  assert.equal(startsTicked(thread(said("ana")), true), true);
  assert.equal(startsTicked(thread(said("x", { agent_kind: "computer", agent_id: CODEX.id, deleted: true }), said("ana")), false), false);
});

test("the agent it names: the one chosen, else the thread's last agent, else the chat's pick, else the first", () => {
  const agents = [CODEX, CLAUDE];
  const byClaude = thread(said("tim"), said("tim", { agent_kind: "computer", agent_id: CLAUDE.id }), said("tim"));
  assert.equal(tickAgent(agents, { thread: byClaude }), CLAUDE);
  assert.equal(tickAgent(agents, { thread: byClaude, chosen: CODEX.id }), CODEX, "chosen in its menu");
  assert.equal(tickAgent(agents, { picked: "claude" }), CLAUDE, "the page's chat picked it");
  assert.equal(tickAgent(agents, { picked: "gamma" }), CODEX, "Gamma AI picked: the first");
  assert.equal(tickAgent(agents, { thread: thread(said("x", { agent_kind: "computer", agent_id: "computer:gone:codex" })) }), CODEX,
    "an agent no longer listed");
  assert.equal(tickAgent([], { picked: "codex" }), null);
  // the chat names this computer's agent by its name, another computer's by its id
  assert.equal(tickAgent([FAR, CODEX], { picked: "codex" }), CODEX, "this computer's Codex, not another's of that name");
  assert.equal(tickAgent([CODEX, FAR], { picked: FAR.id }), FAR, "another computer's, picked in the chat");
});

test("@name typed ticks the box for that agent, outside code and newly", () => {
  const agents = [CODEX, CLAUDE];
  assert.equal(namedAgent(agents, "@claude tighten this"), CLAUDE);
  assert.equal(namedAgent(agents, "see `@codex` here"), null, "code");
  assert.equal(namedAgent(agents, "@Codex go"), null, "the name exactly");
  assert.equal(namedAgent(agents, "@codex go on", "@codex go"), null, "named before");
  assert.equal(namedAgent(agents, "@codex and @claude", "@codex"), CLAUDE);
  assert.equal(namedAgent([], "@codex"), null);
});

test("what the field says: the agent's problem, its computer offline, the placeholder when ticked", () => {
  assert.equal(sendNote(CODEX), null);
  assert.deepEqual(sendNote({ ...CODEX, ready: false, problem: "Codex is not signed in on this computer." }),
    { text: "Codex is not signed in on this computer.", warn: true });
  assert.deepEqual(sendNote({ ...FAR, online: false }), { text: "TIM-PC is offline: send this once it connects.", warn: true });
  assert.equal(sendNote(FAR), null);
  assert.equal(sendNote(null), null);
  assert.equal(tickedPlaceholder(CODEX, "Reply…"), "Ask Codex to edit");
  assert.equal(tickedPlaceholder(null, "Reply…"), "Reply…");
});

test("an edit that newly names an agent says saving sends it there, or why the server will not", () => {
  const agents = [CODEX, FAR];
  assert.deepEqual(editHint({ agents: [CODEX], text: "@codex fix", initial: "fix" }),
    { text: "Saving sends this to Codex on this computer.", warn: false });
  assert.deepEqual(editHint({ agents: [FAR], text: "@codex fix", initial: "fix" }),
    { text: "Saving sends this to Codex on TIM-PC.", warn: false });
  assert.deepEqual(editHint({ agents, text: "@codex fix", initial: "fix", refused: "closed" }),
    { text: "Reopen this thread to ask Codex again.", warn: true });
  assert.deepEqual(editHint({ agents, text: "@codex fix", initial: "fix", refused: "not_newest" }),
    { text: "Reply to ask Codex again: only the newest comment is sent.", warn: true });
  assert.equal(editHint({ agents, text: "@codex fix it", initial: "@codex fix" }), null, "it named it before");
  assert.equal(editHint({ agents: [], text: "@codex fix" }), null);
});
