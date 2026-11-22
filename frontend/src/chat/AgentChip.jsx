// The composer's model chip, Gamma AI's and a computer agent's alike
// (docs/dev/ai.md "The picker"): with more than one agent to talk to, its
// menu lists them first (chat/agents.js chatAgents), each by its icon and
// where it runs, with a hint on one that cannot answer now ("offline",
// "not ready": computerChat.js agentHint), then the picked one's models and
// what else it takes (`sections`: the reasoning effort, the speed, Gamma
// AI's and a computer agent's alike; where a
// computer's agent works is the bar's folder chip, computer/PlaceChip.jsx),
// and the chip reads the agent, its model and
// effort ("Codex on TIM-PC · GPT-5.5 · high"), the model giving way first.
// With Gamma AI alone it is the model chip it always was. Picking another
// agent is the chat's to answer (`onAgent`, which may ask first: a
// conversation keeps its agent). Gamma AI's models are the favorites and
// the one in use; "All models…" opens the menu of every connection's
// (ai/ModelMenu.jsx) above the chip.
import { useRef, useState } from "react";
import { MenuSelect } from "../shared/ui/Menus";
import { menuAnchor } from "../shared/ui/placement.js";
import { BotIcon, LinkIcon, SearchIcon, SparklesIcon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";
import { ModelMenuList } from "../ai/ModelMenu.jsx";
import { GAMMA, agentChoice } from "./agents.js";
import { agentHint } from "./computer/computerChat.js";

// An agent's icon by its name, in the chip's menu and History's rows: Gamma
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

// `agents` the chat's, `agent` the one shown; `models` [[id, label]] the
// picked agent's, `model` the one chosen, `onModel(id)`; `label`
// the trigger's name with Gamma AI alone, `text` the model's words
// ("GPT-5 · high"), `glyph` what follows them (the speed chosen, Gamma
// AI's or a computer agent's), `speed` the speed it stands for;
// `allModels` the model menu's props ({models, favorites, missing, value,
// onPick, onToggleFavorite}) for Gamma AI, null for a computer agent.
export default function AgentChip({ agents, agent, onAgent, models, model, onModel, sections = [], label, text = "",
  glyph = null, speed = "", guide, allModels = null }) {
  const chipRef = useRef(null);
  const [allAt, setAllAt] = useState(null);
  const openAll = () => setAllAt(menuAnchor(chipRef.current.getBoundingClientRect(), { side: "above" }));
  const several = (agents || []).length > 1;
  // The agent leads the chip whenever it is not Gamma AI alone.
  const name = several || agent?.id !== GAMMA ? agentChoice(agent) : "";
  const modelSection = models.length ? { label: t("Model"), value: model, onChange: onModel, options: models } : null;
  const named = !name ? label
    : speed ? t("Agent, model, reasoning effort and speed ({speed})", { speed }) : t("Agent, model and reasoning effort");
  return (
    <span className="chatModelChip" data-guide={guide} ref={chipRef}>
      <MenuSelect
        up
        label={named}
        heading={several ? t("Agent") : t("Model")}
        value={several ? agent.id : model}
        onChange={several ? onAgent : onModel}
        display={<>
          <span className="chatModelName">
            {name ? <span className="chatModelAgent">{name}</span> : null}
            {text || !name ? <span className="chatModelText">{name ? ` · ${text}` : text || agent?.label}</span> : null}
          </span>
          {glyph}
        </>}
        options={several ? agents.map(agentOption) : models}
        sections={[several ? modelSection : null, ...sections].filter(Boolean)}
        actions={allModels ? [{ icon: SearchIcon, label: T("All models…"), title: T("Search every connection's models"), onClick: openAll }] : []}
      />
      {allAt && allModels ? <ModelMenuList {...allAt} {...allModels} label={t("All models")} onClose={() => setAllAt(null)} /> : null}
    </span>
  );
}
