// The agents a person talks to (docs/dev/ai.md "The picker",
// docs/dev/local_agents.md "The client"): one list for the chat's picker,
// the comment field's @ menu and Settings → AI → Agents. Gamma AI first,
// then the computers' agents: those of the account's computers shown to
// the page's server (the page's computers, GET /api/pages/{id}/computer,
// step 4.2 of docs/research/agents-as-participants-plan.md), each
// computer's that keeps the page's work folder first, then on the desktop
// app's own server those this computer runs (GET /api/local-agents, what
// the account turned on), then the other computers'. Each agent is
//   {id, kind: "gamma" | "computer", name, label, where, models, efforts,
//    ready, problem, owner, ...}
// `id` what the chat's routes name: Gamma AI's "gamma", this computer's
// agent by its name ("codex", "claude"), another computer's by its id,
// `agentId` a computer's agent's id as every agent has one
// (gamma/agents.py: "computer:<computer>:<name>", what a comment sent to it
// names; "" while it is not known),
// `where` the computer it runs on by name ("" for this one, and for Gamma
// AI), `models` [{id, label, efforts, effort, default}], `efforts` the
// default model's, `ready` null while it is not known, `problem` why it
// cannot run ("" while it can), `owner` whether the account may use it (it
// turned it on, or its owner lets it: a co-author's, decision 13 of
// docs/research/agents-as-participants-plan.md), `lentBy` that owner's
// username for a co-author ("" for one's own). A computer's agent also
// carries `code` (catalog.code), `version`, `found`, `enabled`, `runs`, and
// for another computer's `remote: true`, `online` and `since`, and on a
// page of the work folder that computer keeps `folder` and `paused`. Pure:
// tests/chatAgents.test.mjs.
import { T, t } from "../shared/i18n/i18n.js";
import { modelLabel } from "./modelPrefs.js";
import { readComputers } from "../vault/vaultState.js";

export const GAMMA = "gamma";
const LABELS = { codex: T("Codex"), claude: T("Claude Code") };

// An agent's name as people read it: the label it came with, else Gamma's
// own for it, else its name.
export const agentLabel = (name, label = "") => String(label || (LABELS[name] ? t(LABELS[name]) : name) || "");

// The model a computer's agent runs unless another is picked: its default,
// else its first; null for none.
export const defaultModel = (models) => (models || []).find((m) => m.default) || (models || [])[0] || null;

// How a computer's agent stands, by its code (never another computer's
// English problem): {ready, problem}. On this computer (`where` "") a
// problem this computer worded itself is shown as it came (`said`, which
// names the command that mends it).
function stateOf(code, ready, label, where, said = "") {
  if (code === "checking" || ready == null) return { ready: null, problem: "" };
  if (!code && ready) return { ready: true, problem: "" };
  const words = { agent: label, computer: where };
  if (!where && said) return { ready: false, problem: t(said) };
  if (code === "missing") return { ready: false, problem: where ? t("{agent} is not on {computer}.", words) : t("{agent} is not on this computer.", words) };
  if (code === "signed_out") {
    return { ready: false, problem: where ? t("{agent} is not signed in on {computer}.", words) : t("{agent} is not signed in on this computer.", words) };
  }
  return { ready: false, problem: where ? t("{agent} is not ready on {computer}.", words) : t("{agent} is not ready on this computer.", words) };
}

// Gamma AI, as the AI info says it (`aiInfo`, GET /api/ai/models; `models`
// the chat's connection's, providerModels): `connection` the connection's
// name, `ready` null until the info is in.
export function gammaAgent(aiInfo, models = []) {
  const ready = aiInfo ? !!aiInfo.enabled : null;
  return {
    id: GAMMA, kind: "gamma", name: GAMMA, label: t("Gamma AI"), where: "",
    models: (models || []).map((m) => ({ id: m.id, label: modelLabel(m) })), efforts: aiInfo?.efforts || [],
    connection: String(models?.[0]?.provider_name || ""), ready,
    problem: ready === false ? t("No AI service is connected.") : "", owner: true,
  };
}

// An agent this computer runs, as GET /api/local-agents lists it (`found`:
// {id, name, label, found, version, ready, problem, code, models, enabled,
// runs, mine, lent: {by} | null, people}).
export function localAgent(found) {
  const label = agentLabel(found.name, found.label);
  const models = Array.isArray(found.models) ? found.models : [];
  return {
    id: found.name, agentId: String(found.id || ""), kind: "computer", name: found.name, label, where: "", models,
    efforts: defaultModel(models)?.efforts || [], ...stateOf(found.code, found.ready, label, "", found.problem),
    owner: !!found.mine || !!found.lent, lentBy: found.mine ? "" : String(found.lent?.by || ""),
    code: found.code || "", version: String(found.version || ""), found: found.found,
    enabled: !!found.enabled, runs: found.runs === "ask" ? "ask" : "never", remote: false,
    people: found.mine && Array.isArray(found.people) ? found.people : [],
  };
}

