// What the reminders want now (app/useReminders.js, docs/dev/mentions.md):
// the ones due and not yet dismissed, the moment the hook next has to look,
// and the line a card or a notification shows. Pure, so
// tests/reminders.test.mjs pins it. A reminder's time is a wall-clock time:
// this browser's zone decides when it is due.
import { dateLabel, remindAt, scanMentions } from "../editor/mentions.js";
import { refBlockText } from "../editor/refLists.js";

const HOUR = 3600 * 1000;

// `list`: GET /api/reminders' rows; `done`: the dismissed keys. → {due:
// the reminders due by `now`, oldest first, each with its `at`; wake: when
// to look again — the next reminder, the next midnight (the chips' Today
// becomes Yesterday) or an hour from now, whichever is first, so a sleep
// or a clock change is caught up}.
export function reminderState(list, done, now = new Date()) {
  const dismissed = new Set(done);
  const due = [];
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  let wake = Math.min(midnight.getTime(), now.getTime() + HOUR);
  for (const r of list || []) {
    const at = remindAt(r);
    if (!at || dismissed.has(r.key)) continue;
    if (at <= now) due.push({ ...r, at });
    else wake = Math.min(wake, at.getTime());
  }
  due.sort((a, b) => a.at - b.at || a.key.localeCompare(b.key));
  return { due, wake: new Date(wake) };
}

// A reminder's block as one plain line: its other mentions as their chips
// read, its [[refs]] as their labels (`labelOf`), the reminder itself left
// out — the card says when.
export function reminderText(r, labelOf, now = new Date()) {
  const content = String(r.text || "");
  let out = "", pos = 0;
  for (const m of scanMentions(content)) {
    out += content.slice(pos, m.from);
    if (!m.remind) out += m.kind === "person" ? m.raw : `@${dateLabel(m, now)}`;
    pos = m.to;
  }
  return refBlockText(out + content.slice(pos), labelOf).replace(/\s+/g, " ").trim();
}

// Whether a reminder still makes a system notification: due lately (a
// browser opened after a week away shows the old ones as cards, without
// ringing for each).
export const ringsNow = (r, now = new Date()) => now - r.at < 12 * HOUR;
