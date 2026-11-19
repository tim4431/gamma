// The open page's comment threads as the app folds, filters and changes
// them before the server answers (src/comments/comments.js,
// docs/dev/comments.md "In the app").
import test from "node:test";
import assert from "node:assert/strict";
import {
  abandonedCommentHighlight, addComment, agentFor, anchorSnippet, anchorWords, authorLabel, authorTitle, canDeleteComment, canEditComment, canReportComment, cardedAnchors,
  commentOnlyHighlight, deleteComment, EAGER_TEXT, editComment, estimatedLines, findComment, foldThreads, isEdited, keepThreads,
  laterComments, popoverThreads, refusalChip, resolveThread, resolvedBy, shortCount, threadLink, threadPlace, threadsIn, turnChip, turnEnd,
  turnRefusal, upsertThread,
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
  const same = findComment(list, "r1").comment.body;
  assert.equal(editComment(list, "r1", same, "2026-10-10T00:00:00.000000Z")[1], list[1], "the same text changes nothing (Send again)");
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

test("an agent's comment: its name on the line, then its computer and its person; the person deletes it, never edits it", () => {
  const agents = c("x", "x", "ann", "t", { agent: "Claude Code", agent_kind: "connection" });
  assert.equal(authorLabel(agents), "Claude Code");
  assert.equal(agentFor(agents), "for ann");
  assert.equal(authorTitle(agents), "Written by Claude Code with ann's connection");
  assert.equal(authorTitle({ ...agents, author: "" }), "Written by Claude Code with Someone's connection", "an account gone");
  const local = c("z", "z", "tim", "t", { agent: "Codex", agent_kind: "computer", agent_on: "TIM-PC" });
  assert.equal(authorLabel(local), "Codex");
  assert.equal(agentFor(local), "on TIM-PC, for tim", "the byline after the agent's name");
  assert.equal(authorTitle(local), "Written by Codex on TIM-PC, for tim", "a computer's agent");
  assert.equal(authorTitle({ ...local, author: "" }), "Written by Codex on TIM-PC, for Someone");
  const lent = { ...local, author: "bob", agent_owner: "alice" };
  assert.equal(agentFor(lent), "on TIM-PC, for bob", "a co-author's: the person it wrote for");
  assert.equal(authorTitle(lent), "Written by alice's Codex on TIM-PC, for bob", "and its owner, where there is room");
  const unknown = c("u", "u", "tim", "t", { agent: "Codex", agent_kind: "computer" });
  assert.equal(agentFor(unknown), "for tim", "a computer no longer known");
  assert.equal(authorTitle(unknown), "Written by Codex, for tim");
  const gamma = c("g", "g", "tim", "t", { agent: "Gamma AI", agent_kind: "gamma" });
  assert.equal(authorLabel(gamma), "Gamma AI");
  assert.equal(authorTitle(gamma), "Written by Gamma AI, for tim");
  assert.ok(!canEditComment(local, "tim"));
  const own = c("y", "y", "ann", "t", { agent: "" });
  assert.equal(authorLabel(own), "ann");
  assert.equal(authorTitle(own), "ann");
  assert.equal(agentFor(own), "", "a person's line adds nothing");
  assert.ok(!canEditComment(agents, "ann"), "the agent's to change");
  assert.ok(canEditComment(own, "ann"));
  assert.ok(canDeleteComment(agents, "ann"), "the person's to delete");
  assert.ok(canDeleteComment(agents, "ben", true), "the owner's, any");
  assert.ok(!canDeleteComment(agents, "ben"), "another's, none");
  assert.ok(!canDeleteComment(agents, ""), "a link visitor's, none");
  assert.ok(!canDeleteComment({ ...agents, deleted: true }, "ann"));
});

test("a thread resolved here names no agent; an agent's resolve is the server's", () => {
  const codex = { id: "computer:pc:codex", kind: "computer", name: "Codex", on: "TIM-PC" };
  const list = page().map((x) => (x.id === "t3" ? { ...x, resolved_by: "ann", resolved_agent: codex } : x));
  assert.equal(resolvedBy(list.find((x) => x.id === "t3")), "Codex on TIM-PC", "the agent, worded here");
  const reopened = resolveThread(list, "t3", false, "ann", "2026-10-10T00:00:00.000000Z");
  assert.equal(reopened.find((x) => x.id === "t3").resolved_agent, null);
  const again = resolveThread(list, "t3", true, "ann", "2026-10-10T00:00:00.000000Z");
  assert.equal(again.find((x) => x.id === "t3").resolved_agent, null);
  assert.equal(resolvedBy(again.find((x) => x.id === "t3")), "ann", "the person");
  assert.equal(resolvedBy({ resolved_by: null, resolved_agent: { id: "gamma", kind: "gamma", name: "Gamma AI" } }), "Gamma AI");
});

test("what a thread is on, in words: the quote, the text, else a caption-less ink group's page", () => {
  const pos = { pageNumber: 4, width: 612, height: 792, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [] };
  assert.equal(anchorWords({ id: "h", content: "my note", properties: { quote: "the quote", pdf_position: pos } }), "the quote");
  assert.equal(anchorWords({ id: "n", content: "a note", properties: {} }), "a note");
  assert.equal(anchorWords({ id: "g", content: "", properties: { ink_url: "/api/uploads/a.ink", pdf_position: pos } }), "Handwriting on page 4");
  assert.equal(anchorWords({ id: "g", content: "the arrow", properties: { ink_url: "/api/uploads/a.ink", pdf_position: pos } }), "the arrow",
    "a caption");
  assert.equal(anchorWords({ id: "g", content: "", properties: { ink_url: "/api/uploads/a.ink" } }), "", "a group on a sheet");
  const region = { width: 1200, height: 800, boundingRect: { x1: 10, y1: 20, x2: 300, y2: 200 }, rects: [{ x1: 10, y1: 20, x2: 300, y2: 200 }], hash: "i1" };
  assert.equal(anchorWords({ id: "r", content: "", properties: { image_position: region } }), "Region of the picture");
  assert.equal(anchorWords({ id: "r", content: "the legend", properties: { image_position: region } }), "the legend", "a region's note");
  assert.equal(anchorWords(undefined), "", "a block the page does not show");
});

test("a thread's chip follows the reader's turn of it: queued, working, waiting for an answer here or on the computer", () => {
  const on = (turn, extra = {}) => ({ ...page()[0], agent_chat: { conversation_id: "c1", agent: "codex", label: "Codex", turn, ...extra } });
  const turn = (extra) => ({ turn_id: "u1", state: "running", asking: false, waiting: false, computer: "", ...extra });
  const chip = (kind, label, extra = {}) => ({ kind, label, title: label, tone: "pending", live: false, stop: true, open: false, person: "", ...extra });
  assert.equal(turnChip(page()[0]), null, "no conversation");
  assert.equal(turnChip(on(null)), null, "its turn ended: the agent's reply says the rest");
  assert.deepEqual(turnChip(on(turn())), chip("working", "Codex is working…", { live: true }));
  assert.deepEqual(turnChip(on(turn({ state: "queued" }))), chip("queued", "Waiting for Codex"), "in the queue: not live");
  assert.equal(turnChip(on(turn({ state: "queued", computer: "TIM-PC" }))).label, "Waiting for TIM-PC");
  assert.deepEqual(turnChip(on(turn({ asking: true }))), chip("asking", "Codex is waiting for you", { open: true }), "its card is in the chat");
  assert.deepEqual(turnChip(on(turn({ waiting: true, computer: "TIM-PC" }))),
    chip("waiting", "Waiting for you on TIM-PC", { title: "Codex is waiting for your answer on TIM-PC" }), "short words, the whole in its title");
  assert.equal(turnChip(on(turn(), { for: "bob" })).person, "bob", "a co-author's turn of the reader's agent");
});

test("the note a turn's end wrote reads as that turn's state, in the reader's words", () => {
  const note = (end) => c("e", "t1", "tim", "t", { agent: "Codex", agent_kind: "computer", body: "Codex could not finish.", turn_end: end });
  assert.deepEqual(turnEnd(note({ state: "failed", error: "Codex could not finish: the model refused to start" })),
    { kind: "failed", label: "Codex could not finish", title: "Codex could not finish", tone: "failed", live: false,
      detail: "Codex could not finish: the model refused to start" });
  assert.deepEqual(turnEnd(note({ state: "stopped", error: "" })),
    { kind: "stopped", label: "Stopped before Codex answered", title: "Stopped before Codex answered", tone: "", live: false, detail: "" });
  assert.equal(turnEnd(note({ state: "empty", error: "" })).label, "Codex finished without a reply");
  assert.equal(turnEnd(note(undefined)), null, "an answer");
  assert.equal(turnEnd(c("p", "t1", "tim", "t", { turn_end: { state: "failed" } })), null, "a person's comment");
});

test("a send that started no turn stays on its thread: not sent, why, and Send again where it can help", () => {
  const refused = (code, extra = {}) => ({ agent: "Codex", agent_id: "computer:pc1:codex", refused: code, why: "", comment_id: "r1", ...extra });
  assert.deepEqual(refusalChip(refused("busy")), {
    kind: "refused", label: "Not sent to Codex", title: "Not sent to Codex", tone: "warn", live: false,
    detail: "Codex is still working on this comment: wait for it, or stop it.", retry: true, comment: "r1", to: "computer:pc1:codex" });
  assert.equal(refusalChip(refused("not_newest")).retry, false, "sent again it is still not the newest");
  assert.equal(refusalChip(refused("viewer")).retry, false);
  assert.equal(refusalChip(refused("offline", { computer: "TIM-PC" })).retry, true);
  assert.equal(refusalChip(refused("busy", { agent_id: "" })).retry, false, "no agent to send it to");
  assert.equal(refusalChip(null), null);
  assert.equal(refusalChip({ agent: "Codex", turn_id: "u1", state: "queued" }), null, "begun");
});

test("a comment the server sent to no agent says why, worded here by its code, else in the server's words", () => {
  const refused = (code, why = "") => ({ agent: "Codex", refused: code, why });
  assert.equal(turnRefusal(refused("closed")), "Reopen this comment to ask Codex again.");
  assert.equal(turnRefusal(refused("not_newest")), "Reply to ask Codex again: only the newest comment is sent.");
  assert.equal(turnRefusal(refused("viewer")), "Only the workspace's owners and editors can send comments to Codex.");
  assert.equal(turnRefusal(refused("off")), "Turn on Codex in Settings → AI → Agents first.");
  assert.equal(turnRefusal(refused("busy")), "Codex is still working on this comment: wait for it, or stop it.");
  assert.equal(turnRefusal(refused("folder")), "Codex works for you only on the files of a synced folder.");
  assert.equal(turnRefusal({ ...refused("offline"), computer: "TIM-PC" }), "TIM-PC is offline: send this again once it connects.");
  assert.equal(turnRefusal({ ...refused("paused"), computer: "TIM-PC" }),
    "The synced folder is paused on TIM-PC: Codex starts once it syncs again.");
  assert.equal(turnRefusal(refused("paused")), "The synced folder is paused on this computer: Codex starts once it syncs again.");
  assert.equal(turnRefusal(refused("writes", "This server takes no changes now.")),
    "Gamma takes no changes here now: nothing was sent to Codex.");
  assert.equal(turnRefusal(refused("elsewhere")), "Codex on that computer does not work on this page.");
  assert.equal(turnRefusal(refused("not_ready", "Codex is not signed in.")), "Codex is not signed in.", "the agent's own problem");
  assert.equal(turnRefusal({ agent: "Codex", conversation_id: "c1", turn_id: "u1", state: "queued" }), "", "begun");
  assert.equal(turnRefusal(undefined), "", "sent to no agent");
});

test("what became of a thread's place after a rebuild: a highlight moved or lost, ink or a region drawn on an earlier build", () => {
  const pos = (extra = {}) => ({ pageNumber: 1, width: 612, height: 792, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [], ...extra });
  const hl = (extra) => ({ id: "h", content: "", properties: { quote: "q", pdf_position: pos(extra) } });
  assert.equal(threadPlace({ made_on: "a" }, hl(), "b"), "", "a highlight placed again exactly");
  assert.equal(threadPlace({ made_on: "a" }, hl({ approx: true, match: 0.8 }), "b"), "moved");
  assert.equal(threadPlace({ made_on: "a" }, hl({ approx: true, lost: true }), "b"), "lost");
  const region = (hash) => ({ id: "r", content: "", properties: { image_position: { width: 12, height: 8, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [], hash } } });
  assert.equal(threadPlace({ made_on: "a" }, region("i0"), "i1"), "earlier", "drawn on another version of the picture");
  assert.equal(threadPlace({ made_on: "a" }, region("i1"), "i1"), "", "the version it was drawn on, whatever the thread says");
  assert.equal(threadPlace({ made_on: "a" }, region("i0"), ""), "", "a page that shows no file");
  assert.equal(threadPlace({ made_on: "a" }, region(""), "i1"), "", "a region that names no version");
  const ink = { id: "g", content: "", properties: { ink_url: "/api/uploads/a.ink", pdf_position: pos() } };
  assert.equal(threadPlace({ made_on: "a" }, ink, "b"), "earlier");
  assert.equal(threadPlace({ made_on: "b" }, ink, "b"), "", "the build it was drawn on");
  assert.equal(threadPlace({ made_on: "" }, ink, "b"), "", "a thread from before the file was named");
  assert.equal(threadPlace({ made_on: "a" }, ink, ""), "", "a page that shows no file");
  assert.equal(threadPlace({ made_on: "a" }, { id: "n", content: "a note", properties: {} }, "b"), "", "a note");
  assert.equal(threadPlace({ made_on: "a" }, undefined, "b"), "", "a block the page no longer shows");
});

test("a text file's range: moved or lost by its flags, nothing while pending; its lines when it has no words", () => {
  const range = (extra = {}) => ({ id: "t", content: "", properties: { quote: "q",
    text_position: { line: 14, col: 0, end: 16, end_col: 3, prefix: "", suffix: "", hash: "f1", ...extra } } });
  assert.equal(threadPlace({ made_on: "f0" }, range(), "f1"), "", "placed exactly");
  assert.equal(threadPlace({ made_on: "f0" }, range({ approx: true, match: 0.86, hash: "f2" }), "f2"), "moved");
  assert.equal(threadPlace({ made_on: "f0" }, range({ lost: true }), "f2"), "lost");
  assert.equal(threadPlace({ made_on: "f0" }, range({ hash: "f0" }), "f2"), "", "pending says nothing, as a PDF's does");
  assert.equal(anchorWords(range()), "q");
  const bare = (span) => ({ id: "t", content: "", properties: { text_position: { prefix: "", suffix: "", hash: "f1", ...span } } });
  assert.equal(anchorWords(bare({ line: 14, col: 0, end: 16, end_col: 3 })), "Lines 14–16 of the file");
  assert.equal(anchorWords(bare({ line: 14, col: 0, end: 14, end_col: 3 })), "Line 14 of the file");
  assert.equal(anchorWords(bare({ line: 0 })), "", "a malformed one names no lines");
});

test("the cards a margin draws: anchors with threads and the one being written on, each at its top", () => {
  const anchors = [{ id: "a", threadCount: 2, position: { line: 3 } }, { id: "b", threadCount: 0, position: { line: 9 } },
    { id: "c", threadCount: 0, position: { line: 12 } }];
  assert.deepEqual(cardedAnchors(anchors, "c", (a) => a.position.line * 20), [{ id: "a", top: 60 }, { id: "c", top: 240 }]);
  assert.deepEqual(cardedAnchors(anchors, "", () => 0), [{ id: "a", top: 0 }]);
});

test("who may report: anyone but the author, a link visitor too; a tombstone no one", () => {
  const mine = c("x", "x", "ann", "t");
  assert.ok(canReportComment(mine, "ben"));
  assert.ok(canReportComment(mine, ""), "a visitor without an account");
  assert.ok(!canReportComment(mine, "ann"));
  assert.ok(!canReportComment({ ...mine, deleted: true }, "ben"));
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
  // a [[link|words]] reads as its words, known or not
  assert.equal(anchorSnippet("See [[pg1|the tables]] and [[zz9|that]]", 80, labelOf), "See the tables and that");
  assert.equal(anchorSnippet("See [[pg1|the tables]]"), "See the tables");
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

test("a highlight with open threads and no note draws as a commented passage", () => {
  assert.equal(commentOnlyHighlight({ hasNote: false, threadCount: 1 }), true);
  assert.equal(commentOnlyHighlight({ hasNote: true, threadCount: 2 }), false, "a note keeps the plain fill");
  assert.equal(commentOnlyHighlight({ hasNote: false }), false, "no open thread (none, or all resolved)");
  assert.equal(commentOnlyHighlight(null), false);
});

test("the selection popup's Comment highlight goes again only when nothing is on it", () => {
  const resolved = th("t9", "h1", "2026-10-01T00:00:00.000000Z", [c("t9", "t9", "ann", "2026-10-01T00:00:00.000000Z")],
    { resolved_at: "2026-10-02T00:00:00.000000Z", resolved_by: "ann" });
  assert.equal(abandonedCommentHighlight({ id: "h1", hasNote: false }, undefined), true);
  assert.equal(abandonedCommentHighlight({ id: "h1", hasNote: false }, []), true);
  assert.equal(abandonedCommentHighlight({ id: "h1", hasNote: false }, [resolved]), false, "a thread, even resolved, keeps it");
  assert.equal(abandonedCommentHighlight({ id: "h1", hasNote: true }, []), false, "a note keeps it");
  assert.equal(abandonedCommentHighlight(undefined, []), false, "already gone");
});

test("a popover draws its first screens of text at once and the rest as they near its view", () => {
  const long = (id, thread, body) => ({ ...c(id, thread, "ann", "2026-10-02T00:00:00.000000Z"), body });
  const prose = "x".repeat(900);
  const threads = [
    th("t1", "b1", "2026-10-01T00:00:00.000000Z", [long("t1", "t1", prose), long("r1", "t1", prose), long("r2", "t1", prose), long("r3", "t1", "short")]),
    th("t2", "b1", "2026-10-03T00:00:00.000000Z", [long("t2", "t2", "short")]),
  ];
  assert.deepEqual([...laterComments(threads)], ["r3", "t2"], "past the first 2000 characters");
  assert.deepEqual([...laterComments(threads, { budget: 1000 })], ["r2", "r3", "t2"]);
  // from the thread it scrolls to: that one, those below it, then those above it, the nearest first
  assert.deepEqual([...laterComments(threads, { focus: "t2", budget: 1000 })], ["t1"], "counted from the thread it scrolls to");
  assert.deepEqual([...laterComments(threads, { focus: "t1", budget: 1000 })], ["r2", "r3", "t2"],
    "a long thread it scrolls to is drawn from its top, the rest as it nears");
  assert.deepEqual([...laterComments(threads, { focus: "gone" })], ["r3", "t2"], "a thread it does not show counts from the top");
  assert.equal(laterComments([th("t3", "b1", "2026-10-01T00:00:00.000000Z", [c("t3", "t3", "ann", "2026-10-01T00:00:00.000000Z")])]).size, 0,
    "a short thread is drawn whole");
  // a display equation counts for more than its characters
  const math = "$$\\sum_k k$$ ".repeat(10);
  assert.ok(math.length < EAGER_TEXT);
  const mathy = [th("t4", "b1", "2026-10-01T00:00:00.000000Z", [long("t4", "t4", math.repeat(2)), long("r4", "t4", "short")])];
  assert.deepEqual([...laterComments(mathy)], ["r4"]);
});

test("the box standing in for a comment is about as tall as its text", () => {
  assert.equal(estimatedLines("one line"), 1);
  assert.equal(estimatedLines("one\ntwo\n\nfour"), 4);
  assert.equal(estimatedLines("y".repeat(100)), 3, "wrapped at about 45 characters");
  assert.equal(estimatedLines("before $$x^2$$ after"), 3, "two more lines for a display equation");
  assert.equal(estimatedLines(""), 1);
});
