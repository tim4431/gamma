import test from "node:test";
import assert from "node:assert/strict";
import { usageCalendar } from "../src/settings/usageCalendar.js";

test("calendar aligns UTC dates across leap day and year boundaries without fabricating activity", () => {
  const daily = Array.from({ length: 365 }, (_, i) => ({
    date: new Date(Date.UTC(2023, 9, 1 + i)).toISOString().slice(0, 10), calls: i === 151 ? 2 : 0,
    input: i === 151 ? 800 : 0, output: i === 151 ? 200 : 0,
  }));
  const result = usageCalendar(daily);
  assert.equal(result.weeks.length, 53);
  assert.equal(result.weeks.flat().filter(Boolean).length, 365);
  assert.equal(result.weeks.flat().find((d) => d?.date === "2024-02-29").level, 4);
  assert.equal(result.activeDays, 1);
  assert.equal(result.tokens, 1000);
  assert.equal(result.calls, 2);
  for (const week of result.weeks) week.forEach((day, index) => {
    if (day) assert.equal(new Date(`${day.date}T00:00:00Z`).getUTCDay(), index);
  });
  assert.equal(result.weeks.at(-1).at(-1), null);
});

test("metric switching changes intensity; empty days and edge padding remain distinct", () => {
  const daily = [
    { date: "2026-09-25", calls: 1, input: 1000, output: 0 },
    { date: "2026-09-26", calls: 4, input: 40, output: 0 },
    { date: "2026-09-27", calls: 0, input: 0, output: 0 },
  ];
  assert.deepEqual(usageCalendar(daily).weeks.flat().filter(Boolean).map((d) => d.level), [4, 1, 0]);
  assert.deepEqual(usageCalendar(daily, "calls").weeks.flat().filter(Boolean).map((d) => d.level), [1, 4, 0]);
  assert.equal(usageCalendar(daily).weeks[0][0], null);
  assert.equal(usageCalendar([]).weeks.length, 0);
});
