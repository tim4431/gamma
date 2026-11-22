import test from "node:test";
import assert from "node:assert/strict";
import { usageChartData } from "../src/settings/usageChartData.js";

// GET /api/ai/usage's days: a counts object each, split by billing ("" for
// calls recorded before the billing was).
const counts = (calls, input, output, cost = 0, extra = {}) => ({ calls, input, output, cache_read: 0, cache_write: 0, cost, ...extra });
const daily = Array.from({ length: 365 }, (_, i) => ({
  date: new Date(Date.UTC(2023, 3, 2 + i)).toISOString().slice(0, 10),
  ...counts(1, 100, 10, 0, { cache_read: 20, cache_write: 5 }), priced_calls: 0, unpriced_calls: 0,
  billing: { "": counts(1, 100, 10, 0, { cache_read: 20, cache_write: 5 }) },
}));

test("daily bars show the latest 30 UTC days and scale the selected metric", () => {
  const result = usageChartData(daily);
  assert.equal(result.bars.length, 30);
  assert.equal(result.bars[0].date, "2024-03-02");
  assert.equal(result.bars.at(-1).date, "2024-03-31");
  assert.equal(result.ceiling, 200);
  assert.equal(usageChartData(daily, "daily", "calls").ceiling, 1);
  assert.deepEqual(usageChartData([]), { bars: [], ceiling: 1 });
});

test("monthly bars sum calls and all token fields across leap day without modifying daily data", () => {
  const before = structuredClone(daily);
  const result = usageChartData(daily, "monthly");
  assert.equal(result.bars.length, 12);
  const february = result.bars.find((bar) => bar.date === "2024-02-01");
  assert.deepEqual(
    { calls: february.calls, input: february.input, output: february.output, cache_read: february.cache_read, cache_write: february.cache_write },
    { calls: 29, input: 2900, output: 290, cache_read: 580, cache_write: 145 });
  assert.deepEqual(february.segments, [{ kind: "unknown", value: 3190 }], '"" is unknown');
  assert.equal(result.bars.reduce((sum, bar) => sum + bar.calls, 0), 365);
  assert.deepEqual(daily, before);
  assert.equal(usageChartData(daily, "monthly", "calls").ceiling, 50);
});

const split = [
  { date: "2024-05-01", ...counts(3, 900, 100, 0.05), billing: { api: counts(1, 450, 50, 0.05), subscription: counts(1, 250, 50), "": counts(1, 200, 0) } },
  { date: "2024-05-02", ...counts(3, 4000, 100), billing: { local: counts(1, 100, 0), shared: counts(2, 3900, 100) } },
];

test("bars stack by billing, bottom up in a fixed order, for tokens and calls", () => {
  const tokens = usageChartData(split, "daily", "tokens");
  assert.deepEqual(tokens.bars[0].segments, [{ kind: "api", value: 500 }, { kind: "subscription", value: 300 }, { kind: "unknown", value: 200 }]);
  assert.equal(tokens.bars[0].value, 1000);
  assert.deepEqual(tokens.bars[1].segments.map((s) => s.kind), ["shared", "local"], "the order is the kind's, not the data's");
  assert.equal(tokens.ceiling, 5000);
  const calls = usageChartData(split, "daily", "calls");
  assert.deepEqual(calls.bars[1].segments, [{ kind: "shared", value: 2 }, { kind: "local", value: 1 }]);
  assert.equal(calls.ceiling, 5);
});

test("cost stacks the pay-per-token calls alone, on an axis sized to cents", () => {
  const cost = usageChartData(split, "daily", "cost");
  assert.deepEqual(cost.bars[0].segments, [{ kind: "api", value: 0.05 }]);
  assert.deepEqual(cost.bars[1].segments, [], "a day of subscription, shared and local calls costs nothing to chart");
  assert.ok(Math.abs(cost.ceiling - 0.05) < 1e-12, `ceiling ${cost.ceiling}`);
  // a period with no priced call does not draw a one-dollar axis
  assert.ok(Math.abs(usageChartData(split.slice(1), "daily", "cost").ceiling - 0.01) < 1e-12);
});

test("a month sums its days' split, the unknown kind included", () => {
  const [may] = usageChartData(split, "monthly", "tokens").bars;
  assert.deepEqual(may.segments, [{ kind: "api", value: 500 }, { kind: "subscription", value: 300 }, { kind: "shared", value: 4000 },
    { kind: "local", value: 100 }, { kind: "unknown", value: 200 }]);
  assert.equal(may.calls, 6);
  assert.ok(Math.abs(usageChartData(split, "monthly", "cost").bars[0].value - 0.05) < 1e-12);
});
