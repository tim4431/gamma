// The @ mentions (editor/mentions.js): the syntax against the cases the
// backend runs too (tests/shared/mentions.json at the repository root), how
// a date reads, the dates a query names, the trigger and the menu's rows.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import {
  dateLabel, datePending, dateToken, mentionRows, mentionText, mentionTrigger, parseDateQuery, remindAt, scanMentions,
} from "../src/editor/mentions.js";

const shared = JSON.parse(await readFile(new URL("../../tests/shared/mentions.json", import.meta.url), "utf8"));
// Friday 9 October 2026, 3 pm local.
const NOW = new Date(2026, 9, 9, 15, 0);
const dates = (q, now = NOW) => parseDateQuery(q, now).map((d) => (d.time ? `${d.date} ${d.time}` : d.date));

test("scanMentions reads every shared case as the server does", () => {
  for (const c of shared.scan) {
    const got = scanMentions(c.text).map(({ from, to, ...token }) => {
      assert.equal(c.text.slice(from, to), token.raw, c.note);
      return token;
    });
    assert.deepEqual(got, c.tokens, c.note);
  }
});

test("a date token round-trips", () => {
  for (const token of [{ date: "2026-10-09" }, { date: "2026-10-10", time: "14:30" }, { date: "2026-10-10", time: "09:00", remind: "bob.k" }]) {
    const [{ kind, raw, from, to, ...back }] = scanMentions(`x ${dateToken(token)} y`);
    assert.equal(kind, "date");
    assert.deepEqual(back, token);
  }
});

test("a date reads as Today / Tomorrow / Yesterday, else the full date, then its time", () => {
  assert.equal(dateLabel({ date: "2026-10-09" }, NOW), "Today");
  assert.equal(dateLabel({ date: "2026-10-10", time: "09:00" }, NOW), "Tomorrow 9:00 AM");
  assert.equal(dateLabel({ date: "2026-10-08" }, NOW), "Yesterday");
  assert.equal(dateLabel({ date: "2026-10-12" }, NOW), "October 12, 2026");
  assert.equal(remindAt({ date: "2026-10-10" }).getHours(), 9, "a reminder without a time is due at nine");
});

test("relative words, their prefixes and a time", () => {
  assert.deepEqual(dates("today"), ["2026-10-09"]);
  assert.deepEqual(dates("tom"), ["2026-10-10"]);
  assert.deepEqual(dates("t"), ["2026-10-09", "2026-10-10"], "ambiguous while being typed");
  assert.deepEqual(dates("tmr"), ["2026-10-10"]);
  assert.deepEqual(dates("tomorrow 3pm"), ["2026-10-10 15:00"]);
  assert.deepEqual(dates("Tomorrow at 9:30 am"), ["2026-10-10 09:30"]);
  assert.deepEqual(dates("yesterday 14:05"), ["2026-10-08 14:05"]);
  assert.deepEqual(dates("9am"), ["2026-10-09 09:00"], "a time alone is today");
  assert.deepEqual(dates("noon"), ["2026-10-09 12:00"]);
  assert.deepEqual(dates("13pm"), [], "no such time");
  assert.deepEqual(dates("foo"), []);
});

test("weekdays: the coming one, this week's, next week's, the last one", () => {
  assert.deepEqual(dates("mon"), ["2026-10-12"]);
  assert.deepEqual(dates("fri"), ["2026-10-16"], "a Friday's bare Friday is the next one");
  assert.deepEqual(dates("this fri"), ["2026-10-09"]);
  assert.deepEqual(dates("next wed"), ["2026-10-14"], "the Wednesday of next week");
  assert.deepEqual(dates("last monday"), ["2026-10-05"]);
  assert.deepEqual(dates("th"), ["2026-10-15"]);
  assert.deepEqual(dates("w"), [], "one letter names no weekday");
});

test("spans of time and calendar dates", () => {
  assert.deepEqual(dates("in 3 days"), ["2026-10-12"]);
  assert.deepEqual(dates("2 weeks ago"), ["2026-09-25"]);
  assert.deepEqual(dates("next month"), ["2026-11-09"]);
  assert.deepEqual(dates("in 1 month", new Date(2026, 0, 31)), ["2026-02-28"], "clamped to the month's end");
  assert.deepEqual(dates("oct 12"), ["2026-10-12"]);
  assert.deepEqual(dates("12th October 2027"), ["2027-10-12"]);
  assert.deepEqual(dates("dec 25, 2026 6pm"), ["2026-12-25 18:00"]);
  assert.deepEqual(dates("2026-1-5"), ["2026-01-05"]);
  assert.deepEqual(dates("feb 30"), [], "no such day");
  assert.deepEqual(dates("oct"), [], "a month without a day");
});

