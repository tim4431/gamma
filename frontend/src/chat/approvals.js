// The approval card's pure part (chat/ApprovalCard.jsx): what a tool call
// waiting for the user reads as, and the permissions a conversation allowed
// for itself. The server sends the card as an {"approval"} line, {id,
// call_id, tool, perm, args, preview, timeout} (gamma/ai_permissions.py),
// and waits for POST /api/ai/approvals/<id> with a decision: "once" runs the
// call, "chat" also stops asking in this conversation, "always" also stops
// asking in Settings (this kind of chat), "deny" leaves it unmade.
import { t, tn } from "../shared/i18n/i18n.js";

export const ALLOWING = new Set(["once", "chat", "always"]);

// Up to the names a preview carries, then how many more there are.
const listOf = (items = [], total = 0) => items.join(", ")
  + (total > items.length ? t(", and {n} more", { n: total - items.length }) : "");

// The page tools that take a list of pages (gamma/ai_tools.py): a card for
// two or more pages carries `count`, and one line per page in its `diff`.
const BATCH_TOOLS = new Set(["rename_page", "move_page", "label_page", "restore_page"]);
export const isBatchPreview = (tool, preview) => BATCH_TOOLS.has(tool) && typeof preview?.count === "number";

function batchTitle(tool, preview) {
  const n = preview.count;
  switch (tool) {
    case "rename_page": return tn("Rename {n} page", "Rename {n} pages", n);
    case "move_page":
      return preview.to ? tn("Move {n} page to {folder}", "Move {n} pages to {folder}", n, { folder: preview.to })
        : tn("Move {n} page to the library root", "Move {n} pages to the library root", n);
    case "label_page": return tn("Change the labels of {n} page", "Change the labels of {n} pages", n);
    default: return tn("Restore {n} page from Recently deleted", "Restore {n} pages from Recently deleted", n);
  }
}

// The card's headline: what the call would do, in words. `titleOf` names a
// page the library knows; `permission` is the permission's label, for a
// tool the user asked to approve that has no sentence here.
export function approvalTitle(approval, { titleOf = () => "", permission = "" } = {}) {
  const preview = approval?.preview || {};
  const args = approval?.args || {};
  const title = preview.title || (args.page_id ? titleOf(args.page_id) : "") || t("Untitled");
  if (isBatchPreview(approval?.tool, preview)) return batchTitle(approval.tool, preview);
  switch (approval?.tool) {
    case "rename_page": return t("Rename “{title}”", { title });
    case "move_page":
      return preview.to ? t("Move “{title}” to {folder}", { title, folder: preview.to })
        : t("Move “{title}” to the library root", { title });
    case "edit_block":
      return {
        append: () => t("Add to a note in “{title}”", { title }),
        prepend: () => t("Add to the start of a note in “{title}”", { title }),
        patch: () => t("Edit part of a note in “{title}”", { title }),
        selection: () => t("Edit your selection in “{title}”", { title }),
      }[preview.mode]?.() || t("Edit a note in “{title}”", { title });
    case "create_block":
      return preview.parent ? t("Add a note under “{parent}” in “{title}”", { parent: preview.parent, title })
        : t("Add a note to “{title}”", { title });
    case "move_block":
      if (preview.src_title) return t("Move a note from “{from}” to “{title}”", { from: preview.src_title, title });
      return preview.parent ? t("Move a note under “{parent}” in “{title}”", { parent: preview.parent, title })
        : t("Move a note in “{title}”", { title });
    case "delete_block": return t("Delete a {what} in “{title}”", { what: deletedKind(preview.what), title });
    case "clip_region":
      return preview.what ? t("Clip a picture of {what} in “{title}”", { what: preview.what, title })
        : t("Clip a picture in “{title}”", { title });
    case "save_paper":
      if (preview.existed) return t("File “{title}” in {folder}", { title, folder: preview.to });
      return preview.to ? t("Save “{title}” to {folder}", { title, folder: preview.to })
        : t("Save “{title}” to the library root", { title });
    case "restore_page": return t("Restore “{title}” from Recently deleted", { title });
    case "rename_folder": return t("Rename the folder “{title}”", { title });
    case "rename_label": return t("Rename the label “{title}”", { title });
    case "create_label": return t("Make the label “{title}”", { title });
    case "label_page": return t("Change the labels of “{title}”", { title });
    case "delete_folder": return t("Delete the folder “{title}”", { title });
    case "delete_label": return t("Delete the label “{title}”", { title });
    case "list_pages": case "list_folders": return t("List your pages and folders");
    case "list_deleted": return t("List Recently deleted");
    case "read_page": return t("Read “{title}”", { title });
    case "read_block": return t("Read notes of “{title}”", { title: args.block_id ? titleOf(args.block_id) || t("a page") : title });
    case "read_chats": return t("Read an earlier AI chat");
    case "view_pdf_page": return t("Look at PDF page {page}", { page: args.pdf_page || "?" });
    case "view_ink": return t("Look at your handwriting");
    case "view_image": return t("Look at a note's pictures");
    case "cite": return t("Look up citation records");
    case "search_library": return t("Search your library for “{query}”", { query: args.query || "" });
    case "search_papers": return t("Search papers online for “{query}”", { query: args.query || "" });
    case "search_web": return t("Search the web for “{query}”", { query: args.query || "" });
    case "related_papers": return t("Follow the citations of {source}", { source: args.source || t("a paper") });
    case "fetch_paper": return t("Fetch {source}", { source: args.source || t("a document") });
    default: return permission ? t("Use “{permission}”", { permission }) : t("Use a tool");
  }
}

