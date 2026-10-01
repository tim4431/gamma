import { T } from "../../shared/i18n/i18n.js";
// The viewer's left edge, top to bottom: the PDF's table of contents, the
// tool column, full screen. The PDF and the notebook viewer share the
// column up to the pen, and full screen, so their tours are siblings: the
// steps about those are `shared`, the same objects in both. Once the user
// has finished either tour, the other drops them for its `recap` card, which
// points at the same buttons and moves on to what is new there
// (stepApplies in guide/triggers.js, docs/dev/onboarding.md).

const zoom = { id: "viewer-zoom", anchor: "viewer.zoom", placement: "right", shared: true,
  title: T("Zoom in and out, or fit the page to the width"),
  body: T("{key:Mod} and the scroll wheel zoom around the pointer."),
  bodyTouch: T("Pinching zooms too.") };
const pen = { id: "viewer-pen", anchor: "pdf.inkButton", placement: "right", shared: true, requires: { editable: true },
  title: T("Write and draw on the page"),
  body: T("Pens, highlighters, an eraser, a lasso and text boxes; {key:Escape} puts them away.") };
const fullscreen = { id: "viewer-fullscreen", anchor: "viewer.fullscreen", placement: "right", shared: true,
  title: T("Full screen: only the page"),
  body: T("{key:Escape} brings the rest back.") };

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
  offer: { title: T("You opened a paper"), line: T("Zoom, contents, translation and the pen sit down its left edge.") },
  steps: [
    { id: "pdf-outline", anchor: "viewer.outline", placement: "right", optional: true,
      title: T("The paper's table of contents"),
      body: T("Click a heading to go to that section.") },
    zoom,
    { id: "pdf-recap", anchor: "viewer.common", placement: "right", recap: true, requires: { editable: true },
      title: T("Zoom, fit and the pen work as in the notebook view"),
      body: T("So does full screen, bottom left. Here is what a PDF adds.") },
    { id: "pdf-recap-zoom", anchor: "viewer.common", placement: "right", recap: true, requires: { editable: false },
      title: T("Zoom and fit work as in the notebook view"),
      body: T("So does full screen, bottom left. Here is what a PDF adds.") },
    pen,
    { id: "pdf-translate", anchor: "viewer.translate", placement: "right", optional: true,
      title: T("Translate the page you're reading"),
      body: T("Right-click for the whole document and the language; hold {key:Alt} to peek at the original."),
      bodyTouch: T("Long-press for the whole document and the language.") },
    { id: "pdf-select", anchor: "viewer.selectMode", placement: "right", requires: { phone: true },
      title: T("Choose what a drag does"),
      body: T("Select text, or draw a box around a figure or a formula.") },
    fullscreen,
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
    { id: "nbv-recap", anchor: "viewer.common", placement: "right", recap: true, requires: { editable: true },
      title: T("Zoom, fit and the pen work as on a PDF"),
      body: T("So does full screen, bottom left. Here is what the notebook view adds.") },
    { id: "nbv-recap-zoom", anchor: "viewer.common", placement: "right", recap: true, requires: { editable: false },
      title: T("Zoom and fit work as on a PDF"),
      body: T("So does full screen, bottom left. Here is what the notebook view adds.") },
    pen,
    { id: "nbv-paper", anchor: "viewer.paper", placement: "right", requires: { editable: true },
      title: T("The paper of the page in view"),
      body: T("Size, pattern and background. **Apply to all pages** gives every page the same.") },
    { id: "nbv-notes", anchor: "viewer.notesView", placement: "right", media: "page-notebook",
      title: T("Back to your notes"),
      body: T("The same pages, standing among your notes again. × at the bottom right does the same.") },
    fullscreen,
  ],
};
