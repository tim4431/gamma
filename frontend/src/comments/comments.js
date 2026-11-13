// The open page's comment threads as the app holds them (docs/dev/comments.md
// "In the app"): folded per block for the rows, filtered and ordered for the
// Comments window, and changed in place by the writes the reader makes
// before the server answers. Pure, so node tests it (tests/comments.test.mjs).
//
// A thread is the server's `{id, block_id, page_id, resolved_at,
// resolved_by, resolved_agent, made_on, sent, created_at, last_at,
// comments}`, its comments oldest first, each `{id, thread_id, author,
// agent, body, created_at, updated_at, deleted}`. A thread's root has
// `thread_id === id`; `author` is a username, and `agent` the name of the
// integration token that wrote it through the author's connection ("" for
// the person's own); `made_on` the file the thread was opened on, `sent`
// the Send waiting on it (docs/dev/feedback.md).
import { t } from "../shared/i18n/i18n.js";
import { blockQuote, isHighlightBlock, isInkGroup, isRegionBlock, pdfAnchorOf } from "../shared/model/blockModel.js";
import { plainSnippet } from "../search/snippets.js";
import { peopleIn } from "../editor/mentions.js";
import { AGENT_MAX } from "../feedback/feedbackView.js";

export const MAX_COMMENT = 20000; // characters, as the server (comments.MAX_BODY)

export const isOpen = (thread) => !thread.resolved_at;

// A count on a small badge: "9+" past nine.
export const shortCount = (n) => (n > 9 ? "9+" : String(n));

// The element a block's thread popover hangs from, and Escape gives the
// focus back to: the one that opened it while it is in the document, else
// the block's row control (a row that remounted). Never one inert: a folded
// dock window keeps its rows mounted, out of sight, and a leaving copy is a
// picture. With none, the popover stands centred near the top.
export function threadAnchor({ anchor, blockId }) {
  const usable = (el) => !!el?.isConnected && !el.closest("[inert]");
  if (usable(anchor)) return anchor;
  return [...document.querySelectorAll(`[data-comment-for="${CSS.escape(blockId)}"]`)].find(usable) || null;
}

// What a row reads: `byBlock` maps a block's id to its threads, oldest
// first; `counts` to how many of them are open (blocks with none are left
// out, so a row without comments reads 0).
export function foldThreads(threads) {
  const byBlock = new Map();
  const counts = new Map();
  for (const th of [...threads].sort(byCreated)) {
    if (!byBlock.has(th.block_id)) byBlock.set(th.block_id, []);
    byBlock.get(th.block_id).push(th);
    if (isOpen(th)) counts.set(th.block_id, (counts.get(th.block_id) || 0) + 1);
  }
  return { byBlock, counts };
}

// A PDF highlight drawn as a commented passage (a light wash and an
// underline, PdfViewer): `h` (App's highlight object) has no note of its
// own (`hasNote`: no text, no child note) and at least one open thread.
export const commentOnlyHighlight = (h) => !!h && !h.hasNote && (h.threadCount || 0) > 0;

// Whether a highlight made by the selection popup's Comment is left with
// nothing on it once its popover closes, and so is taken away again, as an
// abandoned comment in Notion leaves no mark: no note (`h.hasNote`) and no
// thread (`blockThreads`, open or resolved). One already gone (`h` null)
// is not.
export const abandonedCommentHighlight = (h, blockThreads) =>
  !!h && !h.hasNote && !(blockThreads || []).length;

const byCreated = (a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0);
const byLatest = (a, b) => (a.last_at > b.last_at ? -1 : a.last_at < b.last_at ? 1 : byCreated(a, b));

// The Comments window's list: the threads in `state` ("open" or
// "resolved"), the latest activity first.
export function threadsIn(threads, state) {
  return threads.filter((th) => (state === "resolved" ? !isOpen(th) : isOpen(th))).sort(byLatest);
}

// A thread popover's list: the block's open threads, oldest first, and a
// resolved one only when it is asked for by id (a deep link, the window's
// Resolved tab) or `withResolved`.
export function popoverThreads(blockThreads, { focus = "", withResolved = false } = {}) {
  return (blockThreads || []).filter((th) => isOpen(th) || withResolved || th.id === focus);
}

// How much text a popover draws at once, from its top (laterComments).
export const EAGER_TEXT = 2000;
// What a $$…$$ equation counts for beside its characters: it stands on
// lines of its own.
const DISPLAY_WEIGHT = 100;
const displayCount = (body) => Math.floor(((body || "").match(/\$\$/g) || []).length / 2);

