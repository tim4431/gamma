import { T } from "../../shared/i18n/i18n.js";
// The viewer's left edge, top to bottom: the PDF's table of contents, then
// the tool column. The PDF and the notebook viewer share the column up to
// the pen, so their tours are siblings: the zoom and pen steps are `shared`,
// the same objects in both, and a user who has finished either tour gets
// the other without them — only what is new there (stepApplies in
// guide/triggers.js, docs/dev/onboarding.md).

const zoom = { id: "viewer-zoom", anchor: "viewer.zoom", placement: "right", shared: true,
  title: T("Zoom, or fit to the width"),
  body: T("{key:Mod} + scroll zooms at the pointer."),
  bodyTouch: T("Pinching zooms too."),
  // Zoom out, zoom in, fit to width: the column's three buttons, top to bottom.
  scene: [{ point: "viewer.zoom", at: [0.5, 0.17] }, { point: "viewer.zoom", at: [0.5, 0.5] }, { point: "viewer.zoom", at: [0.5, 0.83] }] };
const pen = { id: "viewer-pen", anchor: "pdf.inkButton", placement: "right", shared: true, requires: { editable: true },
  title: T("Write and draw on the page"),
  body: T("{key:Escape} puts the pen away."),
  // The stroke goes on the pane, which holds either viewer: the notebook
  // viewer has no pdf.viewer of its own.
  scene: [{ click: "pdf.inkButton" }, { stroke: "pdf.pane", at: [0.14, 0.3, 0.44, 0.4] }] };

export const pdfViewer = {
  id: "pdf-viewer",
  version: 1,
  title: T("The PDF viewer"),
  sibling: "notebook-view",
  requires: { hasPdf: true, viewerTools: true },
  // After Arrange windows, which the registry lists first: the first paper
  // of a load gets that offer, a paper on a later visit this one (the
  // phone, without windows, gets this one first).
  trigger: { event: "page.opened" },
  offerAnchor: "viewer.tools",
  offerPlacement: "right",
  offer: { title: T("You opened a paper"), line: T("Its tools sit down the left edge.") },
  steps: [
    { id: "pdf-outline", anchor: "viewer.outline", placement: "right", optional: true,
      title: T("Click a heading to jump there") },
    zoom,
    pen,
    { id: "pdf-translate", anchor: "viewer.translate", placement: "right", optional: true,
      title: T("Translate the page you're reading"),
      body: T("Right-click for options; hold {key:Alt} for the original."),
      bodyTouch: T("Long-press for options.") },
    { id: "pdf-select", anchor: "viewer.selectMode", placement: "right", requires: { touch: true },
      title: T("Choose what a drag does"),
      body: T("Select text, or box a figure.") },
  ],
};

export const notebookView = {
  id: "notebook-view",
  version: 1,
  title: T("The notebook view"),
  sibling: "pdf-viewer",
  requires: { notebookView: true, viewerTools: true },
  // Offered once the user is in the notebook view (Pages to write on ends
  // by pointing at the button that opens it).
  trigger: { requires: { notebookView: true } },
  offerAnchor: "viewer.tools",
  offerPlacement: "right",
  offer: { title: T("You're in the notebook view"), line: T("Its tools, its paper, and the way back to your notes.") },
  steps: [
    zoom,
    pen,
    { id: "nbv-paper", anchor: "viewer.paper", placement: "right", requires: { editable: true },
      title: T("Change this page's paper"),
      body: T("**Apply to all pages** sets every page at once.") },
    { id: "nbv-notes", anchor: "viewer.notesView", placement: "right", media: "page-notebook",
      title: T("Back to your notes"),
      body: T("The same pages, among your notes again.") },
  ],
};
