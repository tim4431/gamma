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
// first to reveal them. surface: the popover or panel such an anchor sits
// in; a tour's spotlight leaves that whole surface undimmed, with the ring
// on the anchor itself, and its card keeps clear of it. pick: when the
// anchor repeats, "last" means the newest one (the latest chat reply),
// "recent" the one inside the element the app marks data-guide-recent (the
// table the user just made); the first one otherwise, and when nothing is
// marked.

export const ANCHORS = {
  "header.view": { view: "desktop", description: "View menu: reopen closed windows" },
  "chat.grip": { view: "desktop chat", description: "Chat title: drag to dock, double-click to collapse or expand" },
  "notes.grip": { view: "docked notes", description: "Notes title: drag to dock, double-click to collapse or expand" },
  "header.home": { view: "any", description: "The Home button in the topbar; on a phone, the bottom bar's Library tab" },
  "header.back": { view: "nav", description: "Back to where you were before a link jump; there only after one" },
  "header.add": { view: "any", description: "Add — new page, PDF by URL / arXiv / DOI, uploads" },
  "header.tasks": { view: "any", description: "Background tasks (exports, backups, restores, imports, indexing, downloads, uploads); a row of the More sheet on a phone, without the anchor" },
  "header.search": { view: "any", description: "Workspace search (Ctrl+F)" },
  "header.share": { view: "page", description: "The page's Share button" },
  "header.account": { view: "any", description: "Account & settings menu; on a phone, the bottom bar's More, the same menu with the topbar's other actions on top" },
  "account.menu": { view: "any", open: ["header.account"], description: "The account menu itself" },
  "account.tour": { view: "any", open: ["header.account"], surface: "account.menu", description: "Tours submenu in the account menu" },
  "account.card": { view: "any", open: ["header.account"], surface: "account.menu", description: "The account menu's card: you, this workspace and your role" },
  "account.workspaces": { view: "any", open: ["header.account"], surface: "account.menu", description: "The account menu's workspace switcher" },
  "add.popover": { view: "any", open: ["header.add"], description: "The Add popover itself" },
  "add.urlInput": { view: "any", open: ["header.add"], surface: "add.popover", description: "The Add popover's URL / arXiv / DOI box" },
  "add.upload": { view: "any", open: ["header.add"], surface: "add.popover", description: "Upload files — PDFs and markdown notes from this computer" },
  "add.newPage": { view: "any", open: ["header.add"], surface: "add.popover", description: "New page: an empty page of notes" },
  "add.newNotebook": { view: "any", open: ["header.add"], surface: "add.popover", description: "New notebook: a page that starts with one blank sheet to write on" },
  "tasks.panel": { view: "tasks", open: ["header.tasks"], description: "The background tasks panel: In progress, then Finished" },
  "tasks.row": { view: "tasks", open: ["header.tasks"], surface: "tasks.panel", description: "One task's row: its bar and detail, with Stop, Download, Start again and Remove" },
  "tasks.clear": { view: "tasks", open: ["header.tasks"], surface: "tasks.panel", description: "Clear finished: drops the finished rows and their files" },
  "share.popover": { view: "page", open: ["header.share"], description: "The Share popover itself" },
  "share.link": { view: "shared page", open: ["header.share"], surface: "share.popover", description: "The share popover's link and its Copy link button, once the page is shared" },
  "share.access": { view: "page", open: ["header.share"], surface: "share.popover", description: "General access: who can open the share link, and whether they can edit; the first pick creates the link" },
  "share.people": { view: "page", open: ["header.share"], surface: "share.popover", description: "Who has access: the invite box, you, the workspace's members, each invited person with their own access" },
  "share.stop": { view: "shared page", open: ["header.share"], surface: "share.popover", description: "Stop sharing, confirmed inline" },
  "pdf.viewer": { view: "pdf", description: "The PDF viewer" },
  "pdf.page": { view: "pdf", description: "Each rendered PDF page; supports rectangle drags" },
  "pdf.textLayer": { view: "pdf", description: "Selectable text on each rendered PDF page" },
  "pdf.highlightColor": { view: "pdf", open: ["pdf.textLayer"], description: "The first colour in the text selection palette" },
  "pdf.pane": { view: "pdf", description: "The PDF with its controls and tool strip" },
  "pdf.inkButton": { view: "viewer", description: "Opens and closes the handwriting tools; in the PDF and the notebook viewer's tool column" },
  // The viewer's left edge, the same in the PDF and the notebook viewer up
  // to the pen; "viewer" is either of them, open and not closed.
  "viewer.tools": { view: "viewer", description: "The viewer's tool column, mid-left: zoom, the pen, then what only this viewer has" },
  "viewer.zoom": { view: "viewer", description: "Zoom out, zoom in and fit to width" },
  "viewer.fullscreen": { view: "viewer", description: "Full screen, bottom-left" },
  "viewer.translate": { view: "translatable pdf", description: "Translate this page; right-click or long-press for the whole document and options" },
  "viewer.selectMode": { view: "phone pdf", description: "On a phone: whether a drag selects text or draws a box" },
  "viewer.outline": { view: "pdf with an outline", description: "The PDF's table of contents, top-left, when the PDF carries one" },
  "viewer.paper": { view: "notebook view", description: "The notebook view's paper button: size, pattern and colour of the page in view" },
  "viewer.notesView": { view: "notebook view", description: "Back to the notes view: the same pages among the notes" },
  "pdf.citation": { view: "citation", description: "The passage a citation link marked in the PDF" },
  "ink.toolbar": { view: "ink", open: ["pdf.inkButton"], description: "The handwriting tool strip" },
  "ink.options": { view: "ink", description: "The armed pen's colour, width and style row" },
  "ink.eraser": { view: "ink", open: ["pdf.inkButton"], description: "The eraser tool" },
  "ink.lasso": { view: "ink", open: ["pdf.inkButton"], description: "The lasso: select strokes to move or delete" },
  "dock.notes": { view: "page", description: "The notes: docked beside a PDF, or filling the page without one" },
  "notes.editor": { view: "page", open: ["dock.notes"], description: "The active note editor" },
  "notes.ink": { view: "ink", pick: "last", description: "A handwriting block in the notes: its caption and the drawing" },
  "notes.table": { view: "table", pick: "recent", description: "An editable table in the notes" },
  "notes.tableAdd": { view: "table", pick: "recent", description: "The strip under a table that adds a row" },
  "notes.tableCorner": { view: "table", pick: "recent", description: "The corner handle that selects a whole table" },
  "notes.peers": { view: "presence", description: "Avatars on the block another person is on" },
  "notes.sheet": { view: "notebook", pick: "last", description: "A sheet of paper standing among the notes, with its own tool row under it" },
  "sheet.pen": { view: "notebook", description: "Write on this sheet: opens the handwriting tools" },
  "sheet.paper": { view: "notebook", description: "The sheet's paper button: size, orientation, pattern and background" },
  "sheet.notebookView": { view: "notebook", description: "Open the sheets in the viewer's place (the notebook view)" },
  "notebook.paperMenu": { view: "paper menu", open: ["sheet.paper"], description: "The paper menu itself, down to Apply to all pages" },
  "editor.refSearch": { view: "editing", description: "The [[ block search while typing a reference" },
  "editor.mathPreview": { view: "editing", description: "The live preview of the formula being typed" },
  "merge.versions": { view: "merge", description: "A conflict's versions: this clone, origin and the merge" },
  "page.labels": { view: "page", description: "The paper's labels" },
  "page.labelInput": { view: "page", open: ["page.labels"], description: "Add a label to this paper" },
  "page.presence": { view: "presence", description: "Who else is on this page" },
  "page.export": { view: "exportable page", open: ["header.view"], description: "Export… — the page as an annotated PDF, Markdown, a Logseq graph, a Zotero library or a Gamma export" },
  "home.listing": { view: "home", description: "The library listing's bar: sort, kinds and view" },
  "home.empty": { view: "fresh", description: "Start your library: a library with no pages of the user's own yet" },
  // The library's own surfaces come and go with what is in it, so none of
  // them is a "home" anchor the browser suite expects on every library.
  "home.card": { view: "library", description: "A page card in the library grid; right-click opens its menu (move, label, pin, delete)" },
  "home.cardLabels": { view: "labelled page", description: "The folder and label chips on a page card" },
  "home.pinned": { view: "pinned", description: "The pinned strip: the pages and folders kept at the top" },
  "home.recents": { view: "recents", description: "Recently viewed: the strip of pages opened lately" },
  "home.trash": { view: "organizable library", description: "Recently deleted: pages restorable for 30 days" },
  "chat.composer": { view: "chat", description: "The message composer and Send button" },
  "chat.input": { view: "chat", description: "Chat message text box" },
  "chat.setup": { view: "chat", description: "The setup card the chat shows while no AI is connected" },
  "chat.imageContext": { view: "chat", description: "PDF selections attached to the message" },
  "chat.context": { view: "chat", description: "Add attachments or library pages" },
  "chat.settings": { view: "chat", description: "Chat model, reasoning, context and tool settings" },
  "chat.tools": { view: "chat", description: "Enable or disable assistant tools" },
  "chat.citation": { view: "citation", pick: "last", description: "A citation link in the newest chat reply" },
  "chat.approval": { view: "approval", pick: "last", description: "The card that asks before the assistant makes a change: what it would do, and the four answers" },
  "sync.pill": { view: "mirror", description: "The sync pill of a workspace that has an offline copy or a publication" },
};

export const ATTR = "data-guide";

export function anchorElement(id) {
  if (!id) return null;
  const all = [...document.querySelectorAll(`[${ATTR}="${id}"]`)];
  const pick = ANCHORS[id]?.pick;
  if (pick === "last") return all[all.length - 1] || null;
  if (pick === "recent") return all.find((el) => el.closest(`[${ATTR}-recent]`)) || all[0] || null;
  return all[0] || null;
}

// Which views an anchor is expected in; used by the e2e presence check.
export function anchorsForView(view) {
  return Object.entries(ANCHORS)
    .filter(([, a]) => a.view === "any" || a.view === view || (view === "pdf" && a.view === "page"))
    .map(([id]) => id);
}