// The comments of `threads` (the popover's, in its order) that it draws
// later, as each nears its view (ThreadPopover.jsx): those past the first
// `budget` characters of text, a display equation counting DISPLAY_WEIGHT
// more. The text is counted from where the popover opens: from the top, or
// from the `focus` thread it scrolls to (a deep link, an Inbox jump), then
// the threads below it, then those above it, the nearest first. A thread of
// many long comments full of math would otherwise build every equation
// before the popover shows.
export function laterComments(threads, { focus = "", budget = EAGER_TEXT } = {}) {
  const list = threads || [];
  const at = Math.max(0, list.findIndex((th) => th.id === focus));
  const order = [
    ...list.slice(at).map((th) => th.comments),
    ...list.slice(0, at).reverse().map((th) => [...th.comments].reverse()),
  ];
  const later = new Set();
  let left = budget;
  for (const comments of order) {
    for (const comment of comments) {
      if (left <= 0) later.add(comment.id);
      left -= (comment.body || "").length + DISPLAY_WEIGHT * displayCount(comment.body);
    }
  }
  return later;
}

// About how many lines a comment's text takes in the popover, for the box
// that stands in for it until it is drawn: its lines, wrapped at about 45
// characters, and two more for each display equation.
export function estimatedLines(body) {
  const lines = (body || "").split("\n").reduce((n, line) => n + Math.max(1, Math.ceil(line.length / 45)), 0);
  return lines + 2 * displayCount(body);
}

// `next` with each thread that reads the same as one in `prev` replaced by
// that one, so a refetch that changed nothing keeps every thread's identity
// (and the folded maps their entries: keepIfSame upstream).
export function keepThreads(prev, next) {
  if (!prev?.length) return next;
  const old = new Map(prev.map((th) => [th.id, th]));
  let same = prev.length === next.length;
  const out = next.map((th, i) => {
    const was = old.get(th.id);
    const kept = was && JSON.stringify(was) === JSON.stringify(th) ? was : th;
    if (kept !== prev[i]) same = false;
    return kept;
  });
  return same ? prev : out;
}

// The comment and its thread, by the comment's id.
export function findComment(threads, id) {
  for (const thread of threads) {
    const comment = thread.comments.find((c) => c.id === id);
    if (comment) return { thread, comment };
  }
  return null;
}

const later = (a, b) => (a > b ? a : b);

// A thread or a server answer for one, in place of the thread of that id
// (appended when the page had none). A null thread is a no-op.
export function upsertThread(threads, thread) {
  if (!thread) return threads;
  const i = threads.findIndex((th) => th.id === thread.id);
  if (i < 0) return [...threads, thread];
  const out = threads.slice();
  out[i] = thread;
  return out;
}

// The thread of that id gone (its last comment was deleted).
export const dropThread = (threads, id) => threads.filter((th) => th.id !== id);

// A comment written here, shown before the server has it: a new thread
// when it is its own root (`comment.thread_id === comment.id`), else a
// reply at the end of its thread. `blockId` and `pageId` place a new
// thread; a reply to a thread the page does not hold changes nothing.
export function addComment(threads, comment, { blockId = "", pageId = "" } = {}) {
  if (findComment(threads, comment.id)) return threads;
  if (comment.thread_id === comment.id) {
    return [...threads, {
      id: comment.id, block_id: blockId, page_id: pageId, resolved_at: null, resolved_by: null,
      created_at: comment.created_at, last_at: comment.created_at, comments: [comment],
    }];
  }
  return threads.map((th) => (th.id === comment.thread_id
    ? { ...th, last_at: later(th.last_at, comment.created_at), comments: [...th.comments, comment] }
    : th));
}

// A comment's new text (`at`: when, so the thread's activity moves).
export function editComment(threads, id, body, at) {
  return threads.map((th) => (th.comments.some((c) => c.id === id)
    ? {
      ...th, last_at: later(th.last_at, at),
      comments: th.comments.map((c) => (c.id === id ? { ...c, body, updated_at: at } : c)),
    }
    : th));
}

