// The illustration registry: the pictures a guide card may show above its
// copy, by id — the same contract as the anchor registry, so a tour names an
// illustration and never a file path (docs/dev/onboarding.md, "Drawings and
// clips").
//
// Two kinds, one grammar. A drawing is for a concept that has no place on
// screen (one paper in many folders). A clip shows a gesture on an important
// control in miniature — the pointer, its press, what it carries and where
// that lands — and what the app does in answer, so the card says what
// happens while the spotlight says where. A step shows a clip or a scene,
// never both. The welcome deck shows the same files, one per feature card.
// A step's `media` (or a deck card's) is an id here; the file is
// `guide/media/<id>.svg`, inlined into the card by GuideOverlay so it reads
// the theme's tokens — eight themes, so a picture that carried its own
// colours would be wrong in most of them. A file is shapes and nothing more:
// its look and its motion are the shared classes of guide/media.css (no
// <style>, no colour, no words of its own), so every one plays the same
// rhythm and rests on its finished picture, which is also all that reduced
// motion shows.
//
// ratio: the picture's width over its height (its viewBox). The card reserves
// the box from this before the picture is laid out, so a step with a picture
// never moves its own card after placement (`place.js` measures the card
// once). Keep it between 1.4 and 2.2: the card is 320 px wide, so a taller
// picture pushes the copy off a short viewport.
//
// shows (a clip's): what it depicts of the app — the anchors of the controls
// it draws, the events of the outcome it shows, the dock zones it lands in.
// tests/guide.test.mjs holds both sides to it: each must still exist, and a
// step showing the clip must point at one of its anchors and advance on one
// of its events. A clip draws behaviour, not pixels, so restyling a control
// leaves it right; changing what the gesture does fails that test, which
// names the clip to redraw.

