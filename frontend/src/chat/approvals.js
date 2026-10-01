// The approval card's pure part (chat/ApprovalCard.jsx): what a tool call
// waiting for the user reads as, and the permissions a conversation allowed
// for itself. The server sends the card as an {"approval"} line, {id,
// call_id, tool, perm, args, preview, timeout} (gamma/ai_permissions.py),
// and waits for POST /api/ai/approvals/<id> with a decision: "once" runs the
// call, "chat" also stops asking in this conversation, "always" also stops
// asking in Settings (this kind of chat), "deny" leaves it unmade.
import { t } from "../shared/i18n/i18n.js";

export const ALLOWING = new Set(["once", "chat", "always"]);

// The card's headline: what the call would do, in words. `titleOf` names a
// page the library knows; `permission` is the permission's label, for a
// tool the user asked to approve that has no sentence here.
export function approvalTitle(approval, { titleOf = () => "", permission = "" } = {}) {
  const preview = approval?.preview || {};
  const args = approval?.args || {};
  const title = preview.title || (args.page_id ? titleOf(args.page_id) : "") || t("Untitled");
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
    case "save_paper":
      if (preview.existed) return t("File “{title}” in {folder}", { title, folder: preview.to });
      return preview.to ? t("Save “{title}” to {folder}", { title, folder: preview.to })
        : t("Save “{title}” to the library root", { title });
    case "restore_page": return t("Restore “{title}” from Recently deleted", { title });
    case "list_pages": case "list_folders": return t("List your pages and folders");
    case "list_deleted": return t("List Recently deleted");
    case "read_page": return t("Read “{title}”", { title });
    case "read_block": return t("Read notes of “{title}”", { title: args.block_id ? titleOf(args.block_id) || t("a page") : title });
    case "read_chats": return t("Read an earlier AI chat");
    case "view_pdf_page": return t("Look at PDF page {page}", { page: args.pdf_page || "?" });
    case "view_ink": return t("Look at your handwriting");
    case "cite": return t("Look up citation records");
    case "search_library": return t("Search your library for “{query}”", { query: args.query || "" });
    case "search_papers": return t("Search papers online for “{query}”", { query: args.query || "" });
    case "search_web": return t("Search the web for “{query}”", { query: args.query || "" });
    case "related_papers": return t("Follow the citations of {source}", { source: args.source || t("a paper") });
    case "fetch_paper": return t("Fetch {source}", { source: args.source || t("a document") });
    default: return permission ? t("Use “{permission}”", { permission }) : t("Use a tool");
  }
}

// What the chip of a call the user did not allow says: why, and the change
// it was, from the fields the server copies onto the chip.
export function declinedSummary(action, titleOf = () => "") {
  const why = action?.approval === "expired" ? t("Not answered in time") : t("Not allowed by you");
  const change = approvalTitle({ tool: action?.tool, args: action?.args,
    preview: { title: action?.title, mode: action?.mode, to: action?.args?.folder } },
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
