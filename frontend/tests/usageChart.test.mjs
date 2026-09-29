import test from "node:test";
import assert from "node:assert/strict";
import { usageChartData } from "../src/settings/usageChartData.js";

const daily = Array.from({ length: 365 }, (_, i) => ({
  date: new Date(Date.UTC(2023, 3, 2 + i)).toISOString().slice(0, 10),
  calls: 1, input: 100, output: 10, cache_read: 20, cache_write: 5,
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
  assert.deepEqual(result.bars.find((bar) => bar.date === "2024-02-01"), {
    date: "2024-02-01", calls: 29, input: 2900, output: 290, cache_read: 580, cache_write: 145,
  });
  assert.equal(result.bars.reduce((sum, bar) => sum + bar.calls, 0), 365);
  assert.deepEqual(daily, before);
  assert.equal(usageChartData(daily, "monthly", "calls").ceiling, 50);
});
