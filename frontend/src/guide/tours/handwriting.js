import { T } from "../../shared/i18n/i18n.js";
// Offered after the first handwriting stroke — a stylus writes even with the
// tools closed, so the offer points at the button that opens them. Started
// from the Tours menu on a page without handwriting, the first step has the
// user draw something for the rest to point at (a `scene` shows a stroke on
// the page). It leads with why ink is worth it (the drawing is a note you
// can caption), then the pen's options, then the eraser and the lasso. The
// drawing and erasing steps spotlight the whole PDF pane, tool strip
// included, so the user can pick a tool and use it inside one hole. Finishing re-arms the pen
// (`restore`), so the next drag writes.
export default {
  id: "handwriting",
  version: 2,
  title: T("Handwriting"),
  requires: { hasPdf: true, editable: true },
  trigger: { event: "ink.stroke" },
  offerAnchor: "pdf.inkButton",
  offer: { title: T("You're writing on the PDF"), line: T("Erase, undo, recolour, and caption your drawing.") },
  restore: "pen",
  steps: [
    { id: "ink-draw", anchor: "pdf.pane", reveal: "ink.toolbar", placement: "inside", creates: "notes.ink",
      title: T("Pick a pen and draw here"),
      scene: [{ stroke: "pdf.pane", at: [0.3, 0.35, 0.62, 0.46] }],
      advanceOn: { event: "ink.stroke" } },
    { id: "ink-note", anchor: "notes.ink", optional: true, placement: "left",
      title: T("Your drawing is a note too"),
      body: T("Click its text to add a caption.") },
    { id: "ink-style", anchor: "ink.toolbar", placement: "bottom",
      title: T("Tap your pen again for options"),
      body: T("Colour, width, and Pen (pressure) or Monoline."),
      advanceOn: { event: "ink.options" } },
    { id: "ink-erase", anchor: "pdf.pane", reveal: "ink.toolbar", placement: "inside",
      title: T("Erase, or lasso strokes to move"),
      body: T("{key:Mod-z} brings back what you erased."),
      scene: [{ point: "ink.eraser" }, { wait: 500 }, { point: "ink.lasso" }],
      next: T("Done") },
  ],
};
