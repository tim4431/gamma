// The Usage chart's arithmetic (Settings → AI → Usage, GET /api/ai/usage):
// its bars, a day's or a month's counts split by billing.
import { BILLING_KINDS, billingKind } from "../chat/tokenUsage.js";

const FIELDS = ["calls", "input", "output", "cache_read", "cache_write"];
const COUNT_FIELDS = [...FIELDS, "cost", "priced_calls", "unpriced_calls"];

// One bar's stacked segments for the metric, bottom up in BILLING_KINDS
// order, from its split by billing ({billing: counts}; "" is unknown).
// Tokens are input + output; cost stacks the pay-per-token calls alone:
// nothing else is billed per token.
function segmentsOf(bar, metric) {
  const sums = {};
  for (const [billing, part] of Object.entries(bar.billing)) {
    const kind = billingKind(billing);
    const value = metric === "cost" ? (kind === "api" ? part.cost || 0 : 0)
      : metric === "calls" ? part.calls || 0 : (part.input || 0) + (part.output || 0);
    sums[kind] = (sums[kind] || 0) + value;
  }
  return BILLING_KINDS.filter((kind) => sums[kind] > 0).map((kind) => ({ kind, value: sums[kind] }));
}

// Daily bars cover the latest 30 UTC days (the answer's `daily`, each a
// counts object with its date); monthly bars cover the latest 12 calendar
// months, each its days summed. `metric` is "tokens" (input + output),
// "calls" or "cost". Each bar gets its `segments` and `value` (their sum),
// and `ceiling` is the axis top: a round number at or above the tallest
// bar.
export function usageChartData(daily, interval = "daily", metric = "tokens") {
  let bars = daily.slice(-30);
  if (interval === "monthly") {
    const months = new Map();
    for (const day of daily) {
      const key = day.date.slice(0, 7);
      if (!months.has(key)) months.set(key, []);
      months.get(key).push(day);
    }
    bars = [...months].slice(-12).map(([key, days]) => ({ date: `${key}-01`, ...sumCounts(days) }));
  }
  bars = bars.map((bar) => {
    const segments = segmentsOf(bar, metric);
    return { ...bar, segments, value: segments.reduce((sum, segment) => sum + segment.value, 0) };
  });
  const peak = Math.max(metric === "cost" ? 0.01 : 1, ...bars.map((bar) => bar.value));
  const magnitude = 10 ** Math.floor(Math.log10(peak));
  const ceiling = [1, 2, 5, 10].find((step) => step * magnitude >= peak) * magnitude;
  return { bars, ceiling };
}

// Several counts objects summed into one ({calls, input, output,
// cache_read, cache_write, cost, priced_calls, unpriced_calls, billing:
// {billing: the same counts}}): a month's bar from its days.
function sumCounts(list) {
  const total = Object.fromEntries(COUNT_FIELDS.map((field) => [field, 0]));
  total.billing = {};
  for (const counts of list) {
    for (const field of COUNT_FIELDS) total[field] += counts[field] || 0;
    for (const [billing, part] of Object.entries(counts.billing)) {
      const into = (total.billing[billingKind(billing)] ||= Object.fromEntries([...FIELDS, "cost"].map((field) => [field, 0])));
      for (const field of [...FIELDS, "cost"]) into[field] += part[field] || 0;
    }
  }
  return total;
}
