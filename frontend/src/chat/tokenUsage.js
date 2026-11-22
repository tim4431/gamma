// Token usage as the providers report it — one normalized shape from the
// server ({input, output, cache_read, cache_write}, see Protocol.usage in
// gamma/ai_protocols/base.py), plus what each call was billed as and, for a
// pay-per-token call, its estimated `cost` in USD (null when the model has
// no public price; gamma/ai_usage.py `cost_of`): the per-reply line under an
// AI bubble, the running total of a conversation, and Settings → AI → Usage
// all format it here.
import { T, t } from "../shared/i18n/i18n.js";

const KEYS = ["input", "output", "cache_read", "cache_write"];

// What a call was billed as (the server's `billing`): metered per token,
// out of a subscription, through the server's shared connection, a model on
// this computer or network, or not known (calls recorded before the field,
// stored as ""). The order is the chart's stacking order, bottom up.
export const BILLING_KINDS = ["api", "subscription", "shared", "local", "unknown"];
export const billingKind = (billing) => (BILLING_KINDS.includes(billing) ? billing : "unknown");
// Each kind's tag, the one wording everywhere a billing kind shows: the
// connection cards, the model menu's groups, the usage tables, the chart's
// legend and the connect dialog's service cards.
const BILLING_LABELS = {
  api: T("Pay per token"), subscription: T("Subscription"), shared: T("Shared by this server"),
  local: T("Free (local)"), unknown: T("Unknown"),
};
export const billingLabel = (billing) => (BILLING_LABELS[billing] ? t(BILLING_LABELS[billing]) : "");

// Sums two reports. Tokens add up; so does the cost of the calls that have
// one, never re-priced from the summed tokens. A pay-per-token call without
// a price (or a sum that already had one) marks the sum `unpriced`: its
// `cost` is then the known part only. A reply saved before costs were
// recorded carries neither, and its sum stays tokens alone. `billing` is the
// calls' own when they agree, "mixed" when they do not.
export function addUsage(total, usage) {
  if (!usage) return total || null;
  const out = {};
  for (const k of KEYS) out[k] = (total?.[k] || 0) + (usage[k] || 0);
  const costs = [total?.cost, usage.cost].filter((cost) => typeof cost === "number" && Number.isFinite(cost));
  if (costs.length) out.cost = costs.reduce((sum, cost) => sum + cost, 0);
  if (total?.unpriced || usage.unpriced || (usage.billing === "api" && typeof usage.cost !== "number")) out.unpriced = true;
  const billing = !total ? usage.billing : total.billing === usage.billing ? usage.billing : "mixed";
  if (billing) out.billing = billing;
  return out;
}

// An estimated amount in US dollars, short enough for a usage line:
// "<$0.01" under a cent, two or three decimals under a dollar ("$0.021",
// "$0.25"), cents up to a thousand, whole dollars past it. A known zero is
// "$0"; an unknown amount is "—", never "$0".
export function fmtCost(usd) {
  const v = typeof usd === "number" ? usd : Number.NaN;
  if (!Number.isFinite(v) || v < 0) return "—";
  if (v === 0) return "$0";
  if (v < 0.01) return "<$0.01";
  if (v < 0.9995) {
    const s = v.toFixed(3);
    return `$${s.endsWith("0") ? s.slice(0, -1) : s}`;
  }
  if (v < 999.995) return `$${v.toFixed(2)}`;
  return `$${Math.round(v).toLocaleString("en-US")}`;
}

// The cost a reply's line (or a conversation's total) shows after its
// tokens: "$0.021", or "~$0.02+" when some of its rounds had no price, which
// the known part understates. "" when nothing was priced: a subscription,
// a shared connection, a local model, a model without a public price, or a
// reply saved before costs were recorded show their tokens alone.
export function costLabel(usage) {
  const cost = usage?.cost;
  if (typeof cost !== "number" || !Number.isFinite(cost)) return "";
  if (!usage.unpriced) return fmtCost(cost);
  if (cost <= 0) return "";
  return cost < 0.01 ? `${fmtCost(cost)}+` : `~${fmtCost(cost)}+`;
}

// A price per million tokens, as the model lists show it: "$3", "$2.5",
// "$0.075". "" when unknown.
export function fmtPrice(usd) {
  const v = typeof usd === "number" ? usd : Number.NaN;
  if (!Number.isFinite(v) || v < 0) return "";
  return `$${v >= 1 ? Number(v.toFixed(2)) : Number(v.toPrecision(2))}`;
}

// 950 → "950", 12_340 → "12.3k", 1_250_000 → "1.25M" — compact enough for a
// chip, exact enough to compare two replies.
export function fmtTokens(n) {
  const v = Math.max(0, Number(n) || 0);
  if (v < 1000) return String(v);
  if (v < 10_000) return `${(v / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  if (v < 1_000_000) return `${Math.round(v / 1000)}k`;
  if (v < 10_000_000) return `${(v / 1_000_000).toFixed(2).replace(/\.?0+$/, "")}M`;
  return `${(v / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

// While a reply streams the provider has not counted yet: a rough running
// figure from the characters received so far (about four per token for
// English prose; CJK and code run denser). Shown with a "~" and replaced
// by the provider's own report as each turn completes.
export function estimateTokens(chars) {
  return Math.round(Math.max(0, Number(chars) || 0) / 4);
}

// The live line of a streaming reply: the rounds already reported (exact)
// plus the estimate for the text still arriving.
export function liveUsage(usage, pendingChars) {
  const pending = estimateTokens(pendingChars);
  if (!usage && !pending) return null;
  return { ...addUsage(null, usage || { input: 0, output: 0, cache_read: 0, cache_write: 0 }),
    output: (usage?.output || 0) + pending, estimate: pending > 0 };
}

// The share of the prompt the provider served from its cache, 0–100.
export function cachedPercent(usage) {
  if (!usage?.input) return 0;
  return Math.round(((usage.cache_read || 0) / usage.input) * 100);
}

// Sum of the replies' usage in a conversation (messages without a report
// add nothing; older chats saved before usage was recorded count as zero),
// their costs summed as addUsage does.
export function conversationUsage(messages) {
  let total = null;
  for (const m of messages || []) if (m.role === "ai" && m.usage) total = addUsage(total, m.usage);
  return total;
}

// How much of the model's window the conversation fills now: the latest
// reply's LAST round — its prompt plus what it wrote, which the next message
// carries as history (a reply's `usage` sums every tool round, so it
// overcounts). Replies record it as `context_tokens`; one saved before that
// without tool calls was a single round, so its usage is the same figure.
// Null until some reply reported counts.
export function contextUsed(messages) {
  const list = messages || [];
  for (let i = list.length - 1; i >= 0; i--) {
    const m = list[i];
    if (m.role !== "ai") continue;
    if (m.context_tokens) return m.context_tokens;
    if (m.usage && !m.actions?.length) return (m.usage.input || 0) + (m.usage.output || 0);
  }
  return null;
}

// The long form for a tooltip: every count spelled out.
export function usageDetail(usage) {
  if (!usage) return "";
  const parts = [t("{input} input tokens", { input: (usage.input || 0).toLocaleString() }),
    t("{output} output tokens", { output: (usage.output || 0).toLocaleString() })];
  if (usage.cache_read) parts.push(t("{cache_read} read from the prompt cache ({usage}% of the input)", { cache_read: usage.cache_read.toLocaleString(), usage: cachedPercent(usage) }));
  if (usage.cache_write) parts.push(t("{cache_write} written to the prompt cache", { cache_write: usage.cache_write.toLocaleString() }));
  return parts.join(" · ");
}
