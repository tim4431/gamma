// The @ mentions of note text (docs/dev/mentions.md): their syntax, how a
// chip reads, the dates a typed query names, and the rows of the editor's
// "@" menu. Pure, so tests/mentions.test.mjs pins it; editor/MentionMenu.jsx
// draws the menu and the chips, editor/BlockTree.jsx owns the trigger, keys
// and state, app/useReminders.js delivers the reminders.
//
// A mention is plain text, so whatever knows nothing of it (exports, PDFs,
// the AI, search) still reads it right:
//   @2026-10-09                        a date
//   @2026-10-09 14:30                  a date with a wall-clock time
//   @2026-10-10 09:00 (remind @alice)  a reminder for the account alice
//   @alice                             a person, by username
// A page link is the [[id]] the [[ picker writes. The server reads the same
// syntax (backend/gamma/mentions.py); tests/shared/mentions.json pins both.
import { T, fmtDate, t } from "../shared/i18n/i18n.js";
import { rankRefPages } from "./refLists.js";
import { fuzzyIndices } from "./slashCommands.js";

// The "@" starts a word — not after a letter or digit (an e-mail address),
// a path's "/", a "." or a backslash escape — and a username ends before a
// trailing "." or "-", which end the sentence. ASCII classes on purpose:
// Chinese text runs straight into a mention ("请@bob看一下").
const NAME = "[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?";
const BEFORE = "(?<![A-Za-z0-9_@./+\\\\-])";
export const MENTION_RE = new RegExp(
  `${BEFORE}@(?:(\\d{4}-\\d{2}-\\d{2})(?: (\\d{2}:\\d{2}))?(?: \\(remind @(${NAME})\\))?(?![A-Za-z0-9_:-])`
  + `|(?!\\d{4}-\\d{2}-\\d{2})(${NAME})(?![A-Za-z0-9_-]))`, "g");

// When a reminder written without a time is due, and what "Remind me"
// offers by default: 9 in the morning.
export const REMIND_TIME = "09:00";

const pad = (n) => String(n).padStart(2, "0");

// A Date's local calendar day as YYYY-MM-DD (toISOString would give UTC's).
export const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
// "YYYY-MM-DD" [+ "HH:MM"] → that local wall-clock moment, or null when no
// such day or time exists.
export function localMoment(date, time = "00:00") {
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  const at = new Date(y, m - 1, d, hh, mm);
  return at.getFullYear() === y && at.getMonth() === m - 1 && at.getDate() === d && hh <= 23 && mm <= 59 ? at : null;
}
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);

// Every mention in `text`, in order: {kind: "date", raw, from, to, date[,
// time][, remind]} or {kind: "person", raw, from, to, user}. An impossible
// date or time is no mention.
export function scanMentions(text) {
  const out = [];
  for (const m of String(text || "").matchAll(MENTION_RE)) {
    const at = { raw: m[0], from: m.index, to: m.index + m[0].length };
    if (m[4]) { out.push({ kind: "person", ...at, user: m[4] }); continue; }
    if (!localMoment(m[1], m[2] || "00:00")) continue;
    out.push({ kind: "date", ...at, date: m[1], ...(m[2] ? { time: m[2] } : {}), ...(m[3] ? { remind: m[3] } : {}) });
  }
  return out;
}

// A mention inside a Markdown link's text or an HTML tag is left alone: a
// chip there would break the link around it. What the rendered view and
// the editor draw: scanMentions less those.
const LINK_OR_TAG_RE = /!?\[[^\]\n]*\]\([^)\n]*\)|<[^>\n]*>/g;
export function proseMentions(text) {
  const found = scanMentions(text);
  if (!found.length) return found;
  const skip = [...String(text).matchAll(LINK_OR_TAG_RE)].map((m) => [m.index, m.index + m[0].length]);
  return found.filter((m) => !skip.some(([a, b]) => m.from < b && m.to > a));
}

// The rendered view's step (BlockTree's mdPreprocess, outside math and
// code): each mention a `mention:` link, which BlockMarkdown draws as a
// chip (MentionMenu.jsx MentionChip) — or as its text, for a name no one
// here has.
export function mentionMarkdown(text) {
  const found = text.includes("@") ? proseMentions(text) : [];
  let out = "", pos = 0;
  for (const m of found) {
    out += `${text.slice(pos, m.from)}[${m.raw}](mention:${encodeURIComponent(m.raw)})`;
    pos = m.to;
  }
  return found.length ? out + text.slice(pos) : text;
}

// A mention's text: the inverse of scanMentions.
export function dateToken({ date, time, remind }) {
  return `@${date}${time ? ` ${time}` : ""}${remind ? ` (remind @${remind})` : ""}`;
}
export const personToken = (user) => `@${user}`;

