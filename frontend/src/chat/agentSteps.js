// The agent's tool calls as the chat shows them: every call counts as a
// step, summed up in one pill ("6 steps · listed, read 1 page · 1 failed"),
// and the calls that changed something are listed apart, grouped by where
// the change landed — the library (renamed or filed pages) or the notes
// (edited, added or moved blocks). While a reply streams, the {"step"} line
// the server sends before each call names what is running now.
import { t, tn } from "../shared/i18n/i18n.js";

const CHANGE_KINDS = new Set(["rename", "move", "edit", "create"]);
const LIBRARY_TOOLS = new Set(["rename_page", "move_page"]);

// A call that changed something (listed in the reply, and ChatDock refreshes
// the home feed after one). Actions saved before `noop` existed mark a
// change that changed nothing only by their "ok — …" summary.
export function isChange(a) {
  return !!a && !a.error && !a.noop && CHANGE_KINDS.has(a.kind) && !/^ok\b/.test(a.summary || "");
}

// Where a change landed: "library" (a page renamed or filed) or "notes".
export function changePlace(a) {
  if (a.tool) return LIBRARY_TOOLS.has(a.tool) ? "library" : "notes";
  return a.kind === "rename" || (a.kind === "move" && !a.block_id) ? "library" : "notes";
}

export function splitActions(actions = []) {
  const library = [], notes = [];
  for (const a of actions) if (isChange(a)) (changePlace(a) === "library" ? library : notes).push(a);
  return { library, notes, failed: actions.filter((a) => a.error).length };
}

// The pill's words for the reading steps, in the order they first ran.
const READ_VERBS = {
  list: () => t("listed"),
  read: (n) => tn("read {n} page", "read {n} pages", n),
  view: (n) => tn("looked at {n} PDF page", "looked at {n} PDF pages", n),
  search: (n) => tn("searched", "searched {n} times", n),
  websearch: (n) => tn("searched papers online", "searched papers online {n} times", n),
  fetch: (n) => tn("fetched {n} document", "fetched {n} documents", n),
};

export function stepsSummary(actions = []) {
  const counts = new Map();
  for (const a of actions) {
    if (!a.error && READ_VERBS[a.kind]) counts.set(a.kind, (counts.get(a.kind) || 0) + 1);
  }
  const verbs = [...counts].map(([kind, n]) => READ_VERBS[kind](n));
  return [tn("{n} step", "{n} steps", actions.length), verbs.join(", ")].filter(Boolean).join(" · ");
}

// What the step running right now is doing, from its {"step"} line
// ({tool, args}); `titleOf(pageId)` names a page when the library knows it.
export function runningLabel(step, titleOf = () => "") {
  const args = step?.args || {};
  const title = args.page_id ? titleOf(args.page_id) : "";
  switch (step?.tool) {
    case "list_pages": return t("Listing pages…");
    case "read_page": return title ? t("Reading “{title}”…", { title }) : t("Reading a page…");
    case "read_block": return title ? t("Reading notes of “{title}”…", { title }) : t("Reading notes…");
    case "view_pdf_page": return t("Looking at PDF page {page}…", { page: args.pdf_page || "?" });
    case "search_library": return t("Searching library for “{query}”…", { query: args.query || "" });
    case "search_papers": return t("Searching papers for “{query}”…", { query: args.query || "" });
    case "fetch_paper": return t("Fetching {source}…", { source: args.source || t("a document") });
    case "rename_page": return title ? t("Renaming “{title}”…", { title }) : t("Renaming a page…");
    case "move_page": return title ? t("Filing “{title}”…", { title }) : t("Filing a page…");
    case "edit_block": return t("Editing a note…");
    case "create_block": return t("Adding a note…");
    case "move_block": return t("Moving a note…");
    default: return t("Working…");
  }
}

// The sentence of a note change, around the link to its page ({page}).
export function noteChangeText(a, page) {
  if (a.kind === "create") return t("Added a note in {page}", { page });
  if (a.kind === "move") return a.src_page_id && a.src_page_id !== a.page_id
    ? t("Moved a note to {page}", { page }) : t("Moved a note in {page}", { page });
  return {
    append: () => t("Appended to a note in {page}", { page }),
    prepend: () => t("Prepended to a note in {page}", { page }),
    patch: () => t("Edited part of a note in {page}", { page }),
    selection: () => t("Edited the selection in {page}", { page }),
  }[a.mode]?.() || t("Edited a note in {page}", { page });
}
