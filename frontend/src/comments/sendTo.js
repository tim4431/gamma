// A comment field's *Send to {agent}* tick (docs/dev/comments.md "Sent to an
// agent", "The interaction" of docs/research/agents-as-participants-plan.md):
// which agent it names, whether it starts ticked, and what typing `@codex`
// does to it. Ticked, the comment is stored and is also the instruction of
// a turn of that agent (POST /api/pages/{id}/comments `to`, the agent's id);
// unticked, it is a comment among people. Pure, so node tests it
// (tests/commentSendTo.test.mjs).
//
// `agents` are the ones a comment on the page can be sent to
// (chat/agents.js commentAgents: [{id, name, label, computer?, online?,
// ready, problem}]); `thread` the thread a reply goes in (null for a new
// one), its comments as comments.js holds them.
import { t } from "../shared/i18n/i18n.js";
import { agentOffline, peopleIn } from "../editor/mentions.js";

// The newest of the thread's comments that a computer's agent wrote, null
// for none.
const agentsLast = (thread) => (thread?.comments || []).findLast((c) => !c.deleted && c.agent_kind === "computer") || null;

// Whether the tick starts ticked: in a thread whose newest comment is a
// computer's agent's (it asked something, or answered), else as the person
// left it last (`remembered`, the account's preference).
export function startsTicked(thread, remembered = false) {
  const newest = (thread?.comments || []).findLast((c) => !c.deleted) || null;
  return newest?.agent_kind === "computer" || !!remembered;
}

// The agent the tick names: the one the person chose in its menu
// (`chosen`, an agent's id) while it is listed, else the one that wrote in
// the thread last, else the one picked in the page's chat (`picked`, its
// name: chat/computer/chatSide.js), else the first; null for none.
export function tickAgent(agents, { thread = null, picked = "", chosen = "" } = {}) {
  const list = agents || [];
  const by = (pred) => list.find(pred) || null;
  const last = agentsLast(thread);
  return (chosen && by((a) => a.id === chosen)) || (last && by((a) => a.id === last.agent_id))
    || (picked && by((a) => a.name === picked)) || list[0] || null;
}

// The agent `@name` names in `text` outside code, newly (not in `initial`,
// an edit's text before): typing it ticks the box for that agent. Null for
// none.
export function namedAgent(agents, text, initial = "") {
  const names = peopleIn(text);
  const before = peopleIn(initial);
  return (agents || []).find((a) => names.has(a.name) && !before.has(a.name)) || null;
}

// What the field says under the text while it is sent to `agent`: why it
// cannot answer now (its problem), or that its computer is offline (the
// server refuses it then); null while it can.
export function sendNote(agent) {
  if (!agent) return null;
  if (agent.ready === false && agent.problem) return { text: agent.problem, warn: true };
  const away = agentOffline(agent);
  return away ? { text: away, warn: true } : null;
}

// The placeholder of a field ticked for `agent`, or the field's own.
export const tickedPlaceholder = (agent, placeholder) => (agent ? t("Ask {agent} to edit", { agent: agent.label }) : placeholder);

// An edit's field, which has no tick: saving a text that newly names an
// agent (`namedAgent`) sends it there, which it says, or why the server
// will not (`refused`, comments.js turnRefusal's codes: "closed" in a
// resolved thread, "not_newest" for an older comment). Null for nothing to
// say.
export function editHint({ agents, text, initial = "", refused = "" }) {
  const agent = namedAgent(agents, text, initial);
  if (!agent) return null;
  if (refused === "closed") return { text: t("Reopen this thread to ask {agent} again.", { agent: agent.label }), warn: true };
  if (refused === "not_newest") return { text: t("Reply to ask {agent} again: only the newest comment is sent.", { agent: agent.label }), warn: true };
  const note = sendNote(agent);
  if (note) return note;
  return {
    text: agent.computer ? t("Saving sends this to {agent} on {computer}.", { agent: agent.label, computer: agent.computer })
      : t("Saving sends this to {agent} on this computer.", { agent: agent.label }),
    warn: false,
  };
}
