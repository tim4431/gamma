import { T } from "../../shared/i18n/i18n.js";
// Offered after the first handwriting stroke — a stylus writes even with the
// tools closed, so the offer points at the button that opens them. Started
// from the Tours menu on a page without handwriting, the first step has the
// user draw something for the rest to point at. The practice steps spotlight
// the whole PDF pane, tool strip included, so the user can pick a tool and
// use it inside one hole.
export default {
  id: "handwriting",
  version: 2,
  title: T("Handwriting"),
  requires: { hasPdf: true, editable: true },
  trigger: { event: "ink.stroke" },
  offerAnchor: "pdf.inkButton",
  steps: [
    { id: "ink-draw", anchor: "pdf.pane", reveal: "ink.toolbar", placement: "inside", creates: "notes.ink",
      title: T("Pick a pen, then draw on the page"), advanceOn: { event: "ink.stroke" } },
    { id: "ink-style", anchor: "ink.toolbar", placement: "bottom", creates: "ink.options",
      title: T("Tap your pen again for its colour, width and style"), advanceOn: { event: "ink.options" } },
    { id: "ink-options", anchor: "ink.options", optional: true, placement: "bottom",
      title: T("Pen follows your pressure; Monoline draws an even line") },
    { id: "ink-erase", anchor: "pdf.pane", reveal: "ink.toolbar", placement: "inside",
      title: T("Pick the eraser and rub out part of your drawing"), advanceOn: { event: "ink.erased" } },
    { id: "ink-undo", anchor: "ink.history", placement: "bottom",
      title: T("Undo brings it back: click ↶ or press Ctrl+Z"), advanceOn: { event: "ink.undone" } },
    { id: "ink-lasso", anchor: "ink.lasso", placement: "bottom", title: T("The lasso selects strokes to move or delete") },
    { id: "ink-note", anchor: "notes.ink", optional: true, placement: "left", title: T("Each drawing is also a note block"), next: T("Done") },
  ],
};
