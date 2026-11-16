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
// arrival offer (GuideOverlay's WelcomeDeck): what Gamma does, one slide at a
// time, each a looping drawing (`media`, guide/media.js) beside its topic,
// title and line. The first slide carries this tour. A slide whose feature
// is set up in Settings or another app names its setup guide (`setup`) and
// the words of its way in (`go`). The last slide holds the deck's way in
// (`next`), which opens the seeded Welcome page, and its footnote.
// A run never shows the intro: started from the deck, the Tours menu or the
// library's "Take the 2-minute tour", the tour opens on its first step.
const WELCOME = {
  anchor: null,
  intro: true,
  // Adding a paper first, then what Gamma does on one, who works on it with
  // you (people, then an agent on your computer), and where it reaches beyond
  // it.
  slides: [
    { media: "paper-fetch", topic: T("Start"), title: T("Add a paper"),
      line: T("Click + and paste a link, DOI or arXiv id. Gamma fetches the PDF, with its title and authors.") },
    { media: "highlight-note", topic: T("Reading"), title: T("Highlights become notes"),
      line: T("Drag across a sentence and pick a colour. The highlight lands in Notes, linked to its place on the page.") },
    { media: "chat-figure", topic: T("Ask"), title: T("Ask the paper"),
      line: T("Box a figure or a passage and ask about it. The answer comes back citing the page it read.") },
    { media: "ink-pen", topic: T("Writing"), title: T("Write on the page"),
      line: T("Turn on the pen to circle, underline or sketch in the margin. Every drawing is kept as a note.") },
    { media: "notes-move", topic: T("Writing"), title: T("Notes nest like an outline"),
      line: T("Drag a note by its handle to move it. Drop it a step to the right to nest it under the note above.") },
    { media: "share-link", topic: T("Together"), title: T("Share and edit together"),
      line: T("Copy a link from Share. Whoever opens it sees the same page and edits it with you, live.") },
    { media: "agent-comment", topic: T("Agents"), title: T("Ask an agent in a comment"),
      line: T("In the desktop app, sync a folder and tick Send to Codex on a comment. Codex or Claude Code edits the files on your computer, rebuilds and answers in the thread.") },
    { media: "zotero-sync", topic: T("Connect"), title: T("In sync with Zotero"),
      line: T("Highlights, notes and folders travel both ways through zotero.org."),
      setup: "zotero-link", go: T("Set it up") },
    { media: "notability-sync", topic: T("Connect"), title: T("Notability, both ways"),
      line: T("Bring notes in with their handwriting still live, and send pages back as .note files."),
      setup: "notability", go: T("Show me how") },
    { media: "assistants-mcp", topic: T("Connect"), title: T("Gamma is an MCP server"),
      line: T("Connect Codex, Claude Code or any MCP client to it. They search and read your papers through Gamma's tools, and cite the page."),
      setup: "assistants", go: T("Connect one") },
  ],
  next: T("Open the Welcome page"),
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
