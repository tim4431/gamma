// The Inbox as the app reads it (docs/dev/comments.md "In the app" › "The
// Inbox"): one sentence per kind, the snippet under it, the days the list
// is grouped by, where a click goes, which items a system notification
// rings for, and the changes a mark makes before the server answers. Pure,
// so node tests it (tests/inboxText.test.mjs).
//
// An item is the server's `{id, kind, actor, workspace_id, workspace_name,
// page_id, page_title, block_id, thread_id, comment_id, share_token,
// snippet, at, read_at, archived_at}` (GET /api/inbox); `actor` is a
// username ("" once the account is gone). A `join_request` comes from
// someone with no account here: its snippet is their Gamma Cloud username.
import { fmtDate, t } from "../shared/i18n/i18n.js";
import { parseStamp } from "../library/libraryUtils.js";
import { anchorSnippet } from "../comments/comments.js";

export const isUnread = (item) => !item.read_at && !item.archived_at;

// Whether an item belongs in a tab: Unread (neither read nor archived), All
// (not archived) or Archived.
export function inFilter(item, filter) {
  if (filter === "archived") return !!item.archived_at;
  if (filter === "all") return !item.archived_at;
  return isUnread(item);
}

// A folder share's item names the folder (block_id), not a page: a share
// of it, or a request to join through it.
export const isFolderShare = (item) => (item.kind === "shared" || item.kind === "join_request")
  && !item.page_id && !!item.block_id;

// Who the item is from: the account, or the Gamma Cloud username asking to join.
export const inboxActor = (item) => (item.kind === "join_request" ? item.snippet : item.actor) || "";

// What happened, in one sentence.
export function inboxSentence(item) {
  const who = inboxActor(item) || t("Someone");
  const page = item.page_title || t("Untitled");
  switch (item.kind) {
    case "mention": return t("{who} mentioned you in {page}", { who, page });
    case "comment_mention": return t("{who} mentioned you in a comment on {page}", { who, page });
    case "reply": return t("{who} replied to a comment on {page}", { who, page });
    case "resolved": return t("{who} resolved your comment on {page}", { who, page });
    case "shared":
      return isFolderShare(item) ? t("{who} shared the folder {name} with you", { who, name: page })
        : t("{who} shared {page} with you", { who, page });
    case "invited":
      return t("{who} added you to {workspace}", { who, workspace: item.workspace_name || item.snippet || t("a workspace") });
    case "join_request":
      return isFolderShare(item) ? t("{who} asks to open the folder {name}", { who, name: page })
        : t("{who} asks to open {page}", { who, page });
    default: return t("{who} wrote on {page}", { who, page });
  }
}

// The words under the sentence: the note's or the comment's text as one
// line of at most `max` characters. A share's, an invitation's and a
// request's snippet is the name the sentence already says, so they have none.
export function inboxSnippet(item, max = 140) {
  if (item.kind === "shared" || item.kind === "invited" || item.kind === "join_request") return "";
  return anchorSnippet(item.snippet, max);
}

const dayKey = (d) => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;

// The list's days, newest first as the items come: `[{key, label, items}]`,
// labelled Today, Yesterday, else the date (the year only when it is not
// this one), in the reader's time zone.
export function inboxDays(items, now = Date.now()) {
  const today = new Date(now);
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const days = [];
  for (const item of items) {
    const at = parseStamp(item.at || "");
    const valid = !Number.isNaN(at.getTime());
    const key = valid ? dayKey(at) : "";
    let day = days[days.length - 1];
    if (!day || day.key !== key) {
      const label = !valid ? "" : key === dayKey(today) ? t("Today") : key === dayKey(yesterday) ? t("Yesterday")
        : fmtDate(at, at.getFullYear() === today.getFullYear() ? { month: "short", day: "numeric" }
          : { year: "numeric", month: "short", day: "numeric" });
      day = { key, label, items: [] };
      days.push(day);
    }
    day.items.push(item);
  }
  return days;
}

// An address in this app: a share (`share`) or a workspace (`ws`), then a
// library folder, the page, the block (left out when it is the page) and the thread.
export function inboxUrl({ share = "", ws = "", folder = "", page = "", block = "", thread = "" }, base = "/") {
  const q = new URLSearchParams();
  if (share) q.set("share", share);
  else if (ws) q.set("ws", ws);
  if (folder) q.set("folder", folder);
  if (page) q.set("page", page);
  if (block && block !== page) q.set("block", block);
  if (thread) q.set("thread", thread);
  return `${base}?${q}`;
}