// A comment deleted, as the server does it: a root that still has replies
// stays as a tombstone (its text gone); a reply that was the last one under
// a tombstone takes the tombstone along; a thread left without comments
// goes.
export function deleteComment(threads, id) {
  const found = findComment(threads, id);
  if (!found) return threads;
  const { thread } = found;
  const replies = thread.comments.filter((c) => c.id !== thread.id);
  if (id === thread.id && replies.length) {
    return upsertThread(threads, {
      ...thread, comments: thread.comments.map((c) => (c.id === id ? { ...c, body: "", deleted: true } : c)),
    });
  }
  let rest = thread.comments.filter((c) => c.id !== id);
  if (rest.length === 1 && rest[0].id === thread.id && rest[0].deleted) rest = [];
  return rest.length ? upsertThread(threads, { ...thread, comments: rest }) : dropThread(threads, thread.id);
}

// A thread resolved by `by` at `at` (the reader, so by no agent), or
// reopened.
export function resolveThread(threads, id, resolved, by, at) {
  return threads.map((th) => (th.id === id
    ? { ...th, resolved_at: resolved ? at : null, resolved_by: resolved ? by : null, resolved_agent: "", last_at: later(th.last_at, at) }
    : th));
}

// Whether `me` (a username) may change a comment's text: their own, written
// by them, not one an agent wrote through their connection, which is the
// agent's to change. Delete: their own, the agent's included, and the
// workspace's owner anyone's. A tombstone is no one's to change.
export const canEditComment = (comment, me) => !!me && !comment.deleted && !comment.agent && comment.author === me;
export const canDeleteComment = (comment, me, owner = false) => !comment.deleted && (owner || (!!me && comment.author === me));
// Report (support/reportContent.js): anyone's comment but one's own, a
// link visitor's too; a tombstone has nothing left to report.
export const canReportComment = (comment, me) => !comment.deleted && (!me || comment.author !== me);

// A comment whose text was changed after it was written.
export const isEdited = (comment) => !comment.deleted && !!comment.updated_at && comment.updated_at !== comment.created_at;

// Who a comment reads as written by: the agent that wrote it through its
// author's connection ("Claude Code"), else its author. `authorTitle` is
// its avatar's title, which names the connection for an agent, or the
// computer for an agent Gamma ran there (`agent_kind` "local",
// docs/dev/local_agents.md: its author is the person who sent it the
// comments).
export const authorLabel = (comment) => comment.agent || comment.author;
export function authorTitle(comment) {
  if (!comment.agent) return comment.author;
  const who = comment.author || t("Someone");
  return comment.agent_kind === "local"
    ? t("Written by {agent} on {who}'s computer", { agent: comment.agent, who })
    : t("Written by {agent} with {who}'s connection", { agent: comment.agent, who });
}

// A Send its requester withdrew: the server's `failed` with "withdrawn".
export const isWithdrawn = (request) => request?.state === "failed" && request.detail === "withdrawn";

// The chip of a thread a Send waits on (`thread.sent`, {request_id, state,
// at, detail, state_by}), {state, label, title, tone} (`tone` the uiTag's):
// Sent while the request waits for the agent, the agent working (named by
// `state_by` when it says) with what it is at in the title, Failed with its
// reason in the title. null for none, and for a Send withdrawn: the thread
// is open, the person's to send again.
export function sentChip(thread) {
  const sent = thread?.sent;
  if (!sent || isWithdrawn(sent)) return null;
  const { state, detail, state_by: agent } = sent;
  if (state === "failed") {
    return { state, label: t("Failed"), title: detail ? t("Failed: {reason}", { reason: detail }) : t("Failed"), tone: "failed" };
  }
  if (state === "running") {
    const label = agent ? t("{agent} is working", { agent }) : t("The agent is working");
    return { state, label, title: detail || label, tone: "pending" };
  }
  return { state, label: t("Sent"), title: t("Waiting for the agent"), tone: "pending" };
}

// What a comment field says under the text while it names the work
// folder's agent on this computer (`agent`, vaultState.js mentionAgent) as
// the server reads it: a mention outside code that `initial` (an edit's
// text before) did not have. Sending it is a Send of the thread, or of the
// file's open comments from the page's own thread (`file`); an edit the
// server will not send (`refused`: "closed" in a resolved thread,
// "not_newest" for an older comment, as local_agents/mention.py) says why,
// and while the agent cannot run, its problem. → {text, warn}, or null for
// nothing to say.
export function mentionHint({ agent, text, initial = "", file = false, refused = "" }) {
  if (!agent || !peopleIn(text).has(agent.name) || peopleIn(initial).has(agent.name)) return null;
  if (refused) return { text: sendRefusal({ agent: agent.label, refused }), warn: true };
  if (agent.ready === false && agent.problem) return { text: agent.problem, warn: true };
  return {
    text: file ? t("Sends this file's open comments to {agent} on this computer.", { agent: agent.label })
      : t("Sends this thread to {agent} on this computer.", { agent: agent.label }),
    warn: false,
  };
}

