import { T } from "../../shared/i18n/i18n.js";
// The first-run tour. Data only: anchors from guide/anchors.js, events from
// guide/events.js. A step with no anchor is a centred card. A step with `do`
// is a demo: the guide performs the actions itself (click / type / press /
// waitFor / wait), then moves on — or, with `advanceOn`, hands over to the
// user, whose own try ticks the step (Next moves on without it). The copy is
// a short title, and a body only for what the demo cannot show; `bodyTouch`
// words it for touch. `skippable: false` keeps "Skip this demo" off a demo
// the rest of the tour depends on. `{demoUrl}` in typed text comes from
// `vars` (overridable through the localStorage key gamma-guide-vars). Start
// it from the account menu's Tours > Your first paper. It is also offered,
// as its welcome deck, on the library of a demo server once the guest lands
// and on a library with nothing in it yet but the seeded Welcome page and Sketchbook: the
// trigger's own `requires` / `anyOf` gate only that offer, so the tour stays
// startable everywhere.
//
// `welcome: true`: the first (`intro`) step is the welcome deck, the tour's
// arrival offer (GuideOverlay's WelcomeDeck): what Gamma does, a card each
// with its drawing (`media`, guide/media.js) and one line, the first card the
// hero that carries this tour, then `more`, what has no drawing. Its way in
// (`next`) opens the seeded Welcome page; the hero's button starts the tour.
// A run never shows the intro: started from the deck, the Tours menu or the
// library's "Take the 2-minute tour", the tour opens on its first step.
const WELCOME = {
  anchor: null,
  intro: true,
  body: T("Read papers and keep what you learn, in one place."),
  // Two rows of four, the hero two wide: what Gamma does on a paper, then
  // where it reaches beyond it.
  features: [
    { media: "highlight-note", title: T("Highlights become notes"),
      line: T("Highlight a passage and it lands in your notes, linked to its page.") },
    { media: "chat-figure", title: T("Ask the paper"),
      line: T("Box a figure and ask; the answer cites its page.") },
    { media: "labels-folders", title: T("One paper, many folders"),
      line: T("Label a paper and keep it in several folders, never copied.") },
    { media: "ink-pen", title: T("Write on the page"),
      line: T("Circle, sketch or type; every mark is a note.") },
    { media: "notability-sync", title: T("Notability, both ways"),
      line: T("Bring notes in with their handwriting still live, and send pages back.") },
    { media: "zotero-sync", title: T("In sync with Zotero"),
      line: T("Highlights, notes and folders travel both ways through zotero.org.") },
    { media: "assistants-mcp", title: T("Your AI tools, connected"),
      line: T("Claude Code, Codex or any MCP client can search and read your papers.") },
  ],
  more: [
    { icon: "share", text: T("Share and edit together") },
    { icon: "offline", text: T("Offline copies that sync back") },
    { icon: "connector", text: T("Save papers from your browser") },
    { icon: "translate", text: T("Translate a page in place") },
  ],
  next: T("Open the Welcome page"),
  later: T("Explore on my own"),
  footnote: T("Every tour stays in the account menu."),
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
    lead: T("Everything the tour made is yours to keep:"),
    made: [
      { icon: "page", event: "page.opened", text: T("the paper “{title}”, in your library") },
      { icon: "highlight", event: "highlight.created", text: T("{n} highlight on it"), plural: T("{n} highlights on it") },
      { icon: "note", step: "note-label", text: T("a note under your highlights") },
      { icon: "label", step: "note-label", text: T("the {label} label, to find it again"), args: { label: "llm" } },
    ],
    next: ["ai", "tours"],
    footnote: T("Replay any tour from the account menu"),
  },
  // The demo paper: Attention Is All You Need, the paper the later steps are
  // written for (its abstract sentence, its attention formula). A library
  // that already holds it is no trouble — Add opens the saved copy by arXiv
  // id or DOI instead of resolving the link again (App's openPdf), so the
  // demo adds no second page and the step still sees page.opened.
  vars: {
    demoUrl: "https://arxiv.org/abs/1706.03762", // Attention Is All You Need
  },
  steps: [
    { ...WELCOME, id: "welcome", requires: { demo: false }, title: T("Welcome to Gamma") },
    { ...WELCOME, id: "welcome-demo", requires: { demo: true }, title: T("Welcome to the Gamma demo") },
    {
      id: "add-demo",
      anchor: "header.add",
      placement: "left",
      title: T("Add a paper from a link"),
      body: T("A DOI or an arXiv id works too."), // the arXiv link the demo types (vars)
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
    // The two highlights: Gamma shows the gesture once, then the user's own
    // highlight of that kind ticks the step.
    {
      id: "highlight",
      anchor: "pdf.viewer",
      placement: "inside",
      media: "highlight-note",
      title: T("Select text, then choose a colour"),
      bodyTouch: T("Long-press a word, drag the handles, then pick a colour."),
      do: [{ previewHighlight: true }],
      advanceOn: { event: "highlight.created", match: { kind: "text" } },
    },
    {
      id: "area",
      anchor: "pdf.viewer",
      placement: "inside",
      title: T("{key:pdf.areaDrag}+drag a box, then choose a colour"),
      do: [{ previewArea: true }],
      advanceOn: { event: "highlight.created", match: { kind: "area" } },
    },
    {
      id: "note-label",
      anchor: "dock.notes",
      placement: "left",
      title: T("A note, then a label"),
      body: T("Labels find papers across folders."),
      do: [
        { note: T("Attention compares queries with keys, then uses those scores to combine the values. Scaling keeps the scores stable.") },
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
      title: T("Go back to your library"),
      advanceOn: { event: "home.opened" },
      next: T("Finish"),
    },
  ],
};
