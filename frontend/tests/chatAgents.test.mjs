// The agents the chat, the comment fields and Settings list, and the
// picker's rules (src/chat/agents.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  GAMMA, agentChoice, agentKind, agentPeople, asksNewChat, chatAgents, computerAgents, farAgents, gammaAgent, localAgent, madeBy,
  commentAgents, newChatQuestion, pickAgent, stampLabel,
} from "../src/chat/agents.js";
import { blockAgent } from "../src/shared/model/blockModel.js";
import { readAccountComputers } from "../src/vault/vaultState.js";

const models = [{ id: "gpt-5.5", label: "GPT-5.5", efforts: ["low", "high"], effort: "high", default: true },
  { id: "gpt-mini", label: "GPT Mini", efforts: [], effort: null, default: false }];
const found = (over = {}) => ({ name: "codex", label: "Codex", found: true, version: "0.167.0", ready: true, problem: "", code: "",
  models, enabled: true, runs: "never", mine: true, ...over });
const listing = (...agents) => ({ here: true, computer: "TIM-PC", agents, dirs: [] });
const codexThere = { name: "codex", label: "Codex", found: true, ready: true, version: "0.167.0", code: "", models, enabled: true, runs: "ask" };
const pc = (over = {}) => ({ id: "c1", name: "TIM-PC", online: true, since: null,
  vault: { kind: "folder", name: "cavity", paused: false, work: true }, assistants: [codexThere], dirs: [], ...over });
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

test("the page's computers' agents: each agent on of each computer, a work folder's keeper's with its folder", () => {
  const [agent] = farAgents([pc()]);
  assert.deepEqual([agent.id, agent.name, agent.label, agent.where, agent.ready, agent.folder, agent.paused, agent.keeps, agent.owner,
    agent.remote], ["computer:c1:codex", "codex", "Codex", "TIM-PC", true, "cavity", false, true, true, true]);
  const claude = { ...codexThere, name: "claude", label: "Claude Code" };
  assert.deepEqual(farAgents([pc({ id: "c0", name: "LAPTOP", vault: null, assistants: [codexThere, claude] }), pc()])
    .map((a) => [a.id, a.folder || "", !!a.keeps]),
  [["computer:c0:codex", "", false], ["computer:c0:claude", "", false], ["computer:c1:codex", "cavity", true]],
  "a computer that keeps no page of it, every agent on there: the server's order");
  assert.deepEqual(farAgents([pc({ vault: { kind: "file", name: "x.pdf", paused: false, work: false } })]).map((a) => a.keeps), [undefined],
    "a vault of no work folder keeps nothing its agents work in");
  assert.deepEqual(farAgents([pc({ assistants: [{ ...codexThere, enabled: false }] })]), [], "off for this account there");
  assert.deepEqual(farAgents([pc({ assistants: [{ ...codexThere, found: false, ready: false, code: "missing" }] })]), [], "not there");
  assert.deepEqual(farAgents([]), []);
  assert.deepEqual(farAgents(undefined), []);
  const away = farAgents([pc({ online: false, since: "2026-11-13T14:02:00Z" })])[0];
  assert.deepEqual([away.online, away.ready, away.since], [false, null, "2026-11-13T14:02:00Z"], "offline: listed offline");
  assert.equal(farAgents([pc({ vault: { kind: "folder", name: "cavity", paused: true, work: true } })])[0].paused, true);
});

test("the chat's list: Gamma AI, the keepers' agents, the agents here the account turned on, the other computers'", () => {
  const ids = (list) => list.map((a) => `${a.id}${a.where ? `@${a.where}` : ""}`);
  const here = listing(found(), found({ name: "claude", label: "Claude Code", enabled: false, mine: false }));
  assert.deepEqual(ids(chatAgents({ aiInfo: aiOn, models: aiModels, local: here })), ["gamma", "codex"]);
  assert.deepEqual(ids(chatAgents({ local: listing(found(), found({ name: "claude", label: "Claude Code" })) })), ["gamma", "codex", "claude"]);
  assert.deepEqual(ids(chatAgents({ local: listing(found({ mine: false })) })), ["gamma"], "another account's agent");
  assert.deepEqual(ids(chatAgents({ local: here, computers: [pc()] })), ["gamma", "computer:c1:codex@TIM-PC", "codex"],
    "the page's computer keeps its work folder: its agent first");
  assert.deepEqual(ids(chatAgents({ local: here, computers: [pc({ vault: null })] })), ["gamma", "codex", "computer:c1:codex@TIM-PC"],
    "a page of no synced folder of it: after this computer's");
  const account = { computers: [{ id: "c1", name: "TIM-PC", online: true, since: null, assistants: [codexThere], asks: false, dirs: [] },
    { id: "c9", name: "ALICE-PC", online: true, since: null, assistants: [codexThere], asks: false, dirs: [], lent_by: "alice" }] };
  assert.deepEqual(ids(chatAgents({ local: here, computers: readAccountComputers(account) })), ["gamma", "codex", "computer:c1:codex@TIM-PC"],
    "no page open: the account's computers, after this computer's, without a lent one");
  assert.deepEqual(readAccountComputers({}), []);
  assert.deepEqual(ids(chatAgents({ local: here, readOnly: true })), ["gamma"], "a viewer");
  assert.deepEqual(ids(chatAgents()), ["gamma"]);
  assert.deepEqual(ids(chatAgents({ local: { here: false, computer: "", agents: [], dirs: [] } })), ["gamma"], "a server that runs none");
});

