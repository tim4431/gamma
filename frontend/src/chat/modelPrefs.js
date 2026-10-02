// The two knobs the chat sends with a model: how hard it thinks (reasoning
// effort) and which of the provider's service tiers serves the call (speed).
// The chat keeps ONE preference of each (the chatEffort / chatSpeed prefs)
// and sends each model what that model actually takes; a model's levels and
// tiers come from GET /api/ai/model-info (its provider's listing, else
// models.dev for effort, else what the wire itself can ask for).

// Lowest first — the order mirrors EFFORT_ORDER in gamma/routers/ai.py.
export const EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];

// The service tiers, cheapest first — ai_protocols.SPEED_ORDER. "flex"
// trades latency for a lower price, "fast" buys the provider's premium
// low-latency routing (Anthropic's fast mode, OpenAI's and Codex's) at a
// higher one.
export const SPEED_ORDER = ["flex", "fast"];

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

// The tier to send for the preferred one: itself when the model has it, else
// "" — the provider's usual routing. Unlike effort there is no nearest tier:
// paying for a speed the model doesn't offer, or silently dropping to the
// cheap one, would both be the wrong call to make for the user.
export function speedFor(preferred, tiers) {
  return preferred && tiers?.includes(preferred) ? preferred : "";
}
