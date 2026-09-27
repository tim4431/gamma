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
// account menu's Tours > Your first paper. It is also offered, as its
// welcome card, on the library of a demo server once the guest lands and on
// a library with nothing in it yet but the seeded Welcome page: the
// trigger's own `requires` / `anyOf` gate only that offer, so the tour stays
// startable everywhere.
//
// `welcome: true`: the first (`intro`) step is a centred welcome card — what
// the tour does, how long it takes, an outline — and so is the tour's offer;
// Start from the offer begins after it. The intro is not counted as a step.
const WELCOME = {
  anchor: null,
  intro: true,
  body: T("Learn it on a real paper. Gamma shows each move once, then you try it yourself."),
  outline: [
    { text: T("Add a paper from a link"), kind: "watch" },
    { text: T("Highlight a sentence and an equation"), kind: "try" },
    { text: T("Turn highlights into notes and labels"), kind: "watch" },
  ],
  next: T("Start the tour"),
  later: T("Explore on my own"),
  footnote: T("You can replay it any time from the account menu."),
};

export default {
  id: "first-run",
  version: 2,
  title: T("Your first paper"),
  welcome: true,
  minutes: 2,
  trigger: { requires: { view: "home", editable: true }, anyOf: [{ demo: true }, { emptyLibrary: true }] },
  // The finish card (guide/finish.js): what the run made, from its events
  // and the demos that ran to the end, then what to try next.
  finishCard: {
    title: T("That's your first paper"),
    lead: T("Everything the tour made is real and yours to keep:"),
    made: [
      { icon: "page", event: "page.opened", text: T("{title} is in your library") },
      { icon: "highlight", event: "highlight.created", text: T("{n} highlight on it"), plural: T("{n} highlights on it") },
      { icon: "note", step: "notes", text: T("a note under your highlights") },
      { icon: "label", step: "label", text: T("the {label} label, to find it again"), args: { label: "llm" } },
    ],
    next: ["ai", "tours"],
    footnote: T("Replay any tour from the account menu"),
  },
  // The demo paper: the seeded Welcome page's own PDF while the library has
  // it (no network needed), else Attention Is All You Need from arXiv.
  vars: (facts) => ({ demoUrl: facts.welcomePdf || "https://arxiv.org/abs/1706.03762" }),
  steps: [
    { ...WELCOME, id: "welcome", requires: { demo: false }, title: T("Welcome to Gamma") },
    { ...WELCOME, id: "welcome-demo", requires: { demo: true }, title: T("Welcome to the Gamma demo") },
    {
      id: "add-demo",
      anchor: "header.add",
      placement: "left",
      title: T("Adding a paper"),
      body: T("Gamma pastes a paper's address into Add and opens it. An arXiv link or a DOI works too."), // the Welcome PDF or arXiv (vars)
      skippable: false, // every later step works on the paper it opens
      do: [
        { click: "header.add" },
        { wait: 500 },
        { type: "add.urlInput", text: "{demoUrl}" },
        { wait: 500 },
        { press: "Enter", on: "add.urlInput" },
        { waitFor: { event: "page.opened" }, status: T("Fetching the paper…") },
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
      body: T("The paper, its highlights and your note are saved. Go back to your library to find it there."),
      advanceOn: { event: "home.opened" },
      next: T("Finish"),
    },
  ],
};