test("the agent shown: the one picked while it is listed, else Gamma AI, else an agent that can answer", () => {
  const list = chatAgents({ aiInfo: aiOn, models: aiModels, local: listing(found()) });
  assert.equal(pickAgent(list, "codex").id, "codex");
  assert.equal(pickAgent(list, "").id, "gamma");
  assert.equal(pickAgent(list, "claude").id, "gamma", "picked once, turned off since");
  const far = chatAgents({ aiInfo: aiOn, local: listing(found({ id: "computer:here1:codex" })), computers: [pc({ vault: null })] });
  assert.equal(pickAgent(far, "computer:c1:codex").where, "TIM-PC", "another computer's, by its id");
  assert.equal(pickAgent(far, "computer:here1:codex").id, "codex", "this computer's, by the id the server names it with");
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
  assert.equal(agentChoice(farAgents([pc()])[0]), "Codex on TIM-PC");
});

test("the agents a comment can be sent to: the page's computer's, else the account's here, each by its id", () => {
  const both = chatAgents({ local: listing(found({ id: "computer:here1:codex" }),
    found({ name: "claude", label: "Claude Code", id: "computer:here1:claude" })) });
  assert.deepEqual(commentAgents(both), [
    { id: "computer:here1:codex", name: "codex", label: "Codex", ready: true, problem: "" },
    { id: "computer:here1:claude", name: "claude", label: "Claude Code", ready: true, problem: "" }], "Gamma AI is none");
  assert.deepEqual(commentAgents(chatAgents({ computers: [pc()] })),
    [{ id: "computer:c1:codex", name: "codex", label: "Codex", ready: true, problem: "", computer: "TIM-PC", online: true }]);
  assert.deepEqual(commentAgents(chatAgents({ local: listing(found()) })), [], "an agent whose id is not known yet");
  assert.deepEqual(commentAgents(chatAgents()), [], "no agent on");
});

test("who made what an agent made: its kind by its id, its name, its computer and its person", () => {
  assert.deepEqual(["", GAMMA, "computer:PC:codex", "computer::claude", "3f9a"].map(agentKind),
    ["", "gamma", "computer", "computer", "connection"]);
  assert.equal(madeBy({ name: "Codex", on: "TIM-PC", person: "tim" }), "Codex on TIM-PC, for tim");
  assert.equal(madeBy({ id: GAMMA, name: "Gamma AI", person: "tim" }), "Gamma AI, for tim");
  assert.equal(madeBy({ name: "Claude Code" }), "Claude Code", "no computer, no person");
  // a block's stamp (gamma/agents.py stamp), read off its properties
  const stamp = { id: "computer:PC:codex", name: "Codex", on: "TIM-PC", for: "tim" };
  assert.equal(stampLabel(blockAgent({ properties: { agent: stamp } })), "Codex on TIM-PC, for tim");
  assert.equal(stampLabel({ id: "tok1", name: "Claude Code", for: "tim" }), "Claude Code, for tim");
  for (const props of [{}, { agent: "Codex" }, { agent: { id: "x", name: "" } }, null]) {
    assert.equal(blockAgent({ properties: props }), null, JSON.stringify(props));
  }
  assert.equal(stampLabel(null), "");
  // a co-author's turn: its owner said too, and kept in the stamp
  assert.equal(madeBy({ name: "Codex", on: "TIM-PC", person: "bob", owner: "alice" }), "alice's Codex on TIM-PC, for bob");
  assert.equal(stampLabel({ ...stamp, for: "bob", owner: "alice" }), "alice's Codex on TIM-PC, for bob");
});

test("an agent another account lends: offered to the person it serves as theirs to use, its owner named", () => {
  const lent = localAgent(found({ id: "computer:here1:codex", mine: false, lent: { by: "alice" }, people: [{ account: "x" }] }));
  assert.deepEqual([lent.owner, lent.lentBy, lent.people], [true, "alice", []], "whom it serves is its owner's alone");
  assert.deepEqual(chatAgents({ local: listing(found({ mine: false, lent: { by: "alice" } })) }).map((a) => a.id), [GAMMA, "codex"]);
  assert.deepEqual(chatAgents({ local: listing(found({ mine: false, lent: null })) }).map((a) => a.id), [GAMMA], "another's, not lent");
  const mine = localAgent(found({ people: [{ agent: "codex", server: "", workspace_id: "w1", account: "u_bob", username: "bob" }] }));
  assert.deepEqual([mine.lentBy, mine.people.length], ["", 1]);
  // another account's computer that serves this one, its agent alone
  const [far] = farAgents([pc({ lent_by: "alice", assistants: [{ ...codexThere, runs: "never" }] })]);
  assert.deepEqual([far.owner, far.lentBy, far.where], [true, "alice", "TIM-PC"]);
  assert.deepEqual(commentAgents(chatAgents({ computers: [pc({ lent_by: "alice" })] })).map((a) => a.id), ["computer:c1:codex"]);
});

test("whom an agent serves and whom it may still: by workspace, its server named for another one", () => {
  const places = [
    { server: "", workspace_id: "w1", name: "Lab", origin: "", people: [{ id: "u_bob", username: "bob", role: "editor" }, { id: "u_eve", username: "eve", role: "owner" }] },
    { server: "https://nas.example", workspace_id: "w9", name: "Group", origin: "https://nas.example", people: [{ id: "u_bob", username: "bob", role: "editor" }] }];
  const people = [{ agent: "codex", server: "", workspace_id: "w1", account: "u_bob", username: "bob" },
    { agent: "codex", server: "https://far.example", workspace_id: "w7", account: "u_kim", username: "kim" }];
  const { named, offered } = agentPeople(people, places);
  assert.deepEqual(named.map((p) => [p.username, p.where]), [["bob", "Lab"], ["kim", "https://far.example"]],
    "a place not read is named by its server");
  assert.deepEqual(offered.map((o) => o.label), ["eve in Lab", "bob in Group on https://nas.example"]);
  assert.deepEqual(agentPeople([], null), { named: [], offered: [] });
});