// Why a comment that named the agent was not sent to it, for the status
// line (the comment itself is posted): the answer's `send` ({agent,
// refused, why}, routers/comments.py), worded here by its code, else the
// server's sentence. "" for a Send that was made, or none.
const REFUSALS = {
  closed: (agent) => t("Reopen this thread to send it to {agent} again.", { agent }),
  not_newest: (agent) => t("Mention {agent} in a new reply to send this thread again.", { agent }),
  viewer: (agent) => t("Only the workspace's owners and editors can send comments to {agent}.", { agent }),
  too_many: (agent) => t("{agent} takes at most {max} comments at a time: mention it in the threads you want answered.", { agent, max: AGENT_MAX }),
};
export function sendRefusal(send) {
  if (!send?.refused) return "";
  const agent = send.agent || "";
  return REFUSALS[send.refused]?.(agent) || t("Not sent to {agent}: {reason}", { agent, reason: send.why || send.refused });
}

// What a thread is on, in the Comments window's words: the highlight's
// quote, else the block's text, else for an ink group on a PDF page
// "Handwriting on page 3", for a region of a picture "Region of the
// picture"; "" for a block without words.
export function anchorWords(block) {
  const words = blockQuote(block) || block?.content || "";
  if (words) return words;
  if (isRegionBlock(block)) return t("Region of the picture");
  if (!isInkGroup(block)) return "";
  const page = pdfAnchorOf(block)?.page;
  return page ? t("Handwriting on page {n}", { n: page }) : "";
}

// What became of the place a thread hangs on once its file was rebuilt:
// "moved" (a highlight placed again roughly: its pdf_position's approx),
// "lost" (one not found again), "earlier" (an ink group drawn on another
// build than `docHash`, the file the page shows now: ink is never placed
// again, so it stands where that build had its content; a region of a
// picture drawn on another version than `docHash`, its image_position's
// `hash`: regions are never placed again either, and stay where they were
// on the new picture), else "". `block` is the thread's as the tree holds
// it now, `made_on` (the thread's) the file it was opened on.
export function threadPlace(thread, block, docHash) {
  if (isRegionBlock(block)) {
    const { hash } = block.properties.image_position;
    return hash && docHash && hash !== docHash ? "earlier" : "";
  }
  const pos = pdfAnchorOf(block)?.position;
  if (!pos) return "";
  if (isHighlightBlock(block)) return pos.lost ? "lost" : pos.approx ? "moved" : "";
  return thread?.made_on && docHash && thread.made_on !== docHash ? "earlier" : "";
}

// The top of a comment anchor's first line on its page (a highlight's
// rects, an ink group's box), in px of the page box `pageH` tall: its rects
// are in the frame of `position.height`. Where the anchor's margin card
// stands (marginSlot.jsx).
export function anchorTop(anchor, pageH) {
  const pos = anchor.position || {};
  let top = Infinity;
  for (const r of pos.rects || []) top = Math.min(top, r.y1);
  if (!Number.isFinite(top)) top = pos.boundingRect?.y1 || 0;
  return top * pageH / (pos.height || 1);
}

// A text as one line of at most `max` characters: in the Comments window,
// what a thread is on ("" for a block the page no longer shows, or one
// without text) and its first comment; the Inbox's snippets too.
// `labelOf(id)`: a [[link]]'s label as editor/refLabels.js gives it
// ({content}), so the line names the page rather than its id; a link it
// does not know stays as written.
export function anchorSnippet(text, max = 80, labelOf = null) {
  let src = String(text || "");
  if (labelOf) {
    src = src.replace(/(!?)\[\[([a-zA-Z0-9_-]+)\]\]/g, (whole, _bang, id) => {
      const label = labelOf(id);
      return label?.content ? label.content : whole;
    });
  }
  const line = plainSnippet(src).replace(/\s+/g, " ").trim();
  return line.length > max ? `${line.slice(0, max - 1).trimEnd()}…` : line;
}

// The address that opens a thread: its page, its block and the thread
// (App reads `thread` with `block` and `page` at boot). `search` carries
// what else the link needs (a workspace, a share).
export function threadLink(origin, { pageId, blockId, threadId }, extra = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(extra)) if (v) q.set(k, v);
  if (pageId) q.set("page", pageId);
  if (blockId && blockId !== pageId) q.set("block", blockId);
  q.set("thread", threadId);
  return `${origin}/?${q}`;
}
