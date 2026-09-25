import { t, T } from "../../shared/i18n/i18n.js";
// The first-run tour. Data only: anchors from guide/anchors.js, events from
// guide/events.js. A step with no anchor is a centred card. A step with `do`
// is a demo: the guide performs the actions itself (click / type / press /
// waitFor / wait), then moves on — or, with `advanceOn`, hands over to the
// user. `{demoUrl}` in typed text comes from `vars` (overridable through the
// localStorage key gamma-guide-vars). Start it from the
// account menu's Tours > Your first paper.

export default {
  id: "first-run",
  version: 2,
  title: T("Your first paper"),
  vars: {
    demoUrl: "https://arxiv.org/abs/1706.03762", // Attention Is All You Need
  },
  steps: [
    {
      id: "welcome",
      anchor: null,
      title: T("Welcome to Gamma"),
      next: T("Start"),
    },
    {
      id: "add-demo",
      anchor: "header.add",
      placement: "left",
      title: T("Adding a paper"),
      do: [
        { click: "header.add" },
        { wait: 500 },
        { type: "add.urlInput", text: "{demoUrl}" },
        { wait: 500 },
        { press: "Enter", on: "add.urlInput" },
        { waitFor: { event: "page.opened" } },
        { wait: 800 },
      ],
    },
    {
      id: "highlight-demo",
      anchor: "pdf.viewer",
      placement: "inside",
      title: T("Highlight a passage"),
      do: [{ previewHighlight: true }],
    },
    {
      id: "highlight",
      anchor: "pdf.viewer",
      placement: "inside",
      title: T("Select text, then choose a colour"),
      advanceOn: { event: "highlight.created", match: { kind: "text" } },
    },
    {
      id: "area-demo",
      anchor: "pdf.viewer",
      placement: "inside",
      title: T("Highlight a figure or equation"),
      do: [{ previewArea: true }],
    },
    {
      id: "area",
      anchor: "pdf.viewer",
      placement: "inside",
      title: T("Ctrl-drag a box, then choose a colour"),
      advanceOn: { event: "highlight.created", match: { kind: "area" } },
    },
    {
      id: "notes",
      anchor: "dock.notes",
      placement: "left",
      title: T("Add a note"),
      do: [{ note: t("Attention compares queries with keys, then uses those scores to combine the values. Scaling keeps the scores stable.") }],
    },
    {
      id: "label",
      anchor: "page.labels",
      placement: "left",
      title: T("Add the llm label"),
      do: [
        { click: "page.labels" },
        { type: "page.labelInput", text: "llm" },
        { press: "Enter", on: "page.labelInput" },
        { wait: 900 },
      ],
    },
    {
      id: "home",
      anchor: "header.home",
      placement: "bottom",
      title: T("Back to your library"),
      advanceOn: { event: "home.opened" },
      next: T("Finish"),
    },
  ],
};
