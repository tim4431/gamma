// The composer's model chip, Gamma AI's and a computer agent's alike
// (docs/dev/ai.md "The picker"): with more than one agent to talk to, its
// menu lists them first (chat/agents.js chatAgents), each by its icon and
// where it runs, then the picked one's models and what else it takes
// (`sections`: the reasoning effort, the speed). Picking another agent is
// the chat's to answer (`onAgent`, which may ask first: a conversation keeps
// its agent).
import { MenuSelect } from "../shared/ui/Menus";
import { BotIcon, LinkIcon, SparklesIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import { GAMMA, agentChoice } from "./agents.js";

// An agent's icon by its name, in the chip's menu and History's rows: Gamma
// AI's sparkles, a computer agent's bot.
export const agentIcon = (name) => (name === GAMMA ? SparklesIcon : BotIcon);

// The same by an agent's kind (agents.js agentKind), for what an agent
// wrote or made (a comment's avatar, a highlight's row): a connection's is
// the link Settings → Integrations draws it with.
const KIND_ICONS = { gamma: SparklesIcon, computer: BotIcon, connection: LinkIcon };
export const agentKindIcon = (kind) => KIND_ICONS[kind] || BotIcon;

// `agents` the chat's, `agent` the one shown; `models` [[id, label]] the
// picked agent's, `model` the one chosen, `onModel(id)`; `label` the
// trigger's name, `display` what it reads.
export default function AgentChip({ agents, agent, onAgent, models, model, onModel, sections = [], label, display, guide }) {
  const several = (agents || []).length > 1;
  const modelSection = models.length ? { label: t("Model"), value: model, onChange: onModel, options: models } : null;
  return (
    <span className="chatModelChip" data-guide={guide}>
      <MenuSelect
        up
        label={label}
        heading={several ? t("Agent") : t("Model")}
        value={several ? agent.id : model}
        onChange={several ? onAgent : onModel}
        display={display}
        options={several ? agents.map((a) => [a.id, agentChoice(a), agentIcon(a.id)]) : models}
        sections={several ? [modelSection, ...sections].filter(Boolean) : sections}
      />
    </span>
  );
}
