// Who answers in the chat (docs/dev/ai.md "The picker"): with more than one
// agent to talk to (chat/agents.js chatAgents), the chat window's header
// leads its buttons with a pill naming the one picked, its icon before its
// name ("Codex on TIM-PC"). Its menu lists them, each by its icon and
// where it runs, with a hint on one that cannot answer now ("offline",
// "not ready": computerChat.js agentHint), and ends with Manage agents…
// (Settings → AI → Local agents). Picking another agent is the chat's to
// answer (`onAgent`, which may ask first: a conversation keeps its agent).
// How the picked one answers (its model, effort and speed) is the
// composer's model chip (chat/ModelChip.jsx).
import { MenuSelect } from "../shared/ui/Menus";
import { BotIcon, LinkIcon, SettingsIcon, SparklesIcon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";
import { GAMMA, agentChoice } from "./agents.js";
import { agentHint } from "./computer/computerChat.js";

// An agent's icon by its name, in the picker and History's rows: Gamma
// AI's sparkles, a computer agent's bot.
export const agentIcon = (name) => (name === GAMMA ? SparklesIcon : BotIcon);

// The same by an agent's kind (agents.js agentKind), for what an agent
// wrote or made (a comment's avatar, a highlight's row): a connection's is
// the link Settings → Integrations draws it with.
const KIND_ICONS = { gamma: SparklesIcon, computer: BotIcon, connection: LinkIcon };
export const agentKindIcon = (kind) => KIND_ICONS[kind] || BotIcon;

// An agent's row of the menu: its icon, where it runs, and the hint of one
// that cannot answer now, whose whole sentence is the row's title (and so
// what a screen reader adds to its name).
function agentOption(a) {
  const hint = agentHint(a);
  const trailing = hint ? <span className={`ctxMenuHint${hint.problem ? " agentProblem" : ""}`} aria-hidden="true">{hint.text}</span> : null;
  return [a.id, agentChoice(a), agentIcon(a.id), trailing, hint?.title];
}

// `agents` the chat's, `agent` the one shown, `onAgent(id)` a pick,
// `onManage` Settings → AI → Local agents (null where Settings can't open).
export default function AgentPicker({ agents, agent, onAgent, onManage = null }) {
  const manage = onManage ? [{
    icon: SettingsIcon, label: T("Manage agents…"), title: T("Turn the agents on your computers on or off"), onClick: onManage,
  }] : [];
  return (
    <span className="chatAgentPicker" data-guide="chat.agent">
      <MenuSelect ghost names label={t("Agent: {agent}", { agent: agentChoice(agent) })} heading={t("Agent")}
        value={agent.id} onChange={onAgent} options={agents.map(agentOption)} actions={manage} />
    </span>
  );
}
