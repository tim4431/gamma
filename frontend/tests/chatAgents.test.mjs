// The agents the chat, the comment fields and Settings list, and the
// picker's rules (src/chat/agents.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  GAMMA, agentChoice, asksNewChat, chatAgents, computerAgents, gammaAgent, keeperAgent, localAgent, mentionAgent, newChatQuestion,
  pickAgent,
} from "../src/chat/agents.js";

const models = [{ id: "gpt-5.5", label: "GPT-5.5", efforts: ["low", "high"], effort: "high", default: true },
  { id: "gpt-mini", label: "GPT Mini", efforts: [], effort: null, default: false }];
const found = (over = {}) => ({ name: "codex", label: "Codex", found: true, version: "0.167.0", ready: true, problem: "", code: "",
  models, enabled: true, runs: "never", mine: true, ...over });
const listing = (...agents) => ({ here: true, computer: "TIM-PC", agents, dirs: [] });
const codexThere = { name: "codex", label: "Codex", found: true, ready: true, version: "0.167.0", code: "", models, enabled: true, runs: "ask" };
const pc = (over = {}) => ({ id: "c1", name: "TIM-PC", online: true, since: null,
  vault: { kind: "folder", name: "cavity", paused: false, agent: { name: "codex", label: "Codex" } }, assistants: [codexThere], ...over });
const aiOn = { enabled: true, efforts: ["low", "high"] };
const aiModels = [{ id: "demo:model", provider: "demo", provider_name: "Demo", model: "model" }];

test("Gamma AI: its connection's models, ready once the AI info says a service is connected", () => {
  const gamma = gammaAgent(aiOn, aiModels);
  assert.deepEqual([gamma.id, gamma.kind, gamma.label, gamma.ready, gamma.problem, gamma.connection, gamma.where],
    [GAMMA, "gamma", "Gamma AI", true, "", "Demo", ""]);
  assert.deepEqual(gamma.models, [{ id: "demo:model", label: "model" }]);
  assert.equal(gammaAgent(null).ready, null, "not known yet");
  assert.deepEqual([gammaAgent({ enabled: false }).ready, gammaAgent({ enabled: false }).problem], [false, "No AI service is connected."]);
});

test("an agent of this computer: its version, its default model's efforts, how it stands by its code", () => {
  const codex = localAgent(found());
  assert.deepEqual([codex.id, codex.kind, codex.label, codex.where, codex.version, codex.ready, codex.problem, codex.owner, codex.enabled,
    codex.runs, codex.remote], ["codex", "computer", "Codex", "", "0.167.0", true, "", true, true, "never", false]);
  assert.deepEqual(codex.efforts, ["low", "high"]);
  assert.equal(localAgent(found({ ready: null, code: "checking" })).ready, null);
  assert.equal(localAgent(found({ ready: false, code: "signed_out", problem: "Codex is not signed in on this computer: run `codex login` in a terminal." })).problem,
    "Codex is not signed in on this computer: run `codex login` in a terminal.", "this computer's own words, which name the command");
  assert.equal(localAgent(found({ ready: false, code: "missing", problem: "" })).problem, "Codex is not on this computer.");
  assert.equal(localAgent(found({ name: "claude", label: "", ready: false, code: "signed_out", problem: "" })).problem,
    "Claude Code is not signed in on this computer.", "no label: Gamma's own for it");
  assert.deepEqual([localAgent(found({ mine: false })).owner, localAgent(found({ runs: "ask" })).runs], [false, "ask"]);
});

test("another computer's agents, as it announced them: those found, by their codes while it is online", () => {
  const [codex] = computerAgents(pc());
  assert.deepEqual([codex.where, codex.ready, codex.owner, codex.runs, codex.remote, codex.online], ["TIM-PC", true, true, "ask", true, true]);
  const as = (said) => computerAgents(pc({ assistants: [{ ...codexThere, ...said }] }))[0];
  assert.equal(as({ ready: false, code: "signed_out" }).problem, "Codex is not signed in on TIM-PC.");
  assert.equal(as({ ready: false, code: "not_ready" }).problem, "Codex is not ready on TIM-PC.");
  assert.equal(as({ ready: null, code: "checking" }).ready, null);
  assert.equal(as({ enabled: false }).owner, false, "on there for another account, or off");
  assert.deepEqual(computerAgents(pc({ assistants: [{ ...codexThere, found: false, code: "missing" }] })), [], "not found there");
  const away = computerAgents(pc({ online: false, since: "2026-11-13T14:02:00Z" }))[0];
  assert.deepEqual([away.ready, away.problem, away.since], [null, "", "2026-11-13T14:02:00Z"], "offline: nothing known of it");
  assert.deepEqual(computerAgents({ name: "X" }), []);
});

