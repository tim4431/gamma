// The anchor registry: every control the guide may point at, by id. A tour
// step names an anchor; the element carries the same id as `data-guide`.
// The guide never selects by class, text or DOM position — when a control
// moves, its attribute moves with the JSX; when it goes, delete its row here
// and the tests name every step that referenced it (docs/dev/onboarding.md).
//
// view: where the anchor exists — "home", "page" (any open page), "pdf" (a
// page with a document) or "any", which the browser suite checks are always
// there; any other view names the situation that brings the anchor up (a
// chat reply with a citation, a table in the notes…) and is never checked.
// open: anchors inside a closed surface list the anchors the engine clicks
// first to reveal them. pick: "last" when the anchor repeats and the newest
// one is meant (the latest chat reply); the first one otherwise.

export const ANCHORS = {
  "header.home": { view: "any", description: "The Home button in the topbar" },
  "header.add": { view: "any", description: "Add — new page, PDF by URL / arXiv / DOI, uploads" },
  "header.tasks": { view: "any", description: "Background tasks (downloads, uploads, indexing)" },
  "header.search": { view: "any", description: "Workspace search (Ctrl+F)" },
  "header.share": { view: "page", description: "The page's Share button" },
  "header.account": { view: "any", description: "Account & settings menu" },
  "account.tour": { view: "any", open: ["header.account"], description: "Tours submenu in the account menu" },
  "account.card": { view: "any", open: ["header.account"], description: "The account menu's card: you, this workspace and your role" },
  "account.workspaces": { view: "any", open: ["header.account"], description: "The account menu's workspace switcher" },
  "add.urlInput": { view: "any", open: ["header.add"], description: "The Add popover's URL / arXiv / DOI box" },
  "share.link": { view: "page", open: ["header.share"], description: "The share popover's link: copy it, or stop sharing" },
  "share.access": { view: "page", open: ["header.share"], description: "Who can open the share link, and whether they can edit" },
  "share.people": { view: "page", open: ["header.share"], description: "People invited to the page, each with their own access" },
  "pdf.viewer": { view: "pdf", description: "The PDF viewer" },
  "pdf.page": { view: "pdf", description: "Each rendered PDF page; supports rectangle drags" },
  "pdf.textLayer": { view: "pdf", description: "Selectable text on each rendered PDF page" },
  "pdf.highlightColor": { view: "pdf", open: ["pdf.textLayer"], description: "The first colour in the text selection palette" },
  "pdf.inkButton": { view: "pdf", description: "Opens and closes the handwriting tools" },
  "pdf.citation": { view: "citation", description: "The passage a citation link marked in the PDF" },
  "ink.toolbar": { view: "ink", open: ["pdf.inkButton"], description: "The handwriting tool strip" },
  "dock.notes": { view: "page", description: "The notes: docked beside a PDF, or filling the page without one" },
  "notes.editor": { view: "page", open: ["dock.notes"], description: "The active note editor" },
  "notes.ink": { view: "ink", pick: "last", description: "A handwriting block in the notes" },
  "notes.table": { view: "table", description: "An editable table in the notes" },
  "notes.tableAdd": { view: "table", description: "The strip under a table that adds a row" },
  "notes.peers": { view: "presence", description: "Avatars on the block another person is on" },
  "editor.refSearch": { view: "editing", description: "The [[ block search while typing a reference" },
  "editor.mathPreview": { view: "editing", description: "The live preview of the formula being typed" },
  "merge.versions": { view: "merge", description: "A conflict's versions: this clone, origin and the merge" },
  "merge.apply": { view: "merge", description: "Apply the chosen version of a conflict" },
  "page.labels": { view: "page", description: "The paper's labels" },
  "page.labelInput": { view: "page", open: ["page.labels"], description: "Add a label to this paper" },
  "page.presence": { view: "presence", description: "Who else is on this page" },
  "home.listing": { view: "home", description: "The library listing's bar: sort, kinds and view" },
  "chat.composer": { view: "chat", description: "The message composer and Send button" },
  "chat.input": { view: "chat", description: "Chat message text box" },
  "chat.voice": { view: "chat", description: "Voice input button" },
  "chat.imageContext": { view: "chat", description: "PDF selections attached to the message" },
  "chat.context": { view: "chat", description: "Add attachments or library pages" },
  "chat.settings": { view: "chat", description: "Chat model, reasoning, context and tool settings" },
  "chat.tools": { view: "chat", description: "Enable or disable assistant tools" },
  "chat.citation": { view: "citation", pick: "last", description: "A citation link in the newest chat reply" },
};

export const ATTR = "data-guide";

export function anchorElement(id) {
  if (!id) return null;
  const all = document.querySelectorAll(`[${ATTR}="${id}"]`);
  return (ANCHORS[id]?.pick === "last" ? all[all.length - 1] : all[0]) || null;
}

// Which views an anchor is expected in; used by the e2e presence check.
export function anchorsForView(view) {
  return Object.entries(ANCHORS)
    .filter(([, a]) => a.view === "any" || a.view === view || (view === "pdf" && a.view === "page"))
    .map(([id]) => id);
}