test("a date still being typed keeps the menu; other text does not", () => {
  for (const q of ["next", "next f", "in 3", "in 3 d", "tomorrow 3", "tomorrow at", "oct 1", "2026-1", "remind me fri", "12th"]) {
    assert.equal(datePending(q), true, q);
  }
  for (const q of ["", "alice is", "nobody", "the design"]) assert.equal(datePending(q), false, q);
});

test("the trigger: a fresh @ starts a word; an open menu's query may hold spaces", () => {
  assert.deepEqual(mentionTrigger("Hi @", 4), { start: 3, query: "" });
  assert.deepEqual(mentionTrigger("Hi @tom", 7), { start: 3, query: "tom" });
  assert.deepEqual(mentionTrigger("请@b", 3), { start: 1, query: "b" });
  assert.equal(mentionTrigger("mail a@b", 8), null, "an e-mail address");
  assert.equal(mentionTrigger("Hi @next fri", 12), null, "a space ends a fresh one");
  assert.deepEqual(mentionTrigger("Hi @next fri", 12, 3), { start: 3, query: "next fri" });
  assert.equal(mentionTrigger("Hi @next\nfri", 12, 3), null, "a line break ends it");
  assert.equal(mentionTrigger("Hi @ x", 6, 3), null, "a space right after the @");
});

const PEOPLE = [{ username: "alice" }, { username: "bob.k" }, { username: "carol" }];
const PAGES = [
  { id: "p1", content: "Powerloss", _updatedAt: "2026-10-08", _folderChips: [{ id: "f", name: "General / Docs" }] },
  { id: "p2", content: "The Design", _updatedAt: "2026-10-07", _attachment: { kind: "pdf" } },
  { id: "open", content: "This page", _updatedAt: "2026-10-09" },
];
const rows = (query, extra = {}) => mentionRows({ query, people: PEOPLE, me: "carol", pages: PAGES, rootId: "open", canInvite: true, now: NOW, ...extra });
const summary = (list) => list.map((r) => [r.section, r.kind, r.user || r.id || [r.date, r.time].filter(Boolean).join(" ")].filter(Boolean).join(":"));

test("a bare @: today and a reminder, the people (you first) and Invite, the recent pages", () => {
  assert.deepEqual(summary(rows("")), [
    "date:date:2026-10-09", "date:remind:2026-10-10 09:00",
    "people:person:carol", "people:person:alice", "people:person:bob.k", "people:invite",
    "pages:page:p1", "pages:page:p2",
  ]);
  const [, , you] = rows("");
  assert.equal(you.you, true);
  assert.equal(rows("").at(-2).path, "General / Docs");
  assert.equal(rows("", { canInvite: false }).some((r) => r.kind === "invite"), false);
  assert.equal(rows("", { me: "" }).some((r) => r.kind === "remind"), false, "a reminder needs someone to remind");
});

test("a query narrows every section", () => {
  assert.deepEqual(summary(rows("tomorrow 4pm")), ["date:date:2026-10-10 16:00", "date:remind:2026-10-10 16:00"]);
  assert.deepEqual(summary(rows("9am")), ["date:date:2026-10-09 09:00", "date:remind:2026-10-10 09:00"],
    "a time gone by today reminds tomorrow");
  assert.deepEqual(summary(rows("yesterday")), ["date:date:2026-10-08"], "no reminder in the past");
  assert.deepEqual(summary(rows("rem")), ["date:remind:2026-10-10 09:00"]);
  assert.deepEqual(summary(rows("remind me next fri 8:30")), ["date:remind:2026-10-16 08:30"]);
  assert.deepEqual(summary(rows("k")), ["people:person:bob.k"], "a part of a name");
  assert.deepEqual(summary(rows("inv")), ["people:invite"]);
  assert.deepEqual(summary(rows("design")), ["pages:page:p2"]);
  assert.deepEqual(rows("zzzz"), [], "nothing answers: the menu closes");
});

test("a picked row's text", () => {
  const [today, remind] = rows("");
  assert.equal(mentionText(today, "carol"), "@2026-10-09");
  assert.equal(mentionText(remind, "carol"), "@2026-10-10 09:00 (remind @carol)");
  assert.equal(mentionText({ kind: "person", user: "bob.k" }), "@bob.k");
  assert.equal(mentionText({ kind: "page", id: "p1" }), "[[p1]]");
  assert.equal(mentionText({ kind: "invite" }), "");
});
