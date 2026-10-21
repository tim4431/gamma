// The open page's comment threads as the app folds, filters and changes
// them before the server answers (src/comments/comments.js,
// docs/dev/comments.md "In the app").
import test from "node:test";
import assert from "node:assert/strict";
import {
  addComment, anchorSnippet, canDeleteComment, canEditComment, deleteComment, editComment, findComment, foldThreads,
  isEdited, keepThreads, popoverThreads, resolveThread, shortCount, threadLink, threadsIn, upsertThread,
} from "../src/comments/comments.js";

const c = (id, thread, author, at, extra = {}) => ({
  id, thread_id: thread, author, body: `${id} text`, created_at: at, updated_at: at, deleted: false, ...extra,
});
const th = (id, block, at, comments, extra = {}) => ({
  id, block_id: block, page_id: "page", resolved_at: null, resolved_by: null, created_at: at,
  last_at: comments.reduce((m, x) => (x.created_at > m ? x.created_at : m), at), comments, ...extra,
});
const page = () => [
  th("t2", "b1", "2026-10-02T00:00:00.000000Z", [c("t2", "t2", "ann", "2026-10-02T00:00:00.000000Z")]),
  th("t1", "b1", "2026-10-01T00:00:00.000000Z", [
    c("t1", "t1", "ann", "2026-10-01T00:00:00.000000Z"),
    c("r1", "t1", "ben", "2026-10-05T00:00:00.000000Z"),
  ]),
  th("t3", "b2", "2026-10-03T00:00:00.000000Z", [c("t3", "t3", "ben", "2026-10-03T00:00:00.000000Z")],
    { resolved_at: "2026-10-04T00:00:00.000000Z", resolved_by: "ann" }),
  th("tp", "page", "2026-10-06T00:00:00.000000Z", [c("tp", "tp", "ben", "2026-10-06T00:00:00.000000Z")]),
];

test("foldThreads groups a block's threads oldest first and counts only the open ones", () => {
  const { byBlock, counts } = foldThreads(page());
  assert.deepEqual(byBlock.get("b1").map((x) => x.id), ["t1", "t2"]);
  assert.deepEqual(byBlock.get("b2").map((x) => x.id), ["t3"]);
  assert.equal(counts.get("b1"), 2);
  assert.equal(counts.has("b2"), false, "a block whose threads are all resolved has no count");
  assert.equal(counts.get("page"), 1, "the page's own thread is on its root");
});

test("threadsIn filters Open or Resolved and puts the latest activity first", () => {
  assert.deepEqual(threadsIn(page(), "open").map((x) => x.id), ["tp", "t1", "t2"], "t1's reply is newer than t2");
  assert.deepEqual(threadsIn(page(), "resolved").map((x) => x.id), ["t3"]);
});

test("popoverThreads shows the open threads, and a resolved one only when it is asked for", () => {
  const { byBlock } = foldThreads(page());
  assert.deepEqual(popoverThreads(byBlock.get("b2")).map((x) => x.id), []);
  assert.deepEqual(popoverThreads(byBlock.get("b2"), { focus: "t3" }).map((x) => x.id), ["t3"]);
  assert.deepEqual(popoverThreads(byBlock.get("b2"), { withResolved: true }).map((x) => x.id), ["t3"]);
  assert.deepEqual(popoverThreads(undefined), []);
});

test("keepThreads keeps every thread that reads the same, and the list itself when nothing changed", () => {
  const before = page();
  assert.equal(keepThreads(before, page()), before, "a refetch that changed nothing");
  const changed = page();
  changed[0] = { ...changed[0], comments: [{ ...changed[0].comments[0], body: "new" }] };
  const kept = keepThreads(before, changed);
  assert.notEqual(kept, before);
  assert.notEqual(kept[0], before[0], "the changed thread is the new one");
  assert.equal(kept[1], before[1], "the others keep their identity");
  assert.equal(keepThreads(null, changed), changed);
});

test("addComment opens a thread or appends a reply, once", () => {
  const root = c("n1", "n1", "ann", "2026-10-07T00:00:00.000000Z");
  let list = addComment(page(), root, { blockId: "b2", pageId: "page" });
  const added = list.find((x) => x.id === "n1");
  assert.equal(added.block_id, "b2");
  assert.equal(added.last_at, root.created_at);
  assert.equal(addComment(list, root, { blockId: "b2" }), list, "an id the page holds changes nothing");
  const reply = c("n2", "t2", "ben", "2026-10-08T00:00:00.000000Z");
  list = addComment(list, reply);
  const t2 = list.find((x) => x.id === "t2");
  assert.deepEqual(t2.comments.map((x) => x.id), ["t2", "n2"]);
  assert.equal(t2.last_at, reply.created_at);
  assert.deepEqual(addComment(list, c("n3", "gone", "ben", "2026-10-09")), list, "a reply to a thread the page lacks");
});