test("the page's computer agent: the first computer's whose work folder of the page's server has one", () => {
  const agent = keeperAgent([pc()]);
  assert.deepEqual([agent.name, agent.label, agent.where, agent.ready, agent.folder, agent.paused, agent.owner, agent.remote],
    ["codex", "Codex", "TIM-PC", true, "cavity", false, true, true]);
  assert.equal(keeperAgent([pc({ id: "c0", name: "LAPTOP", vault: { kind: "folder", name: "x", paused: false } }), pc()]).where, "TIM-PC",
    "the first with an agent");
  assert.equal(keeperAgent([pc({ vault: { kind: "folder", name: "cavity", paused: false, agent: null } })]), null, "no agent on");
  assert.equal(keeperAgent([]), null);
  assert.equal(keeperAgent(undefined), null);
  assert.equal(keeperAgent([pc({ assistants: [] })]).ready, null, "not announced: not known");
  assert.equal(keeperAgent([pc({ assistants: [{ ...codexThere, found: false, ready: false, code: "missing" }] })]).problem,
    "Codex is not on TIM-PC.");
  assert.equal(keeperAgent([pc({ vault: { kind: "folder", name: "cavity", paused: true, agent: { name: "codex" } } })]).paused, true);
});

test("the chat's list: Gamma AI, then the agents here the account turned on, or the page's computer's", () => {
  const ids = (list) => list.map((a) => `${a.id}${a.where ? `@${a.where}` : ""}`);
  const here = listing(found(), found({ name: "claude", label: "Claude Code", enabled: false, mine: false }));
  assert.deepEqual(ids(chatAgents({ aiInfo: aiOn, models: aiModels, local: here })), ["gamma", "codex"]);
  assert.deepEqual(ids(chatAgents({ local: listing(found(), found({ name: "claude", label: "Claude Code" })) })), ["gamma", "codex", "claude"]);
  assert.deepEqual(ids(chatAgents({ local: listing(found({ mine: false })) })), ["gamma"], "another account's agent");
  assert.deepEqual(ids(chatAgents({ local: here, computers: [pc()] })), ["gamma", "codex@TIM-PC"],
    "the page's computer keeps its work folder: the server binds the chat to it");
  assert.deepEqual(ids(chatAgents({ local: here, readOnly: true })), ["gamma"], "a viewer");
  assert.deepEqual(ids(chatAgents()), ["gamma"]);
  assert.deepEqual(ids(chatAgents({ local: { here: false, computer: "", agents: [], dirs: [] } })), ["gamma"], "a server that runs none");
});

test("the agent shown: the one picked while it is listed, else Gamma AI, else an agent that can answer", () => {
  const list = chatAgents({ aiInfo: aiOn, models: aiModels, local: listing(found()) });
  assert.equal(pickAgent(list, "codex").id, "codex");
  assert.equal(pickAgent(list, "").id, "gamma");
  assert.equal(pickAgent(list, "claude").id, "gamma", "picked once, turned off since");
  const noAi = chatAgents({ aiInfo: { enabled: false }, local: listing(found()) });
  assert.equal(pickAgent(noAi, "").id, "codex", "no AI service: the agent here");
  assert.equal(pickAgent(noAi, "gamma").id, "gamma", "picked all the same");
  assert.equal(pickAgent(chatAgents({ aiInfo: { enabled: false }, local: listing(found({ ready: false, code: "missing" })) }), "").id, "gamma");
  assert.equal(pickAgent(chatAgents({ aiInfo: null, local: listing(found()) }), "").id, "gamma", "not known yet: Gamma AI");
  assert.equal(pickAgent([], ""), null);
});

test("another agent picked in a conversation with messages asks first, naming it", () => {
  const [gamma, codex] = chatAgents({ local: listing(found()) });
  assert.equal(asksNewChat(gamma, codex, true), true);
  assert.equal(asksNewChat(gamma, codex, false), false, "nothing said yet");
  assert.equal(asksNewChat(codex, codex, true), false, "the same agent");
  assert.deepEqual(newChatQuestion(codex), {
    title: "Start a new chat with Codex?",
    message: "A conversation keeps the agent it began with. This one stays in Chat history.",
  });
  assert.equal(agentChoice(codex), "Codex");
  assert.equal(agentChoice(keeperAgent([pc()])), "Codex on TIM-PC");
});

test("the agent a comment names: the page's computer's, else the account's first here on a work folder's file", () => {
  const work = { vault: { work_folder: true, mine: false } };
  const both = chatAgents({ local: listing(found(), found({ name: "claude", label: "Claude Code" })) });
  assert.deepEqual(mentionAgent(both, work), { name: "codex", label: "Codex", ready: true, problem: "" },
    "the first on, which its Sends run with, whoever made the vault");
  assert.equal(mentionAgent(both, { vault: { work_folder: false } }), null, "a page of no work folder");
  assert.equal(mentionAgent(both, null), null);
  assert.deepEqual(mentionAgent(chatAgents({ computers: [pc()] }), null),
    { name: "codex", label: "Codex", ready: true, problem: "", computer: "TIM-PC", online: true });
  assert.equal(mentionAgent(chatAgents(), work), null, "no agent on");
});
