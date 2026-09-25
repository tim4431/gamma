import { T } from "../../shared/i18n/i18n.js";
// Offered after the first handwriting stroke — a stylus writes even with the
// tools closed, so the offer points at the button that opens them.
export default {
  id: "handwriting",
  version: 1,
  title: T("Handwriting"),
  requires: { hasPdf: true },
  trigger: { event: "ink.stroke" },
  offerAnchor: "pdf.inkButton",
  steps: [
    { id: "ink-tools", anchor: "ink.toolbar", placement: "bottom", title: T("Tap the active tool again for its colour and width") },
    { id: "ink-note", anchor: "notes.ink", optional: true, placement: "left", title: T("Each drawing is also a note block"), next: T("Done") },
  ],
};
