// Backup schedules entered in the browser's time zone, stored as UTC cron
// (settings/backupSchedule.js).
import test from "node:test";
import assert from "node:assert/strict";
import { fromUtcCron, parseCron, shiftTime, toUtcCron } from "../src/settings/backupSchedule.js";

const PDT = -420, IST = 330, JST = 540;

test("the cron reader knows the presets and leaves the rest custom", () => {
  assert.equal(parseCron("5 * * * *").preset, "hourly");
  assert.equal(parseCron("0 3 * * *").preset, "daily");
  assert.deepEqual(parseCron("0 3 * * 1,5").weekdays, [1, 5]);
  assert.equal(parseCron("0 3 15 * *").monthday, 15);
  assert.equal(parseCron("0 9,17 * * 1-5"), null);
  assert.equal(parseCron(""), null);
});

test("a clock time carries into the next or previous day", () => {
  assert.deepEqual(shiftTime(20, 0, 420), { hour: 3, minute: 0, days: 1 });
  assert.deepEqual(shiftTime(3, 0, -540), { hour: 18, minute: 0, days: -1 });
  assert.deepEqual(shiftTime(10, 15, 0), { hour: 10, minute: 15, days: 0 });
});

test("daily and weekly move hour and weekday together; hourly keeps a whole-hour zone's minute", () => {
  assert.equal(toUtcCron({ preset: "daily", hour: 3, minute: 0 }, PDT), "0 10 * * *");
  assert.equal(toUtcCron({ preset: "daily", hour: 20, minute: 30 }, PDT), "30 3 * * *");
  // Monday and Friday 20:00 in California are Tuesday and Saturday 03:00 UTC.
  assert.equal(toUtcCron({ preset: "weekly", hour: 20, minute: 0, weekdays: [1, 5] }, PDT), "0 3 * * 2,6");
  // Monday 02:00 in Tokyo is Sunday 17:00 UTC.
  assert.equal(toUtcCron({ preset: "weekly", hour: 2, minute: 0, weekdays: [1] }, JST), "0 17 * * 0");
  assert.equal(toUtcCron({ preset: "hourly", minute: 15 }, PDT), "15 * * * *");
  assert.equal(toUtcCron({ preset: "hourly", minute: 0 }, IST), "30 * * * *");
  assert.equal(toUtcCron({ preset: "monthly", hour: 3, minute: 0, monthday: 1 }, PDT), "0 3 1 * *", "monthly stays UTC");
});

test("a stored UTC schedule reads back in local time and round-trips", () => {
  assert.deepEqual(fromUtcCron("0 10 * * *", PDT), { preset: "daily", hour: 3, minute: 0, weekdays: [1], monthday: 1 });
  assert.deepEqual(fromUtcCron("0 3 * * 2,6", PDT).weekdays, [1, 5]);
  assert.equal(fromUtcCron("0 3 1 * *", PDT).hour, 3, "monthly stays UTC");
  assert.equal(fromUtcCron("0 9,17 * * 1-5", PDT), null);
  for (const [cron, offset] of [["0 17 * * 0", JST], ["45 23 * * 1,3,5", PDT], ["30 * * * *", IST], ["0 0 * * *", 0]]) {
    assert.equal(toUtcCron(fromUtcCron(cron, offset), offset), cron);
  }
});
