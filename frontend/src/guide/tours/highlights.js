import { T } from "../../shared/i18n/i18n.js";
// Offered after the third highlight of a load: the first tour makes two and
// moves on, so what a highlight does afterwards is this tour's. A click
// finds its note (and quotes it into the chat), its menu recolours, links or
// deletes it, and its note leads back. The steps point at the highlight made
// last (`pick: "recent"`, guide/recent.js); the note step is `optional`, for
// a page whose Notes window is closed.
export default {
  id: "highlights",
  version: 1,
  title: T("Your highlights"),
  requires: { hasPdf: true, editable: true },
  trigger: { event: "highlight.created", count: 3 },
  offerAnchor: "pdf.highlight",
  offerPlacement: "bottom",
  offer: { title: T("You've made a few highlights"), line: T("Find their notes, recolour or delete them.") },
  steps: [
    { id: "hl-note", anchor: "pdf.highlight", placement: "bottom",
      title: T("Click a highlight to find its note"),
      body: T("It's quoted into your next chat message too."),
      scene: [{ click: "pdf.highlight", at: [0.3, 0.5] }] },
    { id: "hl-menu", anchor: "pdf.highlight", placement: "bottom",
      title: T("Right-click to recolour, link or delete"),
      bodyTouch: T("Long-press it instead."),
      scene: [{ click: "pdf.highlight", button: "right", at: [0.3, 0.5] }] },
    { id: "hl-back", anchor: "notes.highlight", placement: "left", optional: true,
      title: T("Click its note to jump back"),
      scene: [{ click: "notes.highlight", at: [0.5, 0.5] }] },
  ],
};
