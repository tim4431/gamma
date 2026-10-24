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
// marked. An anchor inside inert content is none: a folded dock window keeps
// its body mounted, out of sight and inert (DockWindow), and nothing there
// can be pointed at until it opens.

export const ANCHORS = {
  "header.view": { view: "desktop", description: "View menu: reopen closed windows" },
  "view.menu": { view: "desktop", open: ["header.view"], description: "The View menu itself" },
  "view.import": { view: "desktop", open: ["header.view"], surface: "view.menu", description: "Import…: a Zotero library, Notability notes, an Obsidian vault, a Notion export, Logseq highlights or a Gamma export" },
  "header.tabs": { view: "tabs", description: "The open-tab strip; right-click a tab to pin it, or to pin its page to the library" },
  "chat.grip": { view: "desktop chat", description: "Chat title: drag to dock, double-click to collapse or expand" },
  "notes.grip": { view: "docked notes", description: "Notes title: drag to dock, double-click to collapse or expand" },
  "comments.grip": { view: "docked comments", description: "Comments title: drag to dock, double-click to collapse or expand" },
  "header.home": { view: "any", description: "The Home button in the topbar; on a phone, the bottom bar's Library tab" },
  "header.back": { view: "nav", description: "Back to where you were before a link jump; there only after one" },
  "header.add": { view: "any", description: "Add — new page, PDF by URL / arXiv / DOI, uploads" },
  "header.tasks": { view: "any", description: "Background tasks (exports, backups, restores, imports, indexing, downloads, uploads); a row of the More sheet on a phone, without the anchor" },
  "header.inbox": { view: "any", description: "The Inbox bell with its unread count: mentions, replies, resolved threads, shares and invitations; a row of the More sheet on a phone, without the anchor" },
  "header.search": { view: "any", description: "Workspace search (Ctrl+F)" },
  "header.share": { view: "page", description: "The Share button: the open page's, or on the library the open folder's" },
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
  "share.start": { view: "unshared page", open: ["header.share"], surface: "share.popover", description: "The share popover's Share button, which makes the link; there only before the page is shared" },
  "share.link": { view: "shared page", open: ["header.share"], surface: "share.popover", description: "The share popover's link with Copy link and Stop sharing, once the page is shared" },
  "share.access": { view: "shared page", open: ["header.share"], surface: "share.popover", description: "General access: who can open the share link, and whether they can edit" },
  "share.people": { view: "shared page", open: ["header.share"], surface: "share.popover", description: "Who has access: the invite box, you, the workspace's members, each invited person with their own access" },
  "share.stop": { view: "shared page", open: ["header.share"], surface: "share.popover", description: "Stop sharing beside the link, confirmed inline" },
  "pdf.viewer": { view: "pdf", description: "The PDF viewer" },
  "pdf.page": { view: "pdf", description: "Each rendered PDF page; supports rectangle drags" },
  "pdf.textLayer": { view: "pdf", description: "Selectable text on each rendered PDF page" },
  "pdf.highlightColor": { view: "pdf", open: ["pdf.textLayer"], description: "The first colour in the text selection palette" },
  "pdf.pane": { view: "pdf", description: "The PDF with its controls and tool strip" },
  "pdf.inkButton": { view: "viewer", description: "Opens and closes the handwriting tools; in the PDF and the notebook viewer's tool column" },
  // The viewer's left edge, the same in the PDF and the notebook viewer up
  // to the pen; "viewer" is either of them, open and not closed.
  "viewer.tools": { view: "viewer", description: "The viewer's tool column, mid-left: zoom, the pen, then what only this viewer has" },
  // Recordings (docs/dev/recording.md): the mic after the pen, the pill
  // while one is recorded, a recording's card in the notes, and the player.
  "viewer.record": { view: "viewer", description: "Records audio; in the PDF and the notebook viewer's tool column after the pen" },
  "recording.pill": { view: "viewer", description: "The recording in progress: its time, the input level, Pause, Bookmark and Stop" },
  "recording.card": { view: "notes", pick: "last", description: "A recording block in the notes: its length and date, Play and its menu" },
  "recording.player": { view: "viewer", description: "The recording player at the viewer's bottom: the scrubber with its writing marks, the display modes, the speed" },
  "viewer.zoom": { view: "viewer", description: "Zoom out, zoom in and fit to width" },
  "viewer.fullscreen": { view: "viewer", description: "Full screen, bottom-left" },
  "viewer.translate": { view: "translatable pdf", description: "Translate this page; right-click or long-press for the whole document and options" },
  "viewer.selectMode": { view: "touch pdf", description: "On a touch screen: whether a drag selects text or draws a box" },
  "viewer.outline": { view: "pdf with an outline", description: "The PDF's table of contents, top-left, when the PDF carries one" },
  "viewer.paper": { view: "notebook view", description: "The notebook view's paper button: size, pattern and colour of the page in view" },
  "viewer.notesView": { view: "notebook view", description: "Back to the notes view: the same pages among the notes" },
  "pdf.citation": { view: "citation", description: "The passage a citation link marked in the PDF" },
  "pdf.highlight": { view: "highlighted pdf", pick: "recent", description: "A highlight on the PDF (its first line); the one made last, when it is on a page in view" },
  "ink.toolbar": { view: "ink", open: ["pdf.inkButton"], description: "The handwriting tool strip" },
  "ink.options": { view: "ink", description: "The armed pen's colour, width and style row" },
  "ink.eraser": { view: "ink", open: ["pdf.inkButton"], description: "The eraser tool" },
  "ink.lasso": { view: "ink", open: ["pdf.inkButton"], description: "The lasso: select strokes to move or delete" },
  "ink.text": { view: "ink", open: ["pdf.inkButton"], description: "The Text tool: click the page to type, or drag to set the width" },
  "ink.more": { view: "ink", open: ["pdf.inkButton"], description: "More handwriting tools: paste, select all, the ruler, shapes, the zoom window, settings" },
  "ink.ruler": { view: "ink ruler", description: "The pressed Ruler button while the ruler shows: its options (angle, snap, stamp a line, units, hide)" },
  "ink.shape": { view: "ink shapes", description: "The pressed Shapes button while the Shapes tool is armed: drag on the page to draw a line, an arrow, a rectangle or an ellipse" },
  "ink.zoom": { view: "ink zoom", description: "The zoom window under the pages: write large in it; its bar steps along the lines and sets the magnification" },
  "ink.selection": { view: "ink selection", description: "The box around selected handwriting: drag it to move, its handles resize and rotate" },
  "dock.notes": { view: "page", description: "The notes: docked beside a PDF, or filling the page without one" },
  "notes.editor": { view: "page", open: ["dock.notes"], description: "The active note editor" },
  "notes.ink": { view: "ink", pick: "last", description: "A handwriting block in the notes: its caption and the drawing" },
  "notes.highlight": { view: "highlighted page", pick: "recent", description: "A highlight's card in the notes, the one made last if any; a click jumps to it in the PDF" },
  "notes.handle": { view: "notes", pick: "last", description: "A note's ⋮⋮ handle: drag to move or nest it, click for its menu (shown on hover)" },
  "notes.addBelow": { view: "notes", pick: "last", description: "The + under a note's handle: a new note below (Alt+click: above)" },
  "notes.object": { view: "selected object", description: "The picture, table or diagram the user just selected in the notes" },
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
  "editor.slashMenu": { view: "editing", description: "The / command menu while it is open" },
  "editor.pasteAs": { view: "editing", description: "The Paste as chooser after a paste: mention, synced block, titled link, levels" },
  "editor.mentionMenu": { view: "editing", description: "The @ menu: dates, reminders, people and pages" },
  "merge.versions": { view: "merge", description: "A conflict's versions: this clone, origin and the merge" },
  "page.labels": { view: "page", description: "The paper's labels" },
  "page.labelInput": { view: "page", open: ["page.labels"], description: "Add a label to this paper" },
  "page.presence": { view: "presence", description: "Who else is on this page" },
  "page.export": { view: "exportable page", open: ["header.view"], description: "Export… — the page as an annotated PDF, Markdown, a Logseq graph, a Zotero library or a Gamma export" },
  "page.metadata": { view: "paper", description: "The ⓘ button: the paper's details (type, cite key, Verify); a red ! marks them unverified" },
  "transfer.bibLink": { view: "bibtex export", description: "Keep this .bib up to date: the bibliography's link for Overleaf" },
  "palette.input": { view: "palette", description: "The Ctrl+P palette's box; > turns it into the command palette" },
  "home.listing": { view: "home", description: "The library listing's bar: sort, kinds and view" },
  "home.filter": { view: "library", description: "The listing bar's filter box: titles and labels; Enter with no match searches inside notes and PDFs" },
  "home.select": { view: "library", description: "Select: pick several pages or folders to act on together" },
  "home.labelsView": { view: "library", description: "The kind toggle's Labels: the labels shown as folders" },
  "home.empty": { view: "fresh", description: "Start your library: a library with no pages of the user's own yet" },
  // The library's own surfaces come and go with what is in it, so none of
  // them is a "home" anchor the browser suite expects on every library.
  "home.card": { view: "library", description: "A page card in the library grid; right-click opens its menu (move, label, pin, delete)" },
  "home.cardLabels": { view: "labelled page", description: "The folder and label chips on a page card" },
  "home.pinned": { view: "pinned", description: "The pinned strip: the pages and folders kept at the top" },
  "home.recents": { view: "recents", description: "Recently viewed: the strip of pages opened lately" },
  "home.trash": { view: "organizable library", description: "Recently deleted: pages restorable for 30 days" },
  "chat.composer": { view: "chat", description: "The message composer and Send button" },
  "chat.new": { view: "chat", description: "New chat: keeps this conversation in History (Alt+click: discards it)" },
  "chat.history": { view: "chat", description: "Chat history: earlier conversations of this page, folder or library" },
  "chat.model": { view: "chat", description: "The composer's model chip: model, reasoning effort and speed" },
  "chat.pdfSwitch": { view: "chat with a PDF", description: "The PDF switch: the whole file with each message, or its text only" },
  "chat.changes": { view: "agent changes", pick: "last", description: "A reply's list of what the assistant changed, each with its own undo" },
  "chat.input": { view: "chat", description: "Chat message text box" },
  "chat.setup": { view: "chat", description: "The setup card the chat shows while no AI is connected" },
  "chat.imageContext": { view: "chat", description: "Pictures attached to the message" },
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
  const all = [...document.querySelectorAll(`[${ATTR}="${id}"]`)].filter((el) => !el.closest("[inert]"));
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
