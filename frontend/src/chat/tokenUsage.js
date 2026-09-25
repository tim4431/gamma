// Token usage as the providers report it — one normalized shape from the
// server ({input, output, cache_read, cache_write}, see Protocol.usage in
// gamma/ai_protocols/base.py): the per-reply line under an AI bubble, the
// running total of a conversation, and the Settings → AI tiles all format it here.
import { t } from "../shared/i18n/i18n.js";

const KEYS = ["input", "output", "cache_read", "cache_write"];

export function addUsage(total, usage) {
  if (!usage) return total || null;
  const out = {};
  for (const k of KEYS) out[k] = (total?.[k] || 0) + (usage[k] || 0);
  return out;
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
// add nothing; older chats saved before usage was recorded count as zero).
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