// The agents a computer of the account's announced (a page's computers, or
// GET /api/computers: {id, name, online, since, assistants, lent_by?}),
// those it has found: `ready` and `problem` as it said them while it is
// online, null and "" while it is away; `enabled` whether it is on for this
// account there, which then may use it (`owner`); another account's
// computer whose agent serves this one (`lent_by`, its owner) lists that
// agent alone, `lentBy` its owner.
export function computerAgents(computer) {
  return assistantsOf(computer).filter((a) => a.found !== false && a.code !== "missing").map((a) => announced(a, computer));
}

const assistantsOf = (computer) => (Array.isArray(computer?.assistants) ? computer.assistants : [])
  .filter((a) => a && typeof a === "object" && typeof a.name === "string" && a.name);

// One assistant a computer announced, as an agent (computerAgents).
function announced(a, computer) {
  const online = computer?.online === true;
  const where = String(computer?.name || "");
  const label = agentLabel(a.name, a.label);
  const models = Array.isArray(a.models) ? a.models : [];
  const agentId = computer?.id ? `computer:${computer.id}:${a.name}` : "";
  return {
    id: agentId || a.name, agentId, kind: "computer", name: a.name, label,
    where, models, efforts: defaultModel(models)?.efforts || [],
    ...(online ? stateOf(a.code, a.ready, label, where) : { ready: null, problem: "" }),
    owner: !!a.enabled, lentBy: String(computer?.lent_by || ""), code: a.code || "", version: String(a.version || ""),
    found: a.found ?? null, enabled: !!a.enabled, runs: a.runs === "ask" ? "ask" : "never", remote: true, online,
    since: online ? null : computer.since || null,
  };
}

// The agents of the page's computers (the account's shown to the page's
// server, and another account's whose agent serves it: the server tells
// only them, in the order it gives a turn to them), each agent on for the
// account there (`enabled`) that the computer found, as it was announced;
// on a page of a work folder a computer keeps (its vault's `work`), its
// agents carry `folder` the work folder's name and `paused` whether it is
// paused there, and `keeps: true`. An offline computer's are listed
// offline (`online` false): the server queues nothing for one away.
export function farAgents(computers) {
  return readComputers({ computers }).flatMap((computer) => {
    const kept = computer.vault?.work ? computer.vault : null;
    return computerAgents(computer).filter((a) => a.enabled).map((a) => ({
      ...a, owner: true, ...(kept ? { folder: String(kept.name || ""), paused: !!kept.paused, keeps: true } : {}),
    }));
  });
}

// The chat's agents: Gamma AI, then the page's computers' agents that keep
// its work folder, then the agents this computer runs that the account
// turned on (`local`, GET /api/local-agents' answer, null while unread),
// then the other computers'. A viewer (`readOnly`) has Gamma AI alone.
export function chatAgents({ aiInfo = null, models = [], local = null, computers = [], readOnly = false } = {}) {
  const gamma = gammaAgent(aiInfo, models);
  if (readOnly) return [gamma];
  const far = farAgents(computers);
  const here = (local?.agents || []).map(localAgent).filter((a) => a.enabled && a.owner);
  return [gamma, ...far.filter((a) => a.keeps), ...here, ...far.filter((a) => !a.keeps)];
}

// The agent the chat shows: the one picked for the bucket (`wanted`, its
// id or a computer's agent's `agentId`, as the server names a
// conversation's agent: `agent_id`) while it is listed, else Gamma AI,
// unless it is known to be unable to answer (no AI service) and a
// computer's agent is not: then the first such.
export function pickAgent(agents, wanted = "") {
  const list = agents || [];
  const picked = wanted ? list.find((a) => a.id === wanted) || list.find((a) => a.agentId === wanted) : null;
  if (picked) return picked;
  const gamma = list.find((a) => a.kind === "gamma") || null;
  if (gamma && gamma.ready !== false) return gamma;
  return list.find((a) => a.kind === "computer" && a.ready !== false) || gamma || list[0] || null;
}

// Whether picking `next` in a conversation that has messages asks first:
// a conversation keeps the agent it began with, so another agent starts a
// new one (decision 10 of docs/research/agents-as-participants-plan.md).
export const asksNewChat = (current, next, hasMessages) => !!hasMessages && !!next && current?.id !== next.id;

// What the question before that says: {title, message}.
export function newChatQuestion(next) {
  return {
    title: t("Start a new chat with {agent}?", { agent: next.label }),
    message: t("A conversation keeps the agent it began with. This one stays in Chat history."),
  };
}

// An agent as the picker names it: "Codex", "Codex on TIM-PC".
export const agentChoice = (agent) => (agent?.where ? t("{agent} on {computer}", { agent: agent.label, computer: agent.where }) : agent?.label || "");