// When a reminder is due (its time, else REMIND_TIME), as a local moment.
export const remindAt = ({ date, time }) => localMoment(date, time || REMIND_TIME);

// Days from today to `date` (YYYY-MM-DD): 1 is tomorrow.
export const dayDelta = (date, now = new Date()) => Math.round((localMoment(date) - localMoment(isoDay(now))) / 86400000);

// How a date reads, Notion-style: Today / Tomorrow / Yesterday, else the
// full date ("October 12, 2026"), then the time if it has one ("9:00 AM").
export function dateLabel({ date, time }, now = new Date()) {
  const day = localMoment(date);
  if (!day) return date;
  const delta = dayDelta(date, now);
  const name = delta === 0 ? t("Today") : delta === 1 ? t("Tomorrow") : delta === -1 ? t("Yesterday")
    : fmtDate(day, { month: "long", day: "numeric", year: "numeric" });
  return time ? `${name} ${fmtDate(localMoment(date, time), { hour: "numeric", minute: "2-digit" })}` : name;
}

// --- chips ------------------------------------------------------------------

// Who a note can mention (App's MentionContext): {me: the signed-in
// username ("" in a share view), people: the workspace's [{username,
// role}], known: the Set of usernames a person chip is drawn for}.
export const NO_MENTIONS = Object.freeze({ me: "", people: [], known: new Set() });

// What a mention token shows, the rendered view's chip and the editor's
// alike: {kind ("date" | "remind" | "person"), text, title, overdue}, or
// null for a person no one here is called (the text stays as it is).
export function mentionChip(token, { me, known }, now = new Date()) {
  if (token.kind === "person") {
    if (!known.has(token.user)) return null;
    return { kind: "person", text: `@${token.user}`, title: token.user === me ? t("You") : token.user };
  }
  const text = `@${dateLabel(token, now)}`;
  const full = fmtDate(localMoment(token.date, token.time || "00:00"), {
    weekday: "long", month: "long", day: "numeric", year: "numeric",
    ...(token.time ? { hour: "numeric", minute: "2-digit" } : {}),
  });
  if (!token.remind) return { kind: "date", text, title: full };
  return {
    kind: "remind", text, overdue: remindAt(token) <= now,
    title: token.remind === me ? t("Reminds you: {when}", { when: full })
      : t("Reminds {name}: {when}", { name: token.remind, when: full }),
  };
}

// A chip's classes (the alarm clock of a reminder is its CSS's).
export const mentionChipClass = (chip) => `mentionChip mention-${chip.kind}${chip.overdue ? " overdue" : ""}`;

// --- the dates a query names -----------------------------------------------

const WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
  "september", "october", "november", "december"];
const RELATIVE = [["today", 0], ["tomorrow", 1], ["yesterday", -1]];
const ALIASES = { now: 0, tmr: 1, tmrw: 1 };
const UNITS = { d: "day", day: "day", days: "day", w: "week", week: "week", weeks: "week",
  m: "month", month: "month", months: "month", y: "year", year: "year", years: "year" };

// A trailing time: "3pm", "9:30 am", "14:00", "at 8a", "noon", "midnight".
// A bare number is a day of the month, never a time.
const TIME_RE = /(?:^|\s)(?:at\s+)?(?:(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)|(\d{1,2}):(\d{2})|(noon|midnight))$/;
function splitTime(q) {
  const m = q.match(TIME_RE);
  if (!m) return [q, null];
  let hh, mm;
  if (m[6]) [hh, mm] = m[6] === "noon" ? [12, 0] : [0, 0];
  else if (m[3]) {
    hh = Number(m[1]) % 12 + (m[3].startsWith("p") ? 12 : 0);
    mm = Number(m[2] || 0);
    if (Number(m[1]) < 1 || Number(m[1]) > 12) return [q, null];
  } else [hh, mm] = [Number(m[4]), Number(m[5])];
  if (hh > 23 || mm > 59) return [q, null];
  return [q.slice(0, m.index).trim(), `${pad(hh)}:${pad(mm)}`];
}

const monthOf = (word) => (word.length >= 3 ? MONTHS.findIndex((m) => m.startsWith(word)) : -1);
const weekdayOf = (word) => (word.length >= 2 ? WEEKDAYS.findIndex((d) => d.startsWith(word)) : -1);
// Monday 1 … Sunday 7.
const isoWeekday = (d) => d.getDay() || 7;

function shift(today, n, unit) {
  if (unit === "day") return addDays(today, n);
  if (unit === "week") return addDays(today, 7 * n);
  const months = unit === "year" ? 12 * n : n;
  const end = new Date(today.getFullYear(), today.getMonth() + months + 1, 0).getDate();
  return new Date(today.getFullYear(), today.getMonth() + months, Math.min(today.getDate(), end));
}

