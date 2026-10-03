import { T } from "../../shared/i18n/i18n.js";
// Offered once a sheet of paper is made (a new notebook, +, /page, or
// writing low on the last sheet). Started from the Tours menu on a page
// without one, the first step has the user make one for the rest to point
// at. The next page arriving under your hand cannot be seen standing still,
// so that step shows it (`scene`: a stroke low on the sheet, the outline of
// the page that comes); the last carries the drawing of the one page behind
// both views (guide/media.js, docs/dev/notebooks.md). The paper itself is
// left to the notebook view's tour, offered once the user follows the last
// step there.
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
  offer: { title: T("You have a page to write on"), line: T("Pages that add themselves, and the notebook view.") },
  steps: [
    { id: "nb-make", anchor: "dock.notes", placement: "left", creates: "notes.sheet",
      title: T("Type /page in a note"),
      body: T("Or choose Add > New notebook."),
      advanceOn: { event: "sheet.created" } },
    { id: "nb-grow", anchor: "notes.sheet", placement: "left",
      title: T("Pages add themselves as you write"),
      body: T("**+** under a page adds one anywhere."),
      scene: [{ stroke: "notes.sheet", at: [0.15, 0.78, 0.7, 0.92] }, { ghost: "notes.sheet", size: 44 }] },
    { id: "nb-pen", anchor: "sheet.pen", placement: "top",
      title: T("Pick the pen to write on it"),
      body: T("Your drawing is also a note you can caption."),
      bodyTouch: T("An Apple Pencil writes while your finger scrolls.") },
    { id: "nb-view", anchor: "sheet.notebookView", placement: "top", media: "page-notebook",
      title: T("Open them in the notebook view"),
      body: T("Same page, its sheets filling the viewer."),
      next: T("Done") },
  ],
};
