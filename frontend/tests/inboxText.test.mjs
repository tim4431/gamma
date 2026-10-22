// The Inbox as the app reads it: sentences, snippets, days, jumps, the
// rings and the marks made before the server answers
// (src/inbox/inboxText.js, docs/dev/comments.md "In the app").
import test from "node:test";
import assert from "node:assert/strict";
import {
  applyMark, inFilter, inboxDays, inboxJump, inboxSentence, inboxSnippet, inboxUrl, isFolderShare, isUnread,
  itemsToRing, mergeListing, RING_AT_ONCE, sameListing,
} from "../src/inbox/inboxText.js";

const item = (id, at, extra = {}) => ({
  id, kind: "mention", actor: "ann", workspace_id: "ws1", workspace_name: "Lab", page_id: "pg1",
  page_title: "Draft", block_id: "b1", thread_id: "", comment_id: "", share_token: "", snippet: "Hi @ben",
  at, read_at: null, archived_at: null, ...extra,
});

test("one sentence per kind, with the actor, the page and the workspace", () => {
  const at = "2026-10-20T10:00:00.000000Z";
  assert.equal(inboxSentence(item("a", at)), "ann mentioned you in Draft");
  assert.equal(inboxSentence(item("a", at, { kind: "comment_mention" })), "ann mentioned you in a comment on Draft");
  assert.equal(inboxSentence(item("a", at, { kind: "reply" })), "ann replied to a comment on Draft");
  assert.equal(inboxSentence(item("a", at, { kind: "resolved" })), "ann resolved your comment on Draft");
  assert.equal(inboxSentence(item("a", at, { kind: "shared", block_id: "" })), "ann shared Draft with you");
  assert.equal(inboxSentence(item("a", at, { kind: "shared", page_id: "", block_id: "f1", page_title: "Reading" })),
    "ann shared the folder Reading with you");
  assert.equal(inboxSentence(item("a", at, { kind: "invited", page_id: "", block_id: "" })), "ann added you to Lab");
  assert.equal(inboxSentence(item("a", at, { actor: "", page_title: "" })), "Someone mentioned you in Untitled",
    "a deleted account and an untitled page");
});

test("the snippet is the note's or the comment's words on one line; shares and invitations have none", () => {
  const at = "2026-10-20T10:00:00.000000Z";
  assert.equal(inboxSnippet(item("a", at, { snippet: "**Look**\n\nat  this" })), "Look at this");
  assert.equal(inboxSnippet(item("a", at, { snippet: "x".repeat(300) }), 20), `${"x".repeat(19)}…`);
  assert.equal(inboxSnippet(item("a", at, { kind: "shared", snippet: "Draft" })), "");
  assert.equal(inboxSnippet(item("a", at, { kind: "invited", snippet: "Lab" })), "");
});

test("the tabs: Unread is neither read nor archived, All is not archived", () => {
  const at = "2026-10-20T10:00:00.000000Z";
  const fresh = item("a", at);
  const read = item("b", at, { read_at: at });
  const gone = item("c", at, { archived_at: at });
  assert.deepEqual(["unread", "all", "archived"].map((f) => [fresh, read, gone].filter((i) => inFilter(i, f)).map((i) => i.id)),
    [["a"], ["a", "b"], ["c"]]);
  assert.equal(isUnread(gone), false, "an archived item counts as read");
});

test("the list is grouped by day: Today, Yesterday, then dates", () => {
  const now = new Date(2026, 9, 20, 15, 0).getTime();
  const local = (d, h) => new Date(2026, 9, d, h).toISOString();
  const days = inboxDays([
    item("a", local(20, 14)), item("b", local(20, 1)), item("c", local(19, 23)), item("d", local(3, 12)),
    item("e", new Date(2025, 0, 2, 12).toISOString()),
  ], now);
  assert.deepEqual(days.map((d) => d.items.map((i) => i.id)), [["a", "b"], ["c"], ["d"], ["e"]]);
  assert.deepEqual(days.slice(0, 2).map((d) => d.label), ["Today", "Yesterday"]);
  assert.match(days[2].label, /Oct/);
  assert.doesNotMatch(days[2].label, /2026/, "this year's dates leave the year out");
  assert.match(days[3].label, /2025/);
});

test("a click goes in place in this workspace, by navigation elsewhere, through the share that reached it", () => {
  const at = "2026-10-20T10:00:00.000000Z";
  assert.deepEqual(inboxJump(item("a", at, { kind: "reply", thread_id: "t1" }), "ws1"),
    { to: "here", pageId: "pg1", blockId: "b1", threadId: "t1" });
  assert.deepEqual(inboxJump(item("a", at, { kind: "reply", thread_id: "t1" }), "ws2"),
    { to: "url", url: "/?ws=ws1&page=pg1&block=b1&thread=t1" });
  assert.deepEqual(inboxJump(item("a", at, { kind: "reply", thread_id: "t1", share_token: "tok" }), "ws1"),
    { to: "url", url: "/?share=tok&page=pg1&block=b1&thread=t1" }, "a share-reached item opens through its share");
  assert.deepEqual(inboxJump(item("a", at, { kind: "shared", page_id: "", block_id: "f1", share_token: "tok" }), "ws2"),
    { to: "url", url: "/?share=tok" }, "a folder share opens its listing");
  assert.deepEqual(inboxJump(item("a", at, { kind: "shared", page_id: "", block_id: "f1" }), "ws2"),
    { to: "url", url: "/?ws=ws1&folder=f1" }, "a member's folder share in another workspace opens the folder");
  assert.deepEqual(inboxJump(item("a", at, { kind: "invited", page_id: "", block_id: "", workspace_id: "ws9" }), "ws1"),
    { to: "workspace", workspaceId: "ws9" });
  assert.equal(isFolderShare(item("a", at, { kind: "shared", page_id: "", block_id: "f1" })), true);
});