// The days the date part of a query names (several while it is ambiguous:
// "t" is today or tomorrow), as Dates at local midnight.
function daysOf(q, today) {
  if (!q) return [];
  if (q in ALIASES) return [addDays(today, ALIASES[q])];
  const relative = RELATIVE.filter(([word]) => word.startsWith(q)).map(([, n]) => addDays(today, n));
  if (relative.length) return relative;
  let m = q.match(/^(this|next|last)?\s*([a-z]+)$/);
  const wd = m ? weekdayOf(m[2]) : -1;
  if (wd >= 0) {
    const ahead = (wd - today.getDay() + 7) % 7;
    if (m[1] === "last") return [addDays(today, -((today.getDay() - wd + 7) % 7 || 7))];
    // "next friday" is the Friday of next week (weeks start on Monday).
    if (m[1] === "next") return [addDays(today, 8 - isoWeekday(today) + ((wd + 6) % 7))];
    return [addDays(today, m[1] === "this" ? ahead : ahead || 7)];
  }
  if ((m = q.match(/^(next|last) (week|month|year)$/))) return [shift(today, m[1] === "next" ? 1 : -1, m[2])];
  if ((m = q.match(/^(?:in )?(\d{1,3}) ?([a-z]+)$/)) && UNITS[m[2]]) return [shift(today, Number(m[1]), UNITS[m[2]])];
  if ((m = q.match(/^(\d{1,3}) ?([a-z]+) ago$/)) && UNITS[m[2]]) return [shift(today, -Number(m[1]), UNITS[m[2]])];
  if ((m = q.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) {
    const day = localMoment(`${m[1]}-${pad(m[2])}-${pad(m[3])}`);
    return day ? [day] : [];
  }
  // "oct 12", "october 12, 2026", "12 oct", "12th october 2026".
  m = q.match(/^([a-z]+)\.? (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?$/)
    || q.match(/^(\d{1,2})(?:st|nd|rd|th)? ([a-z]+)\.?(?:,? (\d{4}))?$/);
  if (m) {
    const [word, dayNum] = /^\d/.test(m[1]) ? [m[2], m[1]] : [m[1], m[2]];
    const month = monthOf(word);
    if (month < 0) return [];
    const day = localMoment(`${m[3] || today.getFullYear()}-${pad(month + 1)}-${pad(dayNum)}`);
    return day ? [day] : [];
  }
  return [];
}

// The dates a typed query names, best first: [{date, time?}]. English
// words: today/tomorrow/yesterday (and their prefixes), weekdays ("fri",
// "next friday", "last mon"), "in 3 days", "2 weeks ago", "next month",
// month names ("oct 12", "12 october 2026") and YYYY-MM-DD, each with an
// optional trailing time ("3pm", "9:30", "noon"). A time alone is today.
export function parseDateQuery(query, now = new Date()) {
  const [rest, time] = splitTime(String(query || "").trim().toLowerCase().replace(/\s+/g, " "));
  const today = localMoment(isoDay(now));
  const days = rest ? daysOf(rest, today) : time ? [today] : [];
  return days.slice(0, 2).map((d) => ({ date: isoDay(d), ...(time ? { time } : {}) }));
}

// Whether a query could still grow into a date or a reminder ("next",
// "in 3 d", "tomorrow 3", "remind me fri"): every word a number, a partial
// time or date, or the start of a word the dates know. While it could, the
// menu outlives a query nothing answers yet; "@alice is here" closes it.
const DATE_WORDS = [...RELATIVE.map(([w]) => w), ...Object.keys(ALIASES), ...Object.keys(UNITS), ...WEEKDAYS, ...MONTHS,
  "this", "next", "last", "in", "at", "ago", "noon", "midnight", "am", "pm", "remind", "me"];
export function datePending(query) {
  const words = String(query || "").trim().toLowerCase().split(/[\s,.]+/).filter(Boolean);
  return words.length > 0 && words.every((w) =>
    /^\d{1,4}(?:[-:]\d{0,2}){0,2}(?:st|nd|rd|th|[snrtap]|am|pm)?$/.test(w) || DATE_WORDS.some((d) => d.startsWith(w)));
}

// --- the trigger ------------------------------------------------------------

const MAX_QUERY = 40;
const TYPED_RE = new RegExp(`${BEFORE}@([^\\s@]{0,${MAX_QUERY}})$`);

// The "@" being typed before the caret: {start (the "@"), query}, or null.
// A fresh one is a word of its own ("@", "@tom"); while the menu is open
// (`open`: its start) the query may hold spaces, so "@next fri" and a
// two-word page title keep it open — until a line break, a run past
// MAX_QUERY, or nothing left to show that datePending does not excuse
// (the caller's rows).
export function mentionTrigger(value, cursor, open = null) {
  if (open != null && value[open] === "@" && cursor > open) {
    const query = value.slice(open + 1, cursor);
    if (!/\n/.test(query) && query.length <= MAX_QUERY && !/^\s/.test(query)) return { start: open, query };
  }
  const m = value.slice(0, cursor).match(TYPED_RE);
  return m ? { start: cursor - m[0].length, query: m[1] } : null;
}

// --- the menu's rows --------------------------------------------------------

export const MENTION_SECTIONS = { date: T("Date"), people: T("People"), pages: T("Link to page") };
const PEOPLE_ROWS = 5;
const PAGE_ROWS = 6;
const REMIND_WORDS = "remind me";

// How a username answers the query (lowercase): its start, then the start
// of one of its parts ("k" for "bob.k"), then the slash menu's fuzzy match.
function personRank(user, q) {
  const name = user.toLowerCase();
  if (name.startsWith(q)) return 0;
  if (name.split(/[._-]/).some((part) => part.startsWith(q))) return 1;
  const fuzzy = fuzzyIndices(name, q);
  return fuzzy ? 2 + fuzzy.cost : null;
}

// The "@" menu for `query`: rows under the MENTION_SECTIONS, in that order.
//   date    {kind: "date", date, time?} — "Today", or the dates the query
//           names; {kind: "remind", date, time} — "Remind me", tomorrow at
//           9 by default
//   people  {kind: "person", user, you} — `people` ([{username}], the
//           workspace's; `me` first, "(You)"), then {kind: "invite"} when
//           `canInvite`
//   pages   {kind: "page", id, title, isPdf, path} — `pages` by title
//           (rankRefPages: the recent ones before anything is typed), not
//           the open page (`rootId`)
// Every row has a `key` and its `section`. Empty when nothing answers,
// which closes the menu.
export function mentionRows({ query = "", people = [], me = "", pages = [], rootId = "", canInvite = false, now = new Date() }) {
  const q = query.trim().toLowerCase().replace(/\s+/g, " ");
  const rows = [];
  const add = (section, row) => rows.push({ section, ...row });

  // Date: a bare "@" offers today and a reminder for tomorrow morning; a
  // query, the dates it names and a reminder at the first of them;
  // "remind me <when>" only the reminder.
  const tomorrow = { date: isoDay(addDays(now, 1)), time: REMIND_TIME };
  let remindOnly = false, dateQuery = q;
  if (q.length >= 2 && REMIND_WORDS.startsWith(q)) { remindOnly = true; dateQuery = ""; }
  else if (q.startsWith("remind ")) { remindOnly = true; dateQuery = q.slice(7).replace(/^me\b ?/, ""); }
  const dates = q && dateQuery ? parseDateQuery(dateQuery, now) : [];
  if (!q) add("date", { kind: "date", key: "date", date: isoDay(now) });
  else if (!remindOnly) dates.forEach((d, i) => add("date", { kind: "date", key: `date${i}`, ...d }));
  let remind = !q || (remindOnly && !dateQuery) ? tomorrow : dates[0] ? { time: REMIND_TIME, ...dates[0] } : null;
  // A time alone ("@9am" once nine has passed) is the next one; a moment
  // gone by reminds no one.
  if (remind && remindAt(remind) <= now && !splitTime(dateQuery)[0]) remind = { ...remind, date: tomorrow.date };
  if (remind && remindAt(remind) <= now) remind = null;
  if (remind && me) add("date", { kind: "remind", key: "remind", date: remind.date, time: remind.time });

  // People.
  const everyone = [...new Set([me, ...people.map((p) => p.username)])].filter(Boolean);
  const matched = q
    ? everyone.map((u, i) => [u, i, personRank(u, q)]).filter(([, , r]) => r != null).sort((a, b) => a[2] - b[2] || a[1] - b[1])
    : everyone.map((u, i) => [u, i]);
  matched.slice(0, PEOPLE_ROWS).forEach(([user]) => add("people", { kind: "person", key: `person:${user}`, user, you: user === me }));
  if (canInvite && (!q || (q.length >= 2 && "invite".startsWith(q)))) add("people", { kind: "invite", key: "invite" });

  // Link to page.
  rankRefPages(pages, q, rootId).slice(0, PAGE_ROWS).forEach((p) => add("pages", {
    kind: "page", key: `page:${p.id}`, id: p.id, title: p.content, isPdf: !!p._attachment,
    path: p._folderChips?.[0]?.name || "",
  }));
  return rows;
}

// What a picked row writes in place of the typed "@query" (an invite
// writes nothing: it opens the Share popover).
export function mentionText(row, me) {
  if (row.kind === "date") return dateToken(row);
  if (row.kind === "remind") return dateToken({ date: row.date, time: row.time, remind: me });
  if (row.kind === "person") return personToken(row.user);
  if (row.kind === "page") return `[[${row.id}]]`;
  return "";
}
