import { T } from "../../shared/i18n/i18n.js";
// Offered once a sheet of paper is made (a new notebook, +, /page, or
// writing low on the last sheet). Started from the Tours menu on a page
// without one, the first step has the user make one for the rest to point
// at. Two things about a notebook cannot be seen standing still — the next
// page arriving under your hand, and the paper changing — so those two steps
// carry a drawing (guide/media.js, docs/dev/notebooks.md).
export default {
  id: "notebook",
  version: 1,
  title: T("Pages to write on"),
  requires: { onPage: true, editable: true },
  // Only in the notes view: in the notebook view the sheets fill the
  // viewer and none of the steps' anchors is on screen, so an offer there
  // would be withdrawn — and withdrawing one spends it for good.
  trigger: { event: "sheet.created", requires: { notebookView: false } },
  offerAnchor: "notes.sheet",
  offer: { title: T("You have a page to write on"), line: T("Its paper, the pages that add themselves, and the notebook view.") },
  steps: [
    { id: "nb-make", anchor: "dock.notes", placement: "left", creates: "notes.sheet",
      title: T("Type /page in a note, or pick Add > New notebook"),
      advanceOn: { event: "sheet.created" } },
    { id: "nb-grow", anchor: "notes.sheet", placement: "left", media: "notebook-pages",
      title: T("Write near the bottom and the next page is already there"),
      body: T("So there is always paper below you. **+** under a page adds one wherever you like.") },
    { id: "nb-pen", anchor: "sheet.pen", placement: "top",
      title: T("Write on it with a pen, a stylus or the mouse"),
      body: T("Your drawing is also a note: unfold the page to caption it or replay it."),
      bodyTouch: T("Your drawing is also a note: unfold the page to caption it or replay it. An Apple Pencil writes while your hand scrolls.") },
    { id: "nb-paper", anchor: "notebook.paperMenu", placement: "left", media: "notebook-paper",
      title: T("Each page has its own paper"),
      body: T("Size, orientation, pattern and background. **Apply to all pages** gives every page here the same paper.") },
    { id: "nb-view", anchor: "sheet.notebookView", placement: "top", media: "page-notebook",
      title: T("The same pages, filling the viewer"),
      body: T("One page either way — in the notebook view your notes list its pages beside them."),
      next: T("Done") },
  ],
};