// Where a click on an item goes, from the workspace this tab works in:
// - `{to: "workspace", workspaceId}`: an invitation, the workspace switch;
// - `{to: "url", url}`: through the share that reached the item, or into
//   another workspace (`?ws=&page=&block=&thread=`), a navigation;
// - `{to: "share", pageId, folderId}`: a request to join through a share of
//   this workspace, the page's or the folder's Share popover, where it is
//   decided (the other id "");
// - `{to: "here", pageId, blockId, threadId}`: a page of this workspace,
//   opened in place (a folder share's item: the folder, `pageId` "").
export function inboxJump(item, workspaceId, base = "/") {
  if (item.kind === "invited") return { to: "workspace", workspaceId: item.workspace_id };
  const folder = isFolderShare(item);
  if (item.kind === "join_request" && item.workspace_id === workspaceId) {
    return { to: "share", pageId: item.page_id || "", folderId: folder ? item.block_id : "" };
  }
  const where = {
    page: item.page_id || "", block: folder ? "" : item.block_id || "", thread: item.thread_id || "",
  };
  if (item.share_token) return { to: "url", url: inboxUrl({ share: item.share_token, ...where }, base) };
  if (item.workspace_id !== workspaceId) {
    return { to: "url", url: inboxUrl({ ws: item.workspace_id, folder: folder ? item.block_id : "", ...where }, base) };
  }
  return { to: "here", pageId: item.page_id || "", blockId: item.block_id || "", threadId: item.thread_id || "" };
}

// A listing's first page over what the tab holds: its items, then the older
// items loaded before that it did not reach (`more`: older ones remain).
export function mergeListing(prev, answer, prevMore = false) {
  const items = answer.items || [];
  if (!answer.more || !items.length) return { items, more: !!answer.more };
  const edge = items[items.length - 1].at;
  const seen = new Set(items.map((i) => i.id));
  const older = (prev || []).filter((i) => i.at < edge && !seen.has(i.id));
  return { items: [...items, ...older], more: older.length ? prevMore : true };
}

// Whether two held listings show the same: the tab, its state, and each
// item field for field (an item is the server's flat record).
export function sameListing(a, b) {
  if (a.filter !== b.filter || a.more !== b.more || a.loaded !== b.loaded || a.failed !== b.failed) return false;
  if (a.items.length !== b.items.length) return false;
  return a.items.every((x, i) => {
    const y = b.items[i];
    const keys = Object.keys(x);
    return keys.length === Object.keys(y).length && keys.every((k) => x[k] === y[k]);
  });
}

// A mark on the items of `ids` (every item with `all`) as the server makes
// it (`read`, `archived`: true sets the stamp, kept when set, false clears
// it), with the items that leave the tab `filter` dropped. `unreadDelta`:
// what the change does to the unread count, for the items held.
export function applyMark(items, { ids = [], all = false }, { read, archived }, filter, now) {
  const wanted = new Set(ids);
  let unreadDelta = 0;
  const out = [];
  for (const item of items) {
    if (!all && !wanted.has(item.id)) { out.push(item); continue; }
    const next = { ...item };
    if (read !== undefined) next.read_at = read ? item.read_at || now : null;
    if (archived !== undefined) next.archived_at = archived ? item.archived_at || now : null;
    unreadDelta += (isUnread(next) ? 1 : 0) - (isUnread(item) ? 1 : 0);
    if (inFilter(next, filter)) out.push(next);
  }
  return { items: out, unreadDelta };
}

export const RING_FRESH_MS = 15 * 60 * 1000;
export const RING_AT_ONCE = 5;

// The items a system notification rings for: unread, not rung in this
// browser yet (`rung`, their ids), and fresh (an old item, found when the
// setting is turned on, rings nothing); at most RING_AT_ONCE, newest first.
export function itemsToRing(items, rung, now = Date.now()) {
  const done = new Set(rung || []);
  return (items || []).filter((item) => isUnread(item) && !done.has(item.id)
    && now - parseStamp(item.at || "").getTime() <= RING_FRESH_MS).slice(0, RING_AT_ONCE);
}