test("editComment changes the text and moves the thread's activity", () => {
  const at = "2026-10-09T00:00:00.000000Z";
  const list = editComment(page(), "r1", "fixed", at);
  const { thread, comment } = findComment(list, "r1");
  assert.equal(comment.body, "fixed");
  assert.equal(comment.updated_at, at);
  assert.equal(thread.last_at, at);
  assert.ok(isEdited(comment));
  assert.ok(!isEdited(findComment(list, "t1").comment));
});

test("deleteComment follows the server: a root with replies is a tombstone, the last reply takes it along", () => {
  let list = deleteComment(page(), "t1");
  const tomb = findComment(list, "t1").comment;
  assert.equal(tomb.deleted, true);
  assert.equal(tomb.body, "");
  assert.ok(!isEdited(tomb));
  list = deleteComment(list, "r1");
  assert.equal(list.find((x) => x.id === "t1"), undefined, "the tombstone went with its last reply");
  list = deleteComment(list, "t2");
  assert.equal(list.find((x) => x.id === "t2"), undefined, "a thread of one comment goes");
  assert.equal(deleteComment(list, "nothing"), list);
  const reply = deleteComment(page(), "r1");
  assert.deepEqual(reply.find((x) => x.id === "t1").comments.map((x) => x.id), ["t1"], "a reply alone goes");
});

test("resolveThread resolves and reopens", () => {
  const at = "2026-10-10T00:00:00.000000Z";
  let list = resolveThread(page(), "t1", true, "ben", at);
  assert.equal(list.find((x) => x.id === "t1").resolved_by, "ben");
  assert.equal(foldThreads(list).counts.get("b1"), 1);
  list = resolveThread(list, "t1", false, "ann", at);
  assert.equal(list.find((x) => x.id === "t1").resolved_at, null);
  assert.equal(list.find((x) => x.id === "t1").resolved_by, null);
});

test("upsertThread replaces a thread by id or appends it", () => {
  const list = page();
  const answer = { ...list[0], resolved_at: "x" };
  assert.equal(upsertThread(list, answer)[0].resolved_at, "x");
  assert.equal(upsertThread(list, { ...answer, id: "new" }).length, list.length + 1);
  assert.equal(upsertThread(list, null), list);
});

test("who may edit and delete: the author, and for deleting the owner too; a tombstone no one", () => {
  const mine = c("x", "x", "ann", "t");
  assert.ok(canEditComment(mine, "ann"));
  assert.ok(!canEditComment(mine, "ben"));
  assert.ok(!canEditComment(mine, ""));
  assert.ok(canDeleteComment(mine, "ben", true), "the owner deletes anyone's");
  assert.ok(!canDeleteComment(mine, "ben"));
  assert.ok(!canDeleteComment({ ...mine, deleted: true }, "ann", true));
});

test("shortCount caps a badge at 9+", () => {
  assert.equal(shortCount(1), "1");
  assert.equal(shortCount(9), "9");
  assert.equal(shortCount(10), "9+");
});

test("anchorSnippet is the anchor's words on one line", () => {
  assert.equal(anchorSnippet("## Plan\n- **ship** it"), "Plan ship it");
  assert.equal(anchorSnippet("x".repeat(100), 10), `${"x".repeat(9)}…`);
  assert.equal(anchorSnippet(""), "");
  // a [[link]] reads as its label; one the lookup does not know stays
  const labelOf = (id) => (id === "pg1" ? { content: "Appendix tables" } : null);
  assert.equal(anchorSnippet("See [[pg1]] and [[zz9]]", 80, labelOf), "See Appendix tables and zz9");
  assert.equal(anchorSnippet("See [[pg1]]"), "See pg1");
});

test("threadLink names the page, the block (not twice for a page thread) and the thread", () => {
  assert.equal(threadLink("https://g.example", { pageId: "p", blockId: "b", threadId: "t" }),
    "https://g.example/?page=p&block=b&thread=t");
  assert.equal(threadLink("https://g.example", { pageId: "p", blockId: "p", threadId: "t" }, { share: "tok" }),
    "https://g.example/?share=tok&page=p&thread=t");
  // a note's thread in a share view: the folder share opens `page`, the thread its block
  assert.equal(threadLink("https://g.example", { pageId: "p", blockId: "b", threadId: "t" }, { share: "tok" }),
    "https://g.example/?share=tok&page=p&block=b&thread=t");
});
