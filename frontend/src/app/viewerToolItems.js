// The viewer's side strip (the PDF's and the notebook's, Settings →
// Appearance › Viewer tools): its configurable items in the strip's
// default order. Pure (no React, no DOM), so node tests it
// (tests/viewerToolItems.test.mjs): App places the strip's buttons with
// VIEWER_TOOLS.place, the pane draws one row per item and drags them into
// order.
//
// The `viewerTools` preference (app/prefDefs.js) is a bar layout
// (shared/model/barLayout.js), { order, modes }, each item "always" on the
// strip or "hidden": a row of the ⋯ menu at the strip's end, which shows
// while some tool that applies here is hidden. A tool in use stays on the
// strip whatever its mode (App pins it): Record while recording, Translate
// while it translates or shows a translation, the pen while its tools are
// open, the text highlighter while it is on. Translate hidden is how the
// translate button is turned off.
//
// The default order is the strip's own: the PDF and the notebook viewer
// share the run up to Record, in the same places, so the viewer tours
// teach it once; what only one of them has comes after. Whether an item
// applies to the open viewer (a notebook's Paper, a PDF's Translate, a
// touch screen's selection mode, nothing to draw with on a read-only page)
// is App's to say.
import { T } from "../shared/i18n/locales.js";
import { defineBar } from "../shared/model/barLayout.js";

// Each item: `id` (its key in the preference, and its data-guide after
// "viewer."), `label` and `hint` (its Settings row; the hint says where
// it applies when that is not everywhere), `keywords` (English synonyms
// for the settings search), and `modes` (the choices offered, the first
// its default).
export const VIEWER_TOOL_ITEMS = Object.freeze([
  {
    id: "zoom", label: T("Zoom"),
    hint: T("Zoom out, zoom in and fit to width, together."),
    keywords: "zoom in out fit width magnify scale",
    modes: ["always", "hidden"],
  },
  {
    id: "ink", label: T("Handwriting tools"),
    hint: T("The pen, highlighter, eraser and the rest of the markup strip."),
    keywords: "pen pencil ink draw markup highlighter eraser",
    modes: ["always", "hidden"],
  },
  {
    id: "record", label: T("Record audio"),
    hint: T("What you write meanwhile replays in step with it."),
    keywords: "microphone mic audio recording voice",
    modes: ["always", "hidden"],
  },
  {
    id: "highlighter", label: T("Text highlighter"),
    hint: T("PDFs only. What you select is highlighted at once, in the colour you used last."),
    keywords: "highlight marker colour color select text quick",
    modes: ["always", "hidden"],
  },
  {
    id: "translate", label: T("Translate"),
    hint: T("PDFs only. The page in another language."),
    keywords: "translation language translated view",
    modes: ["always", "hidden"],
  },
  {
    id: "paper", label: T("Notebook paper"),
    hint: T("Notebooks only. A page's size, pattern and colour."),
    keywords: "paper sheet ruled grid lined background",
    modes: ["always", "hidden"],
  },
  {
    id: "notesView", label: T("Notes view"),
    hint: T("Notebooks only. The pages among the notes."),
    keywords: "notes outline back to notes",
    modes: ["always", "hidden"],
  },
  {
    id: "selectMode", label: T("Selection mode"),
    hint: T("On touch screens. A drag selects text, or draws an area note."),
    keywords: "select text rectangle area touch finger",
    modes: ["always", "hidden"],
  },
]);

// The strip (shared/model/barLayout.js): the preference's default and
// codec, the edits its Settings rows make, and where each item goes.
export const VIEWER_TOOLS = defineBar(VIEWER_TOOL_ITEMS, {
  tips: { hidden: T("In the ⋯ menu at the end of the strip") },
});
