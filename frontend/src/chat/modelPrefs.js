// The two knobs the chat sends with a model: how hard it thinks (reasoning
// effort) and whether it runs fast (speed). The chat keeps ONE preference of
// each (the chatEffort / chatSpeed prefs) and sends each model what that
// model actually takes; a model's levels and
// tiers come from GET /api/ai/model-info (its provider's listing, else
// models.dev for effort, else what the wire itself can ask for).
import { T, t } from "../shared/i18n/i18n.js";

// A model as the model menus list it: its name, and "· no tools" when it
// can't call tools on its connection (the registry row's `tools` false; its
// chats then answer as a plain chat, gamma/routers/ai.py _checked_tools).
export function modelLabel(m) {
  return m?.tools === false ? t("{model} · no tools", { model: m.model }) : m?.model || "";
}

// Lowest first — the order mirrors EFFORT_ORDER in gamma/routers/ai.py.
export const EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
// A level as people read it, wherever one is named (the model chip, a
// reply's foot, Settings); one unknown here by its own word.
const EFFORT_LABELS = { none: T("None"), minimal: T("Minimal"), low: T("Low"), medium: T("Medium"), high: T("High"),
  xhigh: T("Extra high"), max: T("Max") };
export const effortLabel = (level) => (EFFORT_LABELS[level] ? t(EFFORT_LABELS[level]) : level || "");

// The service tiers, cheapest first — ai_protocols.SPEED_ORDER. "flex"
// trades latency for a lower price, "fast" buys the provider's premium
// low-latency routing (Anthropic's fast mode, OpenAI's and Codex's) at a
// higher one. The translated view offers both, the chat "fast" alone.
export const SPEED_ORDER = ["flex", "fast"];
// Their names in a menu, through t() where they show.
export const SPEED_LABELS = { flex: T("Flex"), fast: T("Fast") };

// The level to send for the preferred one: itself when the model takes it,
// else the nearest level it does (a tie goes to the lower one); "" — the
// provider's default, the parameter left out — for no preference or a model
// without effort levels.
export function effortFor(preferred, levels) {
  const at = EFFORT_ORDER.indexOf(preferred);
  if (!preferred || !levels?.length) return "";
  if (levels.includes(preferred)) return preferred;
  if (at < 0) return "";
  let best = "", gap = Infinity;
  for (const level of [...levels].sort((a, b) => EFFORT_ORDER.indexOf(a) - EFFORT_ORDER.indexOf(b))) {
    const i = EFFORT_ORDER.indexOf(level);
    if (i >= 0 && Math.abs(i - at) < gap) { best = level; gap = Math.abs(i - at); }
  }
  return best;
}

// The chat's Fast button (chat/ModelChip.jsx): {on, next}, whether the chat
// runs fast and the speed a press picks; null for a model that can't (no
// "fast" among its `tiers`), which is sent no speed. `chosen` is the speed
// picked, "" for none, and `own` the one a computer's agent runs at then: an
// agent set up to run fast is on with nothing picked, and off is then
// "standard", picked by name.
export function fastChoice(chosen, tiers, own = "") {
  if (!tiers?.includes("fast")) return null;
  const on = (chosen || own) === "fast";
  const usual = own === "fast";
  return { on, next: on === usual ? (on ? "standard" : "fast") : "" };
}
