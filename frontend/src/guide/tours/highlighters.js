import { T } from "../../shared/i18n/i18n.js";
// Offered on the sixth text highlight of a load to someone who has never
// turned a highlighter on: each of those took a selection and a pick from
// its colours, where a highlighter on the viewer's strip marks every
// selection at once in its look (docs/dev/settings.md "Text
// highlighters"). Its menu restyles it (a wash or one of three lines) and
// Duplicate makes another; the first nine answer the keys 1–9.
export default {
  id: "highlighters",
  version: 1,
  title: T("Text highlighters"),
  requires: { hasPdf: true, editable: true },
  trigger: { event: "highlight.created", match: { kind: "text" }, count: 6, doneOn: { event: "highlighter.armed" } },
  offerAnchor: "viewer.highlighter",
  offerPlacement: "right",
  offer: { title: T("Highlight as you select"), line: T("With a highlighter on, every selection is marked at once.") },
  steps: [
    { id: "hl-arm", anchor: "viewer.highlighter", placement: "right",
      title: T("Turn a highlighter on, then select"), body: T("{key:Escape} turns it off."),
      scene: [{ click: "viewer.highlighter" }], advanceOn: { event: "highlighter.armed" } },
    { id: "hl-style", anchor: "viewer.highlighter", placement: "right",
      title: T("Its menu sets the style and colour"),
      body: T("Right-click it: highlight, underline, wavy underline or strikethrough."),
      bodyTouch: T("Hold it: highlight, underline, wavy underline or strikethrough."),
      scene: [{ click: "viewer.highlighter", button: "right" }], advanceOn: { event: "highlighter.menu" } },
    { id: "hl-more", anchor: "viewer.highlighter", placement: "right",
      title: T("**Duplicate** one to have several"), body: T("Keys {key:1} to {key:9} turn on the first nine.") },
  ],
};