test("an address leaves out the block when it is the page, and keeps a base path", () => {
  assert.equal(inboxUrl({ ws: "w", page: "p", block: "p", thread: "t" }), "/?ws=w&page=p&thread=t");
  assert.equal(inboxUrl({ share: "s", ws: "w", page: "p" }, "/app/"), "/app/?share=s&page=p", "a share needs no workspace");
});

test("a listing's first page keeps the older items loaded before, and drops what it says again", () => {
  const prev = [item("a", "5"), item("b", "4"), item("c", "3"), item("d", "2"), item("e", "1")];
  const got = { items: [item("n", "6"), item("a", "5"), item("b", "4")], more: true };
  const merged = mergeListing(prev, got, false);
  assert.deepEqual(merged.items.map((i) => i.id), ["n", "a", "b", "c", "d", "e"]);
  assert.equal(merged.more, false, "the older ones held are all there were");
  assert.equal(mergeListing(prev, got, true).more, true);
  const whole = mergeListing(prev, { items: [item("a", "5")], more: false });
  assert.deepEqual(whole.items.map((i) => i.id), ["a"], "a listing that says it is all replaces the rest");
});

test("a poll that changes nothing is the same listing; any field, the tab or its state differs", () => {
  const held = { filter: "unread", items: [item("a", "2"), item("b", "1")], more: false, loaded: true, failed: false };
  const again = { ...held, items: held.items.map((i) => ({ ...i })) };
  assert.equal(sameListing(held, again), true, "new objects, the same fields");
  assert.equal(sameListing(held, { ...again, items: [again.items[0], { ...again.items[1], read_at: "x" }] }), false);
  assert.equal(sameListing(held, { ...again, items: [again.items[0], { ...again.items[1], page_title: "Renamed" }] }), false);
  assert.equal(sameListing(held, { ...again, items: again.items.slice(1) }), false);
  assert.equal(sameListing(held, { ...again, more: true }), false);
  assert.equal(sameListing(held, { ...again, filter: "all" }), false);
  assert.equal(sameListing({ ...held, loaded: false }, again), false);
});

test("a mark changes the items held, drops those leaving the tab, and says what it does to the count", () => {
  const now = "2026-10-20T12:00:00.000000Z";
  const items = [item("a", "3"), item("b", "2", { read_at: "x" }), item("c", "1")];
  const read = applyMark(items, { ids: ["a"] }, { read: true }, "unread", now);
  assert.deepEqual(read.items.map((i) => i.id), ["b", "c"], "a read item leaves Unread");
  assert.equal(read.unreadDelta, -1);
  const kept = applyMark(items, { ids: ["b"] }, { read: true }, "all", now);
  assert.equal(kept.items[1].read_at, "x", "a stamp already set is kept");
  assert.equal(kept.unreadDelta, 0);
  const unread = applyMark(items, { ids: ["b"] }, { read: false }, "all", now);
  assert.equal(unread.items[1].read_at, null);
  assert.equal(unread.unreadDelta, 1);
  const archived = applyMark(items, { ids: ["a", "b"] }, { archived: true }, "all", now);
  assert.deepEqual(archived.items.map((i) => i.id), ["c"]);
  assert.equal(archived.unreadDelta, -1, "archiving an unread item takes it off the count");
  const all = applyMark(items, { all: true }, { read: true }, "all", now);
  assert.ok(all.items.every((i) => i.read_at));
  assert.equal(all.unreadDelta, -2);
  const back = applyMark([item("z", "1", { archived_at: "y" })], { ids: ["z"] }, { archived: false }, "archived", now);
  assert.deepEqual(back.items, [], "restored: out of Archived");
  assert.equal(back.unreadDelta, 1);
});

test("a system notification rings for fresh unread items not rung yet, a few at a time", () => {
  const now = Date.parse("2026-10-20T12:00:00Z");
  const ago = (min) => new Date(now - min * 60000).toISOString().replace("Z", "000Z");
  const items = [
    item("a", ago(1)), item("b", ago(2), { read_at: ago(1) }), item("c", ago(3)), item("d", ago(60)),
  ];
  assert.deepEqual(itemsToRing(items, ["c"], now).map((i) => i.id), ["a"], "read, rung and old items stay quiet");
  const many = Array.from({ length: 9 }, (_, i) => item(`n${i}`, ago(i)));
  assert.equal(itemsToRing(many, [], now).length, RING_AT_ONCE);
});