export const MEDIA = {
  "labels-folders": { ratio: 1.55, description: "One paper carries labels and sits in several folders at once, never copied" },
  "page-notebook": { ratio: 1.6, description: "One page, two views: a sheet among the notes, and the same sheets filling the viewer" },
  "window-dock": { ratio: 1.6, description: "Chat carried by its title over the bottom and docked on the left; the paper narrows, Notes takes the right",
    shows: { anchors: ["chat.grip"], events: ["window.moved"], zones: ["bottom", "left"] } },
  "window-fold": { ratio: 1.6, description: "Chat's title double-clicked: Chat folds to its title strip at the foot of the dock and Notes takes the room",
    shows: { anchors: ["chat.grip"], events: ["window.collapsed"] } },
  "highlight-note": { ratio: 1.6, description: "A sentence dragged over, a colour picked from the popup under it, and the highlight's note lands in Notes, linked to it",
    shows: { anchors: ["pdf.viewer", "pdf.highlightColor", "dock.notes", "notes.highlight"], events: ["highlight.created"] } },
  "chat-figure": { ratio: 1.6, description: "A box dragged around a figure joins the question as a snapshot; sent, the answer's citation opens the passage it quotes",
    shows: { anchors: ["pdf.viewer", "chat.imageContext", "chat.composer", "chat.citation"], events: ["chat.sent", "chat.cited", "citation.shown"] } },
  "ink-pen": { ratio: 1.6, description: "The pen turned on in the viewer's tool column, a phrase circled and an arrow drawn into the margin; the drawing lands in Notes as a note",
    shows: { anchors: ["pdf.inkButton", "pdf.pane", "notes.ink"], events: ["ink.stroke"] } },
  "notes-move": { ratio: 1.6, description: "A note carried by its ⋮⋮ handle down a row and to the right; the drop line steps in an indent, and the note lands nested under the one above",
    shows: { anchors: ["notes.handle"], events: [] } },
  "sheet-grow": { ratio: 1.6, description: "Writing low on the last sheet adds the next one right after it, on the same paper; the blocks below move down",
    shows: { anchors: ["notes.sheet"], events: ["sheet.created"] } },
  "highlight-jump": { ratio: 1.6, description: "A highlight clicked on the paper lights its note in Notes; the note clicked lights the passage again",
    shows: { anchors: ["pdf.highlight", "notes.highlight"], events: [] } },
  "citation-jump": { ratio: 1.6, description: "A citation in Chat's answer clicked: the paper turns to the cited page and the quoted lines are marked",
    shows: { anchors: ["chat.citation", "pdf.citation"], events: ["chat.cited", "citation.shown"] } },
  "record-replay": { ratio: 1.6, description: "The microphone pressed, the recording pill running, a word written; the recording lands in Notes, linked to the writing",
    shows: { anchors: ["viewer.record", "recording.pill", "recording.card"], events: ["recording.stopped", "recording.played"] } },
  "search-filter": { ratio: 1.6, description: "A query typed: titles, notes and PDF text answer in groups, a folder chip narrows them, a PDF-text result is opened",
    shows: { anchors: ["search.input", "search.results"], events: ["search.results"] } },
  "ink-lasso": { ratio: 1.6, description: "The eraser pressed in the markup strip rubs out a stray stroke; the lasso loops round a written word, its selection box stands round it and is carried to the right",
    shows: { anchors: ["pdf.pane", "ink.eraser", "ink.lasso", "ink.selection"], events: ["ink.erased", "ink.selected"] } },
  "ink-shape": { ratio: 1.6, description: "More pressed at the end of the markup strip, Shapes picked from its menu and armed in the strip; a drag lays down a clean rectangle, a second an arrow from it",
    shows: { anchors: ["ink.more", "ink.shape"], events: [] } },
  "table-grow": { ratio: 1.6, description: "The + under a table pressed: a new row slides in under the last, the strip and the notes below move down, and the row's ⋮ handle comes up under the pointer",
    shows: { anchors: ["notes.tableAdd", "notes.table"], events: ["table.edited"] } },
  "share-link": { ratio: 1.6, description: "Copy link pressed in the Share popover: the link goes out, the page opens in someone else's window, and each window shows the other's face",
    shows: { anchors: ["share.link"], events: ["share.created"] } },
  "library-select": { ratio: 1.6, description: "Select pressed, three cards ticked one by one and carried as one bundle onto a folder, which lights; the cards keep their places",
    shows: { anchors: ["home.select", "home.card"], events: [] } },
  "tasks-outlive": { ratio: 1.6, description: "A job's bar fills in the tasks tray; the tab is closed while the server keeps blinking, and the tab back shows the same job further on",
    shows: { anchors: ["header.tasks", "tasks.row"], events: ["job.started"] } },
  "comment-passage": { ratio: 1.6, description: "A passage dragged over, the speech bubble after the palette's colours pressed, and the comment opens in the margin level with the passage, a line typed in it",
    shows: { anchors: ["pdf.highlightColor", "pdf.viewer"], events: ["comment.created"] } },
  // The setup guides': a gesture that ends in Settings or outside the app.
  "zotero-link": { ratio: 1.6, description: "Link pressed in the Zotero link dialog: the new workspace fills card by card while the sync pill counts, then lights",
    shows: { anchors: ["zotero.link", "sync.pill"], events: ["zotero.linked"] } },
  "assistant-connect": { ratio: 1.6, description: "The setup command typed in a terminal, the workspace allowed in the browser, and a question to Gamma answered with a citation",
    shows: { anchors: ["settings.assistantSetup"], events: ["mcp.connected"] } },
  "tex-follow": { ratio: 1.6, description: "The followed PDF rebuilt on disk reaches the page: a new paragraph opens and the highlight moves down with its text",
    shows: { anchors: ["page.follow", "pdf.viewer"], events: ["page.fileReplaced"] } },
  // A setup guide's drawing: what happens in another app, before Gamma.
  "notability-export": { ratio: 1.6, description: "Notability's Export Options with Note picked and Export pressed; the .note goes to Gamma, where the page opens with its handwriting live and the drawing as a note" },
  // The welcome deck's: what has no one control to point at.
  "zotero-sync": { ratio: 1.6, description: "A highlight made in Gamma reaches Zotero through zotero.org as an annotation, and a collection set there comes back as a folder" },
  "notability-sync": { ratio: 1.6, description: "A handwritten Notability page comes in as live ink and a note, the pen adds to it, and it goes back out as a .note" },
  "assistants-mcp": { ratio: 1.6, description: "A question to an AI assistant mentions Gamma; through Gamma's MCP server it searches and reads the page, then answers citing it" },
};

export const mediaRatio = (id) => MEDIA[id]?.ratio || 1.6;
