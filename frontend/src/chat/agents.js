// The agents a person talks to (docs/dev/ai.md "The picker",
// docs/dev/local_agents.md "The client"): one list for the chat's picker,
// the comment field's @ menu and Settings → AI → Agents. Gamma AI first,
// then the computer's agents: on the desktop app's own server those this
// computer runs (GET /api/local-agents, what the account turned on), and on
// a page of a work folder another computer keeps, that computer's agent
// there (the page's computers, GET /api/pages/{id}/computer), which the
// server binds the page's chat to. Each agent is
//   {id, kind: "gamma" | "computer", name, label, where, models, efforts,
//    ready, problem, owner, ...}
// `id` its name ("gamma", "codex", "claude": what the chat's routes name),
// `where` the computer it runs on by name ("" for this one, and for Gamma
// AI), `models` [{id, label, efforts, effort, default}], `efforts` the
// default model's, `ready` null while it is not known, `problem` why it
// cannot run ("" while it can), `owner` whether the account may use it (it
// turned it on). A computer's agent also carries `code` (catalog.code),
// `version`, `found`, `enabled`, `runs`, and for another computer's
// `remote: true`, `online` and `since`. Pure: tests/chatAgents.test.mjs.
import { T, t } from "../shared/i18n/i18n.js";
import { modelLabel } from "./modelPrefs.js";
import { readComputers, vaultAgent } from "../vault/vaultState.js";

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
// {name, label, found, version, ready, problem, code, models, enabled,
// runs, mine}).
export function localAgent(found) {
  const label = agentLabel(found.name, found.label);
  const models = Array.isArray(found.models) ? found.models : [];
  return {
    id: found.name, kind: "computer", name: found.name, label, where: "", models,
    efforts: defaultModel(models)?.efforts || [], ...stateOf(found.code, found.ready, label, "", found.problem),
    owner: !!found.mine, code: found.code || "", version: String(found.version || ""), found: found.found,
    enabled: !!found.enabled, runs: found.runs === "ask" ? "ask" : "never", remote: false,
  };
}

// The agents a computer of the account's announced (a page's computers, or
// GET /api/computers: {name, online, since, assistants}), those it has
// found: `ready` and `problem` as it said them while it is online, null
// and "" while it is away; `enabled` whether it is on for this account
// there, which then may use it (`owner`).
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
  return {
    id: a.name, kind: "computer", name: a.name, label, where, models, efforts: defaultModel(models)?.efforts || [],
    ...(online ? stateOf(a.code, a.ready, label, where) : { ready: null, problem: "" }),
    owner: !!a.enabled, code: a.code || "", version: String(a.version || ""), found: a.found ?? null,
    enabled: !!a.enabled, runs: a.runs === "ask" ? "ask" : "never", remote: true, online, since: online ? null : computer.since || null,
  };
}

// The agent of the first of the page's computers whose work folder of the
// page's server has one on (its vault's `agent`), which the page's chat,
// Sends and mentions run on there: the account's (the server tells only
// it), as its assistant there was announced (not announced: not known),
// with `folder` the work folder's name and `paused` whether it is paused
// there; null for none.
export function keeperAgent(computers) {
  const computer = readComputers({ computers }).find((c) => vaultAgent(c)) || null;
  if (!computer) return null;
  const named = vaultAgent(computer);
  const said = assistantsOf(computer).find((a) => a.name === named.name) || { name: named.name, ready: null, code: "checking" };
  return {
    ...announced(said, computer), label: named.label, owner: true, enabled: true,
    folder: String(computer.vault.name || ""), paused: !!computer.vault.paused,
  };
}

// The chat's agents: Gamma AI, then the page's computer's agent where
// another computer keeps the page's work folder (the server binds the chat
// to it), else the agents this computer runs that the account turned on
// (`local`, GET /api/local-agents' answer, null while unread). A viewer
// (`readOnly`) has Gamma AI alone.
export function chatAgents({ aiInfo = null, models = [], local = null, computers = [], readOnly = false } = {}) {
  const gamma = gammaAgent(aiInfo, models);
  if (readOnly) return [gamma];
  const far = keeperAgent(computers);
  const here = (local?.agents || []).map(localAgent).filter((a) => a.enabled && a.owner);
  return [gamma, ...(far ? [far] : here)];
}

// The agent the chat shows: the one picked for the bucket (`wanted`, an
// id) while it is listed, else Gamma AI, unless it is known to be unable
// to answer (no AI service) and a computer's agent is not: then the first
// such.
export function pickAgent(agents, wanted = "") {
  const list = agents || [];
  const picked = list.find((a) => a.id === wanted);
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
// for: "Codex on TIM-PC, for tim", "Gamma AI, for tim". `id` and `name` the
// agent's, `on` its computer ("" for none, or one no longer known),
// `person` the account's username. A comment's agent (comments.js) and a
// block's `properties.agent` (blockModel.js blockAgent) read alike.
export function madeBy({ id = "", name = "", on = "", person = "" }) {
  const agent = id === GAMMA ? t("Gamma AI") : name;
  const who = on ? t("{agent} on {computer}", { agent, computer: on }) : agent;
  return person ? t("{agent}, for {person}", { agent: who, person }) : who;
}

// The same of a block an agent made, from its stamp (`properties.agent`,
// blockModel.js blockAgent); "" for a person's block.
export const stampLabel = (stamp) => (typeof stamp?.name === "string" && stamp.name ? madeBy({ id: stamp.id, name: stamp.name, on: stamp.on || "", person: stamp.for || "" }) : "");

// The agent a comment on the page can name (`@codex`), which the comment
// then is a Send to: the computer's agent of the page's work folder that
// another computer keeps, else, on a file of a work folder kept here
// (`vaultState.vault.work_folder`), the first of the account's agents here
// (the one its Sends run with). {name, label, ready, problem, computer?,
// online?}, as editor/mentions.js and comments/comments.js read it; null
// for none.
export function mentionAgent(agents, vaultState) {
  const computers = (agents || []).filter((a) => a.kind === "computer");
  const far = computers.find((a) => a.remote);
  if (far) return { name: far.name, label: far.label, ready: far.ready, problem: far.problem, computer: far.where, online: far.online };
  const here = vaultState?.vault?.work_folder ? computers.find((a) => !a.remote) : null;
  return here ? { name: here.name, label: here.label, ready: here.ready, problem: here.problem } : null;
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
