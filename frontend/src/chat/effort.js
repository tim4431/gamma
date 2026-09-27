// Reasoning effort: the chat keeps one preferred level (the chatEffort
// pref) and sends each model the level it takes. A model's levels come from
// GET /api/ai/model-info (its provider's listing, else models.dev); the
// order mirrors EFFORT_ORDER in gamma/routers/ai.py, lowest first.
export const EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];

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