// What a deletion takes, as the server names it (gamma/ai_tools.py
// _deleted_kind), in the user's language.
export function deletedKind(what) {
  return {
    highlight: () => t("highlight"), handwriting: () => t("handwriting"), "text box": () => t("text box"),
    "page of paper": () => t("page of paper"), picture: () => t("picture"),
  }[what]?.() || t("note");
}

// What a folder's or a label's deletion takes besides itself, one line each,
// from its card's preview (gamma/ai_tools.py _preview_delete_folder /
// _preview_delete_label): the subfolders, the pages that lose the filing and
// stay, and the folders whose AI chats move to the library chat's history.
export function deletionNotes(tool, preview = {}) {
  const notes = [];
  if (tool === "delete_folder") {
    if (preview.subfolder_count) {
      notes.push(tn("Its subfolder goes with it: {list}", "Its {n} subfolders go with it: {list}",
        preview.subfolder_count, { list: listOf(preview.subfolders, preview.subfolder_count) }));
    }
    notes.push(preview.page_count
      ? tn("{n} page leaves it and stays in your library: {list}", "{n} pages leave it and stay in your library: {list}",
        preview.page_count, { list: listOf(preview.pages, preview.page_count) })
      : t("No pages are filed in it."));
    if (preview.chats) {
      notes.push(tn("{n} AI chat of {list} moves to the library chat's history and stays there if you revert.",
        "{n} AI chats of {list} move to the library chat's history and stay there if you revert.",
        preview.chats, { list: listOf(preview.chat_folders, preview.chat_folder_count) }));
    }
  } else if (tool === "delete_label") {
    notes.push(preview.page_count
      ? tn("The label comes off {n} page; no page is deleted: {list}", "The label comes off {n} pages; no page is deleted: {list}",
        preview.page_count, { list: listOf(preview.pages, preview.page_count) })
      : t("No page carries it."));
  }
  return notes;
}

// What a card for several pages says besides its lines (gamma/ai_tools.py
// _batch_preview): the folder or labels the call makes first, the pages it
// leaves out (the assistant hears why) and the ones already so.
export function batchNotes(preview = {}) {
  const notes = [];
  if (preview.new_folder) notes.push(t("The folder {folder} does not exist yet: it is made first.", { folder: preview.to }));
  if (preview.new_labels?.length) {
    notes.push(tn("A new label is made first: {list}", "{n} new labels are made first: {list}",
      preview.new_labels.length, { list: preview.new_labels.join(", ") }));
  }
  if (preview.left_out_count) {
    notes.push(tn("{n} page is left out: {list}", "{n} pages are left out: {list}",
      preview.left_out_count, { list: listOf(preview.left_out, preview.left_out_count) }));
  }
  if (preview.same_count) {
    notes.push(tn("{n} page already is so and stays as it is.", "{n} pages already are so and stay as they are.",
      preview.same_count));
  }
  return notes;
}

// What the chip of a call the user did not allow says: why, and the change
// it was, from the fields the server copies onto the chip.
export function declinedSummary(action, titleOf = () => "") {
  const why = action?.approval === "expired" ? t("Not answered in time") : t("Not allowed by you");
  const change = approvalTitle({ tool: action?.tool, args: action?.args,
    preview: { title: action?.title, mode: action?.mode, to: action?.args?.folder, count: action?.count } },
  { titleOf, permission: action?.tool });
  return `${why}: ${change}`;
}

// What a conversation decided for itself, kept in this browser per account
// and conversation (named by its first message's id) and sent with each of
// its requests. A new chat, or another conversation opened from history,
// decides afresh. Two decisions live here:
//
// - "Allow in this chat" on an approval card: those permissions ride as
//   `granted` and run without asking again.
// - "Don't wait in this chat" when skipping a blocked paper: `paper_wait`
//   goes false, so a later blocked fetch leaves its card under the reply
//   instead of holding the reply open (chat/FetchHandoffCards.jsx).
export const GRANTS_KEY = "gamma-ai-chat-grants";
const MAX_CONVERSATIONS = 100;

export const conversationId = (messages) => (messages || []).find((m) => m?.id)?.id || "";
const slot = (user, conversation) => `${user || ""}\n${conversation}`;

export function grantsIn(store, user, conversation) {
  const perms = conversation ? store?.[slot(user, conversation)]?.perms : null;
  return Array.isArray(perms) ? perms.filter((p) => typeof p === "string") : [];
}

// Whether this conversation asked not to wait for blocked papers.
export function waitsForPapers(store, user, conversation) {
  return !(conversation && store?.[slot(user, conversation)]?.noWait);
}

// The store with one more decision recorded for the conversation, keeping
// only the most recently used ones.
function decided(store, user, conversation, change, now) {
  if (!conversation) return store || {};
  const key = slot(user, conversation);
  const entries = Object.entries({ ...(store || {}),
    [key]: { ...(store?.[key] || {}), ...change, at: now } })
    .sort(([, a], [, b]) => (b?.at || 0) - (a?.at || 0)).slice(0, MAX_CONVERSATIONS);
  return Object.fromEntries(entries);
}

export function withGrant(store, user, conversation, perm, now = Date.now()) {
  if (!perm) return store || {};
  return decided(store, user, conversation,
    { perms: [...new Set([...grantsIn(store, user, conversation), perm])] }, now);
}

export function withoutPaperWait(store, user, conversation, now = Date.now()) {
  return decided(store, user, conversation, { noWait: true }, now);
}

export function withoutGrants(store, user, conversation) {
  const next = { ...(store || {}) };
  delete next[slot(user, conversation)];
  return next;
}

export function readGrants(storage = globalThis.localStorage) {
  try {
    const value = JSON.parse(storage?.getItem(GRANTS_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

export function writeGrants(store, storage = globalThis.localStorage) {
  try { storage?.setItem(GRANTS_KEY, JSON.stringify(store)); } catch { /* storage full or off: asks again */ }
}