// The kind of agent an id names (gamma/agents.py): Gamma AI's "gamma", a
// computer's agent's "computer:<computer>:<name>", else the integration
// token's id of a connection; "" for none (a person).
export const agentKind = (id) => (!id ? "" : id === GAMMA ? "gamma" : String(id).startsWith("computer:") ? "computer" : "connection");

// Who an agent that wrote or made something is, with the person it acted
// for: "Codex on TIM-PC, for tim", "Gamma AI, for tim", and for a
// co-author's turn its owner too, "alice's Codex on TIM-PC, for bob". `id`
// and `name` the agent's, `on` its computer ("" for none, or one no longer
// known), `person` the account's username, `owner` the agent's owner's
// where that is another account ("" otherwise). A comment's agent
// (comments.js) and a block's `properties.agent` (blockModel.js blockAgent)
// read alike.
export function madeBy({ id = "", name = "", on = "", person = "", owner = "" }) {
  const named = id === GAMMA ? t("Gamma AI") : name;
  const agent = owner ? t("{owner}'s {agent}", { owner, agent: named }) : named;
  const who = on ? t("{agent} on {computer}", { agent, computer: on }) : agent;
  return person ? t("{agent}, for {person}", { agent: who, person }) : who;
}

// The same of a block an agent made, from its stamp (`properties.agent`,
// blockModel.js blockAgent); "" for a person's block.
export const stampLabel = (stamp) => (typeof stamp?.name === "string" && stamp.name ? madeBy({
  id: stamp.id, name: stamp.name, on: stamp.on || "", person: stamp.for || "", owner: stamp.owner || "",
}) : "");

// The same of an agent the server names beside what it did (an Inbox
// item's `agent`, a thread's `resolved_agent`: {id, kind, name, on?,
// owner?}, gamma/agents.py Agent.shown), `person` the account it acted for
// ("" to leave it out); "" for none.
export const agentWords = (agent, person = "") => (typeof agent?.name === "string" && agent.name ? madeBy({
  id: agent.id || "", name: agent.name, on: agent.on || "", person, owner: agent.owner || "",
}) : "");

// The agents a comment on the page can be sent to (its *Send to* tick and
// its `@codex`, comments/sendTo.js): the chat's computer agents
// (chatAgents), each {id (its agentId), name, label, computer?, online?,
// ready, problem}, as editor/mentions.js and comments/comments.js read
// them, `computer` the other computer's name.
export function commentAgents(agents) {
  return (agents || []).filter((a) => a.kind === "computer" && a.agentId && a.owner).map((a) => ({
    id: a.agentId, name: a.name, label: a.label, ready: a.ready, problem: a.problem,
    ...(a.remote ? { computer: a.where, online: a.online } : {}),
  }));
}

// The people an agent of this computer serves for its owner (`people`, the
// listing's [{server, workspace_id, account, username}]) and whom it may
// still name, from the workspaces its owner edits with others (`places`, GET
// /api/local-agents/people: [{server, workspace_id, name, origin, people:
// [{id, username, role}], error}]): {named: [{key, server, workspace_id,
// account, username, where}], offered: [{key, place, person, label}]},
// `where` the workspace's name ("Lab", "Group on https://nas.example"),
// a place not read yet named by its server alone.
export function agentPeople(people, places) {
  const key = (server, ws, account) => `${server}\n${ws}\n${account}`;
  const placeWords = (place) => (place.origin ? t("{workspace} on {server}", { workspace: place.name, server: place.origin }) : place.name);
  const at = new Map((places || []).map((p) => [`${p.server}\n${p.workspace_id}`, p]));
  const named = (people || []).map((p) => {
    const place = at.get(`${p.server}\n${p.workspace_id}`);
    return { key: key(p.server, p.workspace_id, p.account), server: p.server, workspace_id: p.workspace_id, account: p.account,
      username: p.username, where: place ? placeWords(place) : p.server || t("this server") };
  });
  const taken = new Set(named.map((p) => p.key));
  const offered = (places || []).flatMap((place) => (place.people || [])
    .filter((person) => !taken.has(key(place.server, place.workspace_id, person.id)))
    .map((person) => ({ key: key(place.server, place.workspace_id, person.id), place, person,
      label: t("{person} in {workspace}", { person: person.username, workspace: placeWords(place) }) })));
  return { named, offered };
}

// What a computer agent's strip says of it until the chat's own read
// (GET /api/local-agents/chat's `computer`) lands: {agent, label, ready,
// problem, models, folder, paused}, and for another computer's agent
// {computer, online, since, remote: true} (chat/computer/computerChat.js).
export function stripFacts(agent) {
  if (!agent || agent.kind !== "computer") return null;
  const facts = {
    agent: agent.name, label: agent.label, ready: agent.ready, problem: agent.problem, models: agent.models,
    folder: agent.folder || "", paused: !!agent.paused,
  };
  return agent.remote ? { ...facts, computer: agent.where, online: agent.online, since: agent.since, remote: true } : facts;
}
