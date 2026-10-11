// Which reminders want a look and when the hook looks again
// (app/reminders.js), and the line a card shows.
import assert from "node:assert/strict";
import { test } from "node:test";
import { reminderState, reminderText, ringsNow } from "../src/app/reminders.js";

// Friday 9 October 2026, 3 pm local.
const NOW = new Date(2026, 9, 9, 15, 0);
const r = (key, date, time = "", text = "") => ({ key, date, time, text, page_title: "P" });

test("due: by now and not dismissed, oldest first; a reminder without a time is due at nine", () => {
  const list = [r("late", "2026-10-09", "14:00"), r("early", "2026-10-09"), r("later", "2026-10-09", "16:00"), r("gone", "2026-10-01")];
  const { due } = reminderState(list, ["gone"], NOW);
  assert.deepEqual(due.map((x) => x.key), ["early", "late"]);
  assert.equal(due[0].at.getHours(), 9);
});

test("wake: the next reminder, else midnight or an hour from now", () => {
  assert.deepEqual(reminderState([r("soon", "2026-10-09", "15:20")], [], NOW).wake, new Date(2026, 9, 9, 15, 20));
  assert.deepEqual(reminderState([], [], NOW).wake, new Date(2026, 9, 9, 16, 0), "an hour on");
  assert.deepEqual(reminderState([], [], new Date(2026, 9, 9, 23, 30)).wake, new Date(2026, 9, 10), "midnight first");
  assert.deepEqual(reminderState([r("x", "2026-10-09", "15:20")], ["x"], NOW).wake, new Date(2026, 9, 9, 16, 0),
    "a dismissed one wakes no one");
});

test("a card's line: the reminder left out, other mentions as their chips read, refs as their labels", () => {
  const item = r("k", "2026-10-10", "09:00", "Send [[p1]] to @bob @2026-10-10 09:00 (remind @carol) by @2026-10-12");
  assert.equal(reminderText(item, (id) => (id === "p1" ? "the draft" : ""), NOW), "Send the draft to @bob by @October 12, 2026");
});

test("only a fresh reminder rings", () => {
  assert.equal(ringsNow({ at: new Date(2026, 9, 9, 9, 0) }, NOW), true);
  assert.equal(ringsNow({ at: new Date(2026, 9, 1, 9, 0) }, NOW), false);
});
