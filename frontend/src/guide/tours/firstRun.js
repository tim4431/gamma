import { t, T } from "../../shared/i18n/i18n.js";
// The first-run tour. Data only: anchors from guide/anchors.js, events from
// guide/events.js. A step with no anchor is a centred card. A step with `do`
// is a demo: the guide performs the actions itself (click / type / press /
// waitFor / wait), then moves on — or, with `advanceOn`, hands over to the
// user. Each step has a short title and one body sentence: a demo says
// what Gamma is about to do, a practice step what to do (`bodyTouch` words
// it for touch). `skippable: false` keeps "Skip this demo" off a demo the
// rest of the tour depends on. `{demoUrl}` in typed text comes from `vars` (overridable through the
// localStorage key gamma-guide-vars). Start it from the
// account menu's Tours > Your first paper. On a demo server it is also
// offered on the library once the guest lands: the trigger's own `requires`
// gates only that offer, so the tour stays startable everywhere.

export default {
  id: "first-run",
  version: 2,
  title: T("Your first paper"),
  trigger: { requires: { demo: true, view: "home" } },
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
      body: T("Gamma pastes an arXiv link into Add and opens the paper. A DOI or a PDF's address works too."),
      skippable: false, // every later step works on the paper it opens
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
      body: T("Gamma drags across a sentence, then points at the colours. Your turn is next."),
      do: [{ previewHighlight: true }],
    },
    {
      id: "highlight",
      anchor: "pdf.viewer",
      placement: "inside",
      title: T("Select text, then choose a colour"),
      body: T("Drag across any sentence in the PDF and pick a colour. The highlight lands in your notes."),
      bodyTouch: T("Long-press a word, drag the handles, then pick a colour."),
      advanceOn: { event: "highlight.created", match: { kind: "text" } },
    },
    {
      id: "area-demo",
      anchor: "pdf.viewer",
      placement: "inside",
      title: T("Highlight a figure or equation"),
      body: T("Gamma holds Ctrl and drags a box around the equation. Your turn is next."),
      do: [{ previewArea: true }],
    },
    {
      id: "area",
      anchor: "pdf.viewer",
      placement: "inside",
      title: T("Ctrl-drag a box, then choose a colour"),
      body: T("Hold Ctrl and drag a box around a figure or an equation, then pick a colour. The snapshot lands in your notes."),
      advanceOn: { event: "highlight.created", match: { kind: "area" } },
    },
    {
      id: "notes",
      anchor: "dock.notes",
      placement: "left",
      title: T("Add a note"),
      body: T("Gamma writes a note under your highlights. Click any note to edit it."),
      do: [{ note: t("Attention compares queries with keys, then uses those scores to combine the values. Scaling keeps the scores stable.") }],
    },
    {
      id: "label",
      anchor: "page.labels",
      placement: "left",
      title: T("Add the llm label"),
      body: T("Labels find papers across folders. Gamma adds one under the title."),
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
      body: T("The paper, its highlights and your note are saved. Open Home to find it in your library."),
      advanceOn: { event: "home.opened" },
      next: T("Finish"),
    },
  ],
};
