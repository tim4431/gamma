// The agent's tool calls as the chat shows them: every call counts as a
// step, summed up in one pill ("6 steps · listed, read 1 page · 1 failed"),
// and the calls that changed something are listed apart, grouped by where
// the change landed — the library (renamed, filed, saved or restored pages)
// or the notes (edited, added or moved blocks). While a reply streams, the
// {"step"} line the server sends before each call names what is running now.
// A call the user did not allow on its approval card (`declined`) counts on
// its own, not as a failure.
import { t, tn } from "../shared/i18n/i18n.js";

const CHANGE_KINDS = new Set(["rename", "move", "edit", "create", "save", "restore"]);
const LIBRARY_TOOLS = new Set(["rename_page", "move_page", "save_paper", "restore_page"]);

// A call that changed something (listed in the reply, and ChatDock refreshes
// the home feed after one). Actions saved before `noop` existed mark a
// change that changed nothing only by their "ok — …" summary.
export function isChange(a) {
  return !!a && !a.error && !a.noop && CHANGE_KINDS.has(a.kind) && !/^ok\b/.test(a.summary || "");
}

// Where a change landed: "library" (a page renamed, filed, saved or
// restored) or "notes". Actions saved before they named their `tool` can
// only be renames and moves.
export function changePlace(a) {
  if (a.tool) return LIBRARY_TOOLS.has(a.tool) ? "library" : "notes";
  return a.kind === "rename" || (a.kind === "move" && !a.block_id) ? "library" : "notes";
}

export function splitActions(actions = []) {
  const library = [], notes = [];
  for (const a of actions) if (isChange(a)) (changePlace(a) === "library" ? library : notes).push(a);
  return { library, notes, failed: actions.filter((a) => a.error && !a.declined).length,
    declined: actions.filter((a) => a.declined).length };
}

// The pill's words for the reading steps, in the order they first ran.
const READ_VERBS = {
  list: () => t("listed"),
  read: (n) => tn("read {n} page", "read {n} pages", n),
  view: (n) => tn("looked at {n} PDF page", "looked at {n} PDF pages", n),
  ink: (n) => tn("looked at handwriting", "looked at handwriting {n} times", n),
  cite: (n) => tn("cited", "cited {n} times", n),
  search: (n) => tn("searched", "searched {n} times", n),
  websearch: (n) => tn("searched online", "searched online {n} times", n),
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

// How long a call took and what it turned out to be, shown beside its chip:
// a fetch that waited twenty seconds on a publisher and one served from the
// cache read very differently, and the version says which copy was read.
const VERSIONS = {
  publisher: t("publisher PDF"),
  preprint: t("arXiv preprint"),
  published: t("open-access, published"),
  accepted: t("open-access, accepted manuscript"),
  submitted: t("open-access, preprint"),
};

export function chipNote(a) {
  const parts = [];
  if (a?.version && VERSIONS[a.version]) parts.push(VERSIONS[a.version]);
  if (a?.probe) parts.push(t("front matter only"));
  if (a?.delivered) parts.push(t("from your browser"));
  if (a?.ms >= 1000) parts.push(t("{n}s", { n: (a.ms / 1000).toFixed(1) }));
  return parts.join(" · ");
}

// What the step running right now is doing, from its {"step"} line
// ({tool, args} — the short arguments the server repeats, ai_agent.STEP_ARGS);
// `titleOf(pageId)` names a page when the library knows it (read_block's
// block_id is one when it names a whole page). A batch the server ran side
// by side says how many, since no single call is "the" one running.
export function runningLabel(step, titleOf = () => "") {
  const args = step?.args || {};
  if (step?.batch > 1) {
    const n = step.batch;
    switch (step.tool) {
      case "fetch_paper": return tn("Fetching {n} document…", "Fetching {n} documents…", n);
      case "read_page": return tn("Reading {n} page…", "Reading {n} pages…", n);
      case "search_papers": return tn("Searching papers, {n} query…", "Searching papers, {n} queries…", n);
      case "search_web": return tn("Searching the web, {n} query…", "Searching the web, {n} queries…", n);
      case "search_library": return tn("Searching your library, {n} query…", "Searching your library, {n} queries…", n);
      default: return t("Running {n} steps at once…", { n });
    }
  }
  const title = args.page_id ? titleOf(args.page_id) : "";
  const folder = args.folder || "";
  switch (step?.tool) {
    case "list_pages":
      return args.label ? t("Listing pages labelled “{label}”…", { label: args.label })
        : folder ? t("Listing pages in {folder}…", { folder }) : t("Listing pages…");
    case "list_folders": return folder ? t("Listing folders in {folder}…", { folder }) : t("Listing folders…");
    case "read_page": return title ? t("Reading “{title}”…", { title }) : t("Reading a page…");
    case "read_block": {
      const page = args.block_id ? titleOf(args.block_id) : "";
      return page ? t("Reading notes of “{title}”…", { title: page }) : t("Reading notes…");
    }
    case "read_chats": return title ? t("Reading the chat about “{title}”…", { title }) : t("Reading chats…");
    case "view_pdf_page": return t("Looking at PDF page {page}…", { page: args.pdf_page || "?" });
    case "view_ink": return t("Looking at handwriting…");
    case "cite": return t("Looking up citation records…");
    case "search_library": return t("Searching library for “{query}”…", { query: args.query || "" });
    case "search_papers": return t("Searching papers for “{query}”…", { query: args.query || "" });
    case "related_papers": return t("Following citations of {source}…", { source: args.source || t("a paper") });
    case "search_web": return t("Searching the web for “{query}”…", { query: args.query || "" });
    case "fetch_paper": return t("Fetching {source}…", { source: args.source || t("a document") });
    case "save_paper": return t("Saving {source} to your library…", { source: args.title || args.source || t("a paper") });
    case "list_deleted": return t("Looking in Recently deleted…");
    case "restore_page": return title ? t("Restoring “{title}”…", { title }) : t("Restoring a page…");
    case "rename_page":
      if (args.title) {
        return title ? t("Renaming “{title}” to “{to}”…", { title, to: args.title }) : t("Renaming to “{to}”…", { to: args.title });
      }
      return title ? t("Renaming “{title}”…", { title }) : t("Renaming a page…");
    case "move_page":
      if (folder) return title ? t("Moving “{title}” to {folder}…", { title, folder }) : t("Moving a page to {folder}…", { folder });
      return title ? t("Moving “{title}”…", { title }) : t("Moving a page…");
    case "edit_block":
      return {
        append: () => t("Appending to a note…"),
        prepend: () => t("Prepending to a note…"),
        patch: () => t("Editing part of a note…"),
        selection: () => t("Editing the selection…"),
      }[args.mode]?.() || t("Editing a note…");
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
