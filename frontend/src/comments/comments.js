// The open page's comment threads as the app holds them (docs/dev/comments.md
// "In the app"): folded per block for the rows, filtered and ordered for the
// Comments window, and changed in place by the writes the reader makes
// before the server answers. Pure, so node tests it (tests/comments.test.mjs).
//
// A thread is the server's `{id, block_id, page_id, resolved_at,
// resolved_by, created_at, last_at, comments}`, its comments oldest first,
// each `{id, thread_id, author, body, created_at, updated_at, deleted}`. A
// thread's root has `thread_id === id`; `author` is a username.
import { plainSnippet } from "../search/snippets.js";

export const COMMENT_STATES = ["open", "resolved"];
export const MAX_COMMENT = 20000; // characters, as the server (comments.MAX_BODY)

export const isOpen = (thread) => !thread.resolved_at;

// A count on a small badge: "9+" past nine.
export const shortCount = (n) => (n > 9 ? "9+" : String(n));

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

// A thread resolved by `by` at `at`, or reopened.
export function resolveThread(threads, id, resolved, by, at) {
  return threads.map((th) => (th.id === id
    ? { ...th, resolved_at: resolved ? at : null, resolved_by: resolved ? by : null, last_at: later(th.last_at, at) }
    : th));
}

// Whether `me` (a username) may change a comment's text, or delete it (the
// workspace's owner deletes anyone's). A tombstone is no one's to change.
export const canEditComment = (comment, me) => !!me && !comment.deleted && comment.author === me;
export const canDeleteComment = (comment, me, owner = false) => !comment.deleted && (owner || canEditComment(comment, me));

// A comment whose text was changed after it was written.
export const isEdited = (comment) => !comment.deleted && !!comment.updated_at && comment.updated_at !== comment.created_at;

// What a thread is on, in the Comments window: the anchor's words as one
// line of at most `max` characters ("" for a block the page no longer
// shows, or one without text). `labelOf(id)`: a [[link]]'s label as
// editor/refLabels.js gives it ({content}), so the line names the page
// rather than its id; a link it does not know stays as written.
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
